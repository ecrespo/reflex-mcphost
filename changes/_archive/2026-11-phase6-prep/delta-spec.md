# Delta Spec — 2026-11-phase6-prep

> Status: `APPROVED` (owner's standing auto-approval, 2026-11-01) · folded 2026-11-01 ·
> Source: Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase6.md`.
> Each item names its target document and section and whether the text is **ADDED**,
> **MODIFIED** or **CONFIRMED**. Code changes implied here (`pyproject.toml` test group and
> console script, CI `e2e` job, `release.yml` smoke additions) land in the Phase 6 tasks.

## Applied edits

- **PRD 1.7 → 1.8**: REQ-NFR-011 — remote-mode clause (D3). No other requirement changes.
- **API 1.11 → 1.12**: §4.10 rules — `confirm_trust` not time-bounded (D5); §6 — `run`
  flags (`--log-json`), wildcard-host refusal, pre-flight and exit-code rules, readiness
  before opening the browser (D3, D4); §8 — `install` row (D1, D3), `servers_panel` row
  (D7), `consent_center` / `ReflexPrompter` rows (D2), rules paragraph.
- **TD 1.11 → 1.12**: DD-011 decision and consequences (D1, D2, D7); Flow D (D2); DD-015
  test-group bullet (D6); DD-016 CLI ↔ app contract (D4); DD-017 origin source and remote
  mode (D3); §5.1 `ui/models.py`, `tests/e2e/` (D6, D7); §6.1 DNS-rebinding row (D3); §8
  E2E row (D6).
- **Plan 1.6 → 1.7**: Phase 6 table — F6-01 (D1), F6-03 (D2), F6-04 (D7) wording, F6-09 split
  into F6-09a / F6-09b (D6), duration 13 d → 13.5 d, objective notes that Phase 6
  verifies from the built wheel and PyPI needs a published pre-release (Analyze P6-11);
  metadata versions refreshed.
- **Tasks 1.13**: Phase 6 written against this delta.

## D1 — Host as an async-context-manager lifespan task (P6-01) · HIGH

**TD DD-011 decision — MODIFIED** (first sentence replaced):

> `ui.install(app, host_factory)` registers, with `app.register_lifespan_task`, an
> **async context manager** (`contextlib.asynccontextmanager`) that builds the Host with
> `host_factory()`, binds the process-wide `ReflexPrompter` with `Host.set_prompter` (any
> prompter the factory set is replaced), enters `async with host` before Reflex starts
> serving, publishes it in the module-level `HostRuntime`, and exits it after Reflex stops
> serving. Reflex enters and exits such lifespan tasks on its `AsyncExitStack`, in one task
> and without the 5 s cancel cap it applies to coroutine lifespan tasks
> (`_LIFESPAN_TASK_CANCEL_TIMEOUT`, Reflex 0.10.1), so `Host.start()` and `Host.aclose()`
> run in the same task (API §4.1) and `aclose()` gets its full 15 s budget (REQ-CON-009).
> One Host per backend process; `install()` is idempotent per `rx.App` and raises
> `RuntimeError` on a second call with a different factory.

**TD DD-011 consequences — ADDED**: a `Host.start()` failure propagates out of the lifespan
start-up, so the backend exits non-zero (the CLI maps this to exit `3`, D4). `HostRuntime.host`
raises `RuntimeError` before start-up and after shutdown. A contract test pins that Reflex
awaits an async-context-manager lifespan task's exit to completion even when it takes longer
than 5 s (S-5 assertion #1, extended).

**API §8 `install` row — MODIFIED**: "Registers an async-context-manager lifespan task that
owns one `Host` (start before serving, `aclose()` after), binds `ReflexPrompter`, installs
the Host/Origin guard (origin from the Reflex config's `deploy_url`, D3)".

## D2 — `PromptRegistry` is the source of every pending prompt (P6-02) · MEDIUM

**TD Flow D — MODIFIED** (replaces steps 3–5):

```
3. ReflexPrompter.<method> registers a PendingPrompt(kind, request_id, payload, created_at)
   in the process-wide PromptRegistry and awaits its answer. kind ∈ {consent (tool call
   or sampling, incl. definition_changed / unattributed), sampling_review, elicitation_form,
   elicitation_url, trust}. The payload is the core request object as given (ConsentRequest
   with its argument preview, SamplingResponseReview, ElicitationRequest, TrustRequest).
4. PromptRegistry publishes added / updated / removed on its own bounded change stream
   (one anyio memory stream per subscriber, drop-oldest + a single "dropped" marker, like
   the EventBus); every tab's HostState watcher merges it with its EventBus subscription
   and renders consent_center from PromptRegistry.pending() (also on first load, so a tab
   opened later sees prompts raised before it).
5. The first tab to answer calls PromptRegistry.resolve(request_id, answer) (idempotent;
   later answers are ignored). The entry is removed — and "removed" published — when it is
   answered, when the core-side wait ends (consent_timeout_s, REQ-CNS-009) or when the
   awaiting coroutine is cancelled (Server disconnected, Host closing): ReflexPrompter
   removes it in a `finally`. An elicitation re-prompt (REQ-HSH-013) reuses the request id
   and publishes "updated" with the new errors, so the same form stays open.
```

Steps 1, 2 and 6 unchanged. `consent.requested` / `consent.resolved` stay on the
`EventBus` for library users and the status bar; the UI does not depend on them to render.

**TD DD-011 — ADDED** (after "first answer wins"): the PromptRegistry is UI-layer state
(`ui/prompter.py`), never core; it holds request payloads in memory only and never persists
or logs them (argument previews are already redacted by core, Art. 5).

**API §8 rows — MODIFIED**: `consent_center()` "Pending consent / sampling consent /
sampling-response review / elicitation (form and url) / trust prompts from the
`PromptRegistry`; non-blocking (a side panel, never a modal that blocks the page)";
`ReflexPrompter` "`Prompter` implementation backed by the process-wide `PromptRegistry`;
`supports_elicitation = True`".

## D3 — Guard origin and remote mode (P6-03, P6-04) · MEDIUM

**PRD REQ-NFR-011 — MODIFIED** (sentence added at the end):

> When the reference app was started with `--unsafe-allow-remote` and a concrete
> non-loopback `--host` (REQ-UI-002), the single accepted `Host` is `<host>:<port>` and the
> single accepted `Origin` is `http://<host>:<port>` instead; no other value is ever
> accepted.

**API §6 behavior — MODIFIED**: "`run` with a non-loopback `--host` and no
`--unsafe-allow-remote` → exit 2 with message; with the flag → starts and prints a warning
banner (REQ-UI-002). A wildcard `--host` (`0.0.0.0`, `::`, empty) is refused with exit 2
even with the flag: the guard needs one concrete origin (REQ-NFR-011). In remote mode the
OAuth loopback callback and the MCP Apps sandbox proxy stay bound to the local machine, so
authorization and MCP Apps work only from a browser on that machine."

**TD DD-017 — MODIFIED** (decision): the guard's single allowed origin is taken from the
Reflex config's `deploy_url` (scheme `http`, host, port) at lifespan start-up. The packaged
`rxconfig.py` sets `deploy_url = api_url = http://<host>:<port>` from the CLI's environment
(D4) and `cors_allowed_origins = [that origin]`. `install()` refuses to start (start-up
`RuntimeError`, CLI exit `3`) when the origin's host is not `127.0.0.1` unless
`REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1` is set (the CLI sets it only for
`--unsafe-allow-remote`); `localhost` is always refused (reserved for the sandbox proxy).
An embedding app (REQ-UI-007) sets its own `deploy_url` to the `127.0.0.1` origin its users
load. The 403 body names the allowed origin.

**TD §6.1 DNS-rebinding row — MODIFIED** mitigation: "… accepting only the single
configured origin (default `127.0.0.1:<port>`; in opt-in remote mode the concrete
`<host>:<port>`, never a wildcard) …".

## D4 — CLI ↔ reference-app process contract (P6-07, P6-08) · LOW

**TD DD-016 — ADDED**:
- `reflex-mcphost run` passes its options to the Reflex child only through environment
  variables: `REFLEX_MCPHOST_HOST`, `REFLEX_MCPHOST_PORT`, `REFLEX_MCPHOST_CONFIG` (absolute
  path, unset when `--config` is not given), `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE` (`1` or
  unset), `REFLEX_MCPHOST_LOG_JSON` (`1` or unset). The packaged app module loads
  `HostConfig.load(path)` once at import time (before the event loop serves, so no blocking
  I/O on the loop) when `REFLEX_MCPHOST_CONFIG` is set, and its `host_factory` returns
  `Host(<that config> or None)`. No secret is passed.
- Pre-flight in the CLI process, before materializing the workspace or starting Reflex:
  argument rules (exit 2), `HostConfig.aload(--config)` (an explicit invalid file → exit 3),
  and opening + closing the database in the configured data dir (failure → exit 3). A child
  that exits non-zero before the UI answered the readiness probe → exit 3; afterwards the
  CLI exits with the child's code, and with 0 after a Ctrl-C / SIGTERM it forwarded.
- Readiness: the CLI polls `GET http://<host>:<port>/ping` (Host header = the allowed
  origin) and opens the browser at `http://<host>:<port>/` only after it answers (skipped
  with `--no-browser`); it always prints that URL. A first run that compiles the frontend
  may take minutes; the probe has no deadline while the child is alive.
- The workspace key is version + content hash of the packaged sources (S-5); it is written
  to a temporary sibling directory and renamed into place, so two concurrent `run`s never
  see a half-written workspace.

**API §6 — MODIFIED**: `run` gains `[--log-json]` (JSON log lines on the console, TD §7.1);
the exit-code table's `3` reads "Fatal startup error (invalid own config, DB cannot open,
Host start failed, Reflex exited before the UI was ready)".

## D5 — Trust prompts are not time-bounded (P6-09) · LOW

**API §4.10 rules — ADDED** (after the `confirm_trust` bullet): "`confirm_trust` is not
bounded by `consent_timeout_s`: the Server stays `connecting` until the prompter answers,
the Server is disconnected or removed (the wait is cancelled) or the Host closes. A UI shows
the pending trust prompt until then." **CONFIRMED** against `core/trust.require_trust`
(no timeout; prompter exception → untrusted).

## D6 — Playwright and the E2E layout (P6-05) · MEDIUM

**TD DD-015 — CONFIRMED** (`test` group lists `playwright`) and **ADDED**: "`playwright`
enters `pyproject.toml`/`uv.lock` with the first E2E task (Plan F6-09b); E2E tests carry the
`e2e` marker, are deselected in the matrix `test` job and run in a dedicated `e2e` CI job on
`ubuntu-latest` only (`uv run playwright install --with-deps chromium`; Firefox and WebKit
join in F7-06). Headless smoke tests (Plan F6-09a) need no browser."

**TD §8 E2E row — MODIFIED**: "Headless smoke (httpx against `reflex-mcphost run
--no-browser` from the built wheel: `/` and `/ping` 200 via `127.0.0.1`, 403 + notice via
`localhost` and a foreign `Host`/`Origin`) and Playwright (Python) E2E against the same run:
consent prompt → allow → result; elicitation form; REQ-NFR-003/004 measurements; MCP App
render + App tool call + teardown (Phase 7)".

**TD §5.1 — MODIFIED**: `tests/e2e/` "headless smoke + Playwright against the reference
app (marker `e2e`)".

**Plan Phase 6 — MODIFIED**: F6-09 → **F6-09a** "Headless smoke: built wheel → `uvx --from
<wheel> reflex-mcphost run --no-browser`; guard 403s; `servers --json`; embeddability check
(`ui` in a foreign app)" (REQ-UI-001/002/007, REQ-NFR-011; 0.5 d; depends on F6-06, F6-08)
and **F6-09b** "Playwright E2E: consent → allow → result; elicitation form; NFR-003/004
checks; `playwright` in the `test` group; `e2e` CI job" (REQ-NFR-003/004, REQ-UI-004/005;
1.5 d; depends on F6-09a).

## D7 — UI scope, view models and bounded rendering (P6-06, P6-10) · LOW

**API §8 `servers_panel` row — MODIFIED**: "Servers view: state, origin, transport,
capabilities, log stream; connect / disconnect / reconnect / authorize / sign-out, enable /
disable (REQ-REG-010) and reload (REQ-REG-013). No add / edit / remove form in v0.1
(definitions are added through the library API or `mcphost.toml`; REQ-REG-009 is
unchanged)".

**TD §5.1 — ADDED**: `ui/models.py` — JSON-serializable view-model builders (plain
`dict`/`list`/`str`/`int`/`bool`) computed from core snapshots (`ServerStatus`, Catalog
entries, `ConsentRule`, `AuditRecord`, pending prompts); imports no `reflex`, so the
mapping and filtering logic is unit-tested without Reflex. Reflex state vars hold only
these values (never core objects).

**TD DD-011 consequences — ADDED**: list views (Catalog, Servers, Permissions, Audit)
filter in the backend over the cached view model on every input change (no debounce) and
render at most 200 rows plus a "N more — refine the filter" line; snapshots are recomputed
per affected Server on `server.state_changed` / `catalog.changed`, not for the whole Host
(REQ-NFR-004, measured in F6-09b).
