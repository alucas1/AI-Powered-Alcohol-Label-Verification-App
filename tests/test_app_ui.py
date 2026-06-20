"""End-to-end UI tests driving the real Streamlit app with AppTest.

These exercise app.py itself: the demo batch path, the result tables, and the
flow that the unit tests can't reach, namely how a reviewer's visual-format
checkboxes and per-field overrides feed the downloadable results CSV.

Two boundaries are faked so the tests stay deterministic and offline:
  - `label_ai.OpenAI` returns canned label fields instead of calling the model.
  - `st.download_button` is intercepted to capture the CSV bytes the app builds
    from session state, which is otherwise served over a URL and not readable.

The file uploader cannot be driven by AppTest, so the batch is started through
the **Run demo files** button, which reads the bundled sample set directly. The
canned extraction returns the OLD TOM DISTILLERY values, so the old_tom sample
(whose CSV row carries those same values) verifies as a clean all-PASS label and
the others vary; the assertions below lean on the deterministic old_tom row.
"""

import csv as csvlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from batch import RESULTS_CSV_COLUMNS
from verifier import STANDARD_WARNING

APP = str(Path(__file__).resolve().parents[1] / "app" / "app.py")

# Canned extraction: the OLD TOM label, verbatim against its CSV row.
OLD_TOM = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "government_warning": STANDARD_WARNING,
}

# Sample order is fixed by app.py; old_tom is first.
OLD_TOM_FILE = "old_tom_distillery.png"
SAMPLE_COUNT = 4
FIELDS_PER_LABEL = 5  # brand, class, alcohol, net contents, warning


def _fake_client(payload):
    def factory(*args, **kwargs):
        client = MagicMock()
        message = MagicMock()
        message.content = json.dumps(payload)
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response
        return client

    return factory


class Driver:
    """Holds the patched app and captures the CSV the download button is given."""

    def __init__(self, payload=OLD_TOM):
        self.captured = {}
        self._patches = [
            patch("label_ai.OpenAI", side_effect=_fake_client(payload)),
            patch("streamlit.download_button", self._capture),
        ]
        for p in self._patches:
            p.start()
        self.at = AppTest.from_file(APP, default_timeout=60)

    def _capture(self, label, *args, **kwargs):
        self.captured["data"] = kwargs.get("data")
        return False

    def stop(self):
        for p in self._patches:
            p.stop()

    def run_demo(self):
        self.at.run()
        # Button 0 is "Run demo files"; button 1 is the form's "Verify Labels".
        self.at.button[0].click().run()
        return self

    def csv_rows(self):
        assert "data" in self.captured, "the download button was never rendered"
        return list(csvlib.DictReader(io.StringIO(self.captured["data"])))

    def rows_for(self, filename):
        return [r for r in self.csv_rows() if r["filename"] == filename]

    def manual_status(self, filename):
        rows = self.rows_for(filename)
        manual = [r for r in rows if r["field"] == "Manual Visual Format Review"]
        assert len(manual) == 1
        return manual[0]["status"]


@pytest.fixture
def app():
    driver = Driver()
    yield driver
    driver.stop()


# --- the app renders and runs a batch ----------------------------------------


def test_app_loads_without_error(app):
    app.at.run()
    assert not app.at.exception
    assert [b.label for b in app.at.button] == ["Run demo files", "Verify Labels"]


def test_demo_run_verifies_every_sample(app):
    app.run_demo()
    assert not app.at.exception
    batch = app.at.session_state["batch"]
    assert len(batch) == SAMPLE_COUNT
    assert all("results" in entry for entry in batch)


def test_download_csv_shape(app):
    app.run_demo()
    rows = app.csv_rows()
    # Header matches the export contract.
    header = app.captured["data"].splitlines()[0].split(",")
    assert header == RESULTS_CSV_COLUMNS
    # Each label contributes its fields plus one manual-review row.
    assert len(rows) == SAMPLE_COUNT * (FIELDS_PER_LABEL + 1)
    assert sum(r["field"] == "Manual Visual Format Review" for r in rows) == SAMPLE_COUNT


def test_old_tom_label_passes_every_field(app):
    app.run_demo()
    field_rows = [
        r for r in app.rows_for(OLD_TOM_FILE) if r["field"] != "Manual Visual Format Review"
    ]
    assert len(field_rows) == FIELDS_PER_LABEL
    assert {r["status"] for r in field_rows} == {"PASS"}


# --- CSV accuracy as the reviewer ticks checkboxes ---------------------------


def test_visual_format_checkbox_flips_only_its_own_manual_row(app):
    app.run_demo()
    # Every manual row starts unconfirmed.
    assert app.manual_status(OLD_TOM_FILE) == "NO"

    app.at.checkbox(key="vf_confirm_0").check().run()
    statuses = {
        r["filename"]: r["status"]
        for r in app.csv_rows()
        if r["field"] == "Manual Visual Format Review"
    }
    assert statuses[OLD_TOM_FILE] == "YES"
    assert sum(v == "YES" for v in statuses.values()) == 1  # the others stay NO


def test_unchecking_reverts_the_manual_row(app):
    app.run_demo()
    app.at.checkbox(key="vf_confirm_0").check().run()
    assert app.manual_status(OLD_TOM_FILE) == "YES"
    app.at.checkbox(key="vf_confirm_0").uncheck().run()
    assert app.manual_status(OLD_TOM_FILE) == "NO"


def test_override_sets_manual_status_and_reason(app):
    app.run_demo()
    app.at.checkbox(key="ovr_on_0_0").check().run()  # override Brand Name on old_tom
    app.at.radio(key="ovr_status_0_0").set_value("FAIL").run()
    app.at.text_input(key="ovr_reason_0_0").set_value("glare on the brand").run()

    brand = next(r for r in app.rows_for(OLD_TOM_FILE) if r["field"] == "Brand Name")
    assert brand["status"] == "FAIL (manual)"
    assert brand["explanation"] == "glare on the brand"


def test_override_without_reason_uses_a_default_explanation(app):
    app.run_demo()
    app.at.checkbox(key="ovr_on_0_0").check().run()
    # Leave the result at its default (PASS) and the reason blank.
    brand = next(r for r in app.rows_for(OLD_TOM_FILE) if r["field"] == "Brand Name")
    assert brand["status"] == "PASS (manual)"
    assert brand["explanation"] == "Manually passed."


def test_confirmation_and_override_are_independent(app):
    app.run_demo()
    app.at.checkbox(key="vf_confirm_0").check().run()       # confirm old_tom's format
    app.at.checkbox(key="ovr_on_0_2").check().run()         # override old_tom's alcohol
    app.at.radio(key="ovr_status_0_2").set_value("FAIL").run()

    rows = app.rows_for(OLD_TOM_FILE)
    alcohol = next(r for r in rows if r["field"] == "Alcohol Content")
    brand = next(r for r in rows if r["field"] == "Brand Name")
    assert app.manual_status(OLD_TOM_FILE) == "YES"
    assert alcohol["status"] == "FAIL (manual)"
    assert brand["status"] == "PASS"  # an untouched field keeps its automated verdict


def test_new_run_resets_confirmations_and_overrides(app):
    app.run_demo()
    app.at.checkbox(key="vf_confirm_0").check().run()
    app.at.checkbox(key="ovr_on_0_0").check().run()
    app.at.radio(key="ovr_status_0_0").set_value("FAIL").run()
    assert app.manual_status(OLD_TOM_FILE) == "YES"

    # Re-running the demo must start clean, not inherit the prior ticks.
    app.at.button[0].click().run()
    assert app.manual_status(OLD_TOM_FILE) == "NO"
    brand = next(r for r in app.rows_for(OLD_TOM_FILE) if r["field"] == "Brand Name")
    assert brand["status"] == "PASS"
