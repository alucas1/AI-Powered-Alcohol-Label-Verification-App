"""Concurrent batch orchestration.

Sits between the Streamlit UI and the per-label work (extract + verify) so the
fan-out logic stays free of UI and unit-testable. The heavy step,
`label_ai.extract_label_fields`, is network-bound: each label waits on the
provider far longer than it spends on CPU, so a bounded thread pool overlaps
those waits and a batch of 200 labels no longer takes 200x a single label.

Threads, not processes, because the work is I/O-bound and the payload (image
bytes, results) crosses the boundary cheaply in-process. The worker cap is
deliberately small; pushing more concurrent requests mostly buys rate-limit
errors, which the provider client already absorbs with retry/backoff.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from batch import expected_for, missing_fields
from label_ai import ExtractionError, extract_label_fields
from verifier import verify

# Default ceiling on in-flight extractions. Small on purpose: enough to hide the
# per-label network latency on a big batch, low enough to stay clear of the
# provider's rate limit. Each worker holds one image and one round-trip.
MAX_WORKERS = 5

# An entry is the unit the UI renders, one per uploaded file. It always carries
# "name" and "image", then exactly one outcome:
#   "skip" (str):            no usable expected values, so the model is never called
#   "error" (str):           extraction failed for this file
#   "results" + "elapsed":   verified comparison rows and the processing time
Entry = dict

# Progress callback: (completed, total, last_filename) -> None. Invoked once per
# entry as it lands, on the calling thread, so a UI can update safely.
ProgressFn = Callable[[int, int, str], None]


def resolve_expected(
    name: str, csv_map: Optional[dict], shared_expected: Optional[dict]
) -> tuple[Optional[dict], Optional[str]]:
    """Decide which expected values a file is checked against.

    Returns ``(expected, None)`` when there are usable values, or
    ``(None, skip_reason)`` when the file should be skipped without calling the
    model. With no CSV the shared form values apply to every file; with a CSV
    each file is matched by filename, and a missing or incomplete row is a skip
    rather than a guess.
    """
    if csv_map is None:
        return shared_expected, None

    expected = expected_for(name, csv_map)
    if expected is None:
        return None, (
            "No row for this file in the CSV, so it was skipped. Add a row with "
            "this exact filename, or remove the CSV to use the shared values."
        )
    row_missing = missing_fields(expected)
    if row_missing:
        return None, "Skipped: the CSV row is missing " + ", ".join(row_missing) + "."
    return expected, None


def process_one(
    name: str,
    data: bytes,
    expected: dict,
    *,
    extract: Callable[[bytes], object] = extract_label_fields,
) -> Entry:
    """Extract one label and verify it, timing the model round-trip.

    Returns a completed entry. An `ExtractionError` (unreadable image, bad
    response) is captured on the entry so one bad file can't sink the batch;
    `MissingAPIKeyError` deliberately propagates, since it dooms every file and
    the caller should stop the whole run.
    """
    entry: Entry = {"name": name, "image": data}
    try:
        start = time.perf_counter()
        extracted = extract(data)
        entry["elapsed"] = time.perf_counter() - start
    except ExtractionError as exc:
        entry["error"] = str(exc)
        return entry
    entry["results"] = verify(expected, extracted)
    return entry


def process_batch(
    files: list[tuple[str, bytes]],
    *,
    csv_map: Optional[dict] = None,
    shared_expected: Optional[dict] = None,
    extract: Callable[[bytes], object] = extract_label_fields,
    max_workers: int = MAX_WORKERS,
    on_progress: Optional[ProgressFn] = None,
) -> list[Entry]:
    """Verify a batch of labels concurrently, preserving input order.

    Files needing the model are extracted across a thread pool of at most
    ``max_workers``; files with no usable expected values are skipped up front
    and never submitted. The returned list is in the same order as ``files``
    regardless of completion order, so the UI lines up with the upload.

    ``on_progress`` (if given) fires once per file as it finishes, skips
    included, with the running count, which is useful for a live status line.
    ``MissingAPIKeyError`` from any file aborts the batch and propagates; the
    pending work is cancelled rather than left to fail one by one.
    """
    total = len(files)
    entries: list[Optional[Entry]] = [None] * total
    done = 0

    def _record(idx: int, entry: Entry) -> None:
        nonlocal done
        entries[idx] = entry
        done += 1
        if on_progress is not None:
            on_progress(done, total, entry["name"])

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {}
        for idx, (name, data) in enumerate(files):
            expected, skip = resolve_expected(name, csv_map, shared_expected)
            if skip is not None:
                _record(idx, {"name": name, "image": data, "skip": skip})
            else:
                futures[pool.submit(process_one, name, data, expected, extract=extract)] = idx

        for future in as_completed(futures):
            try:
                entry = future.result()
            except Exception:
                # A batch-fatal failure (e.g. MissingAPIKeyError): stop spending
                # calls on work that will fail identically and surface it.
                for pending in futures:
                    pending.cancel()
                raise
            _record(futures[future], entry)

    return entries  # type: ignore[return-value]  # every slot is filled by here
