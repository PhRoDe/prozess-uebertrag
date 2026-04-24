import json
from unittest.mock import MagicMock
import pytest
from app.worker.claude_client import ClaudeClient, ExtractionError


def make_mock_sdk(response_text: str):
    mock = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    mock.messages.create.return_value = msg
    return mock


def test_classify_document_returns_type():
    sdk = make_mock_sdk("jahresabschluss")
    client = ClaudeClient(sdk=sdk)
    assert client.classify_document("pdf content here") == "jahresabschluss"


def test_classify_document_strips_and_lowercases():
    sdk = make_mock_sdk("  JAHRESABSCHLUSS  \n")
    client = ClaudeClient(sdk=sdk)
    assert client.classify_document("x") == "jahresabschluss"


def test_extract_text_pdf_parses_json():
    payload = {"type": "jahresabschluss", "year": 2024, "accounts": []}
    sdk = make_mock_sdk(json.dumps(payload))
    client = ClaudeClient(sdk=sdk)
    result = client.extract_text_pdf("PDF-Text ...")
    assert result["year"] == 2024


def test_extract_handles_markdown_code_fence():
    payload = {"type": "jahresabschluss", "year": 2024, "accounts": []}
    raw = f"```json\n{json.dumps(payload)}\n```"
    sdk = make_mock_sdk(raw)
    client = ClaudeClient(sdk=sdk)
    result = client.extract_text_pdf("x")
    assert result["year"] == 2024


def test_extract_raises_on_invalid_json():
    sdk = make_mock_sdk("not-json-at-all")
    client = ClaudeClient(sdk=sdk)
    with pytest.raises(ExtractionError):
        client.extract_text_pdf("text")


def test_extract_text_pdf_rejects_oversized_input():
    sdk = make_mock_sdk("{}")
    client = ClaudeClient(sdk=sdk)
    huge_text = "x" * (client.max_extract_chars + 1)
    with pytest.raises(ExtractionError, match="zu lang"):
        client.extract_text_pdf(huge_text)
    # SDK was never called
    sdk.messages.create.assert_not_called()


def test_extract_scan_pdf_sends_base64_images():
    payload = {"type": "jahresabschluss", "year": 2024, "accounts": []}
    sdk = make_mock_sdk(json.dumps(payload))
    client = ClaudeClient(sdk=sdk)
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    client.extract_scan_pdf([png_bytes, png_bytes])
    # Verify the SDK was called with image content blocks
    call_args = sdk.messages.create.call_args
    messages = call_args.kwargs["messages"]
    content = messages[0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    assert len(image_blocks) == 2
    assert image_blocks[0]["source"]["type"] == "base64"
