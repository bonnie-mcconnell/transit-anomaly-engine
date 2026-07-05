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

One real difference between the two backends that callers need to
know: SQLite uses ? as the parameter placeholder, Postgres uses %s.
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
    PLACEHOLDER = "%s"
else:
    PLACEHOLDER = "?"


def get_conn():
    """
    Return a database connection. Caller is responsible for closing it.
    For SQLite, also sets row_factory so rows behave like dicts.
    """
    if _is_postgres:
        conn = psycopg2.connect(DATABASE_URL)
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
    """
    if _is_postgres:
        cursor = conn.cursor()
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
                error TEXT
            )
        """)
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
                error TEXT
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