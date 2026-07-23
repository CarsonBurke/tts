from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional


class PlaybackError(RuntimeError):
    pass


def state_path() -> Path:
    from .daemon import runtime_dir

    return runtime_dir() / "playback.json"


def register(pid: int, title: str = "Speech") -> None:
    """Record the active audio player so CLI controls can find it."""
    if not pid:
        return
    payload = {
        "pid": int(pid),
        "title": title,
        "started_at": time.time(),
    }
    path = state_path()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def unregister(pid: Optional[int] = None) -> None:
    """Clear playback state. If *pid* is set, only clear when it matches."""
    path = state_path()
    if pid is not None:
        current = read_state()
        if current is None or int(current.get("pid") or 0) != int(pid):
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_state() -> Optional[dict[str, Any]]:
    path = state_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "pid" not in data:
        return None
    return data


def status() -> dict[str, Any]:
    state = read_state()
    if state is None:
        return {"playing": False, "status": "Stopped"}
    pid = int(state["pid"])
    if not pid_alive(pid):
        unregister(pid)
        return {"playing": False, "status": "Stopped"}
    paused = pid_paused(pid)
    return {
        "playing": True,
        "status": "Paused" if paused else "Playing",
        "pid": pid,
        "title": state.get("title") or "Speech",
        "paused": paused,
    }


def pause() -> dict[str, Any]:
    current = _require_active()
    pid = int(current["pid"])
    if pid_paused(pid):
        return status()
    _signal_pid(pid, signal.SIGSTOP)
    return status()


def resume() -> dict[str, Any]:
    current = _require_active()
    pid = int(current["pid"])
    if not pid_paused(pid):
        return status()
    _signal_pid(pid, signal.SIGCONT)
    return status()


def play_pause() -> dict[str, Any]:
    current = _require_active()
    pid = int(current["pid"])
    if pid_paused(pid):
        _signal_pid(pid, signal.SIGCONT)
    else:
        _signal_pid(pid, signal.SIGSTOP)
    return status()


def stop() -> dict[str, Any]:
    current = _require_active()
    pid = int(current["pid"])
    if pid_paused(pid):
        try:
            _signal_pid(pid, signal.SIGCONT)
        except PlaybackError:
            pass
    try:
        _signal_pid(pid, signal.SIGTERM)
    except PlaybackError:
        try:
            _signal_pid(pid, signal.SIGKILL)
        except PlaybackError:
            pass
    unregister(pid)
    return {"playing": False, "status": "Stopped", "pid": pid}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_paused(pid: int) -> bool:
    """True when the process is stopped (e.g. after SIGSTOP)."""
    if not pid_alive(pid):
        return False
    if sys.platform.startswith("linux"):
        return _linux_state(pid) == "T"
    if sys.platform == "darwin":
        return _darwin_state(pid) in {"T", "U"}
    # Best effort elsewhere: no reliable portable check.
    return False


def _require_active() -> dict[str, Any]:
    if sys.platform == "win32":
        raise PlaybackError("playback control is not supported on Windows")
    state = read_state()
    if state is None:
        raise PlaybackError("nothing is playing")
    pid = int(state["pid"])
    if not pid_alive(pid):
        unregister(pid)
        raise PlaybackError("nothing is playing")
    return state


def _signal_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except OSError as exc:
        raise PlaybackError(f"could not signal playback process {pid}: {exc}") from exc


def _linux_state(pid: int) -> Optional[str]:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # comm may contain spaces/parens; state is the field after the closing ')'.
    close = raw.rfind(")")
    if close == -1 or close + 2 >= len(raw):
        return None
    rest = raw[close + 2 :].split()
    if not rest:
        return None
    return rest[0]


def _darwin_state(pid: int) -> Optional[str]:
    import subprocess

    try:
        completed = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (completed.stdout or "").strip()
    return value[:1] if value else None
