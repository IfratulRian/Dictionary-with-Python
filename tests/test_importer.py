import threading
import unittest

from app.dictionary_importer import import_file, normalize_entry
from app.pdf_importer import import_pdf, parse_dictionary_lines
from app.search import SearchService
from app.utils import resource_path
from tests.helpers import TempDbTestCase

GOOD = [
    {"word": "Alpha", "phonetic": "/a/", "meanings": [
        {"part_of_speech": "noun", "definition": "The first letter.", "synonyms": ["first", "start"]},
        {"part_of_speech": "adjective", "definition": "Coming first."}]},
    {"word": "beta", "meanings": [{"definition": "The second letter."}]},
]


class JsonImportTests(TempDbTestCase):
    def test_valid_json(self):
        summary = import_file(self.db, self.write_json("d.json", GOOD))
        self.assertFalse(summary.failed)
        self.assertEqual((summary.words_added, summary.meanings_added), (2, 3))
        entry = SearchService(self.db).lookup("alpha").entry
        self.assertEqual(len(entry.meanings), 2)
        self.assertEqual(entry.meanings[0].synonyms, "first, start")

    def test_malformed_json_is_reported_and_db_untouched(self):
        self.add_word("keep")
        summary = import_file(self.db, self.write("bad.json", '{"word": "x", '))
        self.assertTrue(summary.failed)
        self.assertIn("Invalid JSON", summary.errors[0])
        self.assertEqual(self.db.count_words(), 1)

    def test_wrong_json_shape(self):
        self.assertTrue(import_file(self.db, self.write("n.json", '"just a string"')).failed)

    def test_invalid_entries_are_rejected_not_silently_dropped(self):
        data = GOOD + [{"meanings": []}, {"word": "nodef"}, "garbage"]
        summary = import_file(self.db, self.write_json("m.json", data))
        self.assertEqual(summary.words_added, 2)
        self.assertEqual(summary.rejected, 3)
        self.assertEqual(len(summary.errors), 3)

    def test_duplicate_entries(self):
        path = self.write_json("d.json", GOOD)
        import_file(self.db, path)
        again = import_file(self.db, path)
        self.assertEqual((again.words_added, again.meanings_added), (0, 0))
        self.assertEqual(again.duplicates_skipped, 3)
        self.assertEqual(self.db.count_words(), 2)
        # A new definition for an existing word is merged in.
        extra = self.write_json("e.json", [{"word": "ALPHA", "meanings": [{"definition": "A brand."}]}])
        merged = import_file(self.db, extra)
        self.assertEqual((merged.words_merged, merged.meanings_added), (1, 1))
        self.assertEqual(len(SearchService(self.db).lookup("alpha").entry.meanings), 3)

    def test_empty_list_imports_nothing(self):
        summary = import_file(self.db, self.write("e.json", "[]"))
        self.assertEqual(summary.total, 0)
        self.assertEqual(self.db.count_words(), 0)

    def test_missing_and_unsupported_files(self):
        from app.dictionary_importer import ImportFormatError
        with self.assertRaises(ImportFormatError):
            import_file(self.db, self.dir / "nope.json")
        with self.assertRaises(ImportFormatError):
            import_file(self.db, self.write("x.txt", "hi"))

    def test_cancel_stops_import(self):
        event = threading.Event()
        event.set()
        summary = import_file(self.db, self.write_json("d.json", GOOD), cancel_event=event)
        self.assertTrue(summary.cancelled)
        self.assertEqual(self.db.count_words(), 0)

    def test_bundled_sample_dictionary(self):
        summary = import_file(self.db, resource_path("data", "sample_dictionary.json"))
        self.assertFalse(summary.failed)
        self.assertEqual(summary.rejected, 0)
        self.assertGreaterEqual(self.db.count_words(), 20)
        self.assertGreater(len(SearchService(self.db).lookup("run").entry.meanings), 1)


class CsvAndJsonlTests(TempDbTestCase):
    def test_csv_rows_merge_into_one_word(self):
        csv_text = ("word,part_of_speech,definition,example\n"
                    "light,noun,Brightness.,Let there be light.\n"
                    "light,adjective,Not heavy.,\n"
                    "dark,adjective,Without light.,\n")
        summary = import_file(self.db, self.write("d.csv", csv_text))
        self.assertEqual((summary.words_added, summary.meanings_added), (2, 3))
        self.assertEqual(len(SearchService(self.db).lookup("light").entry.meanings), 2)

    def test_csv_without_required_columns(self):
        summary = import_file(self.db, self.write("b.csv", "name,value\na,b\n"))
        self.assertTrue(summary.failed)

    def test_binary_garbage_csv(self):
        path = self.dir / "g.csv"
        path.write_bytes(b"\xff\xfe\x00\x01 not text \x80\x81")
        self.assertTrue(import_file(self.db, path).failed)

    def test_jsonl_reports_bad_lines(self):
        text = '{"word":"a","definition":"One."}\nnot json\n{"word":"b","definition":"Two."}\n'
        summary = import_file(self.db, self.write("d.jsonl", text))
        self.assertEqual((summary.words_added, summary.rejected), (2, 1))

    def test_wiktextract_style_record(self):
        record, error = normalize_entry({
            "word": "tree", "pos": "noun", "etymology_text": "From Old English.",
            "sounds": [{"ipa": "/triː/"}],
            "senses": [{"glosses": ["A woody plant."], "examples": [{"text": "An oak tree."}]}]})
        self.assertEqual(error, "")
        self.assertEqual(record["phonetic"], "/triː/")
        self.assertEqual(record["meanings"][0]["definition"], "A woody plant.")


class PdfTests(TempDbTestCase):
    def test_parse_lines(self):
        entries = parse_dictionary_lines([
            "42", "apple n. A round fruit that grows", "on trees.",
            "run (v.) To move quickly on foot.", "banana /bəˈnɑːnə/ noun: A long yellow fruit."])
        self.assertEqual([e["word"] for e in entries], ["apple", "run", "banana"])
        self.assertEqual(entries[0]["meanings"][0]["definition"], "A round fruit that grows on trees.")
        self.assertEqual(entries[1]["meanings"][0]["part_of_speech"], "verb")
        self.assertEqual(entries[2]["phonetic"], "bəˈnɑːnə")

    def test_text_that_is_not_a_dictionary_yields_nothing(self):
        self.assertEqual(parse_dictionary_lines(["Chapter One", "It was a dark night."]), [])

    def _make_pdf(self, pages):
        import pymupdf as fitz
        doc = fitz.open()
        for lines in pages:
            page = doc.new_page()
            y = 100
            for line in lines:
                page.insert_text((72, y), line, fontsize=11)
                y += 18
        path = self.dir / "dict.pdf"
        doc.save(path)
        doc.close()
        return path

    def test_real_pdf_import(self):
        try:
            import pymupdf as fitz  # noqa: F401
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        path = self._make_pdf([["apple n. A round fruit.", "banana n. A long yellow fruit."], [], ["Just prose here."]])
        summary = import_pdf(self.db, path)
        self.assertFalse(summary.failed)
        self.assertEqual(summary.words_added, 2)
        self.assertTrue(any("no extractable text" in n for n in summary.notes))
        self.assertTrue(any("no recognisable entries" in n for n in summary.notes))

    def test_pdf_with_no_entries_fails_with_advice(self):
        try:
            import pymupdf as fitz  # noqa: F401
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        summary = import_pdf(self.db, self._make_pdf([["Only ordinary sentences here."]]))
        self.assertTrue(summary.failed)
        self.assertIn("JSON or CSV", summary.errors[0])

    def test_invalid_pdf(self):
        try:
            import pymupdf as fitz  # noqa: F401
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        from app.dictionary_importer import ImportFormatError
        bad = self.dir / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with self.assertRaises(ImportFormatError):
            import_pdf(self.db, bad)


if __name__ == "__main__":
    unittest.main()
