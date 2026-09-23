from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from talkies.dashboard import start_dashboard
from talkies.store import TranscriptStore


class FakeTranscriptionConfig:
    model = "fake-model"


class FakeBehaviorConfig:
    paste = True
    keep_dictation_audio = True


class FakeConfig:
    transcription = FakeTranscriptionConfig()
    behavior = FakeBehaviorConfig()
    replacements = {"wispr": "Wispr", "talkies": "Talkies"}


class FakeService:
    def __init__(self, store: TranscriptStore) -> None:
        self.store = store
        self.config = FakeConfig()
        self.config.replacements = dict(self.config.replacements)
        self.requests: list[dict[str, object]] = []

    def status(self) -> dict[str, object]:
        return {"ok": True, "state": "idle", "model": "fake-model"}

    def handle(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        if request["command"] == "set_replacement":
            self.config.replacements[str(request["phrase"])] = str(request["replacement"])
        if request["command"] == "remove_replacement":
            self.config.replacements.pop(str(request["phrase"]), None)
        return {"ok": True, "command": request["command"]}


def test_dashboard_serves_static_app_and_history_api(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    transcript_id = store.save(
        mode="dictation",
        text="hello dashboard",
        audio_path=None,
        note_path=None,
        metadata={"model": "fake"},
        status="pasted",
        duration_seconds=6.0,
    )
    handle = start_dashboard(store, port=0)
    try:
        html = _read_text(handle.url)
        assert "Talkies" in html

        history = _read_json(f"{handle.url}api/history?query=dashboard")
        assert history["ok"] is True
        assert history["items"][0]["id"] == transcript_id
        assert history["items"][0]["status"] == "pasted"

        stats = _read_json(f"{handle.url}api/stats")
        assert stats["stats"]["words_today"] == 2
        assert stats["stats"]["words_per_minute"] == 20
    finally:
        handle.shutdown()


def test_dashboard_serves_retained_audio_by_transcript_id(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFFfake-audio")
    transcript_id = store.save(
        mode="note",
        text="with retained audio",
        audio_path=audio_path,
        note_path=None,
        metadata={},
        status="saved",
    )
    handle = start_dashboard(store, port=0)
    try:
        audio = _read_bytes(f"{handle.url}api/transcripts/{transcript_id}/audio")
        assert audio == b"RIFFfake-audio"
    finally:
        handle.shutdown()


def test_dashboard_updates_text_and_status_through_store(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    transcript_id = store.save(
        mode="note",
        text="old words",
        audio_path=None,
        note_path=None,
        metadata={},
        status="saved",
    )
    handle = start_dashboard(store, port=0)
    try:
        updated = _send_json(
            f"{handle.url}api/transcripts/{transcript_id}",
            "PATCH",
            {"text": "corrected words"},
        )
        assert updated["item"]["text"] == "corrected words"
        assert store.get(transcript_id)["text"] == "corrected words"

        cancelled = _send_json(
            f"{handle.url}api/transcripts/{transcript_id}/status",
            "POST",
            {"status": "cancelled"},
        )
        assert cancelled["item"]["status"] == "cancelled"
        assert store.get(transcript_id)["status"] == "cancelled"

        origin = handle.url.rstrip("/")
        same_origin = _send_json(
            f"{handle.url}api/transcripts/{transcript_id}",
            "PATCH",
            {"text": "same origin correction"},
            headers={"Origin": origin},
        )
        assert same_origin["item"]["text"] == "same origin correction"
    finally:
        handle.shutdown()


def test_dashboard_settings_include_dictionary_and_repaste_uses_service(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    transcript_id = store.save(
        mode="dictation",
        text="paste this again",
        audio_path=None,
        note_path=None,
        metadata={},
        status="cancelled",
    )
    service = FakeService(store)
    handle = start_dashboard(service, port=0)
    try:
        settings = _read_json(f"{handle.url}api/settings")["settings"]
        assert settings["model"] == "fake-model"
        assert settings["replacements"] == [
            {"phrase": "talkies", "replacement": "Talkies"},
            {"phrase": "wispr", "replacement": "Wispr"},
        ]

        response = _send_json(f"{handle.url}api/transcripts/{transcript_id}/paste", "POST", {})
        assert response["ok"] is True
        assert service.requests == [
            {"command": "paste_transcript", "transcript_id": transcript_id}
        ]
        added = _send_json(
            f"{handle.url}api/replacements", "POST",
            {"phrase": "linux", "replacement": "Linux"},
        )
        assert {"phrase": "linux", "replacement": "Linux"} in added["settings"]["replacements"]
        removed = _send_json(
            f"{handle.url}api/replacements", "POST",
            {"phrase": "linux", "replacement": None},
        )
        assert {"phrase": "linux", "replacement": "Linux"} not in (
            removed["settings"]["replacements"]
        )
    finally:
        handle.shutdown()


def test_dashboard_rejects_cross_origin_write(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    transcript_id = store.save(
        mode="note",
        text="safe words",
        audio_path=None,
        note_path=None,
        metadata={},
    )
    handle = start_dashboard(store, port=0)
    try:
        request = Request(
            f"{handle.url}api/transcripts/{transcript_id}",
            data=json.dumps({"text": "blocked"}).encode("utf-8"),
            method="PATCH",
            headers={"Content-Type": "application/json", "Origin": "https://example.com"},
        )
        try:
            urlopen(request, timeout=2)
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("cross-origin write should fail")
        assert store.get(transcript_id)["text"] == "safe words"
    finally:
        handle.shutdown()


def test_dashboard_rejects_non_localhost_host_reads(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    handle = start_dashboard(store, port=0)
    try:
        request = Request(f"{handle.url}api/history", headers={"Host": "attacker.example"})
        try:
            urlopen(request, timeout=2)
        except HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError("non-localhost Host should fail")
    finally:
        handle.shutdown()


def _read_text(url: str) -> str:
    with urlopen(url, timeout=2) as response:
        return response.read().decode("utf-8")


def _read_json(url: str) -> dict[str, object]:
    return json.loads(_read_text(url))


def _read_bytes(url: str) -> bytes:
    with urlopen(url, timeout=2) as response:
        return response.read()


def _send_json(
    url: str,
    method: str,
    payload: dict[str, object],
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    parsed = urlparse(url)
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method=method,
        headers={
            "Content-Type": "application/json",
            "Host": parsed.netloc,
            **(headers or {}),
        },
    )
    with urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))
