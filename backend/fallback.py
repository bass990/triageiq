"""Rule-based ESI fallback.

When the model API is unavailable (connection error, 5xx after retries, a
run ceiling) the triage desk still needs an answer. These rules are the
deterministic part of eval/RUBRIC.md (§1-4): vital-sign thresholds, red-flag
keywords and their aggregation. They are conservative by design (they lean
toward over-triage) and every result is marked `degraded=True` with
confidence LOW so the nurse knows it came from rules, not the model.

`tests/test_backend.py::test_fallback_agrees_with_rubric_audit` keeps this
module and eval/rubric_audit.py from drifting apart.
"""
from __future__ import annotations

from typing import Any

# (canonical key, keywords, ESI implied)
RED_FLAG_RULES: list[tuple[str, list[str], int]] = [
    ("acs", ["chest pain", "chest pressure", "radiating to arm", "diaphoresis", "acs", "stemi", "heart attack"], 2),
    ("stroke", ["facial droop", "arm weakness", "slurred speech", "fast positive", "stroke", "tia", "hemiparesis"], 2),
    ("sah", ["worst headache", "thunderclap headache", "worst headache of life", "subarachnoid"], 2),
    ("sepsis", ["fever and hypotension", "sepsis", "septic shock"], 1),
    ("surgical_abdomen", ["rigid abdomen", "rebound tenderness", "surgical abdomen", "peritonitis"], 2),
    ("airway", ["airway emergency", "respiratory failure", "stridor", "accessory muscle", "barking cough"], 1),
    ("anaphylaxis", ["anaphylaxis", "anaphylactic"], 1),
    ("trauma", ["polytrauma", "major trauma", "penetrating trauma", "gsw", "gunshot"], 2),
    ("arrest", ["cardiac arrest", "asystole", "respiratory arrest"], 1),
    ("seizure_active", ["status epilepticus", "active seizure"], 2),
]

CARE_AREA_MAP = {1: "trauma_bay", 2: "resus", 3: "fast_track", 4: "general", 5: "waiting"}


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _sbp(bp: Any) -> float | None:
    if not bp:
        return None
    try:
        return float(str(bp).split("/")[0].strip())
    except (ValueError, IndexError):
        return None


def vital_flags(vitals: dict | None) -> list[str]:
    """Human-readable abnormal vitals, most severe first."""
    v = vitals if isinstance(vitals, dict) else {}
    out: list[tuple[int, str]] = []
    sbp = _sbp(v.get("bp"))
    if sbp is not None:
        if sbp < 80 or sbp > 220:
            out.append((0, f"SBP {sbp:.0f}: critical ({'shock range' if sbp < 80 else 'hypertensive emergency range'})"))
        elif sbp < 90 or sbp > 180:
            out.append((1, f"SBP {sbp:.0f}: {'hypotension' if sbp < 90 else 'severe hypertension'}"))
    for key, lo_c, hi_c, lo_w, hi_w, label in (
        ("hr", 40, 130, 60, 100, "HR"), ("rr", 8, 30, 10, 20, "RR"), ("spo2", 90, 1e9, 95, 1e9, "SpO2"),
        ("temp", 35, 39.5, 36, 38.3, "Temp"),
    ):
        x = _num(v.get(key))
        if x is None:
            continue
        if x < lo_c or x > hi_c:
            out.append((0, f"{label} {x:g}: critical"))
        elif x < lo_w or x > hi_w:
            out.append((1, f"{label} {x:g}: abnormal"))
    g = _num(v.get("gcs"))
    if g is not None:
        if g <= 12:
            out.append((0, f"GCS {g:g}: critical altered mental status"))
        elif g < 15:
            out.append((1, f"GCS {g:g}: altered mental status"))
    return [s for _, s in sorted(out, key=lambda t: t[0])]


def vital_sign_severity(vitals: dict | None) -> str:
    flags = vital_flags(vitals)
    if any(": critical" in f for f in flags):
        return "critical"
    return "concerning" if flags else "normal"


def red_flag_keywords(chief_complaint: str, history: str = "") -> list[str]:
    text = f"{chief_complaint or ''} {history or ''}".lower()
    return [key for key, kws, _ in RED_FLAG_RULES if any(k in text for k in kws)]


def red_flag_min_esi(chief_complaint: str, history: str = "") -> int | None:
    text = f"{chief_complaint or ''} {history or ''}".lower()
    hits = [esi for _, kws, esi in RED_FLAG_RULES if any(k in text for k in kws)]
    return min(hits) if hits else None


def canonical_esi(patient: dict) -> tuple[int, list[int]]:
    """(canonical ESI, acceptable set) exactly as eval/rubric_audit.py derives it."""
    severity = vital_sign_severity(patient.get("vitals"))
    rf = red_flag_min_esi(patient.get("chief_complaint", ""), patient.get("history", ""))
    if severity == "critical":
        return (1, [1, 2]) if rf == 1 else (2, [1, 2])
    if severity == "concerning":
        return (2, [1, 2, 3]) if rf is not None and rf <= 2 else (3, [2, 3])
    if rf == 1:
        return 2, [1, 2]
    if rf == 2:
        return 2, [2, 3]
    return 4, [3, 4, 5]


def rule_based_triage(patient: dict, reason: str = "model unavailable") -> dict:
    """A full report dict from the rules; always degraded, always LOW confidence."""
    esi, acceptable = canonical_esi(patient)
    vflags = vital_flags(patient.get("vitals"))
    rflags = red_flag_keywords(patient.get("chief_complaint", ""), patient.get("history", ""))
    red_flags = vflags + [f"red-flag keyword: {k}" for k in rflags]
    if not isinstance(patient.get("vitals"), dict) or not patient.get("vitals"):
        red_flags.append("vitals not provided: obtain a full set before finalising ESI")
    checklist = {
        1: ["Move to resuscitation area now", "Airway / breathing / circulation assessment", "Physician to bedside immediately",
            "Continuous monitoring, IV access x2", "Nurse confirms ESI (rule-based estimate)"],
        2: ["Physician within 10 minutes", "Continuous monitoring, IV access", "Order protocol-specific workup (ECG / CT / cultures as indicated)",
            "Reassess vitals every 15 minutes", "Nurse confirms ESI (rule-based estimate)"],
        3: ["Register and place in fast track / general", "Full set of vitals and focused history", "Anticipate ≥2 resources (labs, imaging)",
            "Reassess within 30 minutes", "Nurse confirms ESI (rule-based estimate)"],
        4: ["Register; single-resource workup expected", "Analgesia / basic care as indicated", "Reassess if symptoms change",
            "Nurse confirms ESI (rule-based estimate)"],
        5: ["Register to waiting area", "Discharge planning / instructions", "Reassess if symptoms change",
            "Nurse confirms ESI (rule-based estimate)"],
    }[esi]
    return {
        "esi_score": esi,
        "care_area": CARE_AREA_MAP[esi],
        "patient_summary": f"Rule-based estimate ({reason}): ESI {esi} (acceptable {acceptable}) from vital thresholds and red-flag keywords.",
        "red_flags": red_flags,
        "action_checklist": checklist,
        "vitals_findings": "; ".join(vflags) or "No vital-sign thresholds breached.",
        "symptom_findings": ("Red-flag keywords: " + ", ".join(rflags)) if rflags else "No red-flag keywords matched.",
        "protocol_findings": "Protocol matching unavailable in degraded mode; consult the protocol library manually.",
        "bed_recommendation": f"{CARE_AREA_MAP[esi]} (rule-based mapping for ESI {esi})",
        "confidence": "LOW",
        "rationale": f"Model unavailable ({reason}). Vital severity '{vital_sign_severity(patient.get('vitals'))}', "
                     f"red flags {rflags or 'none'}. Rules lean toward over-triage; the nurse decides.",
        "degraded": True,
        "degraded_reason": reason,
        "acceptable_esi": acceptable,
    }
