# Tier 1 Codex Post-Fix Review — Train Collection (commit edfdbd4)

**Date:** 2026-05-02
**Reviewer:** codex (gpt-5.5)
**Elapsed:** 35.1s
**Status:** OK

---

## VERDICT: NEEDS REVISION

## Per-fix verification (one line each):
  Fix 1 (R10 exception leak):       PASS — chat/CLI/API and scan now return generic messages and print details: `app.py:115-149`, `app.py:397-399`; engine HTTP failures log response text but raise generic: `engine.py:122-124`, `engine.py:148-150`.
  Fix 2 (upload caps):              PASS — app enforces count/size before `photo.read()`: `app.py:302-315`, engine validates size/magic before base64/API: `engine.py:72-84`, `engine.py:89-94`, `engine.py:228-237`.
  Fix 3 (Clear All confirm):        NEEDS WORK — two-step confirmation and backup exist, but backup filename is second-resolution and can be overwritten by concurrent clears: `app.py:610-621`.
  Fix 4 (SQLite WAL):               PASS — WAL in `init_db`, busy timeout in `init_db` and `get_db`, and only those two `sqlite3.connect` calls remain: `app.py:59-79`.
  Fix 5 (markdown escape):          PASS — `_md_cell` escapes `\`, `|`, and newlines, and is applied to report table cells; auction notable-item fields are newline-flattened as plain text: `app.py:39-44`, `app.py:558-571`, `app.py:598-602`.
  Fix 6 (CLI swap — new):           PASS — env scrubs `ANTHROPIC_API_KEY`, uses argv list with no `shell=True`, passes system prompt via `--append-system-prompt`, handles timeout/error/nonzero/empty fallback, and vision remains API-based: `app.py:91-125`, `app.py:128-158`, `engine.py:89-127`.

## New defects introduced by the fix bundle

None beyond the Clear All backup race noted below.

## Critical findings

(empty)

## High-priority findings

`app.py:618-621` in Clear All confirmation: concurrent confirmations can overwrite the only useful backup. The backup path uses `datetime.now().strftime("%Y%m%d-%H%M%S")`, so two users clicking within the same second target the same `backup-*.csv`. One request can write a full backup and delete rows; the second can then read an empty table and overwrite that same backup file with an empty CSV. Use a collision-resistant filename, for example include microseconds plus a UUID, and ideally write with exclusive create semantics.

## Notes / nice-to-have

`app.py:109-114` correctly scrubs `ANTHROPIC_API_KEY` from the CLI child env and avoids command injection by passing a list to `subprocess.run`. The system prompt includes app-generated context only; user chat input stays in stdin transcript, not argv or shell.

`engine.py:31` imports `Path` but does not use it. Low priority cleanup only.