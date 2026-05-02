# Tier 1 Codex Post-Bundle-7+8 Review — Train Collection

**Date:** 2026-05-02
**Reviewer:** codex (gpt-5.5)
**Elapsed:** 41.5s
**Status:** OK

---

## VERDICT: APPROVED

## Bundle 7+8 verification:
  7  (Clear All race fix):           PASS — empty-table guard at `app.py:655-662`, microsecond+uuid backup name at `app.py:666-668`, exclusive create at `app.py:669`, delete only after backup write at `app.py:671`.
  8a (CLI flag check):               PASS — `_claude_cli_supports()` checks `claude --help` at `app.py:19-33`; module-load gate/warning at `app.py:122-125`; CLI path refuses use if false at `app.py:135-136`.
  8b (_flat to module scope):        PASS — module-scope `_flat()` at `app.py:65-69`; auction packet uses it at `app.py:640`.
  8c (init_db one-shot):             PASS — `_DB_INITIALIZED` flag at `app.py:72`; early return/set in `init_db()` at `app.py:87-105`; `get_db()` still applies per-connection busy timeout at `app.py:107-110`.
  8d (ValueError separate catch):    PASS — scan handler catches `ValueError` before generic exception at `app.py:434-437`.
  8e (Path import removal):          PASS — `engine.py:27-32` imports `base64`, `json`, `os`, dataclasses, and `requests`; no unused `Path`.

## Regression check on prior six fixes:
  R10 leak / upload caps / Clear All two-step / SQLite WAL /
  markdown escape / CLI swap — REGRESSED? no.

## New defects introduced by Bundle 7+8
(empty)

## Critical findings
(empty)

## High-priority findings
(empty)

## Notes / nice-to-have
CLI flag detection is intentionally conservative: if `claude --help` omits `--append-system-prompt` despite the flag being accepted, chat will fall back to API and print the billing warning. That is not a blocker for this bundle, but worth knowing operationally.