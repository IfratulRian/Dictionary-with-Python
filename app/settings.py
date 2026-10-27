"""User preferences stored as a small JSON file."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {"theme": "dark", "font_size": 14, "sample_seeded": False}
THEMES = ("light", "dark", "system")
MIN_FONT, MAX_FONT = 11, 22


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.values: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.warning("Settings file unreadable; using defaults.")
            return
        if not isinstance(data, dict):
            return
        for key in DEFAULTS:
            if key in data:
                self.values[key] = self._validate(key, data[key])

    def get(self, key: str) -> Any:
        return self.values[key]

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.values[key] = self._validate(key, value)
        self.save()

    def save(self) -> None:
        """Write atomically so a crash cannot leave a half-written file."""
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            log.exception("Could not save settings")

    @staticmethod
    def _validate(key: str, value: Any) -> Any:
        default = DEFAULTS[key]
        if key == "theme":
            return value if value in THEMES else default
        if key == "font_size":
            try:
                return max(MIN_FONT, min(MAX_FONT, int(value)))
            except (TypeError, ValueError):
                return default
        return bool(value)
