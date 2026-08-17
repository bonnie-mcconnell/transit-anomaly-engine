"""
Collection health check.
Reports poll_log success rate and gaps, raw_stop_events volume, and cold
start progress towards baselines.

Works against whichever backend DATABASE_URL points at (Postgres in
production, SQLite locally) via db.py.
"""

from datetime import datetime
from collections import defaultdict

import db
from config import MIN_N, day_type, time_bucket


def fetchall(conn, sql):
    """Run a SELECT and return rows from either backend"""
    if db.is_postgres():
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    return conn.execute(sql).fetchall()


def fetchscalar(conn, sql, default=None):
    """
    Run a query expected to return a single row with a single column,
    and return just that value. Returns 'default' (not None) if the query
    returned empty. Callers decide what 'empty' should mean for their specific query
    (0 for a COUNT, None for 'no such row').
    """
    rows = fetchall(conn, sql)
    if not rows:
        return default
    return rows[0][0]


def check_poll_log(conn):
    print("=== poll log ===")
    total = fetchscalar(conn, "SELECT COUNT(*) FROM poll_log", default=0)
    if total == 0:
        print("   no polls recorded yet")
        return

    success = fetchscalar(conn, "SELECT COUNT(*) FROM poll_log WHERE success=1", default=0)
    failed = total - success
    print(f"   total attempts: {total}")
    print(f"   succeeded: {success} ({success/total*100:.1f}%)")
    print(f"   failed: {failed}")

    first = fetchscalar(conn, "SELECT polled_at FROM poll_log ORDER BY id LIMIT 1", default=None)
    last = fetchscalar(conn, "SELECT polled_at FROM poll_log ORDER BY id DESC LIMIT 1", default=None)
    print(f"   first poll: {first}")
    print(f"   last poll: {last}")

    # find gaps between consecutive poll attempts, use julianday() for SQLite
    if db.is_postgres():
        gap_query = """
            SELECT a.polled_at, b.polled_at,
                EXTRACT(EPOCH FROM(b.polled_at - a.polled_at)) / 60 as gap_minutes
            FROM poll_log a
            JOIN poll_log b on b.id = a.id + 1
            WHERE EXTRACT(EPOCH FROM (b.polled_at - a.polled_at)) / 60 > 10
            ORDER BY gap_minutes DESC
            LIMIT 5
        """
    else:
        gap_query = """
            SELECT prev_polled_at, polled_at, gap_minutes FROM (
                SELECT polled_at, 
                    LAG(polled_at) OVER (ORDER BY polled_at) as prev_polled_at,
                    (julianday(polled_at) - julianday(LAG(polled_at) OVER (ORDER BY polled_at))) * 1440 as gap_minutes
                FROM poll_log
            ) sub
            WHERE gap_minutes > 10
            ORDER BY gap_minutes DESC
            LIMIT 5
        """
    gaps = fetchall(conn, gap_query)
    if gaps:
        print(f"\n  largest gaps between consecutive poll attempts:")
        for g in gaps:
            print(f"   {g[0]} -> {g[1]} ({g[2]:.0f} min)")
    else:
        print("   no gaps > 10 min found")


def check_rows(conn):
    print(f"\n=== raw_stop_events ===")
    total = fetchscalar(conn, "SELECT COUNT(*) FROM raw_stop_events", default=0)
    print(f"   total rows: {total}")

    by_route = fetchall(conn, "SELECT route_id, COUNT(*) FROM raw_stop_events GROUP BY route_id")
    for route, count in by_route:
        print(f"   {route}: {count} rows")

    extreme = fetchscalar(conn, "SELECT COUNT(*) FROM raw_stop_events WHERE is_extreme=1", default=0)
    print(f"   extreme flagged: {extreme} ({extreme/total*100:.2f}% of total)" if total else "")

    cancelled = fetchscalar(conn, "SELECT COUNT(*) FROM raw_stop_events WHERE schedule_relationship != 0", default=0)
    print(f"   non-scheduled trips: {cancelled}")


def check_cold_start(conn):
    print("\n=== cold start progress ===")
    rows = fetchall(conn, """
        SELECT route_id, direction_id, stop_id, arrival_delay, departure_delay, polled_at, is_extreme
        FROM raw_stop_events
        WHERE schedule_relationship = 0
    """)

    cells = defaultdict(list)
    for route_id, direction, stop_id, arr, dep, polled_at, is_extreme in rows:
        delay = dep if dep is not None else arr
        if delay is None:
            continue

        # polled_at could be str or datetime, to_local() handles both
        key = (route_id, direction, stop_id, day_type(polled_at), time_bucket(polled_at))
        cells[key].append({"delay": delay, "is_extreme": bool(is_extreme)})

    total_cells = len(cells)
    over_threshold = sum(1 for obs in cells.values() if len(obs) >= MIN_N)
    ns = sorted(len(obs) for obs in cells.values())

    print(f"   total cells seen so far: {total_cells}")
    print(f"   cells with N >= {MIN_N}: {over_threshold} ({over_threshold/total_cells*100:.1f}% of seen)" if total_cells else "")

    if ns:
        print(f"   n distribution across cells:")
        for pct in (25, 50, 75, 90):
            idx = min(int(len(ns) * pct / 100), len(ns) - 1)
            print(f"   p{pct}: {ns[idx]}")
        print(f"   max: {ns[-1]}")


if __name__ == "__main__":
    backend = "Postgres" if db.is_postgres() else "SQLite"
    print(f"checking {backend} database (via DATABASE_URL)")
    print(f"run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    conn = None
    try:
        conn = db.get_conn()
        check_poll_log(conn)
        check_rows(conn)
        check_cold_start(conn)
    except Exception as e:
        print(f"database error: {type(e).__name__}: {e}")
        print("is DATABASE_URL set correctly, and is the database reachable?")
    finally:
        if conn is not None:
            conn.close()