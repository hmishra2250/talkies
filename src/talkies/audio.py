from __future__ import annotations

import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AudioConfig


class AudioError(RuntimeError):
    pass


def default_monitor_source(run: callable = subprocess.run) -> str | None:
    result = run(
        ["pactl", "get-default-sink"],
        check=False,
        capture_output=True,
        text=True,
    )
    sink = result.stdout.strip()
    return f"{sink}.monitor" if result.returncode == 0 and sink else None


def recording_command(
    destination: Path,
    config: AudioConfig,
    include_system_audio: bool = False,
    monitor_source: str | None = None,
) -> list[str]:
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "pulse",
        "-i",
        config.microphone,
    ]
    if include_system_audio and monitor_source:
        command += [
            "-f",
            "pulse",
            "-i",
            monitor_source,
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0[a]",
            "-map",
            "[a]",
        ]
    command += [
        "-ac",
        str(config.channels),
        "-ar",
        str(config.sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    return command


@dataclass(slots=True)
class AudioRecorder:
    config: AudioConfig
    process: subprocess.Popen[bytes] | None = None
    destination: Path | None = None

    def start(self, destination: Path, *, include_system_audio: bool = False) -> None:
        if self.process is not None:
            raise AudioError("A recording is already in progress")
        if not shutil.which("ffmpeg"):
            raise AudioError("ffmpeg is required but was not found")
        destination.parent.mkdir(parents=True, exist_ok=True)
        monitor = default_monitor_source() if include_system_audio else None
        if include_system_audio and not monitor:
            raise AudioError(
                "No speaker monitor source was found; meeting recording was not started"
            )
        command = recording_command(destination, self.config, include_system_audio, monitor)
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.destination = destination
        try:
            self.process.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            return
        error_bytes = self.process.stderr.read() if self.process.stderr else b""
        error = error_bytes.decode(errors="replace")
        self.process = None
        self.destination = None
        raise AudioError(error.strip() or "ffmpeg could not start recording")

    def stop(self) -> Path:
        if self.process is None or self.destination is None:
            raise AudioError("No recording is in progress")
        process, destination = self.process, self.destination
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.process = None
        self.destination = None
        if not destination.exists() or destination.stat().st_size <= 44:
            error = (process.stderr.read() if process.stderr else b"").decode(errors="replace")
            raise AudioError(error.strip() or "The recording was empty")
        return destination
