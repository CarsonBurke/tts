from __future__ import annotations

from pathlib import Path

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "neuphonic/neutts-nano"
DEFAULT_CODEC = "neuphonic/neucodec"
DEFAULT_SAMPLE_RATE = 24000


def speak(request: SpeakRequest) -> SpeechResult:
    if not request.reference_audio or not request.reference_text:
        raise SpeechError("NeuTTS requires --reference-audio and --reference-text.")

    try:
        from neutts import NeuTTS
    except ImportError as exc:
        raise SpeechError(
            "NeuTTS backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[neutts]"'
        ) from exc

    device = resolve_torch_device(request.device)
    model = NeuTTS(
        backbone_repo=request.model or DEFAULT_MODEL,
        backbone_device=device,
        codec_repo=DEFAULT_CODEC,
        codec_device=device,
    )
    ref_codes = model.encode_reference(str(request.reference_audio))
    audio = model.infer(text=request.text, ref_codes=ref_codes, ref_text=request.reference_text)

    output = request.output or _default_output_path()
    write_generated_audio(output, audio, DEFAULT_SAMPLE_RATE)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="neutts", sample_rate=DEFAULT_SAMPLE_RATE, output_path=output)


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
