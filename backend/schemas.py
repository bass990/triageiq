"""Structured-output contract for the triage report.

The Synthesizer must call `generate_triage_report` with these fields. The
tool input is validated against `TriageReportInput`; a failure is sent back
to the model once with the error list (retry-with-error-feedback). Care area
and red flags are explicit enumerated / list fields rather than prose the UI
has to parse, which is what the June 2026 eval found the old free-text
`bed_recommendation` field getting wrong 26% of the time.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

CareArea = Literal["trauma_bay", "resus", "fast_track", "general", "waiting"]
Confidence = Literal["HIGH", "MEDIUM", "LOW"]

CONFIDENCE_SCORE = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}


class TriageReportInput(BaseModel):
    esi_score: int = Field(..., ge=1, le=5)
    care_area: CareArea
    patient_summary: str = Field(..., min_length=1, max_length=600)
    red_flags: list[str] = Field(default_factory=list, max_length=12)
    action_checklist: list[str] = Field(default_factory=list, max_length=8)
    vitals_findings: str = ""
    symptom_findings: str = ""
    protocol_findings: str = ""
    bed_recommendation: str = ""
    confidence: Confidence = "MEDIUM"
    rationale: str = ""

    @field_validator("care_area", mode="before")
    @classmethod
    def _norm_area(cls, v):
        return str(v).strip().lower().replace(" ", "_") if isinstance(v, str) else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _norm_conf(cls, v):
        return str(v).strip().upper() if isinstance(v, str) else v

    @field_validator("red_flags", "action_checklist", mode="before")
    @classmethod
    def _norm_list(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split("\n") if s.strip()]
        return v


def validate_report(data: dict) -> tuple[TriageReportInput | None, list[str]]:
    try:
        return TriageReportInput(**data), []
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def parse_json_object(text: str) -> dict:
    s = (text or "").strip()
    for fence in ("```json", "```JSON", "```"):
        if s.startswith(fence):
            s = s[len(fence):].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try:
            obj = json.loads(s[a:b + 1])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


# JSON schema handed to the model as the tool input (strict: no extra keys).
TRIAGE_REPORT_TOOL = {
    "name": "generate_triage_report",
    "description": "Generate the final structured triage report. Call this LAST, exactly once, after all specialist findings are complete.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "esi_score": {"type": "integer", "minimum": 1, "maximum": 5,
                          "description": "ESI priority 1-5 (1 = immediate life-saving intervention, 5 = no resources)"},
            "care_area": {"type": "string", "enum": ["trauma_bay", "resus", "fast_track", "general", "waiting"],
                          "description": "Where the patient goes. ESI 1: trauma_bay/resus; ESI 2: resus/trauma_bay; ESI 3: fast_track/general; ESI 4: general/fast_track; ESI 5: waiting/general"},
            "patient_summary": {"type": "string", "description": "One-sentence working diagnosis hypothesis"},
            "red_flags": {"type": "array", "items": {"type": "string"}, "maxItems": 12,
                          "description": "Explicit red flags driving the ESI, each naming the finding and the concern, e.g. 'SBP 88 with chest pain: possible cardiogenic shock / MI'. Empty list if none."},
            "action_checklist": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                                 "description": "Immediate actions in priority order (max 8)"},
            "vitals_findings": {"type": "string", "description": "Critical vitals findings and abnormalities"},
            "symptom_findings": {"type": "string", "description": "Symptom classification and red flags"},
            "protocol_findings": {"type": "string", "description": "Matched protocol and key interventions"},
            "bed_recommendation": {"type": "string", "description": "Equipment, consults and time-to-physician for the chosen care_area"},
            "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"],
                           "description": "LOW when vitals are missing/contradictory or the presentation is atypical"},
            "rationale": {"type": "string", "description": "2-3 sentences: why this ESI and not the adjacent levels"},
        },
        "required": ["esi_score", "care_area", "patient_summary", "red_flags", "action_checklist",
                     "vitals_findings", "symptom_findings", "protocol_findings", "bed_recommendation", "confidence"],
    },
}
