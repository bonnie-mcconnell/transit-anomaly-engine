import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "transit.db"
BUCKET_MINUTES = 60
MIN_N = 20

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")


def to_local(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AUCKLAND_TZ)


def day_type(dt):
    local = to_local(dt)
    return "weekend" if local.weekday() >= 5 else "weekday"


def time_bucket(dt):
    local = to_local(dt)
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES


def check_poll_log(conn):
    print("=== poll log ===")
    total = conn.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0]
    if total == 0:
        print("  no polls recorded yet")
        return

    success = conn.execute(
        "SELECT COUNT(*) FROM poll_log WHERE success=1"
    ).fetchone()[0]
    failed = total - success
    print(f"  total attempts: {total}")
    print(f"  succeeded: {success} ({success/total*100:.1f}%)")
    print(f"  failed: {failed}")

    first = conn.execute(
        "SELECT polled_at FROM poll_log ORDER BY id LIMIT 1"
    ).fetchone()[0]
    last = conn.execute(
        "SELECT polled_at FROM poll_log ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    print(f"  first poll: {first}")
    print(f"  last poll:  {last}")

    # gaps between any consecutive poll attempts, not just between
    # successful ones, so a failure followed by a long silence still
    # shows up rather than being hidden by the success filter
    gap_query = """
        SELECT a.polled_at, b.polled_at,
               (julianday(b.polled_at) - julianday(a.polled_at)) * 1440 as gap_minutes
        FROM poll_log a
        JOIN poll_log b ON b.id = a.id + 1
        WHERE (julianday(b.polled_at) - julianday(a.polled_at)) * 1440 > 10
        ORDER BY gap_minutes DESC
        LIMIT 5
    """
    gaps = conn.execute(gap_query).fetchall()
    if gaps:
        print(f"\n  largest gaps between consecutive poll attempts:")
        for g in gaps:
            print(f"    {g[0]} -> {g[1]} ({g[2]:.0f} min)")
    else:
        print("  no gaps > 10 min found")


def check_rows(conn):
    print("\n=== raw_stop_events ===")
    total = conn.execute(
        "SELECT COUNT(*) FROM raw_stop_events"
    ).fetchone()[0]
    print(f"  total rows: {total}")

    by_route = conn.execute("""
        SELECT route_id, COUNT(*) FROM raw_stop_events GROUP BY route_id
    """).fetchall()
    for route, count in by_route:
        print(f"  {route}: {count} rows")

    extreme = conn.execute(
        "SELECT COUNT(*) FROM raw_stop_events WHERE is_extreme=1"
    ).fetchone()[0]
    print(f"  extreme flagged: {extreme} ({extreme/total*100:.2f}% of total)" if total else "")

    cancelled = conn.execute(
        "SELECT COUNT(*) FROM raw_stop_events WHERE schedule_relationship != 0"
    ).fetchone()[0]
    print(f"  non-scheduled trips: {cancelled}")


def check_cold_start(conn):
    print("\n=== cold start progress ===")
    rows = conn.execute("""
        SELECT route_id, direction_id, stop_id, arrival_delay, departure_delay,
               polled_at, is_extreme
        FROM raw_stop_events
        WHERE schedule_relationship = 0
    """).fetchall()

    cells = defaultdict(list)
    for route_id, direction, stop_id, arr, dep, polled_at, is_extreme in rows:
        delay = dep if dep is not None else arr
        if delay is None:
            continue
        dt = datetime.fromisoformat(polled_at)
        key = (route_id, direction, stop_id, day_type(dt), time_bucket(dt))
        cells[key].append({"delay": delay, "is_extreme": bool(is_extreme)})

    total_cells = len(cells)
    over_threshold = sum(1 for obs in cells.values() if len(obs) >= MIN_N)
    ns = sorted(len(obs) for obs in cells.values())

    print(f"  total cells seen so far: {total_cells}")
    print(f"  cells with n >= {MIN_N}: {over_threshold} ({over_threshold/total_cells*100:.1f}% of seen)" if total_cells else "")

    if ns:
        print(f"  n distribution across cells:")
        for pct in (25, 50, 75, 90):
            idx = min(int(len(ns) * pct / 100), len(ns) - 1)
            print(f"    p{pct}: {ns[idx]}")
        print(f"    max: {ns[-1]}")


if __name__ == "__main__":
    print(f"checking {DB_PATH}")
    print(f"run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        conn = sqlite3.connect(DB_PATH)
        check_poll_log(conn)
        check_rows(conn)
        check_cold_start(conn)
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"database error: {e}")
        print("is transit.db in the right directory?")