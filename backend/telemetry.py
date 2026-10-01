"""Per-run tracing, cost accounting, ceilings and the hash-chained audit log."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PRICING: dict[str, dict[str, float]] = {  # USD per million tokens
    "claude-sonnet-5": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
    "claude-opus-5": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
}
_FALLBACK = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}


def price(model: str) -> dict[str, float]:
    for key, val in PRICING.items():
        if model.startswith(key):
            return val
    return _FALLBACK


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float:
    p = price(model)
    return (input_tokens * p["input"] + output_tokens * p["output"]
            + cache_write * p["cache_write"] + cache_read * p["cache_read"]) / 1_000_000


@dataclass
class Span:
    name: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    duration_s: float = 0.0
    cost_usd: float = 0.0


class CeilingExceeded(RuntimeError):
    pass


@dataclass
class Trace:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)
    spans: list[Span] = field(default_factory=list)
    parse_failures: int = 0
    pipeline: str = ""
    degraded: bool = False
    token_ceiling: int = 0
    call_ceiling: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_call(self, name: str, model: str, response, duration_s: float) -> Span:
        u = getattr(response, "usage", None)
        span = Span(
            name=name, model=model,
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            duration_s=duration_s,
        )
        span.cost_usd = cost_usd(model, span.input_tokens, span.output_tokens, span.cache_write_tokens, span.cache_read_tokens)
        with self._lock:
            self.spans.append(span)
        self.check_ceilings()
        return span

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens + s.cache_write_tokens + s.cache_read_tokens for s in self.spans)

    def check_ceilings(self) -> None:
        if self.token_ceiling and self.total_tokens > self.token_ceiling:
            raise CeilingExceeded(f"token ceiling {self.token_ceiling:,} exceeded ({self.total_tokens:,} used)")
        if self.call_ceiling and len(self.spans) > self.call_ceiling:
            raise CeilingExceeded(f"call ceiling {self.call_ceiling} exceeded ({len(self.spans)} calls)")

    def summary(self) -> dict:
        by_stage: dict[str, dict] = {}
        for s in self.spans:
            b = by_stage.setdefault(s.name, {"calls": 0, "cost_usd": 0.0, "latency_s": 0.0})
            b["calls"] += 1
            b["cost_usd"] = round(b["cost_usd"] + s.cost_usd, 5)
            b["latency_s"] = round(b["latency_s"] + s.duration_s, 2)
        return {
            "request_id": self.request_id,
            "pipeline": self.pipeline,
            "llm_calls": len(self.spans),
            "input_tokens": sum(s.input_tokens for s in self.spans),
            "output_tokens": sum(s.output_tokens for s in self.spans),
            "cache_write_tokens": sum(s.cache_write_tokens for s in self.spans),
            "cache_read_tokens": sum(s.cache_read_tokens for s in self.spans),
            "cost_usd": round(sum(s.cost_usd for s in self.spans), 5),
            "llm_latency_s": round(sum(s.duration_s for s in self.spans), 2),
            "wall_time_s": round(time.time() - self.started_at, 2),
            "parse_failures": self.parse_failures,
            "degraded": self.degraded,
            "calls_by_stage": by_stage,
        }


# ---------------------------------------------------------------- audit log

_LOCK = threading.Lock()


def _record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _last_hash(path: Path) -> str:
    last = "0" * 64
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        last = json.loads(line).get("hash", last)
                    except json.JSONDecodeError:
                        pass
    return last


def append_audit(record: dict, path: str | None = None) -> dict:
    p = Path(path or os.getenv("TRIAGEIQ_AUDIT_LOG", "logs/audit.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        rec = dict(record)
        rec["prev_hash"] = _last_hash(p)
        rec["ts"] = time.time()
        rec["hash"] = _record_hash(rec)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    return rec


def verify_audit(path: str | None = None) -> tuple[bool, int, int | None]:
    p = Path(path or os.getenv("TRIAGEIQ_AUDIT_LOG", "logs/audit.jsonl"))
    if not p.exists():
        return True, 0, None
    prev, n = "0" * 64, 0
    with p.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_hash") != prev or _record_hash(rec) != rec.get("hash"):
                return False, n, i
            prev = rec["hash"]
            n += 1
    return True, n, None
