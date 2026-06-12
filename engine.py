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
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

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


MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB hard cap (app layer caps at 10 MB)


def validate_image(image_bytes: bytes | None) -> bytes:
    """Validate image bytes. Raises ValueError if invalid."""
    if not image_bytes:
        raise ValueError("No image data.")
    if len(image_bytes) < 100:
        raise ValueError("Image data too small — likely corrupt.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image too large ({len(image_bytes) / 1024 / 1024:.1f} MB). Max {MAX_IMAGE_BYTES // 1024 // 1024} MB.")
    # Reject unrecognized magic bytes — don't silently treat random bytes as JPEG.
    head = image_bytes[:4]
    if head not in (b"\x89PNG", b"RIFF", b"GIF8") and image_bytes[:3] != b"\xff\xd8\xff":
        raise ValueError("Unrecognized image format. Use JPG, PNG, WebP, or GIF.")
    return image_bytes


# ── Providers ────────────────────────────────────────────────────────────────

def _find_claude_cli() -> str:
    """Locate the Claude (Max-subscription) CLI binary, or '' if absent."""
    cli = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return cli if cli and os.path.exists(cli) else ""


def _call_subscription(image_bytes: bytes, prompt: str, api_key: str, timeout: int) -> str:
    """Vision via the operator's Max subscription (the `claude` CLI) — NO API key.

    Writes the photo to a temp file and asks `claude -p` to read it, the SAME
    subscription path the app already uses for chat. This is the canonical vision
    route for a subscription-only account where raw API keys aren't in use:
    routing through the subscription means no key can break a scan (corruption,
    rotation, or deactivation are all moot). `api_key` is ignored here (kept only
    for call-signature parity with the other providers)."""
    cli = _find_claude_cli()
    if not cli:
        raise RuntimeError("Claude subscription CLI not found — install it or set GEMINI_API_KEY.")
    ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(
        detect_mime(image_bytes), ".jpg")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(image_bytes)
        tmp.close()
        # `claude -p` reads local image files referenced by path. Cold-start + vision
        # is slower than a raw API call, so floor the timeout generously (a one-off
        # scan that takes ~60-90s beats a scan that 401s instantly).
        eff_timeout = max(timeout, 120)
        full_prompt = f"Read the image file at {tmp.name}. {prompt}"
        try:
            proc = subprocess.run(
                [cli, "-p", full_prompt],
                capture_output=True, text=True, timeout=eff_timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Subscription vision timed out after {eff_timeout}s.")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            raise RuntimeError(f"Subscription vision failed (rc={proc.returncode}): {err}")
        out = (proc.stdout or "").strip()
        if not out:
            raise RuntimeError("Subscription vision returned no output.")
        return out
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
        error_detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
        print(f"[engine.haiku] HTTP {resp.status_code}: {error_detail}")
        if resp.status_code == 400:
            # HTTP 400 usually means invalid API key or malformed request
            if "api_key" in error_detail.lower() or "invalid" in error_detail.lower():
                error_msg = "Invalid API key. Check ANTHROPIC_API_KEY in ~/axiom/.env"
            else:
                error_msg = f"API request error: {error_detail[:150]}"
        else:
            error_msg = error_detail[:200] if error_detail else f"HTTP {resp.status_code}"
        raise RuntimeError(f"Anthropic API error ({resp.status_code}): {error_msg}")

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
        print(f"[engine.gemini] HTTP {resp.status_code}: {resp.text[:500]}")
        raise RuntimeError("Vision service unavailable. Try again in a moment.")

    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDERS = {
    "subscription": {
        "call": _call_subscription,
        "env_key": None,          # keyless — runs on the Max subscription CLI
    },
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
    """Extract JSON from an LLM response. Handles three shapes, in order:
      1. a ```json fenced block anywhere in the text,
      2. the whole text as bare JSON,
      3. the first balanced [...] / {...} span embedded in prose.
    (3) matters for the subscription CLI, which is more conversational than the
    raw API and often writes a sentence of preamble before the JSON block."""
    text = text.strip()

    # 1) Fenced code block anywhere (```json ... ``` or ``` ... ```).
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 2) Whole text as-is.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3) Widest array/object span (tolerates leading/trailing prose; each
    #    candidate is json.loads-validated, so an over-wide span just falls through).
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    print("[engine.json_parse] Failed to parse JSON from vision response")
    print(f"[engine.json_parse] Response text (first 500 chars): {text[:500]}")
    raise RuntimeError(f"Vision API returned invalid JSON. Response: {text[:100]}...")


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

        # Auto-detect provider. Prefer the subscription CLI when present (a
        # subscription-only account has no working raw API key). Fall back to a
        # raw key only if the CLI is absent (e.g. a BYOK deployment).
        if provider:
            self.provider = provider
        elif _find_claude_cli():
            self.provider = "subscription"
        elif os.getenv("ANTHROPIC_API_KEY"):
            self.provider = "haiku"
        elif os.getenv("GEMINI_API_KEY"):
            self.provider = "gemini"
        else:
            raise RuntimeError(
                "No vision provider found. Install the Claude CLI (subscription) "
                "or set ANTHROPIC_API_KEY / GEMINI_API_KEY."
            )

        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unknown provider: {self.provider}. Use: {list(_PROVIDERS.keys())}")

        # API key: explicit > env var. The subscription provider needs NO key.
        prov = _PROVIDERS[self.provider]
        self.api_key = ""
        if prov["env_key"]:
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
