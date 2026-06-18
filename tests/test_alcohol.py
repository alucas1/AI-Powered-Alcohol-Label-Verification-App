"""Alcohol content comparison (ABV percentage and proof)."""

from verifier import Status, _compare_alcohol


def test_matching_abv_passes():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "45% Alc./Vol.").status is Status.PASS


def test_abv_within_tolerance_passes():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "45.05% Alc./Vol.").status is Status.PASS


def test_differing_abv_fails():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "40% Alc./Vol.").status is Status.FAIL


def test_proof_inconsistent_with_abv_warns():
    # ABV matches but the printed proof (should be ~90) is wrong.
    result = _compare_alcohol("Alcohol", "45% Alc./Vol. (90 Proof)", "45% Alc./Vol. (100 Proof)")
    assert result.status is Status.WARNING


def test_proof_only_match_passes():
    assert _compare_alcohol("Alcohol", "90 Proof", "90 Proof").status is Status.PASS


def test_unreadable_alcohol_needs_review():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "no idea").status is Status.NEEDS_REVIEW


def test_missing_extracted_needs_review():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "").status is Status.NEEDS_REVIEW
