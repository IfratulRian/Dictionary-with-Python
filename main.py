"""PyDictionary Offline - entry point.  Run with:  python main.py"""
from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from app.database import Database, DatabaseError
from app.dictionary_importer import ImportFormatError, import_file
from app.search import SearchService
from app.settings import Settings
from app.utils import resource_path, setup_logging, user_data_dir

log = logging.getLogger("main")


def open_database(path: Path) -> Database:
    """Open (or create) the database. If it is damaged, offer to keep it aside and start fresh."""
    try:
        return Database(path).connect()
    except DatabaseError as exc:
        log.error("Database could not be opened: %s", exc)
        if not path.exists():
            raise
        keep = messagebox.askyesno(
            "Dictionary database problem",
            f"{exc}\n\nWould you like to move the damaged file aside (it is NOT deleted) and create a new "
            "dictionary with the sample words?")
        if not keep:
            raise
        backup = path.with_name(f"{path.stem}.damaged-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        path.replace(backup)
        for extra in ("-wal", "-shm"):  # leftover SQLite side files
            sidecar = Path(str(path) + extra)
            if sidecar.exists():
                sidecar.unlink(missing_ok=True)
        messagebox.showinfo("Database moved", f"The damaged file was kept as:\n{backup}")
        return Database(path).connect()


def seed_sample_data(db: Database, settings: Settings) -> None:
    """On the very first run, load the bundled sample dictionary so the app is usable immediately."""
    if settings.get("sample_seeded") or db.count_words() > 0:
        return
    try:
        summary = import_file(db, resource_path("data", "sample_dictionary.json"))
        if not summary.failed:
            settings.set("sample_seeded", True)
    except (ImportFormatError, DatabaseError):
        log.exception("Could not load the sample data")


def main() -> int:
    data_dir = user_data_dir()
    setup_logging(data_dir)
    settings = Settings(data_dir / "settings.json")
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("CustomTkinter is not installed. Run:  python -m pip install -r requirements.txt")
        return 1

    dialog_root = tk.Tk()
    dialog_root.withdraw()  # hidden window so message boxes work before the main window exists
    try:
        db = open_database(data_dir / "dictionary.db")
    except DatabaseError as exc:
        messagebox.showerror("Cannot start", f"{exc}\n\nDatabase location:\n{data_dir}")
        return 1
    finally:
        dialog_root.destroy()

    seed_sample_data(db, settings)
    from app.ui import App  # imported late so a missing GUI library gives the friendly message above
    app = App(db, SearchService(db), settings)
    try:
        app.mainloop()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
