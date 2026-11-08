"""UI smoke tests: render the real Streamlit app headlessly (streamlit.testing AppTest).

The UI modules had no automated coverage. These tests run the dashboard script exactly as Streamlit would,
on a throwaway database, with the rule-based provider (no network, no API keys).
"""
from __future__ import annotations

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ares import service
from tests._paths import ROOT

APP = str(ROOT / "app.py")


def _reset_streamlit_caches() -> None:
    # app.py caches the Service with st.cache_resource; AppTest runs in this process, so without clearing,
    # a later test would reuse an earlier test's Service (and its closed tmp database).
    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.fixture
def app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("ARES_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("ARES_PROVIDER", "rule-based")
    monkeypatch.setattr(service, "_SERVICE", None)  # fresh service bound to the tmp database
    _reset_streamlit_caches()
    at = AppTest.from_file(APP, default_timeout=120)
    yield at
    _reset_streamlit_caches()


def test_dashboard_renders_without_errors(app):
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    assert any("ARES Accord" in t.value for t in app.title)
    assert any("Start baseline" in b.label for b in app.button)


def test_baseline_run_through_the_ui_reaches_approval(app):
    app.run()
    start = next(b for b in app.button if "Start baseline" in b.label)
    start.click().run()
    for _ in range(3):  # the run is queued, executed with live progress, then the page reruns
        app.run()
    assert not app.exception, [e.value for e in app.exception]
    assert any("APPROVED" in m.value for m in app.markdown), "no APPROVED banner rendered"
