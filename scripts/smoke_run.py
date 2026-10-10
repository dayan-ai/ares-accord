"""Headless end-to-end run: baseline negotiation, then one emergency event.

Usage (repo root):
    .venv/Scripts/python scripts/smoke_run.py                       # rule-based, Power -4 event
    .venv/Scripts/python scripts/smoke_run.py --provider groq
    .venv/Scripts/python scripts/smoke_run.py --event starter_kit/event_injection_format.json
    .venv/Scripts/python scripts/smoke_run.py --db data/smoke.db --export exports/smoke

Uses a separate database (default data/smoke.db) so the dashboard history is untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ares.config import load_settings  # noqa: E402
from ares.service import Service  # noqa: E402
from ares.storage import Store  # noqa: E402


def _sources(svc: Service, scenario_id: int) -> None:
    """Print who produced the agent messages: real LLM vs labelled rule-based fallback."""
    counts: dict[str, int] = {}
    fallback: dict[str, int] = {}
    for m in svc.transcript(scenario_id=scenario_id):
        key = m.get("source") or "?"
        if key == "llm":
            key = f"llm/{m.get('provider')}/{m.get('model')}"
        counts[key] = counts.get(key, 0) + 1
        if m.get("source") == "rule-based":
            fallback[m.get("fallback_reason") or "?"] = fallback.get(m.get("fallback_reason") or "?", 0) + 1
    print(f"    message sources: {counts}")
    if fallback:
        print(f"    rule-based fallback reasons: {fallback}")
    checks = [m for m in svc.transcript(scenario_id=scenario_id) if m["msg_type"] == "FACT_CHECK"]
    if checks:
        print(f"    fact checks issued: {len(checks)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="rule-based")
    ap.add_argument("--db", default=str(ROOT / "data" / "smoke.db"))
    ap.add_argument("--event", default='{"name": "Solar aftershock", "description": "Available Power decreases by 4", '
                                       '"changes": {"power": -4}}',
                    help="event JSON string or path to a JSON file")
    ap.add_argument("--export", default="", help="directory to write the export bundle into")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if os.path.exists(args.db):
        os.remove(args.db)
    svc = Service(settings=load_settings(), store=Store(args.db))

    def show(m: dict) -> None:
        if args.quiet:
            return
        src = m.get("source")
        tag = f"[{src}{'/' + m['provider'] if m.get('provider') else ''}]"
        print(f"  R{m['round']} v{m.get('plan_version') or '-'} {m['sender']:<12} {m['msg_type']:<20} {tag} "
              f"{(m['content'] or '')[:220]}")

    t0 = time.time()
    print("=== BASELINE ===")
    s = svc.start_baseline(None, args.provider, on_message=show)
    print(f"--> {s['status']} in {s['round']} rounds ({time.time() - t0:.1f}s): {s['result_reason']}")
    print(f"    protocol: {s.get('protocol')}")

    event = args.event
    _sources(svc, s["id"])
    if s["status"] != "APPROVED":
        print("EVENT NOT RUN: the emergency demo requires an APPROVED baseline plan first.")
        return 1

    if os.path.exists(event):
        event = Path(event).read_text(encoding="utf-8")
    t1 = time.time()
    print("=== EVENT ===")
    e = svc.inject_event(event, args.provider, on_message=show)
    print(f"--> {e['status']} in {e['round']} rounds ({time.time() - t1:.1f}s): {e['result_reason']}")
    _sources(svc, e["id"])

    if args.export:
        out = Path(args.export)
        out.mkdir(parents=True, exist_ok=True)
        for name, content in svc.export_bundle().items():
            (out / name).write_text(content, encoding="utf-8")
        print(f"Export written to {out}")
    st = svc.state()
    print(json.dumps({"baseline": s["status"], "event": e["status"], "plan": (st["plan"] or {}).get("version"),
                      "plan_status": (st["plan"] or {}).get("status")}))
    return 0 if s["status"] == "APPROVED" and e["status"] in ("APPROVED", "INFEASIBLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
