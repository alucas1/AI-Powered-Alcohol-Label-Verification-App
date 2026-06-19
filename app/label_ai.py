"""Vision extraction layer.

The only module that talks to the AI provider. Everything downstream depends on
`extract_label_fields()` returning a `LabelFields`, so swapping providers is
contained to this file.

Provider: OpenAI. The model is configurable (see `get_model`), defaulting to
`DEFAULT_MODEL`; it's picked to fit the ~5s/label budget and to support JSON
mode, which returns the fields structured in a single round-trip.
"""

from __future__ import annotations

import base64
import io
import json
import os
from typing import Optional

import streamlit as st
from openai import OpenAI
from PIL import Image
from pydantic import BaseModel, ValidationError

# Override via OPENAI_MODEL (env or Streamlit secret); see get_model().
DEFAULT_MODEL = "gpt-5.4-nano"

# Cap on the longest image edge. Phone photos are downscaled to this before
# upload: smaller payload and cost, with no measurable hit to OCR on label text.
MAX_IMAGE_EDGE = 1600

# Hard cap on a single image round-trip. The ~5s/label target is a soft goal
# surfaced in the UI; this is the safety net that turns a hung request into a
# clean ExtractionError instead of an indefinite wait.
REQUEST_TIMEOUT = 30.0


class LabelFields(BaseModel):
    """The fields we ask the model to read off a label. `None` = not readable."""

    brand_name: Optional[str] = None
    class_type: Optional[str] = None
    alcohol_content: Optional[str] = None
    net_contents: Optional[str] = None
    government_warning: Optional[str] = None


class MissingAPIKeyError(Exception):
    """Raised when no OpenAI API key is configured."""


class ExtractionError(Exception):
    """Raised when the image cannot be read or the AI response is unusable."""


SYSTEM_PROMPT = """You are a compliance assistant that reads U.S. alcohol \
beverage (TTB) labels from images.

Extract the following fields and return ONLY a JSON object with these exact keys:
- "brand_name"
- "class_type"          (the class/type designation, e.g. "Kentucky Straight Bourbon Whiskey")
- "alcohol_content"     (e.g. "45% Alc./Vol. (90 Proof)")
- "net_contents"        (e.g. "750 mL")
- "government_warning"  (the full health warning statement)

Rules:
- Transcribe text EXACTLY as printed, preserving the original capitalization,
  punctuation, and wording. This matters most for "government_warning":
  reproduce it verbatim, including whether "GOVERNMENT WARNING:" is capitalized.
- For "government_warning", include the complete statement starting from
  "GOVERNMENT WARNING" if it is present.
- If a field is not visible or cannot be read confidently, set it to null.
- Do not guess, translate, normalize, or invent values.
"""


def get_api_key() -> str:
    """Return the OpenAI API key from Streamlit secrets or the environment.

    Raises:
        MissingAPIKeyError: if no key is configured anywhere.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        try:
            key = st.secrets["OPENAI_API_KEY"]
        except Exception:  # no secrets file or key; handled below
            key = None
    if not key:
        raise MissingAPIKeyError("No OpenAI API key configured.")
    return key


def get_model() -> str:
    """Resolve the model name: OPENAI_MODEL from the environment, then Streamlit
    secrets, then DEFAULT_MODEL."""
    model = os.environ.get("OPENAI_MODEL")
    if not model:
        try:
            model = st.secrets["OPENAI_MODEL"]
        except Exception:  # no secrets file or key; use the default
            model = None
    return model or DEFAULT_MODEL


def _prepare_image(image_bytes: bytes) -> bytes:
    """Decode, downscale, and re-encode an uploaded image entirely in memory.

    Nothing is written to disk. Raises ExtractionError on undecodable input.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
    except Exception as exc:
        raise ExtractionError("The uploaded file could not be read as an image.") from exc

    if max(img.size) > MAX_IMAGE_EDGE:
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def extract_label_fields(image_bytes: bytes) -> LabelFields:
    """Send an image to the vision model and return structured label fields.

    Raises:
        MissingAPIKeyError: if no API key is configured.
        ExtractionError: if the image is unreadable, the API call fails, or the
            response is not valid JSON in the expected shape.
    """
    client = OpenAI(api_key=get_api_key())
    prepared = _prepare_image(image_bytes)
    b64 = base64.b64encode(prepared).decode("ascii")

    try:
        response = client.chat.completions.create(
            model=get_model(),
            temperature=0,
            timeout=REQUEST_TIMEOUT,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Extract the label fields from this alcohol label image as JSON.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                },
            ],
        )
    except Exception as exc:
        raise ExtractionError(f"The AI service could not process this image ({exc}).") from exc

    content = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ExtractionError("The AI returned a response that was not valid JSON.") from exc

    if not isinstance(data, dict):
        raise ExtractionError("The AI response was not in the expected format.")

    try:
        return LabelFields(**{k: data.get(k) for k in LabelFields.model_fields})
    except ValidationError as exc:
        raise ExtractionError("The AI response did not match the expected field format.") from exc
