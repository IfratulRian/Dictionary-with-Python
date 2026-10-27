"""Import structured dictionary data (JSON, JSON Lines, CSV) into SQLite.

Importing only ADDS data. Existing words are never deleted or overwritten:
if a word already exists, only meanings that are not already stored get added.
"""
from __future__ import annotations

import csv
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .database import Database, DatabaseError

log = logging.getLogger(__name__)

BATCH_SIZE = 500           # entries per database transaction
MAX_REPORTED_ERRORS = 200  # keep the report readable
SUPPORTED_EXTENSIONS = {".json", ".jsonl", ".csv"}

# progress(done, total_or_None, message)
ProgressCallback = Callable[[int, Optional[int], str], None]
RawEntry = tuple[str, object, str]  # (label for error messages, raw data, error text)


class ImportFormatError(Exception):
    """The file cannot be used (missing, unsupported, unreadable)."""


@dataclass
class ImportSummary:
    source_name: str = ""
    total: int = 0
    words_added: int = 0
    words_merged: int = 0
    meanings_added: int = 0
    duplicates_skipped: int = 0
    rejected: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False
    failed: bool = False

    def add_error(self, message: str, rejected: bool = True) -> None:
        if rejected:
            self.rejected += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append(message)

    def format(self) -> str:
        lines = [f"Import of {self.source_name}: "
                 + ("FAILED" if self.failed else "CANCELLED (entries imported so far were kept)"
                    if self.cancelled else "finished")]
        lines += [f"  New words added:        {self.words_added}",
                  f"  Existing words updated: {self.words_merged}",
                  f"  Definitions added:      {self.meanings_added}",
                  f"  Duplicates skipped:     {self.duplicates_skipped}",
                  f"  Entries rejected:       {self.rejected}"]
        lines += [f"  Note: {n}" for n in self.notes]
        if self.errors:
            lines.append("Problems:")
            lines += [f"  - {e}" for e in self.errors[:15]]
            if len(self.errors) > 15:
                lines.append(f"  ... and {len(self.errors) - 15} more")
        return "\n".join(lines)


# ---------------------------------------------------------------- normalising
def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return " ".join(str(value).split())


def _from_wiktextract(raw: dict) -> dict:
    """Best-effort conversion of Wiktextract/kaikki.org JSONL records."""
    meanings = []
    for sense in raw.get("senses") or []:
        if not isinstance(sense, dict):
            continue
        glosses = sense.get("glosses") or sense.get("raw_glosses") or []
        if not glosses:
            continue
        examples = sense.get("examples") or []
        example = examples[0].get("text", "") if examples and isinstance(examples[0], dict) else ""
        synonyms = [s.get("word", "") for s in sense.get("synonyms") or [] if isinstance(s, dict)]
        antonyms = [s.get("word", "") for s in sense.get("antonyms") or [] if isinstance(s, dict)]
        meanings.append({"part_of_speech": raw.get("pos", ""), "definition": glosses[-1],
                         "example": example, "synonyms": synonyms, "antonyms": antonyms,
                         "etymology": raw.get("etymology_text", "")})
    ipa = next((s["ipa"] for s in raw.get("sounds") or [] if isinstance(s, dict) and s.get("ipa")), "")
    return {"word": raw.get("word", ""), "phonetic": ipa, "meanings": meanings,
            "source": "Wiktionary via Wiktextract (check CC BY-SA licence terms)"}


def normalize_entry(raw: object, default_source: str = "") -> tuple[Optional[dict], str]:
    """Turn one raw record into the clean dict the database expects.

    Accepted shapes:
      * {"word":..., "phonetic":..., "meanings":[{"part_of_speech":..., "definition":...}]}
      * flat records / CSV rows with "word" and "definition" columns
      * Wiktextract records (best effort)
    Returns (entry, "") or (None, reason).
    """
    if not isinstance(raw, dict):
        return None, "entry is not an object"
    if isinstance(raw.get("senses"), list):
        raw = _from_wiktextract(raw)
    word = _text(raw.get("word"))
    if not word:
        return None, "missing 'word'"
    if len(word) > 100:
        return None, "word is longer than 100 characters"
    meanings_raw = raw.get("meanings")
    if meanings_raw is None:
        meanings_raw = [raw]  # flat form: the record itself is one meaning
    if not isinstance(meanings_raw, list):
        return None, f"'meanings' for '{word}' must be a list"
    meanings = []
    for item in meanings_raw:
        if not isinstance(item, dict):
            continue
        definition = _text(item.get("definition"))
        if not definition:
            continue
        meanings.append({"part_of_speech": _text(item.get("part_of_speech")).lower(),
                         "definition": definition[:4000],
                         "example": _text(item.get("example"))[:2000],
                         "synonyms": _text(item.get("synonyms"))[:1000],
                         "antonyms": _text(item.get("antonyms"))[:1000],
                         "etymology": _text(item.get("etymology"))[:2000]})
    if not meanings:
        return None, f"'{word}' has no definition"
    return ({"word": word, "phonetic": _text(raw.get("phonetic")),
             "audio_path": _text(raw.get("audio_path")),
             "source": _text(raw.get("source")) or default_source, "meanings": meanings}, "")


# ---------------------------------------------------------------- file readers
def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
        return sum(1 for line in handle if line.strip())


def _read_json(path: Path) -> tuple[Iterator[RawEntry], int]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFormatError("The file is not valid UTF-8 text.") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportFormatError(f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).") from exc
    if isinstance(data, dict):
        data = data.get("entries", data.get("words", [data] if "word" in data else None))
    if not isinstance(data, list):
        raise ImportFormatError("Expected a JSON list of entries (or an object with an 'entries' list).")
    return ((f"entry {i}", item, "") for i, item in enumerate(data, 1)), len(data)


def _read_jsonl(path: Path) -> Iterator[RawEntry]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield f"line {number}", json.loads(line), ""
                except json.JSONDecodeError as exc:
                    yield f"line {number}", None, f"invalid JSON ({exc.msg})"
    except UnicodeDecodeError as exc:
        raise ImportFormatError("The file is not valid UTF-8 text.") from exc


def _read_csv(path: Path) -> Iterator[RawEntry]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
            if not {"word", "definition"} <= headers:
                raise ImportFormatError("The CSV needs a header row with at least 'word' and 'definition' columns.")
            for number, row in enumerate(reader, 2):  # row 1 is the header
                yield f"row {number}", {(k or "").strip().lower(): v for k, v in row.items()}, ""
    except UnicodeDecodeError as exc:
        raise ImportFormatError("The file is not valid UTF-8 text.") from exc
    except csv.Error as exc:
        raise ImportFormatError(f"Invalid CSV: {exc}") from exc


# ---------------------------------------------------------------- the pipeline
def import_entries(db: Database, entries: Iterable[RawEntry], summary: ImportSummary,
                   default_source: str = "", total: Optional[int] = None,
                   progress: Optional[ProgressCallback] = None,
                   cancel_event: Optional[threading.Event] = None) -> ImportSummary:
    """Write entries to the database in batches (one transaction per batch).

    A failed batch is rolled back, earlier batches stay saved, and the problem is
    reported in the summary instead of being hidden.
    """
    batch: list[dict] = []

    def flush() -> bool:
        if not batch:
            return True
        try:
            with db.transaction():
                for entry in batch:
                    result = db.upsert_entry(entry["word"], entry["phonetic"], entry["audio_path"],
                                             entry["source"], entry["meanings"])
                    summary.words_added += result.new_word
                    summary.words_merged += not result.new_word
                    summary.meanings_added += result.meanings_added
                    summary.duplicates_skipped += result.meanings_skipped
        except DatabaseError as exc:
            summary.failed = True
            summary.add_error(f"{exc} The last {len(batch)} entries were not saved.", rejected=False)
            return False
        finally:
            batch.clear()
        return True

    try:
        for label, raw, error in entries:
            if cancel_event is not None and cancel_event.is_set():
                summary.cancelled = True
                break
            summary.total += 1
            entry, problem = (None, error) if error else normalize_entry(raw, default_source)
            if entry is None:
                summary.add_error(f"{label}: {problem}")
                continue
            batch.append(entry)
            if len(batch) >= BATCH_SIZE:
                if not flush():
                    return summary
                if progress:
                    progress(summary.total, total, f"Imported {summary.total} entries…")
    except ImportFormatError as exc:
        summary.failed = True
        summary.add_error(str(exc), rejected=False)
    if flush() and progress:
        progress(summary.total, total, "Finished")
    return summary


def import_file(db: Database, path: Path | str, progress: Optional[ProgressCallback] = None,
                cancel_event: Optional[threading.Event] = None) -> ImportSummary:
    """Import a .json, .jsonl or .csv dictionary file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ImportFormatError(f"Unsupported file type '{suffix or 'unknown'}'. Use .json, .jsonl, .csv or a PDF.")
    if not path.is_file():
        raise ImportFormatError(f"File not found: {path.name}")
    summary = ImportSummary(source_name=path.name)
    try:
        if suffix == ".json":
            entries, total = _read_json(path)
        else:
            entries = _read_jsonl(path) if suffix == ".jsonl" else _read_csv(path)
            total = _count_lines(path) - (1 if suffix == ".csv" else 0)
    except ImportFormatError as exc:
        summary.failed = True
        summary.add_error(str(exc), rejected=False)
        return summary
    except OSError as exc:
        raise ImportFormatError(f"Could not read {path.name}: {exc.strerror or exc}") from exc
    try:
        return import_entries(db, entries, summary, default_source=f"Imported from {path.name}",
                              total=total, progress=progress, cancel_event=cancel_event)
    except OSError as exc:
        summary.failed = True
        summary.add_error(f"Could not read {path.name}: {exc.strerror or exc}", rejected=False)
        return summary
