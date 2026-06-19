"""CSV-per-file expected-value loading and filename matching."""

import pytest

from batch import expected_for, load_expected_csv

CSV = (
    "filename,brand_name,class_type,alcohol_content,net_contents,government_warning\n"
    "old_tom.png,Old Tom Distillery,Kentucky Straight Bourbon Whiskey,"
    "45% Alc./Vol. (90 Proof),750 mL,GOVERNMENT WARNING: ...\n"
    "stones_throw.jpg,Stone's Throw,Gin,40% Alc./Vol.,700 mL,GOVERNMENT WARNING: ...\n"
)


def test_loads_one_row_per_filename():
    rows = load_expected_csv(CSV.encode())
    assert set(rows) == {"old_tom.png", "stones_throw.jpg"}
    assert rows["old_tom.png"]["brand_name"] == "Old Tom Distillery"
    assert rows["old_tom.png"]["net_contents"] == "750 mL"


def test_accepts_bytes_and_file_like():
    import io

    assert load_expected_csv(CSV.encode()) == load_expected_csv(io.BytesIO(CSV.encode()))


def test_match_is_case_insensitive_and_trimmed():
    rows = load_expected_csv(CSV.encode())
    assert expected_for("OLD_TOM.PNG", rows)["class_type"] == "Kentucky Straight Bourbon Whiskey"
    assert expected_for("  stones_throw.jpg ", rows)["brand_name"] == "Stone's Throw"


def test_unmatched_filename_returns_none():
    rows = load_expected_csv(CSV.encode())
    assert expected_for("not_in_csv.png", rows) is None


def test_missing_column_raises():
    bad = "filename,brand_name\nx.png,Old Tom\n"
    with pytest.raises(ValueError, match="missing required column"):
        load_expected_csv(bad.encode())


def test_duplicate_filename_raises():
    dup = (
        "filename,brand_name,class_type,alcohol_content,net_contents,government_warning\n"
        "x.png,A,Gin,40% Alc./Vol.,750 mL,GW\n"
        "x.png,B,Gin,40% Alc./Vol.,750 mL,GW\n"
    )
    with pytest.raises(ValueError, match="duplicate filename"):
        load_expected_csv(dup.encode())


def test_blank_filename_raises():
    blank = (
        "filename,brand_name,class_type,alcohol_content,net_contents,government_warning\n"
        ",A,Gin,40% Alc./Vol.,750 mL,GW\n"
    )
    with pytest.raises(ValueError, match="filename"):
        load_expected_csv(blank.encode())
