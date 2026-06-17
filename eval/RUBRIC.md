# TriageIQ Eval Rubric

**Status:** Committed Day 1, BEFORE any scenarios are labeled.
**Authority:** Quotes `triageiq/config.py::ESI_LEVELS` and `triageiq/backend/agents.py` SYSTEM prompts verbatim where applicable, then encodes the rubric into deterministic rules in `eval/rubric_audit.py`.
**Discipline:** If a scenario's gold answer conflicts with this rubric, the rubric wins. Edit the rubric explicitly, with a justification in the commit message, then re-verify all scenarios against the new rules.

This rubric exists to answer the M9 zinger *"you wrote the scenarios AND the rubric — aren't you scoring against your own preferences?"* by making the rules public before the scoring artifacts are.

---

## §1 The 5-level ESI taxonomy

Quoting `config.py::ESI_LEVELS` and `agents.py::COORDINATOR_PROMPT`:

> *ESI scoring guide:*
> *- ESI 1: Requires immediate life-saving intervention (label: Immediate)*
> *- ESI 2: High-risk situation, should not wait (label: Emergent)*
> *- ESI 3: Stable but needs multiple resources (label: Urgent)*
> *- ESI 4: Stable, needs one resource (label: Less Urgent)*
> *- ESI 5: Stable, no resources needed (label: Non-Urgent)*

This is the canonical ESI v4 framework (Emergency Severity Index v4 handbook). The rubric encodes the standard ED triage decision tree:

**Rule 1 (ESI 1 — Immediate / life-saving intervention required).** Any of:
- Cardiac or respiratory arrest
- Severe respiratory distress with imminent airway failure (SpO2 < 88% on room air, accessory muscle use)
- Severe shock (SBP < 80, signs of hypoperfusion)
- GCS ≤ 8 with airway compromise
- Active uncontrolled bleeding with hemodynamic instability
- Major trauma with hypotension or altered mental status

**Rule 2 (ESI 2 — High-risk, cannot wait).** Any of:
- Time-critical condition where delay causes harm: ACS / STEMI / stroke FAST positive / sepsis triad / surgical abdomen / status epilepticus
- Severe pain (≥ 7/10) with concerning features
- Altered mental status without airway compromise
- High-risk vital signs: HR > 130 OR < 40, SBP < 90, RR > 30, SpO2 < 92%, temp > 39.5 or < 35
- Pediatric red flags: infant < 3 months with any fever, child with stridor / barking cough / labored breathing
- Any patient meeting a CRITICAL flag from the SYMPTOM_PROMPT red-flag list (see §3)

**Rule 3 (ESI 3 — Stable, multiple resources needed).** Stable vitals AND the workup needs ≥ 2 resources (CT + labs + IV fluids + consult, etc.). Examples: moderate abdominal pain requiring CT + labs, chest pain in low-risk demographic requiring ECG + troponin + observation, fever in adult requiring CBC + blood cultures + IV antibiotics.

**Rule 4 (ESI 4 — Stable, ONE resource needed).** Stable vitals AND a single resource resolves: simple suture, single-view X-ray for sprain, one prescription, one consult.

**Rule 5 (ESI 5 — Stable, NO resources needed).** Discharge with instructions; no labs, no imaging, no procedures. Examples: medication refill, suture removal, stable BP recheck, very minor cold without complications.

---

## §2 Vital-sign red flags (deterministic ESI elevation)

From `agents.py::VITALS_PROMPT`:

> *- BP: Normal 90-140/60-90 mmHg. <90 systolic = hypotension (CRITICAL)*
> *- HR: Normal 60-100 bpm. >100 = tachycardia, <60 = bradycardia*
> *- RR: Normal 12-20 breaths/min. >20 = tachypnea (concerning)*
> *- SpO2: Normal >95%. <94% = hypoxia (concerning), <90% = CRITICAL*
> *- Temp: Normal 36.1-37.2°C. >38.3°C = fever, <36°C = hypothermia*
> *- GCS: Normal 15. <14 = altered mental status (CRITICAL)*

Encoded in `eval/rubric_audit.py::vital_sign_severity`:

| Vital | Normal | Concerning (ESI ≤ 3) | Critical (ESI ≤ 2) |
|---|---|---|---|
| SBP | 90-140 | < 90 OR > 180 | < 80 OR > 220 |
| HR | 60-100 | > 100 OR < 60 | > 130 OR < 40 |
| RR | 12-20 | > 20 OR < 10 | > 30 OR < 8 |
| SpO2 | > 95% | 90-94% | < 90% |
| Temp (°C) | 36.1-37.2 | > 38.3 OR < 36 | > 39.5 OR < 35 |
| GCS | 15 | 13-14 | ≤ 12 |

**Pediatric adjustments.** Children have different normal ranges. For patients < 18 in the scenario, vital normal ranges shift (HR 80-130 for toddlers, etc.) — the rubric admits scenario-author judgment on pediatric vitals via the `acceptable_esi` set.

**Multi-vital aggregation.** ≥ 2 critical vitals → ESI 1-2 mandatory; 1 critical + 1 concerning → ESI 2; 1 critical alone → ESI 2; ≥ 2 concerning alone → ESI 3.

---

## §3 Red-flag symptoms (deterministic ESI elevation)

From `agents.py::SYMPTOM_PROMPT`:

> *Red-flag symptoms requiring immediate escalation:*
> *- Chest pain + diaphoresis + radiation = possible ACS*
> *- Worst headache of life = possible subarachnoid hemorrhage*
> *- Sudden facial droop / arm weakness / speech difficulty = possible stroke (FAST criteria)*
> *- Fever + hypotension + altered mental status = possible sepsis*
> *- Severe abdominal pain + rigid abdomen = possible surgical emergency*
> *- Respiratory distress with accessory muscle use = airway emergency*

These map to ESI 1 or 2 mandatorily. The `expected_critical_flags` field on a scenario lists the keyword(s) the agent should mention in its critical_flags output.

| Red flag | Canonical ESI | Critical-flag keyword |
|---|---|---|
| ACS / STEMI / cardiac chest pain | 1-2 | `acs`, `stemi`, `cardiac` |
| Subarachnoid hemorrhage / worst headache | 1-2 | `sah`, `subarachnoid`, `stroke` |
| Stroke FAST positive | 1-2 | `stroke`, `cva`, `fast` |
| Sepsis triad | 1 (if hypotensive) or 2 | `sepsis` |
| Surgical abdomen | 2 | `surgical_abdomen`, `peritonitis` |
| Airway emergency | 1 | `airway`, `respiratory_failure` |
| Anaphylaxis | 1 | `anaphylaxis` |
| Major trauma | 1 (if hypotensive) or 2 | `trauma`, `polytrauma` |

---

## §4 Care-area mapping

From `agents.py::BED_PROMPT`:

> *- Trauma bay: Life-threatening emergency requiring immediate intervention*
> *- Resus: Critical but not immediately life-threatening; close monitoring needed*
> *- Fast track: Moderate acuity; can wait briefly but needs timely care*
> *- General: Lower acuity; stable patient*
> *- Waiting: Non-urgent; stable with minor complaint*

Canonical mapping (encoded in `eval/rubric_audit.py::care_area_for`):

| ESI | Canonical care areas |
|---|---|
| 1 | `trauma_bay`, `resus` |
| 2 | `resus`, `trauma_bay` |
| 3 | `fast_track`, `general` |
| 4 | `general`, `fast_track` |
| 5 | `waiting`, `general` |

Adjacent care areas are acceptable. ESI 2 → `fast_track` is NOT acceptable (under-resourced); ESI 4 → `resus` is NOT acceptable (over-resourced); both score as care-area misses.

---

## §5 What this rubric does NOT cover (admitted edges)

This rubric is deliberately silent on:

1. **Differential diagnosis accuracy.** The agent emits a `patient_summary` and the synthesizer reasons about the likely diagnosis. Whether the agent's diagnostic hypothesis is correct is OUT of scope for this eval — that would require attorney/physician calibration. The eval scores ESI assignment + care-area, NOT diagnostic accuracy.
2. **Pediatric / geriatric subspecialty calibration.** Pediatric and geriatric atypical presentations are admitted via `acceptable_esi` sets per scenario. The rubric does NOT encode dedicated subspecialty thresholds (would need pediatric ED triage tables).
3. **Resource-availability dynamics.** Production has `check_bed_availability` that returns real bed counts; bed scarcity could legitimately drive a 3 → 4 reclassification. The eval mocks bed availability to a fixed snapshot so the LLM's clinical reasoning is isolated from system-state mutations.
4. **Cost-quality tradeoffs.** The rubric does not penalize HIGH compute for low-acuity scenarios (overtriage is bad clinically; specialist parallelism on stable patients is not penalized as resource waste in scoring).
5. **The `critical_miss_test` tier admits judgment.** Atypical presentations (silent MI, posterior stroke) have an expected ESI per the rubric BUT the `acceptable_esi` set may admit 2-3 reflecting that even experienced clinicians sometimes call these wrong. The tier exists specifically to test the gray zone; scoring weighs critical-miss-rate over strict-accuracy.

---

## §6 Programmatic encoding (`eval/rubric_audit.py`)

The deterministic subset of this rubric is encoded as Python functions:

```python
def vital_sign_severity(vitals: dict) -> str:
    """Return one of 'critical', 'concerning', or 'normal'."""

def red_flag_keywords(chief_complaint: str) -> list[str]:
    """Return list of canonical red-flag keys matched in chief_complaint."""

def rubric_canonical_esi(patient: Patient) -> tuple[int, list[int]]:
    """Return (canonical_esi, acceptable_esi_set) for a patient.
    Applies Rules 1-5 + vital-sign aggregation + red-flag elevation."""

def care_area_for(esi: int) -> list[str]:
    """Return acceptable care areas for an ESI score."""
```

The scenario tests `tests/test_scenarios.py` call `rubric_canonical_esi()` on every scenario's patient and verify the scenario's gold answer is consistent with the rubric. If a scenario's gold says ESI 4 but the rubric says ESI 2 (because the patient has SpO2 < 90%), the test FAILS and forces me to either fix the scenario or update the rubric explicitly.

**Critical-miss-test tier exception.** The `critical_miss_test` tier scenarios deliberately TRIP UP the deterministic rubric — atypical presentations with apparently-benign surface signs. Those scenarios use the `is_critical_miss_test` flag and the test_scenarios.py rubric-self-check applies relaxed checks (the gold ESI must be 1-2 because the underlying condition is life-threatening, even if the surface vitals don't trip the deterministic vital-sign rules).

---

## §7 Rubric change log

| Date | Change | Justification |
|---|---|---|
| 2026-06-16 | Initial commit | Day 1 scaffold; mirrors production agents.py system prompts + ESI v4 handbook |

Any future change to this rubric must add a row here AND have a commit message referencing the row, AND must be followed by a rerun of `test_scenarios.py` to verify all scenarios still pass.

---

*This rubric is the contract between the scenarios and the scorers. The scenarios encode what ESI patients should receive; the scorers measure how well the agent classifies them; the rubric is what both refer back to. If you disagree with the eval, this is the artifact to argue against — not the scenarios, not the scores.*
