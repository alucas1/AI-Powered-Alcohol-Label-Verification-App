"""CSV-per-file expected-value loading, filename matching, and results export."""

import csv as csvlib
import io

import pytest

from batch import RESULTS_CSV_COLUMNS, expected_for, load_expected_csv, results_to_csv
from verifier import FieldResult, Status

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


# --- results export -----------------------------------------------------------


def _rows(*results):
    return [FieldResult(field, "exp", "ext", status, "why") for field, status in results]


def test_results_csv_has_field_rows_plus_a_manual_review_row_per_image():
    verified = [
        ("a.png", _rows(("Brand Name", Status.PASS), ("Alcohol Content", Status.FAIL)), True),
        ("b.png", _rows(("Brand Name", Status.WARNING)), False),
    ]
    rows = list(csvlib.DictReader(io.StringIO(results_to_csv(verified))))
    assert [(r["filename"], r["field"]) for r in rows] == [
        ("a.png", "Brand Name"),
        ("a.png", "Alcohol Content"),
        ("a.png", "Manual Visual Format Review"),
        ("b.png", "Brand Name"),
        ("b.png", "Manual Visual Format Review"),
    ]


def test_manual_review_status_reflects_the_checkbox():
    confirmed = [("a.png", _rows(("Brand Name", Status.PASS)), True)]
    not_confirmed = [("b.png", _rows(("Brand Name", Status.PASS)), False)]
    yes = list(csvlib.DictReader(io.StringIO(results_to_csv(confirmed))))[-1]
    no = list(csvlib.DictReader(io.StringIO(results_to_csv(not_confirmed))))[-1]
    assert yes["status"] == "YES"
    assert no["status"] == "NO"


def test_results_csv_uses_human_readable_status_labels():
    verified = [("a.png", _rows(("Government Warning", Status.NEEDS_REVIEW)), False)]
    rows = list(csvlib.DictReader(io.StringIO(results_to_csv(verified))))
    assert rows[0]["status"] == "NEEDS REVIEW"  # not the enum's "NEEDS_REVIEW"


def test_results_csv_header_matches_columns():
    header = results_to_csv([]).splitlines()[0]
    assert header.split(",") == RESULTS_CSV_COLUMNS
