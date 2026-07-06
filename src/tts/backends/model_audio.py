from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audio import write_wav


def write_generated_audio(path: Path, audio: Any, sample_rate: int) -> None:
    samples = _to_mono_samples(audio)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_wav(path, samples, sample_rate)


def _to_mono_samples(audio: Any) -> list[float]:
    if hasattr(audio, "detach"):
        audio = audio.detach()
    if hasattr(audio, "cpu"):
        audio = audio.cpu()
    if hasattr(audio, "ndim") and hasattr(audio, "shape"):
        if audio.ndim == 0:
            return [float(audio)]
        if audio.ndim == 1:
            return [float(value) for value in audio.tolist()]
        if audio.ndim == 2:
            if 1 in audio.shape:
                squeezed = audio.squeeze()
                return [float(value) for value in squeezed.tolist()]
            axis = 0 if audio.shape[0] <= audio.shape[1] else 1
            mixed = audio.mean(axis=axis)
            return [float(value) for value in mixed.tolist()]
    if hasattr(audio, "numpy"):
        audio = audio.numpy()
    if hasattr(audio, "ndim") and hasattr(audio, "shape"):
        if audio.ndim == 0:
            return [float(audio)]
        if audio.ndim == 1:
            return [float(value) for value in audio.tolist()]
        if audio.ndim == 2:
            if 1 in audio.shape:
                squeezed = audio.squeeze()
                return [float(value) for value in squeezed.tolist()]
            axis = 0 if audio.shape[0] <= audio.shape[1] else 1
            mixed = audio.mean(axis=axis)
            return [float(value) for value in mixed.tolist()]
    if hasattr(audio, "tolist"):
        audio = audio.tolist()

    if isinstance(audio, tuple):
        audio = list(audio)

    if not isinstance(audio, list):
        return [float(audio)]

    if len(audio) == 1 and hasattr(audio[0], "ndim"):
        return _to_mono_samples(audio[0])

    if audio and isinstance(audio[0], list):
        rows = audio
        if len(rows) == 1:
            return [float(value) for value in rows[0]]
        if rows[0] and len(rows[0]) == 1:
            return [float(row[0]) for row in rows]
        length = min(len(row) for row in rows if row)
        if length == 0:
            return []
        return [sum(float(row[index]) for row in rows) / len(rows) for index in range(length)]

    return [float(value) for value in audio]
