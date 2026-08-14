import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict

MIN_N = 20
# Was 30, switched to 60 after checking against synthetic data showed
# 30-min buckets leave most cells stuck on "not enough data" for a
# long time. See DESIGN.md, Buckets and cold start.
BUCKET_MINUTES = 60

# polled_at timestamps are stored in UTC. Auckland is UTC+12 (NZST)
# or UTC+13 (NZDT) depending on time of year. Using zoneinfo for
# proper DST handling rather than hardcoding an offset.
AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")


def to_local(dt):
    """Convert a UTC-aware datetime to Auckland local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AUCKLAND_TZ)


def time_bucket(dt):
    """60-min bucket index within a local day, 0 to 23."""
    local = to_local(dt)
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES


def day_type(dt):
    local = to_local(dt)
    return "weekend" if local.weekday() >= 5 else "weekday"


def load_observations(conn, route_id=None):
    """
    Pull every stop observation into memory, grouped by cell
    (route, direction, stop, day_type, bucket). Fine at this data
    size, two routes worth of a few weeks. Would need to move this
    into SQL group-by once the real table is much bigger, this is
    a first version to get the logic right, not the final form.

    Real rows can have arrival_delay or departure_delay null
    independently of each other. Using departure_delay as the
    primary value when both exist, falling back to arrival_delay
    otherwise. Departure is the one that was more consistently
    populated in the actual feed samples looked at so far, and it's
    arguably the more meaningful number anyway, when the bus actually
    leaves a stop matters more than when it arrives for most stops
    along a route. This is a judgment call, not something measured
    properly, worth revisiting once there's a real distribution of
    which field is populated when.
    """
    query = """
        SELECT route_id, direction_id, stop_id, arrival_delay,
               departure_delay, polled_at, is_extreme
        FROM raw_stop_events
    """
    params = ()
    if route_id:
        query += " WHERE route_id = ?"
        params = (route_id,)

    cells = defaultdict(list)
    for row in conn.execute(query, params):
        r_id, direction, stop_id, arrival_delay, departure_delay, polled_at, is_extreme = row
        delay = departure_delay if departure_delay is not None else arrival_delay
        if delay is None:
            continue
        dt = datetime.fromisoformat(polled_at)
        cell_key = (r_id, direction, stop_id, day_type(dt), time_bucket(dt))
        cells[cell_key].append({"delay": delay, "is_extreme": bool(is_extreme), "polled_at": polled_at})

    return cells


def compute_baseline(cell_observations):
    """
    Median and IQR for one cell. Excludes flagged extreme values from
    the baseline itself per DESIGN.md, a handful of climbing-delay or
    vehicle-matching glitch trips shouldn't drag the "normal" baseline
    around, but they're still counted toward N so cold-start gating
    isn't gamed by quietly filtering first and counting after.
    """
    n_total = len(cell_observations)
    non_extreme_delays = sorted(o["delay"] for o in cell_observations if not o["is_extreme"])

    if n_total < MIN_N:
        return {"status": "insufficient_data", "n": n_total}

    if len(non_extreme_delays) < 2:
        # everything in this cell got flagged extreme, genuinely odd,
        # worth surfacing rather than crashing on a tiny list
        return {"status": "insufficient_data", "n": n_total, "note": "all observations flagged extreme"}

    median = statistics.median(non_extreme_delays)
    q1 = non_extreme_delays[len(non_extreme_delays) // 4]
    q3 = non_extreme_delays[(3 * len(non_extreme_delays)) // 4]

    return {
        "status": "ok",
        "n": n_total,
        "n_baseline": len(non_extreme_delays),
        "median": median,
        "iqr_low": q1,
        "iqr_high": q3,
        "sorted_delays": non_extreme_delays,
    }


def percentile_rank(value, sorted_values):
    """Where value falls in sorted_values, as a percentile, 0 to 100."""
    if not sorted_values:
        return None
    count_below = sum(1 for v in sorted_values if v < value)
    return round(100 * count_below / len(sorted_values), 1)


def status_tier(pct_rank):
    if pct_rank is None:
        return "insufficient_data"
    if pct_rank < 75:
        return "normal"
    if pct_rank < 95:
        return "running_late"
    return "significantly_delayed"


def evaluate_observation(delay, baseline):
    """
    Score one observation against its cell's baseline. This is what
    would run against a live incoming poll once there's a real
    baseline table built from real data, here it's just being tested
    against the cell's own historical values for sanity checking.
    """
    if baseline["status"] != "ok":
        return {"tier": "insufficient_data", "percentile": None}

    pct = percentile_rank(delay, baseline["sorted_delays"])
    return {"tier": status_tier(pct), "percentile": pct}


def summarize(cells):
    """
    Compute baselines for every cell and return a structured summary.
    Returns both the status counts and the full list of computed baselines
    for cells that passed the cold-start threshold.
    """
    statuses = defaultdict(int)
    ready = []

    for cell_key, observations in cells.items():
        baseline = compute_baseline(observations)
        statuses[baseline["status"]] += 1
        if baseline["status"] == "ok":
            ready.append((cell_key, baseline))

    return statuses, ready


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "synthetic_test.db"
    print(f"running against {db_path}\n")

    conn = sqlite3.connect(db_path)
    cells = load_observations(conn)
    conn.close()

    print(f"total cells seen: {len(cells)}")
    statuses, ready = summarize(cells)
    for status, count in statuses.items():
        print(f"  {status}: {count}")

    if not ready:
        print("\nno cells have enough data yet")
        sys.exit(0)

    by_median = sorted(ready, key=lambda x: x[1]["median"])
    medians = [b["median"] for _, b in by_median]  # derived from the sorted list
    ns = sorted(b["n"] for _, b in ready)
    iqr_widths = sorted(b["iqr_high"] - b["iqr_low"] for _, b in ready)

    def pct_val(lst, pct):
        idx = min(int(len(lst) * pct / 100), len(lst) - 1)
        return lst[idx]

    print(f"\nmedian delay distribution across {len(ready)} ready cells (seconds):")
    for pct in (10, 25, 50, 75, 90):
        print(f"  p{pct}: {pct_val(medians, pct):.0f}s")
    print(f"  min: {medians[0]:.0f}s   max: {medians[-1]:.0f}s")

    print(f"\nIQR width distribution (iqr_high - iqr_low, seconds):")
    for pct in (10, 25, 50, 75, 90):
        print(f"  p{pct}: {pct_val(iqr_widths, pct):.0f}s")

    print(f"\nsamples per cell:")
    for pct in (25, 50, 75, 90):
        print(f"  p{pct}: {pct_val(ns, pct)}")
    print(f"  max: {ns[-1]}")

    # show the 3 cells with highest median and 3 with lowest, as a
    # sanity check for anything obviously wrong at the extremes
    by_median = sorted(ready, key=lambda x: x[1]["median"])
    print("\n3 cells with lowest median delay:")
    for key, b in by_median[:3]:
        route, direction, stop, dtype, bucket = key
        print(f"  {route} dir={direction} stop={stop} {dtype} bucket={bucket}")
        print(f"    n={b['n']} median={b['median']:.0f}s iqr=[{b['iqr_low']:.0f}, {b['iqr_high']:.0f}]")

    print("\n3 cells with highest median delay:")
    for key, b in by_median[-3:]:
        route, direction, stop, dtype, bucket = key
        print(f"  {route} dir={direction} stop={stop} {dtype} bucket={bucket}")
        print(f"    n={b['n']} median={b['median']:.0f}s iqr=[{b['iqr_low']:.0f}, {b['iqr_high']:.0f}]")

    # optional full cell dump for inspection
    if "--dump-cells" in sys.argv:
        print("\nall ready cells sorted by median delay:")
        for key, b in by_median:
            route, direction, stop, dtype, bucket = key
            print(f"  {route} dir={direction} {dtype} bucket={bucket} "
                  f"stop={stop} n={b['n']} median={b['median']:.0f}s "
                  f"iqr=[{b['iqr_low']:.0f}, {b['iqr_high']:.0f}]")