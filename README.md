# AI-Powered Alcohol Label Verification

**Live demo:** https://ai-alcohol-label-verifier.streamlit.app

A prototype that helps TTB compliance reviewers check alcohol beverage labels. A
reviewer enters the values from a label application, uploads the label image(s),
and the app reads each label with a vision model and compares it, field by field,
against what was entered.

The goal is something a non-technical reviewer can use without training, with a
result back in roughly five seconds per label.

## What it does

- Accepts one or more label images (PNG/JPG); a batch is processed one image at a time.
- Takes the expected values for five fields: brand name, class/type, alcohol
  content, net contents, and the government warning (pre-filled with the standard
  TTB text).
- Reads those fields from each image with an OpenAI vision model (configurable;
  default `gpt-5.4-nano`).
- Scores each field and assigns a status with a short explanation:

  | Status | Meaning |
  |---|---|
  | PASS | Matches the application. |
  | WARNING | Minor or cosmetic difference (e.g. capitalization). Confirm by eye. |
  | FAIL | Clear mismatch, or a formatting rule was violated. |
  | NEEDS REVIEW | Could not be read or parsed (glare, bad angle). Review by hand. |

- Shows the processing time per label.
- Flags the government warning's visual formatting (bold, type size, placement)
  for a manual check, since it can't be confirmed from an image.
- Exports every result in a batch as a single downloadable CSV.

### How each field is checked

The model only transcribes text. Every pass/fail decision lives in deterministic
code (`verifier.py`), so the rules are explicit and testable instead of left to
the model:

- **Brand name and class/type** are normalized (case, punctuation, spacing) and
  fuzzy-matched with RapidFuzz, so `STONE'S THROW` and `Stone's Throw` compare equal.
- **Alcohol content** is parsed into ABV and proof and compared numerically, with
  a sanity check that proof is about 2x the ABV.
- **Net contents** are parsed into quantity and unit, converted to milliliters,
  and compared with a small tolerance.
- **Government warning** is checked strictly: `GOVERNMENT WARNING:` must be in
  capitals followed by a colon, and the statement must match word for word. Title
  case or reworded text fails.

## Using the app

A few behaviors are worth calling out:

- **Bundled demo.** A **Run demo files** button verifies four sample labels
  against their matching CSV in one click, so the app can be exercised end to end
  without preparing your own inputs.
- **In-app help.** A collapsible **How to use this app** panel at the top covers
  the steps, what each status means, and the expected behavior.
- **Required fields.** All five expected values — brand name, class/type, alcohol
  content, net contents, and the government warning — must be filled in before a
  label is verified. Submitting with any blank lists the missing fields and stops
  before any AI call, so an incomplete form never burns an API request.
- **Batch expected values.** By default one set of expected values applies to
  every uploaded image, i.e. several photos of the same application. To verify
  different labels in one pass, upload an optional CSV with columns `filename,
  brand_name, class_type, alcohol_content, net_contents, government_warning` — one
  row per image. Each image is matched to its row by filename (case-insensitive).
  A file with no matching row, or a row missing required values, is flagged and
  skipped rather than verified against the wrong data.
- **Warning visual formatting.** Wording and header capitalization are checked
  automatically, but type size, weight, placement, and separation from other copy
  can't be judged from a text transcription. Each result therefore carries a
  **Visual format — manual check** box with a checkbox the reviewer ticks once
  they've confirmed bold `GOVERNMENT WARNING:`, legibility, type size, and
  placement by eye. It's a reviewer acknowledgment, kept separate from the
  automated grid; it doesn't change the pass/fail verdict and isn't persisted.
- **Full warning on demand.** The government warning is long, so the grid shows a
  truncated cell; a **Show full government warning** expander under each result
  reveals the complete expected-vs-extracted text for side-by-side comparison.
- **Manual overrides.** When a reviewer disagrees with an automated check, an
  **Overrides** panel under each result lets them set any field to PASS or FAIL
  with an optional reason. Overridden fields read `PASS (manual)` / `FAIL (manual)` in the grid and
  the results CSV (the reason defaults to "Manually passed/failed" if left blank).
  The top-line banner still reflects the automated verdict, so a manual decision
  never hides what the model found.
- **Response time.** Stakeholder feedback set a ~5 second per-label target. Each
  result shows its processing time, and anything over five seconds is flagged so
  the lag is visible. A hard request timeout (`REQUEST_TIMEOUT` in `label_ai.py`)
  turns a hung call into a clean, user-facing error instead of an indefinite wait.
- **Downloadable results.** Once a batch is verified, a **Download all results
  (CSV)** button exports every field of every label into one file — `filename,
  field, expected, extracted, status, explanation` — for record-keeping or review
  away from the app. Each label also gets a `Manual Visual Format Review` row whose
  status reflects its checkbox (`YES`/`NO`), so ticking a box updates the download.
  Skipped or unreadable files are left out. Results are held in session state, so
  downloading doesn't re-run the labels through the model.

## Architecture

Concerns are kept separate so the AI provider can change without touching the UI
or the comparison rules:

```
.
├── .streamlit/                 # config.toml + secrets, resolved from the run directory
├── app/
│   ├── app.py                  # Streamlit UI and orchestration
│   ├── label_ai.py             # the only module that calls the AI provider
│   ├── batch.py                # CSV validation/loading and results export
│   └── verifier.py             # comparison logic; no UI, no network
├── tests/                      # pytest suite, one file per concern
│   ├── test_text_fields.py
│   ├── test_alcohol.py
│   ├── test_net_contents.py
│   ├── test_warning.py
│   ├── test_warning_visual_format.py
│   ├── test_validation.py
│   ├── test_batch.py
│   ├── test_verify.py
│   ├── test_sample_files.py    # end-to-end checks over the bundled sample set
│   └── make_sample_labels.py   # generates a synthetic label for local testing
├── test_files/                 # bundled sample labels + CSV (used by the demo and tests)
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
└── README.md
```

`.streamlit/` sits at the repository root because Streamlit resolves
`config.toml` and `secrets.toml` relative to the directory you launch from, so
run the app from the root: `streamlit run app/app.py`.

Each image goes to the model in one call that returns a single structured JSON
object, which keeps latency within the per-label budget. Uploaded images are held
in memory and never written to disk.

## Setup

### Requirements

- Python 3.10 or newer (developed and tested on 3.12).
- An OpenAI API key with access to a vision-capable model.
- Python packages, pinned in `requirements.txt`:

  | Package | Version | Used for |
  |---|---|---|
  | streamlit | >= 1.49 | UI and app runtime |
  | openai | >= 1.50 | vision model client |
  | rapidfuzz | >= 3.9 | fuzzy text matching |
  | pillow | >= 11.0 | image decode and downscale |
  | pydantic | >= 2.7 | validating the model's JSON |
  | pandas | >= 2.2 | result tables and CSV I/O |

  Running the tests also needs `pytest >= 8.0` (`requirements-dev.txt`).

### Install and run

```bash
git clone <your-repo-url>
cd AI-Powered-Alcohol-Label-Verification-App

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# add your OpenAI key to .streamlit/secrets.toml

streamlit run app/app.py           # from the repo root
```

The app opens at http://localhost:8501. Click **Run demo files** to verify the
bundled sample labels, or enter your own values and upload images.

To generate a fresh synthetic label to test against:

```bash
python tests/make_sample_labels.py   # writes sample_labels/old_tom_distillery.png
```

### Tests

The pytest suite covers the deterministic logic: `verifier.py`'s field
comparisons, `batch.py`'s validation, CSV loading, and results export, plus an
end-to-end pass over the bundled sample set.

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_sample_files.py` also carries a live, end-to-end check that sends the
sample images to the model and validates the results. It runs by default, but
only after a precheck confirms a usable key and a reachable model: the precheck
calls `models.retrieve` to prove both the key and the configured model are valid,
and if either isn't (no key, bad key, unknown model, no network) the live tests
skip with the reason rather than fail — so an offline checkout still passes. These
calls hit the real API, cost money, and vary slightly between runs, so a single
failure is worth a re-run before treating it as a regression. To opt out even when
a valid key is present:

```bash
SKIP_LIVE_EXTRACTION=1 pytest
```

### Configuration

The API key and model are read from the environment first, then from Streamlit
secrets:

```toml
# .streamlit/secrets.toml  (gitignored; do not commit)
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL   = "gpt-5.4-nano"   # optional; this is the default
```

Or via the environment: `export OPENAI_API_KEY=...` and `export OPENAI_MODEL=...`.

## Deploying to Streamlit Community Cloud

A live instance runs at https://ai-alcohol-label-verifier.streamlit.app. To stand
up your own:

1. Push the repository to GitHub.
2. Create a new app at https://share.streamlit.io pointing at `app/app.py`.
3. Add the key under the app's Advanced Settings > Secrets:
   ```toml
   OPENAI_API_KEY = "sk-..."
   ```
4. Deploy. Streamlit installs `requirements.txt` automatically.

## Cost analysis

Each label is a single API call. The recurring cost of running the tool is the
per-call token charge, so the math is simple and scales linearly with volume.

**Per-call token estimate.** One label sends the fixed system prompt, a short
instruction, and the downscaled image (capped at a 1600 px edge), and receives a
small JSON object of the five fields. That works out to roughly **1,500 input
tokens and 250 output tokens** per label. Image-heavy labels run higher; this is a
working average, not a guarantee.

**Pricing (OpenAI list prices, June 2026).** Vision input is billed at the
model's standard token rate; there is no separate per-image charge.

| Model | Input / 1M | Output / 1M | Per label | Per 1,000 labels |
|---|---|---|---|---|
| `gpt-5.4-nano` (default) | $0.20 | $1.25 | ~$0.0006 | ~$0.61 |
| `gpt-5.4-mini` | $0.75 | $4.50 | ~$0.0023 | ~$2.25 |
| `gpt-5.4` | $2.50 | $15.00 | ~$0.0075 | ~$7.50 |

**At TTB's scale.** The Compliance Division reviews roughly 150,000 applications a
year. Running every one through the default model costs on the order of **$90–100
a year** in API spend; the mid tier is about **$340**, and the largest model about
**$1,100**. Even with retries and multiple images per application, this stays in
the low hundreds to low thousands of dollars annually — set against the $4.2M
quoted for a COLA rebuild, the model cost is not the deciding factor.

**Why the default is the cheapest tier.** The model only transcribes text; every
pass/fail decision is made in deterministic code (see [How each field is
checked](#how-each-field-is-checked)). Transcription is well within the smallest
model's ability, so paying for a larger one buys little here. If extraction
accuracy on poor-quality images proves limiting, `OPENAI_MODEL` switches tiers
without a code change, and the table above bounds the cost of doing so.

**Further reductions.** Two OpenAI features apply directly to this workload but
are not enabled in the prototype:

- **Batch API** — the importer use case (200–300 labels dropped at once) is a
  natural fit for asynchronous batch submission, which lists at a 50% discount.
- **Prompt caching** — the system prompt is identical on every call, so caching
  its prefix trims the input cost on repeated runs.

Prices move; verify against [OpenAI's pricing page](https://openai.com/api/pricing/)
before budgeting.

## Design notes and assumptions

It's a prototype, and a few choices reflect that:

- The provider is isolated in `label_ai.py`, so moving to another model or vendor
  is a one-file change. `gpt-5.4-nano` is the default: fast and cheap enough for
  the latency budget, and it supports JSON output.
- By default one set of expected values applies to every image in a batch, i.e. a
  batch is treated as several photos of the same application. An optional CSV
  pairs each image with its own expected values for mixed batches; a production
  tool would pull those from COLA rather than a hand-authored CSV.
- The thresholds (fuzzy >= 90%, ABV +/-0.1, ~1% volume tolerance) are reasonable
  defaults, not TTB-calibrated tolerances.
- The warning check covers header casing and exact wording, both of which survive
  a text transcription, and deliberately stops there. TTB also requires the
  warning to be bold, legible at a type size that scales with container size, a
  continuous paragraph, and set apart from other copy — none of which a
  transcription can prove. Rather than let the model guess at the most legally
  sensitive field, the app surfaces visual formatting as a manual confirmation the
  reviewer ticks off (recorded as `YES`/`NO` in the results CSV), keeping that
  judgment with a person.
- Output quality tracks image quality. Bad angle, glare, or lighting yields
  NEEDS REVIEW rather than a confident-but-wrong PASS or FAIL.

## Out of scope for production

A proof of concept, not a production system. A real deployment would have to
address at least:

- **Authorization.** A federal deployment needs an authorized, FedRAMP-compliant
  environment; a public Streamlit Cloud app does not qualify.
- **Approved endpoints and egress.** Agency networks restrict outbound traffic,
  so this would need an in-boundary model endpoint (e.g. Azure OpenAI inside the
  accredited environment) rather than the public OpenAI API.
- **PII and data handling.** Applications can contain PII. The prototype keeps
  images in memory and stores nothing; production needs a defined data flow,
  encryption, and access controls.
- **Records retention.** Federal retention and disposition policies would govern
  any stored images, results, or audit logs. None are stored here.
- **COLA integration.** Expected values are typed in by hand rather than pulled
  from COLA.

## Future work

- Pull per-image expected values from COLA instead of a hand-authored CSV.
- Parallelize batch extraction to hold the latency budget on large batches.
- Surface per-field confidence and bounding-box highlights.
- Make tolerances configurable and TTB-calibrated per beverage type.
