"""Integration checks against the bundled sample set in test_files/.

These run offline: the provided CSV drives the expected values, the images are
decoded straight off disk, and each label's real on-disk text (per
test_files/README.md) stands in for the model's output so the documented
pass/fail outcomes are asserted without a network call.

A genuine end-to-end test that sends the images to the AI provider is included
too. It runs by default, but only once a precheck confirms a usable API key and a
reachable model (see the `live_extraction_ready` fixture); without valid
credentials it skips rather than fails, so an offline checkout stays green. Set
SKIP_LIVE_EXTRACTION=1 to opt out even when a key is present.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from batch import expected_for, load_expected_csv, missing_fields
from label_ai import MissingAPIKeyError, _prepare_image, get_api_key, get_model
from verifier import STANDARD_WARNING, Status, verify, warning_visual_format_result

TEST_FILES = Path(__file__).resolve().parents[1] / "test_files"
SAMPLE_CSV = TEST_FILES / "sample_label_batch_test.csv"
SAMPLE_IMAGES = [
    "old_tom_distillery.png",
    "silver_coast.png",
    "stones_throw.png",
    "monarch_hill.png",
]

# Each label's real on-disk text, transcribed in test_files/README.md. Supplied
# as the model's output so the documented outcomes are exercised deterministically.
LABEL_TEXT = {
    "old_tom_distillery.png": {
        "brand_name": "OLD TOM DISTILLERY",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol. (90 Proof)",
        "net_contents": "750 mL",
        "government_warning": STANDARD_WARNING,
    },
    "silver_coast.png": {
        "brand_name": "SILVER COAST DISTILLING CO.",
        "class_type": "London Dry Gin",
        "alcohol_content": "40% Alc./Vol. (80 Proof)",
        "net_contents": "1 L",
        "government_warning": STANDARD_WARNING,
    },
    "stones_throw.png": {
        "brand_name": "STONE'S THROW",  # CSV has "Stone's Throw": case-only diff
        "class_type": "Small Batch Rye Whiskey",
        "alcohol_content": "47% Alc./Vol. (94 Proof)",  # CSV expects 45%/90: fails
        "net_contents": "750 mL",
        "government_warning": STANDARD_WARNING,
    },
    "monarch_hill.png": {
        "brand_name": "MONARCH HILL",
        "class_type": "Cabernet Sauvignon",
        "alcohol_content": "13.5% Alc./Vol.",
        "net_contents": "750 mL",
        # Lowercase header and reworded text: both fail the strict warning check.
        "government_warning": (
            "Government Warning: (1) According to the Surgeon General, pregnant "
            "women should not drink alcoholic beverages because of the risk of "
            "birth defects. (2) Consumption of alcohol impairs your ability to "
            "drive a car or operate machinery, and may cause health problems."
        ),
    },
}


@pytest.fixture(scope="module")
def csv_map():
    return load_expected_csv(SAMPLE_CSV.read_bytes())


# --- the provided files are present and internally consistent -----------------


def test_sample_files_present():
    assert SAMPLE_CSV.is_file()
    for name in SAMPLE_IMAGES:
        assert (TEST_FILES / name).is_file(), f"missing sample image: {name}"


def test_csv_filenames_match_images_on_disk(csv_map):
    """The batch CSV pairs rows to images by filename, so the two must agree."""
    assert set(csv_map) == {p.name.lower() for p in TEST_FILES.glob("*.png")}


def test_every_csv_row_has_all_required_fields(csv_map):
    for name, expected in csv_map.items():
        assert missing_fields(expected) == [], f"{name}: incomplete expected values"


@pytest.mark.parametrize("name", SAMPLE_IMAGES)
def test_sample_images_decode(name):
    prepared = _prepare_image((TEST_FILES / name).read_bytes())
    assert isinstance(prepared, bytes) and prepared


# --- verifier produces the documented results for each sample -----------------


def _statuses(csv_map, name):
    results = verify(expected_for(name, csv_map), SimpleNamespace(**LABEL_TEXT[name]))
    return {r.field: r.status for r in results}


def test_old_tom_passes_every_field(csv_map):
    assert set(_statuses(csv_map, "old_tom_distillery.png").values()) == {Status.PASS}


def test_silver_coast_full_brand_passes_every_field(csv_map):
    assert set(_statuses(csv_map, "silver_coast.png").values()) == {Status.PASS}


def test_stones_throw_fails_only_alcohol(csv_map):
    statuses = _statuses(csv_map, "stones_throw.png")
    assert statuses["Alcohol Content"] is Status.FAIL
    assert statuses["Brand Name"] is Status.PASS  # STONE'S THROW vs Stone's Throw
    assert statuses["Government Warning"] is Status.PASS
    assert [f for f, s in statuses.items() if s is Status.FAIL] == ["Alcohol Content"]


def test_monarch_hill_fails_only_government_warning(csv_map):
    statuses = _statuses(csv_map, "monarch_hill.png")
    assert statuses["Government Warning"] is Status.FAIL
    assert [f for f, s in statuses.items() if s is Status.FAIL] == ["Government Warning"]


def test_visual_format_row_flags_each_sample_for_review():
    assert warning_visual_format_result().status is Status.NEEDS_REVIEW


# --- live: real round-trip through the AI provider ----------------------------
#
# DISCLAIMER: these tests call the live vision model and are NOT deterministic.
# The model's reads drift between runs: OCR, line breaks, and capitalization
# shift, an occasional field comes back unreadable, and a label may carry extra
# words the prompt can't help folding in. Re-run before treating a single failure
# as a regression; the offline tests above pin the outcomes that must always hold.
#
# To stay meaningful without being flaky, each field is checked by intent rather
# than by an exact status:
#   "fail":  the verifier MUST return FAIL. These are the two violations the
#            sample set is built around (stones_throw's wrong alcohol content and
#            monarch_hill's malformed warning), and they hold run to run.
#   "pass":  the field must simply NOT FAIL. PASS and WARNING are matches; a
#            NEEDS REVIEW (e.g. the model couldn't read the class) is tolerated
#            because it defers to a human rather than asserting a mismatch.
#   "noisy": not asserted. The model drops trailing brand words run to run:
#            old_tom reads as "Old Tom" (no "Distillery"), stones_throw as
#            "Stone's Throw Spirits", and silver_coast as "Silver Coast" (no
#            "Distilling Co."), each fuzzy-failing the CSV brand on some runs and
#            matching on others. That's a real extraction quirk, not a signal
#            worth gating the suite on.
EXPECTED_OUTCOMES = {
    "old_tom_distillery.png": {
        "Brand Name": "noisy", "Class/Type": "pass", "Alcohol Content": "pass",
        "Net Contents": "pass", "Government Warning": "pass",
    },
    "silver_coast.png": {
        "Brand Name": "noisy", "Class/Type": "pass", "Alcohol Content": "pass",
        "Net Contents": "pass", "Government Warning": "pass",
    },
    "stones_throw.png": {
        "Brand Name": "noisy", "Class/Type": "pass", "Alcohol Content": "fail",
        "Net Contents": "pass", "Government Warning": "pass",
    },
    "monarch_hill.png": {
        "Brand Name": "pass", "Class/Type": "pass", "Alcohol Content": "pass",
        "Net Contents": "pass", "Government Warning": "fail",
    },
}


@pytest.fixture(scope="module")
def live_extraction_ready():
    """Precondition for the live tests: a usable key and a reachable model.

    Runs before the live extraction tests and gates them. The credentials are
    proven, not assumed. `models.retrieve` exercises both the key (auth) and the
    configured model (existence and access) in one lightweight call. Any failure
    (no key, bad key, unknown model, no network) skips the live tests with the
    reason instead of failing them, so a checkout without credentials stays green.
    Set SKIP_LIVE_EXTRACTION=1 to opt out even when a valid key is present.
    """
    if os.environ.get("SKIP_LIVE_EXTRACTION") == "1":
        pytest.skip("live extraction opted out (SKIP_LIVE_EXTRACTION=1)")

    try:
        key = get_api_key()
    except MissingAPIKeyError:
        pytest.skip("no OpenAI API key configured; set OPENAI_API_KEY to run the live tests")

    from openai import OpenAI

    model = get_model()
    try:
        OpenAI(api_key=key).models.retrieve(model)
    except Exception as exc:
        pytest.skip(f"model {model!r} is not reachable with this key ({exc})")
    return model


@pytest.mark.parametrize("name", SAMPLE_IMAGES)
def test_live_extraction_matches_expected_outcomes(live_extraction_ready, csv_map, name):
    """Read the image with the live model, verify it against the CSV's expected
    values, and check each field's outcome. See the disclaimer above: model
    output varies between runs, so a single failure may be noise; re-run."""
    from label_ai import extract_label_fields

    extracted = extract_label_fields((TEST_FILES / name).read_bytes())
    results = {r.field: r for r in verify(expected_for(name, csv_map), extracted)}

    for field, outcome in EXPECTED_OUTCOMES[name].items():
        r = results[field]
        detail = f"{name} / {field}: label read {r.extracted!r} ({r.explanation})"
        if outcome == "fail":
            assert r.status is Status.FAIL, f"expected FAIL, got {r.status.value}. {detail}"
        elif outcome == "pass":
            assert r.status is not Status.FAIL, f"expected no FAIL, got FAIL. {detail}"
