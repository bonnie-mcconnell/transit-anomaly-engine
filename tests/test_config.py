"""
Regression tests for config.py to_local/time_bucket/day_type.

When to_local was called with a raw datetime instead of a string,
score.py's live scoring crashed.
"""

import pytest
from datetime import datetime, timezone

from config import to_local, time_bucket, day_type


@pytest.fixture
def utc_2am_winter():
    """2am UTC in NZ winter (NZST, UTC+12) is 2pm local time."""
    return datetime(2026, 8, 15, 2, 37, tzinfo=timezone.utc)


def test_to_local_accepts_iso_string():
    # how SQLite hands polled_at back
    utc_string = "2026-08-15T02:37:00+00:00"
    result = to_local(utc_string)

    # 2 am UTC in NZ winter = 2pm local
    assert result.hour == 14
    assert result.minute == 37

def test_to_local_accepts_datetime():
    # how postgres returns polled_at and score calls datetime
    dt = datetime(2026, 8, 15, 2, 37, tzinfo=timezone.utc)
    result = to_local(dt)

    assert result.hour == 14
    assert result.minute == 37

def test_to_local_treates_naive_as_utc():
    # how to_local handles datetime with no timezone
    dt = datetime(2026, 8, 15, 2, 37)
    result = to_local(dt)

    assert result.hour == 14
    assert result.minute == 37


def test_time_bucket_converts_to_local(utc_2am_winter):
    assert time_bucket(utc_2am_winter) == 14

def test_day_type_converts_to_local(utc_2am_winter):
    assert day_type(utc_2am_winter) == "weekend"