from __future__ import annotations

from pathlib import Path

from . import SpeakRequest, SpeechResult
from .model_audio import write_generated_audio
from .torch_runtime import resolve_torch_device
from ..audio import SpeechError, play_wav

DEFAULT_MODEL = "ResembleAI/chatterbox"
DEFAULT_SAMPLE_RATE = 24000


def speak(request: SpeakRequest) -> SpeechResult:
    try:
        import chatterbox.tts as chatterbox_tts
    except ImportError as exc:
        raise SpeechError(
            "Chatterbox backend requires optional dependencies. "
            'Install with: python -m pip install -e ".[chatterbox]"'
        ) from exc

    _patch_missing_watermarker(chatterbox_tts.perth)
    ChatterboxTTS = chatterbox_tts.ChatterboxTTS
    device = resolve_torch_device(request.device)
    if request.model:
        model = ChatterboxTTS.from_local(request.model, device=device)
    else:
        model = ChatterboxTTS.from_pretrained(device=device)
    kwargs: dict[str, object] = {}
    if request.reference_audio:
        kwargs["audio_prompt_path"] = str(request.reference_audio)
    if request.exaggeration is not None:
        kwargs["exaggeration"] = request.exaggeration
    if request.cfg_weight is not None:
        kwargs["cfg_weight"] = request.cfg_weight

    audio = model.generate(request.text, **kwargs)
    sample_rate = int(getattr(model, "sr", DEFAULT_SAMPLE_RATE))
    output = request.output or _default_output_path()
    write_generated_audio(output, audio, sample_rate)
    if request.play:
        play_wav(output)
    return SpeechResult(backend="chatterbox", sample_rate=sample_rate, output_path=output)


def _patch_missing_watermarker(perth) -> None:
    if getattr(perth, "PerthImplicitWatermarker", None) is not None:
        return

    class NoOpWatermarker:
        def apply_watermark(self, audio, sample_rate):
            return audio

    perth.PerthImplicitWatermarker = NoOpWatermarker


def _default_output_path() -> Path:
    from tempfile import NamedTemporaryFile

    handle = NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    return Path(handle.name)
