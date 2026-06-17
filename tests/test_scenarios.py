"""Per-scenario validation + rubric self-check tests.

Discovers every JSON file in eval/scenarios/ and parametrizes:
1. Each loads + validates against the Scenario Pydantic schema.
2. Each scenario's id matches its filename stem.
3. Each scenario's gold ESI is consistent with the rubric's canonical ESI
   derivation, EXCEPT for is_critical_miss_test scenarios which deliberately
   trip up the deterministic rules.
4. Per-tier invariants per the tier's purpose.
5. Tier balance per the scope spec.

Zero LLM calls. CI-safe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.rubric_audit import rubric_canonical_esi
from eval.schemas import Scenario

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "eval" / "scenarios"


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*.json"))


def _load_scenario(path: Path) -> Scenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Scenario(**data)


# ---------------------------------------------------------------------------
# Per-scenario parametrized tests
# ---------------------------------------------------------------------------


SCENARIO_PATHS = _scenario_paths()
SCENARIO_IDS = [p.stem for p in SCENARIO_PATHS]


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=SCENARIO_IDS)
def test_scenario_validates_against_schema(scenario_path: Path):
    """Every scenario JSON must validate against the Scenario Pydantic model."""
    _load_scenario(scenario_path)


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=SCENARIO_IDS)
def test_scenario_id_matches_filename(scenario_path: Path):
    """scenario.id must equal the filename stem (enforces consistent IDs)."""
    scenario = _load_scenario(scenario_path)
    assert scenario.id == scenario_path.stem, (
        f"Scenario id '{scenario.id}' does not match filename "
        f"'{scenario_path.stem}'. Rename one to match."
    )


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=SCENARIO_IDS)
def test_scenario_tier_matches_filename_prefix(scenario_path: Path):
    """scenario.tier must match the filename's prefix.

    e.g. 'clear_esi_1_2_001.json' must have tier='clear_esi_1_2'.
    Catches scenarios placed in the wrong tier directory.
    """
    scenario = _load_scenario(scenario_path)
    stem = scenario_path.stem
    assert stem.startswith(scenario.tier + "_"), (
        f"Scenario '{scenario.id}' has tier='{scenario.tier}' but filename "
        f"'{stem}' does not start with '{scenario.tier}_'."
    )


@pytest.mark.parametrize("scenario_path", SCENARIO_PATHS, ids=SCENARIO_IDS)
def test_scenario_gold_esi_consistent_with_rubric(scenario_path: Path):
    """Scenario's gold ESI must be consistent with rubric's canonical derivation.

    Enforced for clear_esi_1_2, clear_esi_4_5, ambiguous, adversarial tiers.

    Skipped for is_critical_miss_test scenarios — those deliberately have
    surface-benign vitals (or atypical presentations) where the deterministic
    rubric_audit would derive a lower-acuity ESI, but the gold remains 1-2
    because the underlying condition demands it. The critical_miss_test tier
    is the rubric's known blind spot; testing the agent's ability to see
    through that blind spot is the tier's whole purpose.
    """
    scenario = _load_scenario(scenario_path)
    if scenario.is_critical_miss_test:
        pytest.skip(
            f"Scenario '{scenario.id}' is is_critical_miss_test=True — "
            f"deliberately deviates from deterministic rubric. The agent's "
            f"job is to catch what the rubric_audit cannot."
        )

    canonical, acceptable = rubric_canonical_esi(scenario.patient)
    # The scenario's expected_esi should be in the rubric's acceptable set,
    # OR the scenario's acceptable_esi should overlap with the rubric's
    # acceptable set (less strict — admits scenario author judgment).
    overlap = set(scenario.acceptable_esi) & set(acceptable)
    assert overlap, (
        f"Scenario '{scenario.id}': gold acceptable_esi={scenario.acceptable_esi} "
        f"does not overlap with rubric canonical_esi={canonical} or "
        f"acceptable={acceptable}. Patient vitals: {scenario.patient.vitals}, "
        f"chief_complaint='{scenario.patient.chief_complaint[:120]}'. "
        f"Either fix the scenario, update the rubric, or mark as "
        f"is_critical_miss_test=True if this is a deliberate atypical case."
    )


# ---------------------------------------------------------------------------
# Per-tier invariants
# ---------------------------------------------------------------------------


CLEAR_ESI_1_2_PATHS = [p for p in SCENARIO_PATHS if p.stem.startswith("clear_esi_1_2_")]
CLEAR_ESI_4_5_PATHS = [p for p in SCENARIO_PATHS if p.stem.startswith("clear_esi_4_5_")]
AMBIGUOUS_PATHS = [p for p in SCENARIO_PATHS if p.stem.startswith("ambiguous_")]
CRITICAL_MISS_TEST_PATHS = [p for p in SCENARIO_PATHS if p.stem.startswith("critical_miss_test_")]
ADVERSARIAL_PATHS = [p for p in SCENARIO_PATHS if p.stem.startswith("adversarial_")]


@pytest.mark.parametrize(
    "scenario_path",
    CLEAR_ESI_1_2_PATHS,
    ids=[p.stem for p in CLEAR_ESI_1_2_PATHS] or ["__no_scenarios__"],
)
def test_clear_esi_1_2_tier_has_high_acuity_gold(scenario_path: Path):
    """clear_esi_1_2 tier must have expected_esi in {1, 2}."""
    if not CLEAR_ESI_1_2_PATHS:
        pytest.skip("No clear_esi_1_2 scenarios yet.")
    scenario = _load_scenario(scenario_path)
    assert scenario.expected_esi in (1, 2), (
        f"clear_esi_1_2 '{scenario.id}': expected_esi={scenario.expected_esi} "
        f"but tier requires 1 or 2."
    )


@pytest.mark.parametrize(
    "scenario_path",
    CLEAR_ESI_4_5_PATHS,
    ids=[p.stem for p in CLEAR_ESI_4_5_PATHS] or ["__no_scenarios__"],
)
def test_clear_esi_4_5_tier_has_low_acuity_gold(scenario_path: Path):
    """clear_esi_4_5 tier must have expected_esi in {4, 5}."""
    if not CLEAR_ESI_4_5_PATHS:
        pytest.skip("No clear_esi_4_5 scenarios yet.")
    scenario = _load_scenario(scenario_path)
    assert scenario.expected_esi in (4, 5), (
        f"clear_esi_4_5 '{scenario.id}': expected_esi={scenario.expected_esi} "
        f"but tier requires 4 or 5."
    )


# ---------------------------------------------------------------------------
# Day 2 balance check
# ---------------------------------------------------------------------------


def test_day_2_scenario_balance():
    """Day 2 ships >=7 clear_esi_1_2 + >=6 clear_esi_4_5 scenarios."""
    assert len(CLEAR_ESI_1_2_PATHS) >= 7, (
        f"Day 2 requires >=7 clear_esi_1_2 scenarios; "
        f"found {len(CLEAR_ESI_1_2_PATHS)}."
    )
    assert len(CLEAR_ESI_4_5_PATHS) >= 6, (
        f"Day 2 requires >=6 clear_esi_4_5 scenarios; "
        f"found {len(CLEAR_ESI_4_5_PATHS)}."
    )


def test_day_2_total_scenarios():
    """At least 13 scenarios committed (7+6 Day-2 minimum)."""
    assert len(SCENARIO_PATHS) >= 13, (
        f"Day 2 minimum is 13 scenarios; found {len(SCENARIO_PATHS)}."
    )


# ---------------------------------------------------------------------------
# Day 3 — ambiguous tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario_path",
    AMBIGUOUS_PATHS,
    ids=[p.stem for p in AMBIGUOUS_PATHS] or ["__no_scenarios__"],
)
def test_ambiguous_tier_uses_acceptable_esi_set(scenario_path: Path):
    """Ambiguous tier scenarios should accept >=2 ESI levels.

    The whole point of the tier is that multiple ESI assignments are defensible.
    """
    if not AMBIGUOUS_PATHS:
        pytest.skip("No ambiguous scenarios yet.")
    scenario = _load_scenario(scenario_path)
    assert len(scenario.acceptable_esi) >= 2, (
        f"Ambiguous '{scenario.id}': acceptable_esi={scenario.acceptable_esi} "
        f"(size {len(scenario.acceptable_esi)}). Tier requires >=2 defensible "
        f"ESI levels. Tighten the case OR move to a clear_* tier."
    )


def test_day_3_scenario_balance():
    """Day 3 ships >=6 ambiguous scenarios."""
    assert len(AMBIGUOUS_PATHS) >= 6, (
        f"Day 3 requires >=6 ambiguous scenarios; "
        f"found {len(AMBIGUOUS_PATHS)}."
    )


# ---------------------------------------------------------------------------
# Day 4 — critical_miss_test tier (THE SAFETY-CRITICAL TIER)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario_path",
    CRITICAL_MISS_TEST_PATHS,
    ids=[p.stem for p in CRITICAL_MISS_TEST_PATHS] or ["__no_scenarios__"],
)
def test_critical_miss_test_marked_with_flag(scenario_path: Path):
    """critical_miss_test tier scenarios must have is_critical_miss_test=True.

    The flag is what tells the rubric-self-check test to skip — without it,
    the deterministic rubric would (correctly) say these patients look low-acuity,
    which is the entire trap the tier is designed to test.
    """
    if not CRITICAL_MISS_TEST_PATHS:
        pytest.skip("No critical_miss_test scenarios yet.")
    scenario = _load_scenario(scenario_path)
    assert scenario.is_critical_miss_test, (
        f"critical_miss_test '{scenario.id}': is_critical_miss_test must be True. "
        f"The tier's whole purpose is to test atypical presentations the rubric "
        f"would miss; without the flag, the rubric-self-check will reject these "
        f"scenarios. Set is_critical_miss_test=true in the JSON."
    )


@pytest.mark.parametrize(
    "scenario_path",
    CRITICAL_MISS_TEST_PATHS,
    ids=[p.stem for p in CRITICAL_MISS_TEST_PATHS] or ["__no_scenarios__"],
)
def test_critical_miss_test_expected_esi_1_or_2(scenario_path: Path):
    """critical_miss_test scenarios must have expected_esi in {1, 2}.

    The whole point of the tier is testing under-triage of high-acuity patients.
    A 'critical miss' is by definition mis-classifying ESI 1-2 as 3-5.
    """
    if not CRITICAL_MISS_TEST_PATHS:
        pytest.skip("No critical_miss_test scenarios yet.")
    scenario = _load_scenario(scenario_path)
    assert scenario.expected_esi in (1, 2), (
        f"critical_miss_test '{scenario.id}': expected_esi={scenario.expected_esi}. "
        f"Tier requires expected_esi in {{1, 2}}."
    )


def test_day_4_scenario_balance():
    """Day 4 ships >=5 critical_miss_test scenarios."""
    assert len(CRITICAL_MISS_TEST_PATHS) >= 5, (
        f"Day 4 requires >=5 critical_miss_test scenarios; "
        f"found {len(CRITICAL_MISS_TEST_PATHS)}."
    )


# ---------------------------------------------------------------------------
# Day 5 — adversarial tier + tier completeness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario_path",
    ADVERSARIAL_PATHS,
    ids=[p.stem for p in ADVERSARIAL_PATHS] or ["__no_scenarios__"],
)
def test_adversarial_tier_validates(scenario_path: Path):
    """Adversarial scenarios still must validate against schema and rubric.

    The adversarial tier tests robustness; nothing about being adversarial
    excuses it from being a valid scenario.
    """
    if not ADVERSARIAL_PATHS:
        pytest.skip("No adversarial scenarios yet.")
    _load_scenario(scenario_path)


def test_day_5_scenario_balance():
    """Day 5 ships >=6 adversarial scenarios."""
    assert len(ADVERSARIAL_PATHS) >= 6, (
        f"Day 5 requires >=6 adversarial scenarios; "
        f"found {len(ADVERSARIAL_PATHS)}."
    )


def test_all_five_tiers_represented():
    """Tier completeness check — every scope-spec tier has at least one
    scenario. Prevents accidental shipping with an empty tier.
    """
    assert len(CLEAR_ESI_1_2_PATHS) >= 1
    assert len(CLEAR_ESI_4_5_PATHS) >= 1
    assert len(AMBIGUOUS_PATHS) >= 1
    assert len(CRITICAL_MISS_TEST_PATHS) >= 1
    assert len(ADVERSARIAL_PATHS) >= 1


def test_total_scenarios_meets_scope_spec():
    """Scope spec calls for 30 scenarios across 5 tiers."""
    assert len(SCENARIO_PATHS) >= 30, (
        f"Scope spec calls for >=30 scenarios across 5 tiers; "
        f"found {len(SCENARIO_PATHS)}. "
        f"Counts by tier: clear_esi_1_2={len(CLEAR_ESI_1_2_PATHS)}, "
        f"clear_esi_4_5={len(CLEAR_ESI_4_5_PATHS)}, "
        f"ambiguous={len(AMBIGUOUS_PATHS)}, "
        f"critical_miss_test={len(CRITICAL_MISS_TEST_PATHS)}, "
        f"adversarial={len(ADVERSARIAL_PATHS)}."
    )
