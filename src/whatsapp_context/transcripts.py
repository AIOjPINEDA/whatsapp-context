"""Transcripts of voice notes, kept as text only in transcripts.sqlite (0600, next to the export).

Audio is never kept: WAExport copies the requested notes, wa-transcribe reads them and they are deleted.
After every export the cache keeps only rows whose message is still in it, so revoking or excluding a
chat, or a message leaving the policy's window, removes its transcript too.
"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import paths

SCHEMA = """CREATE TABLE IF NOT EXISTS transcripts (
    pk INTEGER PRIMARY KEY, locale TEXT, text TEXT, confidence REAL, seconds REAL, created TEXT)"""


def cache_file() -> Path:
    return paths.data_dir() / "transcripts.sqlite"


def _open() -> sqlite3.Connection:
    path = cache_file()
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def load() -> dict[int, sqlite3.Row]:
    """Every cached transcript by message Z_PK ({} when there is no cache yet)."""
    if not cache_file().exists():
        return {}
    with closing(sqlite3.connect(f"file:{cache_file()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return {row["pk"]: row for row in conn.execute("SELECT * FROM transcripts")}
        except sqlite3.OperationalError:
            return {}


def _voice_notes(export: sqlite3.Connection) -> set[int]:
    return {pk for (pk,) in export.execute("SELECT Z_PK FROM messages WHERE ZMESSAGETYPE = 3")}


def save(rows: list[dict], export: sqlite3.Connection) -> list[dict]:
    """Cache the rows whose voice note is still in the export (call under the export lock); returns them."""
    created = datetime.now(UTC).isoformat(timespec="seconds")
    keep = _voice_notes(export)
    rows = [r for r in rows if r["pk"] in keep]
    values = [(r["pk"], r["locale"], r["text"], r.get("confidence"), r.get("seconds"), created) for r in rows]
    with closing(_open()) as conn, conn:
        conn.executemany("INSERT OR REPLACE INTO transcripts VALUES (?, ?, ?, ?, ?, ?)", values)
    return rows


def prune(export: sqlite3.Connection) -> int:
    """Drop transcripts whose voice note is no longer in the export; returns how many went."""
    if not cache_file().exists():
        return 0
    keep = _voice_notes(export)
    with closing(_open()) as conn, conn:
        gone = [pk for (pk,) in conn.execute("SELECT pk FROM transcripts") if pk not in keep]
        conn.executemany("DELETE FROM transcripts WHERE pk = ?", [(pk,) for pk in gone])
    if gone:
        with closing(_open()) as conn:
            conn.execute("VACUUM")  # deleted text must not linger in free pages
    return len(gone)
