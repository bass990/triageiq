# TriageIQ Eval Harness

Status: **Day 1 — scaffold + RUBRIC.md committed**. Scenarios, runners, and
scorers land over Days 2-8.

## What this measures

The headline question: *does TriageIQ's 4-specialist tiered-model architecture
(4 Haiku specialists in parallel → Sonnet synthesizer) classify ESI triage
levels better than a single Sonnet call that sees the same patient context
inline?*

The eval runs an A/B between:

- **FULL branch** — production pipeline (Vitals + Symptom + Protocol + Bed
  specialists in parallel via ThreadPoolExecutor, all on Haiku; then
  Synthesizer on Sonnet). 5 LLM calls per scenario.
- **STRIPPED branch** — one Sonnet call with the patient record inline as
  XML in the user message. 1 LLM call per scenario.

...against 30 gold scenarios across 5 tiers:

| Tier | Count | Purpose |
|---|---|---|
| `clear_esi_1_2` | 7 | True emergencies — should be ESI 1 or 2 with high precision |
| `clear_esi_4_5` | 6 | Clearly non-urgent — should be ESI 4 or 5; tests overtriage rate |
| `ambiguous` | 6 | Defensible to assign ESI 2 OR 3, or 3 OR 4 — judgment calls |
| `critical_miss_test` | 5 | **THE SAFETY-CRITICAL TIER.** Atypical presentations of high-acuity conditions where surface signs look benign. Underclassification = patient harm |
| `adversarial` | 6 | Prompt injection in chief complaint and name, contradictory vitals, missing vitals, very long history |

...and scores five metric families: ESI strict accuracy, ESI ±1 lenient
accuracy, critical-miss rate (the safety metric), overtriage rate (the
resource metric), and care-area assignment accuracy.

## What it does NOT measure

- EHR / protocol-RAG / bed-availability integration — tool calls are mocked.
- Differential diagnosis accuracy — would need attorney/physician calibration.
- Pediatric / geriatric subspecialty calibration — uses adult-default thresholds.
- Real-world ED outcomes — scored against the published rubric, not ground truth.

See `RUBRIC.md` for the ESI scoring rules + red-flag taxonomy + care-area
mapping. See `../phase2/18_triageiq_eval_scope_spec.md` for the full design
rationale.

## Layout

```
eval/
├── README.md           # this file
├── RUBRIC.md           # committed Day 1, BEFORE scenarios
├── schemas.py          # Pydantic models — the contract between scenarios,
│                       # runners, and scorers
├── instrumentation.py  # CallTrace + cost arithmetic (Haiku + Sonnet pricing)
├── rubric_audit.py     # programmatic encoding of RUBRIC.md §1-4
├── prompts.py          # mirrored production prompts + STRIPPED prompt
├── runners.py          # FULL + STRIPPED pipeline executors + CLI
├── scorers.py          # 5 scoring functions + aggregation + A/B lift
├── orchestrator.py     # Cartesian product runner + report renderer
├── scenarios/          # one *.json per scenario; lands Days 2-5
└── reports/            # one run_YYYYMMDD_HHMMSS.md per eval run
```

## Running the eval

Day 1 scaffold: nothing runs yet — `make eval-dry` prints the Day-1 status.

Once Day 8 lands:

```
make eval-small   # 5 scenarios, 2 branches, 1 rep    ≈ $0.50-$1
make eval         # 30 scenarios, 2 branches, 3 reps  ≈ $5-10
```

Both need `ANTHROPIC_API_KEY` in env. Reports land in `eval/reports/`.

## Honest disclosures

1. **The eval bypasses EHR / protocol / bed tool calls.** Mocked. Testing
   integration requires a separate harness.
2. **Synthetic patient records, not real ED cases.** The `critical_miss_test`
   tier is sourced from standard ED triage references but the specific
   cases are author-constructed.
3. **The rubric encodes the production system prompts.** If `backend/agents.py`
   SYNTHESIZER_PROMPT changes, the rubric must be re-verified — a
   drift-detection test in `tests/test_runners.py` will catch silent skew.
4. **Acceptable-ESI sets admit author judgment.** The `acceptable_esi` list
   per scenario is the rubric's degree of freedom.
5. **No LLM-as-judge.** Scorers are deterministic. Trades human-rater-agreement
   upside for zero same-model bias.
6. **Not clinically validated.** Eval scores against the published rubric,
   not against ground-truth ED outcomes. Use the result for architectural
   decisions, not for clinical claims.
7. **Cost estimates assume Sonnet 4.6 + Haiku 4.5 pricing.** Verify before
   each full run.

## CI strategy

- `ci.yml` runs ruff + pytest on every push/PR. Zero LLM calls. <1 min, free.
- `eval.yml` is manual-trigger only (`workflow_dispatch`). Needs the
  `ANTHROPIC_API_KEY` GitHub secret. Uploads the markdown report as an
  artifact. Cost: $5-10.
