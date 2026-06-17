"""Programmatic encoding of the TriageIQ eval rubric.

See eval/RUBRIC.md for prose authority. This module is the deterministic
subset — given a patient's chief complaint + vitals, what ESI SHOULD they
receive per the rules in RUBRIC.md §1-4?

The scenario tests call rubric_canonical_esi() on every scenario's patient
and verify the gold answer is consistent with this rubric. If a scenario's
gold disagrees with the rubric, the test FAILS until the scenario or the
rubric is updated explicitly.

Exception: scenarios with `is_critical_miss_test=True` deliberately have
surface-benign vitals + a high-acuity underlying condition; for those the
rubric_audit returns a lower deterministic ESI but the gold remains ESI 1-2
because the underlying condition demands it. The test handles this carve-out.
"""

from __future__ import annotations

from eval.schemas import CareArea, Patient, Vitals

# ---------------------------------------------------------------------------
# Vital-sign severity (RUBRIC §2)
# ---------------------------------------------------------------------------


def _parse_sbp(bp: str | None) -> float | None:
    """Extract systolic BP from a 'systolic/diastolic' string."""
    if not bp:
        return None
    try:
        return float(str(bp).split("/")[0].strip())
    except (ValueError, IndexError):
        return None


def vital_sign_severity(vitals: Vitals) -> str:
    """Return one of 'critical', 'concerning', 'normal'.

    Aggregation: any single critical vital → 'critical'.
    Otherwise: any concerning vital → 'concerning'. Else 'normal'.
    """
    critical = 0
    concerning = 0

    sbp = _parse_sbp(vitals.bp)
    if sbp is not None:
        if sbp < 80 or sbp > 220:
            critical += 1
        elif sbp < 90 or sbp > 180:
            concerning += 1

    if vitals.hr is not None:
        if vitals.hr > 130 or vitals.hr < 40:
            critical += 1
        elif vitals.hr > 100 or vitals.hr < 60:
            concerning += 1

    if vitals.rr is not None:
        if vitals.rr > 30 or vitals.rr < 8:
            critical += 1
        elif vitals.rr > 20 or vitals.rr < 10:
            concerning += 1

    if vitals.spo2 is not None:
        if vitals.spo2 < 90:
            critical += 1
        elif vitals.spo2 < 95:
            concerning += 1

    if vitals.temp is not None:
        if vitals.temp > 39.5 or vitals.temp < 35:
            critical += 1
        elif vitals.temp > 38.3 or vitals.temp < 36:
            concerning += 1

    if vitals.gcs is not None:
        if vitals.gcs <= 12:
            critical += 1
        elif vitals.gcs < 15:
            concerning += 1

    if critical >= 1:
        return "critical"
    if concerning >= 1:
        return "concerning"
    return "normal"


# ---------------------------------------------------------------------------
# Red-flag symptoms (RUBRIC §3)
# ---------------------------------------------------------------------------


# Each entry: canonical key, keyword list, default ESI when matched.
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


def red_flag_keywords(chief_complaint: str, history: str = "") -> list[str]:
    """Return list of canonical red-flag keys matched in chief_complaint+history."""
    text = (chief_complaint + " " + history).lower()
    matches = []
    for key, keywords, _esi in RED_FLAG_RULES:
        if any(kw in text for kw in keywords):
            matches.append(key)
    return matches


def red_flag_min_esi(chief_complaint: str, history: str = "") -> int | None:
    """Return the lowest (most-urgent) ESI implied by matched red flags, or None."""
    text = (chief_complaint + " " + history).lower()
    matched_esis = []
    for _key, keywords, esi in RED_FLAG_RULES:
        if any(kw in text for kw in keywords):
            matched_esis.append(esi)
    if not matched_esis:
        return None
    return min(matched_esis)


# ---------------------------------------------------------------------------
# Canonical ESI derivation (RUBRIC §1 aggregated)
# ---------------------------------------------------------------------------


def rubric_canonical_esi(patient: Patient) -> tuple[int, list[int]]:
    """Return (canonical_esi, acceptable_esi_set) for a patient.

    Applies Rules 1-5 + vital-sign aggregation + red-flag elevation.

    Logic:
    - Start with vital-sign severity.
        - 'critical' → ESI 1-2 candidate.
        - 'concerning' → ESI 2-3 candidate.
        - 'normal' → ESI 3-5 candidate based on red flags + complaint.
    - Layer red-flag elevation. If a red flag is present and implies a more
      urgent ESI than vital signs alone, elevate.
    - The canonical_esi is the most defensible single answer.
    - The acceptable_esi_set is a 2-3 entry list spanning the plausible range.

    This rubric is deliberately conservative — it gives the AGENT the benefit
    of the doubt on borderline cases (acceptable_esi can span 2 levels). The
    `critical_miss_test` tier tests the limits of this conservatism.
    """
    severity = vital_sign_severity(patient.vitals)
    red_flag_esi = red_flag_min_esi(patient.chief_complaint, patient.history)

    if severity == "critical":
        # Critical vitals — ESI 1 or 2 mandatory.
        if red_flag_esi is not None and red_flag_esi == 1:
            return 1, [1, 2]
        return 2, [1, 2]

    if severity == "concerning":
        # Concerning vitals — ESI 2-3 by default; red flags can elevate to 1-2.
        if red_flag_esi is not None and red_flag_esi <= 2:
            return 2, [1, 2, 3]
        return 3, [2, 3]

    # Normal vitals. Red flags elevate, else base on resource estimate.
    if red_flag_esi is not None:
        if red_flag_esi == 1:
            return 2, [1, 2]
        if red_flag_esi == 2:
            return 2, [2, 3]
    # Normal vitals, no red flags — default to ESI 3-5 range; without more
    # information default to 4 (mid-low acuity).
    return 4, [3, 4, 5]


# ---------------------------------------------------------------------------
# Care-area mapping (RUBRIC §4)
# ---------------------------------------------------------------------------


CARE_AREA_MAP: dict[int, list[CareArea]] = {
    1: ["trauma_bay", "resus"],
    2: ["resus", "trauma_bay"],
    3: ["fast_track", "general"],
    4: ["general", "fast_track"],
    5: ["waiting", "general"],
}


def care_area_for(esi: int) -> list[CareArea]:
    """Return acceptable care areas for an ESI score."""
    return list(CARE_AREA_MAP.get(esi, ["general"]))
