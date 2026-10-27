"""Colours used by the UI.

CustomTkinter accepts a (light_mode_colour, dark_mode_colour) pair, so each
colour below automatically switches when the theme changes.
"""

ACCENT = ("#2563EB", "#3B82F6")
ACCENT_HOVER = ("#1D4ED8", "#2563EB")
ACCENT_TEXT = ("#1D4ED8", "#7DB2FF")      # accent-coloured text on cards
ON_ACCENT = ("#FFFFFF", "#FFFFFF")
BG = ("#EEF2F9", "#0F172A")               # window background
SIDEBAR = ("#E1E7F3", "#0B1120")
CARD = ("#FFFFFF", "#18223A")
CARD_ALT = ("#E8EDF7", "#233154")         # chips, hover states
TEXT = ("#0F172A", "#E8EEF9")
MUTED = ("#5B6B88", "#8FA0BF")
SUCCESS = ("#15803D", "#4ADE80")
DANGER = ("#DC2626", "#F87171")
DANGER_HOVER = ("#B91C1C", "#DC2626")

NAV_ITEMS = [  # (page key, label, icon)
    ("home", "Home", "⌂"),
    ("search", "Dictionary Search", "⌕"),
    ("favorites", "Favorites", "★"),
    ("history", "Search History", "◷"),
    ("random", "Random Word", "⚄"),
    ("import", "Import Dictionary", "⇩"),
    ("settings", "Settings", "⚙"),
]
PAGE_TITLES = {key: label for key, label, _ in NAV_ITEMS}
