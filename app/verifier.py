"""Field-by-field comparison of expected application values against what was
read off the label.

No Streamlit or network dependencies, so the rules stay unit-testable in
isolation. Each field is compared the way a reviewer would judge it:

- Brand / class:     normalize, then fuzzy match. Tolerates cosmetic case and
                     punctuation differences ("STONE'S THROW" vs "Stone's Throw").
- Alcohol content:   parse ABV % and proof, compare numerically.
- Net contents:      parse quantity and unit, convert to mL, compare magnitude.
- Government warning: strict, verbatim, case-sensitive on "GOVERNMENT WARNING:".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from rapidfuzz import fuzz

# Canonical TTB health warning: the UI default (so reviewers don't retype it)
# and the reference text for the strict warning check.
STANDARD_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)


class Status(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class FieldResult:
    field: str
    expected: str
    extracted: str
    status: Status
    explanation: str


# Human-readable label per status, shared by the on-screen table and the
# downloadable results CSV so both read identically.
STATUS_LABEL = {
    Status.PASS: "PASS",
    Status.WARNING: "WARNING",
    Status.FAIL: "FAIL",
    Status.NEEDS_REVIEW: "NEEDS REVIEW",
}


# --- text normalization ------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _clean(value) -> str:
    """None-safe trimmed string."""
    return "" if value is None else str(value).strip()


def _normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace (for fuzzy matching)."""
    text = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", text).strip()


def _collapse_ws(text: str) -> str:
    """Collapse whitespace but keep case and punctuation (for the strict check)."""
    return _WS_RE.sub(" ", text.strip())


# --- per-field comparisons ---------------------------------------------------


def _compare_fuzzy(field: str, expected, extracted) -> FieldResult:
    exp, ext = _clean(expected), _clean(extracted)
    if not ext:
        return FieldResult(field, exp, "", Status.NEEDS_REVIEW,
                           "No value could be read from the label — please review manually.")
    if not exp:
        return FieldResult(field, "", ext, Status.NEEDS_REVIEW,
                           "No expected value was provided to compare against.")

    if _normalize(exp) == _normalize(ext):
        if exp != ext:
            return FieldResult(field, exp, ext, Status.PASS,
                               "Match (minor differences in capitalization or punctuation).")
        return FieldResult(field, exp, ext, Status.PASS, "Exact match.")

    score = fuzz.token_sort_ratio(_normalize(exp), _normalize(ext))
    if score >= 90:
        return FieldResult(field, exp, ext, Status.WARNING,
                           f"Close match ({score:.0f}% similar) — likely the same value, please confirm.")
    return FieldResult(field, exp, ext, Status.FAIL,
                       f"Does not match (only {score:.0f}% similar).")


_ABV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PROOF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*proof", re.IGNORECASE)


def _parse_alcohol(text: str):
    """Return (abv_percent, proof) as floats; either may be None."""
    abv = proof = None
    m = _ABV_RE.search(text)
    if m:
        abv = float(m.group(1))
    m = _PROOF_RE.search(text)
    if m:
        proof = float(m.group(1))
    return abv, proof


def _compare_alcohol(field: str, expected, extracted) -> FieldResult:
    exp, ext = _clean(expected), _clean(extracted)
    if not ext:
        return FieldResult(field, exp, "", Status.NEEDS_REVIEW,
                           "No alcohol content could be read from the label — please review manually.")

    exp_abv, exp_proof = _parse_alcohol(exp)
    ext_abv, ext_proof = _parse_alcohol(ext)

    if ext_abv is None and ext_proof is None:
        return FieldResult(field, exp, ext, Status.NEEDS_REVIEW,
                           "Could not interpret the alcohol content on the label — please review manually.")

    # Prefer comparing ABV percentages when both are available.
    if exp_abv is not None and ext_abv is not None:
        if abs(exp_abv - ext_abv) <= 0.1:
            if ext_proof is not None and abs(ext_proof - ext_abv * 2) > 0.6:
                return FieldResult(field, exp, ext, Status.WARNING,
                                   "ABV matches, but the proof printed on the label is inconsistent with the ABV.")
            return FieldResult(field, exp, ext, Status.PASS, "Alcohol content matches.")
        return FieldResult(field, exp, ext, Status.FAIL,
                           f"Alcohol content differs (expected {exp_abv}%, label shows {ext_abv}%).")

    # Fall back to comparing proof values.
    if exp_proof is not None and ext_proof is not None:
        if abs(exp_proof - ext_proof) <= 0.6:
            return FieldResult(field, exp, ext, Status.PASS, "Proof matches.")
        return FieldResult(field, exp, ext, Status.FAIL,
                           f"Proof differs (expected {exp_proof}, label shows {ext_proof}).")

    return FieldResult(field, exp, ext, Status.NEEDS_REVIEW,
                       "Alcohol content could not be compared automatically — please review manually.")


_UNIT_TO_ML = {
    "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0, "millilitre": 1.0, "millilitres": 1.0,
    "cl": 10.0, "centiliter": 10.0, "centiliters": 10.0,
    "l": 1000.0, "liter": 1000.0, "liters": 1000.0, "litre": 1000.0, "litres": 1000.0,
    "floz": 29.5735, "oz": 29.5735,
}
_VOL_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(ml|milliliters?|millilitres?|cl|centiliters?|l|liters?|litres?|fl\.?\s*oz|oz)",
    re.IGNORECASE,
)


def _parse_volume_ml(text: str):
    """Return volume in mL, or None if no recognizable quantity+unit is found."""
    m = _VOL_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower().replace(".", "").replace(" ", "")
    factor = _UNIT_TO_ML.get(unit)
    return value * factor if factor else None


def _compare_net_contents(field: str, expected, extracted) -> FieldResult:
    exp, ext = _clean(expected), _clean(extracted)
    if not ext:
        return FieldResult(field, exp, "", Status.NEEDS_REVIEW,
                           "No net contents could be read from the label — please review manually.")

    exp_ml, ext_ml = _parse_volume_ml(exp), _parse_volume_ml(ext)
    if ext_ml is None or exp_ml is None:
        return FieldResult(field, exp, ext, Status.NEEDS_REVIEW,
                           "Net contents could not be parsed into a comparable quantity — please review manually.")

    if abs(exp_ml - ext_ml) <= max(0.01 * exp_ml, 0.5):  # ~1% tolerance
        return FieldResult(field, exp, ext, Status.PASS, "Net contents match.")
    return FieldResult(field, exp, ext, Status.FAIL,
                       f"Net contents differ (expected ~{exp_ml:.0f} mL, label shows ~{ext_ml:.0f} mL).")


def _compare_warning(field: str, expected, extracted) -> FieldResult:
    exp, ext = _clean(expected), _clean(extracted)
    if not ext:
        return FieldResult(field, exp, "", Status.NEEDS_REVIEW,
                           "No government warning text was detected on the label — please review manually.")

    # Collapse whitespace (but keep case and punctuation) before any comparison.
    # Transcriptions routinely break the header across lines or double-space it;
    # the all-caps colon is what TTB requires, not the exact spacing around it.
    exp_norm, ext_norm = _collapse_ws(exp), _collapse_ws(ext)

    # Hard TTB rule: the header must be all-caps with a trailing colon.
    if "GOVERNMENT WARNING:" not in ext_norm:
        if "government warning" in ext_norm.lower():
            return FieldResult(field, exp, ext, Status.FAIL,
                               "'GOVERNMENT WARNING:' must appear in ALL CAPITAL LETTERS followed by a colon.")
        return FieldResult(field, exp, ext, Status.FAIL,
                           "The required 'GOVERNMENT WARNING:' statement was not found on the label.")

    if exp_norm == ext_norm:
        return FieldResult(field, exp, ext, Status.PASS,
                           "Government warning matches the required statement exactly.")
    # Required text present verbatim inside extra label copy; accept it.
    if exp_norm and exp_norm in ext_norm:
        return FieldResult(field, exp, ext, Status.PASS,
                           "Required warning statement is present verbatim (with additional surrounding text).")

    score = fuzz.ratio(exp_norm.lower(), ext_norm.lower())
    return FieldResult(field, exp, ext, Status.FAIL,
                       f"Government warning does not match the required statement word-for-word ({score:.0f}% similar).")


# The visual formatting of the warning (bold header, type size, placement,
# separation) can't be judged from a text transcription. The UI surfaces this as
# a manual-confirmation step rather than letting the model emit a false PASS on
# the most legally sensitive field.
VISUAL_FORMAT_NOTE = (
    "Wording and capitalization are verified automatically. A reviewer must still "
    "confirm the visual formatting by eye: bold 'GOVERNMENT WARNING:', legible "
    "type size, placement, and clear separation from other label information."
)


def warning_visual_format_result() -> FieldResult:
    """The visual-format manual-review item as a FieldResult (always NEEDS_REVIEW).

    Kept as a single source for the field name and note; the UI renders it as a
    confirmation checkbox rather than a comparison-grid row.
    """
    return FieldResult(
        field="Warning Visual Format",
        expected="",
        extracted="",
        status=Status.NEEDS_REVIEW,
        explanation=VISUAL_FORMAT_NOTE,
    )


def verify(expected: dict, extracted) -> list[FieldResult]:
    """Compare a dict of expected values against extracted `LabelFields`.

    `expected` keys: brand_name, class_type, alcohol_content, net_contents,
    government_warning. `extracted` is anything with those attributes.
    """
    return [
        _compare_fuzzy("Brand Name", expected.get("brand_name"), extracted.brand_name),
        _compare_fuzzy("Class/Type", expected.get("class_type"), extracted.class_type),
        _compare_alcohol("Alcohol Content", expected.get("alcohol_content"), extracted.alcohol_content),
        _compare_net_contents("Net Contents", expected.get("net_contents"), extracted.net_contents),
        _compare_warning("Government Warning", expected.get("government_warning"), extracted.government_warning),
    ]
