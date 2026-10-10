"""Render docs/architecture_diagram.png (dev-only: needs `pip install matplotlib`).

The diagram mirrors docs/ARCHITECTURE.md and the actual module layout under ares/.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "architecture_diagram.png"

BOXES = {
    # key: (x, y, w, h, title, subtitle, color)
    "judge": (0.3, 8.6, 2.6, 1.0, "Judge", "browser", "#f1f5f9"),
    "ui": (3.6, 8.4, 5.2, 1.4, "Streamlit dashboard  (app.py, ares/ui)",
           "Mission Control · Mode Board · Council Transcript\nValidation & Votes · Scenarios · Judge Controls", "#e0f2fe"),
    "service": (3.6, 6.7, 5.2, 1.0, "Service facade  (ares/service.py)", "only API the UI may call", "#e0f2fe"),
    "orch": (3.0, 4.4, 6.4, 1.7, "Orchestrator / message bus",
             "ares/orchestrator.py\nR1 proposals → plan vN → validate → R2+ Commander + departments\n"
             "new version clears votes → vote → approval gate\nevents: STALE · commitment review · override",
             "#fef3c7"),
    "cmd": (0.2, 2.2, 2.3, 1.2, "Commander", "goals · state · memory", "#fde68a"),
    "ls": (2.7, 2.2, 1.6, 1.2, "Life Support", "own state", "#dcfce7"),
    "med": (4.5, 2.2, 1.6, 1.2, "Medical", "own state", "#dcfce7"),
    "food": (6.3, 2.2, 1.6, 1.2, "Food Prod.", "own state", "#dcfce7"),
    "eng": (8.1, 2.2, 1.6, 1.2, "Engineering", "own state", "#dcfce7"),
    "rule": (0.2, 0.3, 4.3, 1.1, "Rule-based fallback",
             "ares/agents/rule_policy.py\nper turn on LLM error/timeout · labelled", "#fee2e2"),
    "llm": (4.8, 0.3, 4.9, 1.1, "LLM adapters  (ares/llm)",
            "Gemini · Groq · OpenRouter (:free) · Ollama\nenv keys · timeout · retry · JSON repair", "#ede9fe"),
    "val": (10.2, 5.3, 3.4, 1.4, "Deterministic validator", "ares/validator.py\n7 checks + approval gate", "#fecaca"),
    "opt": (10.2, 3.6, 3.4, 1.3, "Optimizer (support only)", "ares/optimizer.py\ncandidates · infeasibility", "#fecaca"),
    "events": (10.2, 7.0, 3.4, 1.2, "Event parser", "ares/events.py\nofficial JSON · deltas · % · text", "#fecaca"),
    "models": (10.2, 8.6, 3.4, 1.0, "Contract  (ares/models.py)", "12 immutable mode packages", "#f1f5f9"),
    "db": (10.2, 1.6, 3.4, 1.4, "SQLite  data/ares.db", "sessions · scenarios · messages\nplans · votes · commitments · agent_state", "#e2e8f0"),
    "exp": (10.2, 0.2, 3.4, 1.1, "Exports (ares/exports.py)", "JSON · CSV · TXT bundle", "#e2e8f0"),
}

ARROWS = [
    ("judge", "ui", "start · edit pool · inject event"),
    ("ui", "service", ""),
    ("service", "orch", ""),
    ("orch", "cmd", ""), ("orch", "ls", ""), ("orch", "med", ""), ("orch", "food", ""), ("orch", "eng", ""),
    ("cmd", "rule", ""), ("med", "llm", ""),
    ("orch", "val", "validate · gate"), ("orch", "opt", "candidates"), ("service", "events", "parse event"),
    ("orch", "db", "persist"), ("db", "exp", ""), ("val", "models", ""),
]


def center(key: str) -> tuple[float, float]:
    x, y, w, h, *_ = BOXES[key]
    return x + w / 2, y + h / 2


def edge(key: str, toward: tuple[float, float]) -> tuple[float, float]:
    """Point where the line from the box centre toward `toward` leaves the box."""
    x, y, w, h, *_ = BOXES[key]
    cx, cy = x + w / 2, y + h / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    t = min((w / 2) / abs(dx) if dx else float("inf"), (h / 2) / abs(dy) if dy else float("inf"))
    return cx + dx * t * 1.04, cy + dy * t * 1.04


def main() -> None:
    fig, ax = plt.subplots(figsize=(14, 10.2), dpi=130)
    ax.set_xlim(0, 13.8)
    ax.set_ylim(0, 10)
    ax.axis("off")
    for key, (x, y, w, h, title, sub, color) in BOXES.items():
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.03,rounding_size=0.12",
                                    fc=color, ec="#334155", lw=1.2, zorder=2))
        ax.text(x + w / 2, y + h - 0.28, title, ha="center", va="center", fontsize=10.5, weight="bold", zorder=3)
        ax.text(x + w / 2, y + (h - 0.45) / 2, sub, ha="center", va="center", fontsize=8.2, color="#334155",
                zorder=3)
    for a, b, label in ARROWS:
        (x1, y1), (x2, y2) = edge(a, center(b)), edge(b, center(a))
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14, lw=1.2,
                                     color="#475569", shrinkA=0, shrinkB=0, zorder=5))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.12, label, fontsize=7.8, color="#1e293b", ha="center",
                    bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.2), zorder=4)
    ax.text(0.2, 9.85, "ARES Accord — architecture", fontsize=15, weight="bold")
    ax.text(0.2, 7.0, "Agents only speak (proposals, objections, counteroffers,\ncommitments, votes). "
            "Approval = validator PASS +\n4 ACCEPT votes on the current version + min rounds.",
            fontsize=8.5, color="#7f1d1d")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
