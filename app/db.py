"""SQLite (WAL), thread-local connection, forward-only migrácie cez PRAGMA user_version."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

_local = threading.local()

MIGRATIONS: list[str] = [
    # 1
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        session_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE days (
        date TEXT PRIMARY KEY,               -- obchodný deň (America/New_York, YYYY-MM-DD)
        start_equity REAL NOT NULL,
        halted INTEGER NOT NULL DEFAULT 0,
        halt_reason TEXT,
        flattened INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        symbol TEXT NOT NULL,
        price REAL,
        tech_score REAL,
        news_score REAL,
        news_confidence REAL,
        score REAL,
        action TEXT NOT NULL,                -- buy | sell | hold | skip
        reason TEXT NOT NULL,
        details TEXT                          -- JSON
    );
    CREATE INDEX decisions_at ON decisions(at DESC);
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        mode TEXT NOT NULL,                  -- dry | paper | live
        broker_id TEXT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        kind TEXT NOT NULL,                  -- entry | exit | flatten
        price REAL,
        stop_price REAL,
        take_profit REAL,
        status TEXT NOT NULL,                -- submitted | filled | rejected | dry
        note TEXT
    );
    CREATE INDEX orders_at ON orders(at DESC);
    CREATE TABLE news_seen (
        id TEXT PRIMARY KEY,
        at TEXT NOT NULL
    );
    CREATE TABLE analyses (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        headlines INTEGER,
        result TEXT NOT NULL                 -- JSON
    );
    CREATE TABLE equity (
        at TEXT NOT NULL,
        equity REAL NOT NULL,
        cash REAL NOT NULL
    );
    CREATE INDEX equity_at ON equity(at DESC);
    CREATE TABLE history (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        event TEXT NOT NULL,
        details TEXT
    );
    CREATE INDEX history_at ON history(at DESC);
    """,
    # 2 – denný súhrn odoslaný
    """
    ALTER TABLE days ADD COLUMN summary_sent INTEGER NOT NULL DEFAULT 0;
    """,
    # 3 – body krivky zo syntetického FakeBrokera (bežal, kým appka nenašla Alpaca kľúče)
    """
    DELETE FROM equity WHERE equity = 100000.0 AND cash = 100000.0
      AND at > (SELECT MIN(at) FROM equity WHERE equity != 100000.0);
    """,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def close() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def migrate() -> None:
    c = conn()
    version = c.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        # executescript sám commitne otvorenú transakciu, preto BEGIN/COMMIT patrí do skriptu
        try:
            c.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version={i};\nCOMMIT;")
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE; commit alebo rollback. Vnútri nikdy neawaituj."""
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def rollback_if_open() -> None:
    c = getattr(_local, "conn", None)
    if c is not None and c.in_transaction:
        c.execute("ROLLBACK")


def q(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def q1(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def run(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


def rows(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in q(sql, params)]


def row(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    r = q1(sql, params)
    return dict(r) if r else None


def log_history(actor: str, event: str, details: Any = None) -> None:
    run(
        "INSERT INTO history(at, actor, event, details) VALUES (?,?,?,?)",
        (now_iso(), actor, event, json.dumps(details, ensure_ascii=False) if details is not None else None),
    )
