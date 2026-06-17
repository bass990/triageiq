"""Day-1 smoke tests for the TriageIQ eval harness.

No LLM calls. No network. CI-safe. Verifies:
- eval package is importable
- RUBRIC.md is committed before scenarios
- instrumentation cost arithmetic is correct against pricing table
- runners CLI exits non-zero with Day-1 status on --mode dry
- rubric_audit vital-sign severity, red-flag matching, canonical ESI
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "eval"


# ---------------------------------------------------------------------------
# Package wiring
# ---------------------------------------------------------------------------


def test_eval_package_importable():
    from eval import instrumentation, prompts, rubric_audit, schemas  # noqa: F401, PLC0415


def test_rubric_committed_before_scenarios():
    """RUBRIC.md must exist; scenarios directory may be empty Day 1."""
    rubric = EVAL_DIR / "RUBRIC.md"
    scenarios = EVAL_DIR / "scenarios"
    assert rubric.exists(), "RUBRIC.md must be committed Day 1"
    rubric_text = rubric.read_text(encoding="utf-8")
    assert "Status:" in rubric_text
    assert "5-level ESI taxonomy" in rubric_text
    assert scenarios.exists() and scenarios.is_dir()


def test_readme_documents_branches():
    readme = (EVAL_DIR / "README.md").read_text(encoding="utf-8")
    assert "FULL branch" in readme
    assert "STRIPPED branch" in readme
    assert "A/B" in readme


# ---------------------------------------------------------------------------
# Instrumentation cost arithmetic
# ---------------------------------------------------------------------------


def test_cost_for_call_sonnet_4_6():
    from eval.instrumentation import cost_for_call  # noqa: PLC0415

    # 1M input + 1M output at sonnet 4.6 should be exactly $3 + $15 = $18.
    cost = cost_for_call("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(cost - 18.0) < 1e-9


def test_cost_for_call_haiku_4_5():
    from eval.instrumentation import cost_for_call  # noqa: PLC0415

    # 1M input + 1M output at haiku 4.5 should be $0.25 + $1.25 = $1.50.
    cost = cost_for_call("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert abs(cost - 1.50) < 1e-9


def test_cost_for_call_typical_specialist():
    from eval.instrumentation import cost_for_call  # noqa: PLC0415

    # Per scope spec: ~1.5K input + ~600 output per specialist on Haiku ~ $0.001.
    cost = cost_for_call("claude-haiku-4-5-20251001", 1_500, 600)
    assert 0.0001 < cost < 0.002


def test_cost_for_call_typical_synthesizer():
    from eval.instrumentation import cost_for_call  # noqa: PLC0415

    # Per scope spec: ~3K input + ~1K output per synthesizer on Sonnet ~ $0.024.
    cost = cost_for_call("claude-sonnet-4-6", 3_000, 1_000)
    assert 0.015 < cost < 0.030


def test_make_trace_computes_cost():
    from eval.instrumentation import make_trace  # noqa: PLC0415

    trace = make_trace(
        model="claude-sonnet-4-6",
        role="synthesizer",
        input_tokens=3_000,
        output_tokens=1_000,
        duration_seconds=2.5,
        scenario_id="clear_esi_1_2_001",
        branch="full",
        rep=0,
    )
    assert trace.model == "claude-sonnet-4-6"
    assert trace.role == "synthesizer"
    assert trace.scenario_id == "clear_esi_1_2_001"
    assert 0.015 < trace.cost_usd < 0.030


def test_aggregate_traces_per_role_rollup():
    from eval.instrumentation import aggregate_traces, make_trace  # noqa: PLC0415

    traces = [
        make_trace("claude-haiku-4-5-20251001", "vitals", 1_500, 600, 1.0),
        make_trace("claude-haiku-4-5-20251001", "symptoms", 1_500, 600, 1.0),
        make_trace("claude-haiku-4-5-20251001", "protocols", 1_500, 600, 1.0),
        make_trace("claude-haiku-4-5-20251001", "beds", 1_500, 600, 1.0),
        make_trace("claude-sonnet-4-6", "synthesizer", 3_000, 1_000, 2.5),
        make_trace("claude-sonnet-4-6", "stripped", 2_500, 800, 2.0),
    ]
    totals = aggregate_traces(traces)
    assert totals.n_calls == 6
    assert "vitals" in totals.per_role
    assert "synthesizer" in totals.per_role
    assert "stripped" in totals.per_role
    assert totals.per_role["vitals"]["n_calls"] == 1
    assert totals.per_role["synthesizer"]["n_calls"] == 1


def test_format_totals_renders():
    from eval.instrumentation import (  # noqa: PLC0415
        aggregate_traces,
        format_totals,
        make_trace,
    )

    traces = [make_trace("claude-sonnet-4-6", "synthesizer", 3_000, 1_000, 2.5)]
    output = format_totals(aggregate_traces(traces))
    assert "Total calls" in output
    assert "Total cost" in output
    assert "$" in output


# ---------------------------------------------------------------------------
# Rubric audit — vital-sign severity
# ---------------------------------------------------------------------------


def test_vital_sign_severity_critical_sbp():
    from eval.rubric_audit import vital_sign_severity  # noqa: PLC0415
    from eval.schemas import Vitals  # noqa: PLC0415

    # SBP 75 -> critical
    assert vital_sign_severity(Vitals(bp="75/50", hr=80, spo2=98)) == "critical"


def test_vital_sign_severity_critical_spo2():
    from eval.rubric_audit import vital_sign_severity  # noqa: PLC0415
    from eval.schemas import Vitals  # noqa: PLC0415

    # SpO2 88 -> critical
    assert vital_sign_severity(Vitals(spo2=88)) == "critical"


def test_vital_sign_severity_concerning_hr():
    from eval.rubric_audit import vital_sign_severity  # noqa: PLC0415
    from eval.schemas import Vitals  # noqa: PLC0415

    # HR 110 alone -> concerning, not critical
    assert vital_sign_severity(Vitals(hr=110, spo2=98)) == "concerning"


def test_vital_sign_severity_normal():
    from eval.rubric_audit import vital_sign_severity  # noqa: PLC0415
    from eval.schemas import Vitals  # noqa: PLC0415

    assert vital_sign_severity(Vitals(bp="120/80", hr=72, rr=16, spo2=98, temp=36.8, gcs=15)) == "normal"


def test_vital_sign_severity_missing_returns_normal():
    from eval.rubric_audit import vital_sign_severity  # noqa: PLC0415
    from eval.schemas import Vitals  # noqa: PLC0415

    # No vitals at all -> normal (the rubric demands real data; missing is not critical by itself)
    assert vital_sign_severity(Vitals()) == "normal"


# ---------------------------------------------------------------------------
# Rubric audit — red flags
# ---------------------------------------------------------------------------


def test_red_flag_keywords_acs():
    from eval.rubric_audit import red_flag_keywords, red_flag_min_esi  # noqa: PLC0415

    text = "Chest pain radiating to left arm with diaphoresis for 30 minutes."
    assert "acs" in red_flag_keywords(text)
    assert red_flag_min_esi(text) == 2


def test_red_flag_keywords_stroke():
    from eval.rubric_audit import red_flag_keywords, red_flag_min_esi  # noqa: PLC0415

    text = "Sudden onset facial droop and arm weakness 30 minutes ago."
    assert "stroke" in red_flag_keywords(text)
    assert red_flag_min_esi(text) == 2


def test_red_flag_keywords_arrest():
    from eval.rubric_audit import red_flag_keywords, red_flag_min_esi  # noqa: PLC0415

    text = "Witnessed cardiac arrest, CPR in progress on arrival."
    assert "arrest" in red_flag_keywords(text)
    assert red_flag_min_esi(text) == 1


def test_red_flag_keywords_no_match():
    from eval.rubric_audit import red_flag_keywords, red_flag_min_esi  # noqa: PLC0415

    text = "Stable patient here for medication refill."
    assert red_flag_keywords(text) == []
    assert red_flag_min_esi(text) is None


# ---------------------------------------------------------------------------
# Rubric audit — canonical ESI derivation
# ---------------------------------------------------------------------------


def test_rubric_canonical_esi_critical_vitals_and_acs():
    from eval.rubric_audit import rubric_canonical_esi  # noqa: PLC0415
    from eval.schemas import Patient, Vitals  # noqa: PLC0415

    # PT-001 (the production sample patient) shape: critical vitals + ACS picture.
    p = Patient(
        name="John M., 65M",
        chief_complaint="Chest pain radiating to left arm with diaphoresis for 45 minutes.",
        vitals=Vitals(bp="88/60", hr=112, rr=22, spo2=94, temp=37.1, gcs=15),
        history="HTN, type 2 diabetes, smoker",
    )
    canonical, acceptable = rubric_canonical_esi(p)
    assert canonical in (1, 2)
    assert canonical in acceptable


def test_rubric_canonical_esi_normal_vitals_no_red_flag():
    from eval.rubric_audit import rubric_canonical_esi  # noqa: PLC0415
    from eval.schemas import Patient, Vitals  # noqa: PLC0415

    p = Patient(
        name="Stable patient",
        chief_complaint="Need a medication refill, no acute complaints.",
        vitals=Vitals(bp="120/80", hr=72, rr=16, spo2=99, temp=36.8, gcs=15),
        history="HTN well controlled",
    )
    canonical, acceptable = rubric_canonical_esi(p)
    assert canonical >= 3
    assert canonical in acceptable


def test_rubric_canonical_esi_concerning_vitals_no_red_flag():
    from eval.rubric_audit import rubric_canonical_esi  # noqa: PLC0415
    from eval.schemas import Patient, Vitals  # noqa: PLC0415

    # Tachycardic but otherwise stable, no red-flag complaint.
    p = Patient(
        name="Patient",
        chief_complaint="Mild palpitations after exercise.",
        vitals=Vitals(bp="130/85", hr=110, rr=18, spo2=98, temp=37.0, gcs=15),
        history="None",
    )
    canonical, acceptable = rubric_canonical_esi(p)
    assert canonical in (2, 3)
    assert 3 in acceptable


# ---------------------------------------------------------------------------
# Pydantic schema — basic validation
# ---------------------------------------------------------------------------


def test_scenario_validation_minimal():
    from eval.schemas import Scenario  # noqa: PLC0415

    s = Scenario(
        id="clear_esi_1_2_001",
        tier="clear_esi_1_2",
        description="STEMI presentation in a 65-year-old male.",
        patient={
            "name": "John M., 65M",
            "chief_complaint": "Chest pain with radiation and diaphoresis.",
            "vitals": {"bp": "88/60", "hr": 112, "spo2": 94},
            "history": "HTN, T2DM",
        },
        expected_esi=2,
    )
    assert s.id == "clear_esi_1_2_001"
    # acceptable_esi defaults to [expected_esi]
    assert 2 in s.acceptable_esi
    # expected_care_areas defaults from the canonical map
    assert "resus" in s.expected_care_areas or "trauma_bay" in s.expected_care_areas


def test_scenario_rejects_bad_id_format():
    import pytest  # noqa: PLC0415
    from pydantic import ValidationError  # noqa: PLC0415

    from eval.schemas import Scenario  # noqa: PLC0415

    with pytest.raises(ValidationError):
        Scenario(
            id="BadID",
            tier="clear_esi_1_2",
            description="A test scenario.",
            patient={"name": "X", "chief_complaint": "X"},
            expected_esi=2,
        )


def test_scenario_critical_miss_test_requires_high_acuity():
    import pytest  # noqa: PLC0415
    from pydantic import ValidationError  # noqa: PLC0415

    from eval.schemas import Scenario  # noqa: PLC0415

    # is_critical_miss_test must have expected_esi <= 2.
    with pytest.raises(ValidationError):
        Scenario(
            id="critical_miss_test_001",
            tier="critical_miss_test",
            description="An invalid critical-miss-test scenario with low acuity.",
            patient={"name": "X", "chief_complaint": "X"},
            expected_esi=4,
            is_critical_miss_test=True,
        )


# ---------------------------------------------------------------------------
# Prompt mirror — drift detection (light Day-1 version)
# ---------------------------------------------------------------------------


def test_eval_synthesizer_prompt_contains_production_core():
    from eval.prompts import (  # noqa: PLC0415
        SYNTHESIZER_PROMPT,
        SYNTHESIZER_PROMPT_EVAL,
    )

    # SYNTHESIZER_PROMPT_EVAL = SYNTHESIZER_PROMPT + suffix.
    assert SYNTHESIZER_PROMPT in SYNTHESIZER_PROMPT_EVAL
    assert "EVAL MODE" in SYNTHESIZER_PROMPT_EVAL


def test_stripped_prompt_demands_json_only():
    from eval.prompts import SYSTEM_PROMPT_STRIPPED  # noqa: PLC0415

    assert "JSON only" in SYSTEM_PROMPT_STRIPPED
    assert "No specialists" in SYSTEM_PROMPT_STRIPPED
    # Must include the ESI taxonomy so the model has the scoring rules inline.
    assert "ESI 1" in SYSTEM_PROMPT_STRIPPED
    assert "ESI 5" in SYSTEM_PROMPT_STRIPPED


def test_render_stripped_user_message_wraps_xml():
    from eval.prompts import render_stripped_user_message  # noqa: PLC0415

    rendered = render_stripped_user_message(
        {
            "name": "John M., 65M",
            "chief_complaint": "Chest pain",
            "vitals": {"bp": "88/60", "hr": 112, "spo2": 94},
            "history": "HTN",
            "allergies": "PCN",
            "arrival": "walk-in",
        }
    )
    assert "<patient>" in rendered
    assert "<chief_complaint>" in rendered
    assert "<vitals>" in rendered
    assert "Chest pain" in rendered


# ---------------------------------------------------------------------------
# Runners CLI — Day-1 status message
# ---------------------------------------------------------------------------


def test_runners_cli_dry_mode_exits_nonzero():
    result = subprocess.run(
        [sys.executable, "-m", "eval.runners", "--mode", "dry"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Day 1: --mode dry prints status and exits non-zero (intentional).
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert any(
        marker in combined
        for marker in ("Day 1", "Day-1", "eval harness", "scaffold")
    ), combined
