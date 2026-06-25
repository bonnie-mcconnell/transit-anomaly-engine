import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY}


def try_list_routes():
    """
    The docs we've seen only show single-route lookup by id
    (GET /gtfs/v3/routes/{id}), not a documented "list all routes"
    endpoint. Try the unfiltered path anyway, since JSON:API-style
    APIs often support this even when only the by-id form is shown
    in the portal's example.
    """
    url = "https://api.at.govt.nz/gtfs/v3/routes"
    response = requests.get(url, headers=HEADERS, timeout=15)
    print(f"GET /gtfs/v3/routes -> status {response.status_code}")
    if response.status_code != 200:
        print(response.text[:500])
        return None
    return response.json()


def find_northern_express(payload):
    if payload is None:
        return
    records = payload.get("data", [])
    print(f"Total routes returned: {len(records)}")
    matches = [
        r for r in records
        if "NX" in (r["attributes"].get("route_short_name") or "")
        or "Northern Express" in (r["attributes"].get("route_long_name") or "")
    ]
    print(f"\nRoutes matching 'NX' or 'Northern Express': {len(matches)}")
    for r in matches:
        attrs = r["attributes"]
        print(f"  id={r['id']}  short={attrs.get('route_short_name')}  long={attrs.get('route_long_name')}")


if __name__ == "__main__":
    payload = try_list_routes()
    find_northern_express(payload)