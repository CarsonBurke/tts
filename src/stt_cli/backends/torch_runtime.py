from __future__ import annotations

from typing import Optional
from typing import Any

from ..audio import SpeechError
from .vibevoice import resolve_device


def resolve_torch_device(device: Optional[str]) -> str:
    resolved = resolve_device(device or "auto")
    if isinstance(resolved, int):
        return f"cuda:{resolved}"
    return resolved


def resolve_torch_dtype(torch: Any, device: str, dtype: Optional[str] = None) -> Any:
    value = (dtype or "auto").strip().lower()
    if value == "auto":
        if device.startswith("cuda"):
            return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
        if device == "mps":
            return torch.float16
        return torch.float32
    if value in ("float32", "fp32"):
        return torch.float32
    if value in ("float16", "fp16"):
        return torch.float16
    if value in ("bfloat16", "bf16"):
        return torch.bfloat16
    raise SpeechError(f"Unsupported torch dtype: {dtype}")
