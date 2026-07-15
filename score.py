"""
Live scoring: fetches the current AT feed and scores each active
NX1/NX2 trip against the pre-computed baselines.

The core idea: for a trip currently at a given stop, what percentile
of historical observations for that (stop, bucket, day_type) cell
does its current delay fall into? That percentile rank, not the raw
seconds, is what gets shown on the dashboard.

Returns a list of scored observations ready for the dashboard to
render. If the feed is unreachable, returns an empty list with an
error field rather than crashing.
"""

import bisect
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

import db

load_dotenv()

FEED_URL = "https://api.at.govt.nz/realtime/legacy/"

TRACKED_ROUTES = {"NX1-203", "NX2-207"}
EXTREME_DELAY_THRESHOLD = 3600
AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
BUCKET_MINUTES = 60


def to_local(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AUCKLAND_TZ)


def time_bucket(dt):
    local = to_local(dt)
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES


def day_type(dt):
    local = to_local(dt)
    return "weekend" if local.weekday() >= 5 else "weekday"


def percentile_rank(value, sorted_values):
    """
    Fraction of sorted_values strictly below value, as a percentage.
    Uses bisect for O(log n) lookup rather than scanning the whole list.
    """
    if not sorted_values:
        return None
    count_below = bisect.bisect_left(sorted_values, value)
    return round(100 * count_below / len(sorted_values), 1)


def status_tier(pct):
    if pct is None:
        return "unknown"
    if pct < 75:
        return "normal"
    if pct < 95:
        return "late"
    return "very_late"


def load_baselines(conn):
    """
    Load all baselines into memory as a dict keyed by cell tuple.
    At current data volumes this is fine (~200 cells, sorted_delays
    arrays of a few hundred ints each). Would need rethinking if
    tracking many more routes.
    """
    if db.is_postgres():
        cursor = conn.cursor()
        cursor.execute("""
            SELECT route_id, direction_id, stop_id, day_type, bucket,
                   n_total, median_delay, iqr_low, iqr_high, sorted_delays
            FROM baselines
        """)
        rows = cursor.fetchall()
        cursor.close()
    else:
        rows = conn.execute("""
            SELECT route_id, direction_id, stop_id, day_type, bucket,
                   n_total, median_delay, iqr_low, iqr_high, sorted_delays
            FROM baselines
        """).fetchall()

    baselines = {}
    for row in rows:
        route_id, direction_id, stop_id, dtype, bucket, n, median, iqr_lo, iqr_hi, delays_json = row
        key = (route_id, int(direction_id), stop_id, dtype, int(bucket))
        baselines[key] = {
            "n": n,
            "median": median,
            "iqr_low": iqr_lo,
            "iqr_high": iqr_hi,
            "sorted_delays": json.loads(delays_json),
        }

    return baselines


def score_feed(baselines):
    """
    Fetch the live feed and score each active NX1/NX2 trip update.

    Returns a dict with:
      - observations: list of scored stop events
      - fetched_at: ISO timestamp of when the feed was fetched
      - error: None if successful, error message string if not
    """
    now_utc = datetime.now(timezone.utc)
    local_now = to_local(now_utc)
    current_bucket = time_bucket(now_utc)
    current_day_type = day_type(now_utc)

    try:
        headers = {"Ocp-Apim-Subscription-Key": os.environ["AT_API_KEY"]}
        resp = requests.get(FEED_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return {"observations": [], "fetched_at": now_utc.isoformat(), "error": str(e)}
    except KeyError:
        return {
            "observations": [],
            "fetched_at": now_utc.isoformat(),
            "error": "AT_API_KEY not set",
        }

    observations = []

    for entity in data.get("response", {}).get("entity", []):
        tu = entity.get("trip_update")
        if tu is None:
            continue

        trip = tu.get("trip", {})
        route_id = trip.get("route_id")
        if route_id not in TRACKED_ROUTES:
            continue

        sched_rel = trip.get("schedule_relationship", 0)
        if sched_rel != 0:
            continue

        direction_id = trip.get("direction_id")
        trip_delay = tu.get("delay")

        stu = tu.get("stop_time_update")
        if isinstance(stu, dict):
            stop_data = stu
        elif isinstance(stu, list) and stu:
            stop_data = stu[0]
        else:
            continue

        stop_id = stop_data.get("stop_id")
        if not stop_id:
            continue

        dep = (stop_data.get("departure") or {}).get("delay")
        arr = (stop_data.get("arrival") or {}).get("delay")
        delay = dep if dep is not None else arr if arr is not None else trip_delay
        if delay is None:
            continue

        cell_key = (route_id, direction_id, stop_id, current_day_type, current_bucket)
        baseline = baselines.get(cell_key)

        if baseline is None:
            pct = None
            tier = "no_data"
        elif abs(delay) > EXTREME_DELAY_THRESHOLD:
            pct = None
            tier = "extreme"
        else:
            pct = percentile_rank(delay, baseline["sorted_delays"])
            tier = status_tier(pct)

        observations.append({
            "route_id": route_id,
            "direction_id": direction_id,
            "trip_id": trip.get("trip_id"),
            "stop_id": stop_id,
            "delay_seconds": delay,
            "percentile": pct,
            "tier": tier,
            "baseline_median": baseline["median"] if baseline else None,
            "baseline_n": baseline["n"] if baseline else None,
        })

    return {
        "observations": observations,
        "fetched_at": now_utc.isoformat(),
        "local_time": f"{local_now.strftime('%H:%M')} {local_now.tzname()}",
        "error": None,
    }


def get_current_status():
    """
    Entry point for the dashboard. Returns scored observations
    grouped by stop for easy rendering.
    """
    conn = db.get_conn()
    try:
        baselines = load_baselines(conn)
        result = score_feed(baselines)
        return result
    finally:
        conn.close()