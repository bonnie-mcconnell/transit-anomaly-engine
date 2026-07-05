import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
BASE_URL = "https://api.at.govt.nz/gtfs/v3"

HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}


def lookup_route(route_id):
    url = f"{BASE_URL}/routes/{route_id}"
    response = requests.get(url, headers=HEADERS, timeout=10)
    if response.status_code == 404:
        print(f"  {route_id}: NOT FOUND")
        return
    response.raise_for_status()
    data = response.json()["data"]["attributes"]
    print(f"  {route_id}:")
    print(f"    short_name: {data.get('route_short_name')}")
    print(f"    long_name:  {data.get('route_long_name')}")
    print(f"    type:       {data.get('route_type')}")


def search_routes_by_keyword(keyword):
    # The v3 API doesn't document a text-search endpoint, so this is a
    # placeholder for a manual fallback: list a handful of known/guessed
    # candidate IDs instead of searching freely.
    print(f"(No documented search-by-name endpoint; checking known candidates instead)")


if __name__ == "__main__":
    print("Checking candidate Northern Express route IDs:\n")
    # We've confirmed NX1-203 appears live in the realtime feed already.
    # Trying common variants for NX2 and a couple of plausible alternates.
    candidates = ["NX1-203", "NX2-203", "NX1-209", "NX2-209"]
    for rid in candidates:
        lookup_route(rid)