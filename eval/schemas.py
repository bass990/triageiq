"""Pydantic models for the TriageIQ eval harness.

These types are the contract between scenarios, the runners, and the scorers.
A scenario JSON file must validate against `Scenario`. The runner output
populates `ScenarioResult`. The scorers consume `ScenarioResult` and emit
`BranchMetrics` / `ABLiftResult`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ESI scores. ESI 1 = most urgent; ESI 5 = least urgent.
ESI = Literal[1, 2, 3, 4, 5]

# Care areas, mirrored from backend/tools.py::MOCK_BEDS.
CareArea = Literal["trauma_bay", "resus", "fast_track", "general", "waiting"]

# Tier names — must match scenario file directory conventions.
Tier = Literal[
    "clear_esi_1_2",
    "clear_esi_4_5",
    "ambiguous",
    "critical_miss_test",
    "adversarial",
]


class Vitals(BaseModel):
    """Patient vital signs. Strings for BP (e.g. '120/80'); ints for everything else.

    All vitals are optional — the production system instructs "do not invent
    values" when vitals are missing. The eval's adversarial tier has missing-vitals
    scenarios that test this discipline.
    """

    bp: str | None = None       # e.g. "120/80"
    hr: int | None = None       # bpm
    rr: int | None = None       # breaths/min
    spo2: int | None = None     # percent
    temp: float | None = None   # Celsius
    gcs: int | None = None      # Glasgow Coma Scale 3-15


class Patient(BaseModel):
    """Patient record as the agent sees it.

    Mirrors the dict shape used by backend/orchestrator.py + tools.py::MOCK_PATIENTS.
    The eval mocks get_patient_data() to return this object's serialized form.
    """

    name: str = Field(..., min_length=1, max_length=200)
    chief_complaint: str = Field(..., min_length=1, max_length=2000)
    vitals: Vitals = Field(default_factory=Vitals)
    history: str = Field(default="", max_length=2000)
    allergies: str = Field(default="None", max_length=500)
    arrival: str = Field(default="walk-in", max_length=100)


class Scenario(BaseModel):
    """One gold scenario fixture in eval/scenarios/."""

    id: str = Field(..., pattern=r"^[a-z0-9_]+_\d{3}$")
    tier: Tier
    description: str = Field(..., min_length=10, max_length=500)
    patient: Patient

    expected_esi: ESI
    acceptable_esi: list[ESI] = Field(default_factory=list)

    expected_care_areas: list[CareArea] = Field(default_factory=list)
    expected_critical_flags: list[str] = Field(default_factory=list)

    rubric_notes: str = Field(default="", max_length=4000)

    # Marks scenarios in the critical_miss_test tier where the surface vitals
    # don't trip deterministic rules but the underlying condition is high-acuity.
    # The rubric-self-check test applies relaxed validation for these.
    is_critical_miss_test: bool = False

    @model_validator(mode="after")
    def populate_acceptable_sets(self) -> Scenario:
        """Default acceptable_* sets to sensible values when not specified."""
        if not self.acceptable_esi:
            self.acceptable_esi = [self.expected_esi]
        if not self.expected_care_areas:
            # Default to the canonical mapping for the expected ESI.
            mapping: dict[int, list[CareArea]] = {
                1: ["trauma_bay", "resus"],
                2: ["resus", "trauma_bay"],
                3: ["fast_track", "general"],
                4: ["general", "fast_track"],
                5: ["waiting", "general"],
            }
            self.expected_care_areas = mapping[self.expected_esi]
        # is_critical_miss_test scenarios must have expected_esi in {1, 2}.
        if self.is_critical_miss_test and self.expected_esi not in (1, 2):
            raise ValueError(
                f"Scenario '{self.id}' is is_critical_miss_test=True but "
                f"expected_esi={self.expected_esi}. Critical-miss-test "
                f"scenarios test under-triage of urgent patients — "
                f"expected_esi must be 1 or 2."
            )
        return self

    @field_validator("acceptable_esi")
    @classmethod
    def acceptable_esi_nonempty_after_default(cls, v):  # noqa: ANN001
        return v


# ---------------------------------------------------------------------------
# Runtime output types
# ---------------------------------------------------------------------------


class TriageOutput(BaseModel):
    """Final triage report as the agent emits it.

    Mirrors the shape returned by generate_triage_report() in
    backend/tools.py, with the addition of critical_flags for adversarial
    tier scoring.
    """

    esi_score: ESI
    care_area: CareArea | None = None
    patient_summary: str = ""
    critical_flags: list[str] = Field(default_factory=list)
    rationale: str = ""


class ScenarioResult(BaseModel):
    """One (scenario, branch, rep) run output."""

    scenario_id: str
    tier: Tier
    branch: Literal["full", "stripped"]
    rep: int

    # Agent output. None if the run errored.
    output: TriageOutput | None = None

    # Bookkeeping.
    error: str | None = None
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Scorer output types
# ---------------------------------------------------------------------------


class TriageScore(BaseModel):
    """Per-scenario score on one (scenario, branch, rep) run."""

    esi_strict_match: bool                   # predicted == canonical
    esi_lenient_match: bool                  # |predicted - canonical| <= 1
    is_critical_miss: bool                   # gold ESI <= 2 AND predicted >= 3
    is_overtriage: bool                      # gold ESI >= 4 AND predicted <= 2
    care_area_match: bool                    # predicted care_area in acceptable set
    critical_flag_coverage: float            # fraction of expected flags mentioned


class BranchMetrics(BaseModel):
    """Aggregated metrics across scenarios for one branch."""

    branch: Literal["full", "stripped"]
    n_scenarios: int
    n_reps: int

    # Overall.
    esi_strict_acc: float
    esi_lenient_acc: float
    critical_miss_rate: float                # over scenarios with gold ESI <= 2
    overtriage_rate: float                   # over scenarios with gold ESI >= 4
    care_area_acc: float
    critical_flag_coverage_mean: float

    # Per-tier breakdown — same metrics scoped to each tier.
    per_tier: dict[str, dict[str, float]] = Field(default_factory=dict)


class ABLiftResult(BaseModel):
    """The headline A/B finding for one metric family."""

    metric: str
    full_score: float
    stripped_score: float
    lift: float
    interpretation: Literal[
        "full_wins", "stripped_wins", "equivalent", "investigate"
    ]


# Convenience alias for orchestrator return shapes.
ScenarioOrResults = ScenarioResult | list[ScenarioResult]
