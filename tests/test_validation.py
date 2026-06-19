"""Required expected-value validation used to gate the AI call."""

from batch import missing_fields

COMPLETE = {
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "government_warning": "GOVERNMENT WARNING: ...",
}


def test_complete_input_has_no_missing_fields():
    assert missing_fields(COMPLETE) == []


def test_blank_and_whitespace_values_are_missing():
    expected = {**COMPLETE, "brand_name": "", "net_contents": "   "}
    assert missing_fields(expected) == ["Brand Name", "Net Contents"]


def test_absent_keys_are_missing():
    assert missing_fields({}) == [
        "Brand Name",
        "Class / Type",
        "Alcohol Content",
        "Net Contents",
        "Government Warning",
    ]


def test_none_value_is_missing():
    assert missing_fields({**COMPLETE, "class_type": None}) == ["Class / Type"]
