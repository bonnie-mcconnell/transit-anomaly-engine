from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TRACKED_ROUTES = {"NX1-203", "NX2-207"}
EXTREME_DELAY_THRESHOLD = 3600

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")
BUCKET_MINUTES = 60
MIN_N = 20

# how many times to attempt fetch feed before giving up
# delay between attempts: 1s, then 4s (exponential backoff, base 2, starting at 1s)
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1


def to_local(dt):
    """
    Convert a UTC timestamp string to Auckland local time.
    Accepts either ISO-format string (polled_at values read back
    from DB) or datetime (datetime.now(timezone.utc) from score.py).
    """
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(AUCKLAND_TZ)


def time_bucket(dt):
    """Return the time bucket for a given datetime or timestamp string."""
    local = to_local(dt)
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES


def day_type(dt):
    """Return 'weekday' or 'weekend' for a given datetime or timestamp string."""
    local = to_local(dt)
    return "weekend" if local.weekday() >= 5 else "weekday"
