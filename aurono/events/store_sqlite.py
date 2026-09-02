# aurono/events/store_sqlite.py

import sqlite3
from typing import Dict, Any
from pathlib import Path

from aurono.db.connect import connect as db_connect


EVENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT PRIMARY KEY,
    event_type          TEXT NOT NULL,
    event_domain        TEXT NOT NULL,

    timestamp_utc       TEXT NOT NULL,
    aurono_device_id    TEXT NOT NULL,

    actor_type          TEXT NOT NULL,
    actor_id            TEXT NOT NULL,

    correlation_id      TEXT,

    strategy_id         TEXT,
    strategy_version_id TEXT,
    trade_id            TEXT,

    exchange            TEXT,
    symbol              TEXT,
    timeframe           TEXT,
    side                TEXT,

    payload_json        TEXT NOT NULL
);
"""


EVENTS_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp_utc)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_events_strategy ON events(strategy_id, timestamp_utc)",
    "CREATE INDEX IF NOT EXISTS idx_events_trade ON events(trade_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id)",
]


def init_event_store(db_path: Path):
    conn = db_connect(db_path)
    try:
        conn.execute(EVENTS_TABLE_DDL)
        for ddl in EVENTS_INDEX_DDL:
            conn.execute(ddl)

        conn.commit()
    finally:
        conn.close()


def insert_event(db_path: Path, event: Dict[str, Any]):
    """
    Persist a fully validated event into SQLite.

    This function assumes:
    - event is already validated
    - event is immutable
    """

    conn = db_connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO events (
                event_id,
                event_type,
                event_domain,
                timestamp_utc,
                aurono_device_id,
                actor_type,
                actor_id,
                correlation_id,
                strategy_id,
                strategy_version_id,
                trade_id,
                exchange,
                symbol,
                timeframe,
                side,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"],
                event["event_type"],
                event["event_domain"],
                event["timestamp_utc"],
                event["aurono_device_id"],
                event["actor_type"],
                event["actor_id"],
                event.get("correlation_id"),
                event.get("strategy_id"),
                event.get("strategy_version_id"),
                event.get("trade_id"),
                event.get("exchange"),
                event.get("symbol"),
                event.get("timeframe"),
                event.get("side"),
                event["payload_json"],
            ),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def assert_event_store_empty_or_valid(db_path: Path):
    conn = db_connect(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM events")
        count = cur.fetchone()[0]
        if count > 0:
            print(f"ℹ️ Event store contains {count} events")
    finally:
        conn.close()
