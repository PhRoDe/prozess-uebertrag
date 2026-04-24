import base64
import json
import time
from typing import Any
from anthropic import Anthropic, APIStatusError
from app.config import get_settings
from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION, BWA_PROMPT,
)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000


class ExtractionError(Exception):
    pass


class ClaudeClient:
    def __init__(self, sdk: Anthropic | None = None) -> None:
        s = get_settings()
        self.sdk = sdk or Anthropic(api_key=s.anthropic_api_key)

    def classify_document(self, text_or_sample: str) -> str:
        resp = self._call([
            {"role": "user", "content": [
                {"type": "text", "text": DOC_TYPE_PROMPT},
                {"type": "text", "text": f"\n\nInhalt:\n{text_or_sample[:5000]}"},
            ]}
        ])
        return resp.strip().lower()

    def extract_text_pdf(self, text: str, is_bwa: bool = False) -> dict[str, Any]:
        prompt = BWA_PROMPT if is_bwa else EXTRACTION_PROMPT_TEXT
        raw = self._call([
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "text", "text": f"\n\nPDF-Inhalt:\n{text}"},
            ]}
        ])
        return self._parse_json(raw)

    def extract_scan_pdf(self, pages_png: list[bytes], is_bwa: bool = False) -> dict[str, Any]:
        prompt = BWA_PROMPT if is_bwa else EXTRACTION_PROMPT_VISION
        images = [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(p).decode(),
            }}
            for p in pages_png
        ]
        raw = self._call([
            {"role": "user", "content": [{"type": "text", "text": prompt}, *images]}
        ])
        return self._parse_json(raw)

    def consolidate(self, prompt: str) -> dict[str, Any]:
        raw = self._call([{"role": "user", "content": [{"type": "text", "text": prompt}]}])
        return self._parse_json(raw)

    def _call(self, messages: list[dict[str, Any]], retries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                msg = self.sdk.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, messages=messages,
                )
                return msg.content[0].text
            except APIStatusError as e:
                last_err = e
                if getattr(e, "status_code", None) == 429 and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise ExtractionError(f"Claude call failed after {retries} tries: {last_err}")

    def _parse_json(self, raw: str) -> dict[str, Any]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # strip markdown code fences
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"Invalid JSON from Claude: {e}\nRaw (first 500 chars): {raw[:500]}"
            ) from e
