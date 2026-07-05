"""
One-time migration: push all data from local transit.db to Supabase.

Run this once after setting up Supabase to seed it with the existing
collected data. Safe to re-run; the unique constraints on both tables
skip any rows that already exist in Supabase.

Usage:
    SUPABASE_URL=postgresql://... python migrate.py

The script reads from transit.db in the current directory.
"""

import os
import sqlite3
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

LOCAL_DB = os.environ.get("LOCAL_DB", "transit.db")
SUPABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_URL")
BATCH_SIZE = 500


def migrate_events(local_conn, pg_conn):
    rows = local_conn.execute("""
        SELECT trip_id, route_id, direction_id, start_time, start_date,
               stop_id, stop_sequence, arrival_delay, departure_delay,
               trip_level_delay, schedule_relationship, is_extreme, polled_at
        FROM raw_stop_events
        ORDER BY id
    """).fetchall()

    total = len(rows)
    print(f"migrating {total} stop events...")

    inserted = 0
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        cursor = pg_conn.cursor()
        psycopg2.extras.execute_values(
            cursor,
            """
            INSERT INTO raw_stop_events (
                trip_id, route_id, direction_id, start_time, start_date,
                stop_id, stop_sequence, arrival_delay, departure_delay,
                trip_level_delay, schedule_relationship, is_extreme, polled_at
            ) VALUES %s
            ON CONFLICT (trip_id, stop_id, polled_at) DO NOTHING
            """,
            batch,
        )
        pg_conn.commit()
        cursor.close()
        inserted += len(batch)
        print(f"  {inserted}/{total}")

    return total


def migrate_polls(local_conn, pg_conn):
    rows = local_conn.execute("""
        SELECT polled_at, success, rows_written, error
        FROM poll_log
        ORDER BY id
    """).fetchall()

    total = len(rows)
    print(f"migrating {total} poll log entries...")

    cursor = pg_conn.cursor()
    psycopg2.extras.execute_values(
        cursor,
        """
        INSERT INTO poll_log (polled_at, success, rows_written, error)
        VALUES %s
        ON CONFLICT DO NOTHING
        """,
        rows,
    )
    pg_conn.commit()
    cursor.close()
    return total


def main():
    if not SUPABASE_URL:
        print("error: set DATABASE_URL or SUPABASE_URL to your Supabase connection string")
        sys.exit(1)

    if not os.path.exists(LOCAL_DB):
        print(f"error: {LOCAL_DB} not found in current directory")
        sys.exit(1)

    print(f"connecting to local: {LOCAL_DB}")
    print(f"connecting to supabase: {SUPABASE_URL[:40]}...")

    local_conn = sqlite3.connect(LOCAL_DB)
    pg_conn = psycopg2.connect(SUPABASE_URL)

    started = datetime.now()
    try:
        n_events = migrate_events(local_conn, pg_conn)
        n_polls = migrate_polls(local_conn, pg_conn)
        elapsed = (datetime.now() - started).total_seconds()
        print(f"\ndone in {elapsed:.1f}s")
        print(f"  {n_events} stop events")
        print(f"  {n_polls} poll log entries")
        print("\nnext: run materialise.py against supabase to build baselines")
    finally:
        local_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()