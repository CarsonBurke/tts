#!/usr/bin/env python3
"""Standalone MPRIS helper for TTS playback control.

Intended to run under a system Python that has dbus-python and PyGObject
(python-dbus / python-gobject), not necessarily the TTS virtualenv.

Session mode (--session) keeps org.mpris.MediaPlayer2.tts on the bus for the
lifetime of the TTS daemon / speech session, watching playback.json the same
way Spotify stays registered while the app is open.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path


MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.tts"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class StateController:
    """Control whatever utterance is described by playback.json."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def read(self) -> dict | None:
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def write(self, data: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.state_path)

    def clear(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def title(self) -> str:
        state = self.read() or {}
        return str(state.get("title") or "Speech")

    def status(self) -> str:
        state = self.read()
        if not state:
            return "Stopped"
        phase = state.get("phase") or "playing"
        if phase == "pending":
            if state.get("cancelled") or state.get("paused"):
                return "Paused"
            return "Playing"
        pid = state.get("pid")
        if pid is None:
            return "Stopped"
        pid = int(pid)
        if not _pid_alive(pid):
            return "Stopped"
        return "Paused" if _pid_paused(pid) else "Playing"

    def pause(self) -> None:
        state = self.read()
        if not state:
            return
        phase = state.get("phase") or "playing"
        if phase == "pending":
            state["paused"] = True
            state["cancelled"] = True
            self.write(state)
            return
        pid = state.get("pid")
        if pid is None:
            return
        pid = int(pid)
        if not _pid_alive(pid) or _pid_paused(pid):
            return
        try:
            os.kill(pid, signal.SIGSTOP)
        except OSError:
            return

    def play(self) -> None:
        state = self.read()
        if not state:
            return
        phase = state.get("phase") or "playing"
        if phase == "pending":
            return
        pid = state.get("pid")
        if pid is None:
            return
        pid = int(pid)
        if not _pid_alive(pid) or not _pid_paused(pid):
            return
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            return

    def play_pause(self) -> None:
        if self.status() == "Paused":
            self.play()
        elif self.status() == "Playing":
            self.pause()

    def stop(self) -> None:
        state = self.read()
        if not state:
            return
        phase = state.get("phase") or "playing"
        pid = state.get("pid")
        if phase == "pending" or pid is None:
            state["cancelled"] = True
            state["paused"] = True
            self.write(state)
            self.clear()
            return
        pid = int(pid)
        if _pid_paused(pid):
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
                pass
        self.clear()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_paused(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    state = _process_state(pid)
    return state == "T"


def _process_state(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            raw = open(f"/proc/{pid}/stat", encoding="utf-8").read()
        except OSError:
            return None
        close = raw.rfind(")")
        if close == -1 or close + 2 >= len(raw):
            return None
        rest = raw[close + 2 :].split()
        return rest[0] if rest else None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MPRIS helper for tts playback.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--session", action="store_true", help="Long-lived player watching a state file.")
    mode.add_argument("--pid", type=int, help="Legacy one-shot mode: control a single player PID.")
    parser.add_argument("--state-path", help="playback.json path (required for --session).")
    parser.add_argument("--title", default="Speech", help="Track title for legacy --pid mode.")
    parser.add_argument(
        "--idle-seconds",
        type=int,
        default=0,
        help="In session mode, exit after this many idle seconds (0 = run until killed).",
    )
    args = parser.parse_args(argv)

    try:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except Exception as exc:
        print(f"tts-mpris: missing dbus bindings: {exc}", file=sys.stderr)
        return 2

    if args.session:
        if not args.state_path:
            print("tts-mpris: --state-path is required with --session", file=sys.stderr)
            return 2
        controller = StateController(Path(args.state_path))
        legacy_pid = None
    else:
        controller = StateController(Path(os.devnull))  # unused
        legacy_pid = args.pid
        # Legacy one-shot: synthesize a tiny state file in memory via wrapper.
        controller = _LegacyPidController(args.pid, args.title)
        if controller.status() == "Stopped":
            return 0

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    bus_name = None
    # Always prefer the stable well-known name so playerctl/playerctld treat us
    # like any other media app.
    try:
        bus_name = dbus.service.BusName(MPRIS_BUS_NAME, bus, do_not_queue=True)
    except Exception:
        if legacy_pid is not None:
            try:
                bus_name = dbus.service.BusName(
                    f"{MPRIS_BUS_NAME}.instance{legacy_pid}", bus, do_not_queue=True
                )
            except Exception as exc:
                print(f"tts-mpris: could not claim bus name: {exc}", file=sys.stderr)
                return 3
        else:
            # Another session helper already owns the name; exit quietly.
            return 0
    if bus_name is None:
        print("tts-mpris: could not claim bus name", file=sys.stderr)
        return 3

    loop = GLib.MainLoop()
    last_status = {"value": "Stopped"}
    last_activity = {"value": time.monotonic()}

    class MprisObject(dbus.service.Object):
        def __init__(self) -> None:
            super().__init__(bus, MPRIS_OBJECT_PATH)

        @dbus.service.method(ROOT_IFACE)
        def Raise(self) -> None:
            return None

        @dbus.service.method(ROOT_IFACE)
        def Quit(self) -> None:
            controller.stop()
            self._emit_status()
            if not args.session:
                GLib.idle_add(loop.quit)

        @dbus.service.method(PLAYER_IFACE)
        def Next(self) -> None:
            return None

        @dbus.service.method(PLAYER_IFACE)
        def Previous(self) -> None:
            return None

        @dbus.service.method(PLAYER_IFACE)
        def Pause(self) -> None:
            controller.pause()
            self._emit_status()

        @dbus.service.method(PLAYER_IFACE)
        def PlayPause(self) -> None:
            controller.play_pause()
            self._emit_status()

        @dbus.service.method(PLAYER_IFACE)
        def Stop(self) -> None:
            controller.stop()
            self._emit_status()
            if not args.session:
                GLib.idle_add(loop.quit)

        @dbus.service.method(PLAYER_IFACE)
        def Play(self) -> None:
            controller.play()
            self._emit_status()

        @dbus.service.method(PLAYER_IFACE, in_signature="x")
        def Seek(self, offset: int) -> None:
            return None

        @dbus.service.method(PLAYER_IFACE, in_signature="ox")
        def SetPosition(self, track_id, position: int) -> None:
            return None

        @dbus.service.method(PLAYER_IFACE, in_signature="s")
        def OpenUri(self, uri: str) -> None:
            return None

        @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface: str, prop: str):
            props = self.GetAll(interface)
            if prop not in props:
                raise dbus.exceptions.DBusException(
                    f"Property {prop} not found on {interface}",
                    name="org.freedesktop.DBus.Error.InvalidArgs",
                )
            return props[prop]

        @dbus.service.method(PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface: str):
            status = controller.status()
            can_control = status != "Stopped"
            if interface == ROOT_IFACE:
                return dbus.Dictionary(
                    {
                        "CanQuit": dbus.Boolean(True),
                        "CanRaise": dbus.Boolean(False),
                        "HasTrackList": dbus.Boolean(False),
                        "Identity": dbus.String("TTS"),
                        "DesktopEntry": dbus.String("tts"),
                        "SupportedUriSchemes": dbus.Array([], signature="s"),
                        "SupportedMimeTypes": dbus.Array(
                            ["audio/wav", "audio/x-wav"], signature="s"
                        ),
                    },
                    signature="sv",
                )
            if interface == PLAYER_IFACE:
                return dbus.Dictionary(
                    {
                        "PlaybackStatus": dbus.String(status),
                        "LoopStatus": dbus.String("None"),
                        "Rate": dbus.Double(1.0),
                        "Shuffle": dbus.Boolean(False),
                        "Metadata": dbus.Dictionary(
                            {
                                "mpris:trackid": dbus.ObjectPath(
                                    "/org/mpris/MediaPlayer2/tts/track/1"
                                ),
                                "xesam:title": dbus.String(controller.title()),
                                "xesam:artist": dbus.Array(
                                    [dbus.String("TTS")], signature="s"
                                ),
                                "xesam:album": dbus.String("Agent speech"),
                            },
                            signature="sv",
                        ),
                        "Volume": dbus.Double(1.0),
                        "Position": dbus.Int64(0),
                        "MinimumRate": dbus.Double(1.0),
                        "MaximumRate": dbus.Double(1.0),
                        "CanGoNext": dbus.Boolean(False),
                        "CanGoPrevious": dbus.Boolean(False),
                        "CanPlay": dbus.Boolean(can_control),
                        "CanPause": dbus.Boolean(can_control),
                        "CanSeek": dbus.Boolean(False),
                        "CanControl": dbus.Boolean(True),
                    },
                    signature="sv",
                )
            raise dbus.exceptions.DBusException(
                f"Unknown interface {interface}",
                name="org.freedesktop.DBus.Error.InvalidArgs",
            )

        @dbus.service.method(PROPERTIES_IFACE, in_signature="ssv")
        def Set(self, interface: str, prop: str, value) -> None:
            return None

        @dbus.service.signal(PROPERTIES_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated) -> None:
            return None

        def _emit_status(self) -> None:
            status = controller.status()
            last_status["value"] = status
            if status != "Stopped":
                last_activity["value"] = time.monotonic()
            changed = dbus.Dictionary(
                {
                    "PlaybackStatus": dbus.String(status),
                    "CanPlay": dbus.Boolean(status != "Stopped"),
                    "CanPause": dbus.Boolean(status != "Stopped"),
                    "Metadata": dbus.Dictionary(
                        {
                            "mpris:trackid": dbus.ObjectPath(
                                "/org/mpris/MediaPlayer2/tts/track/1"
                            ),
                            "xesam:title": dbus.String(controller.title()),
                            "xesam:artist": dbus.Array(
                                [dbus.String("TTS")], signature="s"
                            ),
                            "xesam:album": dbus.String("Agent speech"),
                        },
                        signature="sv",
                    ),
                },
                signature="sv",
            )
            self.PropertiesChanged(PLAYER_IFACE, changed, dbus.Array([], signature="s"))

    player = MprisObject()
    player._emit_status()

    def poll_state() -> bool:
        status = controller.status()
        if status != last_status["value"]:
            # Transition into Playing is what makes playerctld select us.
            player._emit_status()
        elif status == "Playing":
            # Keep TTS selected over browsers that chatter on MPRIS.
            player._emit_status()
            last_activity["value"] = time.monotonic()
        elif status == "Stopped" and not args.session:
            loop.quit()
            return False
        elif (
            args.session
            and args.idle_seconds > 0
            and status == "Stopped"
            and time.monotonic() - last_activity["value"] >= args.idle_seconds
        ):
            loop.quit()
            return False
        return True

    GLib.timeout_add(250, poll_state)
    try:
        loop.run()
    finally:
        del player
        del bus_name
    return 0


class _LegacyPidController:
    """One-shot controller for a single paplay PID (back-compat)."""

    def __init__(self, pid: int, title: str) -> None:
        self.pid = pid
        self._title = title

    def title(self) -> str:
        return self._title

    def status(self) -> str:
        if not _pid_alive(self.pid):
            return "Stopped"
        return "Paused" if _pid_paused(self.pid) else "Playing"

    def pause(self) -> None:
        if self.status() != "Playing":
            return
        try:
            os.kill(self.pid, signal.SIGSTOP)
        except OSError:
            return

    def play(self) -> None:
        if self.status() != "Paused":
            return
        try:
            os.kill(self.pid, signal.SIGCONT)
        except OSError:
            return

    def play_pause(self) -> None:
        if self.status() == "Paused":
            self.play()
        elif self.status() == "Playing":
            self.pause()

    def stop(self) -> None:
        if self.status() == "Stopped":
            return
        if _pid_paused(self.pid):
            try:
                os.kill(self.pid, signal.SIGCONT)
            except OSError:
                pass
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                return


if __name__ == "__main__":
    raise SystemExit(main())
