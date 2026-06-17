"""FULL and STRIPPED pipeline runners. Day-6 implementation.

FULL branch: mirrors backend/orchestrator.py::run_triage_agent.
- 4 specialists (Vitals, Symptom, Protocol, Bed) run in PARALLEL via
  ThreadPoolExecutor(max_workers=4), each on Haiku.
- Synthesizer runs sequentially after specialists complete, on Sonnet.
- The Synthesizer's tool_use turn (generate_triage_report) is captured.
- All tool calls (get_patient_data, search_protocols, check_bed_availability)
  are mocked to return scenario-pinned data.

STRIPPED branch: one Sonnet call with patient record inline as XML.

Both pipelines return a ScenarioResult that the orchestrator collects, the
scorers consume, and the report renderer summarizes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from eval.instrumentation import CallTrace, make_trace
from eval.prompts import (
    BED_PROMPT,
    PROTOCOL_PROMPT,
    SYMPTOM_PROMPT,
    SYNTHESIZER_PROMPT_EVAL,
    SYSTEM_PROMPT_STRIPPED,
    VITALS_PROMPT,
    render_stripped_user_message,
)
from eval.schemas import Scenario, ScenarioResult, TriageOutput

# Anthropic client is loaded lazily via _get_anthropic_client() so tests can
# patch it without requiring a real API key at import time.
_ANTHROPIC_CLIENT = None


def _get_anthropic_client():
    """Lazy-load the Anthropic client. Patched in tests."""
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        import anthropic  # noqa: PLC0415
        _ANTHROPIC_CLIENT = anthropic.Anthropic()
    return _ANTHROPIC_CLIENT


# Synthesizer tool schema — mirrored from backend/tools.py::TOOLS for
# generate_triage_report. Drift-detection test compares against source.
GENERATE_TRIAGE_REPORT_TOOL: dict = {
    "name": "generate_triage_report",
    "description": (
        "Generate the final structured triage report. Call this LAST after all "
        "specialist findings are complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_summary": {"type": "string", "description": "Brief patient summary"},
            "vitals_findings": {"type": "string", "description": "Critical vitals findings"},
            "symptom_findings": {"type": "string", "description": "Symptom classification"},
            "protocol_findings": {"type": "string", "description": "Matched protocol"},
            "bed_recommendation": {"type": "string", "description": "Care area assignment"},
            "esi_score": {
                "type": "integer",
                "description": "ESI priority score 1-5",
                "minimum": 1,
                "maximum": 5,
            },
        },
        "required": [
            "patient_summary", "vitals_findings", "symptom_findings",
            "protocol_findings", "bed_recommendation", "esi_score",
        ],
    },
}


# ---------------------------------------------------------------------------
# Scenario IO
# ---------------------------------------------------------------------------


def load_scenario(scenario_path: Path) -> Scenario:
    """Load and validate one scenario JSON file."""
    with scenario_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return Scenario(**data)


def list_scenarios(scenarios_dir: Path) -> list[Scenario]:
    """Load all scenarios from a directory, sorted by id."""
    paths = sorted(scenarios_dir.glob("*.json"))
    return [load_scenario(p) for p in paths]


def _parse_json_safe(text: str) -> dict[str, Any]:
    """Extract a JSON object from possibly-prosey LLM output."""
    if not text:
        return {}

    stripped = text.strip()
    for fence in ("```json", "```JSON", "```"):
        if stripped.startswith(fence):
            stripped = stripped[len(fence):].lstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            pass

    return {}


# ---------------------------------------------------------------------------
# Mocked tool implementations (FULL branch)
# ---------------------------------------------------------------------------


def _mock_search_protocols(query: str) -> dict[str, Any]:
    """Return canned protocols based on keyword matching.

    Mirrors backend/tools.py::search_protocols's protocols_db. Simplified
    for eval — agent gets enough to reason about protocols without depending
    on the real ChromaDB / RAG layer.
    """
    query_lower = (query or "").lower()
    protocols_db = {
        "chest_pain": {"name": "ACS / Chest Pain Protocol", "time_sensitive": True},
        "stroke": {"name": "Stroke / TIA Fast-Track Protocol", "time_sensitive": True},
        "sepsis": {"name": "Sepsis 3-Hour Bundle Protocol", "time_sensitive": True},
        "trauma": {"name": "Trauma / Orthopedic Protocol", "time_sensitive": False},
    }
    matches = []
    keyword_map = {
        "chest_pain": ["chest pain", "acs", "stemi", "cardiac"],
        "stroke": ["stroke", "facial droop", "arm weakness", "speech"],
        "sepsis": ["sepsis", "fever", "hypotension", "altered mental"],
        "trauma": ["trauma", "fall", "fracture", "injury"],
    }
    for key, kws in keyword_map.items():
        if any(kw in query_lower for kw in kws):
            matches.append(protocols_db[key])
    if not matches:
        matches = [protocols_db["trauma"]]
    return {"success": True, "protocols": matches[:2], "query": query}


def _mock_check_bed_availability(care_area: str) -> dict[str, Any]:
    """Fixed snapshot — bed scarcity doesn't drive ESI in the eval."""
    snapshot = {
        "trauma_bay": {"total": 2, "available": 1},
        "resus": {"total": 4, "available": 2},
        "fast_track": {"total": 8, "available": 5},
        "general": {"total": 20, "available": 12},
        "waiting": {"total": 30, "available": 18},
    }
    area = (care_area or "").lower().replace(" ", "_")
    if area in snapshot:
        bed = snapshot[area]
        return {
            "success": True,
            "care_area": care_area,
            "total_beds": bed["total"],
            "available_beds": bed["available"],
            "status": "available" if bed["available"] > 0 else "full",
        }
    return {"success": False, "error": f"Unknown care area: {care_area}"}


def _patient_dict(scenario: Scenario) -> dict:
    """Serialize the scenario's patient into a dict for prompts."""
    return scenario.patient.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# FULL branch — 4 specialists + Synthesizer
# ---------------------------------------------------------------------------


DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SONNET_MODEL = "claude-sonnet-4-6"
SPECIALIST_MAX_TOKENS = 1024
SYNTHESIZER_MAX_TOKENS = 4096
SYNTHESIZER_MAX_TURNS = 10


def _usage(response) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a Messages API response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
    )


def _run_specialist(
    client,
    specialist_prompt: str,
    specialist_name: str,
    role_key: str,
    patient_data: dict,
    model: str,
    scenario_id: str,
    rep: int,
    on_trace: Callable[[CallTrace], None] | None,
) -> str:
    """Run one specialist (Haiku) — single turn, no tools.

    Returns the specialist's text findings, or an error sentinel if the call fails.
    """
    user_message = (
        f"Analyze this patient as the {specialist_name}.\n\n"
        f"Patient data: {json.dumps(patient_data, indent=2)}\n\n"
        f"Provide your specialist assessment."
    )
    try:
        t0 = time.time()
        response = client.messages.create(
            model=model,
            max_tokens=SPECIALIST_MAX_TOKENS,
            system=specialist_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        dt = time.time() - t0
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(
                model=model, role=role_key,
                input_tokens=in_tok, output_tokens=out_tok,
                duration_seconds=dt,
                scenario_id=scenario_id, branch="full", rep=rep, turn_index=1,
            ))
        text_out = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_out += block.text
        return text_out or "No findings."
    except Exception as exc:
        return f"[{specialist_name} error: {type(exc).__name__}: {exc}]"


def _run_synthesizer(
    client,
    patient_data: dict,
    findings: dict[str, str],
    model: str,
    scenario_id: str,
    rep: int,
    on_trace: Callable[[CallTrace], None] | None,
) -> tuple[TriageOutput | None, str | None]:
    """Run the Synthesizer (Sonnet) with all 4 specialist findings.

    Loops up to SYNTHESIZER_MAX_TURNS turns, handles generate_triage_report
    tool calls (mocked), and returns the captured triage output.

    Returns (output, error_msg) — output is None when error_msg is set.
    """
    messages = [{
        "role": "user",
        "content": (
            f"Based on all specialist findings below, generate the final triage report "
            f"using generate_triage_report().\n\n"
            f"Patient: {json.dumps(patient_data, indent=2)}\n\n"
            f"VITALS ANALYSIS:\n{findings['vitals']}\n\n"
            f"SYMPTOM ANALYSIS:\n{findings['symptoms']}\n\n"
            f"PROTOCOL MATCH:\n{findings['protocols']}\n\n"
            f"BED ALLOCATION:\n{findings['beds']}\n\n"
            f"Now call generate_triage_report() with your synthesized assessment."
        ),
    }]

    captured: dict | None = None
    for turn in range(1, SYNTHESIZER_MAX_TURNS + 1):
        try:
            t0 = time.time()
            response = client.messages.create(
                model=model,
                max_tokens=SYNTHESIZER_MAX_TOKENS,
                system=SYNTHESIZER_PROMPT_EVAL,
                tools=[GENERATE_TRIAGE_REPORT_TOOL],
                messages=messages,
            )
            dt = time.time() - t0
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(
                model=model, role="synthesizer",
                input_tokens=in_tok, output_tokens=out_tok,
                duration_seconds=dt,
                scenario_id=scenario_id, branch="full", rep=rep, turn_index=turn,
            ))

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                if block.name == "generate_triage_report":
                    captured = dict(block.input or {})
                    result = {"success": True, "report": {"esi_score": captured.get("esi_score")}}
                else:
                    result = {"error": f"Unknown tool: {block.name}"}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
            messages.append({"role": "user", "content": tool_results})
            if captured is not None:
                # We have the report — let the agent see the tool result and
                # finish with end_turn on the next iteration. But if we've
                # already captured, no need to keep looping past end_turn.
                continue
            continue

        # Any other stop_reason — break.
        break

    if captured is None:
        return None, "Synthesizer did not call generate_triage_report."

    # Coerce captured fields into TriageOutput.
    esi_raw = captured.get("esi_score")
    if not isinstance(esi_raw, int) or esi_raw not in (1, 2, 3, 4, 5):
        return None, f"Synthesizer returned invalid esi_score: {esi_raw!r}"

    bed_rec = (captured.get("bed_recommendation") or "").lower()
    care_area = None
    for area in ("trauma_bay", "resus", "fast_track", "general", "waiting"):
        if area in bed_rec or area.replace("_", " ") in bed_rec:
            care_area = area
            break

    output = TriageOutput(
        esi_score=esi_raw,
        care_area=care_area,
        patient_summary=captured.get("patient_summary", ""),
        critical_flags=_extract_critical_flags(captured),
        rationale=captured.get("symptom_findings", ""),
    )
    return output, None


def _extract_critical_flags(captured: dict) -> list[str]:
    """Pull critical-flag keywords from the captured synthesizer output."""
    text = " ".join(str(v) for v in captured.values() if isinstance(v, str)).lower()
    keywords = [
        "acs", "stemi", "stroke", "fast", "sepsis", "anaphylaxis",
        "trauma", "arrest", "airway", "respiratory_failure", "sah",
        "subarachnoid", "surgical_abdomen", "peritonitis", "silent_mi",
        "posterior_stroke", "pe", "pulmonary_embolism", "aaa", "ruptured_aaa",
        "occult_sepsis", "septic_shock", "biphasic_reaction", "catheter_infection",
    ]
    return [kw for kw in keywords if kw in text]


def run_full_pipeline(
    scenario: Scenario,
    rep: int,
    model_specialist: str = DEFAULT_HAIKU_MODEL,
    model_synthesizer: str = DEFAULT_SONNET_MODEL,
    on_trace: Callable[[CallTrace], None] | None = None,
) -> ScenarioResult:
    """FULL branch: 4 specialists in parallel + Synthesizer.

    Mirrors backend/orchestrator.py::run_triage_agent's structure.
    """
    client = _get_anthropic_client()
    start = time.time()
    patient_data = _patient_dict(scenario)

    # Pre-attach mocked protocol context for Protocol specialist.
    protocol_result = _mock_search_protocols(scenario.patient.chief_complaint)
    protocol_context = {**patient_data, "protocols_found": protocol_result.get("protocols", [])}

    specialist_tasks = {
        "vitals": (VITALS_PROMPT, patient_data, "Vitals Analyzer"),
        "symptoms": (SYMPTOM_PROMPT, patient_data, "Symptom Classifier"),
        "protocols": (PROTOCOL_PROMPT, protocol_context, "Protocol Matcher"),
        "beds": (BED_PROMPT, patient_data, "Bed Allocator"),
    }

    findings: dict[str, str] = {}
    error_msg: str | None = None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(
                    _run_specialist,
                    client, prompt, name, role_key, data,
                    model_specialist, scenario.id, rep, on_trace,
                ): role_key
                for role_key, (prompt, data, name) in specialist_tasks.items()
            }
            for future in as_completed(futures):
                role_key = futures[future]
                try:
                    findings[role_key] = future.result()
                except Exception as exc:
                    findings[role_key] = f"[error: {type(exc).__name__}: {exc}]"

        # If too many specialists errored, abort.
        n_errors = sum(1 for v in findings.values() if v.startswith("[") and "error" in v.lower())
        if n_errors >= 3:
            error_msg = f"Too many specialist failures ({n_errors}/4)"
        else:
            output, synth_err = _run_synthesizer(
                client, patient_data, findings, model_synthesizer,
                scenario.id, rep, on_trace,
            )
            if synth_err is not None:
                error_msg = synth_err
            else:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    tier=scenario.tier,
                    branch="full",
                    rep=rep,
                    output=output,
                    duration_seconds=time.time() - start,
                )
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"

    return ScenarioResult(
        scenario_id=scenario.id,
        tier=scenario.tier,
        branch="full",
        rep=rep,
        output=None,
        error=error_msg,
        duration_seconds=time.time() - start,
    )


# ---------------------------------------------------------------------------
# STRIPPED branch — one Sonnet call
# ---------------------------------------------------------------------------


def run_stripped_pipeline(
    scenario: Scenario,
    rep: int,
    model: str = DEFAULT_SONNET_MODEL,
    on_trace: Callable[[CallTrace], None] | None = None,
) -> ScenarioResult:
    """STRIPPED branch: one Sonnet call with patient record inline."""
    client = _get_anthropic_client()
    start = time.time()
    patient_dict = _patient_dict(scenario)
    user_msg = render_stripped_user_message(patient_dict)

    output: TriageOutput | None = None
    error_msg: str | None = None
    try:
        t0 = time.time()
        response = client.messages.create(
            model=model,
            max_tokens=SYNTHESIZER_MAX_TOKENS,
            system=SYSTEM_PROMPT_STRIPPED,
            messages=[{"role": "user", "content": user_msg}],
        )
        dt = time.time() - t0
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(
                model=model, role="stripped",
                input_tokens=in_tok, output_tokens=out_tok,
                duration_seconds=dt,
                scenario_id=scenario.id, branch="stripped", rep=rep, turn_index=1,
            ))

        text_out = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_out += block.text

        parsed = _parse_json_safe(text_out)
        esi = parsed.get("esi_score")
        if isinstance(esi, int) and esi in (1, 2, 3, 4, 5):
            care_area_raw = parsed.get("care_area")
            care_area = care_area_raw if care_area_raw in (
                "trauma_bay", "resus", "fast_track", "general", "waiting"
            ) else None
            critical_flags_raw = parsed.get("critical_flags", [])
            critical_flags = [str(f) for f in critical_flags_raw] if isinstance(critical_flags_raw, list) else []
            output = TriageOutput(
                esi_score=esi,
                care_area=care_area,
                patient_summary=str(parsed.get("patient_summary", "")),
                critical_flags=critical_flags,
                rationale=str(parsed.get("rationale", "")),
            )
        else:
            error_msg = f"STRIPPED output missing/invalid esi_score: {esi!r}"
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"

    return ScenarioResult(
        scenario_id=scenario.id,
        tier=scenario.tier,
        branch="stripped",
        rep=rep,
        output=output,
        error=error_msg,
        duration_seconds=time.time() - start,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _status_message() -> str:
    return (
        "TriageIQ eval harness — Day 8 status.\n"
        "Runners (FULL + STRIPPED) implemented. 30 scenarios committed.\n"
        "Scorers + orchestrator implemented. eval-small + eval are runnable.\n"
        "Run `make eval-small` for a 5-scenario verification ($0.50-$1).\n"
        "Run `make eval` for the full eval ($5-10)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TriageIQ eval harness CLI")
    parser.add_argument(
        "--mode", choices=["dry", "small", "full"], default="dry",
        help=(
            "dry = status message. "
            "small = 5-scenario eval (~$0.50-$1). "
            "full = all 30 scenarios x both branches x 3 reps (~$5-10)."
        ),
    )
    parser.add_argument(
        "--scenarios-dir", type=Path,
        default=Path(__file__).parent / "scenarios",
    )
    parser.add_argument(
        "--reports-dir", type=Path,
        default=Path(__file__).parent / "reports",
    )
    parser.add_argument("--reps", type=int, default=None,
                        help="Overrides default reps (small=1, full=3).")
    parser.add_argument("--model-specialist", default=DEFAULT_HAIKU_MODEL)
    parser.add_argument("--model-synthesizer", default=DEFAULT_SONNET_MODEL)
    args = parser.parse_args(argv)

    if args.mode == "dry":
        sys.stderr.write(_status_message() + "\n")
        return 1

    from eval.orchestrator import (  # noqa: PLC0415
        DEFAULT_EVAL_SMALL_SCENARIO_IDS,
        run_and_save,
    )

    if args.mode == "small":
        scenario_ids = DEFAULT_EVAL_SMALL_SCENARIO_IDS
        n_reps = args.reps if args.reps is not None else 1
    else:
        scenario_ids = [p.stem for p in sorted(args.scenarios_dir.glob("*.json"))]
        n_reps = args.reps if args.reps is not None else 3

    sys.stderr.write(
        f"Running {len(scenario_ids)} scenarios x 2 branches x {n_reps} reps "
        f"(specialist={args.model_specialist}, synth={args.model_synthesizer}).\n"
        f"This will spend real API credits. Press Ctrl+C within 3 seconds to abort.\n"
    )
    import time as _time  # noqa: PLC0415
    _time.sleep(3)

    md_path, json_path, _ = run_and_save(
        scenario_ids=scenario_ids,
        branches=["full", "stripped"],
        n_reps=n_reps,
        scenarios_dir=args.scenarios_dir,
        reports_dir=args.reports_dir,
        model_specialist=args.model_specialist,
        model_synthesizer=args.model_synthesizer,
    )
    sys.stderr.write(f"\nReport written to: {md_path}\n")
    sys.stderr.write(f"Snapshot written to: {json_path}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
