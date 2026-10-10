"""Judge Controls (sidebar) and the live-progress runner.

Long-running service calls (start baseline / inject event) are queued by a button
callback and executed on the next script run in the main area inside
`st.status(...)`, so every sidebar control can be rendered *disabled* while the
council is in session.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from ares.models import BASELINE_POOL, MODE_PACKAGES, RESOURCES
from ares.ui import components as C

PENDING = "ares_pending"
FLASH = "ares_flash"
PREVIEW = "ares_preview"
EXPORT = "ares_export"

_STARTER_EVENT_PATH = Path(__file__).resolve().parents[2] / "starter_kit" / "event_injection_format.json"
# Verbatim copy of starter_kit/event_injection_format.json (the official judge format), used only
# if that file is missing. It is an input example for the JSON box, not a hardcoded run.
_STARTER_EVENT_FALLBACK = {
    "event_id": "CRISIS_002",
    "event_name": "Solar Aftershock",
    "trigger_time": "Hour 14",
    "description": "Secondary solar flare detected. Power generation capacity drops by 30% for next 6 hours.",
    "impact": {
        "power_reduction_percent": 30,
        "duration_hours": 6,
        "affected_systems": ["solar_array_A", "solar_array_B"],
    },
    "requires_replan": True,
    "message_to_commander": "ALERT: Solar Aftershock detected. Immediate resource reallocation required. "
                            "Council must reconvene.",
}

_MIME = {".json": "application/json", ".csv": "text/csv", ".txt": "text/plain", ".md": "text/markdown",
         ".jsonl": "application/json", ".html": "text/html"}


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------
def is_running() -> bool:
    return PENDING in st.session_state


def flash(kind: str, msg: str) -> None:
    st.session_state.setdefault(FLASH, []).append((kind, msg))


def render_flash() -> None:
    for kind, msg in st.session_state.pop(FLASH, []):
        getattr(st, kind if kind in ("error", "warning", "success", "info") else "info")(msg)


def _starter_event_text() -> str:
    try:
        return json.dumps(json.loads(_STARTER_EVENT_PATH.read_text(encoding="utf-8")), indent=2)
    except Exception:
        return json.dumps(_STARTER_EVENT_FALLBACK, indent=2)


def _init_defaults() -> None:
    ss = st.session_state
    for r in RESOURCES:
        ss.setdefault(f"ares_pool_{r}", int(BASELINE_POOL[r]))
        ss.setdefault(f"ares_ev_d_{r}", 0)
    ss.setdefault("ares_max_rounds", 6)
    ss.setdefault("ares_event_max_rounds", 5)
    ss.setdefault("ares_ev_name", "Judge event")
    ss.setdefault("ares_ev_desc", "")
    ss.setdefault("ares_ev_forbidden", [])
    ss.setdefault("ares_ev_json", _starter_event_text())
    ss.setdefault("ares_ev_text", "")
    ss.setdefault("ares_wipe_confirm", False)


def _current_pool() -> dict[str, int]:
    return {r: int(st.session_state.get(f"ares_pool_{r}", BASELINE_POOL[r]) or 0) for r in RESOURCES}


def _restore_pool() -> None:
    for r in RESOURCES:
        st.session_state[f"ares_pool_{r}"] = int(BASELINE_POOL[r])


def _clear_form() -> None:
    for r in RESOURCES:
        st.session_state[f"ares_ev_d_{r}"] = 0
    st.session_state["ares_ev_forbidden"] = []


# ---------------------------------------------------------------------------
# Event payload builders (one per input method)
# ---------------------------------------------------------------------------
def build_event_payload(method: str) -> tuple[dict | str | None, str | None]:
    ss = st.session_state
    if method == "form":
        changes = {r: int(ss.get(f"ares_ev_d_{r}", 0) or 0) for r in RESOURCES}
        changes = {r: v for r, v in changes.items() if v != 0}
        forbidden = list(ss.get("ares_ev_forbidden", []) or [])
        if not changes and not forbidden:
            return None, "Form: enter at least one non-zero resource change or a forbidden mode."
        payload: dict[str, Any] = {
            "name": (ss.get("ares_ev_name") or "Judge event").strip(),
            "description": (ss.get("ares_ev_desc") or "").strip(),
            "changes": changes,
        }
        if forbidden:
            payload["forbidden_modes"] = forbidden
        return payload, None
    if method == "json":
        raw = (ss.get("ares_ev_json") or "").strip()
        if not raw:
            return None, "JSON: paste an event object first."
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"JSON: invalid JSON — {exc}"
        if not isinstance(data, dict):
            return None, "JSON: the event must be a JSON object ({...})."
        return data, None
    if method == "text":
        raw = (ss.get("ares_ev_text") or "").strip()
        if not raw:
            return None, "Text: describe the event first (e.g. 'Available Power decreases by 4')."
        return raw, None
    return None, f"Unknown input method {method!r}"


_METHOD_LABELS = {"form": "Form", "json": "Pasted JSON", "text": "Free text"}


# ---------------------------------------------------------------------------
# Callbacks (run before the next script run)
# ---------------------------------------------------------------------------
def _cb_start(provider: str) -> None:
    st.session_state[PENDING] = {
        "kind": "baseline",
        "pool": _current_pool(),
        "provider": provider,
        "max_rounds": int(st.session_state.get("ares_max_rounds", 6)),
        "cosign": bool(st.session_state.get("ares_cosign", False)),
    }


def _cb_preview(svc: Any, method: str) -> None:
    payload, err = build_event_payload(method)
    if err:
        st.session_state[PREVIEW] = {"method": method, "error": err}
        return
    try:
        result = svc.preview_event(payload)
        st.session_state[PREVIEW] = {"method": method, "payload": payload, "result": result}
    except Exception as exc:  # parse errors etc. are shown, never crash
        st.session_state[PREVIEW] = {"method": method, "error": f"Could not interpret event: {exc}"}


def _cb_inject(method: str, provider: str) -> None:
    payload, err = build_event_payload(method)
    if err:
        flash("error", err)
        return
    st.session_state[PENDING] = {
        "kind": "event",
        "event": payload,
        "method": method,
        "provider": provider,
        "max_rounds": int(st.session_state.get("ares_event_max_rounds", 5)),
        "cosign": bool(st.session_state.get("ares_cosign", False)),
    }


def _cb_reset(svc: Any) -> None:
    try:
        svc.reset_session()
        flash("success", "New session started. Earlier scenarios remain in the transcript history.")
    except Exception as exc:
        flash("error", f"Reset failed: {exc}")
    st.session_state.pop(PREVIEW, None)
    st.session_state.pop(EXPORT, None)


def _cb_wipe(svc: Any) -> None:
    try:
        svc.wipe_all()
        flash("success", "All history wiped (database cleared).")
    except Exception as exc:
        flash("error", f"Wipe failed: {exc}")
    st.session_state["ares_wipe_confirm"] = False
    st.session_state.pop(PREVIEW, None)
    st.session_state.pop(EXPORT, None)


def _cb_export(svc: Any) -> None:
    try:
        st.session_state[EXPORT] = svc.export_bundle()
    except Exception as exc:
        st.session_state.pop(EXPORT, None)
        flash("error", f"Export failed: {exc}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner="Checking LLM providers…")
def _cached_providers(_svc: Any) -> list[dict]:
    # availability checks can probe the network (e.g. Ollama); cache briefly so reruns stay snappy
    return list(_svc.providers() or [])


def _provider_select(svc: Any, running: bool) -> str:
    try:
        providers = _cached_providers(svc)
    except Exception as exc:
        st.error(f"providers() failed: {exc}")
        providers = []
    if not providers:
        providers = [{"name": "rule-based", "model": "", "available": True, "note": "No provider list returned."}]
    by_name = {p.get("name"): p for p in providers}
    names = list(by_name)
    default = next((i for i, n in enumerate(names) if by_name[n].get("available") and n != "rule-based"), None)
    if default is None:
        default = names.index("rule-based") if "rule-based" in names else 0

    def fmt(n: str) -> str:
        p = by_name.get(n, {})
        mark = "✅" if p.get("available") else "⛔"
        model = f" · {p.get('model')}" if p.get("model") else ""
        return f"{mark} {n}{model}"

    if st.session_state.get("ares_provider") not in names:
        st.session_state.pop("ares_provider", None)
    name = st.selectbox("Negotiation provider", names, index=default, format_func=fmt, key="ares_provider",
                        disabled=running, help="✅ configured / reachable · ⛔ not available (missing key or offline)")
    p = by_name.get(name, {})
    if name == "rule-based":
        st.warning("Rule-based mode: no LLM is called. Every agent turn is labelled **RULE-BASED FALLBACK**.")
    elif not p.get("available"):
        env = f" Set `{p.get('env_var')}` in `.env` and restart, or" if p.get("env_var") else ""
        note = str(p.get("note") or "").rstrip(". ")
        st.error(f"⛔ **{name}** is not available (missing key or unreachable).{env} pick a ✅ provider. "
                 "Start / Inject are disabled for it." + (f"  \n_{note}_" if note else ""))
    elif p.get("note"):
        st.caption(p.get("note"))
    with st.expander("All providers", expanded=False):
        st.button("Re-check availability", key="ares_prov_refresh", on_click=_cached_providers.clear,
                  disabled=running)
        for prov in providers:
            st.markdown(f"{'✅' if prov.get('available') else '⛔'} **{C.md(prov.get('name'))}** "
                        f"{C.md(prov.get('model') or '')}  \n"
                        f"<span class='ares-muted'>{C.esc(prov.get('note') or '')}</span>",
                        unsafe_allow_html=True)
    st.session_state["ares_provider_blocked"] = name != "rule-based" and not p.get("available")
    return name


def _blocked() -> bool:
    return bool(st.session_state.get("ares_provider_blocked"))


def _event_buttons(svc: Any, method: str, provider: str, running: bool) -> None:
    st.button("🔍 Preview interpretation", key=f"ares_prev_{method}", on_click=_cb_preview, args=(svc, method),
              disabled=running, width="stretch")
    st.button("⚡ Inject event & renegotiate", key=f"ares_inj_{method}", on_click=_cb_inject,
              args=(method, provider), type="primary", disabled=running or _blocked(), width="stretch")


def _render_preview(state: dict) -> None:
    pv = st.session_state.get(PREVIEW)
    if not pv:
        return
    with st.container(border=True):
        st.markdown(f"**Preview — {_METHOD_LABELS.get(pv.get('method'), pv.get('method'))}**")
        if pv.get("error"):
            st.error(pv["error"])
        else:
            res = pv.get("result") or {}
            ev = res.get("event") or {}
            st.markdown(f"**{C.md(ev.get('name') or 'Event')}**"
                        + (f"  \n{C.md(ev.get('description'))}" if ev.get("description") else ""))
            for line in res.get("changes") or []:
                st.markdown(f"- {C.md(line)}")
            if ev.get("forbidden_modes"):
                st.markdown("Forbidden modes: " + ", ".join(ev["forbidden_modes"]))
            if not res.get("changes") and not ev.get("forbidden_modes"):
                st.warning("No resource change or forbidden mode was recognised. Injecting it will still mark the "
                           "approved plan STALE and start a renegotiation. Use the Form tab for exact deltas.")
            notes = ev.get("interpretation_notes") or []
            if notes:
                st.caption("Interpretation notes:")
                for n in notes:
                    st.caption(f"• {n}")
            new_pool = res.get("new_pool") or {}
            scn = (state or {}).get("scenario") or {}
            cur = res.get("pool") or scn.get("pool") or _current_pool()
            rows = []
            for r in RESOURCES:
                before, after = cur.get(r), new_pool.get(r)
                delta = (after - before) if isinstance(after, int) and isinstance(before, int) else None
                rows.append({"Resource": C.res_label(r), "Now": before, "After": after,
                             "Δ": f"{delta:+d}" if delta is not None else ""})
            st.dataframe(rows, hide_index=True, width="stretch")
            st.caption("Preview only — nothing changes until you press **Inject event & renegotiate**.")
        st.button("Clear preview", key="ares_prev_clear", on_click=lambda: st.session_state.pop(PREVIEW, None))


def _render_exports(svc: Any, running: bool) -> None:
    st.button("Prepare export files", key="ares_export_btn", on_click=_cb_export, args=(svc,),
              disabled=running, width="stretch",
              help="Builds the JSON / CSV / text bundle from the persistent store.")
    bundle = st.session_state.get(EXPORT)
    if not bundle:
        return
    if not isinstance(bundle, dict):
        st.error("export_bundle() returned an unexpected value.")
        return
    for i, (fname, data) in enumerate(bundle.items()):
        ext = Path(str(fname)).suffix.lower()
        st.download_button(f"⬇ {fname}", data=data if isinstance(data, (str, bytes)) else C.to_json(data),
                           file_name=str(fname), mime=_MIME.get(ext, "application/octet-stream"),
                           key=f"ares_dl_{i}_{fname}", on_click="ignore", width="stretch")


def render_sidebar(svc: Any, state: dict, running: bool) -> None:
    _init_defaults()
    st.header("🛰️ Judge Controls")
    if running:
        st.info("Council in session — controls are locked until the run finishes.")

    provider = _provider_select(svc, running)

    # -- Baseline ------------------------------------------------------------
    st.subheader("1 · Resource pool")
    cols = st.columns(2)
    for i, r in enumerate(RESOURCES):
        cols[i % 2].number_input(C.res_label(r), min_value=0, max_value=100000, step=1,
                                 key=f"ares_pool_{r}", disabled=running)
    c1, c2 = st.columns([1, 1])
    c1.number_input("Max rounds", min_value=1, max_value=20, step=1, key="ares_max_rounds", disabled=running,
                    help="Baseline negotiation round budget (approval needs ≥ 3 rounds).")
    c2.button("Baseline values", key="ares_restore_pool", on_click=_restore_pool, disabled=running,
              help="Restore 79 / 52 / 59 / 26 / 17", width="stretch")
    st.checkbox("Human-in-the-loop: judge co-signs plans with risk > 20", key="ares_cosign", disabled=running,
                help="Bonus option (starter checklist). A plan that already passed the validator and 4 ACCEPT votes "
                     "waits for your Co-sign / Veto when its total risk is above 20. Off by default.")
    st.button("🚀 Start baseline crisis", key="ares_start", type="primary", on_click=_cb_start,
              args=(provider,), disabled=running or _blocked(), width="stretch")

    # -- Event injection ------------------------------------------------------
    st.subheader("2 · Crisis event injection")
    if not (state or {}).get("scenario"):
        st.caption("Tip: start (and ideally approve) the baseline first, then inject an event.")
    st.number_input("Event max rounds", min_value=1, max_value=20, step=1, key="ares_event_max_rounds",
                    disabled=running)
    t_form, t_json, t_text = st.tabs(["Form", "Paste JSON", "Free text"], key="event_input_tabs", on_change="rerun")
    with t_form:
        st.text_input("Event name", key="ares_ev_name", disabled=running)
        st.text_area("Description", key="ares_ev_desc", height=68, disabled=running)
        st.caption("Signed change per resource (negative = loss)")
        fc = st.columns(2)
        for i, r in enumerate(RESOURCES):
            fc[i % 2].number_input(f"Δ {C.res_label(r)}", min_value=-100000, max_value=100000, step=1,
                                   key=f"ares_ev_d_{r}", disabled=running)
        st.multiselect(
            "Forbidden modes (optional)", list(MODE_PACKAGES), key="ares_ev_forbidden", disabled=running,
            format_func=lambda c: f"{c} · {MODE_PACKAGES[c].tier.title()} ({C.agent_label(MODE_PACKAGES[c].department)})",
        )
        st.button("Clear form deltas", key="ares_ev_clear", on_click=_clear_form, disabled=running)
        _event_buttons(svc, "form", provider, running)
    with t_json:
        st.text_area("Event JSON (official starter format accepted)", key="ares_ev_json", height=260,
                     disabled=running)
        st.button("Reset to official example", key="ares_ev_json_reset", disabled=running,
                  on_click=lambda: st.session_state.__setitem__("ares_ev_json", _starter_event_text()))
        _event_buttons(svc, "json", provider, running)
    with t_text:
        st.text_area("Describe the event", key="ares_ev_text", height=110, disabled=running,
                     placeholder="e.g. Dust storm: Available Power decreases by 4 and bandwidth drops by 30%")
        _event_buttons(svc, "text", provider, running)
    _render_preview(state)

    # -- Session -------------------------------------------------------------
    st.subheader("3 · Session")
    st.button("↺ Reset session (keep history)", key="ares_reset", on_click=_cb_reset, args=(svc,),
              disabled=running, width="stretch")
    confirm = st.checkbox("I understand: delete ALL scenarios, transcripts and plans", key="ares_wipe_confirm",
                          disabled=running)
    st.button("🗑 Wipe all history", key="ares_wipe", on_click=_cb_wipe, args=(svc,),
              disabled=running or not confirm, width="stretch")

    # -- Export --------------------------------------------------------------
    st.subheader("4 · Export")
    _render_exports(svc, running)


# ---------------------------------------------------------------------------
# Live progress runner
# ---------------------------------------------------------------------------
def _live_line(msg: dict) -> str:
    sender = msg.get("sender")
    parts = [
        f"{C.AVATARS.get(str(sender), '💬')} <b>{C.esc(C.agent_label(sender))}</b>",
        C.msg_type_badge(msg.get("msg_type")),
        C.badge(f"R{msg.get('round')}", "grey") if msg.get("round") is not None else "",
        C.badge(f"v{msg.get('plan_version')}", "purple") if msg.get("plan_version") is not None else "",
        C.source_badge(msg.get("source"), msg.get("provider"), msg.get("model"), msg.get("fallback_reason")),
        C.esc(C.short(msg.get("content"), 240)),
    ]
    return " ".join(p for p in parts if p)


def run_pending(svc: Any) -> None:
    """Execute the queued long-running action with live progress, then rerun."""
    action = st.session_state.pop(PENDING, None)  # pop first: an interrupted run is never replayed
    if not action:
        return
    is_event = action.get("kind") == "event"
    title = "Crisis event — council reconvenes…" if is_event else "Council in session…"
    counter = {"n": 0}
    with st.status(title, expanded=True) as status:
        st.caption(f"Provider: {action.get('provider')} · max rounds: {action.get('max_rounds')}"
                   + (f" · input: {_METHOD_LABELS.get(action.get('method'), '')}" if is_event else ""))

        def on_message(msg: dict) -> None:
            counter["n"] += 1
            try:
                status.markdown(_live_line(msg or {}), unsafe_allow_html=True)
                status.update(label=f"{title} round {msg.get('round')} · plan v{msg.get('plan_version')} · "
                                    f"{counter['n']} messages")
            except Exception:  # a display hiccup must never break the negotiation
                pass

        try:
            if is_event:
                result = svc.inject_event(action["event"], action["provider"],
                                          max_rounds=action["max_rounds"], on_message=on_message,
                                          require_cosign=action.get("cosign", False))
            else:
                result = svc.start_baseline(action["pool"], action["provider"],
                                            max_rounds=action["max_rounds"], on_message=on_message,
                                            require_cosign=action.get("cosign", False))
            result = result or {}
            status.update(label=f"Council adjourned — {result.get('status', '?')}", state="complete",
                          expanded=False)
            kind = "success" if result.get("status") == "APPROVED" else "warning"
            flash(kind, f"Scenario #{result.get('id', '?')} finished: **{result.get('status', '?')}** — "
                        f"{result.get('result_reason') or ''}")
        except Exception as exc:
            status.update(label="Council run failed", state="error", expanded=True)
            flash("error", f"The run stopped with an error: {type(exc).__name__}: {exc}")
    st.session_state.pop(EXPORT, None)
    st.session_state.pop(PREVIEW, None)
    st.rerun()


# ---------------------------------------------------------------------------
# Human-in-the-loop co-sign panel (bonus; only shown when a plan awaits it)
# ---------------------------------------------------------------------------
def _cb_cosign(svc: Any, approve: bool) -> None:
    try:
        result = svc.cosign(approve, st.session_state.get("ares_cosign_note", ""))
        flash("success" if approve else "warning",
              f"Scenario #{result.get('id')} -> **{result.get('status')}** — {result.get('result_reason') or ''}")
    except Exception as exc:
        flash("error", f"Co-sign failed: {exc}")


def render_cosign_panel(svc: Any, state: dict, running: bool) -> None:
    scenario = (state or {}).get("scenario") or {}
    plan = (state or {}).get("plan") or {}
    if scenario.get("status") != "AWAITING_COSIGN" or plan.get("status") != "AWAITING_COSIGN":
        return
    with st.container(border=True):
        st.markdown(f"### ✍️ Judge co-sign required — plan v{plan.get('version')}")
        st.write(f"The plan passed the deterministic validator and received 4/4 ACCEPT votes, but its total risk "
                 f"is {plan.get('risk')} (> 20). It takes effect only after your co-sign. This is a human approval "
                 f"step, not an agent message.")
        st.text_input("Optional note", key="ares_cosign_note", disabled=running)
        c1, c2 = st.columns(2)
        c1.button("✅ Co-sign plan", key="ares_cosign_yes", type="primary", on_click=_cb_cosign, args=(svc, True),
                  disabled=running, width="stretch")
        c2.button("⛔ Veto plan", key="ares_cosign_no", on_click=_cb_cosign, args=(svc, False), disabled=running,
                  width="stretch")
