"""Search logic: exact lookup, suggestions, similar words, random and daily word.

This module knows nothing about the GUI, so it is easy to unit-test.
"""
from __future__ import annotations

import difflib
import logging
import zlib
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .database import Database, DatabaseError, WordEntry
from .utils import MAX_QUERY_LENGTH, clean_query, normalize_word

log = logging.getLogger(__name__)

# SearchResult.status values
OK, EMPTY, TOO_LONG, NOT_FOUND, ERROR = "ok", "empty", "too_long", "not_found", "error"


@dataclass
class SearchResult:
    status: str
    query: str
    entry: Optional[WordEntry] = None
    similar: list[str] = field(default_factory=list)
    message: str = ""


class SearchService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lookup(self, raw_text: str, record_history: bool = True) -> SearchResult:
        """Exact (case-insensitive) search. Never returns an unrelated definition."""
        query = clean_query(raw_text)
        if not query:
            return SearchResult(EMPTY, "", message="Type a word to search for.")
        if len(query) > MAX_QUERY_LENGTH:
            return SearchResult(TOO_LONG, "", message=f"That is too long to be a word "
                                f"(maximum {MAX_QUERY_LENGTH} characters).")
        try:
            entry = self.db.get_entry_by_word(query)
            similar = [] if entry else self.similar_words(query)
        except DatabaseError as exc:
            return SearchResult(ERROR, query, message=str(exc))
        if record_history:
            self._record(query, entry)
        if entry:
            return SearchResult(OK, query, entry=entry)
        return SearchResult(NOT_FOUND, query, similar=similar,
                            message=f"“{query}” was not found in your dictionary.")

    def _record(self, query: str, entry: Optional[WordEntry]) -> None:
        try:
            self.db.record_history(query, entry.id if entry else None, entry is not None)
        except DatabaseError:
            log.warning("Could not record search history")  # not worth interrupting the user

    def suggest(self, text: str, limit: int = 8) -> list[str]:
        """Autocomplete: words that start with what the user has typed so far."""
        query = clean_query(text)
        if not query or len(query) > MAX_QUERY_LENGTH:
            return []
        try:
            return self.db.prefix_words(query, limit)
        except DatabaseError:
            return []

    def similar_words(self, word: str, limit: int = 8) -> list[str]:
        """Prefix matches first, then spelling-similar words (difflib)."""
        normalized = normalize_word(word)
        results: list[str] = []
        for candidate in self.db.prefix_words(normalized, limit):
            if normalize_word(candidate) != normalized:
                results.append(candidate)
        if len(results) < limit and len(normalized) >= 2:
            # Typos rarely change the first letters, so only compare nearby words.
            head = normalized[:2] if len(normalized) >= 3 else normalized[:1]
            pairs = self.db.similar_candidates(head, max(1, len(normalized) - 3), len(normalized) + 3)
            lookup = {norm: display for display, norm in pairs}
            for norm in difflib.get_close_matches(normalized, list(lookup), n=limit, cutoff=0.6):
                if norm != normalized and lookup[norm] not in results:
                    results.append(lookup[norm])
        return results[:limit]

    def get_entry(self, word_id: int) -> Optional[WordEntry]:
        return self.db.get_entry(word_id)

    def random_entry(self) -> Optional[WordEntry]:
        word_id = self.db.random_word_id()
        return self.db.get_entry(word_id) if word_id is not None else None

    def word_of_the_day(self, today: Optional[date] = None) -> Optional[WordEntry]:
        """Same word all day, different tomorrow, chosen from the local database."""
        bounds = self.db.id_bounds()
        if bounds is None:
            return None
        low, high = bounds
        seed = zlib.crc32((today or date.today()).isoformat().encode())
        word_id = self.db.first_word_id_from(low + seed % (high - low + 1))
        return self.db.get_entry(word_id) if word_id is not None else None

    def toggle_favorite(self, word_id: int) -> bool:
        """Returns True if the word is now a favorite."""
        if self.db.is_favorite(word_id):
            self.db.remove_favorite(word_id)
            return False
        self.db.add_favorite(word_id)
        return True
