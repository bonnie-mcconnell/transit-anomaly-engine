import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AT_API_KEY"]
URL = "https://api.at.govt.nz/realtime/legacy/"

def main():
    response = requests.get(
        URL,
        headers={"Ocp-Apim-Subscription-Key": API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    entities = data["response"]["entity"]
    print(f"Total entities in feed: {len(entities)}")

    trip_updates = [e for e in entities if "trip_update" in e]
    vehicle_positions = [e for e in entities if "vehicle" in e and "trip_update" not in e]
    print(f"Trip updates: {len(trip_updates)}")
    print(f"Vehicle positions: {len(vehicle_positions)}")

    print("\nFirst 3 trip updates, raw:\n")
    for entity in trip_updates[:3]:
        print(entity)
        print("---")

if __name__ == "__main__":
    main()