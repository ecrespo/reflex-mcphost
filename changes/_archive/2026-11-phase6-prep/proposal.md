# Proposal — Phase 6 preparation (Reflex UI, reference app and CLI)

| Field | Value |
|---|---|
| Date | 2026-11-01 |
| Author | Ernesto Crespo |
| Status | `APPROVED` 2026-11-01 (owner's standing auto-approval for spec deltas through Phase 8) · FOLDED 2026-11-01 |
| Sources | Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase6.md` (P6-01…P6-10); spike `specs/plans/spikes/S-5.md`; installed `reflex` 0.10.1 (`reflex/app_mixins/lifespan.py`: `_LIFESPAN_TASK_CANCEL_TIMEOUT = 5`, async-context-manager lifespan tasks entered on an `AsyncExitStack`; checked 2026-11-01); `uv.lock` (no `playwright`); `core/trust.py`, `core/consent/policy.py`, `core/events.py` at `dbdc23a` |
| Affects | PRD 1.7 → 1.8, API 1.11 → 1.12, TD 1.11 → 1.12, Plan 1.6 → 1.7 (all folded 2026-11-01); Tasks 1.13 §Phase 6 is written against this delta. Constitution and Data Model unchanged |

## Why

Tasking Phase 6 against the specs, the code and the installed Reflex surfaced one gap that
risks a MUST requirement and several that leave an implementation choice open:

1. **Host shutdown would be cut at 5 s (HIGH, REQ-CON-008/009).** DD-011 says
   `ui.install()` runs the Host "in a lifespan task". Reflex 0.10.1 runs a *coroutine*
   lifespan task as a separate asyncio task and, at shutdown, cancels it and waits only
   `_LIFESPAN_TASK_CANCEL_TIMEOUT = 5` s. `Host.aclose()` has a 15 s budget (REQ-CON-009)
   and stdio shutdown uses 2 s + 2 s per Server (REQ-CON-008). A Host whose clean-up
   outlives 5 s is abandoned while stdio children may still be running. Reflex also accepts
   an *async context manager* lifespan task, entered and exited on its `AsyncExitStack`
   without that cap and in one task — exactly what `Host.start()`/`aclose()` require.
2. **Only consent prompts are visible to the tabs (MEDIUM, REQ-UI-005).** Flow D relies on
   `consent.requested` / `consent.resolved` events. Elicitation, sampling-response review and
   trust prompts have no events, and `consent.requested` deliberately carries no argument
   preview. A tab cannot render, update (elicitation re-prompt with errors) or retire those
   prompts, and a tab opened after a prompt was raised cannot see it.
3. **`--unsafe-allow-remote` cannot work with the guard as written (MEDIUM, REQ-UI-002 vs
   REQ-NFR-011).** The guard accepts only `Host: 127.0.0.1:<port>`; a remote browser sends
   another `Host`, so the flag would start a server nobody can use. Where the guard gets
   its port from (the reference app vs an embedding app, REQ-UI-007) is also unstated.
4. **Playwright is specified but not installed (MEDIUM, drift).** DD-015 lists `playwright`
   in the `test` group and Plan F6-09 / TD §8 rely on it (REQ-NFR-003/004 are measured with
   it), but `pyproject.toml` and `uv.lock` do not contain it.
5. Smaller items: how `reflex-mcphost run` passes `--config`/host/port to the Reflex child
   and when it exits `3`; `--log-json` named in TD §7.1 but absent from API §6; trust prompts
   are not time-bounded (unstated); the Servers view's enable/disable/reload and the absence
   of an add/edit form; REQ-NFR-004's 200 ms budget needs bounded list rendering.

No requirement is weakened. REQ-NFR-011 gains an explicit clause for the opt-in remote mode
REQ-UI-002 already allows; the default (loopback) behavior is unchanged.

## Decisions proposed

- D1 `ui.install()` registers the Host as an **async-context-manager** lifespan task:
  `set_prompter(ReflexPrompter)` → `start()` before Reflex serves, `aclose()` after it stops,
  both in that task, not subject to the 5 s cancel cap. A start failure fails the backend
  start-up.
- D2 `PromptRegistry` is the UI's single source for pending prompts of **every** kind, with
  its own bounded change stream (`added` / `updated` / `removed`) and a `pending()` snapshot;
  the per-tab watcher merges it with the `EventBus`. Core is unchanged.
- D3 The guard's single allowed origin is the Reflex config's `deploy_url`. The reference
  app sets it to `http://127.0.0.1:<port>`; with `--unsafe-allow-remote` and a concrete
  `--host` it is `http://<host>:<port>`; wildcard hosts are refused. `install()` refuses a
  non-`127.0.0.1` origin unless remote mode was opted into. PRD REQ-NFR-011 states the
  remote clause.
- D4 CLI ↔ app contract: environment variables for the child, pre-flight checks before the
  child starts (exit `3`), readiness probe before opening the browser, `--log-json`.
- D5 `confirm_trust` is not time-bounded; the Server stays `connecting` until the prompt is
  answered, the Server is disconnected/removed, or the Host closes.
- D6 Playwright enters the `test` group in F6-09b (code); Plan F6-09 split into F6-09a
  (headless smoke, no Playwright) and F6-09b (Playwright E2E + REQ-NFR-003/004); E2E in a
  dedicated Linux CI job.
- D7 UI scope and structure: Servers view adds enable/disable and reload; no add/edit/remove
  form in v0.1; pure view-model module `ui/models.py`; bounded list rendering for
  REQ-NFR-004.

## Alternatives considered

- D1: (a) keep the coroutine lifespan and shield `aclose()` — the shield does not stop
  Reflex from giving up after 5 s and tearing the loop down; (b) run the Host in the CLI
  process and talk to it over IPC — a second protocol and process boundary for no gain;
  (c) monkey-patch `_LIFESPAN_TASK_CANCEL_TIMEOUT` — private constant (rejected).
- D2: add `prompt.*` events to the core `EventBus` — moves UI bookkeeping into core and
  would publish elicitation messages and trust details to every library subscriber
  (rejected: core stays UI-agnostic; library users already own their Prompter).
- D3: (a) accept any `Host` in remote mode — reopens DNS rebinding for exactly the users
  who exposed the port (rejected); (b) an `install(..., allowed_origin=...)` parameter —
  duplicates `deploy_url`, which Reflex already uses for `api_url`/CORS (rejected); (c) drop
  `--unsafe-allow-remote` — PRD REQ-UI-002 requires it.
- D6: Selenium through `reflex.testing.AppHarness` — not installed either, and S-5 measured
  with Playwright already (rejected).

## Impact

Tasks F6-01…F6-09b (Tasks 1.13) follow these decisions. No owner action is needed to
implement Phase 6. Owner-dependent items (outside the repo) are listed in the Analyze
report: pushing/opening PRs so CI (incl. the new `e2e` job) runs, and — for the Plan's
"`uvx reflex-mcphost` from PyPI" — the PyPI Trusted Publisher and a published pre-release
(F5-06b now, F7-07 `0.1.0b1` later).
