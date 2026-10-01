"""Deterministic scorers for TriageIQ eval results. Day-7 implementation.

Five scoring families (per scope spec §5):
  1. ESI strict accuracy — predicted == canonical
  2. ESI ±1 lenient accuracy — |predicted - canonical| <= 1
  3. Critical-miss rate (SAFETY METRIC) — gold ESI <= 2 AND predicted >= 3
  4. Overtriage rate (RESOURCE METRIC) — gold ESI >= 4 AND predicted <= 2
  5. Care-area accuracy — predicted in scenario.expected_care_areas

Then aggregate_branch_metrics() folds per-scenario scores into a BranchMetrics
and compute_ab_lift() produces ABLiftResult per metric family.

Critical-miss-rate is the LOAD-BEARING metric. An architecture that scores
+5pp on strict accuracy but doubles critical-miss-rate is worse, not better,
for clinical use. Strict accuracy is a vanity metric; critical-miss-rate is
the patient-safety metric.
"""

from __future__ import annotations

from collections import defaultdict

from eval.schemas import (
    ABLiftResult,
    BranchMetrics,
    Scenario,
    ScenarioResult,
    TriageScore,
)

LIFT_THRESHOLD = 0.05  # |lift| <= 0.05 is "equivalent" (per scope spec §5)


# ---------------------------------------------------------------------------
# Per-scenario score
# ---------------------------------------------------------------------------


def _critical_flag_coverage(predicted_flags: list[str], expected_flags: list[str]) -> float:
    """Fraction of expected critical flags mentioned in predicted flags.

    Substring match either way (predicted contains expected, OR expected
    contains predicted). 1.0 if expected_flags is empty.
    """
    if not expected_flags:
        return 1.0
    predicted_lower = [p.lower() for p in predicted_flags]
    expected_lower = [e.lower() for e in expected_flags]
    hits = 0
    for exp in expected_lower:
        for pred in predicted_lower:
            if exp in pred or pred in exp:
                hits += 1
                break
    return hits / len(expected_lower)


def score_triage(scenario: Scenario, result: ScenarioResult) -> TriageScore:
    """All 5 scoring families on one (scenario, run) pair. Returns TriageScore.

    Errored runs (result.error set) score as worst-case: not strict, not lenient,
    is_critical_miss=True if gold high-acuity, is_overtriage=False, care_area=False,
    coverage=0. This penalizes architectures that crash on hard cases.
    """
    gold_esi = scenario.expected_esi
    acceptable_esi = set(scenario.acceptable_esi or [gold_esi])
    expected_care_areas = set(scenario.expected_care_areas or [])
    expected_flags = scenario.expected_critical_flags or []

    if result.output is None:
        return TriageScore(
            esi_strict_match=False,
            esi_lenient_match=False,
            is_critical_miss=(gold_esi in (1, 2)),
            is_overtriage=False,
            care_area_match=False,
            critical_flag_coverage=0.0,
        )

    predicted_esi = result.output.esi_score
    strict = predicted_esi in acceptable_esi
    lenient = any(abs(predicted_esi - e) <= 1 for e in acceptable_esi)

    is_critical_miss = (gold_esi in (1, 2)) and (predicted_esi >= 3)
    is_overtriage = (gold_esi in (4, 5)) and (predicted_esi <= 2)
    is_undertriage_any = predicted_esi > max(acceptable_esi)
    is_overtriage_any = predicted_esi < min(acceptable_esi)

    care_area_match = (
        result.output.care_area is not None
        and result.output.care_area in expected_care_areas
    )

    coverage = _critical_flag_coverage(result.output.critical_flags, expected_flags)

    return TriageScore(
        esi_strict_match=strict,
        esi_lenient_match=lenient,
        is_critical_miss=is_critical_miss,
        is_overtriage=is_overtriage,
        is_undertriage_any=is_undertriage_any,
        is_overtriage_any=is_overtriage_any,
        care_area_match=care_area_match,
        critical_flag_coverage=coverage,
    )


# ---------------------------------------------------------------------------
# Per-rep aggregation
# ---------------------------------------------------------------------------


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _per_scenario_metrics(
    scenario: Scenario,
    rep_results: list[ScenarioResult],
) -> dict[str, float] | None:
    """Average across the successful reps for one scenario.

    Errored reps (API failures, exhausted credits, timeouts) are excluded
    rather than scored as worst case: an outage says nothing about the
    architecture. Returns None when every rep errored so the scenario drops
    out and is listed in the report's completeness section.
    """
    strict_vals: list[float] = []
    lenient_vals: list[float] = []
    crit_miss_vals: list[float] = []
    overtri_vals: list[float] = []
    under_any: list[float] = []
    over_any: list[float] = []
    schema_vals: list[float] = []
    care_vals: list[float] = []
    cov_vals: list[float] = []

    for r in rep_results:
        if r.error or r.output is None:
            continue
        ts = score_triage(scenario, r)
        under_any.append(1.0 if ts.is_undertriage_any else 0.0)
        over_any.append(1.0 if ts.is_overtriage_any else 0.0)
        schema_vals.append(1.0 if r.schema_valid else 0.0)
        strict_vals.append(1.0 if ts.esi_strict_match else 0.0)
        lenient_vals.append(1.0 if ts.esi_lenient_match else 0.0)
        crit_miss_vals.append(1.0 if ts.is_critical_miss else 0.0)
        overtri_vals.append(1.0 if ts.is_overtriage else 0.0)
        care_vals.append(1.0 if ts.care_area_match else 0.0)
        cov_vals.append(ts.critical_flag_coverage)

    if not strict_vals:
        return None
    return {
        "esi_strict": _avg(strict_vals),
        "esi_lenient": _avg(lenient_vals),
        "is_critical_miss": _avg(crit_miss_vals),
        "is_overtriage": _avg(overtri_vals),
        "is_undertriage_any": _avg(under_any),
        "is_overtriage_any": _avg(over_any),
        "schema_valid": _avg(schema_vals),
        "care_area_match": _avg(care_vals),
        "critical_flag_coverage": _avg(cov_vals),
    }


def _group_by_scenario(
    results: list[ScenarioResult],
) -> dict[str, list[ScenarioResult]]:
    grouped: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        grouped[r.scenario_id].append(r)
    return grouped


# ---------------------------------------------------------------------------
# Branch aggregation
# ---------------------------------------------------------------------------


def aggregate_branch_metrics(
    scenarios: list[Scenario],
    results: list[ScenarioResult],
    branch: str,
    n_reps: int,
) -> BranchMetrics:
    """Roll up per-scenario per-rep results into one BranchMetrics.

    Macro-averages over scenarios within each tier, then over tiers for
    overall scores (each tier weighted equally — prevents the larger
    clear_esi_1_2 tier from dominating).

    Critical-miss-rate is scoped to high-acuity scenarios (gold ESI <= 2).
    Overtriage rate is scoped to low-acuity scenarios (gold ESI >= 4).
    """
    scenario_by_id = {s.id: s for s in scenarios}
    results_by_scenario = _group_by_scenario(results)

    per_tier_scores: dict[str, list[dict[str, float]]] = defaultdict(list)
    high_acuity_crit_miss: list[float] = []
    low_acuity_overtriage: list[float] = []
    n_errored = sum(1 for r in results if r.error)
    dropped: list[str] = []

    for scenario_id, rep_results in results_by_scenario.items():
        scenario = scenario_by_id.get(scenario_id)
        if scenario is None:
            continue
        per_s = _per_scenario_metrics(scenario, rep_results)
        if per_s is None:
            dropped.append(scenario_id)
            continue
        per_tier_scores[scenario.tier].append(per_s)

        if scenario.expected_esi in (1, 2):
            high_acuity_crit_miss.append(per_s["is_critical_miss"])
        if scenario.expected_esi in (4, 5):
            low_acuity_overtriage.append(per_s["is_overtriage"])

    # Per-tier averages.
    per_tier_metrics: dict[str, dict[str, float]] = {}
    for tier, scenario_scores in per_tier_scores.items():
        per_tier_metrics[tier] = {
            "n_scenarios": float(len(scenario_scores)),
            "esi_strict": _avg([s["esi_strict"] for s in scenario_scores]),
            "esi_lenient": _avg([s["esi_lenient"] for s in scenario_scores]),
            "is_critical_miss": _avg([s["is_critical_miss"] for s in scenario_scores]),
            "is_overtriage": _avg([s["is_overtriage"] for s in scenario_scores]),
            "is_undertriage_any": _avg([s["is_undertriage_any"] for s in scenario_scores]),
            "is_overtriage_any": _avg([s["is_overtriage_any"] for s in scenario_scores]),
            "schema_valid": _avg([s["schema_valid"] for s in scenario_scores]),
            "care_area_match": _avg([s["care_area_match"] for s in scenario_scores]),
            "critical_flag_coverage": _avg([s["critical_flag_coverage"] for s in scenario_scores]),
        }

    # Overall = macro-average across tiers (each tier weighted equally).
    tiers = list(per_tier_metrics.keys())

    def _across_tiers(field: str) -> float:
        return _avg([per_tier_metrics[t][field] for t in tiers])

    return BranchMetrics(
        branch=branch,  # type: ignore[arg-type]
        n_scenarios=sum(int(per_tier_metrics[t]["n_scenarios"]) for t in tiers),
        n_reps=n_reps,
        esi_strict_acc=_across_tiers("esi_strict"),
        esi_lenient_acc=_across_tiers("esi_lenient"),
        critical_miss_rate=_avg(high_acuity_crit_miss),
        overtriage_rate=_avg(low_acuity_overtriage),
        care_area_acc=_across_tiers("care_area_match"),
        critical_flag_coverage_mean=_across_tiers("critical_flag_coverage"),
        undertriage_any_rate=_across_tiers("is_undertriage_any"),
        overtriage_any_rate=_across_tiers("is_overtriage_any"),
        schema_valid_rate=_across_tiers("schema_valid"),
        per_tier=per_tier_metrics,
        n_errored_runs=n_errored,
        scenarios_dropped=sorted(dropped),
    )


# ---------------------------------------------------------------------------
# A/B lift
# ---------------------------------------------------------------------------


def _interpret_lift(lift: float, threshold: float = LIFT_THRESHOLD, *, lower_is_better: bool = False) -> str:
    """Map a lift value to one of {full_wins, stripped_wins, equivalent}.

    For lower-is-better metrics (critical-miss-rate, overtriage-rate), flip the direction.
    """
    if lower_is_better:
        lift = -lift
    if lift > threshold:
        return "full_wins"
    if lift < -threshold:
        return "stripped_wins"
    return "equivalent"


def compute_ab_lift(full: BranchMetrics, stripped: BranchMetrics) -> list[ABLiftResult]:
    """Per-metric A/B lift between FULL and STRIPPED branches."""
    items = [
        ("esi_strict_acc", full.esi_strict_acc, stripped.esi_strict_acc, False),
        ("esi_lenient_acc", full.esi_lenient_acc, stripped.esi_lenient_acc, False),
        ("critical_miss_rate", full.critical_miss_rate, stripped.critical_miss_rate, True),
        ("overtriage_rate", full.overtriage_rate, stripped.overtriage_rate, True),
        ("undertriage_any_rate", full.undertriage_any_rate, stripped.undertriage_any_rate, True),
        ("overtriage_any_rate", full.overtriage_any_rate, stripped.overtriage_any_rate, True),
        ("care_area_acc", full.care_area_acc, stripped.care_area_acc, False),
        ("critical_flag_coverage_mean", full.critical_flag_coverage_mean, stripped.critical_flag_coverage_mean, False),
    ]
    out: list[ABLiftResult] = []
    for name, full_v, stripped_v, lower_better in items:
        lift = full_v - stripped_v
        interp = _interpret_lift(lift, lower_is_better=lower_better)
        out.append(ABLiftResult(
            metric=name,
            full_score=full_v,
            stripped_score=stripped_v,
            lift=lift,
            interpretation=interp,  # type: ignore[arg-type]
            candidate=full.branch, baseline=stripped.branch,
        ))
    return out
