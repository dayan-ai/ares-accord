"""SQLite persistence. The only module that touches the database."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from ares.models import Commitment, Vote

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    pool TEXT NOT NULL,
    base_pool TEXT NOT NULL,
    policy TEXT NOT NULL,
    event TEXT,
    status TEXT NOT NULL,
    result_reason TEXT DEFAULT '',
    result TEXT,
    protocol TEXT,
    round INTEGER DEFAULT 0,
    max_rounds INTEGER,
    min_rounds INTEGER,
    provider TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    scenario_id INTEGER,
    round INTEGER,
    plan_version INTEGER,
    sender TEXT NOT NULL,
    recipient TEXT,
    msg_type TEXT NOT NULL,
    content TEXT,
    payload TEXT,
    source TEXT,
    provider TEXT,
    model TEXT,
    fallback_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    modes TEXT NOT NULL,
    commitment_ids TEXT NOT NULL,
    totals TEXT,
    reserve TEXT,
    risk INTEGER,
    policy TEXT,
    status TEXT NOT NULL,
    validation TEXT,
    round INTEGER,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    source TEXT,
    fallback_reason TEXT,
    valid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commitments (
    id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    owner TEXT NOT NULL,
    beneficiary TEXT NOT NULL,
    kind TEXT,
    resource TEXT,
    amount INTEGER,
    source TEXT,
    claim TEXT,
    promise TEXT,
    expiry TEXT,
    status TEXT NOT NULL,
    template_id TEXT,
    round_offered INTEGER,
    round_resolved INTEGER,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS agent_state (
    session_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (session_id, agent)
);
"""

_JSON_COLS = {
    "scenarios": ("pool", "base_pool", "policy", "event", "result", "protocol"),
    "messages": ("payload",),
    "plans": ("modes", "commitment_ids", "totals", "reserve", "policy", "validation"),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=str)


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- helpers ------------------------------------------------------------
    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _rows(self, table: str, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in _JSON_COLS.get(table, ()):
                if d.get(col) is not None:
                    d[col] = json.loads(d[col])
            out.append(d)
        return out

    def _update(self, table: str, key: str, key_val: Any, fields: dict[str, Any]) -> None:
        if not fields:
            return
        cols = _JSON_COLS.get(table, ())
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(_dump(v) if k in cols else v)
        vals.append(key_val)
        self._exec(f"UPDATE {table} SET {', '.join(sets)} WHERE {key} = ?", vals)

    # -- sessions -----------------------------------------------------------
    def new_session(self) -> int:
        return self._exec("INSERT INTO sessions (created_at) VALUES (?)", (now(),)).lastrowid

    def current_session(self) -> dict[str, Any] | None:
        rows = self._rows("sessions", "SELECT * FROM sessions ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def sessions(self) -> list[dict[str, Any]]:
        return self._rows("sessions", "SELECT * FROM sessions ORDER BY id DESC")

    # -- scenarios ----------------------------------------------------------
    def create_scenario(self, **f: Any) -> int:
        f.setdefault("started_at", now())
        cols = list(f.keys())
        vals = [_dump(f[c]) if c in _JSON_COLS["scenarios"] else f[c] for c in cols]
        sql = f"INSERT INTO scenarios ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        return self._exec(sql, vals).lastrowid

    def update_scenario(self, scenario_id: int, **fields: Any) -> None:
        self._update("scenarios", "id", scenario_id, fields)

    def get_scenario(self, scenario_id: int) -> dict[str, Any] | None:
        rows = self._rows("scenarios", "SELECT * FROM scenarios WHERE id = ?", (scenario_id,))
        return rows[0] if rows else None

    def scenarios(self, session_id: int | None = None) -> list[dict[str, Any]]:
        if session_id is None:
            return self._rows("scenarios", "SELECT * FROM scenarios ORDER BY id DESC")
        return self._rows("scenarios", "SELECT * FROM scenarios WHERE session_id = ? ORDER BY id DESC",
                          (session_id,))

    def latest_scenario(self, session_id: int) -> dict[str, Any] | None:
        rows = self.scenarios(session_id)
        return rows[0] if rows else None

    # -- messages -----------------------------------------------------------
    def add_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        msg = dict(msg)
        msg.setdefault("created_at", now())
        cols = ["session_id", "scenario_id", "round", "plan_version", "sender", "recipient", "msg_type",
                "content", "payload", "source", "provider", "model", "fallback_reason", "created_at"]
        vals = [_dump(msg.get(c)) if c == "payload" else msg.get(c) for c in cols]
        sql = f"INSERT INTO messages ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        msg["id"] = self._exec(sql, vals).lastrowid
        return msg

    def messages(self, session_id: int | None = None, scenario_id: int | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM messages", []
        conds = []
        if session_id is not None:
            conds.append("session_id = ?")
            params.append(session_id)
        if scenario_id is not None:
            conds.append("scenario_id = ?")
            params.append(scenario_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return self._rows("messages", sql + " ORDER BY id", params)

    # -- plans --------------------------------------------------------------
    def next_plan_version(self, session_id: int) -> int:
        with self._lock:
            row = self._conn.execute("SELECT MAX(version) FROM plans WHERE session_id = ?",
                                     (session_id,)).fetchone()
        return (row[0] or 0) + 1

    def add_plan(self, **f: Any) -> dict[str, Any]:
        f.setdefault("created_at", now())
        cols = list(f.keys())
        vals = [_dump(f[c]) if c in _JSON_COLS["plans"] else f[c] for c in cols]
        sql = f"INSERT INTO plans ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        pid = self._exec(sql, vals).lastrowid
        return self.get_plan(pid)

    def update_plan(self, plan_id: int, **fields: Any) -> None:
        self._update("plans", "id", plan_id, fields)

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        rows = self._rows("plans", "SELECT * FROM plans WHERE id = ?", (plan_id,))
        return rows[0] if rows else None

    def plans(self, scenario_id: int) -> list[dict[str, Any]]:
        return self._rows("plans", "SELECT * FROM plans WHERE scenario_id = ? ORDER BY version", (scenario_id,))

    def session_plans(self, session_id: int) -> list[dict[str, Any]]:
        return self._rows("plans", "SELECT * FROM plans WHERE session_id = ? ORDER BY version", (session_id,))

    def latest_plan(self, scenario_id: int) -> dict[str, Any] | None:
        rows = self._rows("plans", "SELECT * FROM plans WHERE scenario_id = ? ORDER BY version DESC LIMIT 1",
                          (scenario_id,))
        return rows[0] if rows else None

    def latest_approved_plan(self, session_id: int, include_stale: bool = True) -> dict[str, Any] | None:
        statuses = ("APPROVED", "STALE") if include_stale else ("APPROVED",)
        marks = ", ".join("?" for _ in statuses)
        sql = (f"SELECT * FROM plans WHERE session_id = ? AND status IN ({marks}) "
               "ORDER BY version DESC LIMIT 1")
        rows = self._rows("plans", sql, (session_id, *statuses))
        return rows[0] if rows else None

    # -- votes --------------------------------------------------------------
    def add_vote(self, plan_id: int, scenario_id: int, vote: Vote) -> None:
        self._exec(
            "INSERT INTO votes (plan_id, scenario_id, agent, plan_version, decision, reason, source, "
            "fallback_reason, valid, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (plan_id, scenario_id, vote.agent, vote.plan_version, vote.decision, vote.reason, vote.source,
             vote.fallback_reason, now()),
        )

    def votes(self, plan_id: int, valid_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM votes WHERE plan_id = ?" + (" AND valid = 1" if valid_only else "") + " ORDER BY id"
        return self._rows("votes", sql, (plan_id,))

    def invalidate_vote(self, plan_id: int, agent: str) -> int:
        return self._exec("UPDATE votes SET valid = 0 WHERE plan_id = ? AND agent = ? AND valid = 1",
                          (plan_id, agent)).rowcount

    def invalidate_votes(self, plan_id: int) -> int:
        return self._exec("UPDATE votes SET valid = 0 WHERE plan_id = ? AND valid = 1", (plan_id,)).rowcount

    # -- commitments --------------------------------------------------------
    def next_commitment_id(self, scenario_id: int) -> str:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM commitments WHERE scenario_id = ?",
                                   (scenario_id,)).fetchone()[0]
        return f"S{scenario_id}-C{n + 1}"

    def add_commitment(self, session_id: int, c: Commitment) -> None:
        d = c.to_dict()
        cols = ["id", "scenario_id", "owner", "beneficiary", "kind", "resource", "amount", "source", "claim",
                "promise", "expiry", "status", "template_id", "round_offered", "round_resolved", "note"]
        sql = (f"INSERT INTO commitments (session_id, {', '.join(cols)}) "
               f"VALUES (?, {', '.join('?' for _ in cols)})")
        self._exec(sql, [session_id] + [d[k] for k in cols])

    def update_commitment(self, cid: str, **fields: Any) -> None:
        self._update("commitments", "id", cid, fields)

    def commitments(self, scenario_id: int) -> list[Commitment]:
        rows = self._rows("commitments", "SELECT * FROM commitments WHERE scenario_id = ? ORDER BY rowid",
                          (scenario_id,))
        return [Commitment.from_dict(r) for r in rows]

    # -- agent state --------------------------------------------------------
    def save_agent_state(self, session_id: int, agent: str, state: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO agent_state (session_id, agent, state) VALUES (?, ?, ?)",
                   (session_id, agent, json.dumps(state, default=str)))

    def load_agent_state(self, session_id: int, agent: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT state FROM agent_state WHERE session_id = ? AND agent = ?",
                                     (session_id, agent)).fetchone()
        return json.loads(row[0]) if row else None

    # -- maintenance --------------------------------------------------------
    def wipe(self) -> None:
        with self._lock:
            for t in ("votes", "messages", "plans", "commitments", "agent_state", "scenarios", "sessions"):
                self._conn.execute(f"DELETE FROM {t}")
            self._conn.execute("DELETE FROM sqlite_sequence")
            self._conn.commit()
