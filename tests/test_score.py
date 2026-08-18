"""
Test score_feed, percentile_rank and status_tier logic.
"""


from datetime import datetime, timezone

from score import percentile_rank, status_tier, score_feed
from config import day_type, time_bucket


def fake_fetch_normal(max_attempts, backoff_base_seconds, timeout=10):
    return {
        "response": {
            "entity": [
                {
                    "trip_update": {
                        "trip": {
                            "route_id": "NX1-203",
                            "direction_id": 0,
                            "trip_id": "t1",
                            "schedule_relationship": 0,
                        },
                        "delay": 30,
                        "stop_time_update": {
                            "stop_id": "s1",
                            "departure": {"delay": 45},
                        },
                    }
                }
            ]
        }
    }


def fake_fetch_extreme(max_attempts, backoff_base_seconds, timeout=10):
    return {
        "response": {
            "entity": [
                {
                    "trip_update": {
                        "trip": {
                            "route_id": "NX1-203",
                            "direction_id": 0,
                            "trip_id": "t1",
                            "schedule_relationship": 0,
                        },
                        "delay": 30,
                        "stop_time_update": {
                            "stop_id": "s1",
                            "departure": {"delay": 4000},
                        },
                    }
                }
            ]
        }
    }


def fake_fetch_malformed(max_attempts, backoff_base_seconds, timeout=10):
    return {
        "response": {
            "entity": [{"trip_update": "this is not a dict"},
                {
                    "trip_update": {
                        "trip": {
                            "route_id": "NX1-203",
                            "direction_id": 0,
                            "trip_id": "t1",
                            "schedule_relationship": 0,
                        },
                        "delay": 30,
                        "stop_time_update": {
                            "stop_id": "s1",
                            "departure": {"delay": 45},
                        },
                    }
                }
            ]
        }
    }




def test_score_feed_scores_normal_observation(monkeypatch):
    monkeypatch.setattr("score.fetch_at_feed", fake_fetch_normal)
    current = datetime.now(timezone.utc)
    current_day = day_type(current)
    current_bucket = time_bucket(current)
    baselines = {("NX1-203", 0, "s1", current_day, current_bucket): {"n": 20, "median": 30, "iqr_low": 25, "iqr_high": 35, "sorted_delays": [29, 33, 41, 48, 50]}}

    result = score_feed(baselines)

    assert result["observations"][0]["route_id"] == "NX1-203"
    assert result["observations"][0]["delay_seconds"] == 45
    assert result["observations"][0]["percentile"] == 60
    assert result["observations"][0]["tier"] == "normal"


def test_score_feed_scores_no_data_observation(monkeypatch):
    monkeypatch.setattr("score.fetch_at_feed", fake_fetch_normal)
    current = datetime.now(timezone.utc)
    current_day = day_type(current)
    current_bucket = time_bucket(current)
    baselines = {("NX1-207", 0, "s3", current_day, current_bucket): {"n": 20, "median": 30, "iqr_low": 25, "iqr_high": 35, "sorted_delays": []}}

    result = score_feed(baselines)

    assert result["observations"][0]["route_id"] == "NX1-203"
    assert result["observations"][0]["delay_seconds"] == 45
    assert result["observations"][0]["percentile"] == None
    assert result["observations"][0]["tier"] == "no_data"


def test_score_feed_scores_extreme_observation(monkeypatch):
    monkeypatch.setattr("score.fetch_at_feed", fake_fetch_extreme)
    current = datetime.now(timezone.utc)
    current_day = day_type(current)
    current_bucket = time_bucket(current)
    baselines = {("NX1-203", 0, "s1", current_day, current_bucket): {"n": 20, "median": 30, "iqr_low": 25, "iqr_high": 35, "sorted_delays": [20, 25, 30, 35, 40]}}

    result = score_feed(baselines)

    assert result["observations"][0]["route_id"] == "NX1-203"
    assert result["observations"][0]["delay_seconds"] == 4000
    assert result["observations"][0]["percentile"] == None
    assert result["observations"][0]["tier"] == "extreme"


def test_score_feed_scores_malformed_observation(monkeypatch):
    monkeypatch.setattr("score.fetch_at_feed", fake_fetch_malformed)
    current = datetime.now(timezone.utc)
    current_day = day_type(current)
    current_bucket = time_bucket(current)
    baselines = {("NX1-203", 0, "s1", current_day, current_bucket): {"n": 20, "median": 30, "iqr_low": 25, "iqr_high": 35, "sorted_delays": [20, 25, 30, 35, 40]}}

    result = score_feed(baselines)

    assert result["observations"][0]["route_id"] == "NX1-203"
    assert result["observations"][0]["delay_seconds"] == 45
    assert result["observations"][0]["percentile"] == 100
    assert result["observations"][0]["tier"] == "very_late"


def test_percentile_rank_empty_list():
    assert percentile_rank(15, []) == None

def test_percentile_rank_below_all_values():
    assert percentile_rank(1, [3, 5, 6, 12, 99]) == 0

def test_percentile_rank_above_all_values():
    assert percentile_rank(103, [3, 5, 6, 12, 99]) == 100

def test_percentile_rank_ties_count_as_not_below():
    sorted_values = [10, 20, 20, 20, 30]
    assert percentile_rank(20, sorted_values) == 20.0  # 1 of 5 strictly below

def test_status_tier_none():
    assert status_tier(None) == "unknown"

def test_status_tier_below_normal_boundrary():
    assert status_tier(74.9) == "normal"

def test_status_tier_at_normal_boundrary():
    assert status_tier(75) == "late"

def test_status_tier_at_late_boundrary():
    assert status_tier(95) == "very_late"
