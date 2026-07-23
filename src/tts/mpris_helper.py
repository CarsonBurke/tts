#!/usr/bin/env python3
"""Standalone MPRIS helper for TTS playback control.

Intended to run under a system Python that has dbus-python and PyGObject
(python-dbus / python-gobject), not necessarily the TTS virtualenv.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys


MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.tts"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class Controller:
    def __init__(self, pid: int, title: str) -> None:
        self.pid = pid
        self.title = title

    def alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def paused(self) -> bool:
        """Derive pause from process state so CLI SIGSTOP stays in sync."""
        if not self.alive():
            return False
        state = _process_state(self.pid)
        return state == "T"

    def status(self) -> str:
        if not self.alive():
            return "Stopped"
        return "Paused" if self.paused() else "Playing"

    def pause(self) -> None:
        if not self.alive() or self.paused():
            return
        try:
            os.kill(self.pid, signal.SIGSTOP)
        except OSError:
            return

    def play(self) -> None:
        if not self.alive() or not self.paused():
            return
        try:
            os.kill(self.pid, signal.SIGCONT)
        except OSError:
            return

    def play_pause(self) -> None:
        if self.paused():
            self.play()
        else:
            self.pause()

    def stop(self) -> None:
        if not self.alive():
            return
        if self.paused():
            try:
                os.kill(self.pid, signal.SIGCONT)
            except OSError:
                pass
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            # Last resort: SIGKILL works even on a stopped task.
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                return


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
    parser.add_argument("--pid", type=int, required=True, help="PID of the audio player process.")
    parser.add_argument("--title", default="Speech", help="Track title shown to media controllers.")
    args = parser.parse_args(argv)

    try:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except Exception as exc:
        print(f"tts-mpris: missing dbus bindings: {exc}", file=sys.stderr)
        return 2

    controller = Controller(args.pid, args.title)
    if not controller.alive():
        return 0

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    # Prefer the well-known name so `playerctl -p tts` works; keep instance as fallback.
    bus_name = None
    for name in (MPRIS_BUS_NAME, f"{MPRIS_BUS_NAME}.instance{args.pid}"):
        try:
            bus_name = dbus.service.BusName(name, bus, do_not_queue=True)
            break
        except Exception:
            bus_name = None
    if bus_name is None:
        print("tts-mpris: could not claim bus name", file=sys.stderr)
        return 3

    loop = GLib.MainLoop()

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
                        "PlaybackStatus": dbus.String(controller.status()),
                        "LoopStatus": dbus.String("None"),
                        "Rate": dbus.Double(1.0),
                        "Shuffle": dbus.Boolean(False),
                        "Metadata": dbus.Dictionary(
                            {
                                "mpris:trackid": dbus.ObjectPath(
                                    "/org/mpris/MediaPlayer2/tts/track/1"
                                ),
                                "xesam:title": dbus.String(controller.title),
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
                        "CanPlay": dbus.Boolean(True),
                        "CanPause": dbus.Boolean(True),
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
            self.PropertiesChanged(
                PLAYER_IFACE,
                dbus.Dictionary(
                    {"PlaybackStatus": dbus.String(controller.status())},
                    signature="sv",
                ),
                dbus.Array([], signature="s"),
            )

    player = MprisObject()
    # Advertise Playing immediately so playerctld/playerctl prefer this player.
    player._emit_status()

    def watch_target() -> bool:
        if controller.alive():
            return True
        loop.quit()
        return False

    GLib.timeout_add(200, watch_target)
    try:
        loop.run()
    finally:
        del player
        del bus_name
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
