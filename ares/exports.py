"""JSON / CSV / TXT export bundle (final plan + negotiation logs)."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from ares.models import ACCEPT, AGENT_LABELS, DEPARTMENTS, DEPT_MSG_TYPES, MODE_PACKAGES, RESOURCES
from ares.storage import Store, now

TRANSCRIPT_FIELDS = ["id", "session_id", "scenario_id", "round", "plan_version", "sender", "recipient", "msg_type",
                     "source", "provider", "model", "fallback_reason", "created_at", "content"]


def _plan_detail(store: Store, plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    comms = {c.id: c for c in store.commitments(plan["scenario_id"])}
    return {
        **{k: plan[k] for k in ("version", "status", "round", "risk", "policy", "totals", "reserve", "created_at")},
        "modes": {d: {"code": code, **(MODE_PACKAGES[code].resources() if code in MODE_PACKAGES else {}),
                      "risk": MODE_PACKAGES[code].risk if code in MODE_PACKAGES else None,
                      "tier": MODE_PACKAGES[code].tier if code in MODE_PACKAGES else None,
                      "consequence": MODE_PACKAGES[code].consequence if code in MODE_PACKAGES else None}
                  for d, code in plan["modes"].items()},
        "return_commitments": [comms[c].to_dict() for c in plan["commitment_ids"] if c in comms],
        "validation": plan.get("validation"),
        "votes": store.votes(plan["id"]),
    }


def _final_plan_for(store: Store, scenario: dict[str, Any]) -> dict[str, Any] | None:
    plans = store.plans(scenario["id"])
    approved = [p for p in plans if p["status"] in ("APPROVED", "STALE")]
    return (approved or plans or [None])[-1]


def final_plan_json(store: Store, session_id: int) -> dict[str, Any]:
    scenarios = sorted(store.scenarios(session_id), key=lambda s: s["id"])
    return {
        "generated_at": now(),
        "session_id": session_id,
        "note": "Messages with source 'rule-based' were produced by the deterministic fallback policy, not an LLM.",
        "scenarios": [{
            **{k: s[k] for k in ("id", "kind", "name", "description", "status", "result_reason", "round",
                                 "max_rounds", "min_rounds", "provider", "pool", "base_pool", "policy", "event",
                                 "protocol", "started_at", "finished_at")},
            "final_plan": _plan_detail(store, _final_plan_for(store, s)),
        } for s in scenarios],
    }


def final_allocation_json(store: Store, session_id: int) -> dict[str, Any]:
    """Records shaped like starter_kit/output_schema.json (one per agent). See docs/DECISIONS_AND_RISKS.md."""
    plan = store.latest_approved_plan(session_id) or (store.session_plans(session_id) or [None])[-1]
    if not plan:
        return {"schema_reference": "starter_kit/output_schema.json", "status": "NO_PLAN", "records": []}
    votes = {v["agent"]: v for v in store.votes(plan["id"])}
    msgs = store.messages(scenario_id=plan["scenario_id"])
    records = []
    for d in DEPARTMENTS:
        code = plan["modes"].get(d)
        pkg = MODE_PACKAGES.get(code or "")
        last = next((m for m in reversed(msgs) if m["sender"] == d and m["msg_type"] in DEPT_MSG_TYPES), None)
        vote = votes.get(d)
        res = pkg.resources() if pkg else {r: None for r in RESOURCES}
        records.append({
            "round": plan["round"],
            "agent_id": d,
            "agent_name": AGENT_LABELS[d],
            "plan_version": plan["version"],
            "proposal": {
                "mode": code, "tier": pkg.tier if pkg else None, **res,
                "power_kwh": res["power"], "water_liters": res["water"], "oxygen_units": res["oxygen"],
                "food_rations": None,
                "justification": (last or {}).get("content") or (last or {}).get("payload", {}).get("reason", ""),
            },
            "vote": ("APPROVE" if vote["decision"] == ACCEPT else "REJECT") if vote else None,
            "vote_pdf": vote["decision"] if vote else None,
            "vote_valid": bool(vote["valid"]) if vote else None,
            "risk_score": pkg.risk if pkg else None,
            "flag_human_review": (plan.get("risk") or 0) > 20,
        })
    records.append({
        "round": plan["round"], "agent_id": "COMMANDER", "agent_name": "Commander", "plan_version": plan["version"],
        "proposal": {"modes": plan["modes"], **(plan.get("totals") or {}), "reserve": plan.get("reserve"),
                     "justification": "Commander decision recorded by the deterministic approval gate."},
        "vote": "APPROVE" if plan["status"] in ("APPROVED", "STALE") else "REJECT",
        "vote_pdf": plan["status"], "risk_score": plan.get("risk"),
        "flag_human_review": (plan.get("risk") or 0) > 20,
    })
    return {"schema_reference": "starter_kit/output_schema.json", "plan_version": plan["version"],
            "plan_status": plan["status"], "records": records}


def transcript_csv(messages: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TRANSCRIPT_FIELDS + ["payload"], extrasaction="ignore")
    w.writeheader()
    for m in messages:
        w.writerow({**m, "payload": json.dumps(m.get("payload") or {}, default=str)})
    return buf.getvalue()


def transcript_txt(store: Store, messages: list[dict[str, Any]]) -> str:
    names = {s["id"]: f"[S{s['id']} {s['kind']}] {s['name']}" for s in store.scenarios()}
    lines, current = [], None
    for m in messages:
        if m["scenario_id"] != current:
            current = m["scenario_id"]
            lines.append("")
            lines.append("=" * 100)
            lines.append(names.get(current, f"Scenario {current}"))
            lines.append("=" * 100)
        src = m.get("source") or ""
        if src == "rule-based":
            src = f"RULE-BASED FALLBACK ({m.get('fallback_reason') or 'no reason'})"
        elif src == "llm":
            src = f"LLM {m.get('provider') or ''}/{m.get('model') or ''}"
        ver = f"v{m['plan_version']}" if m.get("plan_version") else "-"
        lines.append(f"R{m['round']} {ver} | {AGENT_LABELS.get(m['sender'], m['sender'])} | {m['msg_type']} | "
                     f"{src}\n    {m['content']}")
    return "\n".join(lines).strip() + "\n"


def crisis_log_txt(store: Store, session_id: int) -> str:
    out = []
    for s in sorted(store.scenarios(session_id), key=lambda s: s["id"]):
        if s["kind"] != "EVENT":
            continue
        ev = s.get("event") or {}
        out.append(f"EVENT S{s['id']}: {s['name']}")
        out.append(f"  Description: {s.get('description') or ''}")
        out.append(f"  Pool before: {s['base_pool']}")
        out.append(f"  Pool after:  {s['pool']}")
        out.append(f"  Changes: {', '.join(ev.get('changes') or []) or 'none'}")
        if ev.get("interpretation_notes"):
            out.append(f"  Interpretation: {'; '.join(ev['interpretation_notes'])}")
        for m in store.messages(scenario_id=s["id"]):
            if m["msg_type"] in ("PLAN_STALE", "COMMITMENT_VOIDED", "COMMITMENT", "APPROVAL", "INFEASIBLE",
                                 "DEADLOCK", "TIMEOUT") or (m["msg_type"] == "SYSTEM" and "Override" in m["content"]):
                out.append(f"  - R{m['round']} {m['msg_type']}: {m['content']}")
        out.append(f"  Rounds: {s['round']}  Policy: {s['policy']}")
        out.append(f"  RESULT: {s['status']} — {s.get('result_reason') or ''}")
        out.append("")
    return "\n".join(out) or "No emergency events were injected in this session.\n"


def plans_csv(store: Store, session_id: int) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["version", "scenario_id", "round", "status", "modes", "risk", *[f"used_{r}" for r in RESOURCES],
                *[f"reserve_{r}" for r in RESOURCES], "validation", "commitment_ids"])
    for p in store.session_plans(session_id):
        w.writerow([p["version"], p["scenario_id"], p["round"], p["status"],
                    "+".join(p["modes"].get(d) or "?" for d in DEPARTMENTS), p["risk"],
                    *[(p.get("totals") or {}).get(r) for r in RESOURCES],
                    *[(p.get("reserve") or {}).get(r) for r in RESOURCES],
                    (p.get("validation") or {}).get("status"), " ".join(p["commitment_ids"])])
    return buf.getvalue()


def build_bundle(store: Store, session_id: int | None) -> dict[str, str]:
    if session_id is None:
        return {}
    msgs = store.messages(session_id=session_id)
    all_msgs = store.messages()
    return {
        "final_plan.json": json.dumps(final_plan_json(store, session_id), indent=2, default=str),
        "final_allocation.json": json.dumps(final_allocation_json(store, session_id), indent=2, default=str),
        "council_transcript.json": json.dumps(msgs, indent=2, default=str),
        "council_transcript.csv": transcript_csv(msgs),
        "council_transcript.txt": transcript_txt(store, msgs),
        "crisis_handling_log.txt": crisis_log_txt(store, session_id),
        "plans.csv": plans_csv(store, session_id),
        "full_history_all_sessions.csv": transcript_csv(all_msgs),
    }
