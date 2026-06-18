"""Fuzzy text comparison used for Brand Name and Class/Type."""

import pytest

from verifier import Status, _compare_fuzzy


@pytest.mark.parametrize(
    "expected, extracted, status",
    [
        ("Old Tom Distillery", "Old Tom Distillery", Status.PASS),      # exact
        ("OLD TOM DISTILLERY", "Old Tom Distillery", Status.PASS),      # case/punctuation only
        ("Old Tom Distillery", "Old Tomm Distillery", Status.WARNING),  # near miss
        ("Old Tom Distillery", "Buffalo Trace", Status.FAIL),           # clearly different
    ],
)
def test_match_status(expected, extracted, status):
    assert _compare_fuzzy("Brand Name", expected, extracted).status is status


def test_missing_extracted_needs_review():
    assert _compare_fuzzy("Brand Name", "Old Tom", "").status is Status.NEEDS_REVIEW


def test_missing_expected_needs_review():
    assert _compare_fuzzy("Brand Name", "", "Old Tom").status is Status.NEEDS_REVIEW
