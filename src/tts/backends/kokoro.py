from __future__ import annotations

from contextlib import contextmanager, redirect_stderr
from pathlib import Path
import sys
import threading
import warnings

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "hexgrad/Kokoro-82M"
DEFAULT_LANGUAGE = "a"
DEFAULT_VOICE = "af_sarah"
DEFAULT_SAMPLE_RATE = 24000
_PIPELINE_LOCK = threading.Lock()
_PIPELINES = {}


def speak(request: SpeakRequest) -> SpeechResult:
    with _quiet_known_runtime_warnings():
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

    with _quiet_known_runtime_warnings():
        pipeline = _pipeline(KPipeline, request.language or DEFAULT_LANGUAGE, device)
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


def warm(request: SpeakRequest) -> None:
    with _quiet_known_runtime_warnings():
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise SpeechError(
                "Kokoro backend requires optional dependencies. "
                'Install with: python -m pip install -e ".[kokoro]"'
            ) from exc

        device = resolve_torch_device(request.device)
        pipeline = _pipeline(KPipeline, request.language or DEFAULT_LANGUAGE, device)
        generator = pipeline(request.text, voice=str(request.speaker or DEFAULT_VOICE), speed=request.speed)
        for _, _, _ in generator:
            pass


def _pipeline(KPipeline, language: str, device: str):
    key = (language, device)
    with _PIPELINE_LOCK:
        pipeline = _PIPELINES.get(key)
        if pipeline is None:
            pipeline = KPipeline(lang_code=language, repo_id=DEFAULT_MODEL, device=device)
            _PIPELINES[key] = pipeline
        return pipeline


@contextmanager
def _quiet_known_runtime_warnings():
    with warnings.catch_warnings(), redirect_stderr(_FilteredStderr(sys.stderr)):
        warnings.filterwarnings("ignore", message="You are sending unauthenticated requests.*")
        warnings.filterwarnings("ignore", message="dropout option adds dropout.*")
        warnings.filterwarnings("ignore", message="`torch.nn.utils.weight_norm` is deprecated.*")
        warnings.filterwarnings("ignore", message="An output with one or more elements was resized.*")
        yield


class _FilteredStderr:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text):
        if "You are sending unauthenticated requests to the HF Hub" in text:
            return len(text)
        return self._wrapped.write(text)

    def flush(self):
        return self._wrapped.flush()


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
