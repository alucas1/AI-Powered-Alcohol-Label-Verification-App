"""End-to-end behavior of the top-level verify() entry point."""

from types import SimpleNamespace

from verifier import STANDARD_WARNING, Status, verify

EXPECTED = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "government_warning": STANDARD_WARNING,
}


def _extracted(**overrides):
    """A LabelFields-like object that matches EXPECTED unless overridden."""
    fields = {
        "brand_name": "Old Tom Distillery",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol. (90 Proof)",
        "net_contents": "750 mL",
        "government_warning": STANDARD_WARNING,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_returns_one_result_per_field_in_order():
    results = verify(EXPECTED, _extracted())
    assert [r.field for r in results] == [
        "Brand Name",
        "Class/Type",
        "Alcohol Content",
        "Net Contents",
        "Government Warning",
    ]


def test_all_fields_pass_on_matching_label():
    assert all(r.status is Status.PASS for r in verify(EXPECTED, _extracted()))


def test_mismatch_is_isolated_to_its_field():
    by_field = {r.field: r.status for r in verify(EXPECTED, _extracted(alcohol_content="40% Alc./Vol."))}
    assert by_field["Alcohol Content"] is Status.FAIL
    assert by_field["Brand Name"] is Status.PASS
