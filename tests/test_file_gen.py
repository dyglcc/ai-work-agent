import base64

from app.services.file_gen import _decode_data_url, _extract_image_payload


def test_extract_openai_image_url():
    kind, value = _extract_image_payload({"data": [{"url": "https://example.com/a.png"}]})

    assert kind == "url"
    assert value == "https://example.com/a.png"


def test_extract_openai_base64_image():
    kind, value = _extract_image_payload({"data": [{"b64_json": "abc"}]})

    assert kind == "b64"
    assert value == "abc"


def test_extract_dashscope_style_url():
    kind, value = _extract_image_payload({"output": {"results": [{"url": "https://example.com/b.png"}]}})

    assert kind == "url"
    assert value == "https://example.com/b.png"


def test_decode_data_url():
    raw = b"png-bytes"
    encoded = base64.b64encode(raw).decode("ascii")

    assert _decode_data_url(f"data:image/png;base64,{encoded}") == raw
