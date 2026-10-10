"""Main-area views: header + the five dashboard tabs.

All data comes from `Service.state()` and `Service.transcript()`. The UI only
formats it; plan status / approval are whatever the service reports.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pandas as pd
import streamlit as st

from ares.models import AGENT_LABELS, DEPARTMENTS, MODE_PACKAGES, RESOURCES
from ares.ui import components as C

AGENT_SOURCES = ("llm", "rule-based")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scenario_title(scn: dict | None) -> str:
    if not scn:
        return "—"
    kind = str(scn.get("kind") or "").title()
    return f"#{scn.get('id')} {kind} · {scn.get('name') or ''}".strip()


def scenario_messages(messages: list[dict], scn: dict | None) -> list[dict]:
    if not scn:
        return []
    sid = scn.get("id")
    return [m for m in messages if m.get("scenario_id") == sid]


def _vote_tally(votes: list[dict] | None, version: Any = None) -> tuple[int, int]:
    acc = rej = 0
    for v in votes or []:
        if v.get("valid") in (0, False):
            continue
        if version is not None and v.get("plan_version") not in (None, version):
            continue
        if v.get("decision") == "ACCEPT":
            acc += 1
        elif v.get("decision") == "REJECT":
            rej += 1
    return acc, rej


def _mode_diff(prev: dict | None, cur: dict | None) -> str:
    if not cur:
        return ""
    if not prev:
        return "initial"
    changes = [f"{prev.get(d)}→{cur.get(d)}" for d in DEPARTMENTS if prev.get(d) != cur.get(d)]
    return ", ".join(changes) if changes else "no mode change"


def _empty_state() -> None:
    with st.container(border=True):
        st.markdown("### 👋 No active scenario in this session")
        st.markdown(
            "1. Pick a **negotiation provider** in *Judge Controls* (✅ = configured).\n"
            "2. Enter the **resource pool** (defaults to the guide's baseline 79 / 52 / 59 / 26 / 17).\n"
            "3. Press **🚀 Start baseline crisis** — the five agents negotiate live; each message streams in.\n"
            "4. After the result, inject a **crisis event** (form, the official JSON format, or free text), "
            "preview its interpretation, then **Inject event & renegotiate**.\n\n"
            "Earlier sessions stay visible in **Council Transcript** and **Scenarios**."
        )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def render_header(state: dict, cur_msgs: list[dict]) -> None:
    scn = state.get("scenario")
    plan = state.get("plan")
    if not scn:
        sess = state.get("session") or {}
        st.caption(f"Session #{sess.get('id', '—')} · no scenario yet")
        return
    status = str(scn.get("status") or "RUNNING")
    reason = scn.get("result_reason") or ("Negotiation in progress." if status == "RUNNING" else "")
    C.result_banner(status, _scenario_title(scn), reason)

    provider = scn.get("provider") or "—"
    prov_badge = (C.badge("Provider: RULE-BASED (no LLM)", "red") if provider == "rule-based"
                  else C.badge(f"Provider: {provider}", "blue"))
    parts = [
        C.badge(f"Session #{scn.get('session_id', '—')}", "grey"),
        C.badge(f"Round {scn.get('round', 0)} / {scn.get('max_rounds', '?')}", "grey"),
    ]
    if plan:
        parts += [C.badge(f"Plan v{plan.get('version')}", "purple"), C.plan_status_badge(plan.get("status")),
                  C.badge(f"Modes {C.modes_label(plan.get('modes'))}", "grey")]
    else:
        parts.append(C.badge("No plan yet", "grey"))
    parts += [prov_badge, C.badge(C.policy_short(scn.get("policy")), "mars")]
    C.html_line(*parts)

    agent_msgs = [m for m in cur_msgs if (m.get("source") or "").lower() in AGENT_SOURCES]
    fb_msgs = [m for m in agent_msgs if (m.get("source") or "").lower() == "rule-based"]
    fb_votes = [v for v in (plan or {}).get("votes") or [] if (v.get("source") or "").lower() == "rule-based"]
    if provider == "rule-based":
        C.fallback_banner("Provider is set to rule-based: every agent turn and vote in this scenario was produced "
                          "by the deterministic rule policy, not by a language model.")
    elif fb_msgs or fb_votes:
        C.fallback_banner(f"{len(fb_msgs)} of {len(agent_msgs)} agent messages"
                          + (f" and {len(fb_votes)} vote(s)" if fb_votes else "")
                          + " in this scenario fell back to the rule-based policy (LLM error, timeout or invalid "
                            "JSON). Those items carry a RULE-BASED FALLBACK badge with the reason.")


# ---------------------------------------------------------------------------
# Mission Control
# ---------------------------------------------------------------------------
def _event_card(scn: dict) -> None:
    ev = scn.get("event")
    with st.container(border=True):
        if not ev:
            st.markdown("#### 🪐 Baseline crisis")
            st.markdown(C.md(scn.get("description") or "Initial council negotiation on the judge-entered pool."))
            st.caption(f"Pool: {C.pool_short(scn.get('pool'))}")
            return
        st.markdown(f"#### 🚨 {C.md(ev.get('name') or scn.get('name') or 'Crisis event')}")
        if ev.get("event_id"):
            st.caption(f"Event ID: {ev.get('event_id')}"
                       + (f" · duration {ev.get('duration_hours')} h" if ev.get("duration_hours") else ""))
        if ev.get("description"):
            st.markdown(C.md(ev["description"]))
        if ev.get("message_to_commander"):
            st.info(f"📡 {ev['message_to_commander']}")
        lines = []
        for r, v in (ev.get("deltas") or {}).items():
            lines.append(f"{C.res_label(r)} {int(v):+d}")
        for r, v in (ev.get("percent_changes") or {}).items():
            lines.append(f"{C.res_label(r)} {float(v):+g}%")
        for r, v in (ev.get("set_values") or {}).items():
            lines.append(f"{C.res_label(r)} set to {v}")
        if lines:
            st.markdown("**Changes:** " + " · ".join(lines))
        if ev.get("forbidden_modes"):
            st.markdown("**Forbidden modes:** " + ", ".join(map(str, ev["forbidden_modes"])))
        base, pool = scn.get("base_pool") or {}, scn.get("pool") or {}
        if base and pool and base != pool:
            st.caption("Pool " + " · ".join(
                f"{C.res_label(r)} {base.get(r)}→{pool.get(r)}" for r in RESOURCES if base.get(r) != pool.get(r)))
        notes = ev.get("interpretation_notes") or []
        if notes:
            with st.expander(f"Interpretation notes ({len(notes)})"):
                for n in notes:
                    st.markdown(f"- {C.md(n)}")


def _resource_section(scn: dict, plan: dict | None) -> None:
    pool = scn.get("pool") or {}
    base = scn.get("base_pool") or {}
    totals = (plan or {}).get("totals") or {}
    reserve = (plan or {}).get("reserve") or {}
    cols = st.columns(len(RESOURCES))
    for col, r in zip(cols, RESOURCES):
        avail = pool.get(r)
        if plan and r in totals:
            res_v = reserve.get(r, (avail or 0) - totals.get(r, 0))
            col.metric(C.res_label(r), f"{totals.get(r)} / {avail}", delta=f"{res_v:+d} reserve",
                       delta_color="normal", border=True, help="used by the plan / available")
        else:
            col.metric(C.res_label(r), f"{avail}", border=True, help="available")

    rows = []
    for r in RESOURCES:
        avail = pool.get(r)
        used = totals.get(r) if plan else None
        res_v = reserve.get(r) if plan else None
        if res_v is None and used is not None and avail is not None:
            res_v = avail - used
        util = round(100.0 * used / avail, 1) if used is not None and avail else (0.0 if used == 0 else None)
        rows.append({"Resource": C.res_label(r), "Base pool": base.get(r), "Available": avail, "Used": used,
                     "Reserve": res_v, "Utilisation %": util})
    df = pd.DataFrame(rows)
    styler = df.style.map(
        lambda v: "color:#c62828;font-weight:700" if isinstance(v, (int, float)) and v < 0 else "",
        subset=["Reserve"],
    )
    st.dataframe(styler, hide_index=True, width="stretch", column_config={
        "Utilisation %": st.column_config.ProgressColumn("Utilisation", min_value=0, max_value=100,
                                                         format="%.0f%%"),
    })
    neg = [C.res_label(r) for r in RESOURCES if isinstance(reserve.get(r), (int, float)) and reserve.get(r) < 0]
    if neg:
        st.error("Over-allocated (negative reserve): " + ", ".join(neg))


def _protocol_checklist(proto: dict) -> None:
    rounds = proto.get("rounds_before_approval") or 0
    commits = proto.get("return_commitments") or 0
    items = [
        (rounds >= 3, f"≥ 3 negotiation rounds before approval (now {rounds})"),
        (bool(proto.get("revised_or_rejected")), "At least one proposal revised or rejected"),
        (bool(proto.get("sacrifice_refused")), "At least one Sacrifice request refused"),
        (commits >= 2, f"≥ 2 return commitments (now {commits})"),
    ]
    for ok, text in items:
        st.markdown(f"{C.tick(ok)} {text}")


def render_mission_control(state: dict) -> None:
    scn = state.get("scenario")
    if not scn:
        _empty_state()
        return
    plan = state.get("plan")
    left, right = st.columns([3, 2])
    with left:
        _event_card(scn)
    with right:
        with st.container(border=True):
            st.markdown("#### 🧭 Council status")
            st.markdown(f"**Round:** {scn.get('round', 0)} / {scn.get('max_rounds', '?')}"
                        + (f" &nbsp;·&nbsp; min rounds {scn['min_rounds']}" if scn.get("min_rounds") else ""))
            if plan:
                C.html_line("<b>Plan:</b>", C.badge(f"v{plan.get('version')}", "purple"),
                            C.plan_status_badge(plan.get("status")),
                            C.badge(C.modes_label(plan.get("modes")), "grey"))
            else:
                st.markdown("**Plan:** none drafted yet")
            pol = scn.get("policy") or {}
            C.html_line("<b>Policy:</b>", C.badge(f"Risk limit {pol.get('risk_limit', '?')}", "grey"),
                        C.badge(f"Max sacrifices {pol.get('max_sacrifices', '?')}", "grey"),
                        C.badge("Crisis Override ON", "red") if pol.get("crisis_override")
                        else C.badge("Crisis Override off", "green"))
            if plan:
                risk = plan.get("risk") or 0
                limit = pol.get("risk_limit") or (plan.get("policy") or {}).get("risk_limit") or 0
                frac = min(risk / limit, 1.0) if limit else 0.0
                st.progress(frac, text=f"Total risk {risk} / limit {limit}")
                if limit and risk > limit:
                    st.error(f"Risk {risk} exceeds limit {limit}")
    st.markdown("#### ⚡ Resources — available · used · reserve")
    _resource_section(scn, plan)

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.markdown("#### 📋 Protocol evidence")
            _protocol_checklist(state.get("protocol") or {})
    with c2:
        with st.container(border=True):
            st.markdown("#### 🏛️ Approved plan (this session)")
            ap = state.get("approved_plan")
            if ap:
                C.html_line(C.badge(f"v{ap.get('version')}", "purple"), C.plan_status_badge(ap.get("status")),
                            C.badge(C.modes_label(ap.get("modes")), "grey"),
                            C.badge(f"risk {ap.get('risk')}", "grey"))
                if ap.get("status") == "STALE":
                    st.warning("This plan was approved before the latest event and is now STALE.")
            else:
                st.caption("No plan approved yet in this session.")
    result = scn.get("result")
    if isinstance(result, dict) and isinstance(result.get("diagnosis"), dict):
        result = {**result["diagnosis"], **{k: v for k, v in result.items() if k != "diagnosis"}}
    if isinstance(result, dict) and result and scn.get("status") in ("INFEASIBLE", "DEADLOCK", "TIMEOUT"):
        with st.container(border=True):
            st.markdown("#### 🚫 Blocking constraints & requested change")
            for b in result.get("blocking") or []:
                st.markdown(f"- {C.md(b)}")
            for s in result.get("suggestions") or []:
                st.markdown(f"- 💡 **{C.md(s)}**")
            nearest = result.get("nearest")
            if isinstance(nearest, dict) and nearest:
                st.caption(f"Nearest combination: {nearest.get('label') or C.modes_label(nearest.get('modes'))}"
                           f" · shortfall {nearest.get('shortfall')}")
            with st.expander("Raw result"):
                st.json(scn.get("result"), expanded=False)


# ---------------------------------------------------------------------------
# Mode Board
# ---------------------------------------------------------------------------
def _dept_needles(dept: str, code: str | None) -> list[str]:
    needles = [dept, AGENT_LABELS.get(dept, dept)]
    if code:
        needles.append(code)
    return needles


def render_mode_board(state: dict, cur_msgs: list[dict]) -> None:
    scn = state.get("scenario")
    plan = state.get("plan")
    if not scn:
        _empty_state()
        return
    if not plan:
        st.info("No plan drafted yet for this scenario — the board fills in once the Commander drafts plan v1.")
    modes = (plan or {}).get("modes") or {}
    validation = (plan or {}).get("validation") or {}
    reasons = list(validation.get("reasons") or [])
    warnings = list(validation.get("warnings") or [])
    commitments = state.get("commitments") or []

    matched: set[str] = set()
    cols = st.columns(len(DEPARTMENTS))
    for col, dept in zip(cols, DEPARTMENTS):
        code = modes.get(dept)
        pkg = MODE_PACKAGES.get(code) if code else None
        with col, st.container(border=True):
            st.markdown(f"<div class='ares-card-title'>{C.AVATARS.get(dept, '')} {C.esc(C.agent_label(dept))}</div>",
                        unsafe_allow_html=True)
            C.html_line(C.mode_badge(code) if code else C.badge("no mode", "grey"),
                        C.badge(f"risk {pkg.risk}", "grey") if pkg else "")
            if pkg:
                vals = pkg.resources()
                st.markdown("| " + " | ".join(C.RES_SHORT[r] for r in RESOURCES) + " |\n|"
                            + "---|" * len(RESOURCES) + "\n| "
                            + " | ".join(str(vals[r]) for r in RESOURCES) + " |")
                st.caption("Fixed package values (P/W/O/R/B) — read-only")
                st.markdown(f"<span class='ares-muted'><b>Consequence:</b> {C.esc(pkg.consequence)}</span>",
                            unsafe_allow_html=True)
            # return commitments
            mine = [c for c in commitments if c.get("beneficiary") == dept or c.get("owner") == dept]
            st.markdown("**Return commitments**")
            if not mine:
                st.caption("none")
            for c in mine:
                direction = "⬅ receives" if c.get("beneficiary") == dept else "➡ gives"
                C.html_line(
                    C.badge(c.get("id"), "grey"),
                    C.badge(c.get("status"), C.COMMITMENT_COLORS.get(str(c.get("status")), "grey")),
                    f"<span class='ares-muted'>{direction}: {C.esc(C.agent_label(c.get('owner')))} → "
                    f"{C.esc(C.agent_label(c.get('beneficiary')))}</span>",
                )
                st.caption(f"{c.get('promise') or ''} · expires: {c.get('expiry') or '—'}")
            # conflicts
            needles = _dept_needles(dept, code)
            dept_conf = [r for r in reasons if C.mentions(r, needles)]
            dept_warn = [w for w in warnings if C.mentions(w, needles)]
            matched.update(dept_conf)
            matched.update(dept_warn)
            if dept_conf or dept_warn:
                st.markdown("**Conflicts**")
                for r in dept_conf:
                    st.markdown(f"<span style='color:#c62828'>❌ {C.esc(r)}</span>", unsafe_allow_html=True)
                for w in dept_warn:
                    st.markdown(f"<span style='color:#b26a00'>⚠️ {C.esc(w)}</span>", unsafe_allow_html=True)
            # latest stance from transcript
            last = next((m for m in reversed(cur_msgs) if m.get("sender") == dept), None)
            if last:
                st.markdown("**Latest stance**")
                C.html_line(C.msg_type_badge(last.get("msg_type")), C.badge(f"R{last.get('round')}", "grey"),
                            C.source_badge(last.get("source"), last.get("provider"), last.get("model"),
                                           last.get("fallback_reason")))
                st.caption(C.short(last.get("content"), 200))

    colony = [r for r in reasons + warnings if r not in matched]
    if colony:
        st.markdown("#### Colony-wide conflicts")
        for r in colony:
            (st.error if r in reasons else st.warning)(r)

    st.markdown("#### 📦 Mode package reference (fixed, PDF p.4)")
    selected = set(modes.values())
    rows = []
    for p in MODE_PACKAGES.values():
        rows.append({"Selected": "✔" if p.code in selected else "", "Code": p.code,
                     "Department": C.agent_label(p.department), "Tier": p.tier.title(),
                     **{C.res_label(r): getattr(p, r) for r in RESOURCES}, "Risk": p.risk,
                     "Consequence": p.consequence})
    df = pd.DataFrame(rows)

    def _hl(row: pd.Series) -> list[str]:
        if row["Selected"]:
            hexc = C.TIER_HEX.get(row["Tier"].upper(), "#5f6368")
            return [f"background-color:{hexc}33;font-weight:700"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(_hl, axis=1), hide_index=True, width="stretch")

    if commitments:
        st.markdown("#### 🤝 All commitments in this scenario")
        cdf = pd.DataFrame([{
            "ID": c.get("id"), "Owner": C.agent_label(c.get("owner")),
            "Beneficiary": C.agent_label(c.get("beneficiary")), "Status": c.get("status"),
            "Kind": c.get("kind"), "Resource": C.res_label(c.get("resource")) if c.get("resource") else "",
            "Amount": c.get("amount"), "Source": c.get("source") or "", "Claim": c.get("claim") or "",
            "Promise": c.get("promise"), "Expiry": c.get("expiry"), "Offered R": c.get("round_offered"),
            "Resolved R": c.get("round_resolved"), "Template": c.get("template_id") or "",
            "Note": c.get("note") or "",
        } for c in commitments])
        st.dataframe(cdf, hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Council Transcript
# ---------------------------------------------------------------------------
def _render_message(m: dict) -> None:
    sender = m.get("sender")
    with st.chat_message(C.agent_label(sender), avatar=C.AVATARS.get(str(sender), "💬")):
        rec = m.get("recipient")
        parts = [f"<b>{C.esc(C.agent_label(sender))}</b>"]
        if rec and str(rec).upper() not in ("ALL", "COUNCIL", "*"):
            parts.append(f"<span class='ares-muted'>→ {C.esc(C.agent_label(rec))}</span>")
        parts += [
            C.msg_type_badge(m.get("msg_type")),
            C.badge(f"R{m.get('round')}", "grey") if m.get("round") is not None else "",
            C.badge(f"v{m.get('plan_version')}", "purple") if m.get("plan_version") is not None else "",
            C.source_badge(m.get("source"), m.get("provider"), m.get("model"), m.get("fallback_reason")),
            f"<span class='ares-muted'>{C.esc(m.get('created_at') or '')}</span>",
        ]
        C.html_line(*parts)
        st.markdown(C.md(m.get("content") or ""))
        if (m.get("source") or "").lower() == "rule-based" and m.get("fallback_reason"):
            st.caption(f"⚠️ Rule-based fallback reason: {m.get('fallback_reason')}")
        if m.get("payload"):
            with st.expander("Payload JSON"):
                st.json(m.get("payload"), expanded=True)


def render_transcript(state: dict, messages: list[dict]) -> None:
    if not messages:
        st.info("The persistent transcript is empty. Start a baseline crisis to begin the council record.")
        return
    scenarios = {s.get("id"): s for s in state.get("scenarios") or []}
    sessions = sorted({m.get("session_id") for m in messages if m.get("session_id") is not None}, reverse=True)
    scen_ids = sorted({m.get("scenario_id") for m in messages if m.get("scenario_id") is not None}, reverse=True)
    senders = sorted({str(m.get("sender")) for m in messages if m.get("sender")})
    types = sorted({str(m.get("msg_type")) for m in messages if m.get("msg_type")})

    f1, f2, f3, f4 = st.columns(4)
    sel_sess = f1.multiselect("Session", sessions, key="tx_sess", format_func=lambda s: f"Session #{s}")
    sel_scn = f2.multiselect("Scenario", scen_ids, key="tx_scn",
                             format_func=lambda i: _scenario_title(scenarios.get(i)) if i in scenarios else f"#{i}")
    sel_agent = f3.multiselect("Agent", senders, key="tx_agent", format_func=C.agent_label)
    sel_type = f4.multiselect("Message type", types, key="tx_type")
    o1, o2, o3 = st.columns([1, 1, 2])
    table_view = o1.toggle("Table view", key="tx_table")
    only_fb = o2.toggle("Only rule-based fallback", key="tx_fb")
    limit = o3.select_slider("Max messages rendered (chat view)", options=[100, 250, 500, 1000, 5000], value=250,
                             key="tx_limit")

    filt = [m for m in messages
            if (not sel_sess or m.get("session_id") in sel_sess)
            and (not sel_scn or m.get("scenario_id") in sel_scn)
            and (not sel_agent or str(m.get("sender")) in sel_agent)
            and (not sel_type or str(m.get("msg_type")) in sel_type)
            and (not only_fb or (m.get("source") or "").lower() == "rule-based")]
    n_fb = sum(1 for m in filt if (m.get("source") or "").lower() == "rule-based")
    n_llm = sum(1 for m in filt if (m.get("source") or "").lower() == "llm")
    st.caption(f"{len(filt)} of {len(messages)} messages · {n_llm} LLM · {n_fb} rule-based fallback · "
               f"{len(filt) - n_llm - n_fb} system/validator")
    if not filt:
        st.info("No messages match the filters.")
        return

    if table_view:
        df = pd.DataFrame([{
            "ID": m.get("id"), "Time": m.get("created_at"), "Session": m.get("session_id"),
            "Scenario": m.get("scenario_id"), "Round": m.get("round"), "Plan v": m.get("plan_version"),
            "Sender": C.agent_label(m.get("sender")), "Recipient": C.agent_label(m.get("recipient"))
            if m.get("recipient") else "", "Type": m.get("msg_type"),
            "Source": "RULE-BASED FALLBACK" if (m.get("source") or "").lower() == "rule-based"
            else str(m.get("source") or "").upper(),
            "Provider/model": "/".join(str(x) for x in (m.get("provider"), m.get("model")) if x),
            "Fallback reason": m.get("fallback_reason") or "", "Content": m.get("content"),
        } for m in filt])
        st.dataframe(df, hide_index=True, width="stretch", height=560)
        return

    filt = sorted(filt, key=lambda m: (m.get("id") or 0))
    if len(filt) > limit:
        st.warning(f"Showing the latest {limit} messages — narrow the filters or use Table view for all.")
        filt = filt[-limit:]
    groups: "OrderedDict[Any, list[dict]]" = OrderedDict()
    for m in sorted(filt, key=lambda m: (-(m.get("scenario_id") or 0), m.get("id") or 0)):
        groups.setdefault(m.get("scenario_id"), []).append(m)
    for sid, msgs in groups.items():
        scn = scenarios.get(sid) or {}
        with st.container(border=True):
            C.html_line(f"<span class='ares-card-title'>{C.esc(_scenario_title(scn) if scn else f'Scenario #{sid}')}"
                        "</span>",
                        C.status_badge(scn.get("status")) if scn else "",
                        C.badge(f"Session #{msgs[0].get('session_id')}", "grey"),
                        C.badge(f"provider {scn.get('provider')}", "blue") if scn.get("provider") else "")
            if scn.get("result_reason"):
                st.caption(scn.get("result_reason"))
            current_round = object()
            for m in msgs:
                if m.get("round") != current_round:
                    current_round = m.get("round")
                    st.markdown(f"**— Round {current_round if current_round is not None else '·'} —**")
                _render_message(m)


# ---------------------------------------------------------------------------
# Validation & Votes
# ---------------------------------------------------------------------------
def render_validation(state: dict, cur_msgs: list[dict]) -> None:
    scn = state.get("scenario")
    plans = sorted(state.get("plans") or [], key=lambda p: p.get("version") or 0)
    if not scn:
        _empty_state()
        return
    if not plans:
        st.info("No plan versions yet for this scenario.")
        return
    latest = state.get("plan") or plans[-1]
    versions = [p.get("version") for p in plans]
    idx = versions.index(latest.get("version")) if latest.get("version") in versions else len(versions) - 1
    ver = st.selectbox("Inspect plan version", versions, index=idx, key=f"val_ver_{scn.get('id')}",
                       format_func=lambda v: f"v{v}" + ("  (current)" if v == latest.get("version") else ""))
    plan = next(p for p in plans if p.get("version") == ver)
    validation = plan.get("validation") or {}
    vstatus = validation.get("status") or "—"

    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"#### Validator — plan v{plan.get('version')} ({C.modes_label(plan.get('modes'))})")
        C.big_badge(vstatus, "green" if vstatus == "PASS" else "red" if vstatus == "FAIL" else "grey")
        C.html_line(C.plan_status_badge(plan.get("status")), C.badge(f"round {plan.get('round')}", "grey"),
                    C.badge(f"risk {plan.get('risk')}", "grey"))
        for chk in validation.get("checks") or []:
            st.markdown(f"{C.check_mark(chk.get('passed'))} **{C.md(chk.get('name'))}** — {C.md(chk.get('detail'))}")
        for r in validation.get("reasons") or []:
            st.error(r)
        for w in validation.get("warnings") or []:
            st.warning(w)
    with right:
        with st.container(border=True):
            st.markdown("#### 🔐 Approval gate")
            st.markdown(
                "A plan is approved **only** when all of these hold (enforced by the validator, "
                "the Commander cannot bypass):\n"
                "1. Validator **PASS** on this exact plan version\n"
                "2. **4 / 4 ACCEPT** votes from the departments on this version\n"
                "3. It is the **current** version — any plan change clears earlier votes\n"
                "4. Minimum negotiation rounds reached (≥ 3 baseline, ≥ 2 visible rounds after an event)"
            )
            acc, rej = _vote_tally(plan.get("votes"), plan.get("version"))
            st.markdown(
                f"**This version:** validator {vstatus} · ACCEPT {acc}/4 · REJECT {rej} · round {plan.get('round')}"
                f" · status **{plan.get('status')}**"
            )
            gate_msgs = [m for m in cur_msgs if m.get("msg_type") in ("APPROVAL", "APPROVAL_BLOCKED")
                         and m.get("plan_version") in (None, plan.get("version"))]
            if gate_msgs:
                g = gate_msgs[-1]
                (st.success if g.get("msg_type") == "APPROVAL" else st.warning)(
                    f"{g.get('msg_type')} (R{g.get('round')}): {C.short(g.get('content'), 400)}")
            st.caption("The plan status above is the service's authoritative decision; the UI does not compute it.")

    st.markdown(f"#### 🗳️ Votes on plan v{plan.get('version')}")
    votes = plan.get("votes") or []
    if votes:
        vdf = pd.DataFrame([{
            "Agent": C.agent_label(v.get("agent")),
            "Decision": ("✅ ACCEPT" if v.get("decision") == "ACCEPT" else "❌ REJECT"
                         if v.get("decision") == "REJECT" else v.get("decision")),
            "Reason": v.get("reason"), "Plan v": v.get("plan_version"),
            "Source": "RULE-BASED FALLBACK" if (v.get("source") or "").lower() == "rule-based"
            else str(v.get("source") or "").upper(),
            "Fallback reason": v.get("fallback_reason") or "",
            **({"Valid": "yes" if v.get("valid") not in (0, False) else "cleared"} if "valid" in v else {}),
        } for v in votes])
        st.dataframe(vdf, hide_index=True, width="stretch")
    else:
        st.caption("No votes recorded for this version (votes are only called on a validator-PASS plan).")

    st.markdown("#### 🗂️ Plan version history")
    rows, prev = [], None
    for p in plans:
        val = p.get("validation") or {}
        acc, rej = _vote_tally(p.get("votes"), p.get("version"))
        failed = [c.get("name") for c in val.get("checks") or [] if not c.get("passed")]
        rows.append({"Version": p.get("version"), "Round": p.get("round"), "Modes": C.modes_label(p.get("modes")),
                     "Changes vs previous": _mode_diff(prev, p.get("modes")), "Risk": p.get("risk"),
                     "Status": p.get("status"), "Validation": val.get("status"),
                     "Votes (A/R)": f"{acc}/{rej}", "Failed checks": ", ".join(map(str, failed))})
        prev = p.get("modes")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def render_scenarios(state: dict) -> None:
    scns = state.get("scenarios") or []
    if not scns:
        st.info("No scenarios recorded yet.")
        return
    df = pd.DataFrame([{
        "ID": s.get("id"), "Session": s.get("session_id"), "Kind": s.get("kind"), "Name": s.get("name"),
        "Status": s.get("status"), "Rounds": f"{s.get('round', 0)}/{s.get('max_rounds', '?')}",
        "Provider": s.get("provider"), "Pool": C.pool_short(s.get("pool")),
        "Base pool": C.pool_short(s.get("base_pool")), "Policy": C.policy_short(s.get("policy")),
        "Result reason": s.get("result_reason"), "Started": s.get("started_at"), "Finished": s.get("finished_at"),
    } for s in scns])

    def _color(v: Any) -> str:
        hexc = C.STATUS_BG.get(C.STATUS_COLORS.get(str(v), ""), "")
        return f"color:{hexc};font-weight:700" if hexc else ""

    st.dataframe(df.style.map(_color, subset=["Status"]), hide_index=True, width="stretch")
    ids = [s.get("id") for s in scns]
    sel = st.selectbox("Inspect scenario", ids, key="scn_inspect",
                       format_func=lambda i: _scenario_title(next((s for s in scns if s.get("id") == i), None)))
    s = next((x for x in scns if x.get("id") == sel), None)
    if s:
        C.html_line(C.status_badge(s.get("status")), C.badge(C.policy_short(s.get("policy")), "mars"))
        st.caption(s.get("result_reason") or "")
        st.json(s, expanded=False)
