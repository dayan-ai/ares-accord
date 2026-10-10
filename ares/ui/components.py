"""Small, reusable rendering helpers (badges, labels, formatting).

Everything here is presentation only: it formats records returned by the Service
API. No approval logic lives here.
"""
from __future__ import annotations

import html
import json
from typing import Any, Iterable, Mapping

import streamlit as st

from ares.models import (
    AGENT_LABELS,
    DEPARTMENTS,
    MODE_PACKAGES,
    RESOURCE_LABELS,
    RESOURCES,
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
_CSS = """
<style>
.ares-badge{display:inline-block;padding:1px 8px;margin:0 4px 2px 0;border-radius:10px;
  font-size:0.75rem;font-weight:600;line-height:1.5rem;white-space:nowrap;color:#fff;}
.ares-green{background:#1e8e3e;} .ares-amber{background:#b26a00;} .ares-red{background:#c62828;}
.ares-blue{background:#1565c0;} .ares-grey{background:#5f6368;} .ares-purple{background:#6a1b9a;}
.ares-teal{background:#00796b;} .ares-mars{background:#b5420f;}
.ares-banner{padding:14px 18px;border-radius:10px;color:#fff;margin:4px 0 10px 0;}
.ares-banner h3{margin:0 0 4px 0;padding:0;color:#fff;font-size:1.35rem;}
.ares-banner p{margin:0;color:#fff;opacity:0.95;}
.ares-fallback{padding:10px 14px;border-radius:8px;background:#c62828;color:#fff;font-weight:700;
  margin:4px 0 10px 0;border:2px dashed #ffcdd2;}
.ares-fallback span{font-weight:400;opacity:0.95;}
.ares-bigbadge{display:inline-block;padding:8px 26px;border-radius:12px;font-size:1.6rem;font-weight:800;color:#fff;}
.ares-card-title{font-size:1.05rem;font-weight:700;margin-bottom:2px;}
.ares-muted{opacity:0.75;font-size:0.85rem;}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def esc(text: Any) -> str:
    """HTML-escape any value for use inside unsafe_allow_html markup."""
    return html.escape("" if text is None else str(text))


def md(text: Any) -> str:
    """Make free text safe for st.markdown (Streamlit renders $...$ as LaTeX)."""
    return ("" if text is None else str(text)).replace("$", "\\$")


def badge(label: Any, color: str = "grey", title: str | None = None) -> str:
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="ares-badge ares-{color}"{t}>{esc(label)}</span>'


def html_line(*parts: str) -> None:
    st.markdown(" ".join(p for p in parts if p), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Labels / colours
# ---------------------------------------------------------------------------
STATUS_COLORS = {
    "RUNNING": "blue", "APPROVED": "green", "DEADLOCK": "red", "INFEASIBLE": "red",
    "TIMEOUT": "amber", "STALE": "amber", "AWAITING_COSIGN": "amber", "VETOED": "red", "ERROR": "red",
}
STATUS_BG = {"blue": "#1565c0", "green": "#1e8e3e", "red": "#c62828", "amber": "#b26a00", "grey": "#5f6368"}
STATUS_ICONS = {
    "RUNNING": "⏳", "APPROVED": "✅", "DEADLOCK": "⛔", "INFEASIBLE": "🚫", "TIMEOUT": "⌛", "STALE": "♻️",
    "AWAITING_COSIGN": "✍️", "VETOED": "⛔", "ERROR": "⚠️",
}
PLAN_STATUS_COLORS = {
    "DRAFT": "grey", "FAILED": "red", "VOTING": "blue", "REJECTED": "red", "APPROVED": "green",
    "SUPERSEDED": "grey", "STALE": "amber", "AWAITING_COSIGN": "amber", "VALIDATED": "blue",
}
TIER_COLORS = {"STANDARD": "green", "RESTRICTED": "amber", "SACRIFICE": "red"}
TIER_HEX = {"STANDARD": "#1e8e3e", "RESTRICTED": "#b26a00", "SACRIFICE": "#c62828"}
COMMITMENT_COLORS = {"PROPOSED": "blue", "ACCEPTED": "green", "REJECTED": "red", "VOIDED": "grey"}

AVATARS = {
    "COMMANDER": "🎖️", "LIFE_SUPPORT": "🫁", "MEDICAL": "🩺", "FOOD": "🌱", "ENGINEERING": "🔧",
    "SYSTEM": "🛰️", "VALIDATOR": "⚖️", "JUDGE": "🧑‍⚖️",
}

MSG_TYPE_COLORS = {
    "PROPOSAL": "blue", "OBJECTION": "amber", "COUNTEROFFER": "purple", "ACCEPT_MODE": "green",
    "REFUSE_SACRIFICE": "red", "ACCEPT_SACRIFICE": "green", "BRIEFING": "mars", "CONFLICT": "amber",
    "REQUEST": "mars", "COMMITMENT": "teal", "COMMITMENT_ACCEPTED": "green", "COMMITMENT_REJECTED": "red",
    "COMMITMENT_VOIDED": "grey", "PLAN_DRAFT": "purple", "VALIDATION": "teal", "VOTE_CALL": "mars",
    "VOTE": "blue", "VOTES_CLEARED": "grey", "APPROVAL": "green", "APPROVAL_BLOCKED": "red",
    "EVENT": "red", "PLAN_STALE": "amber", "DEADLOCK": "red", "INFEASIBLE": "red", "TIMEOUT": "amber",
    "SYSTEM": "grey",
}

RES_SHORT = {"power": "P", "water": "W", "oxygen": "O", "robot": "R", "bandwidth": "B"}


def agent_label(agent: Any) -> str:
    if agent is None:
        return "—"
    return AGENT_LABELS.get(str(agent), str(agent).replace("_", " ").title())


def res_label(res: Any) -> str:
    return RESOURCE_LABELS.get(str(res), str(res))


def tier_of(code: Any) -> str | None:
    pkg = MODE_PACKAGES.get(str(code)) if code else None
    return pkg.tier if pkg else None


def mode_badge(code: Any) -> str:
    tier = tier_of(code)
    if not tier:
        return badge(code or "—", "grey")
    return badge(f"{code} · {tier.title()}", TIER_COLORS[tier])


def modes_label(modes: Mapping[str, str] | None) -> str:
    if not modes:
        return "—"
    return "+".join(str(modes.get(d, "?")) for d in DEPARTMENTS if d in modes) or "—"


def pool_short(pool: Mapping[str, Any] | None) -> str:
    if not pool:
        return "—"
    return " ".join(f"{RES_SHORT.get(r, r)}{pool.get(r, '?')}" for r in RESOURCES)


def policy_short(policy: Mapping[str, Any] | None) -> str:
    if not policy:
        return "—"
    ov = "Crisis Override ON" if policy.get("crisis_override") else "override off"
    return f"risk ≤ {policy.get('risk_limit', '?')}, ≤ {policy.get('max_sacrifices', '?')} sacrifice(s), {ov}"


def source_badge(src: Any, provider: Any = None, model: Any = None, fallback_reason: Any = None) -> str:
    s = (src or "").lower()
    if s == "llm":
        who = "/".join(str(x) for x in (provider, model) if x) or "LLM"
        return badge(f"LLM · {who}", "green")
    if s == "rule-based":
        return badge("RULE-BASED FALLBACK", "red", title=str(fallback_reason or "rule-based policy"))
    if s == "validator":
        return badge("VALIDATOR", "teal")
    if s == "system":
        return badge("SYSTEM", "grey")
    return badge((src or "unknown").upper(), "grey")


def msg_type_badge(t: Any) -> str:
    return badge(t or "—", MSG_TYPE_COLORS.get(str(t), "grey"))


def status_badge(status: Any) -> str:
    return badge(status or "—", STATUS_COLORS.get(str(status), "grey"))


def plan_status_badge(status: Any) -> str:
    return badge(status or "—", PLAN_STATUS_COLORS.get(str(status), "grey"))


def result_banner(status: str, title: str, reason: str) -> None:
    color = STATUS_COLORS.get(status, "grey")
    icon = STATUS_ICONS.get(status, "•")
    st.markdown(
        f'<div class="ares-banner" style="background:{STATUS_BG[color]}">'
        f"<h3>{icon} {esc(status)} — {esc(title)}</h3><p>{esc(reason) or '&nbsp;'}</p></div>",
        unsafe_allow_html=True,
    )


def fallback_banner(detail: str) -> None:
    st.markdown(
        '<div class="ares-fallback">⚠️ RULE-BASED FALLBACK — not an LLM negotiation'
        f"<br><span>{esc(detail)}</span></div>",
        unsafe_allow_html=True,
    )


def big_badge(text: str, color: str) -> None:
    st.markdown(
        f'<span class="ares-bigbadge" style="background:{STATUS_BG.get(color, "#5f6368")}">{esc(text)}</span>',
        unsafe_allow_html=True,
    )


def tick(ok: Any) -> str:
    return "✅" if ok else "⬜"


def check_mark(ok: Any) -> str:
    return "✅" if ok else "❌"


def short(text: Any, n: int = 220) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def mentions(text: str, needles: Iterable[str]) -> bool:
    low = text.lower()
    return any(n and n.lower() in low for n in needles)
