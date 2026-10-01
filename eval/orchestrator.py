"""Orchestration + report rendering for TriageIQ eval runs.

run_eval() runs scenarios × branches × reps, captures pipeline errors as
ScenarioResult.error, reuses successful prior runs when resuming, checkpoints
after every run, and stops on a fatal API error (billing, auth) so the run
can be resumed instead of filling with holes.

render_report() writes the markdown: headline (critical-miss first, for every
candidate branch vs the stripped baseline), completeness, per-branch metrics
including cost/latency, per-tier breakdown, lift tables, per-scenario detail,
totals, methodology.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from eval.instrumentation import CallTrace, aggregate_traces, format_totals
from eval.runners import RUNNERS, list_scenarios, load_scenario
from eval.schemas import ABLiftResult, BranchMetrics, Scenario, ScenarioResult
from eval.scorers import aggregate_branch_metrics, compute_ab_lift

# 5 scenarios spanning all 5 tiers, including the prompt-injection and the
# silent-MI scenarios (highest value per call).
DEFAULT_EVAL_SMALL_SCENARIO_IDS: list[str] = [
    "clear_esi_1_2_001",         # STEMI
    "clear_esi_4_5_001",         # Medication refill
    "ambiguous_001",             # Chest pain in 40s smoker
    "critical_miss_test_001",    # Silent MI in elderly diabetic — safety-critical
    "adversarial_001",           # Prompt injection — security-critical
]

FATAL_ERROR_MARKERS = ("credit balance", "authentication_error", "authentication method", "invalid x-api-key", "permission_error")


def is_fatal_error(message: str | None) -> bool:
    """Errors that repeat on every call; continuing only manufactures holes."""
    m = (message or "").lower()
    return any(k in m for k in FATAL_ERROR_MARKERS)


# ---------------------------------------------------------------------------
# run_eval
# ---------------------------------------------------------------------------


def _branch_runner(branch: str):
    try:
        return RUNNERS[branch]
    except KeyError:
        raise ValueError(f"Unknown branch: {branch}") from None


def _run_one(branch, runner, scenario, rep, model_specialist, model_synthesizer, on_trace) -> ScenarioResult:
    if branch == "stripped":
        return runner(scenario=scenario, rep=rep, model=model_synthesizer, on_trace=on_trace)
    return runner(scenario=scenario, rep=rep, model_specialist=model_specialist, model_synthesizer=model_synthesizer, on_trace=on_trace)


def run_eval(
    scenario_ids: list[str],
    branches: list[str],
    n_reps: int,
    scenarios_dir: Path,
    model_specialist: str = "claude-haiku-4-5-20251001",
    model_synthesizer: str = "claude-sonnet-5",
    on_trace=None,
    prior_results: dict[tuple[str, str, int], ScenarioResult] | None = None,
    state: dict | None = None,
    on_result=None,
) -> list[ScenarioResult]:
    """Run scenarios × branches × reps. Errors captured per-result."""
    results: list[ScenarioResult] = []
    prior = prior_results or {}
    state = state if state is not None else {}
    state.setdefault("aborted", None)
    state.setdefault("reused", 0)
    for scenario_id in scenario_ids:
        path = scenarios_dir / f"{scenario_id}.json"
        if not path.exists():
            results.append(ScenarioResult(scenario_id=scenario_id, tier="clear_esi_1_2", branch="full", rep=0,
                                          error=f"Scenario file not found: {path}"))
            continue
        scenario = load_scenario(path)
        for branch in branches:
            runner = _branch_runner(branch)
            for rep in range(n_reps):
                if state["aborted"]:
                    return results
                prev = prior.get((scenario_id, branch, rep))
                if prev is not None and not prev.error:
                    results.append(prev)
                    state["reused"] += 1
                    continue
                try:
                    result = _run_one(branch, runner, scenario, rep, model_specialist, model_synthesizer, on_trace)
                except Exception as exc:  # the runners already catch; belt and braces
                    result = ScenarioResult(scenario_id=scenario_id, tier=scenario.tier, branch=branch, rep=rep,
                                            error=f"{type(exc).__name__}: {exc}")
                results.append(result)
                if on_result is not None:
                    on_result(result)
                if is_fatal_error(result.error):
                    state["aborted"] = result.error
                    sys.stderr.write(f"\nABORTING after {len(results)} runs — fatal API error: {result.error}\n"
                                     "Fix the account, then re-run with --resume.\n")
    return results


def load_prior_results(snapshot: dict) -> tuple[dict[tuple[str, str, int], ScenarioResult], list[CallTrace]]:
    prior: dict[tuple[str, str, int], ScenarioResult] = {}
    for raw in snapshot.get("results", []):
        r = ScenarioResult(**raw)
        if not r.error:
            prior[(r.scenario_id, r.branch, r.rep)] = r
    traces = [CallTrace(**t) for t in snapshot.get("traces", [])
              if (t.get("scenario_id"), t.get("branch"), t.get("rep")) in prior]
    return prior, traces


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_num(x: float) -> str:
    return f"{x:.3f}"


def _headline(lifts: list[ABLiftResult]) -> str:
    """Critical-miss rate first (the safety metric), then strict accuracy."""
    crit = next((lift for lift in lifts if lift.metric == "critical_miss_rate"), None)
    strict = next((lift for lift in lifts if lift.metric == "esi_strict_acc"), None)
    if crit is None or strict is None:
        return "_(Insufficient metrics to summarize.)_"
    cand = crit.candidate.upper()
    base = crit.baseline.upper()
    full_strict, strip_strict = _fmt_pct(strict.full_score), _fmt_pct(strict.stripped_score)
    strict_pp = strict.lift * 100
    full_cm, strip_cm = _fmt_pct(crit.full_score), _fmt_pct(crit.stripped_score)
    cm_pp = crit.lift * 100

    if crit.interpretation == "full_wins":
        cm_verdict = (f"**{cand} has a LOWER critical-miss rate than {base}** ({full_cm} vs {strip_cm}, {-cm_pp:+.1f}pp safer). "
                      f"The specialist architecture earns its complexity on the patient-safety metric.")
    elif crit.interpretation == "stripped_wins":
        cm_verdict = (f"**{base} has a LOWER critical-miss rate than {cand}** ({strip_cm} vs {full_cm}, {cm_pp:+.1f}pp WORSE on {cand} — "
                      f"a safety regression). {cand} mis-classifies high-acuity patients more often than the single-prompt baseline; "
                      f"clinical use of {cand} over {base} would harm patients.")
    else:
        cm_verdict = (f"**{cand} ≈ {base} on critical-miss rate** ({full_cm} vs {strip_cm}; {cm_pp:+.1f}pp). "
                      f"The architectures are equivalent on the load-bearing safety metric.")

    if strict.interpretation == "full_wins":
        strict_verdict = f"On ESI strict accuracy, {cand} beats {base} by {strict_pp:+.1f}pp ({cand}: {full_strict} vs {base}: {strip_strict})."
    elif strict.interpretation == "stripped_wins":
        strict_verdict = f"On ESI strict accuracy, {base} beats {cand} by {-strict_pp:+.1f}pp ({cand}: {full_strict} vs {base}: {strip_strict})."
    else:
        strict_verdict = f"On ESI strict accuracy, {cand} ≈ {base} ({strict_pp:+.1f}pp; {cand}: {full_strict} vs {base}: {strip_strict})."
    return f"{cm_verdict}\n\n{strict_verdict}"


def _render_completeness(results: list[ScenarioResult], branch_metrics: list[BranchMetrics], planned: int | None, aborted: str | None) -> str:
    n_ok = sum(1 for r in results if not r.error)
    n_err = sum(1 for r in results if r.error)
    n_missing = max(0, (planned or 0) - len(results)) if planned else 0
    lines = ["## Completeness", ""]
    if n_err == 0 and n_missing == 0 and not aborted:
        lines.append(f"All {n_ok} planned runs completed. Every metric below uses every run.")
        return "\n".join(lines)
    lines += ["**⚠ PARTIAL RUN — treat every number below as provisional.**", "",
              f"- runs scored: {n_ok}", f"- runs errored (excluded from all metrics): {n_err}"]
    if n_missing:
        lines.append(f"- runs never executed (aborted early): {n_missing}")
    if aborted:
        lines.append(f"- aborted on fatal API error: `{aborted[:160]}`")
        lines.append("- resume with `python -m eval.runners --mode full --resume --yes` once the account is fixed")
    lines += ["", "| Branch | scored | errored | scenarios dropped (all reps errored) |", "|---|---|---|---|"]
    for m in branch_metrics:
        ok = sum(1 for r in results if r.branch == m.branch and not r.error)
        dropped = ", ".join(f"`{s}`" for s in m.scenarios_dropped) or "—"
        lines.append(f"| `{m.branch}` | {ok} | {m.n_errored_runs} | {dropped} |")
    return "\n".join(lines)


def _render_branch_table(metrics: BranchMetrics) -> str:
    return (
        f"### Branch `{metrics.branch}`\n"
        f"_n_scenarios={metrics.n_scenarios}, n_reps={metrics.n_reps}_\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| ESI strict accuracy | {_fmt_pct(metrics.esi_strict_acc)} |\n"
        f"| ESI ±1 lenient accuracy | {_fmt_pct(metrics.esi_lenient_acc)} |\n"
        f"| Critical-miss rate (lower=better) | {_fmt_pct(metrics.critical_miss_rate)} |\n"
        f"| Overtriage rate on ESI 4-5 (lower=better) | {_fmt_pct(metrics.overtriage_rate)} |\n"
        f"| Under-triage, any tier (lower=better) | {_fmt_pct(metrics.undertriage_any_rate)} |\n"
        f"| Over-triage, any tier (lower=better) | {_fmt_pct(metrics.overtriage_any_rate)} |\n"
        f"| Care-area accuracy | {_fmt_pct(metrics.care_area_acc)} |\n"
        f"| Critical-flag coverage | {_fmt_pct(metrics.critical_flag_coverage_mean)} |\n"
        f"| Structured report valid first try | {_fmt_pct(metrics.schema_valid_rate)} |\n"
        f"| Mean LLM calls / cost / latency per run | {metrics.mean_llm_calls:.1f} / ${metrics.mean_cost_usd:.4f} / {metrics.mean_latency_s:.1f}s |\n"
    )


def _render_per_tier_breakdown(branch_metrics: list[BranchMetrics]) -> str:
    tiers = sorted({t for m in branch_metrics for t in m.per_tier})
    names = [m.branch for m in branch_metrics]
    lines = ["### Per-tier ESI strict accuracy", "",
             "| Tier | " + " | ".join(f"`{n}`" for n in names) + " |", "|---|" + "---|" * len(names)]
    for tier in tiers:
        lines.append(f"| `{tier}` | " + " | ".join(_fmt_pct(m.per_tier.get(tier, {}).get("esi_strict", 0.0)) if tier in m.per_tier else "—" for m in branch_metrics) + " |")
    lines += ["", "### Per-tier critical-miss rate (high-acuity tiers only)", "",
              "| Tier | " + " | ".join(f"`{n}`" for n in names) + " |", "|---|" + "---|" * len(names)]
    for tier in tiers:
        if tier not in ("clear_esi_1_2", "critical_miss_test", "adversarial"):
            continue
        lines.append(f"| `{tier}` | " + " | ".join(_fmt_pct(m.per_tier.get(tier, {}).get("is_critical_miss", 0.0)) if tier in m.per_tier else "—" for m in branch_metrics) + " |")
    return "\n".join(lines)


def _render_lift_table(lifts: list[ABLiftResult]) -> str:
    cand, base = (lifts[0].candidate, lifts[0].baseline) if lifts else ("full", "stripped")
    lines = [f"### A/B lift (`{cand}` − `{base}`)", "",
             f"| Metric | `{cand}` | `{base}` | Lift | Interpretation |", "|---|---|---|---|---|"]
    for lift in lifts:
        lower = lift.metric in ("critical_miss_rate", "overtriage_rate", "undertriage_any_rate", "overtriage_any_rate")
        lines.append(f"| `{lift.metric}`{' (lower=better)' if lower else ''} | {_fmt_pct(lift.full_score)} | {_fmt_pct(lift.stripped_score)} | "
                     f"{lift.lift * 100:+.1f}pp | `{lift.interpretation}` |")
    return "\n".join(lines)


def _render_variance(scenarios: list[Scenario], results: list[ScenarioResult]) -> str:
    by = {s.id: s for s in scenarios}
    rows = []
    for branch in sorted({r.branch for r in results}):
        ranges = []
        for sid in sorted({r.scenario_id for r in results if r.branch == branch}):
            sc = by.get(sid)
            vals = [1.0 if r.output.esi_score in set(sc.acceptable_esi or [sc.expected_esi]) else 0.0
                    for r in results if r.branch == branch and r.scenario_id == sid and r.output is not None and sc is not None]
            if len(vals) > 1:
                ranges.append(max(vals) - min(vals))
        if ranges:
            rows.append(f"| `{branch}` | {sum(ranges) / len(ranges):.3f} | {sum(1 for x in ranges if x > 0)} / {len(ranges)} |")
    if not rows:
        return ""
    return "\n".join(["### Run-to-run variance (strict ESI, across reps)", "",
                      "| Branch | Mean range | Unstable scenarios |", "|---|---|---|", *rows, "",
                      "_A lift smaller than the mean range is within sampling noise._"])


def _render_per_scenario_detail(scenarios: list[Scenario], results: list[ScenarioResult]) -> str:
    by_id = {s.id: s for s in scenarios}
    grouped: dict[tuple[str, str], list[ScenarioResult]] = {}
    for r in results:
        grouped.setdefault((r.scenario_id, r.branch), []).append(r)
    lines = ["### Per-scenario detail", "",
             "| Scenario | Tier | Branch | Reps | Predicted ESI | Expected ESI | Critical Misses | Errors |",
             "|---|---|---|---|---|---|---|---|"]
    for (sid, branch), rs in sorted(grouped.items()):
        scenario = by_id.get(sid)
        tier = scenario.tier if scenario else "?"
        expected = scenario.expected_esi if scenario else "?"
        pred = [str(r.output.esi_score) if r.output else "?" for r in rs]
        n_errors = sum(1 for r in rs if r.error)
        n_misses = sum(1 for r in rs if r.output and r.output.esi_score >= 3) if scenario and scenario.expected_esi in (1, 2) else 0
        lines.append(f"| `{sid}` | {tier} | `{branch}` | {len(rs)} | {','.join(pred)} | {expected} | "
                     f"{f'**{n_misses}**' if n_misses else '0'} | {n_errors} |")
    return "\n".join(lines)


def _render_methodology_footer() -> str:
    return (
        "### Methodology disclosure\n\n"
        "- **Tool calls are mocked.** `get_patient_data`, `search_protocols` and `check_bed_availability` return canned data "
        "tied to the scenario. This eval tests reasoning, not EHR/RAG/bed-state robustness.\n"
        "- **Prompts and the report tool are imported from production** (`backend/agents.py`, `backend/schemas.py`), not mirrored.\n"
        "- **Branches.** `full` = 4 Haiku specialists + Sonnet synthesizer; `lean` = Symptom specialist + synthesizer (production default); "
        "`stripped` = one Sonnet call. Every branch sees the same tokenised, tagged patient record.\n"
        "- **Scorers are deterministic.** No LLM-as-judge. ESI accuracy is set membership in `acceptable_esi`; critical-miss is "
        "gold <= 2 AND predicted >= 3; under/over-triage (any tier) compare against the acceptable set; care-area is set membership.\n"
        "- **Errored runs are excluded**, never scored as worst case; the Completeness section lists them.\n"
        "- **Per-tier macro-average.** Overall scores weight each tier equally.\n"
        "- **Critical-miss rate is the load-bearing safety metric.** An architecture that scores +5pp on strict accuracy but "
        "increases critical-miss rate is worse for clinical use.\n"
        "- **Not clinically validated.** Scored against the published rubric, not ED outcomes. Use for architectural decisions only."
    )


def render_report(
    branch_metrics: list[BranchMetrics],
    lifts: list[ABLiftResult],
    totals_text: str,
    scenario_results: list[ScenarioResult],
    scenarios: list[Scenario] | None = None,
    lifts_by_branch: dict[str, list[ABLiftResult]] | None = None,
    models: dict | None = None,
    planned_runs: int | None = None,
    aborted: str | None = None,
) -> str:
    partial = bool(aborted) or any(r.error for r in scenario_results)
    lifts_by_branch = lifts_by_branch or ({"full": lifts} if lifts else {})
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"# TriageIQ Eval Report — {timestamp}", ""]
    if models:
        parts += [f"_specialists: `{models.get('specialist')}` · synthesizer / stripped: `{models.get('synthesizer')}`_", ""]
    parts += ["## Headline", ""]
    if partial:
        parts += ["**⚠ PARTIAL RUN** (see Completeness).", ""]
    for cand, ls in lifts_by_branch.items():
        parts += [f"**`{cand}` vs `stripped`.** " + _headline(ls), ""]
    if not lifts_by_branch:
        parts += ["_(No baseline branch ran; no A/B to summarize.)_", ""]
    parts += [_render_completeness(scenario_results, branch_metrics, planned_runs, aborted), "", "## Branch metrics", ""]
    for m in branch_metrics:
        parts.append(_render_branch_table(m))
    if len(branch_metrics) >= 2:
        parts += ["", _render_per_tier_breakdown(branch_metrics)]
    for _cand, ls in lifts_by_branch.items():
        parts += ["", _render_lift_table(ls)]
    if scenarios:
        var = _render_variance(scenarios, scenario_results)
        if var:
            parts += ["", var]
        parts += ["", _render_per_scenario_detail(scenarios, scenario_results)]
    parts += ["", "## Cost & latency", "", totals_text, "", _render_methodology_footer()]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# save_run
# ---------------------------------------------------------------------------


def _serialize_result(r: ScenarioResult) -> dict:
    return r.model_dump(mode="json")


def _serialize_traces(traces: list[CallTrace]) -> list[dict]:
    return [asdict(t) for t in traces]


def save_run(report_md: str, snapshot: dict, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = reports_dir / f"run_{timestamp}.md"
    json_path = reports_dir / "latest_run.json"
    md_path.write_text(report_md, encoding="utf-8")
    json_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def _with_cost(m: BranchMetrics, results: list[ScenarioResult], traces: list[CallTrace]) -> BranchMetrics:
    ok = [(r.scenario_id, r.rep) for r in results if r.branch == m.branch and not r.error]
    if not ok:
        return m
    keyset = set(ok)
    ts = [t for t in traces if t.branch == m.branch and (t.scenario_id, t.rep) in keyset]
    m.mean_llm_calls = len(ts) / len(ok)
    m.mean_cost_usd = sum(t.cost_usd for t in ts) / len(ok)
    m.mean_latency_s = sum(t.duration_seconds for t in ts) / len(ok)
    return m


def _score(results, traces, scenarios, branches, n_reps):
    branch_metrics: list[BranchMetrics] = []
    for branch in branches:
        br = [r for r in results if r.branch == branch]
        if br:
            branch_metrics.append(_with_cost(aggregate_branch_metrics(scenarios, br, branch, n_reps), results, traces))
    stripped_m = next((m for m in branch_metrics if m.branch == "stripped"), None)
    lifts_by_branch: dict[str, list[ABLiftResult]] = {}
    if stripped_m is not None:
        for m in branch_metrics:
            if m.branch != "stripped":
                lifts_by_branch[m.branch] = compute_ab_lift(m, stripped_m)
    lifts = lifts_by_branch.get("full") or (next(iter(lifts_by_branch.values())) if lifts_by_branch else [])
    return branch_metrics, lifts, lifts_by_branch


def run_and_save(
    scenario_ids: list[str],
    branches: list[str],
    n_reps: int,
    scenarios_dir: Path,
    reports_dir: Path,
    model_specialist: str = "claude-haiku-4-5-20251001",
    model_synthesizer: str = "claude-sonnet-5",
    resume_snapshot: dict | None = None,
) -> tuple[Path, Path, str]:
    """End-to-end: run eval, score, render, save. Returns (md_path, json_path, report_text)."""
    traces: list[CallTrace] = []
    prior: dict = {}
    if resume_snapshot:
        prior, prior_traces = load_prior_results(resume_snapshot)
        traces.extend(prior_traces)
        sys.stderr.write(f"Resuming: {len(prior)} successful runs reused.\n")
    state: dict = {}
    reports_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = reports_dir / "checkpoint.jsonl"
    checkpoint.write_text("", encoding="utf-8")

    def _checkpoint(r: ScenarioResult) -> None:
        with checkpoint.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_serialize_result(r), default=str) + "\n")

    results = run_eval(scenario_ids=scenario_ids, branches=branches, n_reps=n_reps, scenarios_dir=scenarios_dir,
                       model_specialist=model_specialist, model_synthesizer=model_synthesizer, on_trace=traces.append,
                       prior_results=prior, state=state, on_result=_checkpoint)
    scenarios = [load_scenario(scenarios_dir / f"{sid}.json") for sid in scenario_ids if (scenarios_dir / f"{sid}.json").exists()]
    if not scenarios:
        scenarios = list_scenarios(scenarios_dir)
    planned = len(scenario_ids) * len(branches) * n_reps
    branch_metrics, lifts, lifts_by_branch = _score(results, traces, scenarios, branches, n_reps)
    models = {"specialist": model_specialist, "synthesizer": model_synthesizer}
    report_md = render_report(branch_metrics=branch_metrics, lifts=lifts, totals_text=format_totals(aggregate_traces(traces)),
                              scenario_results=results, scenarios=scenarios, lifts_by_branch=lifts_by_branch, models=models,
                              planned_runs=planned, aborted=state.get("aborted"))
    snapshot = {
        "scenario_ids": scenario_ids, "branches": branches, "n_reps": n_reps,
        "model_specialist": model_specialist, "model_synthesizer": model_synthesizer,
        "planned_runs": planned, "completed_runs": len(results), "errored_runs": sum(1 for r in results if r.error),
        "reused_runs": state.get("reused", 0), "aborted": state.get("aborted"),
        "results": [_serialize_result(r) for r in results], "traces": _serialize_traces(traces),
        "branch_metrics": [m.model_dump(mode="json") for m in branch_metrics],
        "lifts": [lift.model_dump(mode="json") for lift in lifts],
        "lifts_by_branch": {b: [x.model_dump(mode="json") for x in ls] for b, ls in lifts_by_branch.items()},
    }
    md_path, json_path = save_run(report_md, snapshot, reports_dir)
    return md_path, json_path, report_md


def rerender_snapshot(reports_dir: Path, scenarios_dir: Path) -> Path:
    """Re-score and re-render reports/latest_run.json without model calls."""
    snap = json.loads((reports_dir / "latest_run.json").read_text(encoding="utf-8"))
    results = [ScenarioResult(**r) for r in snap["results"]]
    traces = [CallTrace(**t) for t in snap.get("traces", [])]
    scenario_ids, branches, n_reps = snap["scenario_ids"], snap["branches"], int(snap["n_reps"])
    scenarios = [load_scenario(scenarios_dir / f"{sid}.json") for sid in scenario_ids if (scenarios_dir / f"{sid}.json").exists()]
    branch_metrics, lifts, lifts_by_branch = _score(results, traces, scenarios, branches, n_reps)
    planned = snap.get("planned_runs") or len(scenario_ids) * len(branches) * n_reps
    models = {"specialist": snap.get("model_specialist"), "synthesizer": snap.get("model_synthesizer")}
    md = render_report(branch_metrics=branch_metrics, lifts=lifts, totals_text=format_totals(aggregate_traces(traces)),
                       scenario_results=results, scenarios=scenarios, lifts_by_branch=lifts_by_branch, models=models,
                       planned_runs=planned, aborted=snap.get("aborted"))
    snap["branch_metrics"] = [m.model_dump(mode="json") for m in branch_metrics]
    snap["lifts"] = [x.model_dump(mode="json") for x in lifts]
    snap["lifts_by_branch"] = {b: [x.model_dump(mode="json") for x in ls] for b, ls in lifts_by_branch.items()}
    snap["errored_runs"] = sum(1 for r in results if r.error)
    md_path, _ = save_run(md, snap, reports_dir)
    return md_path
