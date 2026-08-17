"""
Database connection layer.

Reads DATABASE_URL from the environment:
  - If it looks like a postgres URL, uses psycopg2.
  - Otherwise treats it as a SQLite file path.

Every other module imports get_conn() from here. Nothing else
touches sqlite3 or psycopg2 directly.

In local development, set DATABASE_URL=transit.db (or don't set it
at all, the default is transit.db). In production on Render, set it
to the Supabase connection string.

Difference between the backends: 
SQLite uses ? as the parameter placeholder, Postgres uses %s.
This module exposes a PLACEHOLDER constant so queries can be written
portably without hardcoding either.
"""

import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "transit.db")
_is_postgres = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if _is_postgres:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    PLACEHOLDER = "%s"
else:
    PLACEHOLDER = "?"


def get_conn():
    """
    Return a database connection. Caller is responsible for closing it.
    For SQLite, also sets row_factory so rows behave like dicts.

    connect_timeout=10 on the Postgres path, without it a connection attempt
    during a flaky network window (e.g after laptop wakes from sleep) can hang 
    longer than Task Scheduler poll interval, causing the next scheduled run
    to collide with a still-running previous one instead of the connection failing fast
    and being logged as a failed poll.
    """
    if _is_postgres:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        return conn
    else:
        conn = sqlite3.connect(DATABASE_URL)
        conn.row_factory = sqlite3.Row
        return conn


def is_postgres():
    return _is_postgres


def init_schema(conn):
    """
    Create tables if they don't exist. Safe to call on every startup.
    Uses IF NOT EXISTS throughout so it's idempotent.

    On Postgres, the whole block runs under a session advisory lock,
    since ingest.py and materialise.py both call this on every run and
    can race on a brand-new database. See DESIGN.md for why.
    """
    if _is_postgres:
        cursor = conn.cursor()
        # Arbitrary constant, unique to this lock's purpose. If another
        # advisory lock is ever added elsewhere in this codebase, pick a
        # different constant so the two don't collide (locks are scoped
        # per-database, not per-server, so this only needs to be unique
        # within transport-anomaly-engine's own database).
        LOCK_KEY = 847_291_003
        cursor.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS raw_stop_events (
                    id SERIAL PRIMARY KEY,
                    trip_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    direction_id INTEGER,
                    start_time TEXT,
                    start_date TEXT,
                    stop_id TEXT,
                    stop_sequence INTEGER,
                    arrival_delay INTEGER,
                    departure_delay INTEGER,
                    trip_level_delay INTEGER,
                    schedule_relationship INTEGER,
                    is_extreme INTEGER NOT NULL DEFAULT 0,
                    polled_at TIMESTAMPTZ NOT NULL,
                    UNIQUE(trip_id, stop_id, polled_at)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rse_trip
                    ON raw_stop_events(trip_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rse_route_stop
                    ON raw_stop_events(route_id, stop_id)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poll_log (
                    id SERIAL PRIMARY KEY,
                    polled_at TIMESTAMPTZ NOT NULL,
                    success INTEGER NOT NULL,
                    rows_written INTEGER,
                    error TEXT,
                    UNIQUE(polled_at)
                )
            """)
            # Patches poll_log tables created before UNIQUE(polled_at)
            # existed (e.g existing Supabase table). Checked via
            # pg_constraint directly since the whole block is already
            # serialized by the advisory lock above, so no separate
            # race-handling is needed here anymore.

            # If poll_log already has duplicate 
            # polled_at rows (e.g from migrate.py's ON CONFLICT DO NOTHING 
            # silently duplicating rows before it had a real constraint to 
            # target), adding the constraint is a data conflict, not fixed by
            # retrying or locking. Caught here and turned into an
            # actionable error, since deleting rows to make it pass isn't this function's job.
            try:
                cursor.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'poll_log_polled_at_key'
                              AND conrelid = 'poll_log'::regclass
                        ) THEN
                            ALTER TABLE poll_log ADD CONSTRAINT poll_log_polled_at_key UNIQUE (polled_at);
                        END IF;
                    END $$;
                """)
            except psycopg2.errors.UniqueViolation as e:
                conn.rollback()
                raise RuntimeError(
                    "poll_log has duplicate polled_at rows, so its UNIQUE "
                    "constraint can't be added. Find them with:\n"
                    "  SELECT polled_at, COUNT(*) FROM poll_log "
                    "GROUP BY polled_at HAVING COUNT(*) > 1;\n"
                    "then remove the duplicates before init_schema() can proceed."
                ) from e
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS baselines (
                    route_id TEXT NOT NULL,
                    direction_id INTEGER NOT NULL,
                    stop_id TEXT NOT NULL,
                    day_type TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    n_total INTEGER NOT NULL,
                    n_baseline INTEGER NOT NULL,
                    median_delay REAL NOT NULL,
                    iqr_low REAL NOT NULL,
                    iqr_high REAL NOT NULL,
                    sorted_delays TEXT NOT NULL,
                    computed_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (route_id, direction_id, stop_id, day_type, bucket)
                )
            """)
            conn.commit()
        finally:
            # must conn.rollback() before unlock as a (non-race)
            # failure above leaves the transaction aborted, and
            # pg_advisory_unlock can't run until that clears. Session-level
            # advisory locks survive a rollback, so this doesn't lose the
            # lock, it just lets the connection release it properly instead
            # of leaving that to happen implicitly on connection close.
            conn.rollback()
            cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            cursor.close()
    else:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_stop_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id TEXT NOT NULL,
                route_id TEXT NOT NULL,
                direction_id INTEGER,
                start_time TEXT,
                start_date TEXT,
                stop_id TEXT,
                stop_sequence INTEGER,
                arrival_delay INTEGER,
                departure_delay INTEGER,
                trip_level_delay INTEGER,
                schedule_relationship INTEGER,
                is_extreme INTEGER NOT NULL DEFAULT 0,
                polled_at TEXT NOT NULL,
                UNIQUE(trip_id, stop_id, polled_at)
            );
            CREATE INDEX IF NOT EXISTS idx_rse_trip
                ON raw_stop_events(trip_id);
            CREATE INDEX IF NOT EXISTS idx_rse_route_stop
                ON raw_stop_events(route_id, stop_id);
            CREATE TABLE IF NOT EXISTS poll_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                polled_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                rows_written INTEGER,
                error TEXT,
                UNIQUE(polled_at)
            );
            CREATE TABLE IF NOT EXISTS baselines (
                route_id TEXT NOT NULL,
                direction_id INTEGER NOT NULL,
                stop_id TEXT NOT NULL,
                day_type TEXT NOT NULL,
                bucket INTEGER NOT NULL,
                n_total INTEGER NOT NULL,
                n_baseline INTEGER NOT NULL,
                median_delay REAL NOT NULL,
                iqr_low REAL NOT NULL,
                iqr_high REAL NOT NULL,
                sorted_delays TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (route_id, direction_id, stop_id, day_type, bucket)
            );
        """)
        conn.commit()