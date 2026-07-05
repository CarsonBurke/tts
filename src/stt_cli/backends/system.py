from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import SpeakRequest, SpeechResult
from ..audio import SpeechError, play_wav


def speak(request: SpeakRequest) -> SpeechResult:
    if request.output is not None:
        result = _write_to_file(request)
        if request.play:
            play_wav(request.output)
        return result
    if not request.play:
        raise SpeechError("System backend needs --output when --no-play is set.")

    command = _speak_command(request)
    if command is None:
        raise SpeechError("No system TTS command found.")
    subprocess.run(command, check=True)
    return SpeechResult(backend="system")


def _write_to_file(request: SpeakRequest) -> SpeechResult:
    path = request.output
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "darwin" and shutil.which("say"):
        command = ["say", "-o", str(path), request.text]
        if request.voice:
            command[1:1] = ["-v", request.voice]
        if request.speed != 1.0:
            command[1:1] = ["-r", str(_words_per_minute(request.speed))]
        subprocess.run(command, check=True)
        return SpeechResult(backend="system", output_path=path)

    raise SpeechError("The system backend can only write files through macOS say.")


def _speak_command(request: SpeakRequest) -> Optional[list[str]]:
    if sys.platform == "darwin" and shutil.which("say"):
        command = ["say"]
        if request.voice:
            command.extend(["-v", request.voice])
        if request.speed != 1.0:
            command.extend(["-r", str(_words_per_minute(request.speed))])
        command.append(request.text)
        return command

    if sys.platform == "win32":
        voice_filter = ""
        if request.voice:
            escaped_voice = request.voice.replace("'", "''")
            voice_filter = (
                "$voice=$synth.GetInstalledVoices() | "
                f"Where-Object {{$_.VoiceInfo.Name -eq '{escaped_voice}'}} | "
                "Select-Object -First 1;"
                "if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) };"
            )
        escaped_text = request.text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$synth=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"{voice_filter}"
            f"$synth.Rate={_windows_rate(request.speed)};"
            f"$synth.Speak('{escaped_text}')"
        )
        return ["powershell", "-NoProfile", "-Command", script]

    for name in ("spd-say", "espeak-ng", "espeak"):
        exe = shutil.which(name)
        if not exe:
            continue
        if name == "spd-say":
            command = [exe]
            if request.voice:
                command.extend(["-o", request.voice])
            if request.speed != 1.0:
                command.extend(["-r", str(_speech_dispatcher_rate(request.speed))])
            command.append(request.text)
            return command
        command = [exe]
        if request.voice:
            command.extend(["-v", request.voice])
        if request.speed != 1.0:
            command.extend(["-s", str(_words_per_minute(request.speed))])
        command.append(request.text)
        return command

    return None


def _words_per_minute(speed: float) -> int:
    return max(80, min(450, round(175 * speed)))


def _windows_rate(speed: float) -> int:
    return max(-10, min(10, round((speed - 1.0) * 10)))


def _speech_dispatcher_rate(speed: float) -> int:
    return max(-100, min(100, round((speed - 1.0) * 100)))
