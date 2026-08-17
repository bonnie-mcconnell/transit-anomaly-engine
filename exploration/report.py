"""
Generates a plain-text report of delay baselines per stop, sorted by
median delay. Run this against transit.db to see what the system
currently knows. Output can be piped to a file and committed to show
the project is producing real, evolving results.

    python report.py transit.db > report.txt
"""
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

from exploration.aggregate import (
    load_observations,
    compute_baseline,
    BUCKET_MINUTES,
    MIN_N,
)

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")

# stop names from lookup_stops.py run on 03/07.
# only covering stops that have appeared in ready cells so far.
STOP_NAMES = {
    "4200-3b3da5cd": "Constellation (Stop B)",
    "3355-dbd9aecd": "Smales Farm (Stop B)",
    "4063-7dff9dde": "Akoranga (Stop B)",
    "3221-7da3d572": "Sunnynook (Stop B)",
    "4227-0e16a7e9": "Albany Bus Station (Stop C)",
    "7036-f1ffa0be": "Fanshawe St / Victoria Park",
    "1002-c8bb8209": "Lower Albert (Stop A)",
    "7034-7b36cf5b": "Bradnor Lane",
    "4981-ecc5b741": "Hibiscus Coast (Stop A)",
    "1003-ea94d2b2": "Lower Albert (Stop B)",
    "7147-4e9003b4": "Auckland Universities (Stop E) [terminus]",
    "4222-1f71df5f": "Smales Farm (Stop A)",
    "4226-19578f75": "Albany (Stop B)",
    "4065-8a7abe55": "Constellation (Stop A)",
    "3219-bb8cdfc6": "Sunnynook (Stop A)",
    "7142-fda95eea": "Akoranga (Stop A)",
    "1315-1e8f2b03": "Akoranga Dr / Northcote Rd",
    "1089-d1686dd6": "Esmonde Rd / Lake Rd",
    "1319-b41ddf26": "Esmonde Rd / Taharoto Rd",
    "7001-8665a850": "Onewa Rd / Curran St",
    "7039-8de4104f": "Onewa Domain",
    "1334-c70b5c82": "Fanshawe St / Halsey St",
    "1088-d1b8ae2b": "Fanshawe St / Nelson St",
    "3360-2172b89d": "Tristram St",
    "7089-8ec85023": "Akoranga (Stop B2)",
    "7232-5d167cca": "Smales Farm (Stop D)",
    "4228-9f4125d4": "Constellation (Stop C)",
    "7037-03bd4aae": "Oteha Valley Rd",
}


def bucket_label(bucket):
    hour = (bucket * BUCKET_MINUTES) // 60
    return f"{hour:02d}:00-{(hour+1):02d}:00"


def fmt_delay(seconds):
    sign = "+" if seconds >= 0 else "-"
    m, s = divmod(abs(int(seconds)), 60)
    if m > 0:
        return f"{sign}{m}m{s:02d}s"
    return f"{sign}{abs(int(seconds))}s"


def report(db_path):
    conn = sqlite3.connect(db_path)
    cells = load_observations(conn)

    now_local = datetime.now(AUCKLAND_TZ)
    generated_at = f"{now_local.strftime('%Y-%m-%d %H:%M')} {now_local.tzname()}"
    row_count = conn.execute("SELECT COUNT(*) FROM raw_stop_events").fetchone()[0]
    poll_count = conn.execute("SELECT COUNT(*) FROM poll_log WHERE success=1").fetchone()[0]
    conn.close()

    print(f"NX1/NX2 delay report")
    print(f"generated: {generated_at}")
    print(f"based on {row_count:,} stop observations across {poll_count} successful polls")
    print(f"cells with N >= {MIN_N}: ", end="")

    ready = []
    for cell_key, observations in cells.items():
        baseline = compute_baseline(observations)
        if baseline["status"] == "ok":
            ready.append((cell_key, baseline))

    print(f"{len(ready)} of {len(cells)} total")
    print()

    # group by route and direction for readable output
    by_route_dir = defaultdict(list)
    for cell_key, baseline in ready:
        route, direction, stop_id, dtype, bucket = cell_key
        by_route_dir[(route, direction, dtype)].append(
            (stop_id, bucket, baseline)
        )

    for (route, direction, dtype), entries in sorted(by_route_dir.items()):
        dir_label = "northbound" if direction == 1 else "southbound"
        print(f"=== {route} {dir_label} ({dtype}) ===")

        # sort by bucket then by stop for each group
        entries_sorted = sorted(entries, key=lambda x: (x[1], x[0]))
        for stop_id, bucket, baseline in entries_sorted:
            stop_name = STOP_NAMES.get(stop_id, stop_id)
            median = baseline["median"]
            iqr_lo = baseline["iqr_low"]
            iqr_hi = baseline["iqr_high"]
            n = baseline["n"]
            print(
                f"  {bucket_label(bucket)}  {stop_name:<35} "
                f"median {fmt_delay(median):>8}  "
                f"IQR [{fmt_delay(iqr_lo)}, {fmt_delay(iqr_hi)}]  "
                f"n={n}"
            )
        print()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "transit.db"
    report(db_path)