"""Locate organiser starter-kit files: the real `starter_kit/` when present, else `tests/fixtures/`."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTER_KIT = ROOT / "starter_kit"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def starter_file(name: str) -> Path:
    """Path to starter-kit file `name`; see tests/fixtures/README.md for the provenance of the fallbacks."""
    original = STARTER_KIT / name
    return original if original.exists() else FIXTURES / name
