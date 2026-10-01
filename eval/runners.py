"""FULL, LEAN and STRIPPED pipeline runners.

FULL     4 Haiku specialists (Vitals, Symptom, Protocol, Bed) in parallel, then
         the Sonnet Synthesizer with the strict generate_triage_report tool.
         5-6 LLM calls. The original architecture.
LEAN     the production default since v2: Symptom specialist (Haiku) + Sonnet
         Synthesizer. 2-3 calls. What the June 2026 eval recommended.
STRIPPED one Sonnet call with the patient record inline as XML. 1 call.

Tool calls (get_patient_data, search_protocols, check_bed_availability) are
mocked with scenario-pinned data: the eval measures the reasoning, not the
EHR integration. Every branch returns a ScenarioResult the scorers consume.
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

from backend.sanitize import render_patient_xml, tokenize_patient
from backend.schemas import TRIAGE_REPORT_TOOL, validate_report
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
from eval.schemas import BRANCHES, Scenario, ScenarioResult, TriageOutput

# Anthropic client is loaded lazily via _get_anthropic_client() so tests can
# patch it without requiring a real API key at import time.
_ANTHROPIC_CLIENT = None


def _get_anthropic_client():
    """Lazy-load the Anthropic client. Patched in tests."""
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        import anthropic  # noqa: PLC0415

        import config  # noqa: PLC0415  (loads .env)
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=config.require_api_key(), max_retries=2, timeout=90)
    return _ANTHROPIC_CLIENT


# The production tool schema, imported rather than mirrored.
GENERATE_TRIAGE_REPORT_TOOL: dict = TRIAGE_REPORT_TOOL


# ---------------------------------------------------------------------------
# Scenario IO
# ---------------------------------------------------------------------------


def load_scenario(scenario_path: Path) -> Scenario:
    with scenario_path.open("r", encoding="utf-8") as f:
        return Scenario(**json.load(f))


def list_scenarios(scenarios_dir: Path) -> list[Scenario]:
    return [load_scenario(p) for p in sorted(scenarios_dir.glob("*.json"))]


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
    first, last = stripped.find("{"), stripped.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Mocked tool implementations
# ---------------------------------------------------------------------------


def _mock_search_protocols(query: str) -> dict[str, Any]:
    query_lower = (query or "").lower()
    protocols_db = {
        "chest_pain": {"name": "ACS / Chest Pain Protocol", "time_sensitive": True},
        "stroke": {"name": "Stroke / TIA Fast-Track Protocol", "time_sensitive": True},
        "sepsis": {"name": "Sepsis 3-Hour Bundle Protocol", "time_sensitive": True},
        "trauma": {"name": "Trauma / Orthopedic Protocol", "time_sensitive": False},
    }
    keyword_map = {
        "chest_pain": ["chest pain", "acs", "stemi", "cardiac"],
        "stroke": ["stroke", "facial droop", "arm weakness", "speech"],
        "sepsis": ["sepsis", "fever", "hypotension", "altered mental"],
        "trauma": ["trauma", "fall", "fracture", "injury"],
    }
    matches = [protocols_db[k] for k, kws in keyword_map.items() if any(kw in query_lower for kw in kws)]
    if not matches:
        matches = [protocols_db["trauma"]]
    return {"success": True, "protocols": matches[:2], "query": query}


def _mock_check_bed_availability(care_area: str) -> dict[str, Any]:
    snapshot = {
        "trauma_bay": {"total": 2, "available": 1}, "resus": {"total": 4, "available": 2},
        "fast_track": {"total": 8, "available": 5}, "general": {"total": 20, "available": 12},
        "waiting": {"total": 30, "available": 18},
    }
    area = (care_area or "").lower().replace(" ", "_")
    if area in snapshot:
        bed = snapshot[area]
        return {"success": True, "care_area": care_area, "total_beds": bed["total"], "available_beds": bed["available"],
                "status": "available" if bed["available"] > 0 else "full"}
    return {"success": False, "error": f"Unknown care area: {care_area}"}


def _patient_dict(scenario: Scenario) -> dict:
    return scenario.patient.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SONNET_MODEL = "claude-sonnet-5"
SPECIALIST_MAX_TOKENS = 1024
SYNTHESIZER_MAX_TOKENS = 4096
SYNTHESIZER_MAX_TURNS = 10


def _usage(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


def _system(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _record_xml(scenario: Scenario) -> str:
    """Production rendering: name tokenised, fields tagged as data."""
    safe, _ = tokenize_patient(_patient_dict(scenario), salt=scenario.id)
    protos = _mock_search_protocols(scenario.patient.chief_complaint).get("protocols", [])
    return render_patient_xml({**safe, "protocols_found": protos})


def _run_specialist(client, specialist_prompt, specialist_name, role_key, record_xml, model, scenario_id, rep, on_trace, branch="full") -> str:
    user_message = f"Analyze this patient as the {specialist_name}.\n\n{record_xml}\n\nProvide your specialist assessment."
    try:
        t0 = time.time()
        response = client.messages.create(model=model, max_tokens=SPECIALIST_MAX_TOKENS, system=_system(specialist_prompt),
                                          messages=[{"role": "user", "content": user_message}])
        dt = time.time() - t0
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(model=model, role=role_key, input_tokens=in_tok, output_tokens=out_tok, duration_seconds=dt,
                                scenario_id=scenario_id, branch=branch, rep=rep, turn_index=1))
        return "".join(getattr(b, "text", "") for b in response.content) or "No findings."
    except Exception as exc:
        return f"[{specialist_name} error: {type(exc).__name__}: {exc}]"


def _extract_critical_flags(captured: dict) -> list[str]:
    """Explicit red_flags when the model supplied them; keyword scan of the prose otherwise."""
    flags = captured.get("red_flags")
    if isinstance(flags, list) and flags:
        return [str(f) for f in flags]
    text = " ".join(str(v) for v in captured.values() if isinstance(v, str)).lower()
    keywords = ["acs", "stemi", "stroke", "fast", "sepsis", "anaphylaxis", "trauma", "arrest", "airway", "respiratory_failure",
                "sah", "subarachnoid", "surgical_abdomen", "peritonitis", "silent_mi", "posterior_stroke", "pe", "pulmonary_embolism",
                "aaa", "ruptured_aaa", "occult_sepsis", "septic_shock", "biphasic_reaction", "catheter_infection"]
    return [kw for kw in keywords if kw in text]


def _run_synthesizer(client, record_xml, findings, model, scenario_id, rep, on_trace, branch="full") -> tuple[TriageOutput | None, str | None, bool]:
    """Sonnet with the strict report tool. Returns (output, error, schema_valid)."""
    blocks = "\n\n".join(f"{k.upper()} FINDINGS:\n{v}" for k, v in findings.items())
    messages = [{"role": "user", "content": (
        "Synthesize the final triage decision and call generate_triage_report() exactly once.\n\n"
        f"{record_xml}\n\n{blocks}"
    )}]
    captured: dict | None = None
    for turn in range(1, SYNTHESIZER_MAX_TURNS + 1):
        try:
            t0 = time.time()
            response = client.messages.create(model=model, max_tokens=SYNTHESIZER_MAX_TOKENS, system=_system(SYNTHESIZER_PROMPT_EVAL),
                                              tools=[GENERATE_TRIAGE_REPORT_TOOL], messages=messages)
            dt = time.time() - t0
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}", False
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(model=model, role="synthesizer", input_tokens=in_tok, output_tokens=out_tok, duration_seconds=dt,
                                scenario_id=scenario_id, branch=branch, rep=rep, turn_index=turn))
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
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            messages.append({"role": "user", "content": tool_results})
            continue
        break

    if captured is None:
        return None, "Synthesizer did not call generate_triage_report.", False
    esi_raw = captured.get("esi_score")
    if not isinstance(esi_raw, int) or esi_raw not in (1, 2, 3, 4, 5):
        return None, f"Synthesizer returned invalid esi_score: {esi_raw!r}", False

    parsed, _errors = validate_report(captured)
    care_area = None
    if parsed is not None:
        care_area = parsed.care_area
    else:
        bed_rec = (captured.get("care_area") or captured.get("bed_recommendation") or "").lower()
        for area in ("trauma_bay", "resus", "fast_track", "general", "waiting"):
            if area in bed_rec or area.replace("_", " ") in bed_rec:
                care_area = area
                break
    output = TriageOutput(esi_score=esi_raw, care_area=care_area, patient_summary=captured.get("patient_summary", ""),
                          critical_flags=_extract_critical_flags(captured), rationale=captured.get("rationale") or captured.get("symptom_findings", ""))
    return output, None, parsed is not None


def _specialist_pipeline(scenario, rep, keys, branch, model_specialist, model_synthesizer, on_trace) -> ScenarioResult:
    client = _get_anthropic_client()
    start = time.time()
    record_xml = _record_xml(scenario)
    prompts = {"vitals": (VITALS_PROMPT, "Vitals Analyzer"), "symptoms": (SYMPTOM_PROMPT, "Symptom Classifier"),
               "protocols": (PROTOCOL_PROMPT, "Protocol Matcher"), "beds": (BED_PROMPT, "Bed Allocator")}
    findings: dict[str, str] = {}
    error_msg: str | None = None
    schema_valid = False
    try:
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            futures = {pool.submit(_run_specialist, client, prompts[k][0], prompts[k][1], k, record_xml, model_specialist,
                                   scenario.id, rep, on_trace, branch): k for k in keys}
            for future in as_completed(futures):
                k = futures[future]
                try:
                    findings[k] = future.result()
                except Exception as exc:
                    findings[k] = f"[error: {type(exc).__name__}: {exc}]"
        n_errors = sum(1 for v in findings.values() if v.startswith("[") and "error" in v.lower())
        if n_errors >= max(1, (len(keys) + 1) // 2) and n_errors == len(keys):
            error_msg = f"All specialists failed ({n_errors}/{len(keys)}): {next(iter(findings.values()))}"
        else:
            output, synth_err, schema_valid = _run_synthesizer(client, record_xml, findings, model_synthesizer, scenario.id, rep, on_trace, branch)
            if synth_err is not None:
                error_msg = synth_err
            else:
                return ScenarioResult(scenario_id=scenario.id, tier=scenario.tier, branch=branch, rep=rep, output=output,
                                      schema_valid=schema_valid, duration_seconds=time.time() - start)
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
    return ScenarioResult(scenario_id=scenario.id, tier=scenario.tier, branch=branch, rep=rep, output=None, error=error_msg,
                          duration_seconds=time.time() - start)


def run_full_pipeline(scenario: Scenario, rep: int, model_specialist: str = DEFAULT_HAIKU_MODEL,
                      model_synthesizer: str = DEFAULT_SONNET_MODEL, on_trace: Callable[[CallTrace], None] | None = None) -> ScenarioResult:
    """FULL branch: 4 specialists in parallel + Synthesizer."""
    return _specialist_pipeline(scenario, rep, ("vitals", "symptoms", "protocols", "beds"), "full", model_specialist, model_synthesizer, on_trace)


def run_lean_pipeline(scenario: Scenario, rep: int, model_specialist: str = DEFAULT_HAIKU_MODEL,
                      model_synthesizer: str = DEFAULT_SONNET_MODEL, on_trace: Callable[[CallTrace], None] | None = None) -> ScenarioResult:
    """LEAN branch (production default): Symptom specialist + Synthesizer."""
    return _specialist_pipeline(scenario, rep, ("symptoms",), "lean", model_specialist, model_synthesizer, on_trace)


def run_stripped_pipeline(scenario: Scenario, rep: int, model: str = DEFAULT_SONNET_MODEL,
                          on_trace: Callable[[CallTrace], None] | None = None) -> ScenarioResult:
    """STRIPPED branch: one Sonnet call with patient record inline."""
    client = _get_anthropic_client()
    start = time.time()
    user_msg = render_stripped_user_message(_patient_dict(scenario))
    output: TriageOutput | None = None
    error_msg: str | None = None
    try:
        t0 = time.time()
        response = client.messages.create(model=model, max_tokens=SYNTHESIZER_MAX_TOKENS, system=SYSTEM_PROMPT_STRIPPED,
                                          messages=[{"role": "user", "content": user_msg}])
        dt = time.time() - t0
        in_tok, out_tok = _usage(response)
        if on_trace is not None:
            on_trace(make_trace(model=model, role="stripped", input_tokens=in_tok, output_tokens=out_tok, duration_seconds=dt,
                                scenario_id=scenario.id, branch="stripped", rep=rep, turn_index=1))
        text_out = "".join(getattr(b, "text", "") for b in response.content)
        parsed = _parse_json_safe(text_out)
        esi = parsed.get("esi_score")
        if isinstance(esi, int) and esi in (1, 2, 3, 4, 5):
            care_area_raw = parsed.get("care_area")
            care_area = care_area_raw if care_area_raw in ("trauma_bay", "resus", "fast_track", "general", "waiting") else None
            flags_raw = parsed.get("critical_flags", [])
            output = TriageOutput(esi_score=esi, care_area=care_area, patient_summary=str(parsed.get("patient_summary", "")),
                                  critical_flags=[str(f) for f in flags_raw] if isinstance(flags_raw, list) else [],
                                  rationale=str(parsed.get("rationale", "")))
        else:
            error_msg = f"STRIPPED output missing/invalid esi_score: {esi!r}"
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
    return ScenarioResult(scenario_id=scenario.id, tier=scenario.tier, branch="stripped", rep=rep, output=output,
                          schema_valid=output is not None and output.care_area is not None, error=error_msg,
                          duration_seconds=time.time() - start)


RUNNERS = {"full": run_full_pipeline, "lean": run_lean_pipeline, "stripped": run_stripped_pipeline}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _status_message() -> str:
    return (
        "TriageIQ eval harness.\n"
        f"Branches: {', '.join(BRANCHES)}. 30 scenarios across 5 tiers.\n"
        "Run `make eval-small` for a 5-scenario verification (~$0.50).\n"
        "Run `make eval` for the full eval (30 x 3 branches x 3 reps, ~$8)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TriageIQ eval harness CLI")
    parser.add_argument("--mode", choices=["dry", "small", "full"], default="dry")
    parser.add_argument("--scenarios-dir", type=Path, default=Path(__file__).parent / "scenarios")
    parser.add_argument("--reports-dir", type=Path, default=Path(__file__).parent / "reports")
    parser.add_argument("--reps", type=int, default=None, help="Overrides default reps (small=1, full=3).")
    parser.add_argument("--branches", default=",".join(BRANCHES), help="Comma-separated subset of branches.")
    parser.add_argument("--model-specialist", default=DEFAULT_HAIKU_MODEL)
    parser.add_argument("--model-synthesizer", default=DEFAULT_SONNET_MODEL)
    parser.add_argument("--yes", action="store_true", help="Skip the 3-second abort window.")
    parser.add_argument("--resume", action="store_true", help="Reuse successful runs from reports/latest_run.json.")
    parser.add_argument("--rerender", action="store_true", help="Re-score and re-render reports/latest_run.json without model calls.")
    args = parser.parse_args(argv)

    if args.rerender:
        from eval.orchestrator import rerender_snapshot  # noqa: PLC0415
        sys.stderr.write(f"Report re-rendered: {rerender_snapshot(args.reports_dir, args.scenarios_dir)}\n")
        return 0
    if args.mode == "dry":
        sys.stderr.write(_status_message() + "\n")
        return 1

    from eval.orchestrator import DEFAULT_EVAL_SMALL_SCENARIO_IDS, run_and_save  # noqa: PLC0415

    branches = [b.strip() for b in args.branches.split(",") if b.strip()]
    unknown = set(branches) - set(BRANCHES)
    if unknown:
        parser.error(f"unknown branches: {sorted(unknown)}")
    if args.mode == "small":
        scenario_ids = DEFAULT_EVAL_SMALL_SCENARIO_IDS
        n_reps = args.reps if args.reps is not None else 1
    else:
        scenario_ids = [p.stem for p in sorted(args.scenarios_dir.glob("*.json"))]
        n_reps = args.reps if args.reps is not None else 3

    sys.stderr.write(f"Running {len(scenario_ids)} scenarios x {len(branches)} branches x {n_reps} reps "
                     f"(specialist={args.model_specialist}, synth={args.model_synthesizer}).\n"
                     "This will spend real API credits." + ("" if args.yes else " Press Ctrl+C within 3 seconds to abort.") + "\n")
    if not args.yes:
        time.sleep(3)

    resume_snapshot = None
    if args.resume:
        snap_path = args.reports_dir / "latest_run.json"
        if not snap_path.exists():
            parser.error(f"--resume given but {snap_path} does not exist")
        resume_snapshot = json.loads(snap_path.read_text(encoding="utf-8"))

    md_path, json_path, _ = run_and_save(scenario_ids=scenario_ids, branches=branches, n_reps=n_reps, scenarios_dir=args.scenarios_dir,
                                         reports_dir=args.reports_dir, model_specialist=args.model_specialist,
                                         model_synthesizer=args.model_synthesizer, resume_snapshot=resume_snapshot)
    sys.stderr.write(f"\nReport written to: {md_path}\nSnapshot written to: {json_path}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
