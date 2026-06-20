"""Batch CSV handling: required-field validation, per-file expected-value
loading, and exporting verified results as a single downloadable CSV.

Pure helpers (pandas only, no Streamlit or network), so the UI stays thin and the
rules stay unit-testable. Two input modes feed the verifier:

- Shared: one set of expected values typed once, applied to every image.
- CSV: a per-image override, matched to the upload by filename.
"""

from __future__ import annotations

import io
from typing import Iterable

import pandas as pd

from verifier import STATUS_LABEL, FieldResult

# Form key -> human label, in display order. These are the five values a
# reviewer must supply for every label before it can be verified.
FIELD_LABELS = {
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "alcohol_content": "Alcohol Content",
    "net_contents": "Net Contents",
    "government_warning": "Government Warning",
}

# Columns a batch CSV must carry, one row per image.
EXPECTED_CSV_COLUMNS = ["filename", *FIELD_LABELS]


def missing_fields(expected: dict) -> list[str]:
    """Labels of the required expected values left blank, in display order."""
    return [
        label
        for key, label in FIELD_LABELS.items()
        if not str(expected.get(key) or "").strip()
    ]


def _normalize_name(filename) -> str:
    """Filenames are matched case-insensitively and whitespace-trimmed, so a CSV
    authored on one platform still lines up with uploads from another."""
    return str(filename).strip().lower()


def load_expected_csv(data) -> dict[str, dict]:
    """Parse a batch CSV into ``{normalized filename: expected-values dict}``.

    Accepts raw bytes or a file-like object. Raises ValueError with a
    reviewer-readable message if the file can't be parsed, a required column is
    absent, or a filename is blank or duplicated.
    """
    if isinstance(data, (bytes, bytearray)):
        data = io.BytesIO(data)

    try:
        df = pd.read_csv(data, dtype=str).fillna("")
    except Exception as exc:
        raise ValueError(f"the file could not be read as CSV ({exc}).") from exc

    df.columns = [str(c).strip().lower() for c in df.columns]
    absent = [c for c in EXPECTED_CSV_COLUMNS if c not in df.columns]
    if absent:
        raise ValueError("missing required column(s): " + ", ".join(absent))

    rows: dict[str, dict] = {}
    for _, row in df.iterrows():
        name = _normalize_name(row["filename"])
        if not name:
            raise ValueError("every row must list a filename.")
        if name in rows:
            raise ValueError(f"duplicate filename in the CSV: {row['filename']}")
        rows[name] = {key: str(row[key]).strip() for key in FIELD_LABELS}
    return rows


def expected_for(filename, csv_map: dict[str, dict]) -> dict | None:
    """Expected values for an uploaded image, matched by filename, or None when
    the CSV carries no row for it."""
    return csv_map.get(_normalize_name(filename))


# Columns of the downloadable results CSV: one row per field per image, plus a
# trailing manual-review row carrying the reviewer's visual-format confirmation.
RESULTS_CSV_COLUMNS = ["filename", "field", "expected", "extracted", "status", "explanation"]


def _manual_review_row(filename: str, confirmed: bool) -> dict:
    """The visual-format confirmation row, with status driven by the reviewer's
    checkbox (YES once they've confirmed it by eye, NO until then)."""
    return {
        "filename": filename,
        "field": "Manual Visual Format Review",
        "expected": "Bold 'GOVERNMENT WARNING:', legible type size, placement, separation",
        "extracted": "",
        "status": "YES" if confirmed else "NO",
        "explanation": (
            "Reviewer confirmed the warning's visual formatting by eye."
            if confirmed
            else "Not confirmed. Visual formatting still needs manual review."
        ),
    }


def results_to_csv(verified: Iterable[tuple[str, list[FieldResult], bool, dict]]) -> str:
    """Flatten a verified batch into one CSV: one row per field per image, then a
    manual-review row per image.

    `verified` is an iterable of (filename, results, visual_confirmed, overrides)
    tuples. `results` is the comparison rows shown for that image,
    `visual_confirmed` is the reviewer's checkbox state, and `overrides` maps a
    result index to a (status, explanation) pair that replaces the automated
    verdict for that field. Returns CSV text.
    """
    records = []
    for filename, results, confirmed, overrides in verified:
        overrides = overrides or {}
        for j, r in enumerate(results):
            status, explanation = overrides.get(j, (STATUS_LABEL[r.status], r.explanation))
            records.append({
                "filename": filename,
                "field": r.field,
                "expected": r.expected,
                "extracted": r.extracted,
                "status": status,
                "explanation": explanation,
            })
        records.append(_manual_review_row(filename, confirmed))
    return pd.DataFrame(records, columns=RESULTS_CSV_COLUMNS).to_csv(index=False)
