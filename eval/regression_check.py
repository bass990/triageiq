"""Regression gate for CI.

Compares eval/reports/latest_run.json against eval/baseline.json. The gate
is asymmetric on purpose: the critical-miss rate has a hard CEILING (any
increase above baseline + tolerance fails), ESI strict accuracy has a floor.
Floors are frozen from a complete run; the tolerance sits above the measured
run-to-run band so noise does not fail the build. A partial snapshot (errored
runs) can never become a baseline.

    python -m eval.regression_check
    python -m eval.regression_check --write-baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPORTS = Path(__file__).parent / "reports"
BASELINE = Path(__file__).parent / "baseline.json"
DEFAULT_TOLERANCE = 0.10


def _metrics(snapshot: dict) -> dict[str, dict]:
    return {m["branch"]: m for m in snapshot.get("branch_metrics", [])}


def check(latest: dict[str, dict], baseline: dict) -> list[str]:
    tol = float(baseline.get("tolerance", DEFAULT_TOLERANCE))
    failures = []
    for branch, floor in baseline.get("branches", {}).items():
        m = latest.get(branch)
        if not m:
            continue
        ceiling = float(floor["critical_miss_rate"]) + tol / 2  # safety metric: tighter band
        if m["critical_miss_rate"] > ceiling:
            failures.append(f"{branch}: critical-miss rate {m['critical_miss_rate']:.3f} > ceiling {ceiling:.3f}")
        f = float(floor["esi_strict_acc"]) - tol
        if m["esi_strict_acc"] < f:
            failures.append(f"{branch}: ESI strict accuracy {m['esi_strict_acc']:.3f} < floor {f:.3f}")
    return failures


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshot", type=Path, default=REPORTS / "latest_run.json")
    p.add_argument("--baseline", type=Path, default=BASELINE)
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = p.parse_args(argv)
    if not args.snapshot.exists():
        print("no snapshot; nothing to check")
        return 0
    snap = json.loads(args.snapshot.read_text(encoding="utf-8"))
    latest = _metrics(snap)
    if args.write_baseline:
        transient = [r for r in snap.get("results", []) if r.get("error") and any(
            k in r["error"].lower() for k in ("connection", "timeout", "credit balance", "authentication", "rate limit", "overloaded"))]
        if transient or snap.get("aborted"):
            print(f"refusing to write a baseline: {len(transient)} infrastructure error(s) or an aborted run; finish it with --resume first")
            return 1
        model_failures = [r for r in snap.get("results", []) if r.get("error")]
        if model_failures:
            print(f"note: {len(model_failures)} run(s) failed inside the model output (kept out of the metrics, listed in the report)")
        data = {"tolerance": args.tolerance, "model_synthesizer": snap.get("model_synthesizer"), "branches": {
            b: {"critical_miss_rate": round(m["critical_miss_rate"], 4), "esi_strict_acc": round(m["esi_strict_acc"], 4)}
            for b, m in latest.items()}}
        args.baseline.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print("baseline written:", json.dumps(data["branches"]))
        return 0
    if not args.baseline.exists():
        print("no baseline.json; run --write-baseline after a complete full eval")
        return 0
    failures = check(latest, json.loads(args.baseline.read_text(encoding="utf-8")))
    for b, m in latest.items():
        print(f"{b:10s} critical-miss {m['critical_miss_rate']:.3f}  strict {m['esi_strict_acc']:.3f}")
    if failures:
        print("REGRESSION:")
        for f in failures:
            print("  -", f)
        return 1
    print("regression check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
