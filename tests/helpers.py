"""Shared test helpers. Every test uses a temporary database, never the user's real one."""
import json
import tempfile
import unittest
from pathlib import Path

from app.database import Database


class TempDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = Database(self.dir / "test.db").connect()
        self.addCleanup(self.db.close)

    def add_word(self, word, definition="A test definition.", pos="noun", **extra):
        return self.db.upsert_entry(word, extra.get("phonetic", ""), "", "test",
                                    [{"part_of_speech": pos, "definition": definition, "example": "",
                                      "synonyms": "", "antonyms": "", "etymology": ""}])

    def write(self, name: str, content: str) -> Path:
        path = self.dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, name: str, data) -> Path:
        return self.write(name, json.dumps(data))
