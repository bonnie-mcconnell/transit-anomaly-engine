"""
Tests for materialise.py's compute_baseline.
"""

from materialise import compute_baseline


def make_obs(delay, is_extreme=False):
    return {"delay": delay, "is_extreme": is_extreme}


def test_compute_baseline_below_min_n_returns_none():
    # MIN_N is 20, 19 observations shouldn't produce a baseline
    # cold start should produce "no_data" in dashboard
    observations = [make_obs(i) for i in range(19)]
    assert compute_baseline(observations) is None


def test_compute_baseline_at_min_n_returns_baseline():
    observations = [make_obs(i) for i in range(20)]
    assert compute_baseline(observations) is not None


def test_compute_baseline_too_few_non_extreme():
    # AND condition in guard, n_total can pass MIN_N while having 
    # only/mostly extreme data. Should return None
    observations = [make_obs(9999, is_extreme=True) for _ in range(19)]
    observations.append(make_obs(100, is_extreme=False))
    assert len(observations) == 20 # clears MIN_N
    assert compute_baseline(observations) is None


def test_compute_baseline_excludes_extreme_from_stats():
    # 20 normal observations all with the same delay (100), plus 5
    # extreme ones with a wildly different delay. n_total should
    # count all 25, but median should be unaffected by the extreme ones.
    observations = [make_obs(100) for _ in range(20)]
    observations += [make_obs(99999, is_extreme=True) for _ in range(5)]

    result = compute_baseline(observations)
    assert result is not None

    assert result["n_total"] == 25
    assert result["n_baseline"] == 20
    assert result["median_delay"] == 100.0


def test_compute_baseline_median_and_iqr():
    observations = [make_obs(i) for i in range(1, 21)] 

    result = compute_baseline(observations)
    assert result is not None

    assert result["median_delay"] == 10.5
    assert result["iqr_low"] == 6.0
    assert result["iqr_high"] == 16.0



def test_compute_baseline_sorted_delays_are_sorted():
    # score.py percentile_rank() uses bisect on this list,
    # which silently gives wrong answers/errors if its not sorted
    observations = [make_obs(d) for d in [50, 5, 99, 1, 42, 7] + list(range(14))]
    
    result = compute_baseline(observations)
    assert result is not None

    assert result["sorted_delays"] == sorted(result["sorted_delays"])