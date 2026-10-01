# TriageIQ Eval Harness

Status: **v2, three branches, runnable end-to-end.** 30 scenarios across 5
tiers; the production prompts and the structured report tool are imported,
not mirrored; errored runs are excluded and reported; a stopped run resumes.

## What this measures

*The A/B question: does a specialist pipeline classify ESI triage levels more safely than a
single Sonnet call that sees the same patient record?* Three branches:

| Branch | Shape | Calls per scenario |
|---|---|---|
| `full` | 4 Haiku specialists (Vitals, Symptom, Protocol, Bed) in parallel, then the Sonnet Synthesizer with the strict `generate_triage_report` tool | 5-6 |
| `lean` | Symptom specialist (Haiku) + Synthesizer. **The production default** since the June 2026 run | 2-3 |
| `stripped` | One Sonnet call, patient record inline as XML, JSON out | 1 |

Every branch sees the same tokenised, tagged record (`backend/sanitize.py`).

Scenarios (`eval/scenarios/`):

| Tier | Count | Purpose |
|---|---|---|
| `clear_esi_1_2` | 7 | True emergencies, should be ESI 1-2 |
| `clear_esi_4_5` | 6 | Clearly non-urgent, tests over-triage |
| `ambiguous` | 6 | Defensible to assign either of two adjacent levels |
| `critical_miss_test` | 5 | **The safety tier.** Atypical high-acuity presentations with benign-looking vitals |
| `adversarial` | 6 | Prompt injection in chief complaint and name, contradictory / missing vitals, long histories |

Metric families (`eval/scorers.py`, deterministic, no LLM judge): ESI strict
and ±1 accuracy, **critical-miss rate** (gold ≤ 2 predicted ≥ 3, the
load-bearing metric), over-triage rate on ESI 4-5, under- and over-triage on
any tier against the acceptable set, care-area accuracy, red-flag coverage,
structured-report validity, and per-branch cost / latency from the traces.

## Running

```
make eval-small          # 5 scenarios x 3 branches x 1 rep     ≈ $0.50
make eval                # 30 scenarios x 3 branches x 3 reps   ≈ $8, ~40 min
python -m eval.runners --mode full --resume --yes    # continue a run that stopped
make eval-report         # re-score + re-render eval/reports/latest_run.json
make baseline            # freeze a COMPLETE run as eval/baseline.json (regression gate)
make regression          # CI gate: critical-miss ceiling + strict-accuracy floor
```

A fatal API error (exhausted credits, bad key) aborts the run immediately; the
report's Completeness section says how many runs were scored, errored, or
never executed, and `--resume` reuses every successful run.

## What it does NOT measure

- EHR / protocol-RAG / bed-availability integration: tool calls are mocked.
- Differential-diagnosis accuracy: would need physician calibration.
- Paediatric / geriatric subspecialty thresholds: adult defaults.
- Real-world ED outcomes: scored against `RUBRIC.md`, not ground truth.

## Layout

```
eval/
├── RUBRIC.md           # committed before any scenario was labelled
├── schemas.py          # Scenario / ScenarioResult / BranchMetrics / ABLiftResult
├── instrumentation.py  # CallTrace + pricing (Sonnet 5, Haiku 4.5)
├── rubric_audit.py     # deterministic rubric; backend/fallback.py must agree (tested)
├── prompts.py          # imports backend/agents.py; owns only the STRIPPED prompt
├── runners.py          # full / lean / stripped + CLI (--branches, --reps, --resume, --rerender)
├── scorers.py          # metric families, errored runs excluded, lift per candidate branch
├── orchestrator.py     # fail-fast, checkpoint, resume, report renderer
├── regression_check.py # CI gate
├── scenarios/          # 30 gold scenarios
└── reports/            # run_YYYYMMDD_HHMMSS.md + latest_run.json
```
