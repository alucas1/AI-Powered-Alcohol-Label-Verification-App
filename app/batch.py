"""Expected-value inputs: required-field validation and optional CSV-per-file
batch loading.

Pure helpers (pandas only, no Streamlit or network), so the UI can stay thin and
the matching rules stay unit-testable. The UI offers two modes:

- Shared: one set of expected values typed once, applied to every image.
- CSV: a per-image override, matched to the upload by filename.
"""

from __future__ import annotations

import io

import pandas as pd

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
