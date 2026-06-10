import json
import sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ANTHROPIC_API_KEY

# ─── Mock EHR / hospital data ────────────────────────────────────────────────

MOCK_BEDS = {
    "trauma_bay":  {"total": 2,  "available": 1},
    "resus":       {"total": 4,  "available": 2},
    "fast_track":  {"total": 8,  "available": 5},
    "general":     {"total": 20, "available": 12},
    "waiting":     {"total": 30, "available": 18},
}

MOCK_PATIENTS = {
    "PT-001": {
        "name": "John M., 65M",
        "chief_complaint": "Chest pain radiating to left arm, diaphoresis, nausea for 45 minutes",
        "vitals": {"bp": "88/60", "hr": 112, "rr": 22, "spo2": 94, "temp": 37.1, "gcs": 15},
        "history": "HTN, type 2 diabetes, smoker 30pk/yr",
        "allergies": "Penicillin",
        "arrival": "walk-in"
    },
    "PT-002": {
        "name": "Sarah K., 28F",
        "chief_complaint": "Sudden severe headache, worst of life, onset 20 minutes ago",
        "vitals": {"bp": "180/110", "hr": 88, "rr": 16, "spo2": 98, "temp": 37.0, "gcs": 14},
        "history": "No significant PMH, on OCPs",
        "allergies": "None",
        "arrival": "EMS"
    },
    "PT-003": {
        "name": "Robert T., 45M",
        "chief_complaint": "Right ankle pain after fall, unable to bear weight",
        "vitals": {"bp": "128/82", "hr": 76, "rr": 14, "spo2": 99, "temp": 36.8, "gcs": 15},
        "history": "None",
        "allergies": "None",
        "arrival": "walk-in"
    },
    "PT-004": {
        "name": "Emma L., 8F",
        "chief_complaint": "Barking cough, stridor, labored breathing — woke from sleep unable to breathe normally. Parent reports symptoms worsened rapidly over 2 hours.",
        "vitals": {"bp": "96/62", "hr": 138, "rr": 36, "spo2": 93, "temp": 39.8, "gcs": 14},
        "history": "No significant PMH, vaccinations up to date",
        "allergies": "None",
        "arrival": "EMS"
    },
    "PT-005": {
        "name": "Dorothy W., 72F",
        "chief_complaint": "General weakness, mild confusion, hasn't eaten in 2 days — family says 'just not acting right'. No chest pain or shortness of breath reported.",
        "vitals": {"bp": "104/68", "hr": 98, "rr": 18, "spo2": 96, "temp": 38.1, "gcs": 13},
        "history": "Type 2 diabetes, hypertension, mild dementia — on metformin, lisinopril",
        "allergies": "Sulfa drugs",
        "arrival": "family"
    },
    "PT-006": {
        "name": "Marcus J., 58M",
        "chief_complaint": "Fever, rigors, confusion, and dysuria for 3 days. Now hypotensive and minimally responsive. Had urinary catheterization 1 week ago.",
        "vitals": {"bp": "82/50", "hr": 124, "rr": 28, "spo2": 91, "temp": 39.6, "gcs": 11},
        "history": "BPH, type 2 diabetes, recent urinary catheterization",
        "allergies": "Vancomycin",
        "arrival": "EMS"
    },
}

# Expected acuity hints for patient list display (not used by agents)
PATIENT_ACUITY = {
    "PT-001": 2,
    "PT-002": 2,
    "PT-003": 4,
    "PT-004": 2,
    "PT-005": 3,
    "PT-006": 1,
}

# ─── Tool functions ──────────────────────────────────────────────────────────

def get_patient_data(patient_id: str) -> dict:
    """Retrieve patient intake data from the mock EHR."""
    if patient_id in MOCK_PATIENTS:
        return {"success": True, "patient": MOCK_PATIENTS[patient_id]}
    # Return unknown patient template
    return {
        "success": True,
        "patient": {
            "name": "Unknown",
            "chief_complaint": "To be assessed",
            "vitals": {},
            "history": "Unknown",
            "allergies": "Unknown",
            "arrival": "unknown"
        }
    }

def search_protocols(query: str, top_k: int = 3) -> dict:
    """
    Search clinical protocol knowledge base.
    In demo mode, returns curated protocol data based on keyword matching.
    In production, this would query ChromaDB with embeddings.
    """
    query_lower = query.lower()

    protocols_db = {
        "chest_pain": {
            "name": "ACS / Chest Pain Protocol",
            "keywords": ["chest pain", "acs", "stemi", "cardiac", "heart", "troponin", "ecg"],
            "key_steps": [
                "Obtain 12-lead ECG within 10 minutes of arrival",
                "IV access x2, draw troponin, CBC, BMP, coagulation studies",
                "Aspirin 325mg PO if no contraindication",
                "Activate cath lab if ST elevation confirmed on ECG",
                "Cardiology consult stat",
                "Continuous cardiac monitoring and pulse oximetry",
                "Serial ECGs every 15-30 minutes"
            ],
            "time_sensitive": True,
            "door_to_balloon_target": "90 minutes for STEMI"
        },
        "stroke": {
            "name": "Stroke / TIA Fast-Track Protocol",
            "keywords": ["stroke", "tia", "facial droop", "arm weakness", "speech", "headache", "worst headache"],
            "key_steps": [
                "Activate stroke team immediately",
                "NIHSS score assessment",
                "Non-contrast CT head stat",
                "Blood glucose check immediately",
                "IV access, CBC, BMP, coagulation, type and screen",
                "tPA eligibility assessment — window is 4.5 hours from symptom onset",
                "NPO until swallow evaluation"
            ],
            "time_sensitive": True,
            "door_to_ct_target": "25 minutes",
            "door_to_needle_target": "60 minutes for tPA eligible"
        },
        "sepsis": {
            "name": "Sepsis 3-Hour Bundle Protocol",
            "keywords": ["sepsis", "infection", "fever", "low bp", "hypotension", "altered mental"],
            "key_steps": [
                "Blood cultures x2 before antibiotics",
                "Broad-spectrum antibiotics within 1 hour",
                "Lactate level",
                "30mL/kg IV crystalloid for hypotension or lactate ≥4",
                "Repeat lactate if initial >2",
                "Vasopressors if MAP <65 despite fluids"
            ],
            "time_sensitive": True,
            "bundle_target": "Complete within 3 hours"
        },
        "trauma": {
            "name": "Trauma / Orthopedic Protocol",
            "keywords": ["fall", "injury", "fracture", "ankle", "pain", "swelling", "unable to bear weight"],
            "key_steps": [
                "Ottawa ankle rules to determine X-ray need",
                "Pain assessment and analgesia",
                "X-ray of affected area",
                "Neurovascular assessment distal to injury",
                "Orthopedic consult if fracture confirmed",
                "Ice, elevation, and immobilization"
            ],
            "time_sensitive": False
        }
    }

    matches = []
    for key, protocol in protocols_db.items():
        score = sum(1 for kw in protocol["keywords"] if kw in query_lower)
        if score > 0:
            matches.append((score, protocol))

    matches.sort(key=lambda x: x[0], reverse=True)
    top = [m[1] for m in matches[:top_k]]

    if not top:
        top = [protocols_db["trauma"]]  # default fallback

    return {"success": True, "protocols": top, "query": query}


def check_bed_availability(care_area: str) -> dict:
    """Check current bed availability for a given care area."""
    area = care_area.lower().replace(" ", "_")
    if area in MOCK_BEDS:
        bed = MOCK_BEDS[area]
        return {
            "success": True,
            "care_area": care_area,
            "total_beds": bed["total"],
            "available_beds": bed["available"],
            "status": "available" if bed["available"] > 0 else "full"
        }
    return {"success": False, "error": f"Unknown care area: {care_area}"}


def _decrement_bed(text: str) -> None:
    """Decrement available count for the care area mentioned in text."""
    text_lower = text.lower()
    area_keywords = {
        "trauma_bay": ["trauma bay", "trauma_bay"],
        "resus": ["resus", "resuscitation"],
        "fast_track": ["fast track", "fast_track"],
        "general": ["general"],
        "waiting": ["waiting"],
    }
    for area, keywords in area_keywords.items():
        if any(kw in text_lower for kw in keywords):
            if MOCK_BEDS[area]["available"] > 0:
                MOCK_BEDS[area]["available"] -= 1
            return


def generate_triage_report(
    patient_summary: str,
    vitals_findings: str,
    symptom_findings: str,
    protocol_findings: str,
    bed_recommendation: str,
    esi_score: int
) -> dict:
    """Compile all specialist findings into a structured triage report."""
    from config import ESI_LEVELS
    level_info = ESI_LEVELS.get(esi_score, ESI_LEVELS[3])
    _decrement_bed(bed_recommendation)
    return {
        "success": True,
        "report": {
            "esi_score": esi_score,
            "esi_label": level_info["label"],
            "esi_color": level_info["color"],
            "patient_summary": patient_summary,
            "vitals_findings": vitals_findings,
            "symptom_findings": symptom_findings,
            "protocol_findings": protocol_findings,
            "bed_recommendation": bed_recommendation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "generated_by": "TriageIQ Multi-Agent System"
        }
    }


# ─── Tool schemas ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_patient_data",
        "description": "Retrieve patient intake data from the EHR system by patient ID. Call this first to get vitals, chief complaint, and medical history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Patient ID (e.g. PT-001)"}
            },
            "required": ["patient_id"]
        }
    },
    {
        "name": "search_protocols",
        "description": "Search the clinical protocol library for relevant triage protocols based on the patient's symptoms and presentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query based on symptoms and clinical presentation"},
                "top_k": {"type": "integer", "description": "Number of protocols to return (default 3)", "default": 3}
            },
            "required": ["query"]
        }
    },
    {
        "name": "check_bed_availability",
        "description": "Check current bed availability for a specific care area in the ER.",
        "input_schema": {
            "type": "object",
            "properties": {
                "care_area": {
                    "type": "string",
                    "description": "Care area to check",
                    "enum": ["trauma_bay", "resus", "fast_track", "general", "waiting"]
                }
            },
            "required": ["care_area"]
        }
    },
    {
        "name": "generate_triage_report",
        "description": "Generate the final structured triage report. Call this LAST after all specialist findings are complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_summary": {"type": "string", "description": "Brief patient summary"},
                "vitals_findings": {"type": "string", "description": "Critical vitals findings and abnormalities"},
                "symptom_findings": {"type": "string", "description": "Symptom classification and red flags"},
                "protocol_findings": {"type": "string", "description": "Matched protocol and key interventions"},
                "bed_recommendation": {"type": "string", "description": "Care area assignment and resource needs"},
                "esi_score": {"type": "integer", "description": "ESI priority score 1-5 (1=immediate, 5=non-urgent)", "minimum": 1, "maximum": 5}
            },
            "required": ["patient_summary", "vitals_findings", "symptom_findings", "protocol_findings", "bed_recommendation", "esi_score"]
        }
    }
]

TOOL_MAP = {
    "get_patient_data": get_patient_data,
    "search_protocols": search_protocols,
    "check_bed_availability": check_bed_availability,
    "generate_triage_report": generate_triage_report,
}
