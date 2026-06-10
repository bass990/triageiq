import json
import random
import sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from config import MODEL_FAST, MODEL_SMART, MAX_TOKENS, ANTHROPIC_API_KEY
from backend.tools import TOOLS, TOOL_MAP
from backend.agents import (
    COORDINATOR_PROMPT, VITALS_PROMPT, SYMPTOM_PROMPT,
    PROTOCOL_PROMPT, BED_PROMPT, SYNTHESIZER_PROMPT
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=45.0)


def _generate_trends(vitals: dict) -> dict:
    """Generate synthetic historical vital sign trends for sparkline charts.
    Direction is clinically meaningful: abnormal values trend toward current,
    normal values are shown as stable.
    """
    def stable(current, n=6, noise=0.02):
        readings = [round(current * (1 + random.uniform(-noise, noise)), 1) for _ in range(n - 1)]
        readings.append(current)
        return readings

    def trending(current, start_ratio, n=6, noise=0.02):
        start = current * start_ratio
        readings = []
        for i in range(n - 1):
            t = i / (n - 1)
            base = start + (current - start) * t
            readings.append(round(base * (1 + random.uniform(-noise, noise)), 1))
        readings.append(current)
        return readings

    trends = {}

    if "hr" in vitals:
        hr = vitals["hr"]
        if hr > 100:
            trends["hr"] = trending(hr, start_ratio=0.85, noise=0.02)
        elif hr < 60:
            trends["hr"] = trending(hr, start_ratio=1.15, noise=0.02)
        else:
            trends["hr"] = stable(hr)

    if "spo2" in vitals:
        spo2 = vitals["spo2"]
        trends["spo2"] = trending(spo2, start_ratio=1.03, noise=0.004) if spo2 < 95 else stable(spo2, noise=0.004)

    if "rr" in vitals:
        rr = vitals["rr"]
        trends["rr"] = trending(rr, start_ratio=0.78, noise=0.03) if rr > 20 else stable(rr, noise=0.03)

    if "temp" in vitals:
        temp = vitals["temp"]
        if temp > 38.0:
            trends["temp"] = trending(temp, start_ratio=0.985, noise=0.001)
        elif temp < 36.0:
            trends["temp"] = trending(temp, start_ratio=1.015, noise=0.001)
        else:
            trends["temp"] = stable(temp, noise=0.001)

    if "gcs" in vitals:
        gcs = vitals["gcs"]
        if gcs < 14:
            raw = trending(gcs, start_ratio=1.15, noise=0.01)
            trends["gcs"] = [max(1, min(15, int(round(v)))) for v in raw]
        else:
            trends["gcs"] = [15] * 6

    if "bp" in vitals:
        try:
            sys_bp = float(str(vitals["bp"]).split("/")[0])
            if sys_bp < 90:
                raw = trending(sys_bp, start_ratio=1.25, noise=0.03)
            elif sys_bp > 140:
                raw = trending(sys_bp, start_ratio=0.88, noise=0.03)
            else:
                raw = stable(sys_bp, noise=0.03)
            trends["bp_sys"] = [round(v, 1) for v in raw]
        except (ValueError, IndexError):
            pass

    return trends


def run_specialist(specialist_prompt: str, patient_data: dict,
                   specialist_name: str, extra_tools: list = None,
                   model: str = None, max_retries: int = 1) -> str:
    """
    Run a single specialist agent and return its findings as a string.
    Each specialist runs its own mini agentic loop with its specific tools.
    Retries once on timeout before giving up.
    """
    specialist_tools = extra_tools or []
    selected_model = model or MODEL_FAST

    for attempt in range(max_retries + 1):
        messages = [{
            "role": "user",
            "content": (
                f"Analyze this patient as the {specialist_name}.\n\n"
                f"Patient data: {json.dumps(patient_data, indent=2)}\n\n"
                f"Provide your specialist assessment."
            )
        }]

        for _ in range(8):  # max turns per specialist
            response = client.messages.create(
                model=selected_model,
                max_tokens=1024,
                system=specialist_prompt,
                tools=specialist_tools if specialist_tools else [{"name": "none", "description": "No tools needed", "input_schema": {"type": "object", "properties": {}}}],
                messages=messages
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "No findings."

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "none":
                            result = {"ok": True}
                        elif block.name in TOOL_MAP:
                            result = TOOL_MAP[block.name](**block.input)
                        else:
                            result = {"error": f"Unknown tool: {block.name}"}
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })
                messages.append({"role": "user", "content": tool_results})

        # Timed out this attempt — retry if we have attempts left
        if attempt < max_retries:
            continue

    return f"[{specialist_name} timed out — findings unavailable]"


def run_triage_agent(patient_input: str, on_progress=None, vitals_str: str = None) -> dict:
    """
    Main orchestrator — runs all specialist agents and synthesizes findings.

    Args:
        patient_input: Either a patient_id (e.g. 'PT-001') or
                       a free-text description of the patient
        on_progress: Optional callback(step, message) for streaming updates
        vitals_str: Optional vitals string for free-text mode

    Returns:
        dict with keys: success, report, error
    """
    def log(step, msg, meta=None):
        if on_progress:
            on_progress(step, msg, meta)

    # ── Step 1: Get patient data ──────────────────────────────────────────────
    log(1, "Retrieving patient data...")
    from backend.tools import get_patient_data, search_protocols

    if patient_input.startswith("PT-"):
        result = get_patient_data(patient_input)
        patient_data = result.get("patient", {})
    else:
        # Free-text input — treat as chief complaint
        patient_data = {
            "chief_complaint": patient_input,
            "vitals": vitals_str if vitals_str else "NOT PROVIDED — do not invent values. Note which vitals are missing and flag that a full set should be obtained before finalizing ESI score.",
            "history": "Not provided",
            "name": "Walk-in patient"
        }

    # ── Step 2: Run 4 specialists in parallel ────────────────────────────────
    log(2, "Running all specialists in parallel...", {"active_agents": ["vitals", "symptoms", "protocols", "beds"]})

    chief = patient_data.get("chief_complaint", "general complaint")
    protocol_result = search_protocols(chief, top_k=2)
    protocol_context = {**patient_data, "protocols_found": protocol_result.get("protocols", [])}

    specialist_tasks = {
        "vitals":   (VITALS_PROMPT,   patient_data,      "Vitals Analyzer"),
        "symptoms": (SYMPTOM_PROMPT,  patient_data,      "Symptom Classifier"),
        "protocols":(PROTOCOL_PROMPT, protocol_context,  "Protocol Matcher"),
        "beds":     (BED_PROMPT,      patient_data,      "Bed Allocator"),
    }

    key_to_step = {"vitals": 2, "symptoms": 3, "protocols": 4, "beds": 5}
    key_to_done_msg = {
        "vitals":    "Vitals Analyzer complete",
        "symptoms":  "Symptom Classifier complete",
        "protocols": "Protocol Matcher complete",
        "beds":      "Bed Allocator complete",
    }

    findings = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(run_specialist, prompt, data, name): key
            for key, (prompt, data, name) in specialist_tasks.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                findings[key] = future.result()
            except Exception as e:
                specialist_name = specialist_tasks[key][2]
                findings[key] = f"[{specialist_name} error — {e}]"
            log(key_to_step[key], key_to_done_msg[key], {"agent_done": key})

    vitals_findings   = findings["vitals"]
    symptom_findings  = findings["symptoms"]
    protocol_findings = findings["protocols"]
    bed_findings      = findings["beds"]

    # ── Check for specialist failures ─────────────────────────────────────────
    failed = [name for name, result in [
        ("Vitals Analyzer", vitals_findings),
        ("Symptom Classifier", symptom_findings),
        ("Protocol Matcher", protocol_findings),
        ("Bed Allocator", bed_findings),
    ] if "timed out" in result or "unavailable" in result]

    if len(failed) >= 3:
        return {"success": False, "error": f"Too many specialist failures: {', '.join(failed)}", "report": None}

    # ── Step 3: Synthesizer produces final report ─────────────────────────────
    log(6, "Synthesizing final triage decision...")

    synthesis_input = {
        "patient_data": patient_data,
        "vitals_findings": vitals_findings,
        "symptom_findings": symptom_findings,
        "protocol_findings": protocol_findings,
        "bed_findings": bed_findings
    }

    # Run coordinator with full tools to generate the report
    messages = [{
        "role": "user",
        "content": (
            f"Based on all specialist findings below, generate the final triage report "
            f"using generate_triage_report().\n\n"
            f"Patient: {json.dumps(patient_data, indent=2)}\n\n"
            f"VITALS ANALYSIS:\n{vitals_findings}\n\n"
            f"SYMPTOM ANALYSIS:\n{symptom_findings}\n\n"
            f"PROTOCOL MATCH:\n{protocol_findings}\n\n"
            f"BED ALLOCATION:\n{bed_findings}\n\n"
            f"Now call generate_triage_report() with your synthesized assessment."
        )
    }]

    triage_report = None
    for _ in range(10):
        response = client.messages.create(
            model=MODEL_SMART,
            max_tokens=MAX_TOKENS,
            system=SYNTHESIZER_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if block.name in TOOL_MAP:
                        result = TOOL_MAP[block.name](**block.input)
                        if block.name == "generate_triage_report" and result.get("success"):
                            triage_report = result["report"]
                    else:
                        result = {"error": f"Unknown tool: {block.name}"}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})

    if triage_report:
        # Attach structured vitals for chart rendering
        if isinstance(patient_data.get("vitals"), dict):
            triage_report["vitals_raw"] = patient_data["vitals"]
            triage_report["vitals_trends"] = _generate_trends(patient_data["vitals"])
        # Enrich with specialist detail
        triage_report["vitals_detail"] = vitals_findings
        triage_report["symptom_detail"] = symptom_findings
        triage_report["protocol_detail"] = protocol_findings
        triage_report["bed_detail"] = bed_findings
        log(7, "Triage complete.")
        return {"success": True, "report": triage_report}

    return {"success": False, "error": "Synthesizer did not produce a report.", "report": None}


if __name__ == "__main__":
    result = run_triage_agent("PT-001")
    print(json.dumps(result, indent=2))
