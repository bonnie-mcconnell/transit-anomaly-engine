import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}
BASE = "https://api.at.govt.nz/gtfs/v3"

# stops appearing in the high-delay NX1 dir=1 bucket=16/17 cells,
# plus the NX2 terminus stop for comparison
STOPS = {
    "4200-3b3da5cd": "NX1 dir1 b16/17 highest delay",
    "3355-dbd9aecd": "NX1 dir1 b16/17 second highest",
    "4063-7dff9dde": "NX1 dir1 b16/17 third",
    "3221-7da3d572": "NX1 dir1 b16/17 fourth",
    "4227-0e16a7e9": "NX1 dir1 b16/17 large n, moderate delay",
    "7036-f1ffa0be": "NX1 dir1 b16/17 also high",
    "1002-c8bb8209": "NX1 dir1 b16/17",
    "7034-7b36cf5b": "NX1 dir1 b16/17",
    "4981-ecc5b741": "appears across many cells",
    "1003-ea94d2b2": "NX1 dir0 appears a lot",
    "7147-4e9003b4": "NX2 terminus (consistently early)",
}


def get_stop(stop_id):
    r = requests.get(f"{BASE}/stops/{stop_id}", headers=HEADERS, timeout=10)
    if r.status_code == 200:
        attrs = r.json()["data"]["attributes"]
        return attrs.get("stop_name"), attrs.get("stop_lat"), attrs.get("stop_lon")
    return None, None, None


if __name__ == "__main__":
    print(f"{'stop_id':<25} {'note':<35} {'name'}")
    print("-" * 100)
    for stop_id, note in STOPS.items():
        name, lat, lon = get_stop(stop_id)
        print(f"{stop_id:<25} {note:<35} {name or 'NOT FOUND'}")