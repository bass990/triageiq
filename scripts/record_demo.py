"""Record one real triage run as an SSE replay file for demo mode.

    python scripts/record_demo.py [--patient PT-001] [--out demo/sample_trace.json]
    python scripts/record_demo.py --all          # one trace per demo patient -> demo/traces/<id>.json

Runs the production pipeline once per patient (about $0.04 each on the lean
pipeline) and writes every event the API would have streamed, so a public
instance started with TRIAGEIQ_DEMO=1 replays the run for the patient that was
actually clicked, without an API key or any model calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from backend.orchestrator import run_triage_agent  # noqa: E402
from backend.tools import MOCK_PATIENTS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default="PT-001")
    ap.add_argument("--out", default=str(ROOT / "demo" / "sample_trace.json"))
    ap.add_argument("--all", action="store_true", help="record every demo patient into demo/traces/")
    args = ap.parse_args()
    config.require_api_key()
    if args.all:
        total = 0.0
        for pid in sorted(MOCK_PATIENTS):
            rc = record(pid, ROOT / "demo" / "traces" / f"{pid}.json")
            if rc:
                return rc
            total += LAST_COST["usd"]
        print(f"\nrecorded {len(MOCK_PATIENTS)} patients · total ${total:.4f}")
        return 0
    return record(args.patient, Path(args.out))


LAST_COST = {"usd": 0.0}


def record(patient: str, out: Path) -> int:
    print(f"\n== {patient} -> {out.name}")

    events: list[dict] = [{"event": "status", "step": 0, "total": 7, "message": "Starting triage...", "pipeline": config.PIPELINE}]
    agent_of = {1: "coordinator", 2: "vitals", 3: "symptoms", 4: "protocols", 5: "beds", 6: "synthesizer", 7: "done"}

    def on_progress(step, message, meta=None):
        events.append({"event": "status", "step": step, "total": 7, "message": message, "agent": agent_of.get(step, ""), **(meta or {})})
        print(f"  [{step}] {message}")

    result = run_triage_agent(patient, on_progress=on_progress)
    if not result.get("success"):
        print("pipeline failed:", result.get("error"))
        return 1
    rep = result["report"]
    events.append({"event": "trace", **rep.get("trace", {})})
    events.append({"event": "status", "step": 7, "total": 7, "message": "Done!"})
    events.append({"event": "complete", "report": rep})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(), "model": config.MODEL_SMART,
                               "pipeline": config.PIPELINE, "patient": patient, "events": events}, indent=2, default=str),
                   encoding="utf-8")
    t = rep["trace"]
    LAST_COST["usd"] = float(t.get("cost_usd") or 0)
    print(f"wrote {out} · {len(events)} events · ESI {rep['esi_score']} ({rep['care_area']}) · {t['llm_calls']} calls · ${t['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
