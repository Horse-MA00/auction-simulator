"""Transactional persistence for SQLite development and PostgreSQL deployments."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    text,
)

from .engine import reduce_event

metadata = MetaData()
Table(
    "runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("state", Text, nullable=False),
)
Table(
    "events",
    metadata,
    Column("run_id", Text, ForeignKey("runs.id"), primary_key=True),
    Column("seq", BigInteger, primary_key=True),
    Column("data", Text, nullable=False),
)
Table(
    "messages",
    metadata,
    Column("run_id", Text, ForeignKey("runs.id"), primary_key=True),
    Column("message_id", Text, primary_key=True),
    Column("actor", Text, nullable=False),
    Column("body", Text, nullable=False),
    Column("accepted", Integer, nullable=False),
    Column("error", Text),
)
Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("username", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("role IN ('user', 'admin')", name="users_role_check"),
)
Table(
    "sessions",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", Text, nullable=False),
    Column("expires_at", BigInteger, nullable=False, index=True),
)
Table(
    "auth_attempts",
    metadata,
    Column("key", Text, primary_key=True),
    Column("attempts", Integer, nullable=False),
    Column("expires_at", BigInteger, nullable=False, index=True),
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Connection:
    """Bind positional values to SQLAlchemy statements; SQL is application-owned."""

    def __init__(self, connection, *, writing: bool):
        self.connection = connection
        self.writing = writing
        self.dialect = connection.dialect.name

    def execute(self, sql: str, values: tuple = ()):
        pieces = sql.split("?")
        if len(pieces) != len(values) + 1:
            raise ValueError("SQL parameter count mismatch")
        statement = pieces[0]
        for index, piece in enumerate(pieces[1:]):
            statement += f":p{index}" + piece
        result = self.connection.execute(
            text(statement), {f"p{index}": value for index, value in enumerate(values)}
        )
        return result.mappings() if result.returns_rows else result


class Store:
    def __init__(self, path: str | Path):
        value = str(path)
        self.path = None
        if value.startswith("postgres://"):
            value = "postgresql+psycopg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        if "://" not in value:
            self.path = Path(value)
            value = "sqlite:///" + str(self.path)
        elif value.startswith("sqlite:///") and value != "sqlite:///:memory:":
            self.path = Path(value[len("sqlite:///") :])
        self.engine = create_engine(value, pool_pre_ping=True)
        self.dialect = self.engine.dialect.name
        if self.dialect not in {"sqlite", "postgresql"}:
            raise ValueError("Use a SQLite path or PostgreSQL database URL")
        if self.dialect == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(dbapi_connection, connection_record):
                dbapi_connection.execute("PRAGMA foreign_keys = ON")
                dbapi_connection.execute("PRAGMA busy_timeout = 15000")

    def initialize(self):
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # Add auth tables without rewriting existing v0 runs or event logs.
        metadata.create_all(self.engine)

    def close(self):
        self.engine.dispose()

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            try:
                if self.dialect == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.begin()
                yield Connection(conn, writing=True)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def reader(self):
        with self.engine.connect() as conn:
            # Replay reads its snapshot and events from a single consistent point.
            if self.dialect == "postgresql":
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                conn.begin()
            else:
                conn.exec_driver_sql("BEGIN")
            try:
                yield Connection(conn, writing=False)
            finally:
                conn.rollback()

    @staticmethod
    def state(conn, run_id: str) -> dict:
        lock = " FOR UPDATE" if conn.writing and conn.dialect == "postgresql" else ""
        row = conn.execute("SELECT state FROM runs WHERE id = ?" + lock, (run_id,)).fetchone()
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
        recorded_event = {
            "seq": state["event_count"] + 1 if state else 1,
            "type": kind,
            "actor": actor,
            "round": 1,
            "visibility": visibility,
            "payload": payload,
            "message_id": message_id,
            "created_at": timestamp(),
        }
        updated = reduce_event(state, recorded_event)
        conn.execute(
            "INSERT INTO events(run_id, seq, data) VALUES (?, ?, ?)",
            (run_id, recorded_event["seq"], json.dumps(recorded_event)),
        )
        conn.execute("UPDATE runs SET state = ? WHERE id = ?", (json.dumps(updated), run_id))
        return updated
