"""Small helpers shared by the whole application (paths, text, time, logging)."""
from __future__ import annotations

import logging
import os
import sys
import unicodedata
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "PyDictionary Offline"
APP_VERSION = "1.0.0"
MAX_QUERY_LENGTH = 100  # longest search text we accept


def is_frozen() -> bool:
    """True when running as a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Path to a READ-ONLY bundled file (sample data, icons).

    PyInstaller unpacks bundled files into sys._MEIPASS, so we look there
    first and fall back to the project folder when running from source.
    """
    base = Path(getattr(sys, "_MEIPASS", project_root()))
    return base.joinpath(*parts)


def user_data_dir() -> Path:
    """Writable folder for the database, settings and logs.

    - From source:   <project>/user_data
    - Packaged .exe: %LOCALAPPDATA%/PyDictionaryOffline (never inside the
      install folder, which may be read-only)
    - Tests / power users can override with PYDICT_DATA_DIR.
    """
    override = os.environ.get("PYDICT_DATA_DIR")
    if override:
        path = Path(override)
    elif is_frozen():
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PyDictionaryOffline"
    else:
        path = project_root() / "user_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_query(text: str) -> str:
    """Trim the text and collapse repeated whitespace."""
    return " ".join((text or "").split())


def normalize_word(text: str) -> str:
    """Canonical form used for searching: Unicode-normalised, trimmed, lower-case."""
    return clean_query(unicodedata.normalize("NFKC", text or "")).casefold()


def shorten(text: str, limit: int = 120) -> str:
    text = clean_query(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def utc_now_iso() -> str:
    """Timestamps are always stored in UTC as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp(stored: str) -> str:
    """Convert a stored UTC timestamp into a readable local time."""
    try:
        moment = datetime.strptime(stored, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return stored or ""


def setup_logging(data_dir: Path) -> None:
    """Log to a file in user_data. We never log the words people search for."""
    handler = RotatingFileHandler(data_dir / "pydictionary.log", maxBytes=512_000,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
