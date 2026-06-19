"""Alcohol content comparison (ABV percentage and proof)."""

from verifier import Status, _compare_alcohol, _parse_alcohol


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


def test_leading_non_abv_percentage_is_ignored():
    # A stray percentage ("100% Agave") must not be mistaken for the ABV.
    result = _compare_alcohol("Alcohol", "40% Alc./Vol.", "100% Agave. 40% Alc./Vol. (80 Proof)")
    assert result.status is Status.PASS


def test_abv_anchored_when_keyword_precedes_the_number():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "Alc. 45% by Vol.").status is Status.PASS


def test_european_decimal_comma_reads_as_decimal():
    assert _compare_alcohol("Alcohol", "13.5% Alc./Vol.", "13,5% Alc./Vol.").status is Status.PASS


def test_ambiguous_unanchored_percentages_need_review():
    # Several percentages, none tied to an alcohol keyword: defer, don't guess.
    result = _compare_alcohol("Alcohol", "40% Alc./Vol.", "Made with 30% rye and 70% corn")
    assert result.status is Status.NEEDS_REVIEW


def test_parse_prefers_keyword_anchored_percentage():
    assert _parse_alcohol("100% Agave. 40% Alc./Vol. (80 Proof)") == (40.0, 80.0)


def test_unreadable_alcohol_needs_review():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "no idea").status is Status.NEEDS_REVIEW


def test_missing_extracted_needs_review():
    assert _compare_alcohol("Alcohol", "45% Alc./Vol.", "").status is Status.NEEDS_REVIEW
