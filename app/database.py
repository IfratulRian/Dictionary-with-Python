"""SQLite access layer. All SQL lives here; the UI never writes SQL."""
from __future__ import annotations

import logging
import random
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .utils import normalize_word, utc_now_iso

log = logging.getLogger(__name__)

MAX_HISTORY = 5000  # oldest history rows beyond this are trimmed

SCHEMA = """
CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word            TEXT NOT NULL,
    normalized_word TEXT NOT NULL UNIQUE,      -- UNIQUE also creates the search index
    phonetic        TEXT NOT NULL DEFAULT '',
    audio_path      TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meanings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id        INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    part_of_speech TEXT NOT NULL DEFAULT '',
    definition     TEXT NOT NULL,
    example        TEXT NOT NULL DEFAULT '',
    synonyms       TEXT NOT NULL DEFAULT '',
    antonyms       TEXT NOT NULL DEFAULT '',
    etymology      TEXT NOT NULL DEFAULT '',
    UNIQUE (word_id, part_of_speech, definition)   -- stops duplicate definitions
);
CREATE INDEX IF NOT EXISTS idx_meanings_word ON meanings(word_id);
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id    INTEGER NOT NULL UNIQUE REFERENCES words(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id       INTEGER REFERENCES words(id) ON DELETE SET NULL,
    searched_word TEXT NOT NULL,
    found         INTEGER NOT NULL,
    searched_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_time ON search_history(searched_at);
"""


class DatabaseError(Exception):
    """A database problem, with a message that is safe to show to the user."""


@dataclass(frozen=True)
class Meaning:
    part_of_speech: str
    definition: str
    example: str = ""
    synonyms: str = ""
    antonyms: str = ""
    etymology: str = ""


@dataclass(frozen=True)
class WordEntry:
    id: int
    word: str
    phonetic: str
    audio_path: str
    source: str
    meanings: tuple[Meaning, ...] = ()


@dataclass(frozen=True)
class HistoryItem:
    id: int
    word_id: Optional[int]
    searched_word: str
    found: bool
    searched_at: str


@dataclass(frozen=True)
class FavoriteItem:
    word_id: int
    word: str
    preview: str
    created_at: str


@dataclass(frozen=True)
class UpsertResult:
    new_word: bool
    meanings_added: int
    meanings_skipped: int


def like_escape(text: str) -> str:
    """Escape LIKE wildcards so user text is matched literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None
        self._depth = 0  # >0 while inside transaction()

    # ---------- connection ----------
    def connect(self) -> "Database":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, timeout=5)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA)  # creates tables on first run
            self.conn.commit()
        except (sqlite3.Error, OSError) as exc:
            self.close()
            raise DatabaseError(self._explain(exc)) from exc
        return self

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None

    @staticmethod
    def _explain(exc: Exception) -> str:
        text = str(exc).lower()
        if "locked" in text or "busy" in text:
            return "The dictionary database is busy. Close other copies of the app and try again."
        if "not a database" in text or "malformed" in text or "corrupt" in text:
            return "The dictionary database file is damaged."
        if isinstance(exc, PermissionError) or "readonly" in text or "permission" in text:
            return "The app does not have permission to write to its data folder."
        return f"Database error: {exc}"

    # ---------- low-level helpers ----------
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        if self.conn is None:
            raise DatabaseError("The database is not open.")
        try:
            cursor = self.conn.execute(sql, params)
            if self._depth == 0:
                self.conn.commit()
            return cursor
        except sqlite3.Error as exc:
            if self._depth == 0:
                self.conn.rollback()
            log.error("SQL failure: %s", exc)
            raise DatabaseError(self._explain(exc)) from exc

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        if self.conn is None:
            raise DatabaseError("The database is not open.")
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            log.error("SQL failure: %s", exc)
            raise DatabaseError(self._explain(exc)) from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group many writes into one all-or-nothing transaction."""
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            if self._depth == 0 and self.conn is not None:
                self.conn.rollback()
            raise
        self._depth -= 1
        if self._depth == 0 and self.conn is not None:
            try:
                self.conn.commit()
            except sqlite3.Error as exc:
                self.conn.rollback()
                raise DatabaseError(self._explain(exc)) from exc

    # ---------- words ----------
    def count_words(self) -> int:
        return self._query("SELECT COUNT(*) FROM words")[0][0]

    def count_meanings(self) -> int:
        return self._query("SELECT COUNT(*) FROM meanings")[0][0]

    def upsert_entry(self, word: str, phonetic: str, audio_path: str, source: str,
                     meanings: list[dict]) -> UpsertResult:
        """Add a word, or merge new meanings into an existing word (same normalized form)."""
        normalized = normalize_word(word)
        added = skipped = 0
        with self.transaction():
            rows = self._query("SELECT id FROM words WHERE normalized_word = ?", (normalized,))
            if rows:
                word_id, new_word = rows[0]["id"], False
                # Fill in missing extras without overwriting existing data.
                self._execute(
                    "UPDATE words SET phonetic = CASE WHEN phonetic = '' THEN ? ELSE phonetic END, "
                    "audio_path = CASE WHEN audio_path = '' THEN ? ELSE audio_path END, "
                    "source = CASE WHEN source = '' THEN ? ELSE source END WHERE id = ?",
                    (phonetic, audio_path, source, word_id))
            else:
                cursor = self._execute(
                    "INSERT INTO words (word, normalized_word, phonetic, audio_path, source) "
                    "VALUES (?, ?, ?, ?, ?)", (word.strip(), normalized, phonetic, audio_path, source))
                word_id, new_word = cursor.lastrowid, True
            for m in meanings:
                cursor = self._execute(
                    "INSERT OR IGNORE INTO meanings (word_id, part_of_speech, definition, example, "
                    "synonyms, antonyms, etymology) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (word_id, m["part_of_speech"], m["definition"], m["example"],
                     m["synonyms"], m["antonyms"], m["etymology"]))
                if cursor.rowcount:
                    added += 1
                else:
                    skipped += 1
        return UpsertResult(new_word, added, skipped)

    def _meanings_for(self, word_id: int) -> tuple[Meaning, ...]:
        rows = self._query("SELECT part_of_speech, definition, example, synonyms, antonyms, "
                           "etymology FROM meanings WHERE word_id = ? ORDER BY id", (word_id,))
        return tuple(Meaning(*tuple(r)) for r in rows)

    def _entry_from_row(self, row: sqlite3.Row | None) -> Optional[WordEntry]:
        if row is None:
            return None
        return WordEntry(row["id"], row["word"], row["phonetic"], row["audio_path"],
                         row["source"], self._meanings_for(row["id"]))

    def get_entry_by_word(self, word: str) -> Optional[WordEntry]:
        rows = self._query("SELECT * FROM words WHERE normalized_word = ?", (normalize_word(word),))
        return self._entry_from_row(rows[0] if rows else None)

    def get_entry(self, word_id: int) -> Optional[WordEntry]:
        rows = self._query("SELECT * FROM words WHERE id = ?", (word_id,))
        return self._entry_from_row(rows[0] if rows else None)

    def prefix_words(self, prefix: str, limit: int = 8) -> list[str]:
        """Words starting with `prefix`. A range query lets SQLite use the index."""
        prefix = normalize_word(prefix)
        if not prefix:
            return []
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        rows = self._query("SELECT word FROM words WHERE normalized_word >= ? AND normalized_word < ? "
                           "ORDER BY normalized_word LIMIT ?", (prefix, upper, limit))
        return [r["word"] for r in rows]

    def similar_candidates(self, prefix: str, min_len: int, max_len: int,
                           limit: int = 2000) -> list[tuple[str, str]]:
        """(display word, normalized word) pairs for fuzzy matching."""
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        rows = self._query(
            "SELECT word, normalized_word FROM words WHERE normalized_word >= ? AND normalized_word < ? "
            "AND LENGTH(normalized_word) BETWEEN ? AND ? LIMIT ?", (prefix, upper, min_len, max_len, limit))
        return [(r["word"], r["normalized_word"]) for r in rows]

    def id_bounds(self) -> Optional[tuple[int, int]]:
        row = self._query("SELECT MIN(id), MAX(id) FROM words")[0]
        return None if row[0] is None else (row[0], row[1])

    def first_word_id_from(self, target_id: int) -> Optional[int]:
        rows = self._query("SELECT id FROM words WHERE id >= ? ORDER BY id LIMIT 1", (target_id,))
        return rows[0]["id"] if rows else None

    def random_word_id(self) -> Optional[int]:
        bounds = self.id_bounds()
        if bounds is None:
            return None
        return self.first_word_id_from(random.randint(*bounds))

    # ---------- favorites ----------
    def is_favorite(self, word_id: int) -> bool:
        return bool(self._query("SELECT 1 FROM favorites WHERE word_id = ?", (word_id,)))

    def add_favorite(self, word_id: int) -> bool:
        """Returns True if added, False if it was already a favorite."""
        cursor = self._execute("INSERT OR IGNORE INTO favorites (word_id, created_at) VALUES (?, ?)",
                               (word_id, utc_now_iso()))
        return cursor.rowcount > 0

    def remove_favorite(self, word_id: int) -> bool:
        return self._execute("DELETE FROM favorites WHERE word_id = ?", (word_id,)).rowcount > 0

    def list_favorites(self, text_filter: str = "", limit: int = 500) -> list[FavoriteItem]:
        pattern = "%" + like_escape(normalize_word(text_filter)) + "%"
        rows = self._query(
            "SELECT f.word_id, w.word, f.created_at, "
            "(SELECT definition FROM meanings m WHERE m.word_id = w.id ORDER BY m.id LIMIT 1) AS preview "
            "FROM favorites f JOIN words w ON w.id = f.word_id "
            "WHERE w.normalized_word LIKE ? ESCAPE '\\' ORDER BY f.id DESC LIMIT ?", (pattern, limit))
        return [FavoriteItem(r["word_id"], r["word"], r["preview"] or "", r["created_at"]) for r in rows]

    # ---------- history ----------
    def record_history(self, searched_word: str, word_id: Optional[int], found: bool) -> None:
        with self.transaction():
            self._execute("INSERT INTO search_history (word_id, searched_word, found, searched_at) "
                          "VALUES (?, ?, ?, ?)", (word_id, searched_word, int(found), utc_now_iso()))
            self._execute("DELETE FROM search_history WHERE id <= "
                          "(SELECT MAX(id) FROM search_history) - ?", (MAX_HISTORY,))

    def recent_history(self, limit: int = 50, offset: int = 0) -> list[HistoryItem]:
        rows = self._query("SELECT id, word_id, searched_word, found, searched_at FROM search_history "
                           "ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [HistoryItem(r["id"], r["word_id"], r["searched_word"], bool(r["found"]),
                            r["searched_at"]) for r in rows]

    def recent_words(self, limit: int = 8) -> list[str]:
        """Distinct recently found words, newest first."""
        rows = self._query("SELECT w.word FROM search_history h JOIN words w ON w.id = h.word_id "
                           "GROUP BY w.id ORDER BY MAX(h.id) DESC LIMIT ?", (limit,))
        return [r["word"] for r in rows]

    def clear_history(self) -> None:
        self._execute("DELETE FROM search_history")

    # ---------- maintenance ----------
    def backup_to(self, destination: Path | str) -> None:
        """Copy the live database using SQLite's online backup API."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise DatabaseError("Choose a different file than the live database.")
        if self.conn is None:
            raise DatabaseError("The database is not open.")
        try:
            target = sqlite3.connect(destination)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        except (sqlite3.Error, OSError) as exc:
            raise DatabaseError(self._explain(exc)) from exc

    def file_size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
