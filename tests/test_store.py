from datetime import datetime
from pathlib import Path

from talkies.store import TranscriptStore, write_note


def test_store_round_trip(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    transcript_id = store.save(
        mode="dictation",
        text="local speech",
        audio_path=None,
        note_path=None,
        metadata={"model": "small.en"},
    )
    row = store.recent(1)[0]
    assert row["id"] == transcript_id
    assert row["text"] == "local speech"


def test_write_note_creates_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = write_note("note", "remember this", None, datetime.now().astimezone())
    assert path.parent == tmp_path / "talkies" / "notes"
    assert "remember this" in path.read_text(encoding="utf-8")
