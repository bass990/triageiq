import os
from dotenv import load_dotenv

load_dotenv()

# Models — use fast for specialist agents, smart for final synthesis
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_SMART = "claude-sonnet-4-6"
MODEL = MODEL_SMART  # legacy alias
MAX_TOKENS = 4096

# ESI priority levels (1 = most urgent)
ESI_LEVELS = {
    1: {"label": "Immediate",    "color": "#DC2626", "bg": "#FEF2F2"},
    2: {"label": "Emergent",     "color": "#EA580C", "bg": "#FFF7ED"},
    3: {"label": "Urgent",       "color": "#D97706", "bg": "#FFFBEB"},
    4: {"label": "Less Urgent",  "color": "#2563EB", "bg": "#EFF6FF"},
    5: {"label": "Non-Urgent",   "color": "#16A34A", "bg": "#F0FDF4"},
}

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not found. Create a .env file with your key.")
