from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional, Union
from urllib.request import urlretrieve

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from ..audio import SpeechError, play_wav, write_wav

VIBEVOICE_MODELS = {
    "0.5": "microsoft/VibeVoice-Realtime-0.5B",
    "0.5b": "microsoft/VibeVoice-Realtime-0.5B",
    "realtime": "microsoft/VibeVoice-Realtime-0.5B",
    "1.5": "microsoft/VibeVoice-1.5B",
    "1.5b": "microsoft/VibeVoice-1.5B",
}
STREAMING_SAMPLE_RATE = 24_000
DEFAULT_STREAMING_VOICE = "Emma"
STREAMING_VOICES = {
    "carter": "en-Carter_man.pt",
    "davis": "en-Davis_man.pt",
    "emma": "en-Emma_woman.pt",
    "frank": "en-Frank_man.pt",
    "grace": "en-Grace_woman.pt",
    "mike": "en-Mike_man.pt",
}
VOICE_BASE_URL = "https://raw.githubusercontent.com/microsoft/VibeVoice/main/demo/voices/streaming_model"


def resolve_model(model: Optional[str], model_size: str) -> str:
    if model:
        return model
    key = model_size.strip().lower()
    if key not in VIBEVOICE_MODELS:
        raise SpeechError(f"Unsupported VibeVoice model size: {model_size}")
    return VIBEVOICE_MODELS[key]


def speak(request: SpeakRequest) -> SpeechResult:
    model = resolve_model(request.model, request.model_size)
    if model == "microsoft/VibeVoice-Realtime-0.5B":
        return _speak_realtime(request, model)

    try:
        from transformers import pipeline
    except ImportError as exc:
        raise SpeechError(
            "VibeVoice backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[vibevoice]"'
        ) from exc

    pipe_kwargs: dict[str, Any] = {"model": model}
    if request.device:
        pipe_kwargs["device"] = resolve_device(request.device)

    tts = pipeline("text-to-speech", **pipe_kwargs)
    call_kwargs: dict[str, Any] = {}
    if request.speaker is not None:
        call_kwargs["speaker"] = request.speaker

    result = tts(request.text, **call_kwargs)
    samples, sample_rate = _extract_audio(result)

    output = request.output
    if output is None:
        output = _default_output_path()
    write_wav(output, samples, sample_rate)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="vibevoice", sample_rate=sample_rate, output_path=output)


def _speak_realtime(request: SpeakRequest, model_id: str) -> SpeechResult:
    try:
        import torch
        from transformers.cache_utils import DynamicCache
        from transformers.modeling_outputs import BaseModelOutputWithPast
        from vibevoice.modular.modeling_vibevoice_streaming_inference import (
            VibeVoiceStreamingForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor
    except ImportError as exc:
        raise SpeechError(
            "VibeVoice Realtime requires the official VibeVoice package. "
            "Install with: python -m pip install git+https://github.com/microsoft/VibeVoice.git"
        ) from exc

    device = str(resolve_device(request.device or "auto"))
    if device == "0":
        device = "cuda"
    if device == "mps" and not torch.backends.mps.is_available():
        device = "cpu"
    if device == "cuda":
        dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    else:
        dtype = torch.float32
    attention = "sdpa" if device in ("mps", "cpu") else "flash_attention_2"

    processor = VibeVoiceStreamingProcessor.from_pretrained(model_id)
    model_kwargs: dict[str, Any] = {"torch_dtype": dtype, "attn_implementation": attention}
    if device == "mps":
        model_kwargs["device_map"] = None
    else:
        model_kwargs["device_map"] = device
    try:
        model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(model_id, **model_kwargs)
    except Exception:
        if attention != "flash_attention_2":
            raise
        model_kwargs["attn_implementation"] = "sdpa"
        model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(model_id, **model_kwargs)
    if device == "mps":
        model.to("mps")
    model.eval()
    model.set_ddpm_inference_steps(num_steps=5)

    voice_path = _streaming_voice_path(request.speaker)
    target_device = device if device != "cpu" else "cpu"
    with torch.serialization.safe_globals([BaseModelOutputWithPast, DynamicCache]):
        prompt = torch.load(voice_path, map_location=target_device, weights_only=False)

    inputs = processor.process_input_with_cached_prompt(
        text=request.text.replace("’", "'").replace("“", '"').replace("”", '"'),
        cached_prompt=prompt,
        padding=True,
        return_tensors="pt",
        return_attention_mask=True,
    )
    for key, value in inputs.items():
        if torch.is_tensor(value):
            inputs[key] = value.to(target_device)

    outputs = model.generate(
        **inputs,
        max_new_tokens=None,
        cfg_scale=1.5,
        tokenizer=processor.tokenizer,
        generation_config={"do_sample": False},
        verbose=False,
        all_prefilled_outputs=copy.deepcopy(prompt),
    )
    if not outputs.speech_outputs or outputs.speech_outputs[0] is None:
        raise SpeechError("VibeVoice Realtime did not generate audio.")

    output = request.output or _default_output_path()
    write_generated_audio(output, outputs.speech_outputs[0], STREAMING_SAMPLE_RATE)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="vibevoice", sample_rate=STREAMING_SAMPLE_RATE, output_path=output)


def _streaming_voice_path(speaker: Any) -> Path:
    if speaker:
        candidate = Path(str(speaker)).expanduser()
        if candidate.exists():
            return candidate
    key = str(speaker or DEFAULT_STREAMING_VOICE).strip().lower()
    filename = STREAMING_VOICES.get(key)
    if filename is None:
        matches = [value for name, value in STREAMING_VOICES.items() if key in name or name in key]
        if len(matches) == 1:
            filename = matches[0]
        else:
            available = ", ".join(sorted(STREAMING_VOICES))
            raise SpeechError(f"Unknown VibeVoice Realtime voice '{speaker}'. Available voices: {available}")
    cache_path = Path.home() / ".cache" / "stt-cli" / "vibevoice" / "voices" / filename
    if not cache_path.exists():
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(f"{VOICE_BASE_URL}/{filename}", cache_path)
    return cache_path


def resolve_device(device: str) -> Union[int, str]:
    value = device.strip().lower()
    if value == "auto":
        return _auto_device()
    if value.isdigit():
        return int(value)
    if value == "cuda":
        return 0
    if value.startswith("cuda:"):
        index = value.removeprefix("cuda:")
        if not index.isdigit():
            raise SpeechError(f"Invalid CUDA device: {device}")
        return int(index)
    if value in ("cpu", "mps"):
        return value
    return device


def _auto_device() -> Union[int, str]:
    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.cuda.is_available():
        return 0

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"

    return "cpu"


def _extract_audio(result: Any) -> tuple[Any, int]:
    if not isinstance(result, dict):
        raise SpeechError(f"Unexpected Transformers TTS result: {type(result).__name__}")
    audio = result.get("audio")
    sample_rate = result.get("sampling_rate") or result.get("sample_rate")
    if audio is None or sample_rate is None:
        raise SpeechError("Transformers TTS result did not include audio and sampling rate.")
    return audio, int(sample_rate)


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
