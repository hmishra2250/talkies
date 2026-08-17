from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from .audio import AudioRecorder
from .config import Config, data_dir, ensure_user_dirs, load_config, runtime_dir
from .injector import InjectionError, TextInjector
from .store import TranscriptStore, write_note
from .text import normalize_text
from .transcriber import Transcriber

LOG = logging.getLogger(__name__)
VALID_MODES = {"dictation", "note", "meeting"}


def socket_path() -> Path:
    return runtime_dir() / "talkies.sock"


def notify(summary: str, body: str = "", *, urgency: str = "normal") -> None:
    command = ["notify-send", "--app-name=Talkies", f"--urgency={urgency}", summary]
    if body:
        command.append(body)
    try:
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        LOG.info("%s: %s", summary, body)


@dataclass(slots=True)
class ActiveRecording:
    mode: str
    started_at: datetime
    path: Path


class TalkiesService:
    def __init__(self, config: Config | None = None) -> None:
        ensure_user_dirs()
        self.config = config or load_config()
        self.recorder = AudioRecorder(self.config.audio)
        self.transcriber = Transcriber(self.config.transcription)
        self.injector = TextInjector(
            restore_clipboard=self.config.behavior.restore_clipboard,
            restore_delay_ms=self.config.behavior.clipboard_restore_delay_ms,
        )
        self.store = TranscriptStore()
        self.active: ActiveRecording | None = None
        self._operation_lock = threading.Lock()

    def warm_model(self) -> None:
        try:
            self.transcriber.load()
            LOG.info("Loaded %s on %s", self.config.transcription.model, self.transcriber.backend)
        except Exception as exc:
            LOG.exception("Model warm-up failed")
            notify("Talkies model error", str(exc), urgency="critical")

    def status(self) -> dict[str, Any]:
        active = self.active
        return {
            "ok": True,
            "state": "recording" if active else "idle",
            "mode": active.mode if active else None,
            "started_at": active.started_at.isoformat() if active else None,
            "model": self.config.transcription.model,
            "model_loaded": self.transcriber.loaded,
            "backend": self.transcriber.backend if self.transcriber.loaded else None,
        }

    def start(self, mode: str) -> dict[str, Any]:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown mode: {mode}")
        if self.active:
            raise RuntimeError(f"Already recording a {self.active.mode}")
        started_at = datetime.now().astimezone()
        stamp = started_at.strftime("%Y-%m-%d_%H-%M-%S-%f")
        destination = data_dir() / "recordings" / f"{stamp}-{mode}.wav"
        self.recorder.start(destination, include_system_audio=mode == "meeting")
        self.active = ActiveRecording(mode=mode, started_at=started_at, path=destination)
        labels = {
            "dictation": ("Listening…", "Use the hotkey again to paste"),
            "note": ("Recording voice note…", "Run “talkies note” again to save"),
            "meeting": ("Recording meeting…", "Microphone and speaker audio are being captured"),
        }
        notify(*labels[mode], urgency="low")
        return self.status()

    def stop(self) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("Nothing is being recorded")
        active = self.active
        target_window_id = (
            (self.injector.capture_target() or "")
            if active.mode == "dictation" and self.config.behavior.paste
            else None
        )
        path = self.recorder.stop()
        self.active = None
        notify("Transcribing locally…", self.config.transcription.model, urgency="low")
        try:
            raw_text, metadata = self.transcriber.transcribe(path)
        except Exception as exc:
            message = f"{exc}. Raw audio was kept at {path}"
            notify("Transcription failed; audio kept", str(path), urgency="critical")
            raise RuntimeError(message) from exc
        text = normalize_text(
            raw_text,
            replacements=self.config.replacements,
            spoken_commands=self.config.behavior.spoken_commands,
        )
        keep_audio = {
            "dictation": self.config.behavior.keep_dictation_audio,
            "note": self.config.behavior.keep_note_audio,
            "meeting": self.config.behavior.keep_meeting_audio,
        }[active.mode]
        saved_audio = path if keep_audio else None
        note_path = None
        if active.mode in {"note", "meeting"} and text:
            note_path = write_note(active.mode, text, saved_audio, active.started_at)
        transcript_id = self.store.save(
            mode=active.mode,
            text=text,
            audio_path=saved_audio,
            note_path=note_path,
            metadata=metadata,
        )
        injection_error = None
        if active.mode == "dictation" and text:
            try:
                self.injector.inject(
                    text,
                    paste=self.config.behavior.paste,
                    target_window_id=target_window_id,
                )
            except InjectionError as exc:
                injection_error = str(exc)
                notify("Dictation not pasted", injection_error, urgency="critical")
        if not keep_audio:
            path.unlink(missing_ok=True)
        if not text:
            notify("No speech detected", "Try again and speak closer to the microphone")
        elif active.mode == "dictation" and not injection_error:
            notify("Dictation pasted", f"{len(text.split())} words", urgency="low")
        else:
            notify(
                "Transcript saved",
                str(note_path) if note_path else f"History item {transcript_id}",
                urgency="low",
            )
        return {
            "ok": not injection_error,
            "state": "idle",
            "mode": active.mode,
            "text": text,
            "transcript_id": transcript_id,
            "audio_path": str(saved_audio) if saved_audio else None,
            "note_path": str(note_path) if note_path else None,
            "metadata": metadata,
            "error": injection_error,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "status":
            return self.status()
        if not self._operation_lock.acquire(blocking=False):
            return {"ok": False, "error": "Talkies is busy"}
        try:
            if command == "start":
                return self.start(str(request.get("mode", "dictation")))
            if command == "stop":
                return self.stop()
            if command == "toggle":
                mode = str(request.get("mode", "dictation"))
                return self.stop() if self.active else self.start(mode)
            if command == "shutdown":
                return {"ok": True, "shutdown": True}
            return {"ok": False, "error": f"Unknown command: {command}"}
        except Exception as exc:
            LOG.exception("Request failed: %s", command)
            notify("Talkies error", str(exc), urgency="critical")
            return {"ok": False, "error": str(exc)}
        finally:
            self._operation_lock.release()


class Daemon:
    def __init__(self, service: TalkiesService | None = None) -> None:
        self.service = service or TalkiesService()
        self.running = True
        self.server: socket.socket | None = None

    def _signal(self, _signum: int, _frame: FrameType | None) -> None:
        self.running = False
        if self.server:
            self.server.close()

    def serve(self) -> None:
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        os.chmod(path, 0o600)
        self.server.listen(8)
        self.server.settimeout(1)
        threading.Thread(target=self.service.warm_model, daemon=True).start()
        LOG.info("Talkies listening at %s", path)
        try:
            while self.running:
                try:
                    connection, _ = self.server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if not self.running:
                        break
                    raise
                with connection:
                    payload = b""
                    while not payload.endswith(b"\n") and len(payload) < 1_048_576:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        payload += chunk
                    try:
                        request = json.loads(payload.decode("utf-8"))
                        response = self.service.handle(request)
                    except Exception as exc:
                        response = {"ok": False, "error": f"Invalid request: {exc}"}
                    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
                    if response.get("shutdown"):
                        self.running = False
        finally:
            path.unlink(missing_ok=True)
