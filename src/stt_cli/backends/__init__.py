from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class SpeechResult:
    backend: str
    sample_rate: Optional[int] = None
    output_path: Optional[Path] = None


@dataclass(frozen=True)
class SpeakRequest:
    text: str
    output: Optional[Path]
    play: bool
    voice: Optional[str] = None
    speed: float = 1.0
    device: Optional[str] = None
    model: Optional[str] = None
    model_size: str = "0.5"
    speaker: Optional[Union[str, int]] = None
    provider: str = "cpu"
    num_threads: int = 1
    language: Optional[str] = None
    instruct: Optional[str] = None
    reference_audio: Optional[Path] = None
    reference_text: Optional[str] = None
    exaggeration: Optional[float] = None
    cfg_weight: Optional[float] = None
