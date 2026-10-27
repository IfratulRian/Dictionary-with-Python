"""CustomTkinter user interface.

This file only builds widgets and reacts to clicks. All database work is done by
`Database` and `SearchService`, and long imports run in a background thread.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from . import theme
from .audio import play_audio, resolve_audio_path
from .database import Database, DatabaseError, WordEntry
from .dictionary_importer import ImportFormatError, import_file
from .pdf_importer import import_pdf
from .search import ERROR, NOT_FOUND, OK, SearchResult, SearchService
from .settings import MAX_FONT, MIN_FONT, Settings
from .utils import APP_NAME, APP_VERSION, format_timestamp, resource_path, shorten

log = logging.getLogger(__name__)
HISTORY_PAGE_SIZE = 50


def wrapping_label(parent, pad: int = 48, **kwargs) -> ctk.CTkLabel:
    """A label whose text re-wraps when its parent card is resized."""
    label = ctk.CTkLabel(parent, justify="left", anchor="w", **kwargs)
    state = {"width": 0}

    def on_resize(event) -> None:
        if abs(event.width - state["width"]) > 4:
            state["width"] = event.width
            label.configure(wraplength=max(200, event.width - pad))

    parent.bind("<Configure>", on_resize, add="+")
    return label


class SearchBox(ctk.CTkFrame):
    """Search entry + button with an inline autocomplete list."""

    def __init__(self, parent, app: "App", big: bool = False) -> None:
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._job: Optional[str] = None
        height = 48 if big else 38
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.entry = ctk.CTkEntry(row, placeholder_text="Search for a word…", height=height,
                                  corner_radius=12, font=app.font(2 if big else 0))
        self.entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Search", width=110 if big else 90, height=height, corner_radius=12,
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT,
                      font=app.font(0, "bold"), command=self.submit).pack(side="left", padx=(10, 0))
        self.suggestions = ctk.CTkFrame(self, corner_radius=12, fg_color=theme.CARD)
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<Escape>", lambda _e: self.hide_suggestions())
        self.entry.bind("<KeyRelease>", self._on_key)

    def set_text(self, text: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.hide_suggestions()

    def hide_suggestions(self) -> None:
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        self.suggestions.pack_forget()

    def submit(self) -> None:
        text = self.entry.get()
        self.hide_suggestions()
        self.app.run_search(text)

    def _on_key(self, event) -> None:
        if event.keysym in ("Return", "Escape", "Tab"):
            return
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(150, self._show_suggestions)  # wait for a pause in typing

    def _show_suggestions(self) -> None:
        self._job = None
        words = self.app.search.suggest(self.entry.get())
        for child in self.suggestions.winfo_children():
            child.destroy()
        if not words:
            self.suggestions.pack_forget()
            return
        for word in words:
            ctk.CTkButton(self.suggestions, text=word, anchor="w", height=32, corner_radius=8,
                          fg_color="transparent", hover_color=theme.CARD_ALT, text_color=theme.TEXT,
                          command=lambda w=word: self.pick(w)).pack(fill="x", padx=6, pady=2)
        self.suggestions.pack(fill="x", pady=(6, 0))

    def pick(self, word: str) -> None:
        self.set_text(word)
        self.app.run_search(word)


class App(ctk.CTk):
    def __init__(self, db: Database, search: SearchService, settings: Settings) -> None:
        super().__init__()
        self.db, self.search, self.settings = db, search, settings
        self._fonts: dict[tuple, ctk.CTkFont] = {}
        self.current_page = ""
        self.current_result: Optional[SearchResult] = None
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.import_widgets: dict = {}
        self.import_state = {"running": False, "cancel": threading.Event(), "queue": queue.Queue(),
                             "progress": 0.0, "indeterminate": False,
                             "status": "No import has been run in this session.", "report": ""}

        ctk.set_appearance_mode(settings.get("theme"))
        self.title(APP_NAME)
        self.geometry("1120x740")
        self.minsize(940, 620)
        self.configure(fg_color=theme.BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self.show_page("home")

    # ------------------------------------------------------------ small helpers
    def font(self, delta: int = 0, weight: str = "normal", italic: bool = False) -> ctk.CTkFont:
        size = self.settings.get("font_size") + delta
        key = (size, weight, italic)
        if key not in self._fonts:
            self._fonts[key] = ctk.CTkFont(size=size, weight=weight, slant="italic" if italic else "roman")
        return self._fonts[key]

    def label(self, parent, text: str, delta: int = 0, weight: str = "normal", muted: bool = False,
              italic: bool = False, wrap: bool = True, color=None, **kwargs) -> ctk.CTkLabel:
        options = dict(text=text, font=self.font(delta, weight, italic),
                       text_color=color or (theme.MUTED if muted else theme.TEXT), **kwargs)
        return wrapping_label(parent, **options) if wrap else ctk.CTkLabel(parent, anchor="w", **options)

    def card(self, parent) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, corner_radius=16, fg_color=theme.CARD)

    def button(self, parent, text: str, command: Callable, primary: bool = True, danger: bool = False,
               width: int = 0, height: int = 36) -> ctk.CTkButton:
        if danger:
            colors = dict(fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, text_color=theme.ON_ACCENT)
        elif primary:
            colors = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT)
        else:
            colors = dict(fg_color=theme.CARD_ALT, hover_color=theme.ACCENT, text_color=theme.TEXT)
        kwargs = {"width": width} if width else {}
        return ctk.CTkButton(parent, text=text, command=command, height=height, corner_radius=10,
                             font=self.font(0, "bold"), **colors, **kwargs)

    def chip(self, parent, text: str, command: Callable) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=text, command=command, height=30, corner_radius=15,
                             fg_color=theme.CARD_ALT, hover_color=theme.ACCENT, text_color=theme.TEXT,
                             font=self.font(-1))

    def empty_state(self, parent, title: str, message: str, button_text: str = "",
                    command: Optional[Callable] = None) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(expand=True, fill="x", pady=40, padx=20)
        self.label(box, title, delta=6, weight="bold", wrap=False).pack(anchor="center")
        ctk.CTkLabel(box, text=message, font=self.font(), text_color=theme.MUTED, wraplength=520,
                     justify="center").pack(pady=(8, 16))
        if button_text and command:
            self.button(box, button_text, command, width=200).pack()

    def guard(self, func: Callable, *args, default=None):
        """Call a database function; show a friendly message instead of crashing."""
        try:
            return func(*args)
        except DatabaseError as exc:
            messagebox.showerror("Database problem", str(exc))
            return default

    # ------------------------------------------------------------ layout
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=theme.SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.pack_propagate(False)

        logo = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo.pack(fill="x", padx=18, pady=(22, 18))
        badge = ctk.CTkFrame(logo, width=42, height=42, corner_radius=12, fg_color=theme.ACCENT)
        badge.pack(side="left")
        badge.pack_propagate(False)
        ctk.CTkLabel(badge, text="Aa", font=ctk.CTkFont(size=17, weight="bold"),
                     text_color=theme.ON_ACCENT).pack(expand=True)
        names = ctk.CTkFrame(logo, fg_color="transparent")
        names.pack(side="left", padx=10)
        ctk.CTkLabel(names, text="PyDictionary", font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=theme.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text="Offline", font=ctk.CTkFont(size=12), text_color=theme.MUTED,
                     anchor="w").pack(anchor="w")

        for key, label, icon in theme.NAV_ITEMS:
            button = ctk.CTkButton(sidebar, text=f"  {icon}   {label}", anchor="w", height=42,
                                   corner_radius=10, fg_color="transparent", hover_color=theme.CARD_ALT,
                                   text_color=theme.TEXT, font=ctk.CTkFont(size=14),
                                   command=lambda k=key: self.show_page(k))
            button.pack(fill="x", padx=12, pady=2)
            self.nav_buttons[key] = button

        status = ctk.CTkFrame(sidebar, corner_radius=12, fg_color=theme.CARD)
        status.pack(side="bottom", fill="x", padx=12, pady=16)
        ctk.CTkLabel(status, text="●  Offline mode", text_color=theme.SUCCESS,
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(status, text="No network is used.\nYour data stays on this PC.", justify="left",
                     text_color=theme.MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(0, 10))

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(18, 18), pady=14)
        self.header = ctk.CTkFrame(main, fg_color="transparent")
        self.header.pack(fill="x", pady=(0, 10))
        self.header_title = ctk.CTkLabel(self.header, text="", font=ctk.CTkFont(size=22, weight="bold"),
                                         text_color=theme.TEXT)
        self.header_title.pack(side="left", anchor="n", pady=(4, 0))
        self.header_search = SearchBox(self.header, self)
        self.content = ctk.CTkFrame(main, fg_color="transparent")
        self.content.pack(fill="both", expand=True)

    def show_page(self, name: str) -> None:
        self.current_page = name
        self.import_widgets = {}
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(fg_color=theme.ACCENT if active else "transparent",
                             text_color=theme.ON_ACCENT if active else theme.TEXT)
        self.header_title.configure(text=theme.PAGE_TITLES[name])
        self.header_search.pack_forget()
        if name != "home":  # the home screen has its own large search bar
            self.header_search.pack(side="right", fill="x", expand=True, padx=(24, 0))
        for child in self.content.winfo_children():
            child.destroy()
        try:
            getattr(self, f"page_{name}")()
        except Exception:
            log.exception("Failed to build page %s", name)
            self.empty_state(self.content, "Something went wrong",
                             "This screen could not be displayed. Details were saved to the log file.")

    # ------------------------------------------------------------ search flow
    def run_search(self, text: str) -> None:
        result = self.search.lookup(text)
        self.current_result = result
        self.header_search.set_text(result.query)
        self.show_page("search")

    def show_entry(self, entry: Optional[WordEntry]) -> None:
        if entry is None:
            messagebox.showinfo("Not available", "That word is no longer in the dictionary.")
            return
        self.current_result = SearchResult(OK, entry.word, entry=entry)
        self.header_search.set_text(entry.word)
        self.show_page("search")

    def open_word(self, word_id: int) -> None:
        self.show_entry(self.guard(self.search.get_entry, word_id))

    # ------------------------------------------------------------ HOME
    def page_home(self) -> None:
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        page.pack(fill="both", expand=True)
        page.grid_columnconfigure((0, 1), weight=1, uniform="home")
        total = self.guard(self.db.count_words, default=0)

        hero = self.card(page)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 12))
        self.label(hero, "Welcome to PyDictionary Offline", delta=10, weight="bold").pack(anchor="w", padx=24, pady=(22, 2))
        self.label(hero, "Look up English words instantly from a dictionary stored on your own computer.",
                   muted=True).pack(anchor="w", padx=24)
        SearchBox(hero, self, big=True).pack(fill="x", padx=24, pady=(16, 8))
        self.label(hero, f"●  Works fully offline — {total:,} words available",
                   color=theme.SUCCESS, weight="bold", delta=-1, wrap=False).pack(anchor="w", padx=24, pady=(0, 18))

        if total == 0:
            empty = self.card(page)
            empty.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))
            self.label(empty, "Your dictionary is empty", delta=4, weight="bold").pack(anchor="w", padx=24, pady=(18, 2))
            self.label(empty, "Import a dictionary file (JSON, CSV or PDF) or reload the built-in sample words "
                       "to start searching.", muted=True).pack(anchor="w", padx=24)
            self.button(empty, "Import dictionary data", lambda: self.show_page("import"),
                        width=220).pack(anchor="w", padx=24, pady=(12, 18))
            return

        quick = ctk.CTkFrame(page, fg_color="transparent")
        quick.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))
        for text, target in (("★  Favorites", "favorites"), ("◷  History", "history"), ("⚄  Random word", "random")):
            self.button(quick, text, lambda t=target: self.show_page(t), primary=False,
                        width=170, height=40).pack(side="left", padx=(0, 10))

        wotd_card = self.card(page)
        wotd_card.grid(row=2, column=0, sticky="nsew", padx=(4, 6), pady=(0, 12))
        self.label(wotd_card, "WORD OF THE DAY", delta=-2, weight="bold", color=theme.ACCENT_TEXT,
                   wrap=False).pack(anchor="w", padx=20, pady=(16, 4))
        entry = self.guard(self.search.word_of_the_day)
        if entry:
            self.label(wotd_card, entry.word, delta=10, weight="bold").pack(anchor="w", padx=20)
            if entry.phonetic:
                self.label(wotd_card, entry.phonetic, muted=True, wrap=False).pack(anchor="w", padx=20)
            first = entry.meanings[0]
            pos = f"({first.part_of_speech}) " if first.part_of_speech else ""
            self.label(wotd_card, pos + shorten(first.definition, 180)).pack(anchor="w", padx=20, pady=(6, 8))
            self.button(wotd_card, "View full entry", lambda e=entry: self.show_entry(e),
                        width=150).pack(anchor="w", padx=20, pady=(0, 18))

        recent_card = self.card(page)
        recent_card.grid(row=2, column=1, sticky="nsew", padx=(6, 4), pady=(0, 12))
        self.label(recent_card, "RECENT SEARCHES", delta=-2, weight="bold", color=theme.ACCENT_TEXT,
                   wrap=False).pack(anchor="w", padx=20, pady=(16, 8))
        recent = self.guard(self.db.recent_words, 6, default=[])
        if recent:
            for word in recent:
                self.chip(recent_card, word, lambda w=word: self.run_search(w)).pack(anchor="w", padx=20, pady=3)
            ctk.CTkFrame(recent_card, height=10, fg_color="transparent").pack()
        else:
            self.label(recent_card, "Words you search for will appear here.", muted=True).pack(
                anchor="w", padx=20, pady=(0, 18))

        fav_card = self.card(page)
        fav_card.grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 12))
        self.label(fav_card, "FAVORITE WORDS", delta=-2, weight="bold", color=theme.ACCENT_TEXT,
                   wrap=False).pack(anchor="w", padx=20, pady=(16, 8))
        favorites = self.guard(self.db.list_favorites, "", 6, default=[])
        if favorites:
            row = ctk.CTkFrame(fav_card, fg_color="transparent")
            row.pack(anchor="w", padx=20, pady=(0, 18))
            for fav in favorites:
                self.chip(row, fav.word, lambda i=fav.word_id: self.open_word(i)).pack(side="left", padx=(0, 8))
        else:
            self.label(fav_card, "Tap the ☆ on any word to save it here.", muted=True).pack(
                anchor="w", padx=20, pady=(0, 18))

    # ------------------------------------------------------------ SEARCH / DETAILS
    def page_search(self) -> None:
        result = self.current_result
        if result is None:
            self.empty_state(self.content, "Search the dictionary",
                             "Type a word in the search bar above and press Enter.")
        elif result.status == OK and result.entry:
            self.build_detail(self.content, result.entry)
        elif result.status == NOT_FOUND:
            self._build_not_found(result)
        else:
            self.empty_state(self.content, "Can’t search that" if result.status != ERROR else "Search failed",
                             result.message)

    def _build_not_found(self, result: SearchResult) -> None:
        card = self.card(self.content)
        card.pack(fill="x", padx=4, pady=4)
        self.label(card, f"“{result.query}” was not found", delta=8, weight="bold").pack(anchor="w", padx=24, pady=(22, 4))
        self.label(card, "It is not in your local dictionary. It may be misspelled, or your dictionary data may not "
                   "include it.", muted=True).pack(anchor="w", padx=24)
        if result.similar:
            self.label(card, "Did you mean:", weight="bold").pack(anchor="w", padx=24, pady=(16, 6))
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(anchor="w", padx=24)
            for word in result.similar:
                self.chip(row, word, lambda w=word: self.run_search(w)).pack(side="left", padx=(0, 8), pady=2)
        else:
            self.label(card, "No similar words were found.", muted=True).pack(anchor="w", padx=24, pady=(14, 0))
        self.button(card, "Import more dictionary data", lambda: self.show_page("import"), primary=False,
                    width=240).pack(anchor="w", padx=24, pady=(18, 22))

    def build_detail(self, parent, entry: WordEntry, extra: Optional[Callable] = None) -> None:
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        head = self.card(scroll)
        head.pack(fill="x", padx=4, pady=(4, 12))
        top = ctk.CTkFrame(head, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(20, 0))
        self.label(top, entry.word, delta=18, weight="bold", wrap=False).pack(side="left")
        fav_button = ctk.CTkButton(top, width=170, height=36, corner_radius=10, font=self.font(0, "bold"))
        fav_button.pack(side="right")
        self._style_favorite(fav_button, bool(self.guard(self.db.is_favorite, entry.id, default=False)))
        fav_button.configure(command=lambda: self._toggle_favorite(entry, fav_button))
        if extra:
            extra(top)
        if entry.phonetic:
            self.label(head, entry.phonetic, delta=2, muted=True, wrap=False).pack(anchor="w", padx=24, pady=(2, 0))

        audio_row = ctk.CTkFrame(head, fg_color="transparent")
        audio_row.pack(fill="x", padx=24, pady=(10, 20))
        audio_file = resolve_audio_path(entry.audio_path)
        if audio_file:
            self.button(audio_row, "🔊  Play pronunciation", lambda: self._play(audio_file),
                        primary=False, width=200).pack(side="left")
        elif entry.audio_path:
            self.label(audio_row, f"Audio file not found: {Path(entry.audio_path).name}", muted=True,
                       wrap=False).pack(side="left")
        else:
            self.label(audio_row, "No pronunciation audio is stored for this word.", muted=True,
                       delta=-1, wrap=False).pack(side="left")

        for number, meaning in enumerate(entry.meanings, 1):
            card = self.card(scroll)
            card.pack(fill="x", padx=4, pady=6)
            heading = f"{number}.  {meaning.part_of_speech}" if meaning.part_of_speech else f"{number}."
            self.label(card, heading, weight="bold", italic=True, color=theme.ACCENT_TEXT,
                       wrap=False).pack(anchor="w", padx=22, pady=(14, 2))
            self.label(card, meaning.definition, delta=2).pack(anchor="w", padx=22, fill="x")
            if meaning.example:
                self.label(card, f"“{meaning.example}”", italic=True, muted=True).pack(
                    anchor="w", padx=22, pady=(6, 0), fill="x")
            for title, value in (("Synonyms", meaning.synonyms), ("Antonyms", meaning.antonyms),
                                 ("Origin", meaning.etymology)):
                if value:
                    self.label(card, f"{title}: {value}", delta=-1).pack(anchor="w", padx=22, pady=(6, 0), fill="x")
            ctk.CTkFrame(card, height=12, fg_color="transparent").pack()

        if entry.source:
            self.label(scroll, f"Source: {entry.source}", delta=-2, muted=True).pack(
                anchor="w", padx=8, pady=(8, 16), fill="x")

    def _style_favorite(self, button: ctk.CTkButton, is_favorite: bool) -> None:
        if is_favorite:
            button.configure(text="★  In favorites", fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                             text_color=theme.ON_ACCENT)
        else:
            button.configure(text="☆  Add to favorites", fg_color=theme.CARD_ALT, hover_color=theme.ACCENT,
                             text_color=theme.TEXT)

    def _toggle_favorite(self, entry: WordEntry, button: ctk.CTkButton) -> None:
        now_favorite = self.guard(self.search.toggle_favorite, entry.id)
        if now_favorite is not None:
            self._style_favorite(button, now_favorite)

    def _play(self, path: Path) -> None:
        ok, message = play_audio(path)
        if not ok:
            messagebox.showinfo("Pronunciation", message)

    # ------------------------------------------------------------ FAVORITES
    def page_favorites(self) -> None:
        filter_entry = ctk.CTkEntry(self.content, placeholder_text="Search within your favorites…",
                                    height=38, corner_radius=12, font=self.font())
        filter_entry.pack(fill="x", padx=4, pady=(0, 10))
        holder = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        holder.pack(fill="both", expand=True)
        filter_entry.bind("<KeyRelease>", lambda _e: self._render_favorites(holder, filter_entry.get()))
        self._render_favorites(holder, "")

    def _render_favorites(self, holder, text_filter: str) -> None:
        for child in holder.winfo_children():
            child.destroy()
        items = self.guard(self.db.list_favorites, text_filter, 500, default=[])
        if not items:
            if text_filter.strip():
                self.empty_state(holder, "No matching favorites", "Try a different search.")
            else:
                self.empty_state(holder, "No favorites yet",
                                 "Open any word and click “Add to favorites” to keep it here.")
            return
        for item in items:
            row = self.card(holder)
            row.pack(fill="x", padx=4, pady=5)
            row.grid_columnconfigure(0, weight=1)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
            ctk.CTkButton(text, text=item.word, anchor="w", fg_color="transparent", hover_color=theme.CARD_ALT,
                          text_color=theme.ACCENT_TEXT, font=self.font(3, "bold"), height=30,
                          command=lambda i=item.word_id: self.open_word(i)).pack(anchor="w")
            if item.preview:
                self.label(text, shorten(item.preview, 140), muted=True, delta=-1, wrap=False).pack(anchor="w", padx=10)
            self.button(row, "Remove", lambda i=item.word_id: self._remove_favorite(holder, i, text_filter),
                        primary=False, width=90).grid(row=0, column=1, padx=14)

    def _remove_favorite(self, holder, word_id: int, text_filter: str) -> None:
        self.guard(self.db.remove_favorite, word_id)
        self._render_favorites(holder, text_filter)

    # ------------------------------------------------------------ HISTORY
    def page_history(self) -> None:
        bar = ctk.CTkFrame(self.content, fg_color="transparent")
        bar.pack(fill="x", padx=4, pady=(0, 10))
        self.label(bar, "Click a word to search for it again.", muted=True, wrap=False).pack(side="left")
        self.button(bar, "Clear history", self._clear_history, danger=True, width=130).pack(side="right")
        holder = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        holder.pack(fill="both", expand=True)
        self._load_history(holder, 0)

    def _load_history(self, holder, offset: int) -> None:
        items = self.guard(self.db.recent_history, HISTORY_PAGE_SIZE, offset, default=[])
        if offset == 0 and not items:
            self.empty_state(holder, "No search history", "Words you search for will be listed here.")
            return
        for item in items:
            row = self.card(holder)
            row.pack(fill="x", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkButton(row, text=item.searched_word, anchor="w", fg_color="transparent",
                          hover_color=theme.CARD_ALT, text_color=theme.TEXT, font=self.font(1, "bold"), height=34,
                          command=lambda w=item.searched_word: self.run_search(w)).grid(
                row=0, column=0, sticky="w", padx=10, pady=6)
            self.label(row, "Found" if item.found else "Not found", delta=-1, wrap=False,
                       color=theme.SUCCESS if item.found else theme.DANGER).grid(row=0, column=1, padx=10)
            self.label(row, format_timestamp(item.searched_at), delta=-1, muted=True, wrap=False).grid(
                row=0, column=2, padx=(0, 14))
        if len(items) == HISTORY_PAGE_SIZE:
            more = self.button(holder, "Load more", lambda: None, primary=False, width=140)
            more.configure(command=lambda: (more.destroy(), self._load_history(holder, offset + HISTORY_PAGE_SIZE)))
            more.pack(pady=12)

    def _clear_history(self) -> None:
        if messagebox.askyesno("Clear search history", "Delete your entire search history?\n"
                               "Favorites and dictionary data are not affected."):
            self.guard(self.db.clear_history)
            self.show_page("history")

    # ------------------------------------------------------------ RANDOM
    def page_random(self) -> None:
        entry = self.guard(self.search.random_entry)
        if entry is None:
            self.empty_state(self.content, "The dictionary is empty",
                             "Import dictionary data to use the random word feature.",
                             "Import dictionary data", lambda: self.show_page("import"))
            return

        def another_button(top) -> None:
            self.button(top, "⚄  Another", lambda: self.show_page("random"), primary=False,
                        width=110).pack(side="right", padx=(0, 10))

        self.build_detail(self.content, entry, extra=another_button)

    # ------------------------------------------------------------ IMPORT
    def page_import(self) -> None:
        state = self.import_state
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        page.pack(fill="both", expand=True)
        intro = self.card(page)
        intro.pack(fill="x", padx=4, pady=(4, 12))
        self.label(intro, "Add words to your dictionary", delta=6, weight="bold").pack(anchor="w", padx=22, pady=(18, 4))
        self.label(intro, "Importing only adds data: existing words, favorites and history are never deleted or "
                   "overwritten. Duplicate definitions are skipped.\n\n"
                   "• JSON / JSONL / CSV – recommended, reliable.\n"
                   "• PDF – best effort; works only for text-based PDFs with entries like “word n. definition”. "
                   "If a PDF does not parse well, use a JSON or CSV dictionary instead.",
                   muted=True).pack(anchor="w", padx=22, fill="x")
        buttons = ctk.CTkFrame(intro, fg_color="transparent")
        buttons.pack(anchor="w", padx=22, pady=(14, 20))
        actions = [self.button(buttons, "Import JSON / CSV…", lambda: self._start_import("structured"), width=190),
                   self.button(buttons, "Import PDF…", lambda: self._start_import("pdf"), primary=False, width=150),
                   self.button(buttons, "Load sample words", lambda: self._start_import("sample"),
                               primary=False, width=170)]
        for button in actions:
            button.pack(side="left", padx=(0, 10))

        progress_card = self.card(page)
        progress_card.pack(fill="x", padx=4, pady=(0, 12))
        status = self.label(progress_card, state["status"])
        status.pack(anchor="w", padx=22, pady=(16, 8), fill="x")
        bar = ctk.CTkProgressBar(progress_card, height=12, progress_color=theme.ACCENT)
        bar.set(0)
        bar.pack(fill="x", padx=22)
        cancel = self.button(progress_card, "Cancel import", self._cancel_import, danger=True, width=140)
        cancel.pack(anchor="w", padx=22, pady=(12, 16))
        report = ctk.CTkTextbox(page, height=240, font=ctk.CTkFont(family="Consolas", size=12), wrap="word",
                                fg_color=theme.CARD, text_color=theme.TEXT, corner_radius=12)
        report.pack(fill="x", padx=4, pady=(0, 12))
        self.import_widgets = {"status": status, "bar": bar, "cancel": cancel, "report": report,
                               "actions": actions}
        self._refresh_import_widgets()
        if state["running"]:
            self._poll_import()

    def _refresh_import_widgets(self) -> None:
        w, state = self.import_widgets, self.import_state
        if not w or not w["status"].winfo_exists():
            return
        w["status"].configure(text=state["status"])
        mode = "indeterminate" if state["running"] and state["indeterminate"] else "determinate"
        if w["bar"].cget("mode") != mode:
            w["bar"].stop()
            w["bar"].configure(mode=mode)
            if mode == "indeterminate":
                w["bar"].start()
        if mode == "determinate":
            w["bar"].set(state["progress"])
        for button in w["actions"]:
            button.configure(state="disabled" if state["running"] else "normal")
        w["cancel"].configure(state="normal" if state["running"] else "disabled")
        w["report"].configure(state="normal")
        w["report"].delete("1.0", "end")
        w["report"].insert("end", state["report"] or "The import report will appear here.")
        w["report"].configure(state="disabled")

    def _start_import(self, kind: str) -> None:
        state = self.import_state
        if state["running"]:
            return
        if kind == "sample":
            path = resource_path("data", "sample_dictionary.json")
        elif kind == "pdf":
            path = filedialog.askopenfilename(title="Choose a PDF dictionary", filetypes=[("PDF files", "*.pdf")])
        else:
            path = filedialog.askopenfilename(title="Choose a dictionary file",
                                              filetypes=[("Dictionary data", "*.json *.jsonl *.csv"), ("All files", "*.*")])
        if not path:
            return
        name = Path(path).name
        if not messagebox.askyesno("Confirm import",
                                   f"Add entries from “{name}” to your dictionary?\n\nExisting words and your "
                                   "favorites/history are kept. Nothing is overwritten."):
            return
        state.update(running=True, progress=0.0, indeterminate=True, report="", status=f"Starting import of {name}…",
                     queue=queue.Queue())
        state["cancel"].clear()
        threading.Thread(target=self._import_worker, args=(kind, path), daemon=True).start()
        self._refresh_import_widgets()
        self._poll_import()

    def _import_worker(self, kind: str, path: str) -> None:
        """Runs in a background thread with its OWN database connection."""
        state = self.import_state
        out: queue.Queue = state["queue"]

        def progress(done: int, total: Optional[int], message: str) -> None:
            out.put(("progress", (done / total) if total else None, message))

        try:
            worker_db = Database(self.db.path).connect()
            try:
                run = import_pdf if kind == "pdf" else import_file
                summary = run(worker_db, path, progress=progress, cancel_event=state["cancel"])
            finally:
                worker_db.close()
            out.put(("done", summary))
        except (ImportFormatError, DatabaseError, OSError) as exc:
            out.put(("error", str(exc)))
        except Exception:
            log.exception("Unexpected import failure")
            out.put(("error", "An unexpected error occurred. Your existing dictionary was not changed by "
                              "the failed step. Details were saved to the log file."))

    def _poll_import(self) -> None:
        """Runs on the UI thread every 100 ms to apply messages from the worker thread."""
        state = self.import_state
        try:
            while True:
                kind, *payload = state["queue"].get_nowait()
                if kind == "progress":
                    fraction, message = payload
                    state.update(indeterminate=fraction is None, progress=fraction or 0.0, status=message)
                elif kind == "done":
                    summary = payload[0]
                    state.update(running=False, progress=1.0, report=summary.format(),
                                 status="Import failed." if summary.failed else
                                 "Import cancelled." if summary.cancelled else "Import finished.")
                else:
                    state.update(running=False, status="Import failed.", report=payload[0])
        except queue.Empty:
            pass
        self._refresh_import_widgets()
        if state["running"]:
            self.after(100, self._poll_import)

    def _cancel_import(self) -> None:
        self.import_state["cancel"].set()
        self.import_state["status"] = "Cancelling… entries already imported will be kept."
        self._refresh_import_widgets()

    # ------------------------------------------------------------ SETTINGS
    def page_settings(self) -> None:
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        page.pack(fill="both", expand=True)

        def section(title: str) -> ctk.CTkFrame:
            card = self.card(page)
            card.pack(fill="x", padx=4, pady=(4, 10))
            self.label(card, title, delta=4, weight="bold").pack(anchor="w", padx=22, pady=(16, 8))
            return card

        look = section("Appearance")
        theme_row = ctk.CTkFrame(look, fg_color="transparent")
        theme_row.pack(anchor="w", padx=22, pady=(0, 8))
        chooser = ctk.CTkSegmentedButton(theme_row, values=["Light", "Dark", "System"], command=self._change_theme)
        chooser.set(self.settings.get("theme").capitalize())
        chooser.pack(side="left")
        size_row = ctk.CTkFrame(look, fg_color="transparent")
        size_row.pack(anchor="w", padx=22, pady=(4, 18), fill="x")
        size_label = self.label(size_row, f"Text size: {self.settings.get('font_size')} pt", wrap=False)
        size_label.pack(side="left")
        slider = ctk.CTkSlider(size_row, from_=MIN_FONT, to=MAX_FONT, number_of_steps=MAX_FONT - MIN_FONT, width=260,
                               command=lambda v: size_label.configure(text=f"Text size: {int(v)} pt"))
        slider.set(self.settings.get("font_size"))
        slider.pack(side="left", padx=16)
        slider.bind("<ButtonRelease-1>", lambda _e: self._change_font_size(int(slider.get())), add="+")

        info = section("Dictionary database")
        words = self.guard(self.db.count_words, default=0)
        meanings = self.guard(self.db.count_meanings, default=0)
        for text in (f"Words: {words:,}", f"Definitions: {meanings:,}",
                     f"File size: {self.db.file_size() / 1024:,.0f} KB", f"Location: {self.db.path}"):
            self.label(info, text).pack(anchor="w", padx=22, pady=1, fill="x")
        tools = ctk.CTkFrame(info, fg_color="transparent")
        tools.pack(anchor="w", padx=22, pady=(12, 18))
        self.button(tools, "Back up database…", self._backup_database, width=170).pack(side="left", padx=(0, 10))
        self.button(tools, "Re-import sample data", self._reimport_sample, primary=False, width=190).pack(side="left", padx=(0, 10))
        if sys.platform == "win32":
            self.button(tools, "Open data folder", lambda: os.startfile(self.db.path.parent),  # type: ignore[attr-defined]
                        primary=False, width=160).pack(side="left")

        data = section("Your data")
        self.button(data, "Clear search history…", self._clear_history_from_settings, danger=True,
                    width=200).pack(anchor="w", padx=22, pady=(0, 18))

        about = section("About")
        self.label(about, f"{APP_NAME}  •  version {APP_VERSION}", weight="bold").pack(anchor="w", padx=22, fill="x")
        self.label(about, "A fully offline English dictionary. It makes no network requests, collects no analytics "
                   "and needs no account. The bundled sample data is for demonstration only; see the README for "
                   "licensing and how to import a full dictionary.", muted=True).pack(anchor="w", padx=22, pady=(4, 18), fill="x")

    def _change_theme(self, value: str) -> None:
        self.settings.set("theme", value.lower())
        ctk.set_appearance_mode(value.lower())

    def _change_font_size(self, size: int) -> None:
        self.settings.set("font_size", size)
        self._fonts.clear()
        self.show_page(self.current_page)

    def _backup_database(self) -> None:
        target = filedialog.asksaveasfilename(title="Save a backup copy", defaultextension=".db",
                                              initialfile="dictionary-backup.db",
                                              filetypes=[("SQLite database", "*.db")])
        if not target:
            return
        try:
            self.db.backup_to(target)
            messagebox.showinfo("Backup complete", f"Backup saved to:\n{target}")
        except DatabaseError as exc:
            messagebox.showerror("Backup failed", str(exc))

    def _reimport_sample(self) -> None:
        self.show_page("import")
        self._start_import("sample")

    def _clear_history_from_settings(self) -> None:
        if messagebox.askyesno("Clear search history", "Delete your entire search history?"):
            self.guard(self.db.clear_history)
            messagebox.showinfo("Done", "Search history cleared.")
