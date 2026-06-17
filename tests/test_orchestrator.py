"""Mock-based orchestrator tests. Zero LLM calls, CI-safe.

Verifies that the orchestrator:
- Runs (scenario × branch × rep) Cartesian product correctly.
- Captures pipeline errors without crashing.
- Aggregates costs via the trace callback chain.
- Produces a report with the headline foregrounding critical-miss rate,
  branch tables, per-tier breakdown, lift table, per-scenario detail,
  cost totals, and methodology footer.
- Writes the report + JSON snapshot to disk on save_run.
- The default eval-small scenario set spans all 5 tiers and includes
  adversarial_001 + critical_miss_test_001 (the highest-value scenarios).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _fake_response(stop_reason: str, content_blocks: list,
                   input_tokens: int = 500, output_tokens: int = 200):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content_blocks,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(tu_id: str, name: str, tool_input: dict):
    return SimpleNamespace(type="tool_use", id=tu_id, name=name, input=tool_input)


def _stripped_canned_esi(esi: int, care_area: str = "resus"):
    payload = {
        "esi_score": esi,
        "care_area": care_area,
        "patient_summary": "Test summary",
        "critical_flags": ["acs"] if esi <= 2 else [],
        "rationale": "Test rationale",
    }
    return _fake_response("end_turn", [_text_block(json.dumps(payload))])


def _full_canned_esi(esi: int, care_area: str = "resus"):
    """4 specialist responses + 2 synth turns producing the given ESI."""
    specialist = _fake_response("end_turn", [_text_block("Test findings.")])
    synth_tool_use = _fake_response(
        "tool_use",
        [_tool_use_block("tu_synth", "generate_triage_report", {
            "patient_summary": "Test", "vitals_findings": "Test",
            "symptom_findings": "Test", "protocol_findings": "Test",
            "bed_recommendation": care_area, "esi_score": esi,
        })],
        input_tokens=1500, output_tokens=400,
    )
    synth_end = _fake_response("end_turn", [_text_block("done")])
    return [specialist] * 4 + [synth_tool_use, synth_end]


# ---------------------------------------------------------------------------
# Default eval-small scenario set
# ---------------------------------------------------------------------------


def test_default_eval_small_spans_all_tiers():
    from eval.orchestrator import DEFAULT_EVAL_SMALL_SCENARIO_IDS  # noqa: PLC0415

    tier_prefixes = {
        "clear_esi_1_2_": False,
        "clear_esi_4_5_": False,
        "ambiguous_": False,
        "critical_miss_test_": False,
        "adversarial_": False,
    }
    for sid in DEFAULT_EVAL_SMALL_SCENARIO_IDS:
        for prefix in tier_prefixes:
            if sid.startswith(prefix):
                tier_prefixes[prefix] = True
                break
    assert all(tier_prefixes.values()), (
        f"DEFAULT_EVAL_SMALL_SCENARIO_IDS does not span all tiers: {tier_prefixes}"
    )


def test_default_eval_small_includes_prompt_injection_and_critical_miss():
    """Both highest-value scenarios must be in the small set."""
    from eval.orchestrator import DEFAULT_EVAL_SMALL_SCENARIO_IDS  # noqa: PLC0415

    assert "adversarial_001" in DEFAULT_EVAL_SMALL_SCENARIO_IDS
    assert "critical_miss_test_001" in DEFAULT_EVAL_SMALL_SCENARIO_IDS


def test_default_eval_small_scenarios_exist_on_disk():
    from eval.orchestrator import DEFAULT_EVAL_SMALL_SCENARIO_IDS  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    for sid in DEFAULT_EVAL_SMALL_SCENARIO_IDS:
        assert (scenarios_dir / f"{sid}.json").exists(), (
            f"DEFAULT_EVAL_SMALL_SCENARIO_IDS includes '{sid}' but "
            f"eval/scenarios/{sid}.json does not exist."
        )


# ---------------------------------------------------------------------------
# run_eval — Cartesian product
# ---------------------------------------------------------------------------


def test_run_eval_runs_cartesian_product():
    """1 scenario × 2 branches × 1 rep = 2 results."""
    from eval.orchestrator import run_eval  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    full_responses = _full_canned_esi(esi=2)
    stripped_response = _stripped_canned_esi(esi=2)
    # 1 rep of full (6 calls) + 1 rep of stripped (1 call) = 7
    queue = full_responses + [stripped_response]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        results = run_eval(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full", "stripped"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
        )

    assert len(results) == 2
    branches = [r.branch for r in results]
    assert "full" in branches
    assert "stripped" in branches


def test_run_eval_handles_pipeline_error_gracefully():
    from eval.orchestrator import run_eval  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("simulated outage")

    with patch("eval.runners._get_anthropic_client", return_value=client):
        results = run_eval(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
        )
    assert len(results) == 1
    assert results[0].error is not None


def test_run_eval_missing_scenario_file():
    from eval.orchestrator import run_eval  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    results = run_eval(
        scenario_ids=["does_not_exist_999"],
        branches=["full"],
        n_reps=1,
        scenarios_dir=scenarios_dir,
    )
    assert len(results) == 1
    assert results[0].error is not None
    assert "not found" in results[0].error.lower()


# ---------------------------------------------------------------------------
# run_and_save — end-to-end
# ---------------------------------------------------------------------------


def test_run_and_save_writes_report_and_snapshot(tmp_path):
    from eval.orchestrator import run_and_save  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    reports_dir = tmp_path / "reports"

    queue = _full_canned_esi(esi=2) + [_stripped_canned_esi(esi=2)]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        md_path, json_path, report_text = run_and_save(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full", "stripped"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
            reports_dir=reports_dir,
        )

    assert md_path.exists()
    assert json_path.exists()
    assert json_path.name == "latest_run.json"

    # Report content sanity checks.
    assert "TriageIQ Eval Report" in report_text
    assert "## Headline" in report_text
    assert "Branch `full`" in report_text or "Branch `stripped`" in report_text
    assert "Per-scenario detail" in report_text
    assert "Cost & latency" in report_text
    assert "Methodology disclosure" in report_text
    assert "critical-miss" in report_text.lower()

    snap = json.loads(json_path.read_text(encoding="utf-8"))
    assert snap["scenario_ids"] == ["clear_esi_1_2_001"]
    assert "results" in snap
    assert "branch_metrics" in snap
    assert "lifts" in snap


def test_run_and_save_headline_full_safer_on_critical_miss(tmp_path):
    """If FULL catches a high-acuity scenario STRIPPED misses,
    the headline should call out FULL's safety advantage."""
    from eval.orchestrator import run_and_save  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    reports_dir = tmp_path / "reports"

    # FULL classifies correctly as ESI 2; STRIPPED misses (ESI 4 = critical miss).
    queue = _full_canned_esi(esi=2) + [_stripped_canned_esi(esi=4)]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        _, _, report_text = run_and_save(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full", "stripped"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
            reports_dir=reports_dir,
        )

    assert "FULL pipeline has a LOWER critical-miss rate" in report_text


def test_run_and_save_headline_stripped_safer_on_critical_miss(tmp_path):
    """Inverse: FULL misses, STRIPPED catches → headline should flag safety regression."""
    from eval.orchestrator import run_and_save  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    reports_dir = tmp_path / "reports"

    # FULL misses (ESI 4 = critical miss on a clear_esi_1_2 patient).
    queue = _full_canned_esi(esi=4) + [_stripped_canned_esi(esi=2)]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        _, _, report_text = run_and_save(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full", "stripped"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
            reports_dir=reports_dir,
        )

    assert "STRIPPED baseline has a LOWER critical-miss rate" in report_text
    assert "safety regression" in report_text


def test_run_and_save_aggregates_cost(tmp_path):
    """Cost totals in the report should be non-zero after real calls."""
    from eval.orchestrator import run_and_save  # noqa: PLC0415

    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    reports_dir = tmp_path / "reports"

    queue = _full_canned_esi(esi=2) + [_stripped_canned_esi(esi=2)]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        _, json_path, report_text = run_and_save(
            scenario_ids=["clear_esi_1_2_001"],
            branches=["full", "stripped"],
            n_reps=1,
            scenarios_dir=scenarios_dir,
            reports_dir=reports_dir,
        )

    snap = json.loads(json_path.read_text(encoding="utf-8"))
    # FULL: 4 specialists + 2 synth turns = 6 traces. STRIPPED: 1 trace. Total: 7.
    assert 6 <= len(snap["traces"]) <= 7
    total_cost = sum(t["cost_usd"] for t in snap["traces"])
    assert total_cost > 0.0
    assert "Total cost" in report_text
