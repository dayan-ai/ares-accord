"""Runtime settings, read from environment variables (and `.env` if present)."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)

try:  # optional dependency; the app still runs without it
    from dotenv import load_dotenv
    # Load the repository's .env explicitly (same file as ares.llm.providers) instead of searching upwards
    # from the caller's working directory; never overrides variables already set in the environment.
    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    """Read a finite float >= `minimum` from the environment; otherwise warn and use `default`.

    Guards against values such as "nan" (a NaN deadline never expires) or "-5".
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("config: %s=%r is not a number; using default %s", name, raw, default)
        return default
    if not math.isfinite(value) or value < minimum:
        log.warning("config: %s=%r must be a finite number >= %s; using default %s", name, raw, minimum, default)
        return default
    return value


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
