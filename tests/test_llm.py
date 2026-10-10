"""LLM layer tests (no network: requests is mocked)."""
from __future__ import annotations

import pytest
import requests

from ares.llm import LLMError, available_providers, extract_json, get_provider, reset_provider_state
from ares.llm import base as llm_base
from ares.llm.providers import GeminiProvider, OpenAICompatProvider


class FakeResp:
    def __init__(self, status: int, data=None, headers=None, text=""):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_provider_state()
    monkeypatch.setattr(llm_base.time, "sleep", lambda s: None)
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
              "GROQ_MODEL", "GEMINI_MODEL", "OPENROUTER_ALLOW_PAID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("ares.llm.providers._ollama_models", lambda timeout=1.0: None)
    yield
    reset_provider_state()


def _chat(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ------------------------------------------------------------------ jsonutil
def test_extract_plain_and_fenced():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure! Here it is:\n```json\n{"mode": "L2", "x": [1, 2]}\n```\nThanks') == {
        "mode": "L2", "x": [1, 2]}


def test_extract_repairs_trailing_commas_and_prose():
    assert extract_json('Answer: {"a": 1, "b": [1, 2,],} trailing words') == {"a": 1, "b": [1, 2]}
    assert extract_json('{"ok": True, "n": None}') == {"ok": True, "n": None}


def test_extract_nested_braces_in_strings():
    assert extract_json('noise {"m": "use {braces} here", "k": {"z": 2}} more') == {
        "m": "use {braces} here", "k": {"z": 2}}


@pytest.mark.parametrize("bad", ["", "   ", "no json at all", "[1, 2, 3]", None])
def test_extract_impossible_raises(bad):
    with pytest.raises(LLMError):
        extract_json(bad)


# ------------------------------------------------------------------ HTTP behaviour
def test_retry_on_429_then_success(monkeypatch):
    calls = []
    responses = [FakeResp(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
                 FakeResp(200, _chat('{"vote": "ACCEPT", "reason": "ok"}'))]

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, timeout))
        return responses.pop(0)

    monkeypatch.setattr(llm_base.requests, "post", fake_post)
    p = OpenAICompatProvider("groq", "https://api.groq.com/openai/v1/chat/completions", "m", "k",
                             min_interval_s=0, max_retries=2)
    assert p.complete_json("sys", "user", 10) == {"vote": "ACCEPT", "reason": "ok"}
    assert len(calls) == 2
    assert all(isinstance(t, tuple) and t[1] <= 10 for _, t in calls)   # strict timeout on every request


def test_retry_exhausted_raises(monkeypatch):
    monkeypatch.setattr(llm_base.requests, "post", lambda *a, **k: FakeResp(503, {"error": "down"}))
    p = OpenAICompatProvider("groq", "u", "m", "k", min_interval_s=0, max_retries=1)
    with pytest.raises(LLMError, match="503"):
        p.complete_json("s", "u", 10)


def test_timeout_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr(llm_base.requests, "post", boom)
    p = GeminiProvider("gemini-x", "k", min_interval_s=0, max_retries=1)
    with pytest.raises(LLMError, match="timeout"):
        p.complete_json("s", "u", 5)


def test_gemini_request_shape_and_parse(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": '{"mode": "M2"}'}]}}]})

    monkeypatch.setattr(llm_base.requests, "post", fake_post)
    p = GeminiProvider("gemini-test", "secret-key", min_interval_s=0)
    assert p.complete_json("s", "u", 5) == {"mode": "M2"}
    assert seen["headers"]["x-goog-api-key"] == "secret-key"
    assert "secret-key" not in seen["url"]
    assert seen["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "secret-key" not in repr(p)


def test_model_404_falls_back_to_next_free_model(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        if json["model"] == "old-model":
            return FakeResp(404, {"error": {"message": "model not found"}})
        return FakeResp(200, _chat('{"a": 1}'))

    monkeypatch.setattr(llm_base.requests, "post", fake_post)
    p = OpenAICompatProvider("groq", "u", "old-model", "k", fallback_models=("new-model",), min_interval_s=0)
    assert p.complete_json("s", "u", 5) == {"a": 1}
    assert p.model == "new-model"


def test_auth_error_not_retried(monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResp(401, {"error": {"message": "invalid api key"}})

    monkeypatch.setattr(llm_base.requests, "post", fake_post)
    p = OpenAICompatProvider("openrouter", "u", "x:free", "k", min_interval_s=0, max_retries=2)
    with pytest.raises(LLMError, match="401"):
        p.complete_json("s", "u", 5)
    assert len(calls) == 1


# ------------------------------------------------------------------ registry
def test_missing_key_raises():
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        get_provider("groq")
    with pytest.raises(LLMError):
        get_provider("gemini")
    with pytest.raises(LLMError):
        get_provider("does-not-exist")


def test_rule_based_returns_none():
    assert get_provider("rule-based") is None


def test_key_from_env_and_google_alias(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "abc")
    p = get_provider("gemini")
    assert p.name == "gemini" and p.model


def test_openrouter_requires_free_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert get_provider("openrouter").model in ("openrouter/free",) or get_provider("openrouter").model.endswith(":free")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-paid")
    with pytest.raises(LLMError, match="free"):
        get_provider("openrouter")


def test_available_providers_includes_rule_based_first(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    rows = available_providers()
    assert rows[0]["name"] == "rule-based" and rows[0]["available"] is True
    assert rows[0]["note"] == "Deterministic fallback policy - not an LLM"
    by = {r["name"]: r for r in rows}
    assert set(by) >= {"gemini", "groq", "openrouter", "ollama"}
    assert by["groq"]["available"] is True and by["gemini"]["available"] is False
    assert by["ollama"]["available"] is False
    for r in rows:
        assert set(r) >= {"name", "model", "available", "env_var", "note"}
