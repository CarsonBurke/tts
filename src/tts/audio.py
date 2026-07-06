from __future__ import annotations

import math
import shutil
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
    subprocess.run(command, check=True)


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
