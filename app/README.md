# `app/` package

The application code. Five modules, layered so the parts that change for
different reasons stay apart: the UI, the one place that talks to the AI provider,
the batch orchestration, the CSV handling, and the comparison rules. Only `app.py`
imports Streamlit and only `label_ai.py` makes a network call, so the comparison
logic and the CSV handling can be tested in isolation and the provider can be
swapped without touching anything else.

Run the app from the repository root, not from here, because Streamlit resolves
`.streamlit/config.toml` and `secrets.toml` relative to the launch directory:

```bash
streamlit run app/app.py
```

Setup, configuration, deployment, and cost are covered in the [root
README](../README.md); the test layout is in [`tests/README.md`](../tests/README.md).

## Modules

| Module | Responsibility | Imports Streamlit | Makes network calls |
|---|---|---|---|
| `app.py` | Streamlit UI and request handling: the input form, the demo shortcut, result tables, the manual-review and override controls, and the CSV download. | yes | no |
| `pipeline.py` | Batch orchestration. Fans extraction out across a bounded thread pool and returns one result entry per file, in input order. | no | no (delegates) |
| `label_ai.py` | The only module that calls the provider. Image preparation, the prompt, the structured response, retry/backoff, and key/model resolution. | for secrets only | yes |
| `verifier.py` | The comparison rules. Compares expected values against extracted fields and returns a status per field. Pure functions, no I/O. | no | no |
| `batch.py` | CSV handling. Loads and validates the per-file expected-values CSV and serializes verified results back out to CSV. | no | no |

The dependency direction is one way. `app.py` depends on the four below it;
`pipeline.py` depends on `label_ai`, `verifier`, and `batch`; `batch.py` depends
on `verifier` for the shared `Status`/`FieldResult` types; `verifier.py` and
`label_ai.py` depend on nothing else in the package. Nothing depends on `app.py`.

## How one verification flows

1. `app.py` collects the expected values (typed into the form, or parsed from an
   uploaded CSV by `batch.load_expected_csv`) and the uploaded image bytes.
2. It calls `pipeline.process_batch(files, ...)`, which for each file resolves the
   expected values with `resolve_expected` (shared form values, or the CSV row
   matched by filename; a missing or incomplete row is skipped, not guessed) and
   submits the rest to a `ThreadPoolExecutor`.
3. Each worker runs `process_one`, which calls `label_ai.extract_label_fields` to
   read the label into a `LabelFields`, then `verifier.verify` to compare it
   against the expected values.
4. `process_batch` collects the entries back into input order and returns them.
   `app.py` stores them in `st.session_state` and renders a table per label.
5. The reviewer confirms the warning's visual formatting and may override any
   field. `batch.results_to_csv` serializes the verified batch, the manual
   confirmations, and the overrides into the downloadable CSV.

Results live in session state so a download click, which triggers a Streamlit
rerun, redraws the page without calling the provider again.

## Key types

- **`LabelFields`** (`label_ai.py`): a Pydantic model of the five fields read off a
  label. A field is `None` when the model could not read it.
- **`FieldResult`** (`verifier.py`): one field's comparison, carrying the expected
  and extracted text, a `Status`, and a human-readable explanation.
- **`Status`** (`verifier.py`): `PASS`, `WARNING`, `FAIL`, or `NEEDS_REVIEW`.
  `STATUS_LABEL` maps these to the strings shown in the UI and the CSV.
- **Entry** (`pipeline.py`): the per-file dict the UI renders. It always carries
  `name` and `image`, then exactly one outcome: `skip`, `error`, or
  `results` plus `elapsed`.

## How each field is compared

`verifier.verify` returns one `FieldResult` per field:

- **Brand name and class/type** are normalized (case, punctuation, spacing) and
  fuzzy-matched with RapidFuzz, so `STONE'S THROW` and `Stone's Throw` compare
  equal while a genuine difference fails.
- **Alcohol content** is parsed into ABV and proof and compared numerically, with
  a sanity check that the printed proof is about twice the ABV.
- **Net contents** are parsed into a quantity and unit, converted to milliliters,
  and compared within a small tolerance.
- **Government warning** is checked strictly: `GOVERNMENT WARNING:` must be in
  capitals followed by a colon, and the statement must match word for word.

Visual formatting of the warning (bold header, type size, placement, separation)
cannot be judged from a text transcription, so it is surfaced as a manual
confirmation in the UI rather than scored. `warning_visual_format_result` is the
single source for that item's text.

## Configuration knobs

All are module constants, easy to lift to real configuration later.

| Constant | Module | Default | Controls |
|---|---|---|---|
| `MAX_WORKERS` | `pipeline.py` | `5` | Concurrent extractions in a batch. |
| `DEFAULT_MODEL` | `label_ai.py` | `gpt-5.4-nano` | Model, overridable by `OPENAI_MODEL`. |
| `MAX_IMAGE_EDGE` | `label_ai.py` | `1600` | Longest edge an image is downscaled to before upload. |
| `REQUEST_TIMEOUT` | `label_ai.py` | `30.0` | Per-call timeout that turns a hung request into a clean error. |
| `MAX_RETRIES` | `label_ai.py` | `4` | Retries after the first attempt on a transient failure. |
| `RETRY_BASE_DELAY` / `RETRY_MAX_DELAY` | `label_ai.py` | `0.5` / `8.0` | Exponential-backoff window, in seconds. |
| `LATENCY_TARGET_SECONDS` | `app.py` | `5.0` | Per-label time above which the UI flags the lag. |

The key and model resolve from the environment first (`OPENAI_API_KEY`,
`OPENAI_MODEL`), then from Streamlit secrets. A missing key raises
`MissingAPIKeyError` before any image is sent.

## Extending it

- **Change provider or model.** Edit `label_ai.py` only. `extract_label_fields`
  is the seam: keep its signature (`bytes` in, `LabelFields` out) and the rest of
  the package is unaffected. The retryable error set lives in `RETRYABLE_ERRORS`.
- **Add a verified field.** Add it to `LabelFields`, the prompt, and `FIELD_LABELS`
  in `batch.py`, then add a comparison in `verifier.verify`. The UI and CSV iterate
  over whatever `verify` returns, so they pick it up without further changes.
- **Tune throughput or backoff.** Adjust the constants above. `process_batch`
  takes `max_workers` and an injectable `extract`, and `_with_retry` takes an
  injectable `sleep`, which is also how the tests exercise them without the
  network or real delays.
