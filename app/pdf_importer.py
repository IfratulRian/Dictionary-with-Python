"""Best-effort import of dictionary entries from a text-based PDF (PyMuPDF).

IMPORTANT LIMITS (also documented in the README):
  * Only PDFs that contain real text work. Scanned PDFs need OCR first.
  * Entries must look like  "word (phonetic) n. definition..."  or "word n. definition".
  * Unusual layouts (3+ columns, tables, side-bars) will be parsed badly or skipped.
If a PDF does not parse well, use a structured JSON or CSV dataset instead.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Iterator, Optional

from .database import Database
from .dictionary_importer import (ImportFormatError, ImportSummary, ProgressCallback,
                                  RawEntry, import_entries)

log = logging.getLogger(__name__)

POS_NAMES = {"n": "noun", "v": "verb", "adj": "adjective", "adv": "adverb", "prep": "preposition",
             "pron": "pronoun", "conj": "conjunction", "interj": "interjection",
             "det": "determiner", "abbr": "abbreviation"}
_POS_WORDS = "|".join(sorted(list(POS_NAMES) + list(POS_NAMES.values()), key=len, reverse=True))

# word [optional /phonetic/ or [phonetic]] part-of-speech [. : -] definition
ENTRY_PATTERN = re.compile(
    rf"^(?P<word>[A-Za-z][A-Za-z'\-]{{0,40}})"
    rf"(?:\s*(?P<phonetic>[/\[][^/\]]{{1,60}}[/\]]))?[,:]?\s+"   # whitespace is REQUIRED before the POS
    rf"\(?(?P<pos>{_POS_WORDS})\b\.?\)?\s*[:.\-–—]?\s*(?P<definition>.*)$",
    re.IGNORECASE)

HEADER_FOOTER_MARGIN = 0.06  # ignore text in the top/bottom 6% of each page


def parse_dictionary_lines(lines: list[str]) -> list[dict]:
    """Group text lines into entries. Lines that do not start an entry continue the previous one."""
    entries: list[dict] = []
    current: Optional[dict] = None
    for line in lines:
        line = line.strip()
        if not line or line.isdigit():  # blank lines and bare page numbers
            continue
        match = ENTRY_PATTERN.match(line)
        if match:
            pos = match["pos"].lower().rstrip(".")
            current = {"word": match["word"],
                       "phonetic": (match["phonetic"] or "").strip("/[] "),
                       "meanings": [{"part_of_speech": POS_NAMES.get(pos, pos),
                                     "definition": match["definition"].strip()}]}
            entries.append(current)
        elif current is not None:
            meaning = current["meanings"][0]
            text = meaning["definition"]
            # Join words split by a hyphen at the end of a line.
            meaning["definition"] = (text[:-1] + line) if text.endswith("-") else f"{text} {line}".strip()
    return [e for e in entries if e["meanings"][0]["definition"]]


def _page_lines(page) -> list[str]:
    """Extract a page's lines in reading order, handling two columns and skipping headers/footers."""
    width, height = page.rect.width, page.rect.height
    blocks = [b for b in page.get_text("blocks")
              if b[6] == 0  # text blocks only
              and height * HEADER_FOOTER_MARGIN < b[1] < height * (1 - HEADER_FOOTER_MARGIN)]
    middle = width / 2
    has_right_column = any(b[0] >= middle - 5 for b in blocks) and any(b[2] <= middle + 5 for b in blocks)

    def sort_key(block):
        column = 1 if has_right_column and block[0] >= middle - 5 else 0
        return (column, round(block[1], 1), block[0])

    lines: list[str] = []
    for block in sorted(blocks, key=sort_key):
        lines.extend(block[4].splitlines())
    return lines


def import_pdf(db: Database, path: Path | str, progress: Optional[ProgressCallback] = None,
               cancel_event: Optional[threading.Event] = None) -> ImportSummary:
    path = Path(path)
    try:
        import pymupdf as fitz  # PyMuPDF; imported here so the rest of the app works without it
    except ImportError as exc:
        raise ImportFormatError("PDF import needs PyMuPDF. Install it with: python -m pip install pymupdf") from exc
    if not path.is_file():
        raise ImportFormatError(f"File not found: {path.name}")
    try:
        document = fitz.open(path)
    except Exception as exc:  # PyMuPDF raises several error types
        raise ImportFormatError(f"Could not open the PDF ({exc}). It may be damaged or not a PDF.") from exc

    summary = ImportSummary(source_name=path.name)
    try:
        if document.needs_pass:
            raise ImportFormatError("This PDF is password protected.")
        pages = document.page_count
        unreadable: list[int] = []
        no_entries: list[int] = []

        def entries() -> Iterator[RawEntry]:
            for number in range(pages):
                if cancel_event is not None and cancel_event.is_set():
                    return
                if progress:
                    progress(number, pages, f"Reading page {number + 1} of {pages}…")
                try:
                    lines = _page_lines(document[number])
                except Exception:
                    log.exception("Could not read PDF page")
                    unreadable.append(number + 1)
                    continue
                if not any(line.strip() for line in lines):
                    unreadable.append(number + 1)  # no text layer: blank or scanned
                    continue
                parsed = parse_dictionary_lines(lines)
                if not parsed:
                    no_entries.append(number + 1)
                for index, entry in enumerate(parsed, 1):
                    entry["source"] = f"PDF: {path.name}"
                    yield f"page {number + 1}, entry {index}", entry, ""

        import_entries(db, entries(), summary, default_source=f"PDF: {path.name}", total=None,
                       progress=None, cancel_event=cancel_event)
    finally:
        document.close()

    if unreadable:
        summary.notes.append(f"{len(unreadable)} page(s) had no extractable text (scanned or blank): "
                             f"{_page_list(unreadable)}. Scanned PDFs need OCR first.")
    if no_entries:
        summary.notes.append(f"{len(no_entries)} page(s) had text but no recognisable entries: "
                             f"{_page_list(no_entries)}.")
    if summary.total == 0 and not summary.cancelled:
        summary.failed = True
        summary.add_error("No dictionary entries could be recognised. This PDF's layout is not supported. "
                          "Import a structured JSON or CSV dictionary instead.", rejected=False)
    if progress:
        progress(pages, pages, "Finished")
    return summary


def _page_list(pages: list[int], limit: int = 15) -> str:
    text = ", ".join(str(p) for p in pages[:limit])
    return text + (" …" if len(pages) > limit else "")
