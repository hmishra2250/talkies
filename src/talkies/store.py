from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import data_dir, state_dir


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
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO transcripts
                   (created_at, mode, text, audio_path, note_path, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    mode,
                    text,
                    str(audio_path) if audio_path else None,
                    str(note_path) if note_path else None,
                    json.dumps(metadata, sort_keys=True),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int = 10) -> list[dict[str, object]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM transcripts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


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
