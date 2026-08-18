"""
Shared client for AT's realtime GTFS feed.

Both ingest.py (single poll, writes to DB) and score.py (live dashboard
scoring) need to fetch the feed the same way.

AT_API_KEY is read from environment inside fetch_at_feed(), not at
import time, so a caller can import this module even if the key isn't 
set yet and decide for itself how to handle a missing key at call time
(for example score.py catches KeyError and returns a friendly dashboard
error instead of crashing Flask app on startup).
"""

import os
import time
from datetime import datetime

import requests

FEED_URL = "https://api.at.govt.nz/realtime/legacy/"


def fetch_at_feed(max_attempts, backoff_base_seconds, timeout=10):
    """
    Fetch live AT feed. Retries up to max_attempts times on network 
    errors, with exponential backoff between attempts.

    Raises final exception if all attempts fail, so the caller can log
    it and exit cleanly (ingest.py) or show dashboard error message (score.py).

    Raises KeyError immediately without retrying if AT_API_KEY isn't set.
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            headers = {"Ocp-Apim-Subscription-Key": os.environ["AT_API_KEY"]}
            resp = requests.get(FEED_URL, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < max_attempts - 1:
                wait = backoff_base_seconds * (2 ** attempt)
                print(
                    f"{datetime.now().isoformat()}: attempt {attempt + 1} failed "
                    f"({e}), retrying in {wait} s"
                )
                time.sleep(wait)

    raise last_exc or RuntimeError("fetch_at_feed called with max_attempts <= 0")