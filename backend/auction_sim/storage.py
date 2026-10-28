"""Durable SQLite snapshots, messages and append-only events in one transaction."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .engine import reduce_event


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(run_id, seq)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    run_id TEXT NOT NULL REFERENCES runs(id), message_id TEXT NOT NULL,
                    actor TEXT NOT NULL, body TEXT NOT NULL, accepted INTEGER NOT NULL,
                    error TEXT, PRIMARY KEY(run_id, message_id)
                );
            """)

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def reader(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def state(conn, run_id: str) -> dict:
        row = conn.execute("SELECT state FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row["state"])

    @staticmethod
    def events(conn, run_id: str, after: int = 0, through: int | None = None) -> list[dict]:
        if not 0 <= after <= 2**63 - 1:
            raise ValueError("Event cursor is outside the supported range")
        upper = through if through is not None else 2**63 - 1
        return [
            json.loads(row["data"])
            for row in conn.execute(
                "SELECT data FROM events WHERE run_id = ? AND seq > ? AND seq <= ? ORDER BY seq",
                (run_id, after, upper),
            )
        ]

    @staticmethod
    def append(
        conn,
        run_id: str,
        state: dict | None,
        kind: str,
        actor: str,
        payload: dict,
        visibility: str = "public",
        message_id: str | None = None,
    ) -> dict:
        event = {
            "seq": state["event_count"] + 1 if state else 1,
            "type": kind,
            "actor": actor,
            "round": 1,
            "visibility": visibility,
            "payload": payload,
            "message_id": message_id,
            "created_at": timestamp(),
        }
        updated = reduce_event(state, event)
        conn.execute(
            "INSERT INTO events(run_id, seq, data) VALUES (?, ?, ?)",
            (run_id, event["seq"], json.dumps(event)),
        )
        conn.execute("UPDATE runs SET state = ? WHERE id = ?", (json.dumps(updated), run_id))
        return updated
