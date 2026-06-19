"""The standing manual-review row for warning visual formatting."""

from verifier import Status, warning_visual_format_result


def test_always_needs_review():
    assert warning_visual_format_result().status is Status.NEEDS_REVIEW


def test_row_is_clearly_labelled():
    assert warning_visual_format_result().field == "Warning Visual Format"


def test_note_points_a_human_at_the_visual_checks():
    explanation = warning_visual_format_result().explanation.lower()
    assert "bold" in explanation
    assert "type size" in explanation
    assert "placement" in explanation
