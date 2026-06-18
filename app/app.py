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

from label_ai import ExtractionError, MissingAPIKeyError, extract_label_fields
from verifier import STANDARD_WARNING, FieldResult, Status, verify

st.set_page_config(page_title="Alcohol Label Verification", layout="wide")

# Per-status display label and result-table cell colour.
_STATUS_LABEL = {
    Status.PASS: "PASS",
    Status.WARNING: "WARNING",
    Status.FAIL: "FAIL",
    Status.NEEDS_REVIEW: "NEEDS REVIEW",
}
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
    return "All checks passed.", "success"


def _render_table(results: list[FieldResult]) -> None:
    df = pd.DataFrame(
        [
            {
                "Field": r.field,
                "Expected": r.expected or "—",
                "Extracted from Label": r.extracted or "—",
                "Status": _STATUS_LABEL[r.status],
                "Explanation": r.explanation,
            }
            for r in results
        ]
    )

    def _color(val):
        return _STATUS_COLOR.get(val, "")

    styler = df.style.map(_color, subset=["Status"])
    st.dataframe(styler, hide_index=True, use_container_width=True)


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

    submitted = st.form_submit_button("Verify Labels", type="primary", use_container_width=True)

# --- Processing --------------------------------------------------------------

if submitted:
    if not uploaded_files:
        st.warning("Please upload at least one label image to verify.")
        st.stop()

    expected = {
        "brand_name": brand_name,
        "class_type": class_type,
        "alcohol_content": alcohol_content,
        "net_contents": net_contents,
        "government_warning": government_warning,
    }

    st.subheader(f"Results ({len(uploaded_files)} label{'s' if len(uploaded_files) != 1 else ''})")

    for uploaded in uploaded_files:
        st.divider()
        st.markdown(f"#### {uploaded.name}")
        image_col, result_col = st.columns([1, 2])

        with image_col:
            st.image(uploaded.getvalue(), use_container_width=True)

        with result_col:
            try:
                start = time.perf_counter()
                with st.spinner("Reading label…"):
                    extracted = extract_label_fields(uploaded.getvalue())
                elapsed = time.perf_counter() - start
            except MissingAPIKeyError:
                st.error(
                    "**No OpenAI API key configured.** Add `OPENAI_API_KEY` to "
                    "`.streamlit/secrets.toml` (local) or the app's **Secrets** "
                    "settings (Streamlit Cloud), then try again."
                )
                st.stop()
            except ExtractionError as exc:
                st.error(f"Could not verify **{uploaded.name}**: {exc}")
                continue

            headline, banner = _overall(results := verify(expected, extracted))
            getattr(st, banner)(headline)
            st.caption(f"Processed in {elapsed:.1f} seconds")
            _render_table(results)
