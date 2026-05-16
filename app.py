#!/usr/bin/env python3
"""Train Collection — family Streamlit app with AX chat."""
import streamlit as st
import sqlite3
import os
import io
import html as _html
import shutil
import subprocess
import urllib.parse
import uuid
import pandas as pd
import segno
from datetime import datetime

# Family-deployment limits (5-user-max app self-hosted on the family server)
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB per uploaded photo
MAX_PHOTOS_PER_SCAN = 10
SQLITE_BUSY_TIMEOUT_MS = 5000


def _claude_cli_supports(flag: str) -> bool:
    """Verify the installed claude CLI accepts a given flag.

    Without this check, a CLI version mismatch would silently fall through
    to the API on every chat call — re-creating the per-token billing scar
    the CLI swap was meant to close.
    """
    bin_path = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(bin_path):
        return False
    try:
        r = subprocess.run([bin_path, "--help"], capture_output=True, text=True, timeout=5)
        return flag in (r.stdout + r.stderr)
    except Exception:
        return False

# Load API key (used for vision; chat uses CLI under the operator's Max subscription)
def load_api_key():
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key: return key
    except (FileNotFoundError, KeyError, AttributeError):
        pass
    env_path = os.path.expanduser("~/axiom/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.strip().split("=", 1)[1].strip('"').strip("'")
    return os.getenv("ANTHROPIC_API_KEY", "")

API_KEY = load_api_key()
if API_KEY: os.environ["ANTHROPIC_API_KEY"] = API_KEY

CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "trains.db")
PHOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "photos")
# Why local-not-axiom-media-engine (Bundle 10 reuse decision 2026-05-16):
# axiom's engines/media/engine.py would be the "right" backend, but the train
# app is a standalone Streamlit process — adopting it requires either running
# inside the axiom venv or per-upload HTTP calls to axiom-server. Both break
# the app's runs-standalone property for a 5-user-max family deployment.
# Local photos keep the train-collection repo self-contained.
PHOTO_JPEG_QUALITY = 85
PHOTO_MAX_DIMENSION = 1600  # px — downscale longest side to bound disk usage
# Public base URL the QR labels point at. Env override lets a different
# Cloudflare hostname or local-test target generate labels that scan
# back into the right place (Codex 2026-05-16 MEDIUM).
DEFAULT_PUBLIC_BASE_URL = os.getenv("TRAIN_APP_BASE_URL", "https://train.path-os.net")


def _safe_photo_path(rel_or_abs: str) -> str | None:
    """Resolve a stored photo_path and confirm it stays inside PHOTOS_DIR.

    Codex 2026-05-16 MEDIUM: gallery/focus do os.path.join(app_dir, fphoto)
    blindly. If a future import/admin edit ever puts an absolute path or
    `../` traversal in photo_path, the UI would read files outside
    db/photos. This validator returns the canonical absolute path if and
    only if it lives inside PHOTOS_DIR; otherwise None.
    """
    if not rel_or_abs:
        return None
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(app_dir, rel_or_abs))
    photos_dir_abs = os.path.abspath(PHOTOS_DIR)
    try:
        if os.path.commonpath([candidate, photos_dir_abs]) != photos_dir_abs:
            return None
    except ValueError:
        # Mixed drive on Windows / completely unrelated paths
        return None
    return candidate if os.path.exists(candidate) else None


def _md_cell(value):
    """Escape characters that would break a markdown table cell."""
    if value is None:
        return ""
    s = str(value)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _flat(value):
    """Flatten value to single-line plain text. For non-table outputs (auction packet)."""
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ")


_DB_INITIALIZED = False  # one-shot flag — WAL is a persistent file-level setting

NOTABLE_BRANDS = {"lionel","american flyer","marx","ives","mth","williams","k-line",
    "weaver","atlas","bachmann","kato","athearn","walthers","brass","tenshodo",
    "overland","3rd rail","sunset","rivarossi","marklin","fleischmann","hornby"}

def is_notable(item_name, brand, era=""):
    name = (item_name or "").lower()
    b = (brand or "").lower()
    if any(nb in b for nb in NOTABLE_BRANDS): return True
    if any(kw in name for kw in ["hudson","berkshire","gp-7","f3","gp-9","alco","emd",
        "steam","diesel","brass","prewar","standard gauge","blue comet"]): return True
    if era and any(kw in era.lower() for kw in ["prewar","1930","1940","1950","brass"]): return True
    return False

def init_db():
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    c.execute("""CREATE TABLE IF NOT EXISTS trains (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT, brand TEXT, scale TEXT, era TEXT,
        item_type TEXT, catalog_number TEXT, quantity INTEGER DEFAULT 1,
        condition TEXT, has_box INTEGER DEFAULT 0,
        estimated_value TEXT, value_notes TEXT,
        location TEXT, description TEXT,
        is_notable INTEGER DEFAULT 0, notes TEXT, last_updated TEXT
    )""")
    # Bundle 10 migration: add photo_path if missing. ALTER TABLE is idempotent-
    # by-introspection — check existing columns, only add if absent. Doing it
    # here keeps the migration co-located with the schema definition.
    existing_cols = {row[1] for row in c.execute("PRAGMA table_info(trains)").fetchall()}
    if "photo_path" not in existing_cols:
        c.execute("ALTER TABLE trains ADD COLUMN photo_path TEXT")
    conn.commit(); conn.close()
    _DB_INITIALIZED = True


FAMILY_CONFIG_PATH = os.path.expanduser("~/axiom/config/family_members.json")
IDENTITY_CONFIG_PATH = os.path.expanduser("~/axiom/config/identity.json")
_FAMILY_HINTS_CACHE: dict | None = None


def _load_family_hints() -> dict:
    """Pull display-only hints (collection title + sign-in placeholder) from
    the family config when present. Falls back to generic strings so the
    train app still runs standalone outside the axiom directory tree.

    Codex 2026-05-16 MEDIUM PRIVACY: previous versions hardcoded
    Dad/Phil/Colin in user-facing copy. The hints are now optional —
    deployer can leave them empty for a fully generic install.
    """
    global _FAMILY_HINTS_CACHE
    if _FAMILY_HINTS_CACHE is not None:
        return _FAMILY_HINTS_CACHE
    hints = {
        "title": "Train Collection",
        "placeholder": "Your name",
    }
    try:
        if os.path.exists(FAMILY_CONFIG_PATH):
            import json as _json
            data = _json.loads(open(FAMILY_CONFIG_PATH).read())
            names = [m.get("name", "").split()[0]
                     for m in data.get("members", {}).values()
                     if m.get("name")]
            if names:
                hints["placeholder"] = "e.g. " + ", ".join(names[:3])
    except Exception as e:
        print(f"[family_hints] non-fatal load issue: {e}")
    try:
        if os.path.exists(IDENTITY_CONFIG_PATH):
            import json as _json
            ident = _json.loads(open(IDENTITY_CONFIG_PATH).read())
            t = ident.get("train_app_title") or ident.get("display_name", "")
            if t:
                hints["title"] = f"{t} Train Collection" if "Train" not in t else t
    except Exception:
        pass
    _FAMILY_HINTS_CACHE = hints
    return hints


PRICE_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "greenberg_seed.json")
_PRICE_SEED_CACHE: dict | None = None


def _load_price_seed() -> dict:
    """Lazy-loaded, in-process cached price-seed reference.

    Returns the parsed JSON. Cached because lookup happens per-item-add and the
    file is small but parsing is non-trivial. Cache is None-checked on each call
    so a hot-reload doesn't get stuck on a stale parse.
    """
    global _PRICE_SEED_CACHE
    if _PRICE_SEED_CACHE is not None:
        return _PRICE_SEED_CACHE
    try:
        with open(PRICE_SEED_PATH, "r") as f:
            import json as _json
            _PRICE_SEED_CACHE = _json.load(f)
    except Exception as e:
        print(f"[price_seed] load failed: {e}")
        _PRICE_SEED_CACHE = {"items": []}
    return _PRICE_SEED_CACHE


def _normalize_catalog(catalog: str) -> str:
    """Normalize a catalog number for matching. Strips whitespace, uppercases,
    and removes leading punctuation. Returns the FULL normalized string —
    callers that want a Lionel-style prefix match should request it
    explicitly via lookup_price_seed's two-pass strategy.

    Codex 2026-05-16: the previous implementation split on '-' which collapsed
    MTH catalogs like '20-3030' down to '20', making every MTH 20-xxxx item
    auto-match the 20-3030 seed and get the wrong value. Manufacturers like
    Lionel postwar use bare numerics (2333, 726) and sometimes -suffix
    variants (2333-100); MTH uses dashes meaningfully (20-3030 is the full
    catalog). Returning the full string and letting lookup_price_seed do
    a two-pass (exact then trimmed) is the only safe normalization.
    """
    if not catalog:
        return ""
    s = str(catalog).strip().upper()
    for prefix in ("#", "NO.", "NO ", "CAT.", "CAT "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s


def _catalog_prefix(catalog: str) -> str:
    """Return the leading numeric/alpha-block of a Lionel-style variant.
    `2333-100` -> `2333`. Returns empty for catalogs without a dash so MTH
    `20-3030` is NOT collapsed here — that path is exact-match only.
    """
    s = _normalize_catalog(catalog)
    if "-" not in s:
        return ""
    head = s.split("-", 1)[0]
    # Only use prefix matching when the head looks Lionel-shaped (>= 3 chars).
    # MTH's `20-xxxx` heads (2 chars) are NOT eligible — protects 20-3030.
    return head if len(head) >= 3 else ""


def lookup_price_seed(brand: str, catalog: str) -> dict | None:
    """Return the seed entry for a (brand, catalog) pair, or None.

    Brand matching is loose (case-insensitive substring) since vision OCR
    returns "Lionel Corporation" / "lionel" / "LIONEL" inconsistently.

    Catalog matching is two-pass:
      1. Exact normalized match (handles bare numerics + MTH 20-3030).
      2. Lionel-style prefix match (`2333-100` against seed `2333`), but
         ONLY if the head is >= 3 chars — protects MTH 2-digit prefixes.
    """
    target_full = _normalize_catalog(catalog)
    target_prefix = _catalog_prefix(catalog)
    if not target_full:
        return None
    target_brand = (brand or "").strip().lower()

    def _brand_matches(entry):
        if not target_brand:
            return True
        entry_brand = (entry.get("brand") or "").strip().lower()
        if not entry_brand:
            return True
        return entry_brand in target_brand or target_brand in entry_brand

    seed = _load_price_seed()
    # Pass 1: exact match on the full normalized catalog.
    for entry in seed.get("items", []):
        if _normalize_catalog(entry.get("catalog_number", "")) != target_full:
            continue
        if _brand_matches(entry):
            return entry
    # Pass 2: prefix match for Lionel-style variants only.
    if target_prefix:
        for entry in seed.get("items", []):
            if _normalize_catalog(entry.get("catalog_number", "")) != target_prefix:
                continue
            if _brand_matches(entry):
                return entry
    return None


def _apply_price_seed(d: dict) -> tuple[str, str]:
    """Compute (estimated_value, value_notes) for a scan-result dict,
    falling back to the price seed when the vision engine did not provide
    a value. Returns the existing value untouched if already set.
    """
    existing_val = d.get("estimated_value")
    if existing_val not in (None, "", 0, "0"):
        return str(existing_val), d.get("value_notes", "") or ""
    match = lookup_price_seed(d.get("brand", ""), d.get("catalog_number", ""))
    if not match:
        return "", d.get("value_notes", "") or ""
    mid = int((match["low"] + match["high"]) / 2)
    note_existing = d.get("value_notes", "") or ""
    seed_note = (
        f"~${match['low']}-${match['high']} from price seed "
        f"({match.get('era', '')}). {match.get('notes', '')}"
    ).strip()
    combined = f"{note_existing} | {seed_note}" if note_existing else seed_note
    return str(mid), combined


def _label_code(train_id: int) -> str:
    """DASH-style stable code for a train row. Format: T-NNNN (zero-padded
    so labels sort nicely; widens to 5 digits automatically past 9999)."""
    width = max(4, len(str(train_id)))
    return f"T-{train_id:0{width}d}"


def _qr_svg_for(train_id: int, base_url: str, user: str) -> str:
    """Return an inline SVG QR for a single train item.

    Encodes a URL that opens the app signed-in as `user` and focuses the
    target item (the app reads ?focus=N to highlight on landing). Inline
    SVG so the printable label page is single-file — no external image
    requests when Dad's printer is offline.

    Codex 2026-05-16: use urllib.parse.quote so usernames with `&`, `?`,
    `=`, `#` etc don't corrupt the URL.
    """
    safe_user = urllib.parse.quote(user, safe="")
    url = f"{base_url.rstrip('/')}/?user={safe_user}&focus={train_id}"
    qr = segno.make(url, error="m")
    buf = io.StringIO()
    qr.save(buf, kind="svg", scale=4, border=0, xmldecl=False, svgns=False, omithw=True)
    return buf.getvalue()


def _save_train_photo(img_bytes: bytes, train_id: int) -> str | None:
    """Compress + persist an uploaded photo for a train row.

    Returns the relative path stored in the DB (or None on failure).
    Failure is non-fatal — the train row exists with photo_path NULL and
    the UI shows the row without a thumbnail. Saving the photo should
    never block adding the item.
    """
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(img_bytes))
        # Normalize EXIF orientation so phone photos don't display sideways.
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # Downscale the longest side. Bounds disk usage and keeps the
        # Collection tab responsive even at 1000+ items.
        w, h = img.size
        longest = max(w, h)
        if longest > PHOTO_MAX_DIMENSION:
            scale = PHOTO_MAX_DIMENSION / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        out_path = os.path.join(PHOTOS_DIR, f"{train_id}.jpg")
        img.save(out_path, format="JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
        # Store path relative to the app dir so the DB stays portable if
        # someone copies the train-collection repo to a different machine.
        return os.path.relpath(out_path, os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        print(f"[photo_save] failed for train_id={train_id}: {e}")
        return None

def get_db():
    init_db()
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return conn

# AX system prompt (shared by CLI + API fallback paths)
AX_SYSTEM_PROMPT = """You are AX, a friendly and knowledgeable AI assistant for the family.
You're an expert in model trains and railroadiana - Lionel, American Flyer, Marx, HO, N, O gauge, brass, vintage tinplate.
You help identify trains, estimate values, advise on selling via auction houses or eBay.
You know Heritage Auctions, Stout Auctions, and other train auction specialists.
Be warm, practical, and honest about what you know. Never invent prices."""

GENERIC_AX_FAILURE = "AX is unavailable right now. Try again in a moment."

# Verify CLI flag support at module load — guards against silent fall-through to API.
CLAUDE_CLI_USABLE = os.path.exists(CLAUDE_BIN) and _claude_cli_supports("--append-system-prompt")
if os.path.exists(CLAUDE_BIN) and not CLAUDE_CLI_USABLE:
    print(f"[trains] WARNING: claude CLI at {CLAUDE_BIN} missing --append-system-prompt; chat will use API (per-token billing).")


def _chat_via_cli(message, system, history):
    """Run AX via local Claude CLI (Max subscription, $0 marginal cost).

    Scrubs ANTHROPIC_API_KEY from the child env — claude CLI prefers the API
    key over the Max OAuth subscription when both are present, and inheriting
    the parent's key would silently bill per-token (COST.md scar 2026-04-22).
    """
    if not CLAUDE_CLI_USABLE:
        return None  # caller falls back

    transcript_parts = []
    if history:
        for msg in history[-10:]:
            role = msg.get("role", "user").upper()
            transcript_parts.append(f"{role}: {msg['content']}")
    transcript_parts.append(f"USER: {message}")
    full_input = "\n\n".join(transcript_parts)

    child_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cmd = [CLAUDE_BIN, "-p", "--model", "claude-haiku-4-5-20251001",
           "--append-system-prompt", system]
    try:
        result = subprocess.run(cmd, input=full_input, capture_output=True,
                                text=True, timeout=60, env=child_env)
    except subprocess.TimeoutExpired:
        print("[chat_with_ax] CLI timeout after 60s")
        return None
    except Exception as e:
        print(f"[chat_with_ax] CLI subprocess error: {e}")
        return None

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    print(f"[chat_with_ax] CLI rc={result.returncode} stderr={result.stderr[:300]}")
    return None


def _chat_via_api(message, system, history):
    """Fallback: hit the Anthropic API directly."""
    import requests
    if not API_KEY:
        return GENERIC_AX_FAILURE
    messages = []
    if history:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})
    try:
        resp = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
                  "system": system, "messages": messages}, timeout=30)
        if resp.ok:
            return resp.json()["content"][0]["text"]
        print(f"[chat_with_ax] API HTTP {resp.status_code}: {resp.text[:300]}")
        return GENERIC_AX_FAILURE
    except Exception as e:
        print(f"[chat_with_ax] API exception: {e}")
        return GENERIC_AX_FAILURE


def chat_with_ax(message, context="", history=None):
    """AX chat. Tries local Claude CLI first (free under Max), falls back to API."""
    system = AX_SYSTEM_PROMPT + (f"\n\nContext: {context}" if context else "")
    cli_reply = _chat_via_cli(message, system, history)
    if cli_reply is not None:
        return cli_reply
    return _chat_via_api(message, system, history)

def generate_sell_listing(item_info, method="eBay"):
    if method == "eBay":
        return chat_with_ax(f"Create a ready-to-use eBay listing for this model train item: {item_info}. Include title (80 chars), description, category, starting price, and shipping notes for fragile model trains.")
    elif method == "Auction House":
        return chat_with_ax(f"Write a professional submission to a train auction house (like Stout Auctions or Heritage) for: {item_info}. Include item description, condition, and ask about consignment terms.")
    else:
        return chat_with_ax(f"Write a description for selling at a train show or hobby shop: {item_info}. Include fair asking price and negotiation tips.")

PHOTO_GUIDE_SVG = """
<svg viewBox="0 0 500 300" xmlns="http://www.w3.org/2000/svg" style="max-width:500px;font-family:sans-serif;">
  <rect x="100" y="80" width="300" height="100" rx="5" fill="#d4c4a8" stroke="#333" stroke-width="2"/>
  <text x="250" y="135" text-anchor="middle" font-size="14" fill="#666">MODEL TRAIN</text>
  <circle cx="150" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <circle cx="250" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <circle cx="350" cy="180" r="15" fill="none" stroke="#333" stroke-width="2"/>
  <line x1="90" y1="130" x2="40" y2="100" stroke="#e74c3c" stroke-width="1.5"/>
  <text x="5" y="95" font-size="10" fill="#e74c3c">Brand/Logo?</text>
  <line x1="410" y1="130" x2="450" y2="100" stroke="#3498db" stroke-width="1.5"/>
  <text x="415" y="95" font-size="10" fill="#3498db">Catalog #?</text>
  <line x1="250" y1="75" x2="250" y2="50" stroke="#2ecc71" stroke-width="1.5"/>
  <text x="250" y="45" text-anchor="middle" font-size="10" fill="#2ecc71">Paint condition?</text>
  <line x1="150" y1="200" x2="100" y2="230" stroke="#9b59b6" stroke-width="1.5"/>
  <text x="30" y="235" font-size="10" fill="#9b59b6">Wheels/trucks</text>
  <text x="250" y="270" text-anchor="middle" font-size="11" font-weight="bold" fill="#333">Photo flat, good light, show markings</text>
</svg>
"""

def main():
    st.set_page_config(page_title="Train Collection", page_icon="\U0001f682", layout="wide")

    # Family login — query-string carries identity so a bookmarked link
    # (https://train.path-os.net/?user=Dad) signs Dad in automatically.
    # Closes Bundle 9 Bug 2: "when he logs back on he doesn't know where it's at."
    if "user_name" not in st.session_state:
        qp_user = st.query_params.get("user", "")
        st.session_state.user_name = qp_user.strip() if isinstance(qp_user, str) else ""
    if not st.session_state.user_name:
        st.markdown("## Welcome to the Train Collection")
        st.markdown("**Sign in so AX knows who you are:**")
        name = st.text_input("Your name", placeholder=_load_family_hints()["placeholder"])
        if name and st.button("Sign In", type="primary"):
            st.session_state.user_name = name
            st.query_params["user"] = name
            st.rerun()
        st.stop()
    user_name = st.session_state.user_name

    if "app_title" not in st.session_state:
        st.session_state.app_title = _load_family_hints()["title"]
    st.title(st.session_state.app_title)

    init_db()
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM trains").fetchone()[0]
    total_qty = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM trains").fetchone()[0]
    notable = conn.execute("SELECT COUNT(*) FROM trains WHERE is_notable=1").fetchone()[0]
    with_values = conn.execute("SELECT COUNT(*) FROM trains WHERE estimated_value!='' AND estimated_value IS NOT NULL").fetchone()[0]
    brands = [r[0] for r in conn.execute("SELECT DISTINCT brand FROM trains WHERE brand!='' ORDER BY brand").fetchall()]
    scales = [r[0] for r in conn.execute("SELECT DISTINCT scale FROM trains WHERE scale!='' ORDER BY scale").fetchall()]
    last_row = conn.execute("SELECT brand, item_name, last_updated FROM trains WHERE last_updated IS NOT NULL AND last_updated!='' ORDER BY last_updated DESC LIMIT 1").fetchone()
    conn.close()

    with st.sidebar:
        st.markdown(f"**Signed in as:** {user_name}")
        if st.button("Sign Out", key="signout"):
            st.session_state.user_name = ""
            st.query_params.clear()
            st.rerun()
        st.markdown("---")
        new_title = st.text_input("Collection Title", st.session_state.app_title)
        if new_title != st.session_state.app_title:
            st.session_state.app_title = new_title; st.rerun()
        st.markdown("---")
        search = st.text_input("Search", "")
        brand_filter = st.selectbox("Brand", ["All"] + brands)
        scale_filter = st.selectbox("Scale", ["All"] + scales)
        notable_only = st.checkbox("Valuable only", False)
        st.markdown("---")
        st.markdown(f"**Items:** {total:,} | **Pieces:** {total_qty:,}")
        st.markdown(f"**Notable:** {notable:,} | **Priced:** {with_values:,}")

    # Bundle 11 focus handler — when a label-sheet QR is scanned, the QR URL
    # carries `?focus=N`. Surface the matching item up front so the scan-to-app
    # flow has a clear payoff (the user lands on the app AND sees the item).
    focus_id_raw = st.query_params.get("focus", "")
    if focus_id_raw:
        try:
            focus_id = int(focus_id_raw)
            conn_f = get_db()
            focus_row = conn_f.execute(
                "SELECT id, item_name, brand, scale, era, catalog_number, "
                "condition, estimated_value, photo_path FROM trains WHERE id=?",
                (focus_id,),
            ).fetchone()
            conn_f.close()
            if focus_row:
                fid, fname, fbrand, fscale, fera, fcatn, fcond, fval, fphoto = focus_row
                with st.container(border=True):
                    st.markdown(
                        f"### \U0001f516 Scanned label — **{_label_code(fid)}**"
                    )
                    fcols = st.columns([1, 2])
                    with fcols[0]:
                        photo_abs = _safe_photo_path(fphoto)
                        if photo_abs:
                            st.image(photo_abs, use_container_width=True)
                        else:
                            st.caption(":camera_with_flash: _no photo on file_")
                    with fcols[1]:
                        st.markdown(f"**{fname or 'Unknown'}**")
                        meta_bits = [b for b in [fbrand, fscale, fera] if b]
                        if meta_bits:
                            st.markdown(" · ".join(meta_bits))
                        if fcatn:
                            st.caption(f"Catalog #: {fcatn}")
                        if fcond:
                            st.caption(f"Condition: {fcond}")
                        if fval:
                            st.caption(f"Est. value: ${fval}")
                    if st.button("Clear focus", key="clear_focus"):
                        # Drop the focus param so a refresh doesn't reopen the
                        # banner. Preserves user= so the sign-in stays warm.
                        st.query_params.pop("focus", None)
                        st.rerun()
            else:
                st.warning(
                    f"Scanned label was for item ID {focus_id}, but no matching "
                    f"row exists. The item may have been deleted."
                )
        except (TypeError, ValueError):
            # Bad focus value — silently ignore; the welcome card below still renders.
            pass

    # Welcome-back card — first thing the user sees after sign-in so they always
    # know where they left off. Closes Bundle 9 Bug 2.
    if total > 0:
        last_when = "recently"
        last_item = "an item"
        if last_row and last_row[2]:
            try:
                last_when = datetime.fromisoformat(last_row[2]).strftime("%B %d")
            except (ValueError, TypeError):
                pass
            last_item = f"{(last_row[0] or '').strip()} {(last_row[1] or '').strip()}".strip() or "an item"
        st.success(
            f"\U0001f44b Welcome back, **{user_name}** — you have **{total:,} items** saved "
            f"({total_qty:,} pieces, {notable:,} notable). "
            f"Last added: **{last_item}** on {last_when}."
        )
    else:
        st.info(
            f"\U0001f44b Welcome, **{user_name}**. Your collection is empty — "
            f"head to **Scan Items** to photograph your first train."
        )

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Collection", "Scan Items", "Ask AX", "Sell Items", "Stats", "Import/Export"
    ])

    # ── Collection ──
    with tab1:
        conn = get_db()
        q = "SELECT * FROM trains WHERE 1=1"
        p = []
        if search:
            q += " AND (LOWER(item_name) LIKE ? OR LOWER(brand) LIKE ? OR LOWER(catalog_number) LIKE ?)"
            s = f"%{search.lower()}%"; p.extend([s,s,s])
        if brand_filter != "All": q += " AND LOWER(brand) LIKE ?"; p.append(f"%{brand_filter.lower()}%")
        if scale_filter != "All": q += " AND scale=?"; p.append(scale_filter)
        if notable_only: q += " AND is_notable=1"
        q += " ORDER BY brand, item_name"
        df = pd.read_sql_query(q, conn, params=p); conn.close()

        if df.empty:
            if total == 0: st.info("No items yet. Use Scan Items to photograph your trains, or Import/Export to paste data.")
            else: st.info("No items match filters.")
        else:
            view_mode = st.radio(
                "View", ["Table (edit)", "Gallery (with photos)"],
                horizontal=True, key="collection_view_mode",
                label_visibility="collapsed",
            )
            st.markdown(f"**{len(df)} items**")

            if view_mode.startswith("Gallery"):
                # Bundle 10 Gallery view — card grid showing the saved photo + key
                # metadata for each item. Read-only by design (editing happens in
                # Table view) so it stays fast even at 1000+ items.
                cards_per_row = 3
                rows = df.to_dict("records")
                for i in range(0, len(rows), cards_per_row):
                    cols = st.columns(cards_per_row)
                    for j, row in enumerate(rows[i:i+cards_per_row]):
                        with cols[j]:
                            photo_abs = _safe_photo_path(row.get("photo_path") or "")
                            if photo_abs:
                                st.image(photo_abs, use_container_width=True)
                            else:
                                st.caption(":camera_with_flash: _no photo_")
                            name = (row.get("item_name") or "Unknown").strip()
                            brand = (row.get("brand") or "").strip()
                            val = (row.get("estimated_value") or "").strip()
                            tid = int(row['id'])
                            st.markdown(f"**{name}**  \n{brand} · `{_label_code(tid)}`")
                            if val:
                                st.caption(f"Est. value: ${val}")

            else:
                edit_cols = ["id","item_name","brand","scale","era","item_type","catalog_number",
                            "quantity","condition","has_box","estimated_value","location","notes"]
                avail = [c for c in edit_cols if c in df.columns]
                col_config = {"id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                             "has_box": st.column_config.CheckboxColumn("Box?")}
                # Claude 2026-05-16: num_rows="dynamic" let users add rows in
                # the table, but the save path only UPDATEs by id — new rows
                # had NaN id and were silently dropped (data loss). Pin to
                # "fixed" since the intended add path is the Scan Items tab.
                edited = st.data_editor(df[avail], column_config=col_config,
                                       use_container_width=True, height=600, num_rows="fixed")
                if st.button("Save Changes", type="primary"):
                    conn = get_db()
                    for _, row in edited.iterrows():
                        if pd.notna(row.get("id")):
                            conn.execute("""UPDATE trains SET item_name=?,brand=?,scale=?,era=?,item_type=?,
                                catalog_number=?,quantity=?,condition=?,has_box=?,estimated_value=?,
                                location=?,notes=?,last_updated=? WHERE id=?""",
                                (row.get("item_name",""),row.get("brand",""),row.get("scale",""),
                                 row.get("era",""),row.get("item_type",""),row.get("catalog_number",""),
                                 row.get("quantity",1),row.get("condition",""),
                                 1 if row.get("has_box") else 0,
                                 row.get("estimated_value",""),row.get("location",""),
                                 row.get("notes",""),datetime.now().isoformat(),row["id"]))
                    conn.commit(); conn.close(); st.success("Saved!")

    # ── Scan Items ──
    with tab2:
        st.subheader("Scan & Identify Trains")
        with st.expander("Photo tips for best identification"):
            st.markdown(PHOTO_GUIDE_SVG, unsafe_allow_html=True)
            st.markdown("""
            1. **Flat surface, good lighting** - no shadows
            2. **Show the brand/logo** - usually on the side or bottom
            3. **Show any numbers** - catalog numbers on the bottom
            4. **Multiple angles** - side, top, bottom for full ID
            5. **Include the box** if you have it - boxes add significant value
            """)

        scan_type = st.radio("Scan mode", [
            "Single Item (1 photo, 1 item)",
            "Shelf/Table Scan (1 photo, finds ALL items)",
            "Batch Scan (multiple photos, 1 item each)"
        ], horizontal=False)
        photos = st.file_uploader(
            f"Upload photo(s) - up to {MAX_PHOTOS_PER_SCAN}, max {MAX_FILE_BYTES // 1024 // 1024} MB each",
            type=["jpg","jpeg","png","webp"], accept_multiple_files=True)

        if photos:
            if len(photos) > MAX_PHOTOS_PER_SCAN:
                st.error(f"Too many photos. Limit is {MAX_PHOTOS_PER_SCAN} per scan; you uploaded {len(photos)}. Take a smaller batch.")
                photos = []
            else:
                oversized = [p.name for p in photos if p.size > MAX_FILE_BYTES]
                if oversized:
                    st.warning(f"Skipping over-{MAX_FILE_BYTES // 1024 // 1024}-MB photo(s): {', '.join(oversized)}")
                    photos = [p for p in photos if p.size <= MAX_FILE_BYTES]
                if photos:
                    total_mb = sum(p.size for p in photos) / 1024 / 1024
                    st.caption(f"{len(photos)} photo(s) selected · {total_mb:.1f} MB total")

        # Bundle 9 fix: scan TRIGGER stores results in session_state; RENDER
        # reads from session_state below. This survives Streamlit reruns so
        # "Add This One" / "Add ALL" no longer make the results vanish — that
        # was the "can only save 3 pictures" symptom Dad reported.
        if photos and st.button("Scan All Photos", type="primary"):
            if not API_KEY:
                st.error("No API key configured.")
            else:
                try:
                    from engine import VisionEngine
                    from train_prompts import TRAIN_IDENTIFIER, TRAIN_ROOM_SCAN
                    engine = VisionEngine(provider="haiku")

                    new_items = []
                    new_images = []
                    for i, photo in enumerate(photos):
                        img_bytes = photo.read()
                        new_images.append(img_bytes)
                        prompt = TRAIN_ROOM_SCAN if scan_type.startswith("Shelf") else TRAIN_IDENTIFIER
                        with st.spinner(f"Scanning photo {i+1} of {len(photos)}..."):
                            results = engine.analyze(img_bytes, prompt)
                            for r in results:
                                r.raw["_photo_index"] = i
                                new_items.append(r.raw)

                    if new_items:
                        st.session_state["scan_results"] = {
                            "images": new_images,
                            "items": new_items,
                            "added": [False] * len(new_items),
                        }
                        st.rerun()  # render block below picks up the fresh state
                    else:
                        st.warning("Could not identify items. Try clearer photos with good lighting.")
                except ValueError as ve:
                    # validate_image: too small, too large, unrecognized format
                    print(f"[scan] validation error: {ve}")
                    st.error(f"Image issue: {ve}")
                except Exception as e:
                    print(f"[scan] error: {e}")
                    st.error("Photo scan failed. Try clearer photos or try again in a moment.")

        # RENDER block — reads scan_results from session_state. Every rerun
        # (including a button click inside this block) finds the state intact.
        scan_state = st.session_state.get("scan_results")
        if scan_state and scan_state.get("items"):
            items = scan_state["items"]
            images = scan_state.get("images", [])
            # Codex 2026-05-16: normalize `added` shape on read so a stale
            # session_state shape (e.g. after a hot reload mid-add) cannot
            # raise IndexError in the loops below.
            raw_added = scan_state.get("added", [])
            if not isinstance(raw_added, list):
                raw_added = []
            added = [bool(raw_added[i]) if i < len(raw_added) else False
                     for i in range(len(items))]
            # Persist the normalized shape back so subsequent renders use it.
            st.session_state["scan_results"]["added"] = added
            remaining = sum(1 for a in added if not a)

            # Codex 2026-05-16 HIGH: make it unmistakable that scan results are
            # NOT yet saved. Colin reported the previous green banner ("Found N
            # items!") felt like confirmation, and he closed the browser before
            # clicking Add — losing the work. The warning + explicit hint +
            # primary button anchored at the TOP of the results block close
            # that loop.
            if remaining > 0:
                st.warning(
                    f"⚠️ **Found {len(items)} item(s) across {len(images)} photo(s) "
                    f"— NOT SAVED YET.** Click the button below to keep them. "
                    f"If you close this tab now, this scan will be lost."
                )
            else:
                st.success(
                    f"✅ All {len(items)} item(s) from this scan are saved. "
                    f"Use **Clear scan / start over** when you're ready for the next batch."
                )

            btn_col1, btn_col2 = st.columns([1, 1])
            with btn_col1:
                if remaining > 0 and st.button(
                    f"\U0001f4be Save ALL {remaining} to Collection",
                    type="primary",
                    key="add_all_trains",
                ):
                    conn = get_db()
                    added_count = 0
                    for idx, d in enumerate(items):
                        if added[idx]:
                            continue
                        # Bundle 12: price-seed lookup — fills estimated_value from
                        # the seed table when vision returned no value and the
                        # (brand, catalog_number) is in the guide.
                        seeded_val, seeded_notes = _apply_price_seed(d)
                        cur = conn.execute(
                            """INSERT INTO trains (item_name,brand,scale,era,item_type,
                                catalog_number,condition,has_box,estimated_value,value_notes,
                                is_notable,last_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (d.get("item_name", ""), d.get("brand", ""), d.get("scale", ""),
                             d.get("era", ""), d.get("item_type") or d.get("type", ""), d.get("catalog_number", ""),
                             d.get("condition", ""), 1 if d.get("has_original_box") else 0,
                             seeded_val, seeded_notes,
                             1 if is_notable(d.get("item_name", ""), d.get("brand", ""), d.get("era", "")) else 0,
                             datetime.now().isoformat()))
                        # Bundle 10: persist the source photo alongside the row.
                        # Failure of the photo save must NEVER block the add.
                        pi = d.get("_photo_index")
                        if isinstance(pi, int) and 0 <= pi < len(images):
                            rel = _save_train_photo(images[pi], cur.lastrowid)
                            if rel:
                                conn.execute("UPDATE trains SET photo_path=? WHERE id=?", (rel, cur.lastrowid))
                        added[idx] = True
                        added_count += 1
                    conn.commit(); conn.close()
                    st.session_state["scan_results"]["added"] = added
                    st.success(f"Added {added_count} items to collection!")
                    st.rerun()
            with btn_col2:
                if st.button("Clear scan / start over", key="clear_scan"):
                    st.session_state.pop("scan_results", None)
                    st.rerun()

            # Display results grouped by photo
            for i, img_bytes in enumerate(images):
                photo_idxs = [idx for idx, d in enumerate(items) if d.get("_photo_index") == i]
                if not photo_idxs:
                    continue
                st.markdown(f"### Photo {i+1} — {len(photo_idxs)} item(s)")
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.image(img_bytes, width=280)
                with col2:
                    for idx in photo_idxs:
                        d = items[idx]
                        st.markdown(f"**{d.get('item_name','Unknown')}**")
                        st.markdown(f"Brand: {d.get('brand','')} | Scale: {d.get('scale','')} | Era: {d.get('era','')}")
                        st.markdown(f"Condition: {d.get('condition','')} | Box: {'Yes' if d.get('has_original_box') else 'No'}")
                        val = d.get('estimated_value')
                        if val:
                            st.markdown(f"**Est. Value: ${val}**")
                        if d.get('value_notes'):
                            st.caption(d['value_notes'])
                        if added[idx]:
                            st.markdown(":white_check_mark: **Added to collection**")
                        else:
                            if st.button("Add This One", key=f"add_item_{idx}"):
                                conn = get_db()
                                seeded_val, seeded_notes = _apply_price_seed(d)
                                cur = conn.execute(
                                    """INSERT INTO trains (item_name,brand,scale,era,item_type,
                                        catalog_number,condition,has_box,estimated_value,value_notes,
                                        is_notable,last_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (d.get("item_name", ""), d.get("brand", ""), d.get("scale", ""),
                                     d.get("era", ""), d.get("item_type") or d.get("type", ""), d.get("catalog_number", ""),
                                     d.get("condition", ""), 1 if d.get("has_original_box") else 0,
                                     seeded_val, seeded_notes,
                                     1 if is_notable(d.get("item_name", ""), d.get("brand", ""), d.get("era", "")) else 0,
                                     datetime.now().isoformat()))
                                # Bundle 10: photo save alongside row.
                                if 0 <= i < len(images):
                                    rel = _save_train_photo(images[i], cur.lastrowid)
                                    if rel:
                                        conn.execute("UPDATE trains SET photo_path=? WHERE id=?", (rel, cur.lastrowid))
                                conn.commit(); conn.close()
                                added[idx] = True
                                st.session_state["scan_results"]["added"] = added
                                st.rerun()
                        st.markdown("---")

    # ── Ask AX ──
    with tab3:
        st.subheader("Ask AX About Trains")
        st.caption("Your family AI assistant - ask about train identification, values, selling, anything.")

        if "train_chat" not in st.session_state: st.session_state.train_chat = []
        for msg in st.session_state.train_chat:
            with st.chat_message(msg["role"], avatar="\U0001f916" if msg["role"]=="assistant" else "\U0001f9d1"):
                st.markdown(msg["content"])

        if prompt := st.chat_input("Ask AX about trains..."):
            st.session_state.train_chat.append({"role":"user","content":prompt})
            with st.chat_message("user", avatar="\U0001f9d1"): st.markdown(prompt)
            with st.chat_message("assistant", avatar="\U0001f916"):
                with st.spinner("AX is thinking..."):
                    ctx = f"You're talking to {user_name}. Collection has {total} items. Brands: {', '.join(brands[:5])}." if brands else f"You're talking to {user_name}."
                    resp = chat_with_ax(prompt, ctx, st.session_state.train_chat)
                    st.markdown(resp)
                    st.session_state.train_chat.append({"role":"assistant","content":resp})

        if st.session_state.train_chat and st.button("Clear Chat"):
            st.session_state.train_chat = []; st.rerun()

    # ── Sell Items ──
    with tab4:
        st.subheader("Sell Your Trains")
        sell_col1, sell_col2 = st.columns(2)

        with sell_col1:
            st.markdown("### Create a Listing")
            sell_method = st.radio("Sell via", ["eBay","Auction House (Stout, Heritage)","Train Show/Hobby Shop","Sell Entire Collection"], horizontal=False)
            sell_name = st.text_input("Item Name", placeholder="e.g. Lionel 2056 Hudson Steam Locomotive")
            sc1,sc2 = st.columns(2)
            with sc1: sell_brand = st.text_input("Brand", placeholder="Lionel")
            with sc2: sell_condition = st.selectbox("Condition", ["Mint","Like New","Excellent","Good","Fair","Poor"])
            sell_box = st.checkbox("Has original box")
            sell_notes = st.text_input("Notes", placeholder="With tender, original box, runs well")

            if st.button("Generate Listing", type="primary"):
                if sell_name or sell_method == "Sell Entire Collection":
                    info = f"{sell_brand} {sell_name} - Condition: {sell_condition}. Box: {'Yes' if sell_box else 'No'}. {sell_notes}"
                    if sell_method == "Sell Entire Collection":
                        info = f"Complete train collection of {total} items. Brands include: {', '.join(brands[:10])}."
                        with st.spinner("AX is preparing your collection sale plan..."):
                            listing = chat_with_ax(
                                f"Help me sell my entire model train collection: {info}. "
                                f"Give me a step-by-step plan: 1) How to inventory for sale, "
                                f"2) Best auction houses for train collections (Stout Auctions, etc), "
                                f"3) How to get an appraisal, 4) Whether to sell as lot or individually, "
                                f"5) Expected timeline and costs. Be practical and specific.")
                    else:
                        with st.spinner("AX is writing your listing..."):
                            listing = generate_sell_listing(info, sell_method.split(" ")[0])
                    st.session_state["train_listing"] = listing

        with sell_col2:
            st.markdown("### Your Listing")
            if "train_listing" in st.session_state:
                st.markdown(st.session_state["train_listing"])
                st.download_button("Download as Text", st.session_state["train_listing"],
                    "train_listing.txt", "text/plain")
            else:
                st.info("Fill in details and click Generate Listing.")

        # Auction house contacts
        st.markdown("---")
        st.markdown("### Auction House Contacts")
        st.markdown("""
        | House | Specialty | Contact |
        |-------|-----------|---------|
        | **Stout Auctions** | Premier train auctioneer | stoutauctions.com / (765) 764-6901 |
        | **Trainz** | Ship collection, they sell it (15% commission) | sellmytrains.com |
        | **Heritage Auctions** | High-value individual pieces | ha.com |
        | **LiveAuctioneers** | Find specialist auctioneers | liveauctioneers.com |
        """)

    # ── Stats ──
    with tab5:
        st.subheader("Collection Statistics")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Items", f"{total:,}")
        c2.metric("Pieces", f"{total_qty:,}")
        c3.metric("Notable", f"{notable:,}")
        c4.metric("Priced", f"{with_values:,}")
        if total > 0:
            conn = get_db()
            bd = pd.read_sql_query("SELECT brand, COUNT(*) as count FROM trains WHERE brand!='' GROUP BY brand ORDER BY count DESC LIMIT 10", conn)
            sd = pd.read_sql_query("SELECT scale, COUNT(*) as count FROM trains WHERE scale!='' GROUP BY scale ORDER BY count DESC", conn)
            td = pd.read_sql_query("SELECT item_type, COUNT(*) as count FROM trains WHERE item_type!='' GROUP BY item_type ORDER BY count DESC", conn)
            conn.close()
            if not bd.empty: st.subheader("By Brand"); st.bar_chart(bd.set_index("brand")["count"])
            if not sd.empty: st.subheader("By Scale"); st.bar_chart(sd.set_index("scale")["count"])
            if not td.empty: st.subheader("By Type"); st.bar_chart(td.set_index("item_type")["count"])

    # ── Import/Export ──
    with tab6:
        st.subheader("Import / Export")
        imp_col, exp_col = st.columns(2)

        with imp_col:
            st.markdown("### Import Items")
            st.markdown("Paste tab-separated: `Name | Brand | Scale | Era | Type | Catalog# | Qty | Condition | Box | Value | Location | Notes`")
            paste = st.text_area("Paste data", height=200)
            if st.button("Import", type="primary"):
                if paste.strip():
                    conn = get_db(); count = 0; seeded = 0
                    for line in paste.strip().split('\n'):
                        parts = line.split('\t')
                        while len(parts) < 12: parts.append("")
                        try: qty = int(parts[6].strip() or "1")
                        except: qty = 1
                        # Bundle 12: price-seed lookup on import — fills empty
                        # estimated_value from the seed when (brand, catalog#)
                        # matches.
                        row_val = parts[9].strip()
                        row_notes = parts[11].strip()
                        if not row_val:
                            sv, sn = _apply_price_seed({
                                "brand": parts[1].strip(),
                                "catalog_number": parts[5].strip(),
                                "value_notes": row_notes,
                            })
                            if sv:
                                row_val = sv; row_notes = sn; seeded += 1
                        conn.execute("""INSERT INTO trains (item_name,brand,scale,era,item_type,
                            catalog_number,quantity,condition,has_box,estimated_value,location,notes,is_notable)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (parts[0].strip(),parts[1].strip(),parts[2].strip(),parts[3].strip(),
                             parts[4].strip(),parts[5].strip(),qty,parts[7].strip(),
                             1 if parts[8].strip().lower() in ("yes","1","true","y") else 0,
                             row_val,parts[10].strip(),row_notes,
                             1 if is_notable(parts[0].strip(),parts[1].strip(),parts[3].strip()) else 0))
                        count += 1
                    conn.commit(); conn.close()
                    msg = f"Imported {count} items"
                    if seeded:
                        msg += f" — {seeded} auto-priced from the price seed"
                    st.success(msg); st.rerun()

            # Codex 2026-05-16 LOW: wire the upload — previously the uploader
            # read the file but only said "use CSV import in future update,"
            # which Colin would reasonably read as "import is broken." Now we
            # detect tab vs comma delimiter from the file content and run the
            # same row-loop as paste-import, including the price-seed lookup.
            uploaded = st.file_uploader("Or upload CSV/TSV", type=["csv","tsv","txt"])
            if uploaded:
                content = uploaded.read().decode('utf-8', errors='replace')
                # Pick the delimiter that yields the most consistent column
                # count. CSV detect heuristic: tab if any line has a tab.
                sep = "\t" if any("\t" in ln for ln in content.splitlines()[:5]) else ","
                if st.button("Import uploaded file", type="primary", key="import_uploaded"):
                    conn = get_db(); count = 0; seeded = 0
                    for line in content.strip().split('\n'):
                        if not line.strip():
                            continue
                        parts = line.split(sep)
                        while len(parts) < 12:
                            parts.append("")
                        try:
                            qty = int(parts[6].strip() or "1")
                        except ValueError:
                            qty = 1
                        row_val = parts[9].strip()
                        row_notes = parts[11].strip()
                        if not row_val:
                            sv, sn = _apply_price_seed({
                                "brand": parts[1].strip(),
                                "catalog_number": parts[5].strip(),
                                "value_notes": row_notes,
                            })
                            if sv:
                                row_val = sv; row_notes = sn; seeded += 1
                        conn.execute("""INSERT INTO trains (item_name,brand,scale,era,item_type,
                            catalog_number,quantity,condition,has_box,estimated_value,location,notes,is_notable)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (parts[0].strip(),parts[1].strip(),parts[2].strip(),parts[3].strip(),
                             parts[4].strip(),parts[5].strip(),qty,parts[7].strip(),
                             1 if parts[8].strip().lower() in ("yes","1","true","y") else 0,
                             row_val,parts[10].strip(),row_notes,
                             1 if is_notable(parts[0].strip(),parts[1].strip(),parts[3].strip()) else 0))
                        count += 1
                    conn.commit(); conn.close()
                    msg = f"Imported {count} items from {uploaded.name}"
                    if seeded:
                        msg += f" — {seeded} auto-priced from the price seed"
                    st.success(msg); st.rerun()
                else:
                    st.caption(
                        f"Detected {'tab' if sep == chr(9) else 'comma'}-separated. "
                        f"Click **Import uploaded file** to load."
                    )

        with exp_col:
            st.markdown("### Export")
            if total > 0:
                conn = get_db()
                all_df = pd.read_sql_query("SELECT * FROM trains ORDER BY brand, item_name", conn); conn.close()
                csv_buf = io.StringIO(); all_df.to_csv(csv_buf, index=False)
                st.download_button("Download CSV", csv_buf.getvalue(), "train_collection.csv", "text/csv")
                json_str = all_df.to_json(orient="records", indent=2)
                st.download_button("Download JSON", json_str, "train_collection.json", "application/json")

                # Insurance/Estate report
                st.markdown("---")
                st.markdown("### Insurance / Estate Report")
                st.caption("Generate a report for insurance or family estate planning.")
                if st.button("Generate Report"):
                    report = f"# Train Collection Inventory Report\n"
                    report += f"**Generated:** {datetime.now().strftime('%B %d, %Y')}\n\n"
                    report += f"**Total Items:** {total}\n"
                    report += f"**Total Pieces:** {total_qty}\n"
                    report += f"**Items with Values:** {with_values}\n"
                    report += f"**Brands:** {', '.join(brands)}\n\n"
                    report += "---\n\n"

                    # Notable items first
                    conn = get_db()
                    notable_df = pd.read_sql_query("SELECT * FROM trains WHERE is_notable=1 ORDER BY brand", conn)
                    common_df = pd.read_sql_query("SELECT * FROM trains WHERE is_notable=0 ORDER BY brand", conn)
                    conn.close()

                    if not notable_df.empty:
                        report += "## High-Value Items\n\n"
                        report += "| Item | Brand | Scale | Condition | Box | Est. Value |\n"
                        report += "|------|-------|-------|-----------|-----|------------|\n"
                        for _, r in notable_df.iterrows():
                            report += f"| {_md_cell(r.get('item_name'))} | {_md_cell(r.get('brand'))} | {_md_cell(r.get('scale'))} | {_md_cell(r.get('condition'))} | {'Yes' if r.get('has_box') else 'No'} | {_md_cell(r.get('estimated_value'))} |\n"
                        report += "\n"

                    if not common_df.empty:
                        report += "## All Other Items\n\n"
                        report += "| Item | Brand | Scale | Qty | Condition | Value |\n"
                        report += "|------|-------|-------|-----|-----------|-------|\n"
                        for _, r in common_df.iterrows():
                            report += f"| {_md_cell(r.get('item_name'))} | {_md_cell(r.get('brand'))} | {_md_cell(r.get('scale'))} | {_md_cell(r.get('quantity',1))} | {_md_cell(r.get('condition'))} | {_md_cell(r.get('estimated_value'))} |\n"

                    report += "\n---\n"
                    report += "\n## Selling Resources\n"
                    report += "- **Stout Auctions** (stoutauctions.com) — Premier train auction house\n"
                    report += "- **Trainz** (sellmytrains.com) — Ship collection, 15% commission\n"
                    report += "- **Heritage Auctions** (ha.com) — For high-value individual pieces\n"
                    report += "- **Greenberg's Price Guide** — Standard reference book\n"

                    st.download_button("Download Report", report, "train_collection_report.md", "text/markdown")
                    st.markdown(report)

                # Bundle 11: printable label sheet — physical-to-digital bridge.
                # Avery 5160 layout (1" × 2-5/8", 3 cols × 10 rows = 30 per page).
                # Self-contained HTML with embedded SVG QR codes — opens in any
                # browser, hit Print, no PDF library required.
                st.markdown("---")
                st.markdown("### Label Sheet (print on Avery 5160 — 30 per page)")
                st.caption(
                    "Generates a printable sheet of physical labels. Each label has the item ID "
                    "(`T-NNNN`), brand + name, and a QR code that opens the app focused on that item. "
                    "Stick the labels on your shelves so you can scan any train back into the app."
                )
                if st.button("Generate Label Sheet"):
                    conn = get_db()
                    label_df = pd.read_sql_query(
                        "SELECT id, item_name, brand, catalog_number FROM trains "
                        "ORDER BY brand, item_name", conn)
                    conn.close()
                    base_url = DEFAULT_PUBLIC_BASE_URL
                    # Avery 5160 stylesheet — 1" tall, 2.625" wide, 30 per page.
                    css = """
                    <style>
                      @page { size: letter; margin: 0.5in 0.1875in 0.5in 0.1875in; }
                      body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 0; }
                      .sheet { display: grid; grid-template-columns: repeat(3, 2.625in);
                               grid-auto-rows: 1in; gap: 0 0.125in; }
                      .label { display: flex; align-items: center; padding: 0.05in 0.1in;
                               box-sizing: border-box; overflow: hidden; }
                      .qr { width: 0.85in; height: 0.85in; flex: 0 0 auto; margin-right: 0.1in; }
                      .qr svg { width: 100%; height: 100%; display: block; }
                      .meta { display: flex; flex-direction: column; justify-content: center;
                              line-height: 1.2; overflow: hidden; }
                      .code { font-family: monospace; font-size: 11pt; font-weight: 700; }
                      .name { font-size: 9pt; font-weight: 600;
                              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
                      .sub  { font-size: 8pt; color: #555;
                              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
                      .toolbar { padding: 12px; background: #f4f4f4; text-align: center;
                                 font-family: sans-serif; }
                      @media print { .toolbar { display: none; } }
                    </style>
                    """
                    labels_html = []
                    for _, r in label_df.iterrows():
                        tid = int(r["id"])
                        code = _label_code(tid)
                        try:
                            qr_svg = _qr_svg_for(tid, base_url, user_name)
                        except Exception as e:
                            print(f"[label qr] failed for {tid}: {e}")
                            qr_svg = ""
                        # Codex 2026-05-16: HTML-escape every interpolated DB
                        # field. Without this, a train named `<script>alert(1)
                        # </script>` would execute when the label sheet opens.
                        name = (r.get("item_name") or "").strip() or "Unknown"
                        brand = (r.get("brand") or "").strip()
                        catn = (r.get("catalog_number") or "").strip()
                        sub_bits = [b for b in [brand, catn] if b]
                        sub = " · ".join(sub_bits)
                        labels_html.append(
                            '<div class="label">'
                            f'<div class="qr">{qr_svg}</div>'
                            '<div class="meta">'
                            f'<div class="code">{_html.escape(code)}</div>'
                            f'<div class="name">{_html.escape(name)}</div>'
                            f'<div class="sub">{_html.escape(sub)}</div>'
                            '</div></div>'
                        )
                    html = (
                        "<!doctype html><html><head><meta charset=\"utf-8\">"
                        f"<title>Train Collection Labels ({len(label_df)})</title>"
                        f"{css}</head><body>"
                        '<div class="toolbar">'
                        f"{len(label_df)} labels — click Print or press Ctrl/Cmd-P. "
                        "Load Avery 5160 sheets into your printer first."
                        ' <button onclick="window.print()">Print</button>'
                        "</div>"
                        f'<div class="sheet">{"".join(labels_html)}</div>'
                        "</body></html>"
                    )
                    st.download_button(
                        "Download Label Sheet (HTML)",
                        html,
                        "train_labels.html",
                        "text/html",
                    )
                    st.success(
                        f"Generated {len(label_df)} labels. Download → open in any browser → Print."
                    )

                # Auction house packet
                st.markdown("---")
                st.markdown("### Auction House Packet")
                if st.button("Generate Auction Submission"):
                    conn = get_db()
                    items_df = pd.read_sql_query("SELECT item_name,brand,scale,era,catalog_number,condition,has_box,estimated_value FROM trains WHERE is_notable=1 ORDER BY brand", conn)
                    conn.close()
                    packet = f"Dear Auction Team,\n\n"
                    packet += f"I would like to consign my model train collection for auction.\n\n"
                    packet += f"Collection Overview:\n"
                    packet += f"- Total items: {total}\n"
                    packet += f"- Brands: {', '.join(brands[:10])}\n"
                    packet += f"- Scales: {', '.join(scales)}\n\n"
                    if not items_df.empty:
                        packet += f"Notable Items ({len(items_df)}):\n\n"
                        for _, r in items_df.iterrows():
                            packet += f"  - {_flat(r.get('brand'))} {_flat(r.get('item_name'))} (#{_flat(r.get('catalog_number'))}) - {_flat(r.get('condition'))} - Box: {'Yes' if r.get('has_box') else 'No'}\n"
                    packet += f"\nPlease advise on consignment terms, pickup/shipping options, and estimated timeline.\n"
                    packet += f"\nThank you."
                    st.download_button("Download Packet", packet, "auction_submission.txt", "text/plain")
                    st.text_area("Preview", packet, height=300)

            st.markdown("---")
            if total > 0:
                if st.session_state.get("confirm_clear_all"):
                    st.warning(f"⚠ This will permanently delete all **{total} items**. A backup CSV will be saved to `db/` first.")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("Yes, delete everything", type="primary"):
                            try:
                                conn = get_db()
                                backup_df = pd.read_sql_query("SELECT * FROM trains", conn)
                                # If a concurrent clear already emptied the table, do not
                                # write an empty backup that would overwrite the prior one.
                                if backup_df.empty:
                                    conn.close()
                                    st.session_state.pop("confirm_clear_all", None)
                                    st.info("Already empty — nothing to clear.")
                                    st.rerun()
                                # Microsecond timestamp + short uuid + exclusive create =
                                # filename collision is astronomically improbable AND
                                # refused at the OS layer if it ever occurs.
                                ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                                backup_name = f"backup-{ts}-{uuid.uuid4().hex[:8]}.csv"
                                backup_path = os.path.join(os.path.dirname(DB_PATH), backup_name)
                                with open(backup_path, "x", newline="") as f:
                                    backup_df.to_csv(f, index=False)
                                conn.execute("DELETE FROM trains"); conn.commit(); conn.close()
                                st.session_state.pop("confirm_clear_all", None)
                                st.success(f"Cleared. Backup saved to db/{backup_name}")
                                st.rerun()
                            except FileExistsError as fee:
                                print(f"[clear_all] backup filename collision (extremely rare): {fee}")
                                st.error("Backup filename collision — please retry.")
                            except Exception as e:
                                print(f"[clear_all] failed: {e}")
                                st.error("Clear failed. The collection is unchanged.")
                    with cc2:
                        if st.button("Cancel"):
                            st.session_state.pop("confirm_clear_all", None)
                            st.rerun()
                else:
                    if st.button("Clear All"):
                        st.session_state["confirm_clear_all"] = True
                        st.rerun()

if __name__ == "__main__":
    main()
