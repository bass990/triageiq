# TriageIQ, ER Multi-Agent Decision Support

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](./requirements.txt)
[![Models: Haiku 4.5 + Sonnet 4.6](https://img.shields.io/badge/Models-Haiku%204.5%20%2B%20Sonnet%204.6-orange.svg)](./config.py)

A five-agent emergency department triage decision-support system. Three specialists (Vitals · Symptoms · Protocol) run in parallel on Claude Haiku for cost and latency; a Coordinator and a Synthesizer on Claude Sonnet glue them together into an ESI 1-5 classification with a specific action checklist. Built as a FastAPI + React app with SSE streaming so the nurse sees specialist reasoning as it arrives.

> **Decision-support tool, the nurse makes the final call.** Mock patient data only, not clinically validated, not for use with real patients. See [Honest disclosure](#honest-disclosure).

---

## The clinical-safety frame (read this first)

TriageIQ is **decision support**, not diagnosis. The intended user is a triage nurse at the front of an ED waiting room who needs to assign an Emergency Severity Index (ESI 1 immediate → ESI 5 non-urgent) to incoming patients in seconds, while juggling a queue of others.

Three architectural commitments hold the clinical-safety line:

1. **The nurse retains decision authority.** The UI presents the agent's ESI as a recommendation alongside the specialist reasoning. There is no auto-routing, no auto-summoning of physicians, no auto-anything. The recommendation lands; the nurse acts (or doesn't).
2. **Specialist reasoning is shown, not hidden.** Vitals, Symptoms, and Protocol agents each output their findings as separate tabs. A nurse who disagrees with the synthesized ESI can read each specialist's input and identify where the chain broke down.
3. **The framing is consistent throughout.** Demo script, system prompts, UI copy, and presentation deck all say "decision-support tool"; none of them say "AI diagnoses the patient." This is not a marketing choice, it determines what the system can and cannot ethically do.

If a real ED were to use this, it would be a **fourth opinion** alongside the triage nurse's intuition, the established ESI handbook, and the protocol KB. Never a replacement.

---

## What this proves (AI Engineer signals)

| Signal | Where it shows up |
|---|---|
| **Five distinct specialist agents with clear job boundaries** | Coordinator (orchestration) · Vitals Analyzer (BP, HR, RR, SpO2, temp, GCS) · Symptom Classifier (chief complaint → red flags) · Protocol Matcher (KB lookup) · Synthesizer (ESI 1-5 + action plan). Plus a small bed-availability sub-specialist invoked from the Coordinator's workflow (6 system prompts total in [`backend/agents.py`](./backend/agents.py); orchestration in [`backend/orchestrator.py`](./backend/orchestrator.py)). |
| **Tiered model strategy** | Specialists run on **claude-haiku-4-5** (fast, cheap, accurate enough for narrow tasks). Coordinator and Synthesizer run on **claude-sonnet-4-6** (heavier reasoning for routing and final classification). Surfaces a real cost/accuracy tradeoff rather than blanket-applying the largest model. See [`config.py`](./config.py). |
| **Parallel specialist execution** | Vitals, Symptoms, and Protocol have no inter-dependency, they read the same patient record and produce independent findings. The orchestrator runs them concurrently and synthesizes. |
| **Streaming per-agent output** | SSE on `/triage/stream/{patient_id}` emits one event per specialist completion, so the React UI lights up its agent cards as findings arrive instead of blocking on the slowest. |
| **Decision-support framing as a first-class architectural commitment** | "Decision support, nurse makes final call" appears in the system prompts, the UI copy, the demo script, and the LICENSE. Architectural choices (separate specialist tabs, no auto-routing) reinforce the framing. |
| **Standard clinical taxonomy** | ESI 1-5 (Immediate · Emergent · Urgent · Less Urgent · Non-Urgent) is the U.S. ED standard. The system is not inventing a custom severity scale; it's classifying into an existing one. |

---

## System at a glance

```
            ┌─────────────────────────────────┐
            │  Patient record (vitals + cc)   │
            │  PT-001: 65M, chest pain,       │
            │  BP 88/60, HR 110, SpO2 92      │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │       Coordinator (Sonnet)      │
            │  validates input, dispatches    │
            └────────────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼          (parallel)
       ┌────────────┐ ┌────────────┐ ┌────────────┐
       │  Vitals    │ │  Symptom   │ │  Protocol  │
       │  Analyzer  │ │  Classifier│ │  Matcher   │
       │  (Haiku)   │ │  (Haiku)   │ │  (Haiku)   │
       └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
             │              │              │
             └──────────────┼──────────────┘
                            ▼
            ┌─────────────────────────────────┐
            │   Synthesizer (Sonnet)          │
            │   → ESI level (1-5)             │
            │   → Action checklist            │
            │   → Resource requirements       │
            │   → Red flags / safety notes    │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │  Nurse-facing dashboard         │
            │  ESI pill · specialist tabs ·   │
            │  action checklist · red flags   │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │  Nurse reviews and decides.     │
            │  System never auto-routes.      │
            └─────────────────────────────────┘
```

---

## Honest disclosure

Things this system is NOT, that an interviewer should know up front:

1. **Eval-validated A/B: SAFE on critical-miss rate, but does NOT earn its complexity on accuracy.** A 30-scenario eval harness across 5 tiers (clear_esi_1_2, clear_esi_4_5, ambiguous, critical_miss_test, adversarial including prompt injection in chief-complaint AND patient-name fields) ran 630 LLM calls across 180 scored runs (30 scenarios × 2 branches × 3 reps, $6.07, **0 errors**, ~117 min). See `eval/` for the full methodology + RUBRIC.md (committed before any scenarios were labeled).

   **Safety headline (the load-bearing metric):** **critical-miss rate is 0% on BOTH branches.** Neither the 4-specialist tiered architecture nor a single-Sonnet baseline missed a single high-acuity patient across 13 high-acuity scenarios × 3 reps = 39 chances to under-triage. **Including all 5 atypical critical_miss_test scenarios** (silent MI in elderly diabetic, posterior stroke without FAST, occult geriatric sepsis without fever, PE with normal SpO2, slow-leak AAA with stable vitals) AND **all 6 adversarial scenarios** (prompt injection in chief complaint, prompt injection in patient name, anaphylaxis post-EpiPen with normal vitals, cardiac arrest with missing vitals, long-vague-with-buried-critical, demographic-spoofing opener). Both branches resisted every adversarial test 3/3 reps each.

   **But per-metric breakdown reveals the architectures are NOT interchangeable:**

   | Metric | FULL | STRIPPED | Lift | Read |
   |---|---|---|---|---|
   | Critical-miss rate (lower=better) | 0.0% | 0.0% | 0pp | Tied — both safe on the load-bearing metric |
   | ESI strict accuracy | 97.8% | 100.0% | **-2.2pp** | Equivalent; FULL's single deficit is ambiguous_006 (anxious chest tightness) where it over-triaged ESI 2 vs rubric-accepted ESI 3-4 |
   | ESI ±1 lenient | 100.0% | 100.0% | 0pp | Tied |
   | Overtriage rate (lower=better) | 0.0% | 0.0% | 0pp | Tied |
   | Care-area accuracy | 74.2% | 94.4% | **-20.2pp** | **STRIPPED wins significantly.** The specialist Bed Allocator often conflicts with the Synthesizer's care-area choice; the single-Sonnet baseline assigns beds more consistently |
   | Critical-flag coverage | 87.5% | 74.8% | **+12.7pp** | **FULL wins.** The specialist SYMPTOM_PROMPT explicitly enumerates red flags; richer documentation in the audit trail |

   **Architectural recommendation surfaced by the eval:** the 4-specialist pipeline is SAFE but does not earn its complexity on accuracy. STRIPPED is the better production choice for ESI classification + care-area assignment. FULL's only measurable advantage is +12.7pp critical-flag enumeration, which has audit/documentation value but no accuracy benefit. **For a production rewrite, collapse to a single Sonnet call unless the richer red-flag documentation in the audit trail is a hard requirement** (in which case keep the Symptom specialist only, drop Vitals + Protocol + Bed + Coordinator).

   This is a useful negative result. Building the specialist architecture taught the design but the eval showed a single Sonnet call has the same safety profile at 1/5 the LLM calls.

   Reproduce: `make eval` from `triageiq/` with `ANTHROPIC_API_KEY` set.

2. **Still not clinically validated.** The eval scores against the published rubric (RUBRIC.md), NOT against ground-truth ED outcomes. Use the eval result for architectural decisions, not for clinical claims. Real deployment would require IRB approval, partner-hospital validation, and inter-rater reliability work with trained ED nurses.
3. **Mock patient data only.** All patients live in scenario JSONs and a local store. No real PHI, no HIPAA-compliant storage, no consent flow. The architecture is shaped *as if* the input came from EHR integration; the integration is not built.
4. **Tiered-model cost/accuracy tradeoff is NOW measured.** The eval confirms Haiku specialists produce findings the Sonnet Synthesizer can use without safety degradation — but also that the Synthesizer-alone (STRIPPED) does just as well without them on the safety metric. The original assumption "tiering saves cost without quality loss" is technically vindicated AND the architecture is shown to be unnecessary at the same time.
5. **Single language, single-protocol set.** English chief complaints only. The protocol KB is a small hardcoded set; real EDs have hundreds of protocols indexed by chief complaint × age × comorbidity.
6. **No clinical-liability framework.** A real ED deployment would need an institutional review (IRB), insurance coverage for the decision-support tool, EHR-vendor partnership, and a careful agreement with the medical staff that defines exactly what the tool's recommendations mean inside their workflow. None of these exist; pretending the code is what matters here would miss the point.
7. **No live deploy.** Standing this up publicly would require API keys, and exposing a triage simulator on the open internet has its own risks (people typing real symptoms expecting real advice). Local-only is deliberate.

---

## What I'd want before this saw a real patient

In order, from "this is what I'd do tomorrow" to "this is what would take a year":

1. **Gold-case eval harness.** 50 patient scenarios scored by 2-3 trained ED nurses with consensus ESI labels. Measure system ESI agreement per level. Critical: track overtriage (system says ESI 1, nurse says ESI 3) vs undertriage (system says ESI 4, nurse says ESI 2) separately, they have very different clinical-safety implications, and a system that errs toward overtriage is safer than one that errs toward undertriage.
2. **Specialist ablation on Haiku.** Run Vitals/Symptom/Protocol on Sonnet for the same eval set. If Haiku specialist quality is statistically indistinguishable from Sonnet, the tiered architecture is validated; if not, the cost savings aren't worth it.
3. **Red-flag explicit list per specialist.** "Vitals analyzer found *high-risk vitals signs*" is too vague. The output should be "BP 88/60 is hypotensive in the setting of chest pain; reflex toward MI / dissection / sepsis differential." Forces the system to commit to the reasoning that drove the ESI bump.
4. **EHR integration as a real interface.** FHIR R4 read endpoints for patient vitals and prior visits. Even read-only changes the threat model, now PHI flows through the API.
5. **Institutional review process.** Internal IRB, medical-staff agreement on what the tool's outputs mean, malpractice coverage that contemplates AI decision-support, training materials for the nursing staff.
6. **Continuous monitoring of overtriage/undertriage rates.** Per-shift, per-hour-of-day, per-chief-complaint. A drift in undertriage rate is a clinical-safety alarm.
7. **Failover for the LLM provider.** A locally-cached protocol KB with rule-based ESI fallback when the API is unavailable, so a network blip doesn't pause triage.

---

## Failure modes

- **Undertriage on atypical presentations.** Younger patients with MI-equivalent symptoms or older patients with vague complaints can both produce ESI 4 when ESI 2-3 is correct. The system is pattern-matching against the chief-complaint vocabulary it has seen; rare presentations are out of distribution.
- **Vitals look fine in isolation but matter in combination.** BP 100/60 alone is borderline normal; BP 100/60 + HR 130 + altered mental status is shock physiology. The Synthesizer should catch the combination; whether it reliably does is the eval question.
- **Protocol KB has gaps.** A chief complaint with no matching protocol gets a generic "consult protocol library" message. In a real ED this is dangerous, the absence of a protocol does not mean the absence of urgency.
- **Single-language input.** A non-English chief complaint either fails to classify or gets misclassified by translation noise. English-only is a hard constraint of the current system.
- **The "Coordinator may dispatch fewer specialists" failure.** The Coordinator decides which specialists to invoke. If it decides the patient doesn't need a Vitals analysis, the Vitals analysis doesn't happen, and the Synthesizer may miss a critical vitals finding it never had.

---

## Quick start

```bash
# 1. API key
cp .env.example .env
# Edit .env: ANTHROPIC_API_KEY=sk-ant-...

# 2. Backend (Terminal 1)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8001

# 3. Frontend (Terminal 2)
cd frontend
npm install && npm run dev
# → http://localhost:3001
```

### Demo flow

1. Select **PT-001** (65M, chest pain, BP 88/60), designed for maximum impact.
2. Click **Run Triage**, agent grid activates, watch 4 specialists run.
3. ESI Level appears, walk through action checklist.
4. Click each tab: Vitals / Symptoms / Protocol / Resources.

**Free-text mode:** Switch to the "Free Text Input" tab and type any patient description.

---

## Repo structure

```
triageiq/
├── README.md                    ← you are here (portfolio front door)
├── LICENSE                      ← MIT + decision-support / not-medical-advice disclaimer
├── config.py                    ← MODEL_FAST (Haiku) · MODEL_SMART (Sonnet) · ESI_LEVELS
├── requirements.txt
├── .env                         ← Your API key (gitignored)
├── .env.example                 ← Template for the API key
├── backend/
│   ├── tools.py                 ← 4 tool functions + schemas
│   ├── agents.py                ← 5 system prompts
│   ├── orchestrator.py          ← Coordinator + Synthesizer (Sonnet)
│   └── main.py                  ← FastAPI: /patients + /triage/stream/{id}
└── frontend/
    └── src/
        ├── App.jsx              ← clinical dashboard, specialist tabs, ESI pill
        └── App.css              ← IBM Plex design system
```

---

## License

[MIT](./LICENSE) for the code. The patient records, vital signs, chief complaints, and protocols in this repository are fabricated for demonstration. Not medical advice. Not for use with real patients without institutional review, clinical validation, and a defined decision-support framework agreed with medical staff.

---

## Author

Mamadou Bassirou Diallo · MS Business Analytics & AI, UT Dallas · [LinkedIn](https://www.linkedin.com/in/mamadou9905) · [GitHub](https://github.com/bass990)

