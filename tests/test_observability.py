"""Structured logging and the smoke-run database guard (see CHANGELOG.md, "Observability")."""
from __future__ import annotations

import importlib.util
import logging
import sys

import pytest

from ares.config import Settings
from ares.llm.base import short_error
from ares.log import configure_logging, redact_secrets
from ares.service import Service
from ares.storage import Store
from tests._paths import ROOT

# Synthetic, clearly fake key-shaped strings (never real credentials).
FAKE_OPENAI = "sk-proj-FAKEfake0123456789abcdefXYZ"
FAKE_GROQ = "gsk_FAKEfake0123456789abcdefghijklmn"
FAKE_GOOGLE = "AIza" + "F" * 35


@pytest.mark.parametrize("text, secret", [
    (f"Incorrect API key provided: {FAKE_OPENAI}.", FAKE_OPENAI),
    (f"invalid key {FAKE_GROQ}", FAKE_GROQ),
    (f"API key not valid: {FAKE_GOOGLE}", FAKE_GOOGLE),
    ("Authorization: Bearer abc.DEF-123_456.ghi789", "abc.DEF-123_456.ghi789"),
    ('{"api_key": "plainvalue123456789"}', "plainvalue123456789"),
])
def test_redact_secrets_masks_key_shaped_tokens(text, secret):
    out = redact_secrets(text)
    assert secret not in out and "[REDACTED]" in out


def test_redact_secrets_masks_configured_key_values(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "custom-format-secret-value-42")
    assert "custom-format-secret-value-42" not in redact_secrets("echo: custom-format-secret-value-42")
    assert redact_secrets("Plan v3 approved in round 3.") == "Plan v3 approved in round 3."  # normal text intact


class _FakeResponse:
    def __init__(self, body):
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_short_error_never_returns_a_key_echoed_by_the_provider():
    resp = _FakeResponse({"error": {"message": f"Incorrect API key provided: {FAKE_OPENAI}"}})
    out = short_error(resp)
    assert FAKE_OPENAI not in out and "Incorrect API key provided" in out


def test_log_handler_redacts_secrets_from_every_ares_logger():
    logger = logging.getLogger("ares")
    saved_level, saved_handlers = logger.level, list(logger.handlers)
    try:
        configure_logging("INFO")
        handler = next(h for h in logger.handlers if h.get_name() == "ares-default")
        record = logging.LogRecord("ares.llm.base", logging.WARNING, __file__, 1,
                                   "llm_call_failed provider=%s reason=%r", ("groq", f"HTTP 401 {FAKE_GROQ}"), None)
        assert handler.filter(record)
        assert FAKE_GROQ not in handler.format(record)
        assert "[REDACTED]" in handler.format(record)
    finally:
        logger.setLevel(saved_level)
        logger.handlers[:] = saved_handlers


def test_configure_logging_is_idempotent_and_rejects_unknown_levels(caplog):
    logger = logging.getLogger("ares")
    saved_level, saved_handlers = logger.level, list(logger.handlers)
    try:
        configure_logging("DEBUG")
        configure_logging("DEBUG")
        assert sum(1 for h in logger.handlers if h.get_name() == "ares-default") == 1
        assert logger.level == logging.DEBUG
        with caplog.at_level(logging.WARNING, logger="ares"):
            configure_logging("chatty")
            assert logger.level == logging.INFO  # caplog restores the previous level when the block exits
        assert "ARES_LOG_LEVEL='CHATTY'" in caplog.text
    finally:
        logger.setLevel(saved_level)
        logger.handlers[:] = saved_handlers


def test_orchestrator_logs_scenario_lifecycle_and_gate_decisions(tmp_path, caplog):
    settings = Settings(db_path=str(tmp_path / "t.db"), default_provider="rule-based")
    svc = Service(settings=settings, store=Store(settings.db_path))
    with caplog.at_level(logging.INFO, logger="ares.orchestrator"):
        scenario = svc.start_baseline(None, "rule-based")
    assert scenario["status"] == "APPROVED"
    text = caplog.text
    assert f"scenario_start scenario={scenario['id']} kind=BASELINE provider=rule-based" in text
    assert "approval_gate" in text and "ok=True blocking=0" in text
    assert f"scenario_end scenario={scenario['id']} kind=BASELINE status=APPROVED" in text
    assert "agent_fallback" not in text  # rule-based mode is not a fallback from an LLM


def _load_smoke_run():
    spec = importlib.util.spec_from_file_location("smoke_run", ROOT / "scripts" / "smoke_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_run_refuses_to_delete_the_dashboard_database(tmp_path, monkeypatch, capsys):
    dashboard_db = tmp_path / "dashboard.db"
    dashboard_db.write_text("precious history")
    monkeypatch.setenv("ARES_DB_PATH", str(dashboard_db))
    monkeypatch.setattr(sys, "argv", ["smoke_run.py", "--db", str(dashboard_db), "--quiet"])
    assert _load_smoke_run().main() == 2
    assert dashboard_db.read_text() == "precious history"
    assert "Refusing to use the dashboard database" in capsys.readouterr().err
