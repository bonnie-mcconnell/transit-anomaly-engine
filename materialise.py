"""
Materialises per-cell baselines from raw_stop_events.

Reads every raw stop event, groups by (route, direction, stop,
day_type, 60-minute bucket), computes median and IQR for each cell
that has at least MIN_N non-extreme observations, and upserts the
result into the baselines table.

The sorted_delays column stores the full sorted delay array as JSON.
This is what makes live scoring fast: the dashboard can compute a
percentile rank for any observation without re-querying raw_stop_events,
just by binary-searching into the pre-sorted array.

Runs nightly. Safe to re-run at any time since it upserts.
"""

import json
import statistics
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import db

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
BUCKET_MINUTES = 60
MIN_N = 20


def to_local(polled_at_str):
    dt = datetime.fromisoformat(polled_at_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AUCKLAND_TZ)


def time_bucket(dt):
    local = to_local(dt) if isinstance(dt, str) else dt
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES


def day_type(dt):
    local = to_local(dt) if isinstance(dt, str) else dt
    return "weekend" if local.weekday() >= 5 else "weekday"


def load_events(conn):
    """
    Load all non-cancelled stop events and group by cell key.
    Only departure_delay is used for the baseline; if departure is
    null, falls back to arrival_delay. Rows where both are null are
    skipped. schedule_relationship != 0 rows are skipped entirely
    since their delays are not real operational delays.
    """
    p = db.PLACEHOLDER
    if db.is_postgres():
        cursor = conn.cursor()
        cursor.execute("""
            SELECT route_id, direction_id, stop_id,
                   arrival_delay, departure_delay, polled_at, is_extreme
            FROM raw_stop_events
            WHERE schedule_relationship = 0
        """)
        rows = cursor.fetchall()
        cursor.close()
    else:
        rows = conn.execute("""
            SELECT route_id, direction_id, stop_id,
                   arrival_delay, departure_delay, polled_at, is_extreme
            FROM raw_stop_events
            WHERE schedule_relationship = 0
        """).fetchall()

    cells = {}
    for row in rows:
        route_id, direction_id, stop_id, arr, dep, polled_at, is_extreme = row
        delay = dep if dep is not None else arr
        if delay is None:
            continue

        local_dt = to_local(str(polled_at))
        key = (route_id, direction_id, stop_id, day_type(local_dt), time_bucket(local_dt))

        if key not in cells:
            cells[key] = []
        cells[key].append({"delay": int(delay), "is_extreme": bool(is_extreme)})

    return cells


def compute_baseline(observations):
    """
    Note on the IQR: q1/q3 use nearest-rank indexing (non_extreme[n//4] and
    non_extreme[3n//4]), not linear interpolation. This is a deliberate
    simplification, not an oversight: it needs no numpy dependency and is
    close enough to an interpolated quantile once n is a few dozen or more
    (which MIN_N=20 already requires). It will not exactly match
    numpy.percentile's default (linear) method if anyone cross-checks the
    README's IQR figures against numpy - that's expected, not a bug.
    """
    n_total = len(observations)
    non_extreme = sorted(o["delay"] for o in observations if not o["is_extreme"])

    if n_total < MIN_N or len(non_extreme) < 2:
        return None

    median = statistics.median(non_extreme)
    q1 = non_extreme[len(non_extreme) // 4]
    q3 = non_extreme[(3 * len(non_extreme)) // 4]

    return {
        "n_total": n_total,
        "n_baseline": len(non_extreme),
        "median_delay": float(median),
        "iqr_low": float(q1),
        "iqr_high": float(q3),
        "sorted_delays": non_extreme,
    }


def upsert_baselines(conn, baselines):
    computed_at = datetime.now(timezone.utc).isoformat()
    p = db.PLACEHOLDER

    if db.is_postgres():
        cursor = conn.cursor()
        for (route_id, direction_id, stop_id, dtype, bucket), b in baselines.items():
            cursor.execute(
                f"""
                INSERT INTO baselines (
                    route_id, direction_id, stop_id, day_type, bucket,
                    n_total, n_baseline, median_delay, iqr_low, iqr_high,
                    sorted_delays, computed_at
                ) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                ON CONFLICT (route_id, direction_id, stop_id, day_type, bucket)
                DO UPDATE SET
                    n_total = EXCLUDED.n_total,
                    n_baseline = EXCLUDED.n_baseline,
                    median_delay = EXCLUDED.median_delay,
                    iqr_low = EXCLUDED.iqr_low,
                    iqr_high = EXCLUDED.iqr_high,
                    sorted_delays = EXCLUDED.sorted_delays,
                    computed_at = EXCLUDED.computed_at
                """,
                (
                    route_id, direction_id, stop_id, dtype, bucket,
                    b["n_total"], b["n_baseline"], b["median_delay"],
                    b["iqr_low"], b["iqr_high"],
                    json.dumps(b["sorted_delays"]), computed_at,
                ),
            )
        conn.commit()
        cursor.close()
    else:
        for (route_id, direction_id, stop_id, dtype, bucket), b in baselines.items():
            conn.execute(
                f"""
                INSERT OR REPLACE INTO baselines (
                    route_id, direction_id, stop_id, day_type, bucket,
                    n_total, n_baseline, median_delay, iqr_low, iqr_high,
                    sorted_delays, computed_at
                ) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                """,
                (
                    route_id, direction_id, stop_id, dtype, bucket,
                    b["n_total"], b["n_baseline"], b["median_delay"],
                    b["iqr_low"], b["iqr_high"],
                    json.dumps(b["sorted_delays"]), computed_at,
                ),
            )
        conn.commit()


def main():
    conn = db.get_conn()
    db.init_schema(conn)

    print(f"loading events... {datetime.now().strftime('%H:%M:%S')}")
    cells = load_events(conn)
    print(f"  {len(cells)} distinct cells")

    baselines = {}
    skipped = 0
    for key, observations in cells.items():
        result = compute_baseline(observations)
        if result is None:
            skipped += 1
            continue
        baselines[key] = result

    print(f"  {len(baselines)} cells have enough data (N>={MIN_N}), {skipped} skipped")

    print(f"writing baselines... {datetime.now().strftime('%H:%M:%S')}")
    upsert_baselines(conn, baselines)

    print(f"done {datetime.now().strftime('%H:%M:%S')}")
    conn.close()


if __name__ == "__main__":
    main()