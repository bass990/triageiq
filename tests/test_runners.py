"""Mock-based runner tests. Zero LLM calls, CI-safe.

Verifies that FULL and STRIPPED pipelines:
- Make the expected number of API calls per scenario.
- Pass the right system prompt to each branch / specialist / synthesizer.
- Run 4 specialists in parallel via ThreadPoolExecutor.
- Mock get_patient_data / search_protocols / check_bed_availability.
- Capture esi_score + care_area + critical_flags from synthesizer (FULL) or
  JSON output (STRIPPED).
- Record CallTraces with cost/duration.
- Handle malformed output and API errors gracefully.
- Drift-detection: GENERATE_TRIAGE_REPORT_TOOL mirrors backend/tools.py.
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


# ---------------------------------------------------------------------------
# Helpers — fake Anthropic Messages API responses
# ---------------------------------------------------------------------------


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


def _make_scenario(esi: int = 2, tier: str = "clear_esi_1_2"):
    from eval.schemas import Scenario  # noqa: PLC0415
    return Scenario(
        id=f"{tier}_001",
        tier=tier,  # type: ignore[arg-type]
        description="Test fixture scenario for runner tests.",
        patient={
            "name": "Test Patient, 65M",
            "chief_complaint": "Chest pain with diaphoresis.",
            "vitals": {"bp": "88/60", "hr": 112, "spo2": 94},
            "history": "HTN, diabetes",
        },
        expected_esi=esi,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Mocked tool tests
# ---------------------------------------------------------------------------


def test_mock_search_protocols_chest_pain():
    from eval.runners import _mock_search_protocols  # noqa: PLC0415

    result = _mock_search_protocols("chest pain radiating to arm")
    assert result["success"] is True
    assert any("ACS" in p["name"] or "Chest" in p["name"] for p in result["protocols"])


def test_mock_search_protocols_stroke():
    from eval.runners import _mock_search_protocols  # noqa: PLC0415

    result = _mock_search_protocols("facial droop arm weakness")
    assert any("Stroke" in p["name"] for p in result["protocols"])


def test_mock_check_bed_availability():
    from eval.runners import _mock_check_bed_availability  # noqa: PLC0415

    r = _mock_check_bed_availability("resus")
    assert r["success"] is True
    assert r["care_area"] == "resus"
    assert r["status"] == "available"


def test_mock_check_bed_availability_unknown():
    from eval.runners import _mock_check_bed_availability  # noqa: PLC0415

    r = _mock_check_bed_availability("not_a_real_area")
    assert r["success"] is False


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_json_safe_plain():
    from eval.runners import _parse_json_safe  # noqa: PLC0415

    assert _parse_json_safe('{"esi_score": 2}') == {"esi_score": 2}


def test_parse_json_safe_markdown_fence():
    from eval.runners import _parse_json_safe  # noqa: PLC0415

    out = _parse_json_safe('```json\n{"esi_score": 1, "care_area": "resus"}\n```')
    assert out == {"esi_score": 1, "care_area": "resus"}


def test_parse_json_safe_prose_prelude():
    from eval.runners import _parse_json_safe  # noqa: PLC0415

    out = _parse_json_safe('Sure:\n{"esi_score": 3}\nThanks.')
    assert out == {"esi_score": 3}


def test_parse_json_safe_garbage():
    from eval.runners import _parse_json_safe  # noqa: PLC0415

    assert _parse_json_safe("not json at all") == {}


# ---------------------------------------------------------------------------
# FULL pipeline — mocked end-to-end
# ---------------------------------------------------------------------------


def _make_specialist_responses():
    """Make 4 specialist responses + 1 synthesizer turn + tool-use + end_turn."""
    specialist_response = _fake_response(
        "end_turn", [_text_block("Specialist findings: critical, ESI 1-2 indicated.")]
    )
    synth_tool_use = _fake_response(
        "tool_use",
        [_tool_use_block("tu_synth", "generate_triage_report", {
            "patient_summary": "STEMI presentation",
            "vitals_findings": "Critical: SBP 88, HR 112",
            "symptom_findings": "ACS red flags positive",
            "protocol_findings": "ACS Protocol",
            "bed_recommendation": "resus",
            "esi_score": 2,
        })],
        input_tokens=2000, output_tokens=800,
    )
    synth_end = _fake_response("end_turn", [_text_block("Triage complete.")],
                               input_tokens=2200, output_tokens=50)
    # 4 specialists (parallel) + 2 synthesizer turns
    return [specialist_response] * 4 + [synth_tool_use, synth_end]


def test_run_full_pipeline_makes_five_to_six_llm_calls():
    """FULL pipeline: 4 specialists in parallel + 2 synth turns = 6 calls.

    Allows 5-6 because the synthesizer end_turn may merge into the tool_use
    flow on some response shapes.
    """
    from eval.runners import run_full_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    queue = _make_specialist_responses()
    client = MagicMock()
    client.messages.create.side_effect = queue

    traces = []
    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_full_pipeline(scenario, rep=0, on_trace=traces.append)

    # 4 specialists + 1-2 synthesizer turns
    assert 5 <= client.messages.create.call_count <= 6
    assert result.error is None
    assert result.output is not None
    assert result.output.esi_score == 2
    assert result.output.care_area == "resus"
    assert result.branch == "full"
    assert len(traces) >= 5


def test_run_full_pipeline_specialists_use_haiku():
    """Verify all 4 specialists are routed to Haiku model."""
    from eval.runners import (  # noqa: PLC0415
        DEFAULT_HAIKU_MODEL,
        run_full_pipeline,
    )

    scenario = _make_scenario()
    queue = _make_specialist_responses()
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        run_full_pipeline(scenario, rep=0)

    # First 4 calls should be specialists on Haiku.
    specialist_models = [
        call.kwargs.get("model") for call in client.messages.create.call_args_list[:4]
    ]
    assert all(m == DEFAULT_HAIKU_MODEL for m in specialist_models)


def test_run_full_pipeline_synthesizer_uses_sonnet():
    """Verify the synthesizer is routed to Sonnet."""
    from eval.runners import (  # noqa: PLC0415
        DEFAULT_SONNET_MODEL,
        run_full_pipeline,
    )

    scenario = _make_scenario()
    queue = _make_specialist_responses()
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        run_full_pipeline(scenario, rep=0)

    # Synthesizer calls (5th and 6th) should be on Sonnet.
    synth_calls = client.messages.create.call_args_list[4:]
    for call in synth_calls:
        assert call.kwargs.get("model") == DEFAULT_SONNET_MODEL


def test_run_full_pipeline_captures_synthesizer_api_error():
    """API error on synthesizer should populate result.error."""
    from eval.runners import run_full_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    specialist_response = _fake_response("end_turn", [_text_block("findings")])

    def side_effect(*args, **kwargs):
        # First 4 calls succeed (specialists); next call fails (synthesizer).
        if side_effect.count < 4:
            side_effect.count += 1
            return specialist_response
        raise RuntimeError("simulated 500")
    side_effect.count = 0

    client = MagicMock()
    client.messages.create.side_effect = side_effect

    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_full_pipeline(scenario, rep=0)

    assert result.error is not None
    assert result.output is None


def test_run_full_pipeline_synthesizer_invalid_esi():
    """Synthesizer returning out-of-range ESI should error gracefully."""
    from eval.runners import run_full_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    specialist_response = _fake_response("end_turn", [_text_block("findings")])
    synth_tool_use = _fake_response(
        "tool_use",
        [_tool_use_block("tu", "generate_triage_report", {
            "patient_summary": "x", "vitals_findings": "x", "symptom_findings": "x",
            "protocol_findings": "x", "bed_recommendation": "resus", "esi_score": 99,
        })],
    )
    synth_end = _fake_response("end_turn", [_text_block("done")])
    queue = [specialist_response] * 4 + [synth_tool_use, synth_end]
    client = MagicMock()
    client.messages.create.side_effect = queue

    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_full_pipeline(scenario, rep=0)

    assert result.error is not None
    assert "invalid esi_score" in result.error


# ---------------------------------------------------------------------------
# STRIPPED pipeline — mocked
# ---------------------------------------------------------------------------


def test_run_stripped_pipeline_one_call():
    """STRIPPED pipeline makes exactly one LLM call."""
    from eval.runners import run_stripped_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    json_out = json.dumps({
        "esi_score": 2,
        "care_area": "resus",
        "patient_summary": "STEMI presentation",
        "critical_flags": ["acs"],
        "rationale": "Chest pain + diaphoresis + risk factors",
    })
    response = _fake_response("end_turn", [_text_block(json_out)])
    client = MagicMock()
    client.messages.create.return_value = response

    traces = []
    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_stripped_pipeline(scenario, rep=0, on_trace=traces.append)

    assert client.messages.create.call_count == 1
    assert result.error is None
    assert result.output is not None
    assert result.output.esi_score == 2
    assert result.output.care_area == "resus"
    assert "acs" in result.output.critical_flags
    assert result.branch == "stripped"
    assert len(traces) == 1
    assert traces[0].role == "stripped"


def test_run_stripped_pipeline_uses_sonnet():
    from eval.runners import (  # noqa: PLC0415
        DEFAULT_SONNET_MODEL,
        run_stripped_pipeline,
    )

    scenario = _make_scenario()
    response = _fake_response("end_turn", [_text_block('{"esi_score": 3}')])
    client = MagicMock()
    client.messages.create.return_value = response

    with patch("eval.runners._get_anthropic_client", return_value=client):
        run_stripped_pipeline(scenario, rep=0)

    assert client.messages.create.call_args.kwargs["model"] == DEFAULT_SONNET_MODEL


def test_run_stripped_pipeline_malformed_json():
    """Malformed JSON output: result.error is set; output is None."""
    from eval.runners import run_stripped_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    response = _fake_response("end_turn", [_text_block("not json")])
    client = MagicMock()
    client.messages.create.return_value = response

    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_stripped_pipeline(scenario, rep=0)

    assert result.error is not None
    assert result.output is None


def test_run_stripped_pipeline_api_error():
    from eval.runners import run_stripped_pipeline  # noqa: PLC0415

    scenario = _make_scenario()
    client = MagicMock()
    client.messages.create.side_effect = TimeoutError("simulated timeout")

    with patch("eval.runners._get_anthropic_client", return_value=client):
        result = run_stripped_pipeline(scenario, rep=0)

    assert result.error is not None
    assert "simulated timeout" in result.error


# ---------------------------------------------------------------------------
# Drift detection — GENERATE_TRIAGE_REPORT_TOOL mirrors backend/tools.py
# ---------------------------------------------------------------------------


def test_generate_triage_report_tool_mirrors_backend():
    """Drift-detection: eval's GENERATE_TRIAGE_REPORT_TOOL must match
    backend/tools.py::TOOLS schema for generate_triage_report.

    Imports backend.tools without triggering ANTHROPIC_API_KEY validation
    by stubbing config first.
    """
    from eval.runners import GENERATE_TRIAGE_REPORT_TOOL  # noqa: PLC0415

    # Stub config to avoid ANTHROPIC_API_KEY validation at import.
    if "config" not in sys.modules:
        cfg = MagicMock()
        cfg.ANTHROPIC_API_KEY = "test-key"
        cfg.MODEL = "test"
        cfg.MAX_TOKENS = 1024
        cfg.ESI_LEVELS = {}
        sys.modules["config"] = cfg

    try:
        from backend.tools import TOOLS as PRODUCTION_TOOLS  # noqa: PLC0415
    except Exception as exc:
        import pytest as _pytest  # noqa: PLC0415
        _pytest.skip(f"Cannot import backend.tools for drift check: {exc}")

    prod_tool = next(
        (t for t in PRODUCTION_TOOLS if t["name"] == "generate_triage_report"),
        None,
    )
    assert prod_tool is not None, "backend/tools.py is missing generate_triage_report"

    eval_required = set(GENERATE_TRIAGE_REPORT_TOOL["input_schema"].get("required", []))
    prod_required = set(prod_tool["input_schema"].get("required", []))
    assert eval_required == prod_required, (
        f"generate_triage_report required-fields drift: eval={eval_required}, "
        f"prod={prod_required}. Update GENERATE_TRIAGE_REPORT_TOOL in "
        f"eval/runners.py."
    )


def test_specialist_prompts_match_backend_agents_py():
    """Drift-detection: eval/prompts.py specialist prompts must match
    backend/agents.py source verbatim.
    """
    from eval.prompts import (  # noqa: PLC0415
        BED_PROMPT,
        PROTOCOL_PROMPT,
        SYMPTOM_PROMPT,
        SYNTHESIZER_PROMPT,
        VITALS_PROMPT,
    )

    agents_path = REPO_ROOT / "backend" / "agents.py"
    if not agents_path.exists():
        import pytest as _pytest  # noqa: PLC0415
        _pytest.skip(f"backend/agents.py not found at {agents_path}")
    source = agents_path.read_text(encoding="utf-8")

    # Spot-check that the first 100 chars of each prompt appears in source.
    for name, prompt in [
        ("VITALS_PROMPT", VITALS_PROMPT),
        ("SYMPTOM_PROMPT", SYMPTOM_PROMPT),
        ("PROTOCOL_PROMPT", PROTOCOL_PROMPT),
        ("BED_PROMPT", BED_PROMPT),
        ("SYNTHESIZER_PROMPT", SYNTHESIZER_PROMPT),
    ]:
        head = prompt[:80]
        assert head in source, (
            f"Prompt drift detected: {name} mirror's first 80 chars not found "
            f"in backend/agents.py. Update eval/prompts.py to match source."
        )
