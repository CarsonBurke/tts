from __future__ import annotations

from pathlib import Path

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "hexgrad/Kokoro-82M"
DEFAULT_LANGUAGE = "a"
DEFAULT_VOICE = "af_heart"
DEFAULT_SAMPLE_RATE = 24000


def speak(request: SpeakRequest) -> SpeechResult:
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise SpeechError(
            "Kokoro backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[kokoro]"'
        ) from exc

    device = resolve_torch_device(request.device)
    if request.model and request.model != DEFAULT_MODEL:
        raise SpeechError("Kokoro Python backend does not support --model; use the ONNX backend for custom paths.")

    pipeline = KPipeline(lang_code=request.language or DEFAULT_LANGUAGE, device=device)
    generator = pipeline(request.text, voice=str(request.speaker or DEFAULT_VOICE), speed=request.speed)
    audios = [chunk_audio for _, _, chunk_audio in generator if chunk_audio is not None]
    if not audios:
        raise SpeechError("Kokoro generated no audio.")
    audio = _concat_audio(audios)

    output = request.output or _default_output_path()
    write_generated_audio(output, audio, DEFAULT_SAMPLE_RATE)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="kokoro", sample_rate=DEFAULT_SAMPLE_RATE, output_path=output)


def _concat_audio(audios):
    if len(audios) == 1:
        return audios[0]
    first = audios[0]
    if hasattr(first, "new_empty"):
        import torch

        return torch.cat(audios)
    combined = []
    for audio in audios:
        if hasattr(audio, "tolist"):
            audio = audio.tolist()
        combined.extend(audio)
    return combined


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
