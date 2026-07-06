from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from .audio import SpeechError

CONFIG_SECTION = "speak"

CONFIGURABLE_NAMES = (
    "backend",
    "voice",
    "speaker",
    "speed",
    "model_size",
    "model",
    "device",
    "provider",
    "num_threads",
    "language",
    "instruct",
    "reference_audio",
    "reference_text",
    "exaggeration",
    "cfg_weight",
    "onnx_kind",
    "vits_model",
    "vits_lexicon",
    "vits_tokens",
    "vits_data_dir",
    "matcha_acoustic_model",
    "matcha_vocoder",
    "matcha_lexicon",
    "matcha_tokens",
    "matcha_data_dir",
    "kokoro_model",
    "kokoro_voices",
    "kokoro_tokens",
    "kokoro_data_dir",
    "kokoro_lexicon",
    "kitten_model",
    "kitten_voices",
    "kitten_tokens",
    "kitten_data_dir",
    "tts_rule_fsts",
    "max_num_sentences",
    "debug",
)

BUILTIN_DEFAULTS = {
    "backend": "kokoro",
    "speaker": "af_heart",
    "speed": 1.25,
    "device": "auto",
    "model_size": "0.5",
    "provider": "auto",
    "num_threads": 1,
    "onnx_kind": "vits",
    "vits_lexicon": "",
    "vits_data_dir": "",
    "matcha_lexicon": "",
    "matcha_data_dir": "",
    "kokoro_data_dir": "",
    "kokoro_lexicon": "",
    "kitten_data_dir": "",
    "tts_rule_fsts": "",
    "max_num_sentences": 1,
    "debug": False,
}

CHOICES = {
    "backend": ("auto", "vibevoice", "qwen3", "chatterbox", "kokoro", "neutts", "omnivoice", "onnx", "system"),
    "onnx_kind": ("vits", "matcha", "kokoro", "kitten"),
}

TYPES: dict[str, Callable[[str], object]] = {
    "speed": float,
    "exaggeration": float,
    "cfg_weight": float,
    "num_threads": int,
    "max_num_sentences": int,
}


def default_config_path() -> Path:
    if "TTS_CONFIG" in os.environ:
        return Path(os.environ["TTS_CONFIG"]).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "tts" / "config.ini"
        return Path.home() / "AppData" / "Roaming" / "tts" / "config.ini"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tts" / "config.ini"

    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "tts" / "config.ini"
    return Path.home() / ".config" / "tts" / "config.ini"


def load_config(path: Optional[str], disabled: bool) -> dict[str, object]:
    if disabled:
        return {}

    config_path = Path(path).expanduser() if path else default_config_path()
    if not config_path.exists():
        return {}

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except configparser.Error as exc:
        raise SpeechError(f"Invalid config file {config_path}: {exc}") from exc

    if CONFIG_SECTION not in parser:
        return {}

    values: dict[str, object] = {}
    section = parser[CONFIG_SECTION]
    for raw_name, raw_value in section.items():
        name = raw_name.replace("-", "_")
        if name not in CONFIGURABLE_NAMES:
            raise SpeechError(f"Unsupported config option in {config_path}: {raw_name}")
        values[name] = _coerce_value(name, raw_value, config_path)
    return values


def resolve_option(name: str, explicit_value: object, config: dict[str, object]) -> object:
    if explicit_value is not None:
        return explicit_value
    if name in config:
        return config[name]
    return BUILTIN_DEFAULTS.get(name)


def _coerce_value(name: str, raw_value: str, config_path: Path) -> object:
    if name == "debug":
        return _coerce_bool(name, raw_value, config_path)

    converter = TYPES.get(name)
    if converter is None:
        value: object = raw_value
    else:
        try:
            value = converter(raw_value)
        except ValueError as exc:
            raise SpeechError(f"Invalid value for {name} in {config_path}: {raw_value}") from exc

    choices = CHOICES.get(name)
    if choices is not None and value not in choices:
        valid = ", ".join(choices)
        raise SpeechError(f"Invalid value for {name} in {config_path}: {value}. Expected one of: {valid}")

    return value


def _coerce_bool(name: str, raw_value: str, config_path: Path) -> bool:
    value = raw_value.strip().lower()
    if value in ("1", "yes", "true", "on"):
        return True
    if value in ("0", "no", "false", "off"):
        return False
    raise SpeechError(f"Invalid boolean for {name} in {config_path}: {raw_value}")
