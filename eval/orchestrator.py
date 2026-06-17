"""Orchestration + report rendering for TriageIQ eval runs.

run_eval() runs the Cartesian product (scenarios × branches × reps), captures
pipeline errors as ScenarioResult.error rather than crashing the run, and
aggregates costs via instrumentation.aggregate_traces.

render_report() generates a markdown report with:
- Headline A/B finding (foregrounding critical-miss-rate, the safety metric)
- Per-branch metrics tables
- Per-tier breakdown (with critical_miss_test tier called out specifically)
- A/B lift table per metric family
- Per-scenario detail (errors and critical misses highlighted)
- Cost / latency totals
- Eval-mode methodology disclosure footer

save_run() writes the report to eval/reports/run_YYYYMMDD_HHMMSS.md and a
sibling latest_run.json (raw results + traces + metrics + lifts).

The default eval-small scenario set spans every tier AND includes
adversarial_001 (the prompt-injection scenario, highest-value-per-call
per ChainPilot precedent) AND critical_miss_test_001 (the safety-critical
silent-MI scenario).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from eval.instrumentation import (
    CallTrace,
    aggregate_traces,
    format_totals,
)
from eval.runners import (
    list_scenarios,
    load_scenario,
    run_full_pipeline,
    run_stripped_pipeline,
)
from eval.schemas import (
    ABLiftResult,
    BranchMetrics,
    Scenario,
    ScenarioResult,
)
from eval.scorers import aggregate_branch_metrics, compute_ab_lift

# 5 scenarios chosen to span all 5 tiers AND include the two highest-value
# single scenarios: adversarial_001 (prompt-injection) + critical_miss_test_001
# (silent MI in elderly diabetic).
DEFAULT_EVAL_SMALL_SCENARIO_IDS: list[str] = [
    "clear_esi_1_2_001",         # STEMI
    "clear_esi_4_5_001",         # Medication refill
    "ambiguous_001",             # Chest pain in 40s smoker
    "critical_miss_test_001",    # Silent MI in elderly diabetic — safety-critical
    "adversarial_001",           # Prompt injection — security-critical
]


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


def _branch_runner(branch: str):
    if branch == "full":
        return run_full_pipeline
    if branch == "stripped":
        return run_stripped_pipeline
    raise ValueError(f"Unknown branch: {branch}")


def run_eval(
    scenario_ids: list[str],
    branches: list[str],
    n_reps: int,
    scenarios_dir: Path,
    model_specialist: str = "claude-haiku-4-5-20251001",
    model_synthesizer: str = "claude-sonnet-4-6",
    on_trace=None,
) -> list[ScenarioResult]:
    """Run scenarios × branches × reps. Errors captured per-result."""
    results: list[ScenarioResult] = []
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.json"
        if not path.exists():
            results.append(
                ScenarioResult(
                    scenario_id=scenario_id,
                    tier="clear_esi_1_2",  # placeholder; record an error
                    branch="full",  # type: ignore[arg-type]
                    rep=0,
                    error=f"Scenario file not found: {path}",
                )
            )
            continue
        scenario = load_scenario(path)
        for branch in branches:
            runner = _branch_runner(branch)
            for rep in range(n_reps):
                if branch == "full":
                    result = runner(
                        scenario=scenario, rep=rep,
                        model_specialist=model_specialist,
                        model_synthesizer=model_synthesizer,
                        on_trace=on_trace,
                    )
                else:
                    result = runner(
                        scenario=scenario, rep=rep,
                        model=model_synthesizer,
                        on_trace=on_trace,
                    )
                results.append(result)
    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_num(x: float) -> str:
    return f"{x:.3f}"


def _headline(lifts: list[ABLiftResult]) -> str:
    """One-paragraph headline framing the A/B finding.

    For TriageIQ, the headline prioritizes critical-miss-rate over ESI strict
    accuracy because critical-miss is the patient-safety metric. If they
    diverge in direction (one wins, the other loses), say so explicitly.
    """
    crit = next((lift for lift in lifts if lift.metric == "critical_miss_rate"), None)
    strict = next((lift for lift in lifts if lift.metric == "esi_strict_acc"), None)
    if crit is None or strict is None:
        return "_(Insufficient metrics to summarize.)_"

    full_strict = _fmt_pct(strict.full_score)
    strip_strict = _fmt_pct(strict.stripped_score)
    strict_pp = strict.lift * 100
    full_cm = _fmt_pct(crit.full_score)
    strip_cm = _fmt_pct(crit.stripped_score)
    cm_pp = crit.lift * 100  # raw lift (positive = full has MORE misses)

    # Lead with critical-miss-rate framing.
    if crit.interpretation == "full_wins":
        cm_verdict = (
            f"**FULL pipeline has a LOWER critical-miss rate** "
            f"({full_cm} vs {strip_cm}, {-cm_pp:+.1f}pp safer). The specialist "
            f"architecture earns its complexity on the patient-safety metric."
        )
    elif crit.interpretation == "stripped_wins":
        cm_verdict = (
            f"**STRIPPED baseline has a LOWER critical-miss rate** "
            f"({strip_cm} vs {full_cm}, {cm_pp:+.1f}pp WORSE on FULL — "
            f"a safety regression). The specialist architecture is mis-classifying "
            f"high-acuity patients more often than the single-prompt baseline; "
            f"clinical use of FULL over STRIPPED would harm patients."
        )
    else:
        cm_verdict = (
            f"**FULL ≈ STRIPPED on critical-miss rate** "
            f"({full_cm} vs {strip_cm}; {cm_pp:+.1f}pp). The architectures "
            f"are equivalent on the load-bearing safety metric."
        )

    # Then add strict-accuracy context.
    if strict.interpretation == "full_wins":
        strict_verdict = (
            f"On ESI strict accuracy, FULL beats STRIPPED by {strict_pp:+.1f}pp "
            f"(FULL: {full_strict} vs STRIPPED: {strip_strict})."
        )
    elif strict.interpretation == "stripped_wins":
        strict_verdict = (
            f"On ESI strict accuracy, STRIPPED beats FULL by {-strict_pp:+.1f}pp "
            f"(FULL: {full_strict} vs STRIPPED: {strip_strict})."
        )
    else:
        strict_verdict = (
            f"On ESI strict accuracy, FULL ≈ STRIPPED ({strict_pp:+.1f}pp; "
            f"FULL: {full_strict} vs STRIPPED: {strip_strict})."
        )

    return f"{cm_verdict}\n\n{strict_verdict}"


def _render_branch_table(metrics: BranchMetrics) -> str:
    return (
        f"### Branch `{metrics.branch}`\n"
        f"_n_scenarios={metrics.n_scenarios}, n_reps={metrics.n_reps}_\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| ESI strict accuracy | {_fmt_pct(metrics.esi_strict_acc)} |\n"
        f"| ESI ±1 lenient accuracy | {_fmt_pct(metrics.esi_lenient_acc)} |\n"
        f"| Critical-miss rate (lower=better) | {_fmt_pct(metrics.critical_miss_rate)} |\n"
        f"| Overtriage rate (lower=better) | {_fmt_pct(metrics.overtriage_rate)} |\n"
        f"| Care-area accuracy | {_fmt_pct(metrics.care_area_acc)} |\n"
        f"| Critical-flag coverage | {_fmt_pct(metrics.critical_flag_coverage_mean)} |\n"
    )


def _render_per_tier_breakdown(full: BranchMetrics, stripped: BranchMetrics) -> str:
    tiers = sorted(set(full.per_tier.keys()) | set(stripped.per_tier.keys()))
    lines = [
        "### Per-tier ESI strict accuracy breakdown",
        "",
        "| Tier | FULL strict | STRIPPED strict | Lift (pp) |",
        "|---|---|---|---|",
    ]
    for tier in tiers:
        f_s = full.per_tier.get(tier, {}).get("esi_strict", 0.0)
        s_s = stripped.per_tier.get(tier, {}).get("esi_strict", 0.0)
        lines.append(
            f"| `{tier}` | {_fmt_pct(f_s)} | {_fmt_pct(s_s)} | "
            f"{(f_s - s_s) * 100:+.1f} |"
        )

    lines.append("")
    lines.append("### Per-tier critical-miss rate (high-acuity tiers only)")
    lines.append("")
    lines.append("| Tier | FULL miss rate | STRIPPED miss rate | Lift (pp) |")
    lines.append("|---|---|---|---|")
    for tier in tiers:
        if tier not in ("clear_esi_1_2", "critical_miss_test", "adversarial"):
            continue
        f_m = full.per_tier.get(tier, {}).get("is_critical_miss", 0.0)
        s_m = stripped.per_tier.get(tier, {}).get("is_critical_miss", 0.0)
        # Negative lift = FULL is safer (lower miss rate).
        lines.append(
            f"| `{tier}` | {_fmt_pct(f_m)} | {_fmt_pct(s_m)} | "
            f"{(f_m - s_m) * 100:+.1f} |"
        )
    return "\n".join(lines)


def _render_lift_table(lifts: list[ABLiftResult]) -> str:
    lines = [
        "### A/B lift (FULL − STRIPPED)",
        "",
        "| Metric | FULL | STRIPPED | Lift | Interpretation |",
        "|---|---|---|---|---|",
    ]
    for lift in lifts:
        is_rate_metric = lift.metric in ("critical_miss_rate", "overtriage_rate")
        full_s = _fmt_pct(lift.full_score)
        strip_s = _fmt_pct(lift.stripped_score)
        lift_s = f"{lift.lift * 100:+.1f}pp"
        annotation = " (lower=better)" if is_rate_metric else ""
        lines.append(
            f"| `{lift.metric}`{annotation} | {full_s} | {strip_s} | {lift_s} | "
            f"`{lift.interpretation}` |"
        )
    return "\n".join(lines)


def _render_per_scenario_detail(
    scenarios: list[Scenario],
    results: list[ScenarioResult],
) -> str:
    by_id = {s.id: s for s in scenarios}
    grouped: dict[tuple[str, str], list[ScenarioResult]] = {}
    for r in results:
        grouped.setdefault((r.scenario_id, r.branch), []).append(r)

    lines = [
        "### Per-scenario detail",
        "",
        "| Scenario | Tier | Branch | Reps | Predicted ESI | Expected ESI | Critical Misses | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (sid, branch), rs in sorted(grouped.items()):
        scenario = by_id.get(sid)
        tier = scenario.tier if scenario else "?"
        expected = scenario.expected_esi if scenario else "?"
        pred = [
            str(r.output.esi_score) if r.output else "?"
            for r in rs
        ]
        n_errors = sum(1 for r in rs if r.error)
        # Count critical misses on this scenario.
        if scenario and scenario.expected_esi in (1, 2):
            n_misses = sum(
                1 for r in rs
                if r.output and r.output.esi_score >= 3
            )
        else:
            n_misses = 0
        miss_marker = f"**{n_misses}**" if n_misses > 0 else "0"
        lines.append(
            f"| `{sid}` | {tier} | `{branch}` | {len(rs)} | "
            f"{','.join(pred)} | {expected} | {miss_marker} | {n_errors} |"
        )
    return "\n".join(lines)


def _render_methodology_footer() -> str:
    return (
        "### Methodology disclosure\n\n"
        "- **Tool calls are mocked.** `get_patient_data`, `search_protocols`, "
        "and `check_bed_availability` return canned data tied to the scenario. "
        "This eval tests reasoning, not EHR/RAG/bed-state robustness. "
        "Documented in `eval/RUBRIC.md` §5 and `eval/runners.py`.\n"
        "- **Prompts mirrored, not imported.** `eval/prompts.py` keeps a copy of "
        "`backend/agents.py`'s 5 system prompts; a drift-detection test "
        "(`tests/test_runners.py::test_specialist_prompts_match_backend_agents_py`) "
        "compares them.\n"
        "- **Scorers are deterministic.** No LLM-as-judge. ESI accuracy is "
        "integer comparison; critical-miss is boolean (gold <= 2 AND predicted >= 3); "
        "care-area is set membership.\n"
        "- **Per-tier macro-average.** Overall scores weight each tier equally, "
        "preventing the larger `clear_esi_1_2` tier from dominating.\n"
        "- **Critical-miss rate is the load-bearing safety metric.** An "
        "architecture that scores +5pp on strict accuracy but increases "
        "critical-miss rate is worse, not better, for clinical use. The "
        "`critical_miss_test` tier exists specifically to stress this metric — "
        "those scenarios have surface-benign vitals but life-threatening "
        "underlying conditions (silent MI, posterior stroke, occult sepsis, "
        "PE with normal SpO2, slow-leak AAA).\n"
        "- **Not clinically validated.** This eval scores against the published "
        "rubric, not against ground-truth ED outcomes. Use the result for "
        "architectural decisions, not for clinical claims."
    )


def render_report(
    branch_metrics: list[BranchMetrics],
    lifts: list[ABLiftResult],
    totals_text: str,
    scenario_results: list[ScenarioResult],
    scenarios: list[Scenario] | None = None,
) -> str:
    """Markdown report from aggregated metrics + raw results."""
    full = next((m for m in branch_metrics if m.branch == "full"), None)
    stripped = next((m for m in branch_metrics if m.branch == "stripped"), None)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        f"# TriageIQ Eval Report — {timestamp}",
        "",
        "## Headline",
        "",
        _headline(lifts),
        "",
        "## Branch metrics",
        "",
    ]
    if full is not None:
        parts.append(_render_branch_table(full))
    if stripped is not None:
        parts.append(_render_branch_table(stripped))

    if full is not None and stripped is not None:
        parts.append("")
        parts.append(_render_per_tier_breakdown(full, stripped))
        parts.append("")
        parts.append(_render_lift_table(lifts))

    if scenarios:
        parts.append("")
        parts.append(_render_per_scenario_detail(scenarios, scenario_results))

    parts.append("")
    parts.append("## Cost & latency")
    parts.append("")
    parts.append(totals_text)
    parts.append("")
    parts.append(_render_methodology_footer())
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# save_run
# ---------------------------------------------------------------------------


def _serialize_result(r: ScenarioResult) -> dict:
    return r.model_dump(mode="json")


def _serialize_traces(traces: list[CallTrace]) -> list[dict]:
    return [asdict(t) for t in traces]


def save_run(
    report_md: str,
    snapshot: dict,
    reports_dir: Path,
) -> tuple[Path, Path]:
    """Write the markdown report + latest_run.json snapshot."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = reports_dir / f"run_{timestamp}.md"
    json_path = reports_dir / "latest_run.json"
    md_path.write_text(report_md, encoding="utf-8")
    json_path.write_text(
        json.dumps(snapshot, indent=2, default=str),
        encoding="utf-8",
    )
    return md_path, json_path


# ---------------------------------------------------------------------------
# Top-level dispatch (called by CLI)
# ---------------------------------------------------------------------------


def run_and_save(
    scenario_ids: list[str],
    branches: list[str],
    n_reps: int,
    scenarios_dir: Path,
    reports_dir: Path,
    model_specialist: str = "claude-haiku-4-5-20251001",
    model_synthesizer: str = "claude-sonnet-4-6",
) -> tuple[Path, Path, str]:
    """End-to-end: run eval, score, render, save. Returns (md_path, json_path, report_text)."""
    traces: list[CallTrace] = []
    results = run_eval(
        scenario_ids=scenario_ids,
        branches=branches,
        n_reps=n_reps,
        scenarios_dir=scenarios_dir,
        model_specialist=model_specialist,
        model_synthesizer=model_synthesizer,
        on_trace=traces.append,
    )
    # Load only the scenarios we ran (and that exist).
    scenarios = [
        load_scenario(scenarios_dir / f"{sid}.json")
        for sid in scenario_ids
        if (scenarios_dir / f"{sid}.json").exists()
    ]
    if not scenarios:
        scenarios = list_scenarios(scenarios_dir)

    full_results = [r for r in results if r.branch == "full"]
    stripped_results = [r for r in results if r.branch == "stripped"]
    branch_metrics: list[BranchMetrics] = []
    lifts: list[ABLiftResult] = []
    if full_results:
        branch_metrics.append(
            aggregate_branch_metrics(scenarios, full_results, "full", n_reps)
        )
    if stripped_results:
        branch_metrics.append(
            aggregate_branch_metrics(scenarios, stripped_results, "stripped", n_reps)
        )
    if len(branch_metrics) == 2:
        lifts = compute_ab_lift(branch_metrics[0], branch_metrics[1])

    totals_text = format_totals(aggregate_traces(traces))
    report_md = render_report(
        branch_metrics=branch_metrics,
        lifts=lifts,
        totals_text=totals_text,
        scenario_results=results,
        scenarios=scenarios,
    )
    snapshot = {
        "scenario_ids": scenario_ids,
        "branches": branches,
        "n_reps": n_reps,
        "model_specialist": model_specialist,
        "model_synthesizer": model_synthesizer,
        "results": [_serialize_result(r) for r in results],
        "traces": _serialize_traces(traces),
        "branch_metrics": [m.model_dump(mode="json") for m in branch_metrics],
        "lifts": [lift.model_dump(mode="json") for lift in lifts],
    }
    md_path, json_path = save_run(report_md, snapshot, reports_dir)
    return md_path, json_path, report_md
