# Sample labels and expected results

Four label images and a batch CSV for exercising the verifier end to end: one
clean pass, one with a brand-extraction wrinkle, one with a failing field, and
one with a failing government warning. Click **Run demo files** in the app, or
upload the four images with the CSV (batch mode), to reproduce the results below.

## Files

```text
test_files/
├── old_tom_distillery.png       # clean pass
├── silver_coast.png             # pass, with a brand-extraction wrinkle
├── stones_throw.png             # alcohol content fails
├── monarch_hill.png             # government warning fails
├── sample_label_batch_test.csv  # expected values, one row per image
└── README.md
```

## Batch CSV

One row per image. The `filename` column must match the uploaded image name
exactly; that is how each row is paired with its label. Columns:

```text
filename,brand_name,class_type,alcohol_content,net_contents,government_warning
```

Every row uses the standard TTB health warning as the expected text:

```text
GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.
```

Visual formatting of the warning — bold header, type size, placement — can't be
judged from a text transcription, so the app surfaces it as a separate manual
checkbox rather than an automated field. The per-file tables below cover only the
automated comparisons.

## Expected results

| Image | Brand | Class/Type | Alcohol | Net Contents | Gov. Warning |
|---|---|---|---|---|---|
| `old_tom_distillery.png` | PASS | PASS | PASS | PASS | PASS |
| `silver_coast.png` | PASS¹ | PASS | PASS | PASS | PASS |
| `stones_throw.png` | PASS² | PASS | **FAIL** | PASS | PASS |
| `monarch_hill.png` | PASS | PASS | PASS | PASS | **FAIL** |

¹ Passes when the model reads the full `SILVER COAST DISTILLING CO.`.
  `DISTILLING CO.` sits on its own line; a truncated read of just `SILVER COAST`
  falls below the fuzzy-match threshold and would fail.
² `Stone's Throw` and `STONE'S THROW` differ only in case and punctuation, which
  the comparison normalizes away.

### old_tom_distillery.png — clean pass

Positive control. The CSV values match the label, so every automated field
passes.

### silver_coast.png — brand-extraction wrinkle

Built to pass. The only nuance is the brand: `DISTILLING CO.` is set on its own
line, so the model may return the full `SILVER COAST DISTILLING CO.` or just
`SILVER COAST`. The full read passes; a truncated read fails the 90% fuzzy
threshold rather than warning, which is the realistic trade-off for a strict
brand check.

### stones_throw.png — failing alcohol content

The CSV expects `45% Alc./Vol. (90 Proof)`; the label prints
`47% Alc./Vol. (94 Proof)`, so alcohol content fails on a real numeric
difference. The brand differs only in case (`Stone's Throw` vs `STONE'S THROW`)
and still passes.

### monarch_hill.png — failing government warning

The warning fails on two counts:

1. The header reads `Government Warning:` instead of the required all-caps
   `GOVERNMENT WARNING:`.
2. The wording diverges from the standard text — `pregnant women` for `women
   should not drink alcoholic beverages during pregnancy`, and `Consumption of
   alcohol` for `Consumption of alcoholic beverages`.

Every other field passes.

## What "working" looks like

Extraction varies slightly between runs; OCR, punctuation, and line breaks can
nudge a field to WARNING or NEEDS REVIEW. The two outcomes that should hold every
time are the deterministic failures:

- `stones_throw.png` fails on alcohol content.
- `monarch_hill.png` fails on the government warning.

If both hold, the set is behaving as intended.
