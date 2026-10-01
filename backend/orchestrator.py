"""TriageIQ pipeline.

    patient record -> sanitise -> specialists (lean: Symptom only; full: 4) -> Synthesizer
    -> validated structured report (one repair round) -> trace + audit record

The June 2026 eval (30 scenarios x 2 branches x 3 reps) found the four-Haiku-
specialist pipeline no safer than a single Sonnet call (critical-miss rate 0%
on both), 20pp worse on care-area assignment, and better only on red-flag
documentation, which the Symptom specialist alone provides. The default
pipeline is therefore "lean"; TRIAGEIQ_PIPELINE=full keeps the original shape
for the A/B.

When the model API fails (connection, 5xx, ceilings) the rubric rules in
backend/fallback.py produce a degraded, LOW-confidence recommendation rather
than a stack trace.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import config  # noqa: E402
from backend import fallback as fallback_mod  # noqa: E402
from backend.agents import BED_PROMPT, PROTOCOL_PROMPT, SYMPTOM_PROMPT, SYNTHESIZER_PROMPT, VITALS_PROMPT  # noqa: E402
from backend.sanitize import render_patient_xml, scan_patient, tokenize_patient  # noqa: E402
from backend.schemas import TRIAGE_REPORT_TOOL  # noqa: E402
from backend.telemetry import CeilingExceeded, Trace, append_audit  # noqa: E402
from backend.tools import generate_triage_report, get_patient_data, search_protocols  # noqa: E402

_CLIENT = None


def get_client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=config.require_api_key(), timeout=config.REQUEST_TIMEOUT_S,
                                      max_retries=config.API_MAX_RETRIES)
    return _CLIENT


def _system(text: str) -> list[dict]:
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _text_of(resp) -> str:
    return "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or [])


# ── synthetic vitals history for the UI sparklines (unchanged from v1) ───────

def _generate_trends(vitals: dict) -> dict:
    def stable(current, n=6, noise=0.02):
        readings = [round(current * (1 + random.uniform(-noise, noise)), 1) for _ in range(n - 1)]
        return [*readings, current]

    def trending(current, start_ratio, n=6, noise=0.02):
        start = current * start_ratio
        out = []
        for i in range(n - 1):
            t = i / (n - 1)
            out.append(round((start + (current - start) * t) * (1 + random.uniform(-noise, noise)), 1))
        return [*out, current]

    trends: dict = {}
    hr = vitals.get("hr")
    if isinstance(hr, (int, float)):
        trends["hr"] = trending(hr, 0.85) if hr > 100 else trending(hr, 1.15) if hr < 60 else stable(hr)
    spo2 = vitals.get("spo2")
    if isinstance(spo2, (int, float)):
        trends["spo2"] = trending(spo2, 1.03, noise=0.004) if spo2 < 95 else stable(spo2, noise=0.004)
    rr = vitals.get("rr")
    if isinstance(rr, (int, float)):
        trends["rr"] = trending(rr, 0.78, noise=0.03) if rr > 20 else stable(rr, noise=0.03)
    temp = vitals.get("temp")
    if isinstance(temp, (int, float)):
        trends["temp"] = trending(temp, 0.985, noise=0.001) if temp > 38 else trending(temp, 1.015, noise=0.001) if temp < 36 else stable(temp, noise=0.001)
    gcs = vitals.get("gcs")
    if isinstance(gcs, (int, float)):
        trends["gcs"] = [max(1, min(15, int(round(v)))) for v in trending(gcs, 1.15, noise=0.01)] if gcs < 14 else [15] * 6
    try:
        sbp = float(str(vitals.get("bp", "")).split("/")[0])
        trends["bp_sys"] = [round(v, 1) for v in (trending(sbp, 1.25, noise=0.03) if sbp < 90 else trending(sbp, 0.88, noise=0.03) if sbp > 140 else stable(sbp, noise=0.03))]
    except (ValueError, IndexError):
        pass
    return trends


# ── run context ───────────────────────────────────────────────────────────────

@dataclass
class RunContext:
    trace: Trace
    client: Any = None
    on_progress: Any = None
    pipeline: str = config.PIPELINE
    sanitize: bool = config.SANITIZE_INPUTS
    model_fast: str = config.MODEL_FAST
    model_smart: str = config.MODEL_SMART
    findings: dict = field(default_factory=dict)

    def log(self, step, msg, meta=None):
        if self.on_progress:
            self.on_progress(step, msg, meta)

    def create(self, name: str, **kw):
        t0 = time.time()
        resp = self.client.messages.create(**kw)
        self.trace.record_call(name, kw.get("model", ""), resp, time.time() - t0)
        return resp


SPECIALISTS = {
    "vitals": (VITALS_PROMPT, "Vitals Analyzer"),
    "symptoms": (SYMPTOM_PROMPT, "Symptom Classifier"),
    "protocols": (PROTOCOL_PROMPT, "Protocol Matcher"),
    "beds": (BED_PROMPT, "Bed Allocator"),
}
LEAN_SPECIALISTS = ("symptoms",)
FULL_SPECIALISTS = ("vitals", "symptoms", "protocols", "beds")
_STEP = {"vitals": 2, "symptoms": 3, "protocols": 4, "beds": 5}


def run_specialist(ctx: RunContext, key: str, record_xml: str) -> str:
    prompt, name = SPECIALISTS[key]
    resp = ctx.create(key, model=ctx.model_fast, max_tokens=config.SPECIALIST_MAX_TOKENS, system=_system(prompt),
                      messages=[{"role": "user", "content": f"Analyze this patient as the {name}.\n\n{record_xml}\n\nProvide your specialist assessment."}])
    return _text_of(resp) or "No findings."


def run_synthesizer(ctx: RunContext, record_xml: str, findings: dict[str, str]) -> dict | None:
    """Sonnet call with the strict report tool; one repair round on validation errors."""
    blocks = [f"{k.upper()} FINDINGS:\n{v}" for k, v in findings.items()]
    messages = [{"role": "user", "content": (
        "Synthesize the final triage decision and call generate_triage_report() exactly once.\n\n"
        f"{record_xml}\n\n" + "\n\n".join(blocks)
    )}]
    report = None
    for turn in range(4):
        resp = ctx.create("synthesizer" if turn == 0 else "synthesizer_repair", model=ctx.model_smart,
                          max_tokens=config.MAX_TOKENS, system=_system(SYNTHESIZER_PROMPT),
                          tools=[TRIAGE_REPORT_TOOL], tool_choice={"type": "tool", "name": "generate_triage_report"} if turn == 0 else {"type": "auto"},
                          messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not calls:
            break
        results = []
        for block in calls:
            res = generate_triage_report(**dict(block.input or {})) if block.name == "generate_triage_report" else {"error": f"Unknown tool: {block.name}"}
            if res.get("success"):
                report = res["report"]
            elif res.get("validation_errors"):
                ctx.trace.parse_failures += 1
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(res)})
        messages.append({"role": "user", "content": results})
        if report is not None or turn >= 1:
            break
    return report


def _load_patient(patient_input: str, vitals_str: str | None) -> dict:
    if patient_input.startswith("PT-"):
        return dict(get_patient_data(patient_input).get("patient", {}))
    return {
        "name": "Walk-in patient",
        "chief_complaint": patient_input,
        "vitals": vitals_str if vitals_str else "NOT PROVIDED — do not invent values; note which vitals are missing and that a full set is needed before finalising the ESI.",
        "history": "Not provided",
        "allergies": "Unknown",
        "arrival": "walk-in",
    }


def run_triage_agent(patient_input: str, on_progress=None, vitals_str: str | None = None, *,
                     client=None, pipeline: str | None = None, audit_log: str | None = None) -> dict:
    """Run the pipeline. Returns {"success", "report", "error"}; the report carries
    pipeline, trace, sanitization, degraded and the explicit red_flags / care_area."""
    trace = Trace(token_ceiling=config.MAX_TOKENS_PER_RUN, call_ceiling=config.MAX_LLM_CALLS_PER_RUN,
                  pipeline=pipeline or config.PIPELINE)
    ctx = RunContext(trace=trace, client=client, on_progress=on_progress, pipeline=trace.pipeline)
    run_id = f"TRG-{uuid.uuid4().hex[:8].upper()}"

    ctx.log(1, "Retrieving patient data...")
    patient = _load_patient(patient_input, vitals_str)
    suspicious = scan_patient(patient) if ctx.sanitize else {}
    safe, token = tokenize_patient(patient, salt=trace.request_id) if ctx.sanitize else (dict(patient), "")
    protocols = search_protocols(patient.get("chief_complaint", ""), top_k=2).get("protocols", [])
    record_xml = render_patient_xml({**safe, "protocols_found": [{"name": p["name"], "key_steps": p.get("key_steps", [])[:5]} for p in protocols]})
    sanitization = {"name_tokenized": bool(token), "suspected_injections": suspicious}

    keys = FULL_SPECIALISTS if ctx.pipeline == "full" else LEAN_SPECIALISTS
    degraded_reason: str | None = None
    report: dict | None = None
    try:
        if ctx.client is None:
            ctx.client = get_client()
        ctx.log(2, f"Running {'all specialists' if len(keys) > 1 else 'Symptom Classifier'}...",
                {"active_agents": list(keys), "skipped_agents": [k for k in FULL_SPECIALISTS if k not in keys]})
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            futures = {pool.submit(run_specialist, ctx, k, record_xml): k for k in keys}
            for fut in as_completed(futures):
                k = futures[fut]
                ctx.findings[k] = fut.result()
                ctx.log(_STEP[k], f"{SPECIALISTS[k][1]} complete", {"agent_done": k})
        ctx.log(6, "Synthesizing final triage decision...", {"active_agents": ["synthesizer"]})
        report = run_synthesizer(ctx, record_xml, ctx.findings)
        if report is None:
            degraded_reason = "Synthesizer did not return a valid report after repair"
    except (anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.RateLimitError,
            anthropic.APITimeoutError, CeilingExceeded, ValueError) as exc:
        degraded_reason = f"{type(exc).__name__}: {exc}"

    if report is None:
        if not config.FALLBACK_ENABLED:
            return {"success": False, "error": degraded_reason or "no report", "report": None, "trace": trace.summary()}
        trace.degraded = True
        ctx.log(6, "Model unavailable — rule-based ESI (degraded mode)", {"active_agents": ["fallback"]})
        report = fallback_mod.rule_based_triage(patient, reason=degraded_reason or "model unavailable")
        level = config.ESI_LEVELS[report["esi_score"]]
        report.update({"esi_label": level["label"], "esi_color": level["color"], "confidence_score": 0.5,
                       "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "generated_by": "TriageIQ rules"})
    else:
        report["degraded"] = False

    if suspicious:
        note = "Possible tampered intake note: instruction-like text in " + ", ".join(suspicious)
        if note not in report.get("red_flags", []):
            report.setdefault("red_flags", []).append(note)

    if isinstance(patient.get("vitals"), dict):
        report["vitals_raw"] = patient["vitals"]
        report["vitals_trends"] = _generate_trends(patient["vitals"])
    skipped = "(specialist not run in the lean pipeline; see the Synthesizer's findings above)"
    report["vitals_detail"] = ctx.findings.get("vitals") or (report.get("vitals_findings", "") if "vitals" not in keys else "")
    report["symptom_detail"] = ctx.findings.get("symptoms", "")
    report["protocol_detail"] = ctx.findings.get("protocols") or ("Matched by keyword: " + ", ".join(p["name"] for p in protocols) + "\n\n" + skipped)
    report["bed_detail"] = ctx.findings.get("beds") or (report.get("bed_recommendation", "") + "\n\n" + skipped)
    report["patient_name"] = patient.get("name", "")
    report["run_id"] = run_id
    report["pipeline"] = ctx.pipeline
    report["specialists_run"] = list(keys)
    report["sanitization"] = sanitization
    report["trace"] = trace.summary()
    report["confidence"] = report.get("confidence_score", 0.7)  # numeric for the UI meter
    report["confidence_label"] = {0.9: "HIGH", 0.7: "MEDIUM", 0.5: "LOW"}.get(report["confidence"], "MEDIUM")

    try:
        append_audit({"run_id": run_id, "request_id": trace.request_id, "patient": patient_input[:40] if patient_input.startswith("PT-") else "free-text",
                      "pipeline": ctx.pipeline, "esi": report["esi_score"], "care_area": report.get("care_area"),
                      "confidence": report["confidence_label"], "degraded": trace.degraded,
                      "suspected_injections": sorted(suspicious), "llm_calls": trace.summary()["llm_calls"],
                      "cost_usd": trace.summary()["cost_usd"], "model": ctx.model_smart}, path=audit_log)
    except OSError:
        pass

    ctx.log(7, "Triage complete." + (" (degraded)" if trace.degraded else ""))
    return {"success": True, "report": report}


if __name__ == "__main__":
    print(json.dumps(run_triage_agent(sys.argv[1] if len(sys.argv) > 1 else "PT-001"), indent=2, default=str))
