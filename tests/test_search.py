import unittest
from datetime import date

from app.search import EMPTY, NOT_FOUND, OK, TOO_LONG, SearchService
from tests.helpers import TempDbTestCase


class SearchTests(TempDbTestCase):
    def setUp(self):
        super().setUp()
        for word in ("apple", "application", "apply", "banana"):
            self.add_word(word, f"Definition of {word}.")
        self.search = SearchService(self.db)

    def test_exact_search(self):
        result = self.search.lookup("apple")
        self.assertEqual(result.status, OK)
        self.assertEqual(result.entry.word, "apple")
        self.assertEqual(result.entry.meanings[0].definition, "Definition of apple.")

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(self.search.lookup("  AppLE  ").entry.word, "apple")
        self.assertEqual(self.search.lookup("\tBANANA\n").entry.word, "banana")

    def test_missing_word_gives_alternatives_not_wrong_definition(self):
        result = self.search.lookup("aple")
        self.assertEqual(result.status, NOT_FOUND)
        self.assertIsNone(result.entry)
        self.assertIn("apple", result.similar)

    def test_prefix_alternatives(self):
        result = self.search.lookup("appl")
        self.assertEqual(result.status, NOT_FOUND)
        self.assertTrue({"apple", "application", "apply"} <= set(result.similar))

    def test_empty_and_too_long_input(self):
        self.assertEqual(self.search.lookup("   ").status, EMPTY)
        self.assertEqual(self.search.lookup("x" * 500).status, TOO_LONG)
        self.assertEqual(self.db.recent_history(), [])  # invalid input is not recorded

    def test_sql_special_characters_are_safe(self):
        self.assertEqual(self.search.lookup("'; DROP TABLE words;--").status, NOT_FOUND)
        self.assertEqual(self.db.count_words(), 4)

    def test_suggestions(self):
        self.assertEqual(self.search.suggest("app"), ["apple", "application", "apply"])
        self.assertEqual(self.search.suggest("zz"), [])
        self.assertEqual(self.search.suggest(""), [])

    def test_history_is_recorded_by_lookup(self):
        self.search.lookup("apple")
        self.search.lookup("nothing")
        history = self.db.recent_history()
        self.assertEqual([(h.searched_word, h.found) for h in history], [("nothing", False), ("apple", True)])

    def test_toggle_favorite(self):
        word_id = self.search.lookup("apple").entry.id
        self.assertTrue(self.search.toggle_favorite(word_id))
        self.assertFalse(self.search.toggle_favorite(word_id))

    def test_word_of_the_day_is_stable_for_a_date(self):
        day = date(2026, 1, 15)
        self.assertEqual(self.search.word_of_the_day(day).id, self.search.word_of_the_day(day).id)

    def test_random_entry(self):
        self.assertIn(self.search.random_entry().word, {"apple", "application", "apply", "banana"})


class EmptyDictionaryTests(TempDbTestCase):
    def test_empty_dictionary(self):
        search = SearchService(self.db)
        self.assertEqual(search.lookup("anything").status, NOT_FOUND)
        self.assertEqual(search.lookup("anything").similar, [])
        self.assertIsNone(search.random_entry())
        self.assertIsNone(search.word_of_the_day())
        self.assertEqual(search.suggest("a"), [])


if __name__ == "__main__":
    unittest.main()
