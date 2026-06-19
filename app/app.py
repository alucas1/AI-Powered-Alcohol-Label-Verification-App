"""Streamlit UI and orchestration.

A reviewer enters the expected application values, uploads one or more label
images, and submits. Each image is run through the vision model and the
comparison, then rendered as a colour-coded result table with its processing
time. Images stay in memory; nothing is persisted.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import streamlit as st

from batch import expected_for, load_expected_csv, missing_fields, results_to_csv
from label_ai import ExtractionError, MissingAPIKeyError, extract_label_fields
from verifier import (
    STANDARD_WARNING,
    STATUS_LABEL,
    VISUAL_FORMAT_NOTE,
    FieldResult,
    Status,
    verify,
)

st.set_page_config(page_title="Alcohol Label Verification", layout="wide")

# Stakeholder target: a reviewer should get a result back in about this long.
# Slower labels still render; they're flagged so the lag is visible, not hidden.
LATENCY_TARGET_SECONDS = 5.0

# Bundled sample set (committed under test_files/) for the "Run demo files"
# shortcut: four labels and the matching per-file CSV. Resolved from the repo
# root so it works whether run locally or on Streamlit Cloud.
_SAMPLES_DIR = Path(__file__).resolve().parents[1] / "test_files"
_SAMPLE_CSV = _SAMPLES_DIR / "sample_label_batch_test.csv"
_SAMPLE_IMAGES = [
    _SAMPLES_DIR / name
    for name in ("old_tom_distillery.png", "silver_coast.png", "stones_throw.png", "monarch_hill.png")
]
_SAMPLES_AVAILABLE = _SAMPLE_CSV.exists() and all(p.exists() for p in _SAMPLE_IMAGES)

# Result-table cell colour per status label (labels come from verifier.STATUS_LABEL).
_STATUS_COLOR = {
    "PASS": "background-color: #e6f4ea",
    "WARNING": "background-color: #fff4e5",
    "FAIL": "background-color: #fde8e8",
    "NEEDS REVIEW": "background-color: #e8f0fe",
}


def _overall(results: list[FieldResult]):
    """Worst status across fields, as a single (message, st-method) banner."""
    statuses = {r.status for r in results}
    if Status.FAIL in statuses:
        return "Issues found — this label does not match the application.", "error"
    if Status.NEEDS_REVIEW in statuses:
        return "Some fields need manual review.", "warning"
    if Status.WARNING in statuses:
        return "Passed with minor differences to confirm.", "warning"
    # Banner reports only the automated comparison. The warning's visual format
    # is a separate manual step, surfaced as its own confirmation below.
    return "Automated checks passed.", "success"


def _truncate(text: str, limit: int = 75) -> str:
    """Shorten long cell text for the grid; the full value lives elsewhere (the
    government warning has its own expander)."""
    text = str(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _render_table(results: list[FieldResult]) -> None:
    df = pd.DataFrame(
        [
            {
                "Field": r.field,
                "Expected": _truncate(r.expected) or "—",
                "Extracted from Label": _truncate(r.extracted) or "—",
                "Status": STATUS_LABEL[r.status],
                "Explanation": r.explanation,
            }
            for r in results
        ]
    )

    def _color(val):
        return _STATUS_COLOR.get(val, "")

    # st.table (not st.dataframe) so long explanations wrap onto multiple lines
    # and stay fully visible, instead of being truncated in a scrollable grid.
    styler = df.style.map(_color, subset=["Status"]).hide(axis="index")
    st.table(styler)


# --- Header ------------------------------------------------------------------

st.title("Alcohol Label Verification")
st.write(
    "Enter the values from the application, upload the label image(s), and click "
    "**Verify Labels**. The app reads each label and checks it against what you entered."
)

with st.expander("How to use this app"):
    st.markdown(
        """
**What it does** — Reads each uploaded label with a vision model and checks it,
field by field, against the expected values from the application.

**Steps**
1. Enter the expected values, or upload a per-file CSV for a batch of different labels.
2. Upload one or more label images (PNG or JPG).
3. Click **Verify Labels**.

To see it in action, click **Run demo files** to verify four bundled sample
labels automatically.

**What the statuses mean**

| Status | Meaning |
|---|---|
| PASS | Matches the application. |
| WARNING | Minor difference, such as capitalization — confirm by eye. |
| FAIL | Clear mismatch, or a formatting rule was broken. |
| NEEDS REVIEW | Could not be read (glare or angle) — review by hand. |

**Government warning** — The app verifies the wording and capitalization
automatically, but bold text, type size, placement, and separation from other
copy cannot be reliably judged from an image by AI. Each result includes a manual
check box to confirm those by eye.

**Results** — Each label shows its processing time (target: about five seconds).
After a batch, you can download every result as a single CSV.
        """
    )

run_demo = False
if _SAMPLES_AVAILABLE:
    run_demo = st.button(
        "Run demo files",
        help="Verify the four bundled sample labels using their matching CSV.",
    )

# --- Input form --------------------------------------------------------------

with st.form("verification_form"):
    st.subheader("1. Expected values (from the application)")
    c1, c2 = st.columns(2)
    with c1:
        brand_name = st.text_input("Brand Name", placeholder="OLD TOM DISTILLERY")
        alcohol_content = st.text_input("Alcohol Content", placeholder="45% Alc./Vol. (90 Proof)")
    with c2:
        class_type = st.text_input("Class / Type", placeholder="Kentucky Straight Bourbon Whiskey")
        net_contents = st.text_input("Net Contents", placeholder="750 mL")

    government_warning = st.text_area(
        "Government Warning (required statement)",
        value=STANDARD_WARNING,
        height=120,
        help="Pre-filled with the standard TTB health warning. Edit only if the application differs.",
    )

    st.subheader("2. Label image(s)")
    uploaded_files = st.file_uploader(
        "Upload one or more label images (PNG or JPG). Multiple files are processed as a batch.",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )

    st.subheader("3. Per-file expected values (optional)")
    st.caption(
        "Verifying many different labels at once? Upload a CSV with columns "
        "`filename, brand_name, class_type, alcohol_content, net_contents, "
        "government_warning` — one row per image. Each image is matched to its "
        "row by filename. Leave this empty to apply the values above to every image."
    )
    csv_file = st.file_uploader("Expected-values CSV", type=["csv"])

    submitted = st.form_submit_button("Verify Labels", type="primary", width="stretch")

# --- Processing: verify on submit, store the batch in session_state ----------
#
# Results are kept in session_state so they survive the rerun that a download
# click triggers — the AI provider is never re-called just to redraw the page.

if submitted or run_demo:
    st.session_state.pop("batch", None)  # drop any previous run's results
    # Reset per-label visual-format confirmations so they don't carry over.
    for k in [k for k in st.session_state if k.startswith("vf_confirm_")]:
        del st.session_state[k]

    # "Run demo files" verifies the bundled sample set via its CSV, no form input
    # needed. A normal submit uses whatever was entered and uploaded.
    use_samples = run_demo

    # Resolve the images to verify and the expected-value source (a per-file CSV
    # map, or the shared form values). CSV mode validates each row as it's
    # processed, so the form may be blank.
    shared_expected = None
    csv_map = None
    if use_samples:
        files = [(p.name, p.read_bytes()) for p in _SAMPLE_IMAGES]
        csv_map = load_expected_csv(_SAMPLE_CSV.read_bytes())
    else:
        if not uploaded_files:
            st.warning("Please upload at least one label image to verify.")
            st.stop()
        files = [(u.name, u.getvalue()) for u in uploaded_files]
        if csv_file is not None:
            try:
                csv_map = load_expected_csv(csv_file.getvalue())
            except ValueError as exc:
                st.error(f"Could not read the expected-values CSV: {exc}")
                st.stop()
        else:
            shared_expected = {
                "brand_name": brand_name,
                "class_type": class_type,
                "alcohol_content": alcohol_content,
                "net_contents": net_contents,
                "government_warning": government_warning,
            }
            missing = missing_fields(shared_expected)
            if missing:
                st.warning(
                    "Please fill in all required expected values before verifying. "
                    "Missing: " + ", ".join(missing) + "."
                )
                st.stop()

    batch = []
    for name, data in files:
        entry = {"name": name, "image": data}

        if csv_map is not None:
            expected = expected_for(name, csv_map)
            if expected is None:
                entry["skip"] = (
                    "No row for this file in the CSV — skipped. Add a row with this "
                    "exact filename, or remove the CSV to use the shared values."
                )
                batch.append(entry)
                continue
            row_missing = missing_fields(expected)
            if row_missing:
                entry["skip"] = "CSV row is missing " + ", ".join(row_missing) + " — skipped."
                batch.append(entry)
                continue
        else:
            expected = shared_expected

        try:
            start = time.perf_counter()
            with st.spinner(f"Reading {name}…"):
                extracted = extract_label_fields(data)
            entry["elapsed"] = time.perf_counter() - start
        except MissingAPIKeyError:
            st.error(
                "**No OpenAI API key configured.** Add `OPENAI_API_KEY` to "
                "`.streamlit/secrets.toml` (local) or the app's **Secrets** "
                "settings (Streamlit Cloud), then try again."
            )
            st.stop()
        except ExtractionError as exc:
            entry["error"] = str(exc)
            batch.append(entry)
            continue

        entry["results"] = verify(expected, extracted)
        batch.append(entry)

    st.session_state["batch"] = batch

# --- Results: render from session_state --------------------------------------

batch = st.session_state.get("batch")
if batch:
    count = len(batch)
    st.subheader(f"Results ({count} label{'s' if count != 1 else ''})")

    confirmations = {}  # entry index -> visual-format checkbox state
    for i, entry in enumerate(batch):
        st.divider()
        st.markdown(f"#### {entry['name']}")
        image_col, result_col = st.columns([1, 2])

        with image_col:
            # st.image decodes the bytes to render them and raises on anything
            # that isn't a real image (the uploader filters by extension, not
            # content). Guard it so one bad upload can't crash the whole page.
            try:
                st.image(entry["image"], width="stretch")
            except Exception:
                st.caption("Preview unavailable for this file.")

        with result_col:
            if "skip" in entry:
                st.warning(entry["skip"])
                continue
            if "error" in entry:
                st.error(f"Could not verify **{entry['name']}**: {entry['error']}")
                continue

            results = entry["results"]
            headline, banner = _overall(results)
            getattr(st, banner)(headline)

            elapsed = entry["elapsed"]
            st.caption(f"Processed in {elapsed:.1f} seconds")
            if elapsed > LATENCY_TARGET_SECONDS:
                st.warning(
                    f"This label took {elapsed:.1f}s, over the "
                    f"{LATENCY_TARGET_SECONDS:.0f}s target response time."
                )

            _render_table(results)

            # The government warning is long; the grid shows a truncated cell, so
            # offer the full expected-vs-extracted text on demand.
            warning = next((r for r in results if r.field == "Government Warning"), None)
            if warning is not None:
                with st.expander("Show full government warning"):
                    st.markdown("**Expected**")
                    st.write(warning.expected or "—")
                    st.markdown("**Extracted from label**")
                    st.write(warning.extracted or "—")

            # Visual format can't be verified from a transcription. Surface it as
            # an explicit manual step the reviewer ticks off (not persisted).
            with st.container(border=True):
                st.markdown("**Visual format — manual check**")
                st.caption(VISUAL_FORMAT_NOTE)
                confirmations[i] = st.checkbox(
                    "I've confirmed the warning's visual formatting by eye.",
                    key=f"vf_confirm_{i}",
                )

    # One combined CSV of every verified label, carrying each label's manual
    # visual-format confirmation. Built every rerun, so ticking a box updates the
    # download's contents. Skipped/errored files are left out.
    verified = [
        (entry["name"], entry["results"], confirmations.get(i, False))
        for i, entry in enumerate(batch)
        if "results" in entry
    ]
    if verified:
        st.divider()
        st.download_button(
            "Download all results (CSV)",
            data=results_to_csv(verified),
            file_name="label_verification_results.csv",
            mime="text/csv",
            width="stretch",
        )
