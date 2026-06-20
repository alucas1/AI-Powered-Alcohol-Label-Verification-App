"""Streamlit UI and orchestration.

A reviewer enters the expected application values, uploads one or more label
images, and submits. Each image is run through the vision model and the
comparison, then rendered as a colour-coded result table with its processing
time. Images stay in memory; nothing is persisted.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from batch import load_expected_csv, missing_fields, results_to_csv
from label_ai import MissingAPIKeyError
from pipeline import MAX_WORKERS, process_batch
from verifier import (
    STANDARD_WARNING,
    STATUS_LABEL,
    VISUAL_FORMAT_NOTE,
    FieldResult,
    Status,
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
        return "Issues found. This label does not match the application.", "error"
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


def _render_table(results: list[FieldResult], overrides: dict[int, tuple[str, str]] | None = None) -> None:
    """Render the comparison grid. `overrides` maps a result index to a
    (status, explanation) pair that replaces the automated verdict for that
    field; overridden statuses read e.g. "PASS (manual)"."""
    overrides = overrides or {}
    rows = []
    for j, r in enumerate(results):
        status, explanation = overrides.get(j, (STATUS_LABEL[r.status], r.explanation))
        rows.append(
            {
                "Field": r.field,
                "Expected": _truncate(r.expected) or "(none)",
                "Extracted from Label": _truncate(r.extracted) or "(none)",
                "Status": status,
                "Explanation": explanation,
            }
        )
    df = pd.DataFrame(rows)

    def _color(val):
        # Overridden cells read "PASS (manual)"/"FAIL (manual)"; colour by the base.
        return _STATUS_COLOR.get(val.replace(" (manual)", ""), "")

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
**What it does:** Reads each uploaded label and checks it, field by field,
against the expected values from the application.

**Steps**
1. Enter the expected values, or upload a per-file CSV for a batch of different labels.
2. Upload one or more label images (PNG or JPG).
3. Click **Verify Labels**.
4. Review each result. If you disagree with an automated check, use the
   **Overrides** panel to set the field to PASS or FAIL yourself (see below).

To see it in action, click **Run demo files** to verify four bundled sample
labels automatically.

**What the statuses mean**

| Status | Meaning |
|---|---|
| PASS | Matches the application. |
| WARNING | Minor difference, such as capitalization. Confirm by eye. |
| FAIL | Clear mismatch, or a formatting rule was broken. |
| NEEDS REVIEW | Could not be read (glare or angle). Review by hand. |

**Government warning:** The wording and capitalization are checked
automatically. Bold text, type size, placement, and separation from other copy
cannot be judged reliably from an image, so each result includes a check box for
you to confirm those by eye.

**Changing a result (overrides):** If you disagree with an automated check, open
the **Overrides** panel under that result, tick the field, and set it to PASS or
FAIL with an optional reason. Overridden fields are marked "(manual)" in the table
and the downloaded CSV. The summary at the top of each result always reflects the
automated checks, so an override never hides what was found.

**Results:** Each label shows its processing time (target: about five seconds).
After a batch, use **Download all results (CSV)** to export every field of every
label, along with your visual-format confirmations and any overrides.
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
        "government_warning`, one row per image. Each image is matched to its "
        "row by filename. Leave this empty to apply the values above to every image."
    )
    csv_file = st.file_uploader("Expected-values CSV", type=["csv"])

    submitted = st.form_submit_button("Verify Labels", type="primary", width="stretch")

# --- Processing: verify on submit, store the batch in session_state ----------
#
# Results are kept in session_state so they survive the rerun that a download
# click triggers, so the provider is never re-called just to redraw the page.

if submitted or run_demo:
    st.session_state.pop("batch", None)  # drop any previous run's results
    # Reset per-label manual inputs (visual-format confirmations and overrides)
    # so they don't carry over to a new batch.
    for k in [k for k in st.session_state if k.startswith(("vf_confirm_", "ovr_"))]:
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
    total = len(files)
    plural = "s" if total != 1 else ""
    # st.status gives an animated spinner (keeps moving while the calls block)
    # plus an updatable label. Labels are extracted concurrently (up to
    # MAX_WORKERS at once), so progress is reported as each one lands rather than
    # as a single in-flight file.
    with st.status(f"Processing {total} label{plural}…", expanded=False) as status:

        def _on_progress(done: int, total: int, name: str) -> None:
            status.update(label=f"Processed {done} of {total} (last: {name})")

        try:
            batch = process_batch(
                files,
                csv_map=csv_map,
                shared_expected=shared_expected,
                max_workers=MAX_WORKERS,
                on_progress=_on_progress,
            )
        except MissingAPIKeyError:
            status.update(label="Stopped: no API key configured", state="error")
            st.error(
                "**No OpenAI API key configured.** Add `OPENAI_API_KEY` to "
                "`.streamlit/secrets.toml` (local) or the app's **Secrets** "
                "settings (Streamlit Cloud), then try again."
            )
            st.stop()

        status.update(label=f"Verified {total} label{plural}", state="complete")

    st.session_state["batch"] = batch

# --- Results: render from session_state --------------------------------------

batch = st.session_state.get("batch")
if batch:
    count = len(batch)
    st.subheader(f"Results ({count} label{'s' if count != 1 else ''})")

    confirmations = {}  # entry index -> visual-format checkbox state
    overrides_by_entry = {}  # entry index -> {result index: (status, explanation)}
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

            # Manual overrides are read from session_state here; the widgets that
            # set them live in the "Overrides" panel below. A ticked field
            # replaces the automated verdict in the grid and the CSV.
            overrides = {}
            for j, r in enumerate(results):
                if st.session_state.get(f"ovr_on_{i}_{j}", False):
                    status = st.session_state.get(f"ovr_status_{i}_{j}", "PASS")
                    reason = str(st.session_state.get(f"ovr_reason_{i}_{j}", "")).strip()
                    explanation = reason or f"Manually {'passed' if status == 'PASS' else 'failed'}."
                    overrides[j] = (f"{status} (manual)", explanation)
            overrides_by_entry[i] = overrides

            _render_table(results, overrides)

            # The government warning is long; the grid shows a truncated cell, so
            # offer the full expected-vs-extracted text on demand.
            warning = next((r for r in results if r.field == "Government Warning"), None)
            if warning is not None:
                with st.expander("Show full government warning"):
                    st.markdown("**Expected**")
                    st.write(warning.expected or "(none)")
                    st.markdown("**Extracted from label**")
                    st.write(warning.extracted or "(none)")

            # Visual format can't be verified from a transcription. Surface it as
            # an explicit manual step the reviewer ticks off (not persisted).
            with st.container(border=True):
                st.markdown("**Visual format: manual check**")
                st.caption(VISUAL_FORMAT_NOTE)
                confirmations[i] = st.checkbox(
                    "I've confirmed the warning's visual formatting by eye.",
                    key=f"vf_confirm_{i}",
                )

            # Per-field manual overrides. These widgets write to session_state and
            # take effect on the next rerun, where they're read above the grid.
            #
            # The panel is always rendered: gating it behind a toggle tore the
            # widgets out of the tree whenever it closed, and Streamlit drops the
            # session_state for widgets that don't render on a given run, so a
            # field's status and reason were lost as soon as the panel collapsed.
            # The per-field checkbox stays mounted across reruns, so its Result and
            # Reason controls can appear only once it's ticked without losing the
            # checkbox state itself.
            with st.container(border=True):
                st.markdown("**Overrides**")
                st.caption(
                    "Manually set a field's result when you disagree with the "
                    'automated check. Overrides are marked "(manual)" in the grid '
                    "and the CSV; the banner keeps the automated verdict."
                )
                for j, r in enumerate(results):
                    on = st.checkbox(f"Override {r.field}", key=f"ovr_on_{i}_{j}")
                    if on:
                        oc1, oc2 = st.columns([1, 2])
                        with oc1:
                            st.radio(
                                "Result",
                                ["PASS", "FAIL"],
                                horizontal=True,
                                key=f"ovr_status_{i}_{j}",
                                label_visibility="collapsed",
                            )
                        with oc2:
                            st.text_input(
                                "Reason",
                                placeholder="Reason for override (optional)",
                                key=f"ovr_reason_{i}_{j}",
                                label_visibility="collapsed",
                            )

    # One combined CSV of every verified label, carrying each label's manual
    # visual-format confirmation. Built every rerun, so ticking a box updates the
    # download's contents. Skipped/errored files are left out.
    verified = [
        (entry["name"], entry["results"], confirmations.get(i, False), overrides_by_entry.get(i, {}))
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
