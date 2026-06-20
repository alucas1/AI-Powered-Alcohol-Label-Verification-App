"""Extraction layer: image preparation, response handling, and key resolution.

The network is faked by patching `label_ai.OpenAI`, so these cover the parsing
and error paths around the call without making one. The retry/backoff schedule
that wraps the call has its own tests in test_retry.py.
"""

import io
import json

import pytest
from PIL import Image

import label_ai
from label_ai import (
    MAX_IMAGE_EDGE,
    ExtractionError,
    LabelFields,
    MissingAPIKeyError,
    _prepare_image,
    extract_label_fields,
)

VALID = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "government_warning": "GOVERNMENT WARNING: ...",
}


def _png(mode="RGB", size=(64, 64), color=0):
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeClient:
    """Stands in for openai.OpenAI: one canned completion, or a raised error."""

    def __init__(self, *, content=None, error=None):
        self._content = content
        self._error = error
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        if self._error is not None:
            raise self._error
        message = type("M", (), {"content": self._content})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(label_ai, "OpenAI", lambda *a, **k: _FakeClient(**kwargs))


# --- image preparation -------------------------------------------------------


def test_prepare_image_converts_rgba_to_jpeg():
    out = _prepare_image(_png(mode="RGBA"))
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_prepare_image_converts_palette_mode():
    out = _prepare_image(_png(mode="P"))
    assert Image.open(io.BytesIO(out)).mode == "RGB"


def test_prepare_image_downscales_oversized_input():
    out = _prepare_image(_png(size=(MAX_IMAGE_EDGE * 2, MAX_IMAGE_EDGE)))
    assert max(Image.open(io.BytesIO(out)).size) <= MAX_IMAGE_EDGE


def test_prepare_image_leaves_small_images_alone():
    out = _prepare_image(_png(size=(100, 80)))
    assert Image.open(io.BytesIO(out)).size == (100, 80)


def test_prepare_image_rejects_non_image_bytes():
    with pytest.raises(ExtractionError):
        _prepare_image(b"this is not an image")


# --- response handling -------------------------------------------------------


def test_extract_parses_a_valid_json_response(api_key, monkeypatch):
    _patch_client(monkeypatch, content=json.dumps(VALID))
    fields = extract_label_fields(_png())
    assert isinstance(fields, LabelFields)
    assert fields.brand_name == "OLD TOM DISTILLERY"
    assert fields.net_contents == "750 mL"


def test_extract_treats_missing_keys_as_none(api_key, monkeypatch):
    _patch_client(monkeypatch, content=json.dumps({"brand_name": "OLD TOM"}))
    fields = extract_label_fields(_png())
    assert fields.brand_name == "OLD TOM"
    assert fields.class_type is None
    assert fields.government_warning is None


def test_extract_ignores_unexpected_keys(api_key, monkeypatch):
    payload = {**VALID, "color": "amber", "confidence": 0.9}
    _patch_client(monkeypatch, content=json.dumps(payload))
    fields = extract_label_fields(_png())
    assert fields.brand_name == "OLD TOM DISTILLERY"
    assert not hasattr(fields, "color")


def test_extract_raises_on_non_json_response(api_key, monkeypatch):
    _patch_client(monkeypatch, content="Sorry, I can't read this label.")
    with pytest.raises(ExtractionError):
        extract_label_fields(_png())


def test_extract_raises_when_json_is_not_an_object(api_key, monkeypatch):
    _patch_client(monkeypatch, content=json.dumps(["a", "b"]))
    with pytest.raises(ExtractionError):
        extract_label_fields(_png())


def test_extract_wraps_api_errors(api_key, monkeypatch):
    _patch_client(monkeypatch, error=RuntimeError("connection reset"))
    with pytest.raises(ExtractionError):
        extract_label_fields(_png())


# --- key resolution ----------------------------------------------------------


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _NoSecrets:
        def __getitem__(self, key):
            raise KeyError(key)

    monkeypatch.setattr(label_ai.st, "secrets", _NoSecrets())
    with pytest.raises(MissingAPIKeyError):
        extract_label_fields(_png())
