"""Deterministic scorer tests. Zero LLM calls, CI-safe.

Covers each scoring family across the edge cases that matter:
- ESI strict + lenient on matched scenarios
- Critical-miss-rate (the safety metric) — gold ESI<=2 AND predicted>=3
- Overtriage-rate — gold ESI>=4 AND predicted<=2
- Care-area accuracy with the expected_care_areas set
- Critical-flag coverage via substring match
- Branch aggregation across tiers (macro average)
- A/B lift interpretation including lower-is-better metrics
"""

from __future__ import annotations

import pytest

from eval.schemas import (
    Scenario,
    ScenarioResult,
    TriageOutput,
)
from eval.scorers import (
    _critical_flag_coverage,
    _interpret_lift,
    aggregate_branch_metrics,
    compute_ab_lift,
    score_triage,
)


def _scen(
    esi: int = 2,
    tier: str = "clear_esi_1_2",
    id_: str = "clear_esi_1_2_001",
    acceptable_esi: list[int] | None = None,
    expected_care_areas: list[str] | None = None,
    expected_critical_flags: list[str] | None = None,
    is_critical_miss_test: bool = False,
) -> Scenario:
    return Scenario(
        id=id_,
        tier=tier,  # type: ignore[arg-type]
        description="Test fixture scenario for scorer tests.",
        patient={
            "name": "Test Patient",
            "chief_complaint": "Test complaint with some clinical detail.",
            "vitals": {"bp": "120/80", "hr": 80},
        },
        expected_esi=esi,  # type: ignore[arg-type]
        acceptable_esi=acceptable_esi or [],  # type: ignore[arg-type]
        expected_care_areas=expected_care_areas or [],  # type: ignore[arg-type]
        expected_critical_flags=expected_critical_flags or [],
        is_critical_miss_test=is_critical_miss_test,
    )


def _result(
    scenario: Scenario,
    esi: int | None = None,
    care_area: str | None = None,
    critical_flags: list[str] | None = None,
    error: str | None = None,
    branch: str = "full",
    rep: int = 0,
) -> ScenarioResult:
    output = None
    if esi is not None:
        output = TriageOutput(
            esi_score=esi,  # type: ignore[arg-type]
            care_area=care_area,  # type: ignore[arg-type]
            patient_summary="Test summary",
            critical_flags=critical_flags or [],
            rationale="Test rationale",
        )
    return ScenarioResult(
        scenario_id=scenario.id,
        tier=scenario.tier,
        branch=branch,  # type: ignore[arg-type]
        rep=rep,
        output=output,
        error=error,
    )


# ---------------------------------------------------------------------------
# Critical-flag coverage
# ---------------------------------------------------------------------------


def test_critical_flag_coverage_perfect():
    assert _critical_flag_coverage(["acs", "stemi"], ["acs"]) == 1.0


def test_critical_flag_coverage_partial():
    cov = _critical_flag_coverage(["acs"], ["acs", "stemi"])
    assert cov == 0.5


def test_critical_flag_coverage_none_expected():
    assert _critical_flag_coverage([], []) == 1.0
    assert _critical_flag_coverage(["random"], []) == 1.0


def test_critical_flag_coverage_substring_match():
    """'acs' in 'acs_workup' counts as a hit."""
    assert _critical_flag_coverage(["acs_workup"], ["acs"]) == 1.0


# ---------------------------------------------------------------------------
# score_triage — strict + lenient ESI
# ---------------------------------------------------------------------------


def test_score_triage_strict_exact_match():
    scenario = _scen(esi=2, acceptable_esi=[2])
    result = _result(scenario, esi=2, care_area="resus")
    ts = score_triage(scenario, result)
    assert ts.esi_strict_match is True
    assert ts.esi_lenient_match is True
    assert ts.is_critical_miss is False
    assert ts.is_overtriage is False


def test_score_triage_strict_inside_acceptable():
    scenario = _scen(esi=2, acceptable_esi=[1, 2])
    result = _result(scenario, esi=1)
    ts = score_triage(scenario, result)
    assert ts.esi_strict_match is True  # 1 in acceptable {1, 2}


def test_score_triage_lenient_within_1():
    scenario = _scen(esi=2, acceptable_esi=[2])
    result = _result(scenario, esi=3)  # 1 off from 2
    ts = score_triage(scenario, result)
    assert ts.esi_strict_match is False
    assert ts.esi_lenient_match is True


def test_score_triage_strict_no_lenient():
    scenario = _scen(esi=2, acceptable_esi=[2])
    result = _result(scenario, esi=5)  # 3 off from 2
    ts = score_triage(scenario, result)
    assert ts.esi_strict_match is False
    assert ts.esi_lenient_match is False


# ---------------------------------------------------------------------------
# Critical-miss rate (THE SAFETY METRIC)
# ---------------------------------------------------------------------------


def test_score_triage_critical_miss_gold_1_predicted_3():
    """ESI 1 patient classified as ESI 3 = critical miss."""
    scenario = _scen(esi=1, acceptable_esi=[1, 2])
    result = _result(scenario, esi=3)
    ts = score_triage(scenario, result)
    assert ts.is_critical_miss is True


def test_score_triage_critical_miss_gold_2_predicted_4():
    """ESI 2 patient classified as ESI 4 = critical miss."""
    scenario = _scen(esi=2)
    result = _result(scenario, esi=4)
    ts = score_triage(scenario, result)
    assert ts.is_critical_miss is True


def test_score_triage_not_critical_miss_gold_2_predicted_2():
    scenario = _scen(esi=2)
    result = _result(scenario, esi=2)
    ts = score_triage(scenario, result)
    assert ts.is_critical_miss is False


def test_score_triage_not_critical_miss_gold_3_predicted_5():
    """gold ESI 3, NOT a critical miss (gold must be 1-2)."""
    scenario = _scen(esi=3, tier="ambiguous")
    result = _result(scenario, esi=5)
    ts = score_triage(scenario, result)
    assert ts.is_critical_miss is False


# ---------------------------------------------------------------------------
# Overtriage rate
# ---------------------------------------------------------------------------


def test_score_triage_overtriage_gold_5_predicted_1():
    """ESI 5 patient classified as ESI 1 = overtriage."""
    scenario = _scen(esi=5, tier="clear_esi_4_5")
    result = _result(scenario, esi=1)
    ts = score_triage(scenario, result)
    assert ts.is_overtriage is True


def test_score_triage_not_overtriage_gold_3_predicted_1():
    """gold ESI 3, NOT overtriage (gold must be 4-5)."""
    scenario = _scen(esi=3, tier="ambiguous")
    result = _result(scenario, esi=1)
    ts = score_triage(scenario, result)
    assert ts.is_overtriage is False


# ---------------------------------------------------------------------------
# Care-area accuracy
# ---------------------------------------------------------------------------


def test_score_triage_care_area_match():
    scenario = _scen(esi=2, expected_care_areas=["resus", "trauma_bay"])
    result = _result(scenario, esi=2, care_area="resus")
    ts = score_triage(scenario, result)
    assert ts.care_area_match is True


def test_score_triage_care_area_mismatch():
    scenario = _scen(esi=2, expected_care_areas=["resus", "trauma_bay"])
    result = _result(scenario, esi=2, care_area="general")
    ts = score_triage(scenario, result)
    assert ts.care_area_match is False


def test_score_triage_care_area_none():
    scenario = _scen(esi=2, expected_care_areas=["resus"])
    result = _result(scenario, esi=2, care_area=None)
    ts = score_triage(scenario, result)
    assert ts.care_area_match is False


# ---------------------------------------------------------------------------
# Errored result handling
# ---------------------------------------------------------------------------


def test_score_triage_errored_run_high_acuity_counts_as_critical_miss():
    """Pipeline error on a gold-1 patient counts as a critical miss."""
    scenario = _scen(esi=1)
    result = _result(scenario, esi=None, error="API outage")
    ts = score_triage(scenario, result)
    assert ts.is_critical_miss is True
    assert ts.esi_strict_match is False
    assert ts.care_area_match is False


def test_score_triage_errored_run_low_acuity_not_overtriage():
    """Pipeline error on a gold-5 patient is NOT an overtriage (no prediction)."""
    scenario = _scen(esi=5, tier="clear_esi_4_5")
    result = _result(scenario, esi=None, error="API outage")
    ts = score_triage(scenario, result)
    assert ts.is_overtriage is False  # no prediction = no overtriage classification


# ---------------------------------------------------------------------------
# Branch aggregation
# ---------------------------------------------------------------------------


def test_aggregate_branch_metrics_perfect_across_two_tiers():
    """clear_esi_1_2 perfect + clear_esi_4_5 perfect = overall 100%."""
    s1 = _scen(esi=2, tier="clear_esi_1_2", id_="clear_esi_1_2_001",
               expected_care_areas=["resus"])
    s2 = _scen(esi=5, tier="clear_esi_4_5", id_="clear_esi_4_5_001",
               expected_care_areas=["waiting"])
    r1 = _result(s1, esi=2, care_area="resus")
    r2 = _result(s2, esi=5, care_area="waiting")
    metrics = aggregate_branch_metrics(
        scenarios=[s1, s2], results=[r1, r2], branch="full", n_reps=1,
    )
    assert metrics.esi_strict_acc == 1.0
    assert metrics.critical_miss_rate == 0.0
    assert metrics.overtriage_rate == 0.0
    assert metrics.care_area_acc == 1.0


def test_aggregate_branch_metrics_critical_miss_isolated_to_high_acuity():
    """Critical-miss-rate denominator is only high-acuity scenarios."""
    high = _scen(esi=2, tier="clear_esi_1_2", id_="clear_esi_1_2_001")
    low = _scen(esi=5, tier="clear_esi_4_5", id_="clear_esi_4_5_001")
    # FULL: 1 critical miss on high-acuity + correct on low-acuity.
    r_high = _result(high, esi=4)  # critical miss
    r_low = _result(low, esi=5)
    metrics = aggregate_branch_metrics(
        scenarios=[high, low], results=[r_high, r_low], branch="full", n_reps=1,
    )
    assert metrics.critical_miss_rate == 1.0  # 1 of 1 high-acuity scenarios
    assert metrics.overtriage_rate == 0.0


def test_aggregate_branch_metrics_per_tier_breakdown():
    """A failure on one tier shouldn't pollute the other tier's score."""
    s1 = _scen(esi=2, tier="clear_esi_1_2", id_="clear_esi_1_2_001")
    s2 = _scen(esi=5, tier="clear_esi_4_5", id_="clear_esi_4_5_001")
    # Tier 1 perfect; tier 2 fails.
    r1 = _result(s1, esi=2, care_area="resus")
    r2 = _result(s2, esi=1)  # overtriage
    metrics = aggregate_branch_metrics(
        scenarios=[s1, s2], results=[r1, r2], branch="full", n_reps=1,
    )
    assert metrics.per_tier["clear_esi_1_2"]["esi_strict"] == 1.0
    assert metrics.per_tier["clear_esi_4_5"]["esi_strict"] == 0.0
    assert metrics.per_tier["clear_esi_4_5"]["is_overtriage"] == 1.0


# ---------------------------------------------------------------------------
# A/B lift interpretation
# ---------------------------------------------------------------------------


def test_interpret_lift_higher_is_better():
    assert _interpret_lift(0.10, lower_is_better=False) == "full_wins"
    assert _interpret_lift(-0.10, lower_is_better=False) == "stripped_wins"
    assert _interpret_lift(0.02, lower_is_better=False) == "equivalent"


def test_interpret_lift_lower_is_better_inverted():
    """For critical-miss-rate / overtriage-rate: lower = better."""
    assert _interpret_lift(0.10, lower_is_better=True) == "stripped_wins"
    assert _interpret_lift(-0.10, lower_is_better=True) == "full_wins"
    assert _interpret_lift(0.02, lower_is_better=True) == "equivalent"


def test_compute_ab_lift_emits_one_per_metric():
    """compute_ab_lift returns 6 entries — one per metric family."""
    s = _scen(esi=2, tier="clear_esi_1_2")
    full_metrics = aggregate_branch_metrics(
        scenarios=[s], results=[_result(s, esi=2, care_area="resus")],
        branch="full", n_reps=1,
    )
    stripped_metrics = aggregate_branch_metrics(
        scenarios=[s], results=[_result(s, esi=3, branch="stripped")],
        branch="stripped", n_reps=1,
    )
    lifts = compute_ab_lift(full_metrics, stripped_metrics)
    assert len(lifts) == 6
    names = {lift.metric for lift in lifts}
    assert "esi_strict_acc" in names
    assert "critical_miss_rate" in names
    assert "overtriage_rate" in names
    assert "care_area_acc" in names


def test_compute_ab_lift_critical_miss_rate_lower_is_better():
    """Test critical-miss-rate lift: FULL=0.0 vs STRIPPED=0.3 → FULL wins."""
    high1 = _scen(esi=2, tier="clear_esi_1_2", id_="clear_esi_1_2_001")
    high2 = _scen(esi=1, tier="clear_esi_1_2", id_="clear_esi_1_2_002")
    high3 = _scen(esi=2, tier="clear_esi_1_2", id_="clear_esi_1_2_003")
    # FULL: 0/3 critical misses
    full_results = [
        _result(high1, esi=2), _result(high2, esi=1), _result(high3, esi=2),
    ]
    # STRIPPED: 1/3 critical misses
    stripped_results = [
        _result(high1, esi=2, branch="stripped"),
        _result(high2, esi=1, branch="stripped"),
        _result(high3, esi=4, branch="stripped"),  # critical miss
    ]
    full_metrics = aggregate_branch_metrics(
        scenarios=[high1, high2, high3], results=full_results,
        branch="full", n_reps=1,
    )
    stripped_metrics = aggregate_branch_metrics(
        scenarios=[high1, high2, high3], results=stripped_results,
        branch="stripped", n_reps=1,
    )
    lifts = compute_ab_lift(full_metrics, stripped_metrics)
    cm = next(lift for lift in lifts if lift.metric == "critical_miss_rate")
    # Lower is better; FULL had 0 misses, STRIPPED had 1/3 — FULL wins.
    assert cm.full_score == pytest.approx(0.0)
    assert cm.stripped_score == pytest.approx(1.0 / 3)
    assert cm.interpretation == "full_wins"
