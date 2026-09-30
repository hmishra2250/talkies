from __future__ import annotations

import json
import math
import sqlite3
import wave
from datetime import datetime, timedelta
from pathlib import Path

from .config import data_dir, state_dir

MIN_WPM_SAMPLE_SECONDS = 1.0
MAX_PLAUSIBLE_WPM = 350
RECORDING_MODES = {"dictation", "note", "meeting"}
EMPTY_WAV_BYTES = 44


class TranscriptStore:
    def __init__(self, database: Path | None = None) -> None:
        self.database = database or state_dir() / "history.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    text TEXT NOT NULL,
                    audio_path TEXT,
                    note_path TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(transcripts)")}
            additions = {
                "status": "TEXT NOT NULL DEFAULT 'saved'",
                "raw_text": "TEXT",
                "duration_seconds": "REAL",
                "app_name": "TEXT",
                "edited_at": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE transcripts ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS transcripts_created_idx ON transcripts(created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database)

    def save(
        self,
        *,
        mode: str,
        text: str,
        audio_path: Path | None,
        note_path: Path | None,
        metadata: dict[str, object],
        status: str = "saved",
        raw_text: str | None = None,
        duration_seconds: float | None = None,
        app_name: str | None = None,
        created_at: datetime | None = None,
    ) -> int:
        if status not in {"saved", "pasted", "cancelled", "failed"}:
            raise ValueError(f"Unknown transcript status: {status}")
        created = created_at or datetime.now().astimezone()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO transcripts
                   (created_at, mode, text, audio_path, note_path, metadata_json,
                    status, raw_text, duration_seconds, app_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    created.isoformat(timespec="seconds"),
                    mode,
                    text,
                    str(audio_path) if audio_path else None,
                    str(note_path) if note_path else None,
                    json.dumps(metadata, sort_keys=True),
                    status,
                    raw_text,
                    duration_seconds,
                    app_name,
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 10) -> list[dict[str, object]]:
        return self.search(limit=limit)

    def search(
        self,
        *,
        limit: int = 100,
        query: str | None = "",
        status: str | None = "",
        before_id: int | None = None,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        parameters: list[object] = []
        if query:
            clauses.append("(text LIKE ? ESCAPE '\\' OR raw_text LIKE ? ESCAPE '\\')")
            term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parameters.extend((term, term))
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if before_id is not None:
            clauses.append("id < ?")
            parameters.append(before_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT * FROM transcripts{where} ORDER BY id DESC LIMIT ?", parameters
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, transcript_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_text(self, transcript_id: int, text: str) -> dict[str, object] | None:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE transcripts SET text = ?, edited_at = ? WHERE id = ?",
                (text, datetime.now().astimezone().isoformat(timespec="seconds"), transcript_id),
            )
        return self.get(transcript_id) if result.rowcount else None

    def set_status(self, transcript_id: int, status: str) -> dict[str, object] | None:
        if status not in {"saved", "pasted", "cancelled", "failed"}:
            raise ValueError(f"Unknown transcript status: {status}")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE transcripts SET status = ? WHERE id = ?", (status, transcript_id)
            )
        return self.get(transcript_id) if result.rowcount else None

    def update_transcription(
        self,
        transcript_id: int,
        *,
        text: str,
        raw_text: str,
        metadata: dict[str, object],
        status: str,
        note_path: Path | None,
    ) -> dict[str, object] | None:
        if status not in {"saved", "pasted", "cancelled", "failed"}:
            raise ValueError(f"Unknown transcript status: {status}")
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE transcripts
                   SET text = ?, raw_text = ?, metadata_json = ?, status = ?,
                       note_path = COALESCE(?, note_path), edited_at = NULL
                   WHERE id = ?""",
                (
                    text,
                    raw_text,
                    json.dumps(metadata, sort_keys=True),
                    status,
                    str(note_path) if note_path else None,
                    transcript_id,
                ),
            )
        return self.get(transcript_id) if result.rowcount else None

    def recover_orphaned_audio(self, recordings: Path) -> list[int]:
        """Add history rows for recordings whose transcription never finished.

        A crash or restart during transcription leaves the WAV on disk with no row,
        which hides it from the dashboard. Recovered rows are marked failed so they
        can be retried.
        """
        if not recordings.is_dir():
            return []
        with self._connect() as connection:
            known = {
                row[0]
                for row in connection.execute(
                    "SELECT audio_path FROM transcripts WHERE audio_path IS NOT NULL"
                )
            }
        recovered: list[int] = []
        for path in sorted(recordings.glob("*.wav")):
            if str(path) in known or path.stat().st_size <= EMPTY_WAV_BYTES:
                continue
            created_at, mode = _recording_identity(path)
            recovered.append(
                self.save(
                    mode=mode,
                    text="",
                    audio_path=path,
                    note_path=None,
                    metadata={"error": "Transcription was interrupted; audio recovered"},
                    status="failed",
                    duration_seconds=_wav_seconds(path),
                    created_at=created_at,
                )
            )
        return recovered

    def stats(self) -> dict[str, object]:
        now = datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = now - timedelta(days=7)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT created_at, text, duration_seconds, mode, status, app_name FROM transcripts"
            ).fetchall()
        words_today = words_week = total_words = 0
        wpm_words = 0
        wpm_week_words = 0
        speaking_seconds = speaking_week = 0.0
        by_status: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_app: dict[str, int] = {}
        for created, value, duration, mode, status, app_name in rows:
            by_status[status] = by_status.get(status, 0) + 1
            by_mode[mode] = by_mode.get(mode, 0) + 1
            if mode != "dictation" or status == "failed":
                continue
            words = len(value.split())
            if app_name:
                by_app[app_name] = by_app.get(app_name, 0) + words
            stamp = datetime.fromisoformat(created)
            words_today += words if stamp >= today else 0
            words_week += words if stamp >= week else 0
            total_words += words
            valid_duration = _valid_wpm_duration(duration, words)
            if valid_duration is None:
                continue
            wpm_words += words
            speaking_seconds += valid_duration
            if stamp >= week:
                wpm_week_words += words
                speaking_week += valid_duration
        average_wpm = round(wpm_words * 60 / speaking_seconds) if speaking_seconds else 0
        return {
            "words_today": words_today,
            "words_week": words_week,
            "speaking_seconds": round(speaking_seconds, 1),
            "average_wpm": average_wpm,
            "words_per_minute": average_wpm,
            "total_words": total_words,
            "estimated_minutes_saved": round(max(0, wpm_week_words / 40 - speaking_week / 60), 1),
            "by_status": by_status,
            "by_mode": by_mode,
            "words_by_app": by_app,
            "count": len(rows),
        }

    def prune_audio(self, retention_days: int) -> int:
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now().astimezone() - timedelta(days=retention_days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, audio_path FROM transcripts
                   WHERE mode = 'dictation' AND created_at < ? AND audio_path IS NOT NULL""",
                (cutoff,),
            ).fetchall()
            for transcript_id, audio_path in rows:
                Path(audio_path).unlink(missing_ok=True)
                connection.execute(
                    "UPDATE transcripts SET audio_path = NULL WHERE id = ?", (transcript_id,)
                )
        return len(rows)

    def corrected_pairs(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT audio_path, text FROM transcripts
                   WHERE edited_at IS NOT NULL AND audio_path IS NOT NULL
                     AND status != 'failed' ORDER BY id"""
            ).fetchall()
        return [
            {"audio": audio_path, "reference": text}
            for audio_path, text in rows
            if Path(audio_path).is_file() and text.strip()
        ]


def _recording_identity(path: Path) -> tuple[datetime, str]:
    stamp, _, mode = path.stem.rpartition("-")
    try:
        created_at = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S-%f").astimezone()
    except ValueError:
        created_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return created_at, mode if mode in RECORDING_MODES else "dictation"


def _wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path)) as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None


def _valid_wpm_duration(value: object, word_count: int) -> float | None:
    if word_count <= 0:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < MIN_WPM_SAMPLE_SECONDS:
        return None
    if word_count * 60 / seconds > MAX_PLAUSIBLE_WPM:
        return None
    return seconds


def write_note(mode: str, text: str, audio_path: Path | None, started_at: datetime) -> Path:
    directory = data_dir() / ("meetings" if mode == "meeting" else "notes")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = started_at.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    path = directory / f"{stamp}-{mode}.md"
    title = "Meeting transcript" if mode == "meeting" else "Voice note"
    audio_line = f"- Audio: `{audio_path}`\n" if audio_path else ""
    path.write_text(
        f"# {title}\n\n"
        f"- Recorded: {started_at.astimezone().isoformat(timespec='seconds')}\n"
        f"{audio_line}\n"
        f"## Transcript\n\n{text}\n",
        encoding="utf-8",
    )
    return path
