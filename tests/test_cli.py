from __future__ import annotations

import pytest

from stt_cli.backends.onnx import resolve_provider
from stt_cli.backends.vibevoice import resolve_device, resolve_model
from stt_cli.cli import _apply_defaults, _benchmark_variants, _resolve_text, _speak_parser
from stt_cli.audio import _to_pcm16
from stt_cli.backends import SpeakRequest
from stt_cli.backends import system
from stt_cli.backends.model_audio import _to_mono_samples
from stt_cli.audio import SpeechError
from stt_cli.config import load_config, BUILTIN_DEFAULTS
from stt_cli.power import _parse_ioreg_battery


def test_vibevoice_default_model_is_realtime_0_5b():
    assert resolve_model(None, "0.5") == "microsoft/VibeVoice-Realtime-0.5B"


def test_builtin_default_backend_is_kokoro():
    assert BUILTIN_DEFAULTS["backend"] == "kokoro"
    assert BUILTIN_DEFAULTS["speaker"] == "af_heart"
    assert BUILTIN_DEFAULTS["speed"] == 1.25


def test_vibevoice_1_5b_model_alias():
    assert resolve_model(None, "1.5b") == "microsoft/VibeVoice-1.5B"


def test_explicit_model_wins_over_size():
    assert resolve_model("local/model", "0.5") == "local/model"


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("0", 0),
        ("cuda", 0),
        ("cuda:1", 1),
        ("mps", "mps"),
        ("cpu", "cpu"),
    ],
)
def test_vibevoice_device_aliases(device, expected):
    assert resolve_device(device) == expected


def test_vibevoice_invalid_cuda_device_fails():
    with pytest.raises(SpeechError, match="Invalid CUDA device"):
        resolve_device("cuda:fast")


def test_onnx_auto_provider_prefers_coreml_on_macos(monkeypatch):
    monkeypatch.setattr("stt_cli.backends.onnx.sys.platform", "darwin")
    assert resolve_provider("auto") == "coreml"


def test_onnx_auto_provider_uses_cuda_when_nvidia_is_present(monkeypatch):
    monkeypatch.setattr("stt_cli.backends.onnx.sys.platform", "linux")
    monkeypatch.setattr("stt_cli.backends.onnx.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    assert resolve_provider("auto") == "cuda"


def test_onnx_auto_provider_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr("stt_cli.backends.onnx.sys.platform", "linux")
    monkeypatch.setattr("stt_cli.backends.onnx.shutil.which", lambda name: None)
    assert resolve_provider("auto") == "cpu"


def test_say_parser_keeps_full_text_without_truncation():
    args = _speak_parser().parse_args(["one", "two", "three"])
    assert _resolve_text(args) == "one two three"


def test_title_and_body_form_orchestrator_update_text():
    args = _speak_parser().parse_args(
        ["--level", "blocked", "--title", "Blocked", "--body", "Need migration approval."]
    )
    assert _resolve_text(args) == "Blocked. Need migration approval."


def test_config_file_provides_speak_defaults(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[speak]\n"
        "backend = system\n"
        "voice = Samantha\n"
        "speed = 1.2\n"
        "model-size = 1.5\n"
        "num_threads = 4\n",
        encoding="utf-8",
    )

    args = _speak_parser().parse_args(["hello"])
    _apply_defaults(args, load_config(str(config_path), disabled=False))

    assert args.backend == "system"
    assert args.voice == "Samantha"
    assert args.speed == 1.2
    assert args.model_size == "1.5"
    assert args.num_threads == 4


def test_cli_options_override_config_file(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[speak]\nbackend = system\nspeed = 1.2\nvoice = Samantha\n", encoding="utf-8")

    args = _speak_parser().parse_args(["--backend", "onnx", "--speed", "0.9", "hello"])
    _apply_defaults(args, load_config(str(config_path), disabled=False))

    assert args.backend == "onnx"
    assert args.speed == 0.9
    assert args.voice == "Samantha"


def test_model_backend_options_are_configurable(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "[speak]\n"
        "backend = qwen3\n"
        "language = English\n"
        "speaker = Ryan\n"
        "instruct = calm and concise\n"
        "reference_audio = ~/voice.wav\n"
        "reference_text = reference transcript\n"
        "exaggeration = 0.35\n"
        "cfg_weight = 0.4\n",
        encoding="utf-8",
    )

    args = _speak_parser().parse_args(["hello"])
    _apply_defaults(args, load_config(str(config_path), disabled=False))

    assert args.backend == "qwen3"
    assert args.language == "English"
    assert args.speaker == "Ryan"
    assert args.instruct == "calm and concise"
    assert args.reference_audio == "~/voice.wav"
    assert args.reference_text == "reference transcript"
    assert args.exaggeration == 0.35
    assert args.cfg_weight == 0.4


def test_invalid_config_option_fails(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[speak]\nmax_chars = 20\n", encoding="utf-8")

    with pytest.raises(SpeechError, match="Unsupported config option"):
        load_config(str(config_path), disabled=False)


def test_system_backend_maps_speed_to_macos_say_rate(monkeypatch):
    monkeypatch.setattr("stt_cli.backends.system.sys.platform", "darwin")
    monkeypatch.setattr("stt_cli.backends.system.shutil.which", lambda name: "/usr/bin/say")
    request = SpeakRequest(text="hello", output=None, play=True, voice="Flo", speed=1.25)

    assert system._speak_command(request) == ["say", "-v", "Flo", "-r", "219", "hello"]


def test_benchmark_variants_compare_vibevoice_cpu_and_gpu():
    args = _speak_parser().parse_args(["--backend", "vibevoice", "hello"])
    _apply_defaults(args, {})

    assert _benchmark_variants(args) == [("cpu", {"device": "cpu"}), ("gpu", {"device": "auto"})]


def test_benchmark_variants_compare_model_backend_cpu_and_gpu():
    args = _speak_parser().parse_args(["--backend", "qwen3", "hello"])
    _apply_defaults(args, {})

    assert _benchmark_variants(args) == [("cpu", {"device": "cpu"}), ("gpu", {"device": "auto"})]


def test_model_audio_mixes_multichannel_samples():
    assert _to_mono_samples([[1.0, 0.0], [0.0, 1.0]]) == [0.5, 0.5]


def test_parse_ioreg_battery_power():
    values = _parse_ioreg_battery(
        '"Voltage" = 12500\n'
        '"Amperage" = -1200\n'
        '"CurrentCapacity" = 80\n'
        '"MaxCapacity" = 100\n'
        '"ExternalConnected" = No\n'
    )

    assert values == {
        "Voltage": 12500,
        "Amperage": -1200,
        "CurrentCapacity": 80,
        "MaxCapacity": 100,
        "ExternalConnected": 0,
    }


def test_parse_ioreg_unsigned_negative_current():
    values = _parse_ioreg_battery('"Amperage" = 18446744073709551251\n')

    assert values["Amperage"] == -365


def test_system_backend_requires_output_when_no_play():
    request = SpeakRequest(text="hello", output=None, play=False)
    with pytest.raises(SpeechError, match="needs --output"):
        system.speak(request)


@pytest.mark.parametrize(
    ("sample", "expected"),
    [(-2.0, -32767), (-1.0, -32767), (0.0, 0), (1.0, 32767), (2.0, 32767)],
)
def test_float_samples_are_clamped_to_pcm16(sample, expected):
    assert _to_pcm16(sample) == expected
