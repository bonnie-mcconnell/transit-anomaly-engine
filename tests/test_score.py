"""
Test percentile_rank and status_tier logic.
"""

from score import percentile_rank, status_tier


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
