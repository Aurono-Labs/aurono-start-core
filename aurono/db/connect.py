# aurono/db/connect.py

"""
Single entry point for opening SQLite connections.

Centralizes durability and concurrency pragmas so every connection across the
runtime opens with the same settings. Without this, individual call sites
silently inherit SQLite defaults that don't match what we want for a Pi running
24/7 with both a scheduler and an API issuing writes.

Use `connect(db_path)` everywhere instead of `sqlite3.connect(db_path)`.
"""

import sqlite3
from pathlib import Path

# Busy timeout in milliseconds. 5s comfortably covers a scheduler tick while
# never colliding with real user-visible latency expectations on the API side.
BUSY_TIMEOUT_MS = 5000

# WAL checkpoint trigger — once the WAL grows past this many pages, the next
# commit drains it back into the main file. Default is 1000; setting it
# explicitly documents intent.
WAL_AUTOCHECKPOINT_PAGES = 1000


def apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply Aurono's standard pragma set to an already-open connection.

    Safe to call multiple times on the same connection.
    """
    # journal_mode is a persistent per-DB property; re-asserting on every
    # open is defensive — if some external tool downgrades it, the next
    # runtime connection self-heals.
    conn.execute("PRAGMA journal_mode=WAL")
    # synchronous is per-connection and NOT persistent. NORMAL paired with
    # WAL is SQLite's documented safe-but-fast default.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT_PAGES}")


def connect(
    db_path: Path | str,
    *,
    read_only: bool = False,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection with Aurono's standard pragmas applied.

    `read_only=True` opens via the `file:...?mode=ro` URI form so writes raise
    `sqlite3.OperationalError`. WAL is still asserted (no-op on read-only DBs
    but harmless and keeps the call shape uniform).
    """
    if read_only:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            check_same_thread=check_same_thread,
        )
    else:
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    apply_pragmas(conn)
    return conn
