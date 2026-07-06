from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from .backends import SpeakRequest, SpeechResult

DEFAULT_IDLE_SECONDS = 30 * 60
DEFAULT_CONNECT_TIMEOUT = 0.25
DEFAULT_START_TIMEOUT = 45.0


class DaemonError(RuntimeError):
    pass


def state_path() -> Path:
    return runtime_dir() / "daemon.json"


def runtime_dir() -> Path:
    override = os.environ.get("TTS_RUNTIME_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        user = _user_id()
        base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        path = base / f"tts-{user}"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return runtime_dir() / "daemon.log"


def status() -> dict[str, Any]:
    state = _read_state()
    if state is None:
        return {"running": False}
    try:
        response = _request({"command": "ping"}, state, timeout=DEFAULT_CONNECT_TIMEOUT)
    except OSError:
        _unlink_state()
        return {"running": False}
    if not response.get("ok"):
        return {"running": False, "error": response.get("error", "daemon did not answer")}
    return {"running": True, **state, **response}


def ensure_running(config_path: Optional[str], idle_seconds: int, timeout: float = DEFAULT_START_TIMEOUT) -> dict[str, Any]:
    current = status()
    if current.get("running"):
        return current

    start(config_path=config_path, idle_seconds=idle_seconds, timeout=timeout)
    current = status()
    if current.get("running"):
        return current
    raise DaemonError("daemon did not start")


def start(config_path: Optional[str], idle_seconds: int, timeout: float = DEFAULT_START_TIMEOUT) -> dict[str, Any]:
    current = status()
    if current.get("running"):
        return current

    _unlink_state()
    command = _daemon_command(config_path, idle_seconds)
    log = log_path()
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = log.open("ab")
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            close_fds=(sys.platform != "win32"),
            start_new_session=(sys.platform != "win32"),
            creationflags=_windows_detached_flags(),
        )
    finally:
        handle.close()

    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        state = _read_state()
        if state is not None:
            try:
                response = _request({"command": "ping"}, state, timeout=0.5)
            except OSError as exc:
                last_error = str(exc)
            else:
                if response.get("ok") and response.get("ready"):
                    return {"running": True, **state, **response}
                last_error = response.get("error", "daemon is not ready")
        time.sleep(0.1)

    raise DaemonError(f"daemon did not become ready within {timeout:.0f}s: {last_error or 'no state'}")


def stop(timeout: float = 2.0) -> bool:
    state = _read_state()
    if state is None:
        return False
    try:
        _request({"command": "stop"}, state, timeout=timeout)
    except OSError:
        _unlink_state()
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _read_state() is None:
            return True
        time.sleep(0.05)
    return True


def speak(request: SpeakRequest, timeout: Optional[float] = None) -> SpeechResult:
    state = _read_state()
    if state is None:
        raise DaemonError("daemon is not running")
    payload = {
        "command": "speak",
        "request": _request_to_json(request),
    }
    response = _request(payload, state, timeout=timeout)
    if not response.get("ok"):
        raise DaemonError(str(response.get("error", "daemon speak failed")))
    output_path = response.get("output_path")
    return SpeechResult(
        backend=str(response.get("backend") or "kokoro"),
        sample_rate=response.get("sample_rate"),
        output_path=Path(output_path) if output_path else None,
    )


def serve(config_path: Optional[str], idle_seconds: int) -> None:
    import socketserver
    import threading

    from .audio import SpeechError
    from .config import load_config
    from .config import resolve_option

    config = load_config(config_path, disabled=False)
    token = secrets.token_urlsafe(32)
    server_state = {
        "host": "127.0.0.1",
        "port": 0,
        "token": token,
        "pid": os.getpid(),
        "idle_seconds": idle_seconds,
        "ready": False,
        "started_at": time.time(),
    }
    last_activity = time.monotonic()
    should_stop = threading.Event()
    speak_lock = threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            nonlocal last_activity
            raw = self.rfile.readline(1024 * 1024)
            if not raw:
                return
            try:
                message = json.loads(raw.decode("utf-8"))
                if message.get("token") != token:
                    raise DaemonError("invalid daemon token")
                command = message.get("command")
                last_activity = time.monotonic()
                if command == "ping":
                    self._write({"ok": True, "ready": server_state["ready"], "pid": os.getpid()})
                    return
                if command == "stop":
                    self._write({"ok": True})
                    should_stop.set()
                    return
                if command == "speak":
                    if not server_state["ready"]:
                        raise DaemonError("daemon is not ready")
                    request = _request_from_json(message.get("request") or {})
                    with speak_lock:
                        result = _speak_kokoro(request)
                    self._write(
                        {
                            "ok": True,
                            "backend": result.backend,
                            "sample_rate": result.sample_rate,
                            "output_path": str(result.output_path) if result.output_path else None,
                        }
                    )
                    return
                if command == "speak_args":
                    if not server_state["ready"]:
                        raise DaemonError("daemon is not ready")
                    request = _request_from_argv(
                        message.get("args") or [],
                        message.get("cwd"),
                        message.get("tts_config"),
                    )
                    with speak_lock:
                        result = _speak_kokoro(request)
                    self._write(
                        {
                            "ok": True,
                            "backend": result.backend,
                            "sample_rate": result.sample_rate,
                            "output_path": str(result.output_path) if result.output_path else None,
                        }
                    )
                    return
                raise DaemonError(f"unknown daemon command: {command}")
            except (DaemonError, SpeechError, subprocess.SubprocessError) as exc:
                self._write({"ok": False, "error": str(exc)})
            except Exception as exc:
                self._write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        def _write(self, response: dict[str, Any]) -> None:
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))

    class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        server_state["port"] = int(server.server_address[1])
        try:
            _warm_kokoro(config, resolve_option)
            server_state["ready"] = True
            _write_state(server_state)
            while not should_stop.is_set():
                server.handle_request()
                if time.monotonic() - last_activity >= idle_seconds:
                    break
        finally:
            _unlink_state()


def _speak_kokoro(request: SpeakRequest) -> SpeechResult:
    from .backends import kokoro

    return kokoro.speak(request)


def _warm_kokoro(config: dict[str, object], resolve_option) -> None:
    from .backends import kokoro

    request = SpeakRequest(
        text="ready",
        output=None,
        play=False,
        speaker=resolve_option("speaker", None, config),
        speed=resolve_option("speed", None, config),
        device=resolve_option("device", None, config),
        model=resolve_option("model", None, config),
        language=resolve_option("language", None, config),
    )
    kokoro.warm(request)


def _request(payload: dict[str, Any], state: dict[str, Any], timeout: Optional[float]) -> dict[str, Any]:
    message = {**payload, "token": state["token"]}
    with socket.create_connection((state["host"], int(state["port"])), timeout=timeout) as sock:
        with sock.makefile("rwb") as stream:
            stream.write((json.dumps(message) + "\n").encode("utf-8"))
            stream.flush()
            raw = stream.readline()
    if not raw:
        raise DaemonError("daemon closed connection")
    return json.loads(raw.decode("utf-8"))


def _daemon_command(config_path: Optional[str], idle_seconds: int) -> list[str]:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "daemon", "serve"]
    else:
        command = [sys.executable, "-m", "tts", "daemon", "serve"]
    if config_path:
        command.extend(["--config", config_path])
    command.extend(["--idle-seconds", str(idle_seconds)])
    return command


def _request_to_json(request: SpeakRequest) -> dict[str, Any]:
    values = asdict(request)
    for key in ("output", "reference_audio"):
        if values[key] is not None:
            values[key] = str(values[key])
    return values


def _request_from_json(values: dict[str, Any]) -> SpeakRequest:
    if values.get("output"):
        values["output"] = Path(values["output"])
    if values.get("reference_audio"):
        values["reference_audio"] = Path(values["reference_audio"])
    return SpeakRequest(**values)


def _request_from_argv(argv: list[str], cwd: Optional[str], tts_config: Optional[str]) -> SpeakRequest:
    from . import cli
    from .audio import SpeechError
    from .config import load_config

    with _pushd(cwd):
        try:
            args = cli._speak_parser(prog="tts speak").parse_args([str(arg) for arg in argv])
        except SystemExit as exc:
            raise DaemonError(f"invalid speak arguments: exit {exc.code}") from exc
        if tts_config and not args.config:
            args.config = tts_config
        config = load_config(args.config, args.no_config)
        cli._apply_defaults(args, config)
        if args.backend != "kokoro":
            raise SpeechError("daemon speech currently supports the Kokoro backend only")
        if not args.daemon:
            raise SpeechError("daemon disabled for this request")
        text = cli._resolve_text(args)
        return cli._request_from_args(args, text, cli._path_arg(args.output), not args.no_play)


@contextmanager
def _pushd(cwd: Optional[str]):
    if not cwd:
        yield
        return
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(previous)


def _read_state() -> Optional[dict[str, Any]]:
    path = state_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_state(state: dict[str, Any]) -> None:
    path = state_path()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _unlink_state() -> None:
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass


def _user_id() -> str:
    if hasattr(os, "getuid"):
        return str(os.getuid())
    return os.environ.get("USERNAME") or os.environ.get("USER") or "user"


def _windows_detached_flags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
