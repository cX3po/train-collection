"""
engine.py — Universal Visual Identifier Engine

Send any image + any prompt → get structured JSON back.
Swap the prompt, swap the app. Everything else stays the same.

Providers:
  - haiku: Claude Haiku Vision (~$0.01/call)
  - gemini: Gemini Flash Vision (needs paid API or free tier)
  - (future: openai, local)

Usage:
    from engine import VisionEngine

    engine = VisionEngine()  # auto-detects provider from env

    # Home inventory
    items = engine.analyze(image_bytes, INVENTORY_PROMPT)

    # 3D print identification
    items = engine.analyze(image_bytes, PRINT_PROMPT)

    # Garage sale treasure hunt
    items = engine.analyze(image_bytes, GARAGE_SALE_PROMPT)
"""

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import requests


# ── Result ───────────────────────────────────────────────────────────────────

@dataclass
class VisionResult:
    """One identified item from a photo. Fields are prompt-dependent."""
    raw: dict = field(default_factory=dict)
    provider: str = ""
    warning: str = ""

    def get(self, key: str, default=None):
        return self.raw.get(key, default)

    def __getitem__(self, key: str):
        return self.raw[key]

    def __repr__(self):
        name = self.raw.get("item_name", self.raw.get("name", "?"))
        return f"VisionResult({name})"


# ── Image utilities ──────────────────────────────────────────────────────────

def detect_mime(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:4] == b"RIFF":
        return "image/webp"
    if image_bytes[:4] == b"GIF8":
        return "image/gif"
    return "image/jpeg"


def validate_image(image_bytes: bytes | None) -> bytes:
    """Validate image bytes. Raises ValueError if invalid."""
    if not image_bytes:
        raise ValueError("No image data.")
    if len(image_bytes) < 100:
        raise ValueError("Image data too small — likely corrupt.")
    return image_bytes


# ── Providers ────────────────────────────────────────────────────────────────

def _call_haiku(image_bytes: bytes, prompt: str, api_key: str, timeout: int) -> str:
    """Call Claude Haiku Vision API. Returns raw text response."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = detect_mime(image_bytes)

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        },
        timeout=timeout,
    )

    if not resp.ok:
        raise RuntimeError(f"Haiku error ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    return data["content"][0]["text"]


def _call_gemini(image_bytes: bytes, prompt: str, api_key: str, timeout: int) -> str:
    """Call Gemini Vision API. Returns raw text response."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = detect_mime(image_bytes)

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-2.0-flash:generateContent?key={api_key}",
        json={
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        },
        timeout=timeout,
    )

    if not resp.ok:
        raise RuntimeError(f"Gemini error ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDERS = {
    "haiku": {
        "call": _call_haiku,
        "env_key": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "call": _call_gemini,
        "env_key": "GEMINI_API_KEY",
    },
}


# ── JSON extraction ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> list[dict] | dict:
    """Extract JSON from LLM response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    result = json.loads(text)
    return result


# ── Engine ───────────────────────────────────────────────────────────────────

class VisionEngine:
    """
    Universal visual identifier. Send image + prompt, get structured JSON.

    Auto-detects provider from environment:
      - ANTHROPIC_API_KEY → haiku
      - GEMINI_API_KEY → gemini

    Or specify: VisionEngine(provider="haiku")
    Or BYOK:   VisionEngine(provider="haiku", api_key="sk-...")
    """

    def __init__(
        self,
        provider: str | None = None,
        api_key: str = "",
        timeout: int = 45,
    ):
        self.timeout = timeout

        # Auto-detect provider
        if provider:
            self.provider = provider
        elif os.getenv("ANTHROPIC_API_KEY"):
            self.provider = "haiku"
        elif os.getenv("GEMINI_API_KEY"):
            self.provider = "gemini"
        else:
            raise RuntimeError(
                "No vision provider found. Set ANTHROPIC_API_KEY or GEMINI_API_KEY."
            )

        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unknown provider: {self.provider}. Use: {list(_PROVIDERS.keys())}")

        # API key: explicit > env var
        prov = _PROVIDERS[self.provider]
        self.api_key = api_key or os.getenv(prov["env_key"], "")
        if not self.api_key:
            raise RuntimeError(f"{prov['env_key']} not set for provider {self.provider}.")

        self._call = prov["call"]

    def analyze(self, image_bytes: bytes, prompt: str) -> list[VisionResult]:
        """
        Send image + prompt to vision API.
        Expects the prompt to request JSON array output.
        Returns list of VisionResult objects.
        """
        image_bytes = validate_image(image_bytes)

        try:
            raw_text = self._call(image_bytes, prompt, self.api_key, self.timeout)
        except requests.Timeout:
            raise RuntimeError(f"{self.provider} timed out.")
        except requests.ConnectionError:
            raise RuntimeError(f"No internet — {self.provider} unreachable.")

        parsed = _extract_json(raw_text)

        # Handle both array and single object responses
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise RuntimeError(f"Expected JSON array, got {type(parsed).__name__}")

        results = []
        for item in parsed:
            if isinstance(item, dict):
                results.append(VisionResult(raw=item, provider=self.provider))

        return results

    def analyze_raw(self, image_bytes: bytes, prompt: str) -> str:
        """
        Send image + prompt, return raw text response (no JSON parsing).
        Useful for free-form analysis like 3D print suggestions.
        """
        image_bytes = validate_image(image_bytes)
        return self._call(image_bytes, prompt, self.api_key, self.timeout)
