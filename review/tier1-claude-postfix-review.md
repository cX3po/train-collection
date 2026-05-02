# Tier 1 Claude Post-Fix Review — Train Collection (commit edfdbd4)

**Date:** 2026-05-02
**Reviewer:** claude (claude-sonnet-4-6)
**Elapsed:** 166.7s
**Status:** OK

---

The provided source is sufficient for analysis — no need to read from disk for this review. I have the full content of all three files inline. Let me deliver the verdict now.

---

## VERDICT: APPROVED WITH NOTES

---

## Per-fix verification

**Fix 1 (R10 exception leak): PASS**
`_chat_via_api` logs `print(f"[chat_with_ax] API HTTP {resp.status_code}: {resp.text[:300]}")` and returns `GENERIC_AX_FAILURE` constant (`app.py:78,82`). All exception paths in `_chat_via_cli` log to `print` and return `None` (`app.py:65,68`). Scan tab catches broadly at `app.py:213–215` and emits generic `st.error("Photo scan failed...")`. `engine.py` `_call_haiku` / `_call_gemini` raise `RuntimeError("Vision service unavailable. Try again in a moment.")` after logging the HTTP details (`engine.py:98–99, 120–121`). All three original leak sites are closed.

**Fix 2 (upload caps): PASS**
`MAX_FILE_BYTES = 10 MB`, `MAX_PHOTOS_PER_SCAN = 10` defined at module top (`app.py:8–9`). Count check fires before button render (`app.py:157–160`). Per-file size check uses `p.size` (no read) and filters the list before the scan loop (`app.py:161–167`). Engine-layer hard cap at 15 MB + magic-byte gate both in `validate_image` (`engine.py:63–74`), called before `base64` encoding in `analyze()` (`engine.py:175`). Limits fire in the correct order: app-layer count/size → button → `validate_image` → API call.

**Fix 3 (Clear All confirm): PASS**
Two-step flow confirmed: first click sets `confirm_clear_all = True` and reruns (`app.py:343`); second click runs backup-then-delete under `try/except` (`app.py:327–340`). CSV backup written to `db/backup-{ts}.csv` before `DELETE FROM trains` (`app.py:331–336`). Exception path logs and shows generic `st.error("Clear failed. The collection is unchanged.")` (`app.py:337–340`). Cancel path clears state cleanly. Race condition is benign: each Streamlit session has independent `session_state`, and in a concurrent double-confirm the second deleter sees an already-empty table but still gets a (possibly empty) backup file and a success message — no data is lost. Acceptable for a 5-user family app.

**Fix 4 (SQLite WAL): PASS**
`init_db()` issues `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` on every call (`app.py:40–42`). `get_db()` calls `init_db()` to ensure WAL is set, then opens its own connection and re-issues `busy_timeout` (`app.py:47–50`). WAL mode persists at the file level so the double-connect is harmless; the redundant `init_db()` call is slightly wasteful (two connections opened/closed per `get_db()`) but correct. No naked `sqlite3.connect` calls remain outside these two functions.

**Fix 5 (markdown escape): PASS**
`_md_cell()` escapes `\`, `|`, `\n`, and `\r` in correct order (backslash first) (`app.py:55–59`). Applied to every cell in the notable-items table (`app.py:282–285`) and all-items table (`app.py:290–294`). Auction packet uses `_flat()` (newline-strip only, no `|` escaping) because the output is plain text — correct for that format (`app.py:302–304`). Escape sequence order is correct: `\\` is replaced before `\|`, preventing double-escaping.

**Fix 6 (CLI swap — new): PASS**
`ANTHROPIC_API_KEY` is explicitly excluded from `child_env` (`app.py:71`). System prompt goes via `--append-system-prompt` as a positional argument value, not embedded in the user message (`app.py:73–74`). `subprocess.run` uses a list (not `shell=True` — `shell` defaults to `False`) so no shell injection regardless of message content (`app.py:75–77`). `TimeoutExpired` → `None` → API fallback (`app.py:78–80`). Non-zero rc or empty stdout → `None` → API fallback (`app.py:82–84`). `engine.py:_call_haiku` is untouched; vision stays on the API path. No file-descriptor leaks: `capture_output=True` handles pipes internally. User message goes through stdin only, not injected into argv.

---

## New defects introduced by the fix bundle

None.

---

## Critical findings

None.

---

## High-priority findings

None.

---

## Notes / nice-to-have

1. **`--append-system-prompt` flag name is unverified at review time** (`app.py:74`). If the installed `claude` CLI version doesn't support this exact flag, every CLI call will return non-zero rc and silently fall through to the API, billing per-token. The fallback is safe, but the billing cost scar that motivated the CLI path would re-manifest silently. Recommend a startup check: `subprocess.run([CLAUDE_BIN, "--help"], ...)` on first launch, grep for `append-system-prompt`, and log a warning if absent.

2. **`_flat()` defined inside the auction-packet loop** (`app.py:302–304`). Python re-creates the function object on every iteration. Move it to module scope alongside `_md_cell` to be consistent — it's a sibling helper.

3. **`init_db()` called inside `get_db()`** (`app.py:48`) — opens and closes a connection on every database call to ensure WAL is set. Since WAL is a persistent file-level setting, checking once at startup (or using a module-level flag) would halve the connection churn. Minor performance note, not a correctness issue.

4. **Broad `except Exception` in the scan handler** (`app.py:213`) swallows `ValueError` from `validate_image` (wrong image format, corrupt file). The user sees "Try clearer photos" when the real cause is an unrecognized file format. Not a security issue; slightly confusing UX. Consider catching `ValueError` separately to show the specific message before the generic fallback.
