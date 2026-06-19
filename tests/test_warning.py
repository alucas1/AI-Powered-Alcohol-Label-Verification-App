"""Strict government warning comparison."""

from verifier import STANDARD_WARNING, Status, _compare_warning


def test_exact_warning_passes():
    assert _compare_warning("Warning", STANDARD_WARNING, STANDARD_WARNING).status is Status.PASS


def test_verbatim_with_surrounding_text_passes():
    extracted = f"Distilled and bottled by Old Tom. {STANDARD_WARNING} Drink responsibly."
    assert _compare_warning("Warning", STANDARD_WARNING, extracted).status is Status.PASS


def test_header_wrapped_across_lines_passes():
    # OCR/vision transcriptions often break the header onto two lines. The all-caps
    # colon is intact, so the warning must still be recognized rather than reported
    # as missing.
    extracted = STANDARD_WARNING.replace("GOVERNMENT WARNING:", "GOVERNMENT\nWARNING:")
    assert _compare_warning("Warning", STANDARD_WARNING, extracted).status is Status.PASS


def test_doubled_internal_spacing_passes():
    extracted = STANDARD_WARNING.replace(" ", "  ")
    assert _compare_warning("Warning", STANDARD_WARNING, extracted).status is Status.PASS


def test_lowercase_header_fails():
    extracted = STANDARD_WARNING.replace("GOVERNMENT WARNING:", "Government Warning:")
    result = _compare_warning("Warning", STANDARD_WARNING, extracted)
    assert result.status is Status.FAIL
    assert "ALL CAPITAL LETTERS" in result.explanation


def test_missing_warning_fails():
    assert _compare_warning("Warning", STANDARD_WARNING, "Drink responsibly.").status is Status.FAIL


def test_reworded_warning_fails():
    extracted = "GOVERNMENT WARNING: Please drink in moderation."
    assert _compare_warning("Warning", STANDARD_WARNING, extracted).status is Status.FAIL


def test_missing_extracted_needs_review():
    assert _compare_warning("Warning", STANDARD_WARNING, "").status is Status.NEEDS_REVIEW
