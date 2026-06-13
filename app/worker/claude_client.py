import base64
import json
import time
from typing import Any
from anthropic import Anthropic, APIStatusError
from app.config import get_settings
from app.worker.prompts import (
    DOC_TYPE_PROMPT, EXTRACTION_PROMPT_TEXT, EXTRACTION_PROMPT_VISION,
    BWA_PROMPT, SUSA_PROMPT, SYSTEM_PROMPT, REEXTRACT_PROMPT,
)


def _select_prompt(doc_type: str, vision: bool = False) -> str:
    """Map doc_type → richtiger Extraktions-Prompt."""
    if doc_type == "bwa":
        return BWA_PROMPT
    if doc_type == "susa":
        return SUSA_PROMPT
    return EXTRACTION_PROMPT_VISION if vision else EXTRACTION_PROMPT_TEXT


class ExtractionError(Exception):
    pass


class ClaudeClient:
    def __init__(self, sdk: Anthropic | None = None) -> None:
        s = get_settings()
        self.sdk = sdk or Anthropic(api_key=s.anthropic_api_key)
        self.model = s.claude_model
        self.max_tokens = s.claude_max_tokens
        self.max_extract_chars = s.max_extract_chars

    def classify_document(self, text_or_sample: str) -> str:
        resp = self._call(
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": DOC_TYPE_PROMPT},
                    {"type": "text",
                     "text": f"\n\n<pdf_content>\n{text_or_sample[:5000]}\n</pdf_content>"},
                ]}
            ],
            system=SYSTEM_PROMPT,
        )
        return resp.strip().lower()

    def extract_text_pdf(self, text: str, is_bwa: bool = False,
                          doc_type: str | None = None) -> dict[str, Any]:
        """doc_type ('jahresabschluss' | 'bwa' | 'susa') ist authoritativ
        wenn gesetzt; is_bwa bleibt fuer Backwards-Kompatibilitaet."""
        if len(text) > self.max_extract_chars:
            raise ExtractionError(
                f"PDF-Text zu lang ({len(text):,} Zeichen, Limit {self.max_extract_chars:,}). "
                "Bitte kleinere PDF hochladen."
            )
        effective = doc_type or ("bwa" if is_bwa else "jahresabschluss")
        prompt = _select_prompt(effective, vision=False)
        raw = self._call(
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "text", "text": f"\n\n<pdf_content>\n{text}\n</pdf_content>"},
                ]}
            ],
            system=SYSTEM_PROMPT,
        )
        return self._parse_json(raw)

    def reextract_groups(self, text: str, gaps: list[dict]) -> dict[str, list[dict]]:
        """Gezielte Re-Extraktion (Selbstheilung): fragt Claude nur nach den
        Einzelkonten der Lücken-Positionen. Liefert {gruppen_name: [konten]}.
        Leere gaps → kein API-Call."""
        if not gaps:
            return {}
        # Lücken-Positionen als DATEN-Block (<gaps>), NICHT im Instruktions-Text —
        # ein PDF-abgeleiteter Positions-Name könnte sonst die Anweisung steuern
        # (Prompt-Injection, Codex-Finding P2-4).
        def _san(s: object) -> str:
            # Winkelklammern entfernen, damit ein PDF-abgeleiteter Name nicht aus
            # dem <gaps>-Datenblock ausbrechen kann (Codex-Finding P2-5).
            return str(s).replace("<", " ").replace(">", " ")
        # Eine Zeile PRO Lücke mit Periode (Geschäftsjahr/Vorjahr) + Jahr — sonst
        # weiß Claude bei einer reinen VJ-Lücke nicht, welche Spalte gemeint ist,
        # und extrahiert ggf. gegen das falsche Jahr (Codex Round-8B).
        period_label = {"gj": "Geschäftsjahr", "vj": "Vorjahr"}
        gaps_block = "\n".join(
            f"- '{_san(g.get('group'))}' "
            f"({period_label.get(g.get('period'), g.get('period'))} {g.get('year')}): "
            f"gedruckte Summe {g.get('printed_sum')}, erfasste Konten "
            f"{g.get('acc_sum')} (Differenz {g.get('diff')})"
            for g in gaps
        )
        raw = self._call(
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": REEXTRACT_PROMPT},
                    {"type": "text", "text": f"\n\n<gaps>\n{gaps_block}\n</gaps>"},
                    {"type": "text", "text": f"\n\n<pdf_content>\n{text}\n</pdf_content>"},
                ]}
            ],
            system=SYSTEM_PROMPT,
        )
        result = self._parse_json(raw)
        return {grp["name"]: grp.get("accounts", [])
                for grp in result.get("groups", []) if grp.get("name")}

    def extract_scan_pdf(self, pages_png: list[bytes], is_bwa: bool = False,
                          doc_type: str | None = None) -> dict[str, Any]:
        effective = doc_type or ("bwa" if is_bwa else "jahresabschluss")
        prompt = _select_prompt(effective, vision=True)
        images = [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(p).decode(),
            }}
            for p in pages_png
        ]
        raw = self._call(
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, *images]}],
            system=SYSTEM_PROMPT,
        )
        return self._parse_json(raw)

    def _call(self, messages: list[dict[str, Any]], retries: int = 3,
              system: str | None = None) -> str:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model, "max_tokens": self.max_tokens, "messages": messages,
                }
                if system:
                    kwargs["system"] = system
                msg = self.sdk.messages.create(**kwargs)
                return msg.content[0].text
            except APIStatusError as e:
                last_err = e
                if getattr(e, "status_code", None) == 429 and attempt < retries - 1:
                    # Fix 5A: Rate-limit resets oft 30-60s; längere Backoff-Zeiten
                    time.sleep(min(2 ** attempt * 10, 60))
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
