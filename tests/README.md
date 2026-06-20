# Tests

The suite pins the deterministic behavior the app depends on: the field
comparisons, the CSV and validation rules, the concurrent batch orchestration,
and the provider retry/backoff. It runs offline by default. The one component
that genuinely needs the network, the live vision call, is gated behind a
precheck and skips cleanly when no key is configured, so a fresh checkout stays
green without credentials.

## Running

```bash
pip install -r requirements-dev.txt   # from the repo root
pytest
```

`pytest.ini` sets `pythonpath = app` and `testpaths = tests`, so the modules
under `app/` import by name (`from verifier import ...`) and `pytest` needs no
arguments. Run a single file or test with the usual selectors:

```bash
pytest tests/test_warning.py
pytest tests/test_pipeline.py -k concurrency
```

## Layout

Each file targets one module or concern, mirroring the separation in `app/`.

| File | Covers |
|---|---|
| `test_text_fields.py` | Fuzzy comparison for Brand Name and Class/Type (case, punctuation, spacing). |
| `test_alcohol.py` | Alcohol content: ABV and proof parsing, numeric comparison, the proof-vs-ABV sanity check, and European decimal formats. |
| `test_net_contents.py` | Net contents: quantity and unit parsing normalized to milliliters, with tolerance. |
| `test_warning.py` | Strict government warning check: all-caps header, verbatim wording, whitespace handling. |
| `test_warning_visual_format.py` | The standing manual-review row for warning visual formatting. |
| `test_validation.py` | Required-field validation that gates the model call before any request is spent. |
| `test_batch.py` | Per-file CSV loading, filename matching, and the results-export CSV. |
| `test_verify.py` | The top-level `verify()` entry point that assembles the per-field results. |
| `test_pipeline.py` | Concurrent batch orchestration: input ordering, the worker bound, skips, per-file error isolation, progress, and the batch-fatal abort path. |
| `test_retry.py` | The provider retry/backoff schedule: which errors retry, the attempt budget, `Retry-After` handling, and the jittered backoff bounds. |
| `test_sample_files.py` | End-to-end checks over the bundled sample set in `test_files/`, including the live round-trip (see below). |

`make_sample_labels.py` is a development utility, not a test. It renders a
synthetic label PNG to `sample_labels/` (gitignored) so the app can be exercised
without real artwork; `pytest` does not collect it.

## Determinism and isolation

The orchestration and retry tests never touch the network or wall-clock time.
`test_pipeline.py` passes a fake `extract` into `process_batch`, and asserts real
concurrency with a `threading.Barrier` (proving more than one extraction is in
flight) and a peak-counter (proving the worker cap holds). `test_retry.py` injects
the `sleep` function so the backoff schedule is exercised instantly, and raises
the real OpenAI error types so the retryable/non-retryable split is tested against
the same classes the client throws in production.

## The live test

`test_sample_files.py` includes a live, end-to-end check that sends the sample
images to the configured model and verifies the results. A module-scoped fixture
prechecks the credentials with a single `models.retrieve` call: if the key is
missing or invalid, the model is unreachable, or there is no network, the live
tests **skip** with the reason rather than fail. They run by default when a usable
key is present.

These calls hit the real API, cost money, and vary slightly between runs (OCR,
line breaks, and capitalization drift), so each field is asserted by intent rather
than by an exact status, and a single failure is worth a re-run before treating it
as a regression. To opt out even when a valid key is configured:

```bash
SKIP_LIVE_EXTRACTION=1 pytest
```
