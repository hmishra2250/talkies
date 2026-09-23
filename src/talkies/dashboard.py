from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

WEB_ROOT = resources.files("talkies") / "web"
MAX_BODY_BYTES = 64_000
VALID_STATUSES = {"pasted", "saved", "cancelled", "failed"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


@dataclass(frozen=True, slots=True)
class DashboardHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)


class DashboardError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def start_dashboard(
    target: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    background: bool = True,
) -> DashboardHandle:
    """Start the local dashboard for a service or TranscriptStore.

    The integration contract is intentionally small: pass either a TalkiesService-like
    object with ``.store`` and optional ``.status()``/``.config``, or pass a
    TranscriptStore-like object directly. The server binds to loopback by default.
    """

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Talkies dashboard only binds to localhost")
    context = _DashboardContext.from_target(target)
    server = _DashboardServer((host, port), _DashboardRequestHandler, context)
    thread = None
    if background:
        thread = threading.Thread(
            target=server.serve_forever,
            name="talkies-dashboard",
            daemon=True,
        )
        thread.start()
    if open_browser:
        webbrowser.open(f"http://{server.server_address[0]}:{server.server_address[1]}/")
    return DashboardHandle(server=server, thread=thread)


class _DashboardContext:
    def __init__(self, target: Any, store: Any) -> None:
        self.target = target
        self.store = store

    @classmethod
    def from_target(cls, target: Any) -> _DashboardContext:
        store = getattr(target, "store", target)
        return cls(target=target, store=store)

    def history(self, *, limit: int, query: str = "", status: str = "") -> dict[str, object]:
        if not hasattr(self.store, "search"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Store search is unavailable")
        rows = self.store.search(limit=limit, query=query, status=status)
        items = [_normalise_transcript(row) for row in rows]
        return {"items": items, "count": len(items)}

    def get(self, transcript_id: int) -> dict[str, object]:
        if not hasattr(self.store, "get"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Store get is unavailable")
        row = self.store.get(transcript_id)
        if row is None:
            raise DashboardError(HTTPStatus.NOT_FOUND, "Transcript not found")
        return _normalise_transcript(row)

    def update_text(self, transcript_id: int, text: str) -> dict[str, object]:
        if not text.strip():
            raise DashboardError(HTTPStatus.BAD_REQUEST, "Transcript text cannot be empty")
        if not hasattr(self.store, "update_text"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Store text updates are unavailable")
        updated = self.store.update_text(transcript_id, text)
        if not updated:
            raise DashboardError(HTTPStatus.NOT_FOUND, "Transcript not found")
        return self.get(transcript_id)

    def set_status(self, transcript_id: int, status: str) -> dict[str, object]:
        if status not in VALID_STATUSES:
            raise DashboardError(HTTPStatus.BAD_REQUEST, f"Unsupported status: {status}")
        if not hasattr(self.store, "set_status"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Store status updates are unavailable")
        try:
            updated = self.store.set_status(transcript_id, status)
        except ValueError as exc:
            raise DashboardError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if not updated:
            raise DashboardError(HTTPStatus.NOT_FOUND, "Transcript not found")
        return self.get(transcript_id)

    def stats(self) -> dict[str, object]:
        if not hasattr(self.store, "stats"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Store stats are unavailable")
        stats = self.store.stats()
        if not isinstance(stats, dict):
            raise DashboardError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Store stats returned invalid data",
            )
        return _normalise_stats(stats)

    def service_status(self) -> dict[str, object]:
        if hasattr(self.target, "status"):
            try:
                return dict(self.target.status())
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": True, "state": "dashboard"}

    def settings(self) -> dict[str, object]:
        config = getattr(self.target, "config", None)
        if config is None:
            return {
                "model": None,
                "paste": None,
                "keep_dictation_audio": None,
                "replacements": [],
            }
        transcription = getattr(config, "transcription", None)
        behavior = getattr(config, "behavior", None)
        replacements = getattr(config, "replacements", {}) or {}
        return {
            "model": getattr(transcription, "model", None),
            "paste": getattr(behavior, "paste", None),
            "keep_dictation_audio": getattr(behavior, "keep_dictation_audio", None),
            "replacements": [
                {"phrase": phrase, "replacement": replacement}
                for phrase, replacement in sorted(replacements.items())
            ],
        }

    def repaste(self, transcript_id: int) -> dict[str, object]:
        if not hasattr(self.target, "handle"):
            raise DashboardError(HTTPStatus.NOT_IMPLEMENTED, "Paste requires the Talkies service")
        response = self.target.handle(
            {"command": "paste_transcript", "transcript_id": transcript_id}
        )
        if not isinstance(response, dict):
            raise DashboardError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Paste command returned invalid data",
            )
        if not response.get("ok"):
            raise DashboardError(
                HTTPStatus.BAD_REQUEST,
                str(response.get("error", "Paste command failed")),
            )
        return response

    def update_replacement(self, phrase: str, replacement: str | None) -> dict[str, object]:
        if not hasattr(self.target, "handle"):
            raise DashboardError(
                HTTPStatus.NOT_IMPLEMENTED, "Dictionary editing requires the service"
            )
        response = self.target.handle(
            {
                "command": "remove_replacement" if replacement is None else "set_replacement",
                "phrase": phrase,
                "replacement": replacement,
            }
        )
        if not response.get("ok"):
            raise DashboardError(HTTPStatus.BAD_REQUEST, str(response.get("error")))
        return self.settings()

    def audio_path(self, transcript_id: int) -> Path:
        item = self.get(transcript_id)
        value = item.get("audio_path")
        if not value:
            raise DashboardError(HTTPStatus.NOT_FOUND, "Transcript has no retained audio")
        path = Path(str(value)).expanduser()
        if not path.is_file():
            raise DashboardError(HTTPStatus.NOT_FOUND, "Audio file is unavailable")
        return path


class _DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        context: _DashboardContext,
    ):
        super().__init__(server_address, handler)
        self.context = context


class _DashboardRequestHandler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def do_GET(self) -> None:
        try:
            self._require_local_host()
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api_get(parsed.path, parse_qs(parsed.query))
                return
            self._serve_static(parsed.path)
        except DashboardError as exc:
            self._send_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )

    def do_PATCH(self) -> None:
        try:
            self._require_local_write()
            transcript_id = self._transcript_id(self.path)
            payload = self._read_json()
            text = str(payload.get("text", ""))
            item = self.server.context.update_text(transcript_id, text)
            self._send_json({"ok": True, "item": item})
        except DashboardError as exc:
            self._send_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )

    def do_POST(self) -> None:
        try:
            self._require_local_write()
            parsed = urlparse(self.path)
            if parsed.path == "/api/shutdown":
                self._send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if parsed.path == "/api/replacements":
                payload = self._read_json()
                phrase = str(payload.get("phrase", ""))
                replacement = payload.get("replacement")
                if replacement is not None:
                    replacement = str(replacement)
                settings = self.server.context.update_replacement(phrase, replacement)
                self._send_json({"ok": True, "settings": settings})
                return
            if parsed.path.endswith("/paste"):
                transcript_id = self._transcript_id(parsed.path.removesuffix("/paste"))
                self._send_json({"ok": True, "result": self.server.context.repaste(transcript_id)})
                return
            if parsed.path.endswith("/status"):
                transcript_id = self._transcript_id(parsed.path.removesuffix("/status"))
                payload = self._read_json()
                item = self.server.context.set_status(transcript_id, str(payload.get("status", "")))
                self._send_json({"ok": True, "item": item})
                return
            raise DashboardError(HTTPStatus.NOT_FOUND, "Not found")
        except DashboardError as exc:
            self._send_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _require_local_write(self) -> None:
        host = self._require_local_host()
        origin = self.headers.get("Origin")
        if origin and not _origin_matches_host(origin, host):
            raise DashboardError(HTTPStatus.FORBIDDEN, "Writes require a same-origin request")

    def _require_local_host(self) -> str:
        host = self.headers.get("Host", "")
        if not _is_local_host(host):
            raise DashboardError(HTTPStatus.FORBIDDEN, "Requests require a localhost host")
        return host

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        context = self.server.context
        if path == "/api/health":
            self._send_json({"ok": True, "service": context.service_status()})
            return
        if path == "/api/history":
            limit = _bounded_int(_first(query, "limit") or "50", default=50, lower=1, upper=200)
            body = context.history(
                limit=limit,
                query=_first(query, "query") or "",
                status=_first(query, "status") or "",
            )
            self._send_json({"ok": True, **body})
            return
        if path == "/api/stats":
            self._send_json({"ok": True, "stats": context.stats()})
            return
        if path == "/api/settings":
            self._send_json({"ok": True, "settings": context.settings()})
            return
        if path.startswith("/api/transcripts/") and path.endswith("/audio"):
            self._serve_audio(context.audio_path(self._transcript_id(path.removesuffix("/audio"))))
            return
        if path.startswith("/api/transcripts/"):
            self._send_json({"ok": True, "item": context.get(self._transcript_id(path))})
            return
        raise DashboardError(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        clean_path = PurePosixPath(path.lstrip("/"))
        if ".." in clean_path.parts:
            raise DashboardError(HTTPStatus.NOT_FOUND, "Not found")
        resource = WEB_ROOT.joinpath(*clean_path.parts)
        if not resource.is_file():
            raise DashboardError(HTTPStatus.NOT_FOUND, "Not found")
        data = resource.read_bytes()
        mime_type = mimetypes.guess_type(clean_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_audio(self, path: Path) -> None:
        file_size = path.stat().st_size
        mime_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as audio:
            while chunk := audio.read(64 * 1024):
                self.wfile.write(chunk)

    def _send_json(self, payload: dict[str, object], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DashboardError(HTTPStatus.BAD_REQUEST, "Invalid content length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise DashboardError(HTTPStatus.BAD_REQUEST, "Invalid request body")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DashboardError(HTTPStatus.BAD_REQUEST, "Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DashboardError(HTTPStatus.BAD_REQUEST, "JSON body must be an object")
        return payload

    @staticmethod
    def _transcript_id(path: str) -> int:
        parts = [part for part in PurePosixPath(urlparse(path).path).parts if part != "/"]
        if len(parts) < 3 or parts[0] != "api" or parts[1] != "transcripts":
            raise DashboardError(HTTPStatus.NOT_FOUND, "Not found")
        try:
            return int(parts[2])
        except ValueError as exc:
            raise DashboardError(HTTPStatus.BAD_REQUEST, "Invalid transcript id") from exc


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _bounded_int(value: str, *, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except ValueError:
        return default
    return max(lower, min(number, upper))


def _normalise_transcript(row: dict[str, object]) -> dict[str, object]:
    metadata = _parse_metadata(row)
    text = str(row.get("text") or "")
    status = str(row.get("status") or metadata.get("status") or _default_status(row))
    audio_path = row.get("audio_path")
    note_path = row.get("note_path")
    return {
        "id": int(row.get("id") or 0),
        "created_at": str(row.get("created_at") or ""),
        "mode": str(row.get("mode") or "dictation"),
        "status": status,
        "text": text,
        "audio_path": str(audio_path) if audio_path else None,
        "note_path": str(note_path) if note_path else None,
        "metadata": metadata,
        "words": _word_count(text),
        "duration_seconds": _duration_seconds(row, metadata),
    }


def _parse_metadata(row: dict[str, object]) -> dict[str, object]:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    raw = row.get("metadata_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _default_status(row: dict[str, object]) -> str:
    mode = str(row.get("mode") or "")
    if mode == "dictation":
        return "pasted"
    return "saved"


def _duration_seconds(row: dict[str, object], metadata: dict[str, object]) -> float | None:
    value = row.get("duration_seconds", metadata.get("duration_seconds", metadata.get("duration")))
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip()])


def _normalise_stats(stats: dict[str, object]) -> dict[str, object]:
    normalised = dict(stats)
    if "words_per_minute" not in normalised and "average_wpm" in normalised:
        normalised["words_per_minute"] = normalised["average_wpm"]
    if "estimated_minutes_saved" not in normalised:
        words_week = _number(normalised.get("words_week"))
        normalised["estimated_minutes_saved"] = round(words_week / 40, 1)
    if "by_status" not in normalised:
        normalised["by_status"] = {}
    if "by_mode" not in normalised:
        normalised["by_mode"] = {}
    return normalised


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_local_host(value: str) -> bool:
    host = value.split("]", 1)[0] + "]" if value.startswith("[") else value.split(":", 1)[0]
    return host in LOCAL_HOSTS


def _origin_matches_host(value: str, host: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "http" and parsed.netloc == host and _is_local_host(parsed.netloc)
