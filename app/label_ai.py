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
import random
import time
from email.utils import parsedate_to_datetime
from typing import Callable, Optional, TypeVar

import streamlit as st
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
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

# Transient failures worth retrying: a 429 rate limit, a 5xx from the provider,
# or a connection/timeout blip. Everything else (a 400 bad request, a 401 auth
# failure) is a hard error that retrying would only delay, so it surfaces at once.
RETRYABLE_ERRORS = (RateLimitError, InternalServerError, APITimeoutError, APIConnectionError)

# Backoff schedule for those transient failures. Concurrency makes a burst of
# requests far more likely to brush the provider's rate limit, so the client
# retries with exponential backoff and full jitter, which spreads the retries
# out instead of having every throttled worker wake at the same instant and
# collide again. MAX_RETRIES is the retry count after the first attempt; the SDK's
# own retry layer is disabled (max_retries=0) so this is the only one in play.
MAX_RETRIES = 4
RETRY_BASE_DELAY = 0.5  # seconds; the first backoff is drawn from [0, this]
RETRY_MAX_DELAY = 8.0  # seconds; ceiling on any single backoff

T = TypeVar("T")


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


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """The provider's requested wait from a ``Retry-After`` header, if it sent one.

    A 429 often carries ``Retry-After`` telling us exactly how long to hold off,
    as either a number of seconds or an HTTP date. Honoring it beats guessing.
    Returns None when there's no usable header (e.g. a timeout, which has no
    response), so the caller falls back to computed backoff.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    import datetime as _dt

    now = _dt.datetime.now(retry_at.tzinfo) if retry_at.tzinfo else _dt.datetime.now()
    return max(0.0, (retry_at - now).total_seconds())


def _backoff_delay(attempt: int) -> float:
    """Full-jitter exponential backoff for retry ``attempt`` (0-indexed).

    A delay drawn uniformly from ``[0, min(cap, base * 2**attempt)]``. The jitter
    is the point: it decorrelates the retries of workers that were throttled
    together so they don't all retry in lockstep.
    """
    ceiling = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** attempt))
    return random.uniform(0, ceiling)


def _with_retry(
    call: Callable[[], T],
    *,
    max_retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``call``, retrying transient failures with backoff.

    Retries only on RETRYABLE_ERRORS, up to ``max_retries`` times, sleeping a
    ``Retry-After``-aware backoff between attempts. Non-retryable errors and the
    final failure propagate to the caller. ``sleep`` is injectable so tests can
    exercise the schedule without waiting.
    """
    for attempt in range(max_retries + 1):
        try:
            return call()
        except RETRYABLE_ERRORS as exc:
            if attempt == max_retries:
                raise
            wait = _retry_after_seconds(exc)
            sleep(wait if wait is not None else _backoff_delay(attempt))


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
    # max_retries=0 hands all retry/backoff to _with_retry below, so there's a
    # single, observable schedule rather than two layers compounding each other.
    client = OpenAI(api_key=get_api_key(), max_retries=0)
    prepared = _prepare_image(image_bytes)
    b64 = base64.b64encode(prepared).decode("ascii")

    def _create():
        return client.chat.completions.create(
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

    try:
        response = _with_retry(_create)
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
