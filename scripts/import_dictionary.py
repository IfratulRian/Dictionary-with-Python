r"""Command-line import:  python scripts/import_dictionary.py path\to\dictionary.json

Useful for large files (no window needed). Supports .json, .jsonl, .csv and .pdf.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Database, DatabaseError  # noqa: E402
from app.dictionary_importer import ImportFormatError, import_file  # noqa: E402
from app.pdf_importer import import_pdf  # noqa: E402
from app.utils import user_data_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import dictionary data into PyDictionary Offline.")
    parser.add_argument("file", type=Path, help="a .json, .jsonl, .csv or .pdf file")
    parser.add_argument("--db", type=Path, default=user_data_dir() / "dictionary.db",
                        help="database to import into (default: the app's database)")
    args = parser.parse_args()

    def show_progress(done: int, total, message: str) -> None:
        print(f"\r{message:<60}", end="", flush=True)

    try:
        db = Database(args.db).connect()
        try:
            run = import_pdf if args.file.suffix.lower() == ".pdf" else import_file
            summary = run(db, args.file, progress=show_progress)
        finally:
            db.close()
    except (ImportFormatError, DatabaseError) as exc:
        print(f"\nImport failed: {exc}")
        return 1
    print("\n" + summary.format())
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
