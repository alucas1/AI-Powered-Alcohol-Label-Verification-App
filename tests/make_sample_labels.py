"""Generate a synthetic label image for local testing.

Run:  python tests/make_sample_labels.py
Writes a PNG to <repo>/sample_labels/ (gitignored) so the app can be exercised
without sourcing real label artwork. Dev tooling, not part of the app.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

# Import from app/ and anchor paths at the repo root so this runs from any CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "app"))

from verifier import STANDARD_WARNING

OUTPUT_DIR = os.path.join(_REPO_ROOT, "sample_labels")


def _font(size: int):
    return ImageFont.load_default(size=size)


def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    width, height, margin = 700, 900, 40
    img = Image.new("RGB", (width, height), "#f5f0e6")
    draw = ImageDraw.Draw(img)

    draw.text((margin, 60), "OLD TOM DISTILLERY", font=_font(40), fill="#3b2f2f")
    draw.text((margin, 150), "Kentucky Straight Bourbon Whiskey", font=_font(24), fill="#3b2f2f")
    draw.text((margin, 230), "45% Alc./Vol. (90 Proof)", font=_font(22), fill="#3b2f2f")
    draw.text((margin, 290), "750 mL", font=_font(22), fill="#3b2f2f")

    warn_font = _font(16)
    y = 420
    for line in _wrap(draw, STANDARD_WARNING, warn_font, width - 2 * margin):
        draw.text((margin, y), line, font=warn_font, fill="#000000")
        y += 24

    path = os.path.join(OUTPUT_DIR, "old_tom_distillery.png")
    img.save(path)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
