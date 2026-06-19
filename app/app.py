"""Streamlit UI and orchestration.

A reviewer enters the expected application values, uploads one or more label
images, and submits. Each image is run through the vision model and the
comparison, then rendered as a colour-coded result table with its processing
time. Images stay in memory; nothing is persisted.
"""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from batch import expected_for, load_expected_csv, missing_fields, results_to_csv
from label_ai import ExtractionError, MissingAPIKeyError, extract_label_fields
from verifier import (
    STANDARD_WARNING,
    STATUS_LABEL,
    FieldResult,
    Status,
    verify,
    warning_visual_format_result,
)

st.set_page_config(page_title="Alcohol Label Verification", layout="wide")

# Stakeholder target: a reviewer should get a result back in about this long.
# Slower labels still render; they're flagged so the lag is visible, not hidden.
LATENCY_TARGET_SECONDS = 5.0

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
    # Never claim a blanket pass: the warning's visual formatting is always a
    # manual check (see the Warning Visual Format row), so say so here too.
    return "Automated checks passed — confirm the warning's visual format by eye.", "success"


def _render_table(results: list[FieldResult]) -> None:
    df = pd.DataFrame(
        [
            {
                "Field": r.field,
                "Expected": r.expected or "—",
                "Extracted from Label": r.extracted or "—",
                "Status": STATUS_LABEL[r.status],
                "Explanation": r.explanation,
            }
            for r in results
        ]
    )

    def _color(val):
        return _STATUS_COLOR.get(val, "")

    styler = df.style.map(_color, subset=["Status"])
    st.dataframe(styler, hide_index=True, width="stretch")


# --- Header ------------------------------------------------------------------

st.title("Alcohol Label Verification")
st.write(
    "Enter the values from the application, upload the label image(s), and click "
    "**Verify Labels**. The app reads each label and checks it against what you entered."
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

if submitted:
    st.session_state.pop("batch", None)  # drop any previous run's results

    if not uploaded_files:
        st.warning("Please upload at least one label image to verify.")
        st.stop()

    shared_expected = {
        "brand_name": brand_name,
        "class_type": class_type,
        "alcohol_content": alcohol_content,
        "net_contents": net_contents,
        "government_warning": government_warning,
    }

    # CSV mode supplies expected values per file; otherwise the form values
    # above apply to every image. Validation differs: in CSV mode each row is
    # checked as its file is processed, so a blank form is fine.
    csv_map = None
    if csv_file is not None:
        try:
            csv_map = load_expected_csv(csv_file.getvalue())
        except ValueError as exc:
            st.error(f"Could not read the expected-values CSV: {exc}")
            st.stop()
    else:
        missing = missing_fields(shared_expected)
        if missing:
            st.warning(
                "Please fill in all required expected values before verifying. "
                "Missing: " + ", ".join(missing) + "."
            )
            st.stop()

    batch = []
    for uploaded in uploaded_files:
        entry = {"name": uploaded.name, "image": uploaded.getvalue()}

        if csv_map is not None:
            expected = expected_for(uploaded.name, csv_map)
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
            with st.spinner(f"Reading {uploaded.name}…"):
                extracted = extract_label_fields(uploaded.getvalue())
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

    for entry in batch:
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

            # Overall banner reflects the automated field comparisons; the
            # visual-format row is appended as a standing manual-review item.
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

            _render_table(results + [warning_visual_format_result()])

    # One combined CSV of every verified label; skipped/errored files are left out.
    verified = [
        (entry["name"], entry["results"] + [warning_visual_format_result()])
        for entry in batch
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
