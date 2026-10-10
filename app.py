"""ARES Accord — Mars Colony Council: judge dashboard.

Run:  .venv/Scripts/python -m streamlit run app.py

The dashboard only calls the Service API (`ares.service`, CONTRACT §6). It never
computes approval, never edits mode packages and never fabricates messages.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="ARES Accord — Mars Colony Council",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ares.ui import components as C  # noqa: E402
from ares.ui import controls, views  # noqa: E402

try:
    from ares.service import get_service  # noqa: E402
    _IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # show a clear error instead of a traceback
    get_service = None  # type: ignore[assignment]
    _IMPORT_ERROR = _exc

TITLE = "ARES Accord — Mars Colony Council"


@st.cache_resource(show_spinner="Connecting to the council service…")
def load_service():
    return get_service()


def main() -> None:
    C.inject_css()
    if get_service is None:
        st.title(TITLE)
        st.error(f"The council service could not be imported: {type(_IMPORT_ERROR).__name__}: {_IMPORT_ERROR}")
        st.stop()
    try:
        svc = load_service()
    except Exception as exc:
        st.title(TITLE)
        st.error(f"The council service failed to start: {type(exc).__name__}: {exc}")
        st.stop()

    running = controls.is_running()

    try:
        state = svc.state() or {}
    except Exception as exc:
        st.error(f"Could not read the dashboard state: {type(exc).__name__}: {exc}")
        state = {}
    try:
        messages = list(svc.transcript() or [])
    except Exception as exc:
        st.error(f"Could not read the transcript: {type(exc).__name__}: {exc}")
        messages = []

    with st.sidebar:
        controls.render_sidebar(svc, state, running)

    st.title(TITLE)
    st.caption("Five agents — Commander, Life Support, Medical, Food Production, Engineering — negotiate one "
               "fixed mode package per department. A deterministic validator and four votes decide approval.")

    if running:
        controls.run_pending(svc)  # streams messages live, then reruns
        return

    controls.render_flash()
    if state.get("running"):
        st.info("⏳ A negotiation is currently running in another browser tab. Refresh in a moment to see it.")
    cur_msgs = views.scenario_messages(messages, state.get("scenario"))
    views.render_header(state, cur_msgs)
    controls.render_cosign_panel(svc, state, bool(state.get("running")))

    t_mc, t_mb, t_tx, t_val, t_scn = st.tabs(
        ["🧭 Mission Control", "🧩 Mode Board", "💬 Council Transcript", "✅ Validation & Votes", "🗂️ Scenarios"],
        key="main_tabs", on_change="rerun",  # keep the judge's tab across reruns
    )
    with t_mc:
        views.render_mission_control(state)
    with t_mb:
        views.render_mode_board(state, cur_msgs)
    with t_tx:
        views.render_transcript(state, messages)
    with t_val:
        views.render_validation(state, cur_msgs)
    with t_scn:
        views.render_scenarios(state)


main()
