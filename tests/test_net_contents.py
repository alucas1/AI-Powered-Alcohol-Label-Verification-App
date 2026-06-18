"""Net contents comparison (quantity + unit, normalized to mL)."""

from verifier import Status, _compare_net_contents


def test_same_volume_passes():
    assert _compare_net_contents("Net Contents", "750 mL", "750 mL").status is Status.PASS


def test_unit_conversion_passes():
    assert _compare_net_contents("Net Contents", "750 mL", "0.75 L").status is Status.PASS


def test_different_volume_fails():
    assert _compare_net_contents("Net Contents", "750 mL", "700 mL").status is Status.FAIL


def test_unparseable_needs_review():
    assert _compare_net_contents("Net Contents", "750 mL", "one bottle").status is Status.NEEDS_REVIEW


def test_missing_extracted_needs_review():
    assert _compare_net_contents("Net Contents", "750 mL", "").status is Status.NEEDS_REVIEW
