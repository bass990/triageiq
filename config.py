"""TriageIQ runtime configuration.

Every value can be overridden with an environment variable so the same code
runs the live pipeline, the eval harness, the tests (no key needed) and the
Docker demo replay. The API key is only required when a model call is
actually made (see `require_api_key`).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- models -----------------------------------------------------------------
MODEL_FAST = os.getenv("TRIAGEIQ_MODEL_FAST", "claude-haiku-4-5-20251001")   # specialists
MODEL_SMART = os.getenv("TRIAGEIQ_MODEL", "claude-sonnet-5")                 # synthesizer
MODEL = MODEL_SMART  # legacy alias
MAX_TOKENS = int(os.getenv("TRIAGEIQ_MAX_TOKENS", "4096"))
SPECIALIST_MAX_TOKENS = int(os.getenv("TRIAGEIQ_SPECIALIST_MAX_TOKENS", "1024"))

# ---- pipeline shape (eval-driven) -------------------------------------------
# "lean" = Symptom specialist (Haiku) + Synthesizer (Sonnet): the June 2026 eval
# found the 4-specialist pipeline no safer than a single Sonnet call, 20pp worse
# on care-area, and only better on red-flag documentation, which the Symptom
# specialist alone provides. "full" keeps Vitals + Protocol + Bed specialists.
PIPELINE = os.getenv("TRIAGEIQ_PIPELINE", "lean").strip().lower()
if PIPELINE not in ("lean", "full"):
    PIPELINE = "lean"

# ---- safety / robustness ----------------------------------------------------
FALLBACK_ENABLED = _flag("TRIAGEIQ_FALLBACK", True)        # rule-based ESI when the model API is unavailable
SANITIZE_INPUTS = _flag("TRIAGEIQ_SANITIZE", True)         # tokenise the patient name, scan for injected instructions
MAX_TOKENS_PER_RUN = int(os.getenv("TRIAGEIQ_TOKEN_CEILING", "60000"))
MAX_LLM_CALLS_PER_RUN = int(os.getenv("TRIAGEIQ_CALL_CEILING", "12"))
REQUEST_TIMEOUT_S = float(os.getenv("TRIAGEIQ_TIMEOUT_S", "45"))
API_MAX_RETRIES = int(os.getenv("TRIAGEIQ_API_RETRIES", "2"))

# ---- deployment --------------------------------------------------------------
DEMO_MODE = _flag("TRIAGEIQ_DEMO", False)                  # replay demo/sample_trace.json, no model calls
AUDIT_LOG_PATH = os.getenv("TRIAGEIQ_AUDIT_LOG", "logs/audit.jsonl")
CORS_ORIGINS = [o.strip() for o in os.getenv(
    "TRIAGEIQ_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:5173",
).split(",") if o.strip()]

# ESI priority levels (1 = most urgent)
ESI_LEVELS = {
    1: {"label": "Immediate",    "color": "#DC2626", "bg": "#FEF2F2"},
    2: {"label": "Emergent",     "color": "#EA580C", "bg": "#FFF7ED"},
    3: {"label": "Urgent",       "color": "#D97706", "bg": "#FFFBEB"},
    4: {"label": "Less Urgent",  "color": "#2563EB", "bg": "#EFF6FF"},
    5: {"label": "Non-Urgent",   "color": "#16A34A", "bg": "#F0FDF4"},
}

CARE_AREAS = ("trauma_bay", "resus", "fast_track", "general", "waiting")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def require_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY") or ANTHROPIC_API_KEY
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not found. Create a .env file with your key "
                         "(or start with TRIAGEIQ_DEMO=1 to replay the recorded run).")
    return key
