"""Concurrent batch orchestration (pipeline.py).

The model call is replaced with fakes, so these tests are deterministic and
offline: they pin the order, concurrency bound, skip/error handling, and progress
reporting of the fan-out without touching the network.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from label_ai import ExtractionError, MissingAPIKeyError
from pipeline import process_batch, process_one, resolve_expected
from verifier import STANDARD_WARNING, Status

# A complete set of expected values, plus a matching fake extraction, so verify()
# returns all-PASS and the assertions can focus on orchestration, not comparison.
EXPECTED = {
    "brand_name": "Old Tom Distillery",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "government_warning": STANDARD_WARNING,
}


def _ok_extract(data):
    return SimpleNamespace(**EXPECTED)


def _files(n):
    return [(f"f{i}.png", bytes([i])) for i in range(n)]


# --- resolve_expected ---------------------------------------------------------


def test_resolve_uses_shared_values_without_a_csv():
    assert resolve_expected("anything.png", None, EXPECTED) == (EXPECTED, None)


def test_resolve_matches_csv_row_by_filename():
    csv_map = {"a.png": EXPECTED}
    assert resolve_expected("A.PNG", csv_map, None) == (EXPECTED, None)


def test_resolve_skips_file_with_no_csv_row():
    expected, skip = resolve_expected("missing.png", {"a.png": EXPECTED}, None)
    assert expected is None
    assert "No row for this file" in skip


def test_resolve_skips_csv_row_missing_a_required_field():
    csv_map = {"a.png": {**EXPECTED, "net_contents": ""}}
    expected, skip = resolve_expected("a.png", csv_map, None)
    assert expected is None
    assert "Net Contents" in skip


# --- process_one --------------------------------------------------------------


def test_process_one_verifies_and_times():
    entry = process_one("f.png", b"x", EXPECTED, extract=_ok_extract)
    assert entry["name"] == "f.png"
    assert "elapsed" in entry and entry["elapsed"] >= 0
    assert {r.status for r in entry["results"]} == {Status.PASS}


def test_process_one_captures_extraction_error():
    def boom(data):
        raise ExtractionError("unreadable")

    entry = process_one("f.png", b"x", EXPECTED, extract=boom)
    assert entry["error"] == "unreadable"
    assert "results" not in entry


def test_process_one_propagates_missing_api_key():
    def no_key(data):
        raise MissingAPIKeyError("no key")

    with pytest.raises(MissingAPIKeyError):
        process_one("f.png", b"x", EXPECTED, extract=no_key)


# --- process_batch: ordering and concurrency ----------------------------------


def test_preserves_input_order_regardless_of_completion_order():
    # Later files finish first (descending sleep), so a correct result list still
    # has to be reordered back to the input sequence.
    def extract(data):
        time.sleep((5 - data[0]) * 0.005)
        return SimpleNamespace(**EXPECTED)

    batch = process_batch(_files(5), shared_expected=EXPECTED, extract=extract, max_workers=5)
    assert [e["name"] for e in batch] == [f"f{i}.png" for i in range(5)]


def test_runs_files_concurrently():
    # A barrier of three only releases if three extractions are in flight at once;
    # a sequential implementation would block on the first and time out.
    barrier = threading.Barrier(3, timeout=5)

    def extract(data):
        barrier.wait()
        return SimpleNamespace(**EXPECTED)

    batch = process_batch(_files(3), shared_expected=EXPECTED, extract=extract, max_workers=3)
    assert all("results" in e for e in batch)


def test_concurrency_never_exceeds_max_workers():
    active = 0
    peak = 0
    lock = threading.Lock()

    def extract(data):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return SimpleNamespace(**EXPECTED)

    process_batch(_files(12), shared_expected=EXPECTED, extract=extract, max_workers=3)
    assert peak <= 3  # the cap held
    assert peak >= 2  # and work genuinely overlapped


# --- process_batch: skips, errors, progress -----------------------------------


def test_skips_are_recorded_and_never_extracted():
    csv_map = {"f0.png": EXPECTED}  # only the first file has a row
    extracted = []

    def extract(data):
        extracted.append(data)
        return SimpleNamespace(**EXPECTED)

    batch = process_batch(_files(3), csv_map=csv_map, extract=extract, max_workers=5)
    assert "results" in batch[0]
    assert "skip" in batch[1] and "skip" in batch[2]
    assert extracted == [bytes([0])]  # only the matched file hit the model


def test_extraction_errors_are_isolated_per_file():
    def extract(data):
        if data == bytes([1]):
            raise ExtractionError("bad image")
        return SimpleNamespace(**EXPECTED)

    batch = process_batch(_files(3), shared_expected=EXPECTED, extract=extract, max_workers=5)
    assert "results" in batch[0]
    assert batch[1]["error"] == "bad image"
    assert "results" in batch[2]


def test_missing_api_key_aborts_the_batch():
    def extract(data):
        raise MissingAPIKeyError("no key")

    with pytest.raises(MissingAPIKeyError):
        process_batch(_files(4), shared_expected=EXPECTED, extract=extract, max_workers=2)


def test_progress_is_reported_once_per_file():
    seen = []
    process_batch(
        _files(4),
        shared_expected=EXPECTED,
        extract=_ok_extract,
        max_workers=2,
        on_progress=lambda done, total, name: seen.append((done, total, name)),
    )
    assert [d for d, _, _ in seen] == [1, 2, 3, 4]  # monotonic running count
    assert all(t == 4 for _, t, _ in seen)


def test_empty_batch_returns_empty_list():
    assert process_batch([], shared_expected=EXPECTED, extract=_ok_extract) == []
