from __future__ import annotations

from pathlib import Path

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device, resolve_torch_dtype
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
DEFAULT_LANGUAGE = "English"
DEFAULT_SPEAKER = "Aiden"


def speak(request: SpeakRequest) -> SpeechResult:
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise SpeechError(
            "Qwen3 backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[qwen3]"'
        ) from exc

    model_id = request.model or DEFAULT_MODEL
    device = resolve_torch_device(request.device)
    dtype = resolve_torch_dtype(torch, device)
    model = Qwen3TTSModel.from_pretrained(model_id, device_map=device, dtype=dtype)
    language = request.language or DEFAULT_LANGUAGE

    if request.reference_audio or request.reference_text:
        if not request.reference_audio or not request.reference_text:
            raise SpeechError("Qwen3 voice cloning requires both --reference-audio and --reference-text.")
        wavs, sample_rate = model.generate_voice_clone(
            text=request.text,
            language=language,
            ref_audio=str(request.reference_audio),
            ref_text=request.reference_text,
        )
    else:
        wavs, sample_rate = model.generate_custom_voice(
            text=request.text,
            language=language,
            speaker=str(request.speaker or DEFAULT_SPEAKER),
            instruct=request.instruct or "",
        )

    audio = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    output = request.output or _default_output_path()
    write_generated_audio(output, audio, int(sample_rate))
    if request.play:
        play_wav(output)
    return SpeechResult(backend="qwen3", sample_rate=int(sample_rate), output_path=output)


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
