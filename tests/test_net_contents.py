"""Net contents comparison (quantity + unit, normalized to mL)."""

from verifier import Status, _compare_net_contents


def test_same_volume_passes():
    assert _compare_net_contents("Net Contents", "750 mL", "750 mL").status is Status.PASS


def test_unit_conversion_passes():
    assert _compare_net_contents("Net Contents", "750 mL", "0.75 L").status is Status.PASS


def test_centiliters_convert():
    # 70 cl is the common European spirits size; equals 700 mL.
    assert _compare_net_contents("Net Contents", "700 mL", "70 cl").status is Status.PASS


def test_fluid_ounces_convert():
    # 25.4 fl oz is ~751 mL, within the ~1% tolerance of a 750 mL expectation.
    assert _compare_net_contents("Net Contents", "750 mL", "25.4 fl oz").status is Status.PASS


def test_one_percent_tolerance_holds():
    # 5 mL off a 750 mL fill is under the ~1% tolerance and should pass.
    assert _compare_net_contents("Net Contents", "750 mL", "755 mL").status is Status.PASS


def test_different_volume_fails():
    assert _compare_net_contents("Net Contents", "750 mL", "700 mL").status is Status.FAIL


def test_unparseable_needs_review():
    assert _compare_net_contents("Net Contents", "750 mL", "one bottle").status is Status.NEEDS_REVIEW


def test_missing_extracted_needs_review():
    assert _compare_net_contents("Net Contents", "750 mL", "").status is Status.NEEDS_REVIEW
