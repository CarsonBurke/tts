from __future__ import annotations

import math
import shutil
import signal
import struct
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Optional, Sequence, Union


class SpeechError(RuntimeError):
    """Raised when synthesis or playback fails."""


Sample = Union[float, int]


def write_wav(path: Path, samples: Sequence[Sample], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(_pcm16_bytes(samples))


def play_wav(path: Path) -> None:
    command = playback_command(path)
    if command is None:
        raise SpeechError("No WAV playback command found for this platform.")
    # Windows SoundPlayer path has no portable pause hooks; keep it simple.
    if sys.platform == "win32":
        subprocess.run(command, check=True)
        return

    # Avoid preexec_fn: the daemon is multi-threaded and preexec_fn is unsafe there.
    process = subprocess.Popen(command)
    title = _playback_title(path)
    if process.pid:
        try:
            from . import playback as playback_ctl

            playback_ctl.register(process.pid, title=title)
        except Exception:
            pass
    try:
        returncode = _wait_for_playback(process, title=title)
    finally:
        if process.pid:
            try:
                from . import playback as playback_ctl

                playback_ctl.unregister(process.pid)
            except Exception:
                pass
        _ensure_finished(process)

    # Stop via media keys / CLI terminates the player; treat that as success.
    if returncode and returncode < 0:
        return
    if returncode not in (0, None):
        raise SpeechError(f"Playback failed with exit code {returncode}.")


def play_samples(samples: Sequence[Sample], sample_rate: int) -> None:
    with NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = Path(handle.name)
    try:
        write_wav(path, samples, sample_rate)
        play_wav(path)
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def playback_command(path: Path) -> Optional[list[str]]:
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", str(path)]
    if sys.platform == "win32":
        escaped = str(path).replace("'", "''")
        script = (
            f"$p='{escaped}';"
            "$player=New-Object System.Media.SoundPlayer $p;"
            "$player.Load();"
            "$player.PlaySync()"
        )
        return ["powershell", "-NoProfile", "-Command", script]
    for candidate in ("paplay", "aplay", "ffplay"):
        exe = shutil.which(candidate)
        if not exe:
            continue
        if candidate == "ffplay":
            return [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        return [exe, str(path)]
    return None


def _wait_for_playback(process: subprocess.Popen, title: str) -> int:
    try:
        from . import mpris

        if mpris.available():
            return mpris.run_with_player(process, title=title)
    except Exception:
        pass
    returncode = process.wait()
    return 0 if returncode is None else returncode


def _ensure_finished(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform != "win32" and hasattr(signal, "SIGCONT"):
        try:
            process.send_signal(signal.SIGCONT)
        except ProcessLookupError:
            return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            return
        process.wait()


def _playback_title(path: Path) -> str:
    name = path.name
    if name.startswith("tmp") or name.startswith("tts"):
        return "Speech"
    return name or "Speech"


def _pcm16_bytes(samples: Iterable[Sample]) -> bytes:
    chunks: list[bytes] = []
    for sample in samples:
        value = _to_pcm16(sample)
        chunks.append(struct.pack("<h", value))
    return b"".join(chunks)


def _to_pcm16(sample: Sample) -> int:
    if isinstance(sample, float):
        if math.isnan(sample):
            sample = 0.0
        sample = max(-1.0, min(1.0, sample))
        return int(sample * 32767)
    return max(-32768, min(32767, int(sample)))
