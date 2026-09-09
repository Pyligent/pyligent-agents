"""Durable state for graph runs: checkpoints, effects, spans.

Three tables, and the second one is the one that saves money.

**runs / node_runs** — the resumable snapshot. Which nodes finished, what they
produced. Kill the process, reload, carry on.

**effects** — the idempotency ledger, with `UNIQUE(run_id, key)` enforced by the
database. A row is written in two stages: *claimed* before the side effect is
attempted, *recorded* once it is known to have happened. Claiming first is what
makes the constraint decide a race between two workers; reading "have we done
this?" and then acting does not, because both readers can win the read.

What the ledger establishes is **replay suppression for a recorded effect**, and
**refusal for a claimed one whose outcome was never learned**. It does not make
an external call exactly-once — nothing on this side of the wire can. That needs
the destination system's own idempotency key. See ADR 0003.

**spans** — one row per node execution, for the trace.

SQLite because it is in the standard library and a file is a perfectly good
durable store for a worked example. Swap in Postgres and the interface holds.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    graph      TEXT NOT NULL,
    status     TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS node_runs (
    run_id      TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    error       TEXT,
    input_hash  TEXT,
    started_at  REAL,
    ended_at    REAL,
    PRIMARY KEY (run_id, node_id)
);

-- The idempotency ledger. The primary key is what decides a race, and it only
-- decides it because the row is claimed BEFORE the effect is attempted.
--   status 'claimed'  — we are about to act, or we died trying and nobody knows
--   status 'recorded' — it happened, and this is what it returned
CREATE TABLE IF NOT EXISTS effects (
    run_id      TEXT NOT NULL,
    key         TEXT NOT NULL,
    node_id     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'recorded',
    result_json TEXT,
    ts          REAL NOT NULL,
    PRIMARY KEY (run_id, key)
);

CREATE TABLE IF NOT EXISTS spans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id, id);
"""


class GraphStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """A ledger written before effects had a status reads as fully recorded.

        Every row in such a file describes an effect that completed, because the
        old writer only ever inserted after the fact. Defaulting them to
        'recorded' preserves exactly that meaning.
        """
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(effects)")}
        if "status" not in cols:
            conn.execute(
                "ALTER TABLE effects ADD COLUMN status TEXT NOT NULL DEFAULT 'recorded'")

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # --- runs -------------------------------------------------------------

    def save_run(self, run_id: str, graph: str, status: str, state: dict[str, Any]) -> None:
        now = time.time()
        with self._c() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, graph, status, state_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "status=excluded.status, state_json=excluded.state_json, "
                "updated_at=excluded.updated_at",
                (run_id, graph, status, json.dumps(state, default=str), now, now),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with self._c() as conn:
            row = conn.execute(
                "SELECT graph, status, state_json, created_at, updated_at "
                "FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "graph": row["graph"],
            "status": row["status"],
            "state": json.loads(row["state_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_runs(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        q = "SELECT run_id, graph, status, updated_at FROM runs"
        p: tuple[Any, ...] = ()
        if status:
            q, p = q + " WHERE status=?", (status,)
        q += " ORDER BY updated_at DESC LIMIT ?"
        with self._c() as conn:
            return [dict(r) for r in conn.execute(q, (*p, limit)).fetchall()]

    # --- node checkpoints -------------------------------------------------

    def start_node(self, run_id: str, node_id: str, attempt: int, input_hash: str) -> None:
        """Write BEFORE the work. A crash mid-node must leave a record it began."""
        with self._c() as conn:
            conn.execute(
                "INSERT INTO node_runs (run_id,node_id,status,attempts,input_hash,started_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(run_id,node_id) DO UPDATE SET "
                "status='running', attempts=excluded.attempts, "
                "input_hash=excluded.input_hash, started_at=excluded.started_at",
                (run_id, node_id, "running", attempt, input_hash, time.time()),
            )

    def finish_node(self, run_id: str, node_id: str, status: str,
                    output: Any = None, error: str = "") -> None:
        with self._c() as conn:
            conn.execute(
                "UPDATE node_runs SET status=?, output_json=?, error=?, ended_at=? "
                "WHERE run_id=? AND node_id=?",
                (status, json.dumps(output, default=str), error, time.time(), run_id, node_id),
            )

    def node_runs(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self._c() as conn:
            rows = conn.execute(
                "SELECT node_id,status,attempts,output_json,error,input_hash "
                "FROM node_runs WHERE run_id=?", (run_id,)
            ).fetchall()
        return {
            r["node_id"]: {
                "status": r["status"],
                "attempts": r["attempts"],
                "output": json.loads(r["output_json"]) if r["output_json"] else None,
                "error": r["error"] or "",
                "input_hash": r["input_hash"],
            }
            for r in rows
        }

    # --- idempotency ledger -----------------------------------------------

    def get_effect(self, run_id: str, key: str) -> dict[str, Any] | None:
        """The ledger row for this key, whatever state it is in.

        `status` is 'recorded' (the effect happened; `result` is what it
        returned) or 'claimed' (someone said they were about to act and never
        came back). A caller that treats the second as the first re-fires the
        very action the ledger exists to protect.
        """
        with self._c() as conn:
            row = conn.execute(
                "SELECT result_json, node_id, ts, status FROM effects "
                "WHERE run_id=? AND key=?", (run_id, key),
            ).fetchone()
        if row is None:
            return None
        return {
            "result": json.loads(row["result_json"]) if row["result_json"] is not None else None,
            "node_id": row["node_id"], "ts": row["ts"], "status": row["status"],
        }

    def claim_effect(self, run_id: str, key: str, node_id: str) -> dict[str, Any] | None:
        """Reserve this key BEFORE attempting the side effect.

        `None` means the claim is ours and no other worker holds it. Anything
        else is the row that was already there, and the caller must not act:
        'recorded' replays it, 'claimed' means another worker is mid-flight or
        a previous attempt died without telling anyone how it went.

        The ordering is the whole point. Checking and then acting lets two
        workers both read "not yet done"; claiming and then acting lets the
        primary key decide, once, which of them proceeds.
        """
        try:
            with self._c() as conn:
                conn.execute(
                    "INSERT INTO effects (run_id,key,node_id,status,result_json,ts) "
                    "VALUES (?,?,?,'claimed',NULL,?)",
                    (run_id, key, node_id, time.time()),
                )
            return None
        except sqlite3.IntegrityError:
            return self.get_effect(run_id, key)

    def record_effect(self, run_id: str, key: str, node_id: str, result: Any) -> bool:
        """Mark a claimed effect as having happened, and store what it returned.

        False means there was no row to complete — the claim was released or
        never taken, so the caller acted outside the ledger's protection.
        """
        with self._c() as conn:
            cur = conn.execute(
                "UPDATE effects SET status='recorded', result_json=?, ts=?, node_id=? "
                "WHERE run_id=? AND key=? AND status='claimed'",
                (json.dumps(result, default=str), time.time(), node_id, run_id, key),
            )
            if cur.rowcount:
                return True
            # No claim to complete. Insert only if the key is genuinely free, so
            # a direct caller still cannot overwrite a recorded effect.
            try:
                conn.execute(
                    "INSERT INTO effects (run_id,key,node_id,status,result_json,ts) "
                    "VALUES (?,?,?,'recorded',?,?)",
                    (run_id, key, node_id, json.dumps(result, default=str), time.time()),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def release_effect(self, run_id: str, key: str) -> bool:
        """Give up a claim that is known not to have fired.

        Only for an action that *reported* failure, or one abandoned before it
        was attempted. An action whose outcome is unknown — a timeout, a killed
        process — must keep its claim, because releasing it is what turns an
        unknown into a duplicate.
        """
        with self._c() as conn:
            cur = conn.execute(
                "DELETE FROM effects WHERE run_id=? AND key=? AND status='claimed'",
                (run_id, key),
            )
        return bool(cur.rowcount)

    def effects(self, run_id: str) -> list[dict[str, Any]]:
        with self._c() as conn:
            rows = conn.execute(
                "SELECT key,node_id,result_json,ts FROM effects "
                "WHERE run_id=? AND status='recorded' ORDER BY ts",
                (run_id,),
            ).fetchall()
        return [
            {"key": r["key"], "node_id": r["node_id"], "result": json.loads(r["result_json"]),
             "ts": r["ts"]}
            for r in rows
        ]

    # --- spans ------------------------------------------------------------

    def span(self, run_id: str, node_id: str, kind: str, detail: dict[str, Any]) -> None:
        with self._c() as conn:
            conn.execute(
                "INSERT INTO spans (run_id,node_id,kind,detail,ts) VALUES (?,?,?,?,?)",
                (run_id, node_id, kind, json.dumps(detail, default=str), time.time()),
            )

    def spans(self, run_id: str) -> list[dict[str, Any]]:
        with self._c() as conn:
            rows = conn.execute(
                "SELECT node_id,kind,detail,ts FROM spans WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        return [
            {"node_id": r["node_id"], "kind": r["kind"], "detail": json.loads(r["detail"]),
             "ts": r["ts"]}
            for r in rows
        ]
