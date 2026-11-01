# Proposal — fold spike batch B findings (S-3, S-5)

| Field | Value |
|---|---|
| Date | 2026-10-31 |
| Author | Ernesto Crespo |
| Status | APPROVED 2026-10-31 (owner pre-authorised auto-approval for this session) · FOLDED 2026-10-31 |
| Sources | `specs/plans/spikes/S-3.md`, `specs/plans/spikes/S-5.md` |
| Affects | PRD 1.3 → 1.4, API 1.3 → 1.4, TD 1.2 → 1.3, Plan 1.1 → 1.2, Tasks 1.2 → 1.3 |

## Why

- **S-3** confirmed DD-012 (origin pair `127.0.0.1` UI / `localhost` proxy) in Chromium,
  Firefox and WebKit, and DD-017 is required (all engines send UI cookies, incl. `HttpOnly`,
  to `localhost`). It surfaced messaging and CSP details the specs do not pin, one residual
  exfiltration path (View self-navigation) and an IPv6 resolution pitfall.
- **S-5** confirmed DD-011/016/017 in substance with corrections: Reflex prod mode serves UI
  and backend on **one port**; `--env dev` binds all interfaces; the per-tab watcher must be
  started from `on_load` (an `on_mount` chain is not cancelled on disconnect); the guard
  refuses the UI page itself, so the "open 127.0.0.1" notice must be the 403 body.

No requirement is weakened. One CLI option (`--backend-port`) is removed because the
runtime cannot honour it in prod mode.

## Decisions taken (auto-approval)

1. Single `--port` (default 3000) for UI + backend; `--backend-port` dropped (API §6).
2. View self-navigation is recorded as a **residual risk** (TD §6.1) — no new mitigation in
   v0.1 (adding "tear down on second inner `load`" would be new behaviour needing a PRD REQ;
   candidate for v0.2).
3. Workspace key = version + content hash of packaged app sources (DD-016).
4. Documented start command uses `uvx --compile-bytecode`; the "no writes to site-packages"
   criterion excludes `__pycache__/*.pyc`.
5. Reflex surface contract test (`tests/contract/test_reflex_surface.py`) pins S-5 static
   items now; behavioural items move to F6-*/F7-06.
6. Real Safari remains unverified (Playwright WebKit used as stand-in) — tracked as a
   release check before 0.1.0 (Plan risk R-8).
