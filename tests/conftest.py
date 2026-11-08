"""Shared pytest fixtures."""
from __future__ import annotations

import pytest

from ares import storage


@pytest.fixture(autouse=True)
def _close_stores(monkeypatch):
    """Close every SQLite Store a test opens.

    Tests build `Store(...)` objects in many helpers and never closed them, which produced
    `ResourceWarning: unclosed database` noise and leaked file handles (Windows keeps tmp DBs locked).
    """
    created: list[storage.Store] = []
    original_init = storage.Store.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(storage.Store, "__init__", tracking_init)
    yield
    for store in created:
        store.close()
