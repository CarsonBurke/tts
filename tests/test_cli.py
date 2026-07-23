from __future__ import annotations

import pytest

from tts.backends.onnx import resolve_provider
from tts.backends.vibevoice import resolve_device, resolve_model
from tts.cli import _apply_defaults, _benchmark_variants, _resolve_text, _should_use_daemon, _speak_parser
from tts.audio import _to_pcm16
from tts.backends import SpeakRequest
from tts.backends import system
from tts.backends.model_audio import _to_mono_samples
from tts.audio import SpeechError
from tts.config import load_config, BUILTIN_DEFAULTS
from tts.mpris import PlaybackController, run_with_player
from tts import playback as playback_mod
from tts.power import _parse_ioreg_battery


def test_vibevoice_default_model_is_realtime_0_5b():
    assert resolve_model(None, "0.5") == "microsoft/VibeVoice-Realtime-0.5B"


def test_builtin_default_backend_is_kokoro():
    assert BUILTIN_DEFAULTS["backend"] == "kokoro"
    assert BUILTIN_DEFAULTS["speaker"] == "af_sarah"
    assert BUILTIN_DEFAULTS["speed"] == 1.25
    assert BUILTIN_DEFAULTS["daemon"] is True
    assert BUILTIN_DEFAULTS["daemon_idle_seconds"] == 1800


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
    monkeypatch.setattr("tts.backends.onnx.sys.platform", "darwin")
    assert resolve_provider("auto") == "coreml"


def test_onnx_auto_provider_uses_cuda_when_nvidia_is_present(monkeypatch):
    monkeypatch.setattr("tts.backends.onnx.sys.platform", "linux")
    monkeypatch.setattr("tts.backends.onnx.shutil.which", lambda name: "/usr/bin/nvidia-smi")
    assert resolve_provider("auto") == "cuda"


def test_onnx_auto_provider_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr("tts.backends.onnx.sys.platform", "linux")
    monkeypatch.setattr("tts.backends.onnx.shutil.which", lambda name: None)
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


def test_daemon_config_defaults_are_configurable(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[speak]\ndaemon = false\ndaemon_idle_seconds = 60\ndaemon_start_timeout = 3.5\n", encoding="utf-8")

    args = _speak_parser().parse_args(["hello"])
    _apply_defaults(args, load_config(str(config_path), disabled=False))

    assert args.daemon is False
    assert args.daemon_idle_seconds == 60
    assert args.daemon_start_timeout == 3.5


def test_kokoro_uses_daemon_when_enabled():
    args = _speak_parser().parse_args(["hello"])
    _apply_defaults(args, {})

    assert args.backend == "kokoro"
    assert _should_use_daemon(args) is True


def test_no_daemon_disables_daemon_path():
    args = _speak_parser().parse_args(["--no-daemon", "hello"])
    _apply_defaults(args, {})

    assert _should_use_daemon(args) is False


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
    monkeypatch.setattr("tts.backends.system.sys.platform", "darwin")
    monkeypatch.setattr("tts.backends.system.shutil.which", lambda name: "/usr/bin/say")
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


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.signals: list[int] = []
        self._alive = True
        self.terminated = False

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, sig: int) -> None:
        import signal

        self.signals.append(sig)
        if sig == signal.SIGTERM:
            self._alive = False
            self.terminated = True

    def terminate(self) -> None:
        import signal

        self.send_signal(signal.SIGTERM)


def test_playback_controller_pause_resume_stop():
    import signal

    process = _FakeProcess()
    controller = PlaybackController(process, title="Hello")

    assert controller.status() == "Playing"
    controller.pause()
    assert controller.status() == "Paused"
    assert process.signals == [signal.SIGSTOP]

    controller.pause()  # idempotent
    assert process.signals == [signal.SIGSTOP]

    controller.play()
    assert controller.status() == "Playing"
    assert process.signals == [signal.SIGSTOP, signal.SIGCONT]

    controller.play_pause()
    assert controller.status() == "Paused"
    controller.play_pause()
    assert controller.status() == "Playing"

    controller.pause()
    controller.stop()
    assert controller.status() == "Stopped"
    assert process.terminated
    assert process.signals[-2:] == [signal.SIGCONT, signal.SIGTERM]


def test_playback_controller_stop_while_playing():
    import signal

    process = _FakeProcess()
    controller = PlaybackController(process, title="Hello")
    controller.stop()
    assert process.signals == [signal.SIGTERM]
    assert controller.status() == "Stopped"


def test_mpris_helper_controller_pause_resume_stop(tmp_path, monkeypatch):
    """Exercise the standalone helper Controller used in production."""
    import importlib.util
    import signal
    from pathlib import Path

    helper_path = Path(__file__).resolve().parents[1] / "src" / "tts" / "mpris_helper.py"
    spec = importlib.util.spec_from_file_location("tts_mpris_helper", helper_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sent: list[tuple[int, int]] = []
    stopped = {"value": False}

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if any(s == signal.SIGTERM for _, s in sent) or any(s == signal.SIGKILL for _, s in sent):
                raise OSError("gone")
            return
        sent.append((pid, sig))
        if sig == signal.SIGSTOP:
            stopped["value"] = True
        elif sig == signal.SIGCONT:
            stopped["value"] = False
        elif sig in (signal.SIGTERM, signal.SIGKILL):
            return

    monkeypatch.setattr(module.os, "kill", fake_kill)
    monkeypatch.setattr(module, "_process_state", lambda pid: "T" if stopped["value"] else "R")
    controller = module.Controller(pid=99, title="Speech")
    assert controller.status() == "Playing"
    controller.pause()
    assert controller.paused() is True
    assert sent == [(99, signal.SIGSTOP)]
    controller.play()
    assert controller.paused() is False
    assert sent[-1] == (99, signal.SIGCONT)
    controller.pause()
    controller.stop()
    assert (99, signal.SIGCONT) in sent
    assert (99, signal.SIGTERM) in sent


def test_cli_playback_controls_use_state_file(tmp_path, monkeypatch):
    import signal

    monkeypatch.setenv("TTS_RUNTIME_DIR", str(tmp_path))
    sent: list[tuple[int, int]] = []
    paused = {"value": False}
    alive = {"value": True}

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if not alive["value"]:
                raise OSError("gone")
            return
        sent.append((pid, sig))
        if sig == signal.SIGSTOP:
            paused["value"] = True
        elif sig == signal.SIGCONT:
            paused["value"] = False
        elif sig in (signal.SIGTERM, signal.SIGKILL):
            alive["value"] = False

    monkeypatch.setattr(playback_mod.os, "kill", fake_kill)
    monkeypatch.setattr(playback_mod, "pid_paused", lambda pid: paused["value"])
    monkeypatch.setattr(playback_mod.sys, "platform", "linux")

    playback_mod.register(4242, title="Hello")
    assert playback_mod.status()["status"] == "Playing"

    assert playback_mod.pause()["status"] == "Paused"
    assert sent[-1] == (4242, signal.SIGSTOP)

    assert playback_mod.resume()["status"] == "Playing"
    assert sent[-1] == (4242, signal.SIGCONT)

    assert playback_mod.play_pause()["status"] == "Paused"
    assert playback_mod.play_pause()["status"] == "Playing"

    stopped = playback_mod.stop()
    assert stopped["status"] == "Stopped"
    assert playback_mod.status()["playing"] is False


def test_cli_pause_with_nothing_playing(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(playback_mod.PlaybackError, match="nothing is playing"):
        playback_mod.pause()


def test_parser_accepts_playback_commands():
    from tts.cli import _parser

    for command in ("pause", "resume", "play-pause", "stop", "playback-status"):
        args = _parser().parse_args([command])
        assert args.command == command


def test_run_with_player_resumes_if_helper_dies(monkeypatch):
    """If the MPRIS helper exits while audio is SIGSTOP'd, playback must resume."""
    import signal
    import subprocess
    import time

    class Player:
        def __init__(self) -> None:
            self.pid = 12345
            self.signals: list[int] = []
            self._paused = False
            self._alive = True
            self._started = time.monotonic()

        def poll(self):
            return None if self._alive else 0

        def wait(self, timeout=None):
            deadline = None if timeout is None else time.monotonic() + timeout
            while self._alive:
                if self._paused:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(cmd="player", timeout=timeout)
                    time.sleep(0.01)
                    continue
                # Natural end shortly after resume/start.
                if time.monotonic() - self._started > 0.05 and not self._paused:
                    self._alive = False
                    return 0
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(cmd="player", timeout=timeout)
                time.sleep(0.01)
            return 0

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)
            if sig == signal.SIGSTOP:
                self._paused = True
            elif sig == signal.SIGCONT:
                self._paused = False
                self._started = time.monotonic()
            elif sig in (signal.SIGTERM, signal.SIGKILL):
                self._alive = False

        def terminate(self) -> None:
            self.send_signal(signal.SIGTERM)

        def kill(self) -> None:
            self.send_signal(signal.SIGKILL)

    class Helper:
        def __init__(self) -> None:
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self) -> None:
            self._alive = False

        def kill(self) -> None:
            self._alive = False

        def wait(self, timeout=None):
            self._alive = False
            return 0

    player = Player()
    helper = Helper()

    monkeypatch.setattr("tts.mpris._start_helper", lambda pid, title: helper)
    monkeypatch.setattr("tts.mpris._helper_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr("tts.mpris.available", lambda: True)

    def die_soon():
        time.sleep(0.05)
        player.send_signal(signal.SIGSTOP)
        time.sleep(0.05)
        helper._alive = False

    import threading

    threading.Thread(target=die_soon, daemon=True).start()
    code = run_with_player(player, title="Speech")
    assert code == 0
    assert signal.SIGCONT in player.signals
    assert player.poll() is not None
