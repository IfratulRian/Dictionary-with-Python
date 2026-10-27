import sqlite3
import unittest

from app.database import Database, DatabaseError
from tests.helpers import TempDbTestCase


class DatabaseTests(TempDbTestCase):
    def test_initialization_creates_tables(self):
        tables = {r[0] for r in self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"words", "meanings", "favorites", "search_history"} <= tables)
        self.assertEqual(self.db.count_words(), 0)

    def test_add_favorite_once_only(self):
        self.add_word("apple")
        word_id = self.db.get_entry_by_word("apple").id
        self.assertTrue(self.db.add_favorite(word_id))
        self.assertFalse(self.db.add_favorite(word_id))  # duplicate is ignored
        self.assertEqual(len(self.db.list_favorites()), 1)

    def test_remove_favorite(self):
        self.add_word("apple")
        word_id = self.db.get_entry_by_word("apple").id
        self.db.add_favorite(word_id)
        self.assertTrue(self.db.remove_favorite(word_id))
        self.assertFalse(self.db.is_favorite(word_id))
        self.assertFalse(self.db.remove_favorite(word_id))

    def test_favorites_filter_treats_wildcards_literally(self):
        self.add_word("apple")
        self.db.add_favorite(self.db.get_entry_by_word("apple").id)
        self.assertEqual(len(self.db.list_favorites("app")), 1)
        self.assertEqual(self.db.list_favorites("%"), [])

    def test_history_records_found_and_missing(self):
        self.add_word("apple")
        entry = self.db.get_entry_by_word("apple")
        self.db.record_history("apple", entry.id, True)
        self.db.record_history("zzz", None, False)
        items = self.db.recent_history()
        self.assertEqual([i.searched_word for i in items], ["zzz", "apple"])
        self.assertFalse(items[0].found)
        self.assertEqual(self.db.recent_words(), ["apple"])
        self.db.clear_history()
        self.assertEqual(self.db.recent_history(), [])

    def test_transaction_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.add_word("temp")
                raise RuntimeError("boom")
        self.assertEqual(self.db.count_words(), 0)

    def test_corrupted_database_raises_friendly_error(self):
        bad = self.dir / "bad.db"
        bad.write_bytes(b"this is definitely not a sqlite database" * 50)
        with self.assertRaises(DatabaseError):
            Database(bad).connect()

    def test_backup_creates_readable_copy(self):
        self.add_word("apple")
        target = self.dir / "backup.db"
        self.db.backup_to(target)
        self.assertEqual(sqlite3.connect(target).execute("SELECT COUNT(*) FROM words").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
