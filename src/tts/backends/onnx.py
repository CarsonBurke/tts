from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional, Union

from . import SpeakRequest, SpeechResult
from ..audio import SpeechError, play_wav


ConfigValue = Optional[Union[str, int, float, bool]]


def speak(request: SpeakRequest, config: dict[str, ConfigValue]) -> SpeechResult:
    try:
        import soundfile as sf
        import sherpa_onnx
    except ImportError as exc:
        raise SpeechError(
            "ONNX backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[onnx]"'
        ) from exc

    output = request.output or _default_output_path()
    tts_config = _build_config(sherpa_onnx, request, config)
    if not tts_config.validate():
        raise SpeechError("Invalid sherpa-onnx TTS configuration.")

    tts = sherpa_onnx.OfflineTts(tts_config)
    gen_config = sherpa_onnx.GenerationConfig()
    if request.speaker is not None:
        gen_config.sid = int(request.speaker)
    gen_config.speed = request.speed

    audio = tts.generate(request.text, gen_config)
    if len(audio.samples) == 0:
        raise SpeechError("sherpa-onnx generated no audio.")

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), audio.samples, samplerate=audio.sample_rate, subtype="PCM_16")
    if request.play:
        play_wav(output)
    return SpeechResult(backend="onnx", sample_rate=int(audio.sample_rate), output_path=output)


def _build_config(sherpa_onnx, request: SpeakRequest, config: dict[str, ConfigValue]):
    model_kind = str(config.get("onnx_kind") or "vits")
    provider = resolve_provider(request.provider)
    model = sherpa_onnx.OfflineTtsModelConfig(
        vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(config.get("vits_model") or ""),
            lexicon=str(config.get("vits_lexicon") or ""),
            data_dir=str(config.get("vits_data_dir") or ""),
            tokens=str(config.get("vits_tokens") or ""),
        ),
        matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
            acoustic_model=str(config.get("matcha_acoustic_model") or ""),
            vocoder=str(config.get("matcha_vocoder") or ""),
            lexicon=str(config.get("matcha_lexicon") or ""),
            tokens=str(config.get("matcha_tokens") or ""),
            data_dir=str(config.get("matcha_data_dir") or ""),
        ),
        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(config.get("kokoro_model") or ""),
            voices=str(config.get("kokoro_voices") or ""),
            tokens=str(config.get("kokoro_tokens") or ""),
            data_dir=str(config.get("kokoro_data_dir") or ""),
            lexicon=str(config.get("kokoro_lexicon") or ""),
        ),
        kitten=sherpa_onnx.OfflineTtsKittenModelConfig(
            model=str(config.get("kitten_model") or ""),
            voices=str(config.get("kitten_voices") or ""),
            tokens=str(config.get("kitten_tokens") or ""),
            data_dir=str(config.get("kitten_data_dir") or ""),
        ),
        provider=provider,
        num_threads=request.num_threads,
        debug=bool(config.get("debug") or False),
    )
    _validate_required(model_kind, config)
    return sherpa_onnx.OfflineTtsConfig(
        model=model,
        rule_fsts=str(config.get("tts_rule_fsts") or ""),
        max_num_sentences=int(config.get("max_num_sentences") or 1),
    )


def _validate_required(model_kind: str, config: dict[str, ConfigValue]) -> None:
    required_by_kind = {
        "vits": ("vits_model", "vits_tokens"),
        "matcha": ("matcha_acoustic_model", "matcha_vocoder", "matcha_tokens"),
        "kokoro": ("kokoro_model", "kokoro_voices", "kokoro_tokens"),
        "kitten": ("kitten_model", "kitten_voices", "kitten_tokens"),
    }
    required = required_by_kind.get(model_kind)
    if required is None:
        raise SpeechError(f"Unsupported ONNX model kind: {model_kind}")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise SpeechError(f"Missing required {model_kind} option(s): {', '.join(missing)}")


def resolve_provider(provider: str) -> str:
    value = provider.strip().lower()
    if value != "auto":
        return value
    if sys.platform == "darwin":
        return "coreml"
    if shutil.which("nvidia-smi"):
        return "cuda"
    return "cpu"


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
