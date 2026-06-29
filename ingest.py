import os
import sqlite3
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
URL = "https://api.at.govt.nz/realtime/legacy/"
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}

DB_PATH = "transit.db"
# This script does one poll and exits. It's meant to be triggered
# repeatedly by an external scheduler (Windows Task Scheduler) on this
# interval, rather than running its own sleep loop in a long-lived
# process. Set the scheduler's trigger to match this value.
#
# 90s was useful during exploration for fast iteration. For the real
# multi-day collection phase, 180s is still far denser than needed for
# 30-minute buckets and roughly halves the number of scheduled task
# launches over a multi-day run.
POLL_INTERVAL_SECONDS = 180

TRACKED_ROUTES = {"NX1-203", "NX2-207"}

# Anything beyond this is flagged, not discarded. See DESIGN.md - the
# original plan to silently drop these was wrong, the rush hour survey
# turned up a trip with a real, steadily climbing multi-thousand-second
# delay that should absolutely be visible, not quarantined away.
EXTREME_DELAY_THRESHOLD = 3600


SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_raw_stop_events_trip
    ON raw_stop_events(trip_id);

CREATE INDEX IF NOT EXISTS idx_raw_stop_events_route_stop
    ON raw_stop_events(route_id, stop_id);

CREATE TABLE IF NOT EXISTS poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polled_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    rows_written INTEGER,
    error TEXT
);
"""


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def fetch_feed():
    response = requests.get(URL, headers=HEADERS, timeout=10)
    response.raise_for_status()
    return response.json()


def extract_rows(data):
    """
    Pull out every trip update belonging to a tracked route. Cancelled/
    skipped trips are still stored (schedule_relationship != 0), just
    flagged, because the original plan of dropping them silently makes
    it impossible to look back later and check how common they are at
    different times of day - which already turned out to matter a lot
    (0.06% in the afternoon survey, 1.46% at rush hour).
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
        # Cancelled/skipped trips send delay=0 as a placeholder, not a
        # real "on time" reading. Null it out here so nobody downstream
        # has to remember to re-check schedule_relationship before
        # trusting this column.
        if sched_rel != 0:
            trip_delay = None

        stu = tu.get("stop_time_update")
        # every sample so far has been a dict, never a list, but
        # guarding anyway since that's a real assumption, not a fact
        if isinstance(stu, list):
            stop_updates = stu
        elif isinstance(stu, dict):
            stop_updates = [stu]
        else:
            stop_updates = [{}]  # cancelled trip, no stop data

        for s in stop_updates:
            arrival = s.get("arrival") or {}
            departure = s.get("departure") or {}
            arrival_delay = arrival.get("delay")
            departure_delay = departure.get("delay")

            candidates = [d for d in (arrival_delay, departure_delay, trip_delay) if d is not None]
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
    conn.executemany(
        """
        INSERT OR IGNORE INTO raw_stop_events (
            trip_id, route_id, direction_id, start_time, start_date,
            stop_id, stop_sequence, arrival_delay, departure_delay,
            trip_level_delay, schedule_relationship, is_extreme, polled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def log_poll(conn, success, rows_written=None, error=None):
    conn.execute(
        "INSERT INTO poll_log (polled_at, success, rows_written, error) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), 1 if success else 0, rows_written, error),
    )
    conn.commit()


def main():
    conn = init_db()
    try:
        data = fetch_feed()
        rows = extract_rows(data)
        insert_rows(conn, rows)
        extreme_count = sum(1 for r in rows if r[-2] == 1)
        log_poll(conn, success=True, rows_written=len(rows))
        print(f"{datetime.now().isoformat()}: {len(rows)} rows, {extreme_count} extreme")
    except requests.exceptions.RequestException as e:
        log_poll(conn, success=False, error=str(e))
        print(f"{datetime.now().isoformat()}: poll failed: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()