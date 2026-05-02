# Tier 1 Codex Review — Train Collection

**Date:** 2026-05-02
**Reviewer:** codex (gpt-5.5)
**Elapsed:** 42.2s
**Status:** OK

---

## VERDICT: NEEDS REVISION

## Critical findings
(empty)

## High-priority findings

1. Raw exception messages are shown to users.
   - [app.py](/home/eric/train-collection/app.py:82) returns `Error: {e}` from AX chat.
   - [app.py](/home/eric/train-collection/app.py:310) shows `st.error(f"Error: {e}")` during photo scanning.
   - [engine.py](/home/eric/train-collection/engine.py:113) and [engine.py](/home/eric/train-collection/engine.py:138) include upstream response bodies in raised errors.
   - Fix: log details server-side; show generic messages like “AX is unavailable right now” or “Photo scan failed.”

2. File upload validation is too weak for the photo scanning path.
   - [app.py](/home/eric/train-collection/app.py:222) only checks extensions.
   - [app.py](/home/eric/train-collection/app.py:241) reads every uploaded file fully into memory.
   - [engine.py](/home/eric/train-collection/engine.py:58) treats unknown bytes as JPEG.
   - [engine.py](/home/eric/train-collection/engine.py:69) only checks non-empty and `>=100` bytes.
   - Fix: enforce per-file and total size limits, verify image format with a real parser, reject unknown MIME types, and cap number of uploaded photos.

3. User-controlled fields can break generated reports and auction packets.
   - Insurance report Markdown table rows interpolate raw DB fields at [app.py](/home/eric/train-collection/app.py:474) and [app.py](/home/eric/train-collection/app.py:482).
   - Auction packet interpolates raw item fields at [app.py](/home/eric/train-collection/app.py:510).
   - A malicious item name containing `|`, newlines, Markdown links, or fake sections can corrupt the deliverable.
   - Fix: escape Markdown table cells, normalize newlines, and use a structured renderer/template for deliverables.

4. SQLite concurrency is not release-ready for “family sign-in.”
   - Every user writes the same local DB through short-lived default SQLite connections: [app.py](/home/eric/train-collection/app.py:57).
   - Bulk edit save rewrites rows from the visible editor snapshot: [app.py](/home/eric/train-collection/app.py:189).
   - “Clear All” deletes the full collection without confirmation: [app.py](/home/eric/train-collection/app.py:517).
   - Fix: use WAL/busy timeout, explicit transactions, optimistic concurrency via `last_updated`, and a confirmation step for destructive actions.

## Notes / nice-to-have

- Hardcoded family-specific text remains in user-facing strings: `Dad's Train Collection` at [app.py](/home/eric/train-collection/app.py:2) and [app.py](/home/eric/train-collection/app.py:127), plus `Phil, Colin` in the login placeholder at [app.py](/home/eric/train-collection/app.py:120). Move these into runtime config.
- API key loading falls back to `~/axiom/.env` before the process environment: [app.py](/home/eric/train-collection/app.py:17). That is surprising cross-project coupling and could use the wrong key. Prefer `st.secrets`, then app-local env/config, then `os.getenv`.
- `except: pass` in secret loading hides configuration errors: [app.py](/home/eric/train-collection/app.py:13). Catch a specific exception.
- `re` and `Path` imports appear unused.

## Pattern-by-pattern audit

1. HTML escaping — PASS: only `unsafe_allow_html=True` is static SVG at [app.py](/home/eric/train-collection/app.py:208). User content uses default Streamlit escaping, but Markdown artifact injection still needs work.
2. URL scheme guard — N/A: no user-provided URL is rendered as `href`/`src`.
3. Owner-name interpolation — NEEDS WORK: hardcoded `Dad`, `Phil`, `Colin` in user-facing strings.
4. Atomic file writes — NEEDS WORK: no JSON/CSV writes, but SQLite concurrency/destructive write handling is weak.
5. SQL injection — PASS: user values are parameterized; dynamic SQL uses fixed fragments only.
6. Exception leak — NEEDS WORK: raw exceptions and upstream response text can reach users.
7. API key handling — NEEDS WORK: cross-project `.env` fallback and raw error surfaces should be tightened.
8. File upload safety — NEEDS WORK: extension-only filtering, no size caps, incomplete MIME validation.
9. Streamlit-specific — NEEDS WORK: `session_state` is per browser session, but the shared DB has no multi-user conflict handling.
10. Auction/report generator — NEEDS WORK: raw fields can inject/break Markdown and plain-text deliverables.