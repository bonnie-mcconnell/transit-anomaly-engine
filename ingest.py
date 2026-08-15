"""
Single-shot ingest: poll the AT realtime feed once and write new
stop events to the database, then exit.

Meant to be triggered repeatedly by an external scheduler (Windows
Task Scheduler on the laptop that does the actual polling) rather
than running its own sleep loop. The poll interval is controlled by
the scheduler, not this script. Render only runs the Flask dashboard
(app.py) as a web service, baseline computation runs on GitHub Actions
(see materialise.py).

On transient network failures, fetch_at_feed() (shared with score.py, 
see at_client.py) retries with exponential backoff before giving up, 
allowing recovery from connection blips without waiting for 
the next scheduled run. 
"""

from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import db
from at_client import fetch_at_feed
from config import TRACKED_ROUTES, EXTREME_DELAY_THRESHOLD, MAX_ATTEMPTS, BACKOFF_BASE_SECONDS

load_dotenv()


def extract_rows(data):
    """
    Pull stop events for tracked routes out of a raw feed response.

    Handles:

    - schedule_relationship != 0 (cancelled/skipped trips): kept in
      the database with trip_level_delay set to NULL rather than the
      feed's misleading zero, so a cancelled trip cannot be mistaken
      for an on-time one. Cancellation rate varies a lot by time of
      day (0.06% midday vs 1.46% at rush hour in early surveys) so
      these rows are worth keeping for later analysis.

    - stop_time_update: the GTFS-RT spec defines this as a list, but
      AT's compat API returns a single dict. Handling both defensively
      since the list case has never been observed but the spec allows it.

    - departure_delay vs arrival_delay: both stored independently.
      Departure is preferred by the analysis layer since it determines
      when the bus actually leaves a stop.
    """
    rows = []
    polled_at = datetime.now(timezone.utc).isoformat()

    for entity in data.get("response", {}).get("entity", []):
        tu = entity.get("trip_update")
        if tu is None:
            continue

        trip = tu.get("trip", {})
        route_id = trip.get("route_id")
        if route_id not in TRACKED_ROUTES:
            continue

        sched_rel = trip.get("schedule_relationship", 0)
        trip_delay = tu.get("delay")
        if sched_rel != 0:
            trip_delay = None

        stu = tu.get("stop_time_update")
        if isinstance(stu, list):
            stop_updates = stu
        elif isinstance(stu, dict):
            stop_updates = [stu]
        else:
            stop_updates = [{}]

        for s in stop_updates:
            arrival = s.get("arrival") or {}
            departure = s.get("departure") or {}
            arrival_delay = arrival.get("delay")
            departure_delay = departure.get("delay")

            candidates = [
                d for d in (arrival_delay, departure_delay, trip_delay)
                if d is not None
            ]
            is_extreme = any(abs(d) > EXTREME_DELAY_THRESHOLD for d in candidates)

            rows.append((
                trip.get("trip_id"),
                route_id,
                trip.get("direction_id"),
                trip.get("start_time"),
                trip.get("start_date"),
                s.get("stop_id"),
                s.get("stop_sequence"),
                arrival_delay,
                departure_delay,
                trip_delay,
                sched_rel,
                1 if is_extreme else 0,
                polled_at,
            ))

    return rows


def insert_rows(conn, rows):
    p = db.PLACEHOLDER
    on_conflict = (
        "ON CONFLICT (trip_id, stop_id, polled_at) DO NOTHING"
        if db.is_postgres()
        else ""
    )
    prefix = "INSERT" if db.is_postgres() else "INSERT OR IGNORE"
    sql = f"""
        {prefix} INTO raw_stop_events (
            trip_id, route_id, direction_id, start_time, start_date,
            stop_id, stop_sequence, arrival_delay, departure_delay,
            trip_level_delay, schedule_relationship, is_extreme, polled_at
        ) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
        {on_conflict}
    """
    if db.is_postgres():
        cursor = conn.cursor()
        cursor.executemany(sql, rows)
        conn.commit()
        cursor.close()
    else:
        conn.executemany(sql, rows)
        conn.commit()


def log_poll(conn, success, rows_written=None, error=None):
    p = db.PLACEHOLDER
    polled_at = datetime.now(timezone.utc).isoformat()
    sql = f"""
        INSERT INTO poll_log (polled_at, success, rows_written, error)
        VALUES ({p},{p},{p},{p})
    """
    if db.is_postgres():
        cursor = conn.cursor()
        cursor.execute(sql, (polled_at, 1 if success else 0, rows_written, error))
        conn.commit()
        cursor.close()
    else:
        conn.execute(sql, (polled_at, 1 if success else 0, rows_written, error))
        conn.commit()


def main():
    conn = None
    try:
        conn = db.get_conn()
        db.init_schema(conn)
        data = fetch_at_feed(MAX_ATTEMPTS, BACKOFF_BASE_SECONDS)
        rows = extract_rows(data)
        insert_rows(conn, rows)
        extreme_count = sum(1 for r in rows if r[-2] == 1)
        log_poll(conn, success=True, rows_written=len(rows))
        print(f"{datetime.now().isoformat()}: {len(rows)} rows, {extreme_count} extreme")
    except requests.exceptions.RequestException as e:
        if conn is not None:
            log_poll(conn, success=False, error=str(e))
        print(f"{datetime.now().isoformat()}: all {MAX_ATTEMPTS} attempts failed: {e}")
    except Exception as e:
        # Catch all: anything that isn't a network error gets logged here
        # and recorded in poll_log instead of crashing silently
        # if conn itself failed to connect, conn is still None and cannot log
        if conn is not None:
            log_poll(conn, success=False, error=f"{type(e).__name__}: {e}")
        print(f"{datetime.now().isoformat()}: unexpected error: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()