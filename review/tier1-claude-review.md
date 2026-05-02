# Tier 1 Claude Review — Train Collection

**Date:** 2026-05-02
**Reviewer:** claude (claude-sonnet-4-6)
**Elapsed:** 118.9s
**Status:** OK

---

## VERDICT: APPROVED WITH NOTES

The app has no critical exploitability or data-loss vulnerabilities. Three issues warrant fixing before a multi-user family deployment; the rest are low-priority hardening.

---

## Critical findings

None.

---

## High-priority findings

**H1 — "Clear All" has no confirmation guard** (`app.py`, tab6, ~line 490)
```python
if total > 0 and st.button("Clear All"):
    conn.execute("DELETE FROM trains"); conn.commit()
```
One mis-click by any signed-in family member wipes the entire database with no undo. SQLite has no recycle bin. Add `st.warning` + a second confirmation button, or at minimum write a backup CSV before the delete. Data-loss risk is real.

**H2 — Raw exception forwarded to user (R10 pattern)**
- `chat_with_ax` (`app.py` ~line 76): `except Exception as e: return f"Error: {e}"`
- Scan section (`app.py` ~line 178): `except Exception as e: st.error(f"Error: {e}")`
- `_call_haiku` / `_call_gemini` (`engine.py` lines 111, 140): `raise RuntimeError(f"... {resp.text[:200]}")`

The last two surface up to 200 bytes of raw API response body (rate-limit payloads, auth rejection messages) to the browser. The chat path returns the full Python exception string. Use generic user messages and `print`/`logging` for detail server-side.

**H3 — No file-upload size limit** (`app.py` tab2)
```python
photos = st.file_uploader(..., accept_multiple_files=True)
img_bytes = photo.read()  # no bound
```
`validate_image()` in `engine.py` only checks `len < 100`. An oversized upload is read entirely into memory, base64-encoded (33% expansion), and posted to the API. Add a practical cap (e.g., 10 MB per file) and reject before the API call.

---

## Notes / nice-to-have

**N1 — SQLite concurrency** — `get_db()` opens connections without WAL mode. Under the multi-user family sign-in model, concurrent writes will occasionally raise `OperationalError: database is locked`. Add `conn.execute("PRAGMA journal_mode=WAL")` in `init_db()` and catch the lock error with a user-friendly retry message.

**N2 — Hardcoded fallback path** (`app.py` `load_api_key()`, line 15)
```python
env_path = os.path.expanduser("~/axiom/.env")
```
This path is machine-specific. Works on Phil's box; silent no-op everywhere else. Document it or replace with a plain `os.getenv` fallback only.

**N3 — Gemini API key in URL** (`engine.py` line 128)
```python
f"...models/gemini-2.0-flash:generateContent?key={api_key}"
```
The key appears in request logs, proxy logs, and any exception that includes the URL. Standard for the Gemini REST API but worth noting — prefer the `Authorization: Bearer` header if the API supports it, or ensure access logs are not exposed.

**N4 — Markdown pipe injection in reports** (`app.py` ~lines 437, 459)
```python
report += f"| {r.get('item_name','')} | ... |\n"
```
An item name containing `|` breaks the markdown table layout. Not a security issue (no `unsafe_allow_html=True` in sight), but it produces a malformed report. Strip or escape `|` chars before inserting into the table.

**N5 — "Add ALL" button inside conditional render block** (`app.py` ~line 145)
The "Add ALL to Collection" button is rendered inside `if photos and st.button("Scan All Photos"):`. Streamlit rerenders from top on every interaction; the outer condition won't hold across the rerender triggered by "Add ALL". Storing results in `session_state` (already done) is correct, but the button should be rendered outside that conditional branch so it survives the rerender cycle.

**N6 — `_call_gemini` (engine.py)** is currently unreachable from `app.py` (which only ever calls `VisionEngine(provider="haiku")`). If Gemini support is planned, the URL key-embedding concern (N3) applies.

---

## Pattern-by-pattern audit

| # | Check | Result |
|---|-------|--------|
| 1 | HTML escaping / `unsafe_allow_html` | **PASS** — only `unsafe_allow_html=True` use is a hardcoded SVG; all user/AI content rendered via plain `st.markdown()` |
| 2 | URL scheme guard | **N/A** — no user-supplied URLs rendered as links or `src` attributes |
| 3 | Owner-name interpolation | **PASS** — `user_name` is runtime input; no hardcoded family names in user-facing strings (placeholder text only) |
| 4 | Atomic file writes / concurrency | **NEEDS WORK** — no WAL mode, no lock-retry; multi-user writes will occasionally fail silently (N1) |
| 5 | SQL injection | **PASS** — all `cursor.execute` calls use `?` parameterized queries |
| 6 | Exception leak (R10) | **NEEDS WORK** — raw `Exception` and raw API response body exposed to browser (H2) |
| 7 | API key handling | **PASS** (minor notes) — key not logged or returned in responses; Gemini URL embedding noted (N3) |
| 8 | File upload safety | **NEEDS WORK** — MIME sniffing via magic bytes is correct; no per-file size cap (H3) |
| 9 | Streamlit-specific / session_state | **NEEDS WORK** — session isolation is correct; "Clear All" unguarded is a data-loss hole (H1); concurrent SQLite needs WAL (N1) |
| 10 | Report / packet injection | **PASS** (minor notes) — artifacts are plain text/markdown downloads, no `unsafe_allow_html`; pipe chars could corrupt table layout (N4) |
