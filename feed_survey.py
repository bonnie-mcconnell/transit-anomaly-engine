import os
import time
import json
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
URL = "https://api.at.govt.nz/realtime/legacy/"

POLL_INTERVAL_SECONDS = 90
DURATION_MINUTES = 20
OUTPUT_FILE = "survey_log.jsonl"


def fetch_feed():
    response = requests.get(
        URL,
        headers={"Ocp-Apim-Subscription-Key": API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def stop_time_update_shape(entity):
    """Return 'missing', 'dict', 'list', or 'other' for this entity's stop_time_update."""
    trip_update = entity.get("trip_update")
    if trip_update is None:
        return "no_trip_update"
    stu = trip_update.get("stop_time_update")
    if stu is None:
        return "missing"
    if isinstance(stu, dict):
        return "dict"
    if isinstance(stu, list):
        return "list"
    return "other"


def survey_poll(data, schedule_relationship_counts, stu_shape_counts, delay_values, raw_log):
    entities = data["response"]["entity"]
    trip_updates = [e for e in entities if "trip_update" in e]

    for entity in trip_updates:
        trip = entity["trip_update"]["trip"]
        sched_rel = trip.get("schedule_relationship", 0)
        schedule_relationship_counts[sched_rel] += 1

        shape = stop_time_update_shape(entity)
        stu_shape_counts[shape] += 1

        trip_delay = entity["trip_update"].get("delay")
        if trip_delay is not None:
            delay_values.append(("trip", sched_rel, trip_delay))

        stu = entity["trip_update"].get("stop_time_update")
        if isinstance(stu, dict):
            stops = [stu]
        elif isinstance(stu, list):
            stops = stu
        else:
            stops = []

        for s in stops:
            for event_type in ("arrival", "departure"):
                event = s.get(event_type)
                if event and event.get("delay") is not None:
                    delay_values.append((f"stop_{event_type}", sched_rel, event["delay"]))

    # keep a small, capped raw sample so the log file doesn't explode
    if len(raw_log) < 500:
        raw_log.append({
            "polled_at": datetime.now(timezone.utc).isoformat(),
            "total_entities": len(entities),
            "trip_updates": len(trip_updates),
        })


def main():
    schedule_relationship_counts = Counter()
    stu_shape_counts = Counter()
    delay_values = []
    raw_log = []

    poll_count = 0
    end_time = time.time() + DURATION_MINUTES * 60

    print(f"Polling every {POLL_INTERVAL_SECONDS}s for {DURATION_MINUTES} minutes...")

    while time.time() < end_time:
        try:
            data = fetch_feed()
            survey_poll(data, schedule_relationship_counts, stu_shape_counts, delay_values, raw_log)
            poll_count += 1
            print(f"Poll {poll_count} ok, {len(delay_values)} delay samples so far")
        except requests.exceptions.RequestException as e:
            print(f"Poll failed: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)

    print("\n--- SURVEY RESULTS ---")
    print(f"Total polls: {poll_count}")

    print("\nschedule_relationship counts:")
    for k, v in schedule_relationship_counts.most_common():
        print(f"  {k}: {v}")

    print("\nstop_time_update shape counts:")
    for k, v in stu_shape_counts.most_common():
        print(f"  {k}: {v}")

    abs_delays = sorted(abs(d) for _, _, d in delay_values)
    if abs_delays:
        n = len(abs_delays)
        print(f"\nDelay magnitude distribution (n={n}):")
        for pct in (50, 75, 90, 95, 99, 99.9):
            idx = min(int(n * pct / 100), n - 1)
            print(f"  p{pct}: {abs_delays[idx]} seconds")
        print(f"  max: {abs_delays[-1]} seconds")

    with open(OUTPUT_FILE, "w") as f:
        for entry in raw_log:
            f.write(json.dumps(entry) + "\n")
    print(f"\nPoll metadata written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()