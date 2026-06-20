"""Robustness of the batch CSV loader against real-world authoring quirks.

These complement test_batch.py, which covers the happy path and the required
validation errors. The cases here pin behavior on the messy inputs a reviewer is
likely to hand it: spreadsheet BOMs, stray columns, header casing, quoted text
with embedded commas, and non-ASCII values.
"""

import io

import pytest

from batch import expected_for, load_expected_csv

HEADER = "filename,brand_name,class_type,alcohol_content,net_contents,government_warning"


def _csv(*rows):
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def test_utf8_bom_is_tolerated():
    # Excel and other editors prepend a BOM; it must not corrupt the first column.
    data = "﻿" + HEADER + "\nx.png,Old Tom,Gin,40% Alc./Vol.,750 mL,GW\n"
    rows = load_expected_csv(data.encode("utf-8"))
    assert expected_for("x.png", rows)["brand_name"] == "Old Tom"


def test_header_case_and_whitespace_are_normalized():
    header = " FileName , Brand_Name ,Class_Type,Alcohol_Content,Net_Contents,Government_Warning"
    data = (header + "\nx.png,Old Tom,Gin,40% Alc./Vol.,750 mL,GW\n").encode()
    rows = load_expected_csv(data)
    assert rows["x.png"]["class_type"] == "Gin"


def test_extra_columns_are_ignored():
    header = HEADER + ",notes,reviewer"
    data = (header + "\nx.png,Old Tom,Gin,40% Alc./Vol.,750 mL,GW,looks fine,jpark\n").encode()
    rows = load_expected_csv(data)
    assert set(rows["x.png"]) == {
        "brand_name", "class_type", "alcohol_content", "net_contents", "government_warning",
    }
    assert rows["x.png"]["brand_name"] == "Old Tom"


def test_quoted_field_with_commas_is_preserved():
    warning = (
        "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
        "not drink alcoholic beverages during pregnancy."
    )
    data = _csv(f'x.png,Old Tom,Gin,40% Alc./Vol.,750 mL,"{warning}"')
    rows = load_expected_csv(data)
    assert rows["x.png"]["government_warning"] == warning


def test_quoted_multiline_field_is_preserved():
    data = _csv('x.png,Old Tom,Gin,40% Alc./Vol.,750 mL,"line one\nline two"')
    rows = load_expected_csv(data)
    assert rows["x.png"]["government_warning"] == "line one\nline two"


def test_non_ascii_values_round_trip():
    data = _csv("rosé.png,Côtes du Rhône,Rosé Wine,12% Alc./Vol.,750 mL,GW")
    rows = load_expected_csv(data)
    assert expected_for("rosé.png", rows)["class_type"] == "Rosé Wine"


def test_numeric_looking_values_stay_strings():
    # Net contents like "750" must not be coerced to an int by the parser.
    data = _csv("750.png,Old Tom,Gin,40,750,GW")
    rows = load_expected_csv(data)
    assert rows["750.png"]["net_contents"] == "750"
    assert rows["750.png"]["alcohol_content"] == "40"


def test_blank_fields_load_and_are_caught_downstream():
    # A row missing values still loads; missing_fields (tested elsewhere) flags it.
    from batch import missing_fields

    data = _csv("x.png,Old Tom,,40% Alc./Vol.,,GW")
    rows = load_expected_csv(data)
    assert rows["x.png"]["class_type"] == ""
    assert missing_fields(rows["x.png"]) == ["Class / Type", "Net Contents"]


def test_surrounding_whitespace_in_values_is_trimmed():
    data = _csv("x.png,  Old Tom  ,  Gin ,40% Alc./Vol.,750 mL,GW")
    rows = load_expected_csv(data)
    assert rows["x.png"]["brand_name"] == "Old Tom"
    assert rows["x.png"]["class_type"] == "Gin"


def test_empty_csv_with_only_headers_loads_no_rows():
    rows = load_expected_csv((HEADER + "\n").encode())
    assert rows == {}


def test_not_a_csv_raises_value_error():
    with pytest.raises(ValueError):
        load_expected_csv(b"\x00\x01\x02 not a csv")
