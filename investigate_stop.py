import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}
BASE = "https://api.at.govt.nz/gtfs/v3"

STOP_ID = "7147-4e9003b4"


def get_stop(stop_id):
    r = requests.get(f"{BASE}/stops/{stop_id}", headers=HEADERS, timeout=10)
    print(f"GET /stops/{stop_id} -> {r.status_code}")
    if r.status_code != 200:
        print(r.text[:300])
        return
    attrs = r.json()["data"]["attributes"]
    print(f"  name: {attrs.get('stop_name')}")
    print(f"  lat:  {attrs.get('stop_lat')}")
    print(f"  lon:  {attrs.get('stop_lon')}")


def get_trips_at_stop(stop_id):
    # stop_trips endpoint per the API listing we saw earlier
    r = requests.get(f"{BASE}/stops/{stop_id}/trips", headers=HEADERS, timeout=10)
    print(f"\nGET /stops/{stop_id}/trips -> {r.status_code}")
    if r.status_code != 200:
        # try alternate path if this 404s
        r2 = requests.get(f"{BASE}/stop_trips/{stop_id}", headers=HEADERS, timeout=10)
        print(f"GET /stop_trips/{stop_id} -> {r2.status_code}")
        if r2.status_code == 200:
            return r2.json()
        print(r.text[:300])
        return None
    return r.json()


if __name__ == "__main__":
    print(f"Investigating stop: {STOP_ID}\n")
    get_stop(STOP_ID)
    trips = get_trips_at_stop(STOP_ID)
    if trips:
        data = trips.get("data", [])
        print(f"\nTrips serving this stop: {len(data)}")
        for t in data[:5]:
            attrs = t.get("attributes", {})
            print(f"  trip_id={attrs.get('trip_id')} route={attrs.get('route_id')} "
                  f"arrival={attrs.get('arrival_time')} departure={attrs.get('departure_time')}")