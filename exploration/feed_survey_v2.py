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
DURATION_MINUTES = 90
OUTPUT_FILE = "survey_log_v2.jsonl"
EXTREME_SAMPLE_FILE = "extreme_delay_samples.jsonl"
EXTREME_THRESHOLD_SECONDS = 3600  # anything beyond this gets logged in full for inspection


def fetch_feed():
    response = requests.get(
        URL,
        headers={"Ocp-Apim-Subscription-Key": API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def stop_time_update_shape(entity):
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


def survey_poll(data, schedule_relationship_counts, stu_shape_counts, delay_values, extreme_log, poll_index):
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
            if abs(trip_delay) > EXTREME_THRESHOLD_SECONDS:
                extreme_log.append({
                    "poll_index": poll_index,
                    "polled_at": datetime.now(timezone.utc).isoformat(),
                    "source": "trip_level",
                    "delay": trip_delay,
                    "trip_id": trip.get("trip_id"),
                    "route_id": trip.get("route_id"),
                    "start_time": trip.get("start_time"),
                    "start_date": trip.get("start_date"),
                    "schedule_relationship": sched_rel,
                })

        stu = entity["trip_update"].get("stop_time_update")
        stops = [stu] if isinstance(stu, dict) else (stu if isinstance(stu, list) else [])

        for s in stops:
            for event_type in ("arrival", "departure"):
                event = s.get(event_type)
                if event and event.get("delay") is not None:
                    d = event["delay"]
                    delay_values.append((f"stop_{event_type}", sched_rel, d))
                    if abs(d) > EXTREME_THRESHOLD_SECONDS:
                        extreme_log.append({
                            "poll_index": poll_index,
                            "polled_at": datetime.now(timezone.utc).isoformat(),
                            "source": f"stop_{event_type}",
                            "delay": d,
                            "trip_id": trip.get("trip_id"),
                            "route_id": trip.get("route_id"),
                            "start_time": trip.get("start_time"),
                            "start_date": trip.get("start_date"),
                            "schedule_relationship": sched_rel,
                            "stop_id": s.get("stop_id"),
                        })

    return len(entities), len(trip_updates)


def main():
    schedule_relationship_counts = Counter()
    stu_shape_counts = Counter()
    delay_values = []
    extreme_log = []
    raw_log = []

    poll_count = 0
    end_time = time.time() + DURATION_MINUTES * 60

    print(f"Polling every {POLL_INTERVAL_SECONDS}s for {DURATION_MINUTES} minutes...")
    print(f"Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local time")

    while time.time() < end_time:
        try:
            data = fetch_feed()
            total, trip_count = survey_poll(
                data, schedule_relationship_counts, stu_shape_counts,
                delay_values, extreme_log, poll_count
            )
            raw_log.append({
                "polled_at": datetime.now(timezone.utc).isoformat(),
                "total_entities": total,
                "trip_updates": trip_count,
            })
            poll_count += 1
            if poll_count % 5 == 0:
                print(f"Poll {poll_count}: {len(delay_values)} samples, {len(extreme_log)} extreme so far")
        except requests.exceptions.RequestException as e:
            print(f"Poll failed: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)

    print("\n--- SURVEY RESULTS (v2) ---")
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

    print(f"\nExtreme delay samples (>{EXTREME_THRESHOLD_SECONDS}s): {len(extreme_log)}")

    with open(OUTPUT_FILE, "w") as f:
        for entry in raw_log:
            f.write(json.dumps(entry) + "\n")

    with open(EXTREME_SAMPLE_FILE, "w") as f:
        for entry in extreme_log:
            f.write(json.dumps(entry) + "\n")

    print(f"\nWrote {OUTPUT_FILE} and {EXTREME_SAMPLE_FILE}")


if __name__ == "__main__":
    main()