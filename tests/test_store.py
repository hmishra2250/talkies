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


def test_store_migrates_and_keeps_searchable_corrections(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "history.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE transcripts (
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL,
                text TEXT NOT NULL, audio_path TEXT, note_path TEXT, metadata_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO transcripts(created_at, mode, text, metadata_json)
               VALUES('2026-01-01T00:00:00+00:00', 'dictation', 'old text', '{}')"""
        )
    store = TranscriptStore(database)
    assert store.search(query="old")[0]["status"] == "saved"
    assert store.update_text(1, "corrected text")
    assert store.search(query="corrected")[0]["raw_text"] is None
    assert store.get(1)["edited_at"] is not None


def test_expired_dictation_audio_is_pruned_but_note_audio_remains(tmp_path: Path) -> None:
    import sqlite3

    store = TranscriptStore(tmp_path / "history.sqlite3")
    dictation_audio = tmp_path / "dictation.wav"
    note_audio = tmp_path / "note.wav"
    dictation_audio.write_bytes(b"audio")
    note_audio.write_bytes(b"audio")
    first = store.save(
        mode="dictation", text="old dictation", audio_path=dictation_audio,
        note_path=None, metadata={},
    )
    second = store.save(
        mode="note", text="old note", audio_path=note_audio,
        note_path=None, metadata={},
    )
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE transcripts SET created_at = '2020-01-01T00:00:00+00:00'")
    assert store.prune_audio(14) == 1
    assert store.get(first)["audio_path"] is None
    assert store.get(second)["audio_path"] == str(note_audio)
    assert not dictation_audio.exists()
    assert note_audio.exists()


def test_corrected_audio_can_be_exported_for_benchmark(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "history.sqlite3")
    audio = tmp_path / "dictation.wav"
    audio.write_bytes(b"audio")
    transcript_id = store.save(
        mode="dictation", text="heard words", audio_path=audio,
        note_path=None, metadata={}, raw_text="heard words",
    )
    assert store.corrected_pairs() == []
    assert store.update_text(transcript_id, "correct words")
    assert store.corrected_pairs() == [{"audio": str(audio), "reference": "correct words"}]


def test_write_note_creates_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = write_note("note", "remember this", None, datetime.now().astimezone())
    assert path.parent == tmp_path / "talkies" / "notes"
    assert "remember this" in path.read_text(encoding="utf-8")
