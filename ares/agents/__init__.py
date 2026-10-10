"""ARES Accord agents: four departments + Commander, each with private profile, memory and state."""
from __future__ import annotations

from ares.models import DEPARTMENTS
from ares.llm.base import LLMError
from ares.agents.commander import CommanderAgent
from ares.agents.department import DepartmentAgent


def build_agents(provider_name: str) -> tuple[CommanderAgent, dict[str, DepartmentAgent]]:
    """One provider instance shared by all five agents; agents keep separate memory/state.

    If the provider cannot be created (missing key, Ollama down) every turn runs the labelled
    rule-based policy with a fallback_reason naming the problem; it never raises for that.
    """
    from ares.llm.providers import get_provider
    provider, error = None, None
    try:
        provider = get_provider(provider_name)
    except LLMError as exc:
        error = f"provider '{provider_name}' unavailable: {exc}"[:160]
    commander = CommanderAgent(provider, error)
    depts = {d: DepartmentAgent(d, provider, error) for d in DEPARTMENTS}
    return commander, depts


__all__ = ["CommanderAgent", "DepartmentAgent", "build_agents"]
