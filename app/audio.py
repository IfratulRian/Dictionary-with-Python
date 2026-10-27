"""Offline pronunciation playback using only the standard library.

Windows: .wav files through winsound (built into Python, no install needed).
macOS/Linux (for development): afplay / aplay if present.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .utils import project_root, user_data_dir

log = logging.getLogger(__name__)


def resolve_audio_path(audio_path: str) -> Optional[Path]:
    """Find the stored audio file. Relative paths are looked up in user_data/audio, then the project."""
    if not audio_path:
        return None
    candidate = Path(audio_path)
    folders = [Path()] if candidate.is_absolute() else [user_data_dir() / "audio", project_root()]
    for folder in folders:
        full = folder / candidate
        try:
            if full.is_file():
                return full
        except OSError:
            continue
    return None


def play_audio(path: Path) -> tuple[bool, str]:
    """Start playing without blocking the UI. Returns (success, message)."""
    try:
        if sys.platform == "win32":
            if path.suffix.lower() != ".wav":
                return False, "Only .wav files can be played. Convert this file to WAV."
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True, "Playing…"
        player = shutil.which("afplay") or shutil.which("aplay")
        if player is None:
            return False, "No audio player was found on this system."
        subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "Playing…"
    except (OSError, RuntimeError):
        log.exception("Audio playback failed")
        return False, "The audio file could not be played."
