"""Runtime settings, read from environment variables (and `.env` if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional dependency; the app still runs without it
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    db_path: str
    default_provider: str
    baseline_min_rounds: int = 3
    event_min_rounds: int = 2
    baseline_max_rounds: int = 6
    event_max_rounds: int = 5
    event_deadline_s: float = 170.0      # PDF p8: new plan or INFEASIBLE within 3 minutes
    baseline_deadline_s: float = 600.0
    transcript_window: int = 14          # recent messages handed to agents


def load_settings() -> Settings:
    db = os.getenv("ARES_DB_PATH", "data/ares.db")
    if not os.path.isabs(db):
        db = str(ROOT / db)
    return Settings(
        db_path=db,
        default_provider=os.getenv("ARES_PROVIDER", "rule-based") or "rule-based",
        event_deadline_s=_float("ARES_SCENARIO_DEADLINE_S", 170.0),
        baseline_deadline_s=_float("ARES_BASELINE_DEADLINE_S", 600.0),
    )
