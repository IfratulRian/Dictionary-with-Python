# PyDictionary Offline

A modern, fully offline English dictionary for Windows, written in Python with CustomTkinter and SQLite.
Search words, read multiple definitions, save favorites, review your history and import your own
dictionary data. Core features never touch the network.

## Features

- Instant exact search (case-insensitive, whitespace-tolerant) with live autocomplete
- "Did you mean…" suggestions (prefix and spelling-similar words) when a word is not found
- Word details: phonetics, multiple definitions, examples, synonyms, antonyms, origin, source, optional local audio
- Favorites (searchable, no duplicates) and search history (paged, clearable) that persist between sessions
- Word of the Day and Random Word, chosen from the local database
- Import from **JSON, JSON Lines, CSV** and (best effort) **text-based PDF**, with progress bar and Cancel
- Dark / light / system theme, adjustable text size, database backup
- 27 bundled **sample** entries so it works immediately

## Screenshots

No screenshots are committed yet. To add them: run the app, take screenshots (Win + Shift + S), save them in
`assets/images/` (e.g. `home.png`, `word-details.png`) and add lines like
`![Home](assets/images/home.png)` here.

## Technology

Python 3.10+ · CustomTkinter (UI) · SQLite via `sqlite3` (storage) · PyMuPDF (PDF text) ·
`unittest` (tests) · PyInstaller (optional packaging)

## Project structure

```
PyDictionary/
├── main.py                  entry point
├── requirements.txt
├── app/
│   ├── ui.py                all windows and widgets (no SQL here)
│   ├── theme.py             colours and navigation items
│   ├── database.py          schema + every SQL query
│   ├── search.py            exact search, suggestions, similar words, random/daily word
│   ├── dictionary_importer.py   JSON / JSONL / CSV import
│   ├── pdf_importer.py      best-effort PDF import
│   ├── audio.py             local pronunciation playback
│   ├── settings.py          saved preferences
│   └── utils.py             paths, text and time helpers, logging
├── data/sample_dictionary.json   demo data (bundled)
├── scripts/import_dictionary.py  command-line importer
├── tests/                   automated tests (use temporary databases)
├── assets/                  icons / images (placeholders)
└── user_data/               YOUR database, settings and log (git-ignored)
```

## Install (Windows)

Internet is needed **only for this step** (downloading Python packages). Install Python 3.10+ from python.org
and tick "Add python.exe to PATH". Then, in the project folder:

```bat
python -m venv .venv
```

Activate it — Command Prompt:

```bat
.venv\Scripts\activate.bat
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell says scripts are disabled, either use Command Prompt, or skip activation and call the
virtual environment's Python directly (no admin rights needed):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Install dependencies and run:

```bat
python -m pip install -r requirements.txt
python main.py
```

On first launch the app creates `user_data\dictionary.db` and loads the sample words automatically.

## Offline use

After installing the packages and importing a dictionary, **no internet connection is needed**. The app has no
online lookups, analytics, telemetry or accounts, and the code only talks to local files. Internet is needed only
to (1) install Python packages and (2) download a dictionary dataset.

## Getting a real dictionary

The bundled data is a **demonstration** (27 original entries written for this project), not a full dictionary.
Obtain a complete dictionary legally, then import it. Candidates (check each project's current licence and
attribution rules yourself before using or redistributing):

| Source | Typical licence | Notes |
|---|---|---|
| Wiktionary extracts (e.g. via the Wiktextract project / kaikki.org) | CC BY-SA and GFDL | Very large JSONL. The importer has *best-effort* support for the Wiktextract record layout; it was only tested with a small synthetic record. Attribution to Wiktionary and share-alike apply. |
| Princeton WordNet | WordNet licence (permissive, notice required) | Glosses/synonyms, no pronunciations; needs converting to this app's JSON/CSV format. |
| Open English WordNet | CC BY 4.0 | Same idea; needs conversion. |

I have deliberately not hard-coded download links: use each project's official website. Do not import
copyrighted dictionaries (e.g. scanned commercial dictionaries) you are not licensed to copy.
If you redistribute a database built from CC BY-SA data, you must credit the source and share alike.

## Importing data

In the app: **Import Dictionary** → choose a file → confirm. Importing only *adds*; it never deletes or overwrites
existing words, favorites or history. If a word exists, only new definitions are merged in; identical definitions
are skipped. Large files can also be imported without the GUI:

```bat
python scripts\import_dictionary.py path\to\dictionary.json
```

### JSON

A list (or `{"entries": [...]}`) of entries. Only `word` and at least one `definition` are required:

```json
[
  {
    "word": "serene",
    "phonetic": "/səˈriːn/",
    "audio_path": "serene.wav",
    "source": "My dataset",
    "meanings": [
      {
        "part_of_speech": "adjective",
        "definition": "Calm, peaceful and untroubled.",
        "example": "The lake was serene at dawn.",
        "synonyms": ["calm", "tranquil"],
        "antonyms": "stormy, agitated",
        "etymology": "From Latin serenus."
      }
    ]
  }
]
```

Synonyms/antonyms may be a list or a comma-separated string. The whole file is read into memory, so use
**JSONL** (one JSON object per line) for very large datasets.

### CSV

UTF-8 with a header row. Required columns: `word`, `definition`. Optional: `part_of_speech`, `example`,
`synonyms`, `antonyms`, `etymology`, `phonetic`, `audio_path`, `source`. One row = one definition; repeat the word
on several rows for several definitions.

```csv
word,part_of_speech,definition,example
light,noun,The natural brightness that makes things visible.,Light streamed in.
light,adjective,Not heavy.,The bag was light.
```

### Pronunciation audio

Put `.wav` files in `user_data\audio\` and set `audio_path` to the file name (or use an absolute path). Playback
uses Python's built-in `winsound`, so only WAV is supported. Words without audio simply say so.

### PDF import: limitations

PDF import is a heuristic, not magic:

- Only PDFs with a **text layer** work. Scanned PDFs need OCR first (e.g. an external OCR tool); this app does not include OCR.
- Entries must look like `word n. definition`, `word /phonetic/ noun: definition` or `word (v.) definition`.
  Continuation lines are joined to the previous entry. Page numbers, headers and footers are skipped.
- Two-column pages are handled; other layouts (three columns, tables, sidebars) may be mis-parsed or skipped.
- Pages that yielded no text or no entries are listed in the import summary.

If a PDF does not parse well, convert it to JSON/CSV or use a structured dataset instead. Review a PDF import
afterwards: automatic parsing can produce wrong entries.

## Tests

```bat
python -m unittest -v
```

Tests use temporary databases and never touch your real data. (PDF tests are skipped if PyMuPDF is not installed.)

## Build a Windows executable (optional)

```bat
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name PyDictionaryOffline --collect-all customtkinter --add-data "data\sample_dictionary.json;data" main.py
```

The result is `dist\PyDictionaryOffline\PyDictionaryOffline.exe` (copy the whole folder). The packaged app keeps its
database, settings and log in `%LOCALAPPDATA%\PyDictionaryOffline` (never inside the install folder).
Packaging bundles only the app and the tiny sample file; a large dictionary should be imported by each user
(or shipped separately as a data file), not baked into the executable. If PDF import fails in the built exe, add
`--collect-all pymupdf` to the command. *The build command is provided but was not executed in the environment where
this project was written (no Windows machine) — please test it on yours.*

## Where is my data?

`user_data\dictionary.db` (from source) or `%LOCALAPPDATA%\PyDictionaryOffline\dictionary.db` (packaged exe).
Settings → "Open data folder" shows it. Settings → "Back up database…" saves a copy. Settings and log files sit next to it.
The log never records the words you search for.

## Troubleshooting

| Problem | Fix |
|---|---|
| `python` is not recognised | Reinstall Python with "Add to PATH", or use the `py` launcher: `py -m venv .venv` |
| PowerShell blocks `Activate.ps1` | Use Command Prompt, or call `.venv\Scripts\python.exe` directly |
| "CustomTkinter is not installed" | Activate the venv, then `python -m pip install -r requirements.txt` |
| "Database is damaged" at startup | Choose Yes: the damaged file is renamed (not deleted) and a fresh database is created |
| "Database is busy" | Close other copies of the app and retry |
| PDF import finds nothing | The PDF is probably scanned or uses an unsupported layout; use JSON/CSV |
| No sound | Only `.wav` is supported; check the file exists in `user_data\audio\` |

## Data attribution and licence

- Application code: MIT (see `LICENSE`).
- `data/sample_dictionary.json`: original definitions written for this project, MIT, demonstration only.
- Any dictionary you import keeps its own licence; follow its attribution requirements. The app shows each
  entry's `source` field so attribution travels with the data.
