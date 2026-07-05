from __future__ import annotations

from pathlib import Path

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device, resolve_torch_dtype
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "k2-fsa/OmniVoice"
DEFAULT_SAMPLE_RATE = 24000


def speak(request: SpeakRequest) -> SpeechResult:
    if not request.reference_audio or not request.reference_text:
        raise SpeechError("OmniVoice requires --reference-audio and --reference-text.")

    try:
        import torch
        from omnivoice import OmniVoice
    except ImportError as exc:
        raise SpeechError(
            "OmniVoice backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[omnivoice]"'
        ) from exc

    device = resolve_torch_device(request.device)
    dtype = resolve_torch_dtype(torch, device)
    model = OmniVoice.from_pretrained(request.model or DEFAULT_MODEL, device_map=device, dtype=dtype)
    result = model.generate(
        text=request.text,
        ref_audio=str(request.reference_audio),
        ref_text=request.reference_text,
    )
    audio, sample_rate = _extract_audio(result)

    output = request.output or _default_output_path()
    write_generated_audio(output, audio, sample_rate)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="omnivoice", sample_rate=sample_rate, output_path=output)


def _extract_audio(result):
    if isinstance(result, tuple) and len(result) == 2:
        audio, sample_rate = result
        return audio, int(sample_rate)
    if isinstance(result, dict):
        audio = result.get("audio") or result.get("samples")
        sample_rate = result.get("sampling_rate") or result.get("sample_rate") or DEFAULT_SAMPLE_RATE
        if audio is not None:
            return audio, int(sample_rate)
    return result, DEFAULT_SAMPLE_RATE


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
