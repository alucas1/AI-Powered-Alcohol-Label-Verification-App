# AI-Powered Alcohol Label Verification

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

## Architecture

Three concerns, kept separate so the AI provider can change without touching the
UI or the comparison rules:

```
.
├── .streamlit/                 # config + secrets, resolved from the run directory
├── app/
│   ├── app.py                  # Streamlit UI and orchestration
│   ├── label_ai.py             # the only module that calls the AI provider
│   └── verifier.py             # comparison logic; no UI, no network
├── tests/                      # pytest suite for verifier.py, one file per concern
│   ├── test_text_fields.py
│   ├── test_alcohol.py
│   ├── test_net_contents.py
│   ├── test_warning.py
│   ├── test_verify.py
│   └── make_sample_labels.py   # generates a synthetic label for local testing
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

Requires Python 3.10+.

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

The app opens at http://localhost:8501.

Generate a sample label to try it against:

```bash
python tests/make_sample_labels.py   # writes sample_labels/old_tom_distillery.png
```

### Tests

`verifier.py` is covered by a pytest suite: one file per field comparison plus an
end-to-end check of `verify()`.

```bash
pip install -r requirements-dev.txt
pytest
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

1. Push the repository to GitHub.
2. Create a new app at https://share.streamlit.io pointing at `app/app.py`.
3. Add the key under the app's Settings > Secrets:
   ```toml
   OPENAI_API_KEY = "sk-..."
   ```
4. Deploy. Streamlit installs `requirements.txt` automatically.

## Design notes and assumptions

It's a prototype, and a few choices reflect that:

- The provider is isolated in `label_ai.py`, so moving to another model or vendor
  is a one-file change. `gpt-5.4-nano` is the default: fast and cheap enough for
  the latency budget, and it supports JSON output.
- One set of expected values applies to every image in a batch, i.e. a batch is
  treated as several photos of the same application, not many different ones. A
  production tool would pair each image with its own application record (CSV
  import or COLA lookup).
- The thresholds (fuzzy >= 90%, ABV +/-0.1, ~1% volume tolerance) are reasonable
  defaults, not TTB-calibrated tolerances.
- The warning check covers header casing and the standard text. It does not check
  font size, weight, or placement, none of which survive a text transcription.
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

- Pair each image with its own application record (CSV import or COLA).
- Parallelize batch extraction to hold the latency budget on large batches.
- Surface per-field confidence and bounding-box highlights.
- Make tolerances configurable and TTB-calibrated per beverage type.
