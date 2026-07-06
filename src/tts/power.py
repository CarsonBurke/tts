from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PowerSample:
    source: str
    battery_percent: Optional[float] = None
    watts: Optional[float] = None
    note: str = ""


def sample_power() -> PowerSample:
    if sys.platform == "darwin":
        return _sample_macos_power()
    if sys.platform.startswith("linux"):
        return _sample_linux_power()
    if sys.platform == "win32":
        return _sample_windows_power()
    return PowerSample(source="unsupported", note="No power sampler for this platform.")


def _sample_macos_power() -> PowerSample:
    try:
        result = subprocess.run(
            ["ioreg", "-rn", "AppleSmartBattery"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return PowerSample(source="macos", note="ioreg battery data unavailable.")

    values = _parse_ioreg_battery(result.stdout)
    voltage = values.get("Voltage")
    amperage = values.get("Amperage")
    current_capacity = values.get("CurrentCapacity")
    max_capacity = values.get("MaxCapacity")
    external_connected = values.get("ExternalConnected")

    percent = None
    if current_capacity is not None and max_capacity:
        percent = current_capacity / max_capacity * 100

    watts = None
    if voltage is not None and amperage is not None:
        watts = abs(voltage * amperage) / 1_000_000

    source = "ac" if external_connected == 1 else "battery"
    note = ""
    if watts is None:
        note = "Live wattage unavailable from ioreg."
    elif source == "ac":
        note = "Battery wattage may be near zero or noisy while on AC power."

    return PowerSample(source=source, battery_percent=percent, watts=watts, note=note)


def _parse_ioreg_battery(output: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in ("Voltage", "Amperage", "CurrentCapacity", "MaxCapacity"):
        match = re.search(rf'"{key}" = (-?\d+)', output)
        if match:
            values[key] = _signed_ioreg_int(int(match.group(1)))

    match = re.search(r'"ExternalConnected" = (Yes|No)', output)
    if match:
        values["ExternalConnected"] = 1 if match.group(1) == "Yes" else 0
    return values


def _signed_ioreg_int(value: int) -> int:
    if value > 2**63 - 1:
        return value - 2**64
    return value


def _sample_linux_power() -> PowerSample:
    for battery in Path("/sys/class/power_supply").glob("BAT*"):
        percent = _read_float(battery / "capacity")
        watts = _read_float(battery / "power_now")
        if watts is not None:
            watts = watts / 1_000_000
        else:
            current = _read_float(battery / "current_now")
            voltage = _read_float(battery / "voltage_now")
            if current is not None and voltage is not None:
                watts = current * voltage / 1_000_000_000_000

        source = "battery"
        status = _read_text(battery / "status")
        if status and status.lower() in ("charging", "full"):
            source = "ac"
        return PowerSample(source=source, battery_percent=percent, watts=watts)

    return PowerSample(source="linux", note="No BAT* power supply found.")


def _sample_windows_power() -> PowerSample:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Battery | Select-Object -First 1).EstimatedChargeRemaining",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return PowerSample(source="windows", note="Windows battery data unavailable.")

    text = result.stdout.strip()
    percent = float(text) if text else None
    return PowerSample(
        source="windows",
        battery_percent=percent,
        note="Windows does not expose reliable live battery wattage through Win32_Battery.",
    )


def _read_float(path: Path) -> Optional[float]:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
