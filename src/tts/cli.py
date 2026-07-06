from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional

from .audio import SpeechError
from .backends import SpeakRequest
from .config import CONFIGURABLE_NAMES, default_config_path, load_config, resolve_option
from .power import PowerSample, sample_power


def main(argv: Optional[list[str]] = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "speak":
        _run_speak(args)
        return
    if args.command == "benchmark":
        _run_benchmark(args)
        return
    raise SpeechError(f"Unknown command: {args.command}")


def main_say(argv: Optional[list[str]] = None) -> None:
    args = _speak_parser(prog="tts-say").parse_args(argv)
    _run_speak(args)


def _run_speak(args: argparse.Namespace) -> None:
    if args.print_config_path:
        print(default_config_path())
        return

    try:
        config = load_config(args.config, args.no_config)
    except SpeechError as exc:
        print(f"tts: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    _apply_defaults(args, config)
    text = _resolve_text(args)
    request = _request_from_args(args, text, Path(args.output).expanduser() if args.output else None, not args.no_play)

    try:
        result = _speak_with_backend(args.backend, request, _onnx_config(args))
    except SpeechError as exc:
        print(f"tts: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except subprocess.SubprocessError as exc:
        print(f"tts: command failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.print_result:
        print(result.output_path if result.output_path is not None else result.backend)


def _run_benchmark(args: argparse.Namespace) -> None:
    try:
        config = load_config(args.config, args.no_config)
    except SpeechError as exc:
        print(f"tts: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    _apply_defaults(args, config)
    text = " ".join(args.text) if args.text else "Agent status benchmark. Build finished and review is waiting."
    rows: list[dict[str, object]] = []
    if args.runs < 1:
        print("tts: --runs must be at least 1", file=sys.stderr)
        raise SystemExit(1)

    try:
        variants = _benchmark_variants(args)
    except SpeechError as exc:
        print(f"tts: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    for variant, overrides in variants:
        for run_index in range(1, args.runs + 1):
            output = _temporary_audio(args.backend)
            for name, value in overrides.items():
                setattr(args, name, value)
            request = _request_from_args(args, text, output, False)

            before_power = sample_power()
            started = time.perf_counter()
            error = ""
            try:
                _speak_with_backend(args.backend, request, _onnx_config(args))
            except SpeechError as exc:
                error = str(exc)
            except subprocess.SubprocessError as exc:
                error = f"command failed: {exc}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started
            after_power = sample_power()
            output.unlink(missing_ok=True)

            rows.append(
                {
                    "variant": variant,
                    "run": run_index,
                    "latency_s": elapsed,
                    "battery_before": before_power,
                    "battery_after": after_power,
                    "error": error,
                }
            )
            if error:
                break

    _print_benchmark(rows)


def _speak_with_backend(backend: str, request: SpeakRequest, onnx_config: dict[str, object]):
    if backend == "auto":
        errors: list[str] = []
        for candidate in ("vibevoice", "onnx", "system"):
            try:
                return _speak_with_backend(candidate, request, onnx_config)
            except SpeechError as exc:
                errors.append(f"{candidate}: {exc}")
        raise SpeechError("; ".join(errors))

    if backend == "vibevoice":
        from .backends import vibevoice

        return vibevoice.speak(request)
    if backend == "qwen3":
        from .backends import qwen3

        return qwen3.speak(request)
    if backend == "chatterbox":
        from .backends import chatterbox

        return chatterbox.speak(request)
    if backend == "kokoro":
        from .backends import kokoro

        return kokoro.speak(request)
    if backend == "neutts":
        from .backends import neutts

        return neutts.speak(request)
    if backend == "omnivoice":
        from .backends import omnivoice

        return omnivoice.speak(request)
    if backend == "onnx":
        from .backends import onnx

        return onnx.speak(request, onnx_config)
    if backend == "system":
        from .backends import system

        return system.speak(request)
    raise SpeechError(f"Unsupported backend: {backend}")


def _request_from_args(args: argparse.Namespace, text: str, output: Optional[Path], play: bool) -> SpeakRequest:
    return SpeakRequest(
        text=text,
        output=output,
        play=play,
        voice=args.voice,
        speed=args.speed,
        device=args.device,
        model=args.model,
        model_size=args.model_size,
        speaker=args.speaker,
        provider=args.provider,
        num_threads=args.num_threads,
        language=args.language,
        instruct=args.instruct,
        reference_audio=Path(args.reference_audio).expanduser() if args.reference_audio else None,
        reference_text=args.reference_text,
        exaggeration=args.exaggeration,
        cfg_weight=args.cfg_weight,
    )


def _resolve_text(args: argparse.Namespace) -> str:
    if args.text_stdin:
        return sys.stdin.read()
    if args.body:
        if args.title:
            return f"{args.title}. {args.body}"
        return args.body
    if args.title:
        return args.title
    if args.text:
        return " ".join(args.text)
    raise SpeechError("No text provided.")


def _onnx_config(args: argparse.Namespace) -> dict[str, object]:
    names = (
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
    return {name: getattr(args, name) for name in names}


def _apply_defaults(args: argparse.Namespace, config: dict[str, object]) -> None:
    for name in CONFIGURABLE_NAMES:
        setattr(args, name, resolve_option(name, getattr(args, name), config))


def _benchmark_variants(args: argparse.Namespace) -> list[tuple[str, dict[str, object]]]:
    if args.backend == "auto":
        raise SpeechError("Benchmark requires an explicit backend, not --backend auto.")
    if args.backend == "vibevoice":
        gpu_device = args.device if args.device not in (None, "cpu") else "auto"
        return [("cpu", {"device": "cpu"}), ("gpu", {"device": gpu_device})]
    if args.backend in ("qwen3", "chatterbox", "kokoro", "neutts", "omnivoice"):
        gpu_device = args.device if args.device not in (None, "cpu") else "auto"
        return [("cpu", {"device": "cpu"}), ("gpu", {"device": gpu_device})]
    if args.backend == "onnx":
        gpu_provider = args.provider if args.provider not in (None, "cpu") else "auto"
        return [("cpu", {"provider": "cpu"}), ("gpu", {"provider": gpu_provider})]
    if args.backend == "system":
        return [("system", {})]
    raise SpeechError(f"Unsupported benchmark backend: {args.backend}")


def _temporary_audio(backend: str) -> Path:
    suffix = ".aiff" if backend == "system" else ".wav"
    handle = NamedTemporaryFile(suffix=suffix, delete=False)
    handle.close()
    return Path(handle.name)


def _print_benchmark(rows: list[dict[str, object]]) -> None:
    print("variant\trun\tlatency_s\tbattery_before\twatts_before\tbattery_after\twatts_after\tnote")
    for row in rows:
        before = row["battery_before"]
        after = row["battery_after"]
        assert isinstance(before, PowerSample)
        assert isinstance(after, PowerSample)
        note = row["error"] or after.note or before.note
        print(
            "\t".join(
                (
                    str(row["variant"]),
                    str(row["run"]),
                    f"{float(row['latency_s']):.3f}",
                    _format_percent(before.battery_percent),
                    _format_watts(before.watts),
                    _format_percent(after.battery_percent),
                    _format_watts(after.watts),
                    str(note),
                )
            )
        )


def _format_percent(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _format_watts(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}W"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tts")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("speak", parents=[_speak_parser(add_help=False)], add_help=True)
    subcommands.add_parser("benchmark", parents=[_benchmark_parser(add_help=False)], add_help=True)
    return parser


def _speak_parser(prog: Optional[str] = None, add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, add_help=add_help)
    _add_speak_arguments(parser)
    return parser


def _benchmark_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=add_help)
    _add_speak_arguments(parser)
    parser.add_argument("--runs", type=int, default=3, help="Runs per CPU/GPU variant.")
    return parser


def _add_speak_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", nargs="*", help="Text to speak.")
    parser.add_argument("--config", help="Read defaults from this config file.")
    parser.add_argument("--no-config", action="store_true", help="Ignore config files.")
    parser.add_argument("--print-config-path", action="store_true", help="Print the default config path.")
    parser.add_argument("--text-stdin", action="store_true", help="Read text from stdin.")
    parser.add_argument("--level", help="Caller-defined importance label. Not interpreted by tts.")
    parser.add_argument("--title", help="Short spoken title.")
    parser.add_argument("--body", help="Spoken update body.")
    parser.add_argument(
        "--backend",
        choices=("auto", "vibevoice", "qwen3", "chatterbox", "kokoro", "neutts", "omnivoice", "onnx", "system"),
        default=None,
        help="TTS backend.",
    )
    parser.add_argument("--output", help="Write WAV output to this path.")
    parser.add_argument("--no-play", action="store_true", help="Generate only; do not play audio.")
    parser.add_argument("--print-result", action="store_true", help="Print output path or backend.")
    parser.add_argument("--voice", help="Voice name for system backend.")
    parser.add_argument("--speaker", help="Speaker id/name for model backends.")
    parser.add_argument("--speed", type=float, help="Speech speed for supported backends.")
    parser.add_argument("--language", help="Language or language code for model backends.")
    parser.add_argument("--instruct", help="Instruction prompt for supported model backends.")
    parser.add_argument("--reference-audio", help="Reference audio path for cloning/prompted model backends.")
    parser.add_argument("--reference-text", help="Transcript for --reference-audio.")
    parser.add_argument("--exaggeration", type=float, help="Chatterbox expression strength.")
    parser.add_argument("--cfg-weight", type=float, help="Chatterbox CFG weight.")

    parser.add_argument("--model-size", help="VibeVoice size: 0.5 or 1.5.")
    parser.add_argument("--model", help="Explicit Hugging Face model id/path.")
    parser.add_argument("--device", help="Transformers device, e.g. cpu, cuda, mps, 0.")

    parser.add_argument("--provider", help="sherpa-onnx provider: cpu, cuda, coreml.")
    parser.add_argument("--num-threads", type=int, help="sherpa-onnx compute threads.")
    parser.add_argument("--onnx-kind", choices=("vits", "matcha", "kokoro", "kitten"))
    parser.add_argument("--vits-model")
    parser.add_argument("--vits-lexicon")
    parser.add_argument("--vits-tokens")
    parser.add_argument("--vits-data-dir")
    parser.add_argument("--matcha-acoustic-model")
    parser.add_argument("--matcha-vocoder")
    parser.add_argument("--matcha-lexicon")
    parser.add_argument("--matcha-tokens")
    parser.add_argument("--matcha-data-dir")
    parser.add_argument("--kokoro-model")
    parser.add_argument("--kokoro-voices")
    parser.add_argument("--kokoro-tokens")
    parser.add_argument("--kokoro-data-dir")
    parser.add_argument("--kokoro-lexicon")
    parser.add_argument("--kitten-model")
    parser.add_argument("--kitten-voices")
    parser.add_argument("--kitten-tokens")
    parser.add_argument("--kitten-data-dir")
    parser.add_argument("--tts-rule-fsts")
    parser.add_argument("--max-num-sentences", type=int)
    parser.add_argument("--debug", action="store_true", default=None)
