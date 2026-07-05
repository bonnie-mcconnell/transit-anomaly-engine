import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}
BASE = "https://api.at.govt.nz/gtfs/v3"

# The trip showing a persistent ~-6442s delay in this morning's survey.
TRIP_ID = "1254-10101-27780-2-2a40f4a2"


def get_trip(trip_id):
    url = f"{BASE}/trips/{trip_id}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    print(f"GET /trips/{trip_id} -> {r.status_code}")
    if r.status_code != 200:
        print(r.text[:500])
        return None
    return r.json()["data"]["attributes"]


def get_stop_times(trip_id):
    url = f"{BASE}/trips/{trip_id}/stoptimes"
    r = requests.get(url, headers=HEADERS, timeout=10)
    print(f"GET /trips/{trip_id}/stoptimes -> {r.status_code}")
    if r.status_code != 200:
        print(r.text[:500])
        return None
    return r.json()["data"]


if __name__ == "__main__":
    trip = get_trip(TRIP_ID)
    if trip:
        print("\nTrip attributes:")
        for k, v in trip.items():
            print(f"  {k}: {v}")

    stop_times = get_stop_times(TRIP_ID)
    if stop_times:
        print(f"\nScheduled stop times ({len(stop_times)} stops):")
        for entry in stop_times[:5]:
            attrs = entry["attributes"]
            print(f"  seq={attrs.get('stop_sequence')} "
                  f"stop={attrs.get('stop_id')} "
                  f"arr={attrs.get('arrival_time')} "
                  f"dep={attrs.get('departure_time')}")
        if len(stop_times) > 5:
            print(f"  ... ({len(stop_times) - 5} more stops)")