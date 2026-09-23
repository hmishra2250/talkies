from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from .audio import AudioRecorder
from .config import (
    Config,
    data_dir,
    ensure_user_dirs,
    load_config,
    runtime_dir,
    save_hold_key,
    save_replacements,
)
from .dashboard import DashboardHandle, start_dashboard
from .hotkeys import XInputHotkeyListener
from .injector import InjectionError, TextInjector, window_app_name
from .overlay import publish, start_overlay, stop_overlay
from .store import TranscriptStore, write_note
from .text import normalize_text
from .transcriber import Transcriber

LOG = logging.getLogger(__name__)
VALID_MODES = {"dictation", "note", "meeting"}
TOGGLE_DEBOUNCE_SECONDS = 0.75
_NOTIFICATION_ID: str | None = None
_NOTIFICATION_LOCK = threading.Lock()


def socket_path() -> Path:
    return runtime_dir() / "talkies.sock"


def notify(summary: str, body: str = "", *, urgency: str = "normal") -> None:
    global _NOTIFICATION_ID
    with _NOTIFICATION_LOCK:
        command = [
            "notify-send",
            "--app-name=Talkies",
            f"--urgency={urgency}",
            "--transient",
            f"--expire-time={5000 if urgency == 'critical' else 2200}",
            "--print-id",
        ]
        if _NOTIFICATION_ID:
            command.append(f"--replace-id={_NOTIFICATION_ID}")
        command.append(summary)
        if body:
            command.append(body)
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            notification_id = result.stdout.strip()
            if result.returncode == 0 and notification_id.isdigit():
                _NOTIFICATION_ID = notification_id
        except FileNotFoundError:
            LOG.info("%s: %s", summary, body)


@dataclass(slots=True)
class ActiveRecording:
    mode: str
    started_at: datetime
    path: Path
    target_window_id: str | None = None
    app_name: str | None = None


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
        self.store.prune_audio(self.config.behavior.dictation_audio_retention_days)
        self._last_audio_prune = time.monotonic()
        self.active: ActiveRecording | None = None
        self._operation_lock = threading.Lock()
        self._last_toggle_request = float("-inf")
        self._last_model_activity = time.monotonic()
        self._warming_thread: threading.Thread | None = None
        self.dashboard_url: str | None = None
        self.hotkeys: XInputHotkeyListener | None = None

    def warm_model(self) -> None:
        try:
            self.transcriber.load()
            self._last_model_activity = time.monotonic()
            LOG.info("Loaded %s on %s", self.config.transcription.model, self.transcriber.backend)
        except Exception as exc:
            LOG.exception("Model warm-up failed")
            notify("Talkies model error", str(exc), urgency="critical")

    def warm_model_in_background(self) -> None:
        if self.transcriber.loaded:
            self._last_model_activity = time.monotonic()
            return
        if self._warming_thread and self._warming_thread.is_alive():
            return
        self._warming_thread = threading.Thread(target=self.warm_model, daemon=True)
        self._warming_thread.start()

    def should_recycle_model(self, now: float | None = None) -> bool:
        timeout = self.config.transcription.model_idle_timeout_seconds
        if timeout <= 0 or self.active or not self.transcriber.loaded:
            return False
        current_time = now if now is not None else time.monotonic()
        return current_time - self._last_model_activity >= timeout

    def prune_audio_if_due(self, now: float | None = None) -> None:
        current_time = now if now is not None else time.monotonic()
        if current_time - self._last_audio_prune >= 3600:
            self.store.prune_audio(self.config.behavior.dictation_audio_retention_days)
            self._last_audio_prune = current_time

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
            "dashboard_url": self.dashboard_url,
            "hold_key": self.config.behavior.hold_key,
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
        target_window_id = self.injector.capture_target() if mode == "dictation" else None
        self.active = ActiveRecording(
            mode=mode,
            started_at=started_at,
            path=destination,
            target_window_id=target_window_id,
            app_name=window_app_name(target_window_id),
        )
        self._last_model_activity = time.monotonic()
        self.warm_model_in_background()
        labels = {
            "dictation": ("Listening…", "Use the hotkey again to paste"),
            "note": ("Recording voice note…", "Run “talkies note” again to save"),
            "meeting": ("Recording meeting…", "Microphone and speaker audio are being captured"),
        }
        notify(*labels[mode], urgency="low")
        publish({"status": "recording", "detail": mode.capitalize()})
        return self.status()

    def stop(self, *, cancelled: bool = False) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("Nothing is being recorded")
        active = self.active
        target_window_id = active.target_window_id
        try:
            path = self.recorder.stop()
        except Exception:
            if getattr(self.recorder, "process", None) is None:
                self.active = None
                if active.path.exists() and active.path.stat().st_size <= 44:
                    active.path.unlink()
            raise
        self.active = None
        notify("Transcribing locally…", self.config.transcription.model, urgency="low")
        publish({"status": "transcribing", "detail": self.config.transcription.model})
        try:
            raw_text, metadata = self.transcriber.transcribe(path)
        except Exception as exc:
            self.store.save(
                mode=active.mode,
                text="",
                audio_path=path,
                note_path=None,
                metadata={"error": str(exc)},
                status="failed",
                duration_seconds=(datetime.now().astimezone() - active.started_at).total_seconds(),
                app_name=active.app_name,
            )
            message = f"{exc}. Raw audio was kept at {path}"
            notify("Transcription failed; audio kept", str(path), urgency="critical")
            publish({"status": "ready", "message": "Transcription failed", "detail": "Audio saved"})
            raise RuntimeError(message) from exc
        finally:
            self._last_model_activity = time.monotonic()
        text = normalize_text(
            raw_text,
            replacements=self.config.replacements,
            spoken_commands=self.config.behavior.spoken_commands,
        )
        keep_audio = cancelled or {
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
            status="cancelled" if cancelled else "saved",
            raw_text=raw_text,
            duration_seconds=(datetime.now().astimezone() - active.started_at).total_seconds(),
            app_name=active.app_name,
        )
        injection_error = None
        if active.mode == "dictation" and text and not cancelled:
            try:
                self.injector.inject(
                    text,
                    paste=self.config.behavior.paste,
                    target_window_id=target_window_id,
                )
                if self.config.behavior.paste:
                    self.store.set_status(transcript_id, "pasted")
            except InjectionError as exc:
                injection_error = str(exc)
                self.store.set_status(transcript_id, "failed")
                LOG.warning("Injection failed: %s", injection_error)
                notify("Dictation not pasted", injection_error, urgency="critical")
        if not keep_audio:
            path.unlink(missing_ok=True)
        if not text:
            notify("No speech detected", "Try again and speak closer to the microphone")
        elif cancelled:
            notify("Recording cancelled", "Saved in history", urgency="low")
            publish({"status": "cancelled", "detail": "Saved in history"})
        elif active.mode == "dictation" and not injection_error:
            notify("Dictation pasted", f"{len(text.split())} words", urgency="low")
            publish(
                {"status": "ready", "message": "Pasted", "detail": f"{len(text.split())} words"}
            )
        else:
            notify(
                "Transcript saved",
                str(note_path) if note_path else f"History item {transcript_id}",
                urgency="low",
            )
            publish({"status": "ready", "message": "Saved", "detail": active.mode.capitalize()})
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
            "status": (
                "cancelled" if cancelled else "failed" if injection_error else
                "pasted" if active.mode == "dictation" and text and self.config.behavior.paste
                else "saved"
            ),
        }

    def undo_cancel(self, transcript_id: int | None = None) -> dict[str, Any]:
        if transcript_id is None:
            cancelled = self.store.search(limit=100, status="cancelled")
            item = next(
                (row for row in cancelled if row["mode"] == "dictation" and row["text"]),
                None,
            )
        else:
            item = self.store.get(transcript_id)
        if not item or item["status"] != "cancelled" or item["mode"] != "dictation":
            raise ValueError("No cancelled dictation to restore")
        text = str(item["text"])
        if not text:
            raise ValueError("Cancelled dictation has no transcript")
        self.injector.inject(text, paste=self.config.behavior.paste)
        self.store.set_status(int(item["id"]), "pasted" if self.config.behavior.paste else "saved")
        return {"ok": True, "transcript_id": item["id"], "text": text}

    def copy_transcript(self, transcript_id: int) -> dict[str, Any]:
        item = self.store.get(transcript_id)
        if not item or not str(item["text"]):
            raise ValueError("Transcript is unavailable or empty")
        self.injector.inject(str(item["text"]), paste=False)
        return {"ok": True, "transcript_id": transcript_id, "delivery": "clipboard"}

    def update_replacement(self, phrase: str, replacement: str | None) -> dict[str, Any]:
        phrase = phrase.strip()
        if not phrase or len(phrase) > 100 or "\n" in phrase:
            raise ValueError("Phrase must be 1–100 characters on one line")
        if replacement is not None and (not replacement.strip() or len(replacement) > 200):
            raise ValueError("Replacement must be 1–200 characters")
        values = dict(self.config.replacements)
        for existing in list(values):
            if existing.casefold() == phrase.casefold():
                values.pop(existing)
        if replacement is not None:
            values[phrase] = replacement.strip()
        save_replacements(values)
        self.config.replacements = values
        return {"ok": True, "replacements": values}

    def hotkey_info(self) -> dict[str, Any]:
        listener = self.hotkeys
        recommendation = listener.recommendation() if listener else None
        return {
            "binding": self.config.behavior.hold_key,
            "available": bool(listener and listener.running),
            "recommendation": recommendation or "Capture your Copilot key",
            "recommendation_reason": (
                "The X11 key map cannot confirm a physical key. Select Capture key press, then "
                "press your Copilot key once to identify its actual signal."
            ),
        }

    def start_hotkey_capture(self) -> dict[str, Any]:
        with self._operation_lock:
            if self.active:
                raise RuntimeError("Finish the current recording before changing the hotkey")
            if not self.hotkeys or not self.hotkeys.running:
                raise RuntimeError("Key detection requires a running X11 hotkey listener")
            return self.hotkeys.begin_capture()

    def hotkey_capture_status(self) -> dict[str, Any]:
        if not self.hotkeys:
            return {"state": "idle"}
        return self.hotkeys.capture_status()

    def set_hotkey(self, binding: str) -> dict[str, Any]:
        from .hotkeys import normalize_binding

        binding = normalize_binding(binding)
        with self._operation_lock:
            if self.active:
                raise RuntimeError("Finish the current recording before changing the hotkey")
            previous = self.config.behavior.hold_key
            if self.hotkeys:
                self.hotkeys.set_hold_key(binding)
            try:
                save_hold_key(binding)
            except Exception:
                if self.hotkeys:
                    self.hotkeys.set_hold_key(previous)
                raise
            self.config.behavior.hold_key = binding
        return {"ok": True, "binding": binding}

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "status":
            return self.status()
        if command == "toggle":
            try:
                request_time = float(request.get("request_time", time.monotonic()))
            except (TypeError, ValueError):
                request_time = time.monotonic()
            if request_time <= self._last_toggle_request + TOGGLE_DEBOUNCE_SECONDS:
                self._last_toggle_request = max(self._last_toggle_request, request_time)
                LOG.info("Ignored repeated toggle request")
                return {**self.status(), "ignored": True}
            self._last_toggle_request = request_time
        if not self._operation_lock.acquire(blocking=False):
            return {"ok": False, "error": "Talkies is busy"}
        try:
            if command == "start":
                return self.start(str(request.get("mode", "dictation")))
            if command == "stop":
                return self.stop()
            if command == "cancel":
                return self.stop(cancelled=True)
            if command == "undo":
                return self.undo_cancel(
                    int(request["transcript_id"]) if request.get("transcript_id") else None
                )
            if command == "paste_transcript":
                return self.copy_transcript(int(request["transcript_id"]))
            if command == "set_replacement":
                return self.update_replacement(
                    str(request.get("phrase", "")), str(request.get("replacement", ""))
                )
            if command == "remove_replacement":
                return self.update_replacement(str(request.get("phrase", "")), None)
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
        self.dashboard: DashboardHandle | None = None
        self.hotkeys: XInputHotkeyListener | None = None

    def _signal(self, _signum: int, _frame: FrameType | None) -> None:
        self.running = False
        if self.server:
            self.server.close()

    def _stop_companions(self) -> None:
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys = None
            self.service.hotkeys = None
        if self.dashboard:
            self.dashboard.shutdown()
            self.dashboard = None
        stop_overlay()

    def serve(self) -> None:
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, self._signal)
        signal.signal(signal.SIGINT, self._signal)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        os.chmod(path, 0o600)
        # Key-repeat can launch several shortcut clients before debounce handles them.
        self.server.listen(64)
        self.server.settimeout(1)
        start_overlay(lambda action: self.service.handle({"command": action}))
        x11_session = os.environ.get("XDG_SESSION_TYPE") in {None, "x11"}
        if self.service.config.behavior.hold_hotkey and x11_session and os.environ.get("DISPLAY"):
            self.hotkeys = XInputHotkeyListener(
                lambda action: self.service.handle({"command": action, "mode": "dictation"}),
                hold_key=self.service.config.behavior.hold_key,
            )
            self.service.hotkeys = self.hotkeys
            self.hotkeys.start()
        try:
            self.dashboard = start_dashboard(self.service, background=True)
            self.service.dashboard_url = self.dashboard.url
        except OSError:
            LOG.exception("Could not start the local dashboard")
        LOG.info("Talkies listening at %s", path)
        if self.dashboard:
            LOG.info("Talkies dashboard listening at %s", self.dashboard.url)
        try:
            while self.running:
                self.service.prune_audio_if_due()
                if self.service.should_recycle_model():
                    LOG.info("Recycling idle daemon to release speech model memory")
                    self._stop_companions()
                    self.server.close()
                    path.unlink(missing_ok=True)
                    os.execv(sys.executable, [sys.executable, "-m", "talkies", "daemon"])
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
            self._stop_companions()
            path.unlink(missing_ok=True)
