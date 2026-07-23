from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional


# Active player PIDs so process/daemon exit can unstick SIGSTOP'd audio.
_ACTIVE_PLAYERS: set[int] = set()
_ACTIVE_LOCK = threading.Lock()
_CLEANUP_REGISTERED = False


class PlaybackController:
    """Pause/resume/stop a child audio process (SIGSTOP/SIGCONT on Unix)."""

    def __init__(self, process, title: str = "Speech") -> None:
        self.process = process
        self.title = title
        self._paused = False
        self._lock = threading.Lock()

    def status(self) -> str:
        with self._lock:
            if self.process.poll() is not None:
                return "Stopped"
            return "Paused" if self._paused else "Playing"

    def pause(self) -> None:
        with self._lock:
            if self.process.poll() is not None or self._paused:
                return
            if not _supports_signal_pause():
                return
            self.process.send_signal(signal.SIGSTOP)
            self._paused = True

    def play(self) -> None:
        with self._lock:
            if self.process.poll() is not None or not self._paused:
                return
            if not _supports_signal_pause():
                return
            self.process.send_signal(signal.SIGCONT)
            self._paused = False

    def play_pause(self) -> None:
        with self._lock:
            paused = self._paused
        if paused:
            self.play()
        else:
            self.pause()

    def stop(self) -> None:
        with self._lock:
            if self.process.poll() is not None:
                return
            if self._paused and _supports_signal_pause():
                try:
                    self.process.send_signal(signal.SIGCONT)
                except ProcessLookupError:
                    return
                self._paused = False
            try:
                self.process.terminate()
            except ProcessLookupError:
                return


def available() -> bool:
    if not _supports_signal_pause():
        return False
    return _helper_python() is not None


def run_with_player(process, title: str = "Speech") -> int:
    """Wait for *process*, exposing MPRIS controls when possible. Returns exit code."""
    _register_cleanup()
    if process.pid:
        _track_player(process.pid)
    helper = _start_helper(process.pid, title) if process.pid else None
    try:
        return _wait_for_process(process, helper)
    finally:
        if helper is not None:
            _stop_helper(helper)
        _unstick_and_reap(process)
        if process.pid:
            _untrack_player(process.pid)


def _wait_for_process(process: subprocess.Popen, helper: Optional[subprocess.Popen]) -> int:
    """Wait for the player; if the MPRIS helper dies while paused, resume playback."""
    helper_failed = False
    while True:
        try:
            returncode = process.wait(timeout=0.25)
            return 0 if returncode is None else returncode
        except subprocess.TimeoutExpired:
            pass
        if helper is not None and not helper_failed and helper.poll() is not None:
            # Helper crashed or exited while the player may still be SIGSTOP'd.
            helper_failed = True
            _cont_process(process)
    # Unreachable; loop either returns or continues until process exits.


def _start_helper(pid: int, title: str) -> Optional[subprocess.Popen]:
    python = _helper_python()
    helper_path = Path(__file__).with_name("mpris_helper.py")
    if python is None or not helper_path.is_file():
        return None
    try:
        return subprocess.Popen(
            [python, str(helper_path), "--pid", str(pid), "--title", title],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None


def _stop_helper(helper: subprocess.Popen) -> None:
    if helper.poll() is not None:
        return
    try:
        helper.terminate()
    except ProcessLookupError:
        return
    try:
        helper.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            helper.kill()
        except ProcessLookupError:
            return
        helper.wait()


def _unstick_and_reap(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    _cont_process(process)
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


def _cont_process(process: subprocess.Popen) -> None:
    if not _supports_signal_pause() or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGCONT)
    except ProcessLookupError:
        return


def _track_player(pid: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PLAYERS.add(pid)


def _untrack_player(pid: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PLAYERS.discard(pid)


def _register_cleanup() -> None:
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    atexit.register(_cleanup_active_players)
    _CLEANUP_REGISTERED = True


def _cleanup_active_players() -> None:
    with _ACTIVE_LOCK:
        pids = list(_ACTIVE_PLAYERS)
        _ACTIVE_PLAYERS.clear()
    for pid in pids:
        _kill_pid(pid)


def _kill_pid(pid: int) -> None:
    if not _supports_signal_pause():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        return
    try:
        os.kill(pid, signal.SIGCONT)
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return


_HELPER_PYTHON_CACHE: Optional[str] = None
_HELPER_PYTHON_RESOLVED = False


def _helper_python() -> Optional[str]:
    global _HELPER_PYTHON_CACHE, _HELPER_PYTHON_RESOLVED
    if _HELPER_PYTHON_RESOLVED and "TTS_MPRIS_PYTHON" not in os.environ:
        return _HELPER_PYTHON_CACHE

    override = os.environ.get("TTS_MPRIS_PYTHON")
    candidates: list[str] = []
    if override:
        candidates.append(override)
    # Prefer a system interpreter; the TTS venv often lacks dbus/gi.
    candidates.extend(
        [
            "/usr/bin/python3",
            "/usr/local/bin/python3",
            sys.executable,
        ]
    )
    seen: set[str] = set()
    chosen: Optional[str] = None
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if _python_has_mpris_deps(candidate):
            chosen = candidate
            break

    if "TTS_MPRIS_PYTHON" not in os.environ:
        _HELPER_PYTHON_CACHE = chosen
        _HELPER_PYTHON_RESOLVED = True
    return chosen


def _python_has_mpris_deps(python: str) -> bool:
    try:
        completed = subprocess.run(
            [
                python,
                "-c",
                "import dbus; from dbus.mainloop.glib import DBusGMainLoop; from gi.repository import GLib",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _supports_signal_pause() -> bool:
    return sys.platform != "win32" and hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")


