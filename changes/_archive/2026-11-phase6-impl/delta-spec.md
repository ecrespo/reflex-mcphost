# Delta spec — 2026-11-phase6-impl

> Status: `APPROVED` (owner's standing auto-approval) · folded 2026-11-01 ·
> Source: deviations reported while implementing Phase 6 — batch Q (F6-01…F6-03, commit
> `dec371a`), batches R and S (F6-04…F6-08, commit `97443dc`), batch T (F6-09a, F6-09b,
> commit `59967db`) and two Windows test fixes (`b0b8ac2`, `20c477b`), all on `develop`.
> Every item was checked against the code (`ui/**`, `app/**`, `tests/e2e/**`,
> `.github/workflows/{ci,release}.yml`, `scripts/check_wheel.py`, `pyproject.toml`
> import-linter contracts); where the reported wording and the code differed, the code wins
> and the item says so ("Code check").
> Constitution unaffected. PRD unaffected (no requirement changes meaning; the REQ-NFR-004
> target of p95 < 200 ms is unchanged — item 40). Data Model and Plan unaffected.

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **API 1.12 → 1.13**: §6 — `--host` rules (`localhost` / `*.localhost` and malformed
  hosts refused, banner on stderr; item 15), options reach the app only through the
  environment (item 13), `servers` table and exit codes (`servers` with an invalid own
  config → 3; a child exiting 0 before readiness → 0; items 16, 17), exit-code table row 3;
  §8 — table rows `HostRuntime` and `HostState` (state wiring exported for embedders;
  item 34), `install` start-up refusals (no port, non-`http`, wildcard), `503` before the
  origin is resolved, duplicate headers, websocket close code, one Host lifespan per process
  (items 2–4), `consent_center` URL rule (item 30) and the elicitation form validation
  (item 23). `FILTER_DEBOUNCE_MS` is **not** added: it lives in
  `reflex_mcphost.ui.components`, which is not a public module under §3.6 (item 40).
- **TD 1.12 → 1.13**: Flow D step 5 (re-prompt semantics; items 5, 23); DD-011 (debounced
  filters, `HostRuntime` phases, card forms, explicit computed-var deps, `ui.actions`, view
  model rules; items 4–7, 22, 27–29, 31–33, 40); DD-015 (E2E Linux-only; items 35–37);
  DD-016 (child environment, process group and signals, workspace marker, packaged app;
  items 10–14, 17–19); DD-017 (pure-ASGI guard, `503` while starting, duplicate headers,
  `1008`, `deploy_url` refusals; items 2, 3); DD-018 contract 6 (`ui.models` Reflex-free;
  item 8); §5.1 `ui/actions.py`, `ui/components/filters.py`; §6.1 new row "Server-provided
  URLs opened by the UI" (item 30); §7.1 console handler (item 19); §8 E2E row and
  REQ-NFR-004 measurement note (items 35, 39, 40); §9.1 release smoke and wheel checks
  (item 20).
- **Plan**: not affected (no task, dependency or estimate change).
- **Tasks 1.13 → 1.14**: header; execution-log rows for batches Q, R, S and T (with the
  REQ-NFR-003/004 measurements); change history. Status: F6-09a and F6-09b → `[x]
  2026-11-01` — the CI run for `59967db` (push to `develop`) is fully green: lint, build,
  test matrix (ubuntu/macos/windows × 3.11/3.13) and the new `e2e` job (gap G-2 closed);
  the F6-09b status line now carries the full measured warm-start range 3.52–4.65 s.
  F6-08 stays `[~]` (`release.yml` PR run pending, gap G-1).

## Deferred / known gaps

| # | Gap | Where it is closed |
|---|---|---|
| G-1 | **F6-08 PR run:** the `release.yml` CLI smoke (`reflex-mcphost --version`, `servers --json`) has only run locally; `release.yml` has no branch-push trigger (only `v*` tags, `workflow_dispatch` and PRs that change its paths), so the `develop` push run of `59967db` did not include it. The first PR carrying `97443dc` (the PR to `main`) must show `build` + `smoke` green on the 3-OS matrix before F6-08 is `[x]`. | F6-08 (push / PR — owner, or the agent on request) |
| G-2 | **F6-09a / F6-09b CI run:** the `e2e` job (smoke, embedding, Playwright flows, REQ-NFR-003/004) had run only locally. | **Closed 2026-11-01:** CI run for `59967db` (push to `develop`) fully green — lint, build, test matrix (ubuntu/macos/windows × 3.11/3.13) and the `e2e` job; F6-09a, F6-09b `[x]` |
| G-3 | **Windows Ctrl-C forwarding untested.** On Windows the CLI forwards Ctrl-C as `CTRL_BREAK_EVENT` to a `CREATE_NEW_PROCESS_GROUP` child; no test drives it (the E2E suite is Linux-only, item 35; unit tests use a fake child). | Phase 8 — F8-02 (informational macOS/Windows runs) / F8-03 |
| G-4 | **Old workspaces are never pruned.** Every new version or packaged-source change creates `…/app/<version>-<hash>/` (with its `.web/` build) and nothing removes the previous ones. | Phase 8 hardening candidate (prune other `<version>-*` directories on `run`); F8-05 documents the location meanwhile |
| G-5 | **Deviation 5 — re-prompt continuity.** Flow D's "the same form stays open" across a core elicitation re-prompt (REQ-HSH-013) is not implemented: the first `ask` ends before core re-prompts, so the card is `removed` and `added` again with the same id. Mitigated by item 23 (the UI validates against the requested schema before sending, so a core re-prompt is rare). Flow D reworded (item 5). | Accepted for v0.1; Phase 8 if a UX review asks for it |
| G-6 | **Server-provided media:** images are shown only as `data:` URLs of `image/png`, `image/jpeg`, `image/gif`, `image/webp` with at most 8 MiB of base64; SVG and everything else is a placeholder naming the type. | Accepted for v0.1 (v0.2 candidate) |
| G-7 | **Permissions view not live-updated** when a consent answer creates a rule in another tab (refresh on load / explicit action only). REQ-UI-006 does not require live updates. | Phase 8 hardening candidate |
| G-8 | **Refused authorization URL:** when `start_authorization` returns a non-openable URL the UI shows a fixed notice and does not redirect, but the core flow is left to reach its own deadline (`timeout`) instead of being cancelled. | Phase 8 — F8-01 security walk-through (consider cancelling the flow) |
| G-9 | **Deprecated log-level select untested** in the Servers view (`set_log_level`, Logging is deprecated-as-compat, Art. 2). | Phase 8 — F8-04 coverage gaps |
| G-10 | **REQ-NFR-004 measurement scope.** The PRD defines the interaction as "a filter keystroke **or** a `catalog.changed` event … (Playwright trace, Chromium)". F6-09b measures filter keystrokes only, timed in the page (item 39), not `catalog.changed` and not from a Playwright trace file. The budget is met for what is measured; the PRD wording is left unchanged (no meaning change). | Phase 8 — F8-02 final benchmarks per PRD §7 (add `catalog.changed`, or narrow the PRD definition by delta) |
| G-11 | **`ui.actions` Reflex-freedom is not enforced.** Its docstring and item 22 call it Reflex-free, but the only `ui` import-linter contract covers `ui.models` (item 8, code check). | Phase 8 — F8-04 (add `reflex_mcphost.ui.actions` to the contract's sources) |
| G-12 | **`ui.models` cannot forbid `starlette`**: it is reached transitively through the `mcp` SDK that core imports, so the contract forbids `reflex` / `reflex_base` only. | Accepted |
| G-13 | **Interim behaviour superseded (item 9):** between batch Q and F6-06, elicitations waited until `consent_timeout_s` and were cancelled (fail closed). Gone since `97443dc`; no shipped version carried it. | Closed (F6-06) |

## Full deviation list

### Batch Q (F6-01…F6-03)

1. **TD DD-011 consequences — CONFIRMED.** The Reflex lifespan contract test pins the real
   cap (`_LIFESPAN_TASK_CANCEL_TIMEOUT == 5`) and then monkeypatches it to 0.2 s to show
   that a coroutine lifespan task is abandoned at the cap while the async-context-manager
   exit is awaited to completion. Code check: `tests/contract/test_reflex_surface.py`. No
   spec edit (the TD already states the pinned behaviour).
2. **API §8 rules, TD DD-017 — ADDED.** `install()`'s lifespan refuses to start
   (`RuntimeError`) when the Reflex config has no `deploy_url`, its scheme is not `http`,
   it has no explicit port, its host is empty, `localhost` / `*.localhost`, or a wildcard
   (an unspecified IP address), and — without the remote opt-in — any host other than
   `127.0.0.1`. Code check: `ui.install.allowed_origin`; the report omitted the missing
   URL / empty host / `*.localhost` cases.
3. **API §8 rules, TD DD-017 — ADDED.** Until the lifespan has resolved the origin the guard
   lets nothing through: HTTP requests get `503` with a "starting" page (fail closed). A
   request with more than one `Host` or more than one `Origin` header is refused (`403`).
   A refused websocket is closed with code `1008` before accept. Code check:
   `LoopbackGuard`; the guard is a pure-ASGI middleware appended last to
   `app.api_transformer` (outermost), not a Starlette middleware class — TD DD-017 wording
   MODIFIED accordingly.
4. **API §8 table, TD DD-011 — ADDED.** `HostRuntime` is a module-level singleton (an
   instance, not a class) with `host` (raises `RuntimeError` unless running) and `phase`
   (`starting` / `running` / `stopped`); the page banner (`Host starting…` / `Host stopped`)
   is driven by `phase`. Entering a second Host lifespan in the same process while one is
   active raises `RuntimeError`.
5. **TD Flow D step 5 — MODIFIED.** `PromptRegistry.ask` joins on the request id: a second
   `ask` with an id that is still pending replaces the payload, publishes `updated`, and
   every asker receives the one answer. An elicitation re-prompt that arrives after the
   first `ask` ended is a new entry (`removed` then `added` with the same id); the "same
   form stays open" continuity of the prep delta (D2) is not implemented — gap G-5.
6. **TD DD-011 (view models) — ADDED.** Consent cards of an `unattributed` request show
   the asking Consumer as `unattributed` and offer only `allow_once` / `deny` with deny
   lifetime `once` and a single target (`tool`, or `server` for sampling). Sampling consent
   cards offer targets `server` / `global` only (never `tool`). Each card is answered
   through `HostState.answer_card` with per-card `chosen_targets` / `chosen_deny_lifetimes`;
   any option the card does not offer is rejected before core sees it. Code check:
   `ui.prompter.consent_options` — the report did not mention the restricted answers and
   lifetime of unattributed cards.
7. **TD DD-011 (view models) — ADDED.** Servers rows: stdio shows the command only, never
   its arguments (expanded values may be secrets); HTTP shows the destination
   (`scheme://host[:port]`), never path, query or userinfo; env and header **keys** only;
   config-error rows carry their `ConfigProblem` messages; shadowed definitions are listed
   per row. Code check: `ui.models.server_row`, `_destination`.
8. **TD DD-018 — ADDED (contract 6); API §3.6 — CONFIRMED.** `reflex_mcphost.ui.install` is
   the function (it shadows its submodule's name); `ui/__init__` binds `install`,
   `HostRuntime` and `ReflexPrompter` eagerly and resolves every component and `HostState`
   lazily (PEP 562), so importing `reflex_mcphost.ui` does not import Reflex. Import-linter
   contract "UI view models are Reflex-free": `reflex_mcphost.ui.models` ↛ `reflex`,
   `reflex_base` (`starlette` cannot be forbidden: reached through the `mcp` SDK — gap
   G-12). Code check: the report said only `consent_center` / `HostState` are lazy; all five
   components are.
9. **Interim (no spec edit) — CONFIRMED superseded.** Before F6-06 landed,
   `ReflexPrompter.supports_elicitation = True` with no elicitation UI meant elicitations
   waited until `consent_timeout_s` and were cancelled (fail closed). F6-06 (`97443dc`)
   replaced it; API §8 already states `supports_elicitation = True` (gap G-13, closed).

### Batch S (F6-08)

10. **TD §5.1 — CONFIRMED; TD DD-016 — ADDED.** Extra files beyond the task list:
    `tests/unit/app/test_reference_app.py` and `src/reflex_mcphost/app/web/__init__.py` (a
    regular package so mypy and import-linter see `app.web`; it is **not** copied into the
    workspace — the workspace holds `rxconfig.py` and `mcphost_app/` only).
11. **TD DD-016 — ADDED.** The packaged app module builds its `app` attribute lazily
    (PEP 562 `__getattr__`): Reflex triggers it while loading the app; importing the module
    (tests) has no side effect. On first access it configures console logging, loads
    `REFLEX_MCPHOST_CONFIG` once with `HostConfig.load` and calls `install`.
12. **TD DD-016 — ADDED.** The pages come from a registry `PAGES` (`/` Servers, `/catalog`,
    `/permissions`, `/audit`), each naming its view component on `reflex_mcphost.ui`,
    resolved when the app is built; a missing name renders a "view is not available in this
    build" callout instead of failing. Every page has the nav, the Host banner,
    `consent_center` and `on_load=HostState.start_watch`.
13. **TD DD-016 — MODIFIED; API §6 — ADDED (note).** The child environment is the inherited
    environment **minus every `REFLEX_MCPHOST_*` variable** (so an inherited
    `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE` never leaks into a run without the flag) and minus
    `REFLEX_FRONTEND_ONLY` / `REFLEX_BACKEND_ONLY`, plus `GRANIAN_WORKERS=1`,
    `REFLEX_CHECK_LATEST_VERSION=false`, `REFLEX_MCPHOST_HOST`, `REFLEX_MCPHOST_PORT`,
    `REFLEX_MCPHOST_CONFIG` (absolute, only with `--config`),
    `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1` and `REFLEX_MCPHOST_LOG_JSON=1` when applicable.
    Code check: `cli.child_environment`.
14. **TD DD-016 — ADDED.** The child runs in its own session / process group (POSIX
    `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`), so a terminal Ctrl-C reaches
    only the CLI, which forwards it **once** (`SIGINT`; Windows `CTRL_BREAK_EVENT`);
    SIGTERM and (POSIX) SIGHUP are forwarded as SIGTERM (Windows: `CTRL_BREAK_EVENT`); the
    CLI waits up to 30 s, then kills the child; a second Ctrl-C during that wait is
    swallowed (not re-sent). Windows forwarding is untested — gap G-3.
15. **API §6 — MODIFIED.** `--host` rules (all exit 2): a wildcard (`0.0.0.0`, `::`, `*`,
    empty) is refused even with `--unsafe-allow-remote`; `localhost` and `*.localhost` are
    always refused (reserved for the MCP Apps sandbox proxy); a value that is neither an IP
    address nor a valid host name is refused; any host other than `127.0.0.1` needs the
    flag. With the flag the warning banner goes to **stderr** and the URL printed / opened is
    `http://<host>:<port>/` (IPv6 in brackets). Code check: `cli._check_host` — checks run in
    that order, before pre-flight.
16. **API §6 exit codes — MODIFIED.** `servers` with an invalid own config (explicit
    `--config` or the default file) exits `3` with the error on stderr; the table listed
    only 0/1 for `servers`.
17. **API §6 — ADDED.** `servers` table: `ORIGIN` is the source kind (`SourceKind` value, e.g.
    `mcphost_user`, `mcp_json_project`), shadowed definitions appear as extra rows with `STATUS` `shadowed`, and
    the problems are listed below the table (`[code] message`). `run`: a child that exits
    `0` before readiness → exit `0` (only a non-zero exit before readiness is `3`). Code
    check: `cli._servers`, `cli._supervise`.
18. **TD DD-016 — ADDED.** The packaged `rxconfig.py` also sets `backend_host`,
    `frontend_port` = `backend_port` = the port, enables `RadixThemesPlugin` explicitly and
    disables `SitemapPlugin` (besides `telemetry_enabled=False`, `api_url` = `deploy_url` =
    the origin and `cors_allowed_origins` = `[origin]`).
19. **TD §7.1 — ADDED.** `cli.configure_logging` attaches exactly one console handler
    (text, or JSON lines with `ts`, `level`, `logger`, `msg`, the context fields and `exc`)
    carrying the `SecretRedactionFilter` to the `reflex_mcphost` logger at INFO, writing to
    stderr, with `propagate = False` (no duplicate lines through a root handler Reflex
    installs); idempotent. The CLI process and the packaged app (child) both call it.
20. **TD §9.1 — ADDED.** `release.yml` plain-install smoke also runs the console script:
    `reflex-mcphost --version` must print `reflex-mcphost <expected version>` and
    `reflex-mcphost servers --json --config <temp file with schema_version = 1>` must exit 0.
    `check_wheel.py` additionally requires `reflex_mcphost/app/web/rxconfig.py` and
    `reflex_mcphost/app/web/mcphost_app/mcphost_app.py` in the wheel and rejects `.web/`,
    `.states/` and `reflex.lock`. Code check: the smoke config is a minimal valid file
    (`schema_version = 1`), not an empty one.
21. **Tasks F6-08 — CONFIRMED status `[~]`.** The `release.yml` PR run is pending (gap
    G-1); Windows Ctrl-C forwarding untested (G-3); old workspaces not pruned (G-4).

### Batch R (F6-04…F6-07)

22. **TD §5.1, DD-011 — ADDED.** New module `ui/actions.py`: the UI's actions as plain async
    functions over public core methods only (server actions, reload, log level,
    authorization, invoke / cancel, resource / template / prompt reads, rule edit / revoke,
    audit pages). `HostState` handlers are thin wrappers, which also avoids a
    `state ↔ components` import cycle. Its Reflex-freedom is not enforced by a contract —
    gap G-11.
23. **TD Flow D step 5, API §8 `consent_center` — ADDED.** Elicitation form content is
    coerced and validated in the UI against the requested schema (`core.schema`) before it
    is sent; an invalid submit keeps the card open with field errors and nothing reaches the
    Server. A core re-prompt (REQ-HSH-013) still surfaces as `removed` + `added` with the
    same id (item 5).
24. **Tasks F6-07 — CONFIRMED.** Audit rows show `args_hash` (REQ-INV-009/010: the keyed
    hash, never arguments). This reverses a batch Q test that asserted its absence; no spec
    said the hash is hidden.
25. **PRD REQ-HSH-005 — CONFIRMED.** Rejecting a legacy-era sampling review ends the call
    `denied` / `sampling_denied`: the existing core override after an attributed denial
    (Phase 4) applies; the UI adds no behaviour.
26. **PRD REQ-AUTH-007 — CONFIRMED.** Sign out → `disconnected`; Connect afterwards →
    `auth_required` (core behaviour, shown as is).
27. **TD DD-011 — ADDED.** The sampling card's Edit action approves **once** only
    (`allow_once` with `edited_sampling`, target as chosen on the card); only text messages
    are editable; a rejection carries no edit. Code check: `HostState._approve_edited`.
28. **TD DD-011 — ADDED.** `schema_form`: array fields (`string_list` / `enum_list`) are a
    textarea with one value per line; the form also offers "Edit as JSON".
29. **TD DD-011 — MODIFIED.** Audit text filters apply on blur; every other filter input
    applies per keystroke (debounced, item 40).
30. **TD §6.1 — ADDED (new row); API §8 — ADDED (rule).** Defense in depth (automated
    security review): the UI opens a Server-provided URL only if `ui.models.is_openable_url`
    holds (`http` / `https` with a hostname), a second layer after core's own checks. A
    url-mode elicitation whose URL fails it is never rendered as a link; accepting it is
    sent to core as **decline**, with no redirect and a fixed `prompt_error` (the URL is not
    echoed). A non-openable authorization URL → no redirect and a fixed `server_notice`
    (the URL carries `state` and is never echoed); the core flow is left to its own deadline
    — gap G-8.
31. **TD DD-011 — ADDED (internal).** `HostView` / `watch_host` take an optional
    `on_finished` callback and the watcher's `Sink` protocol gains `invocation_finished`;
    the tab uses it to refresh the Audit view after an invocation ends. Not public API.

Deferred items of batch R: media rendering limits (G-6), Permissions view not live
(G-7), refused authorization URL (G-8), deprecated log-level select untested (G-9);
browser-level behaviour went to F6-09b (done; CI green — G-2 closed).

### Batch T (F6-09a, F6-09b)

32. **TD DD-011 — ADDED (defect fixed).** `HostState.catalog_shown` / `catalog_more` read
    the current tab's rows with `getattr`, which Reflex's automatic dependency tracking does
    not see, so the list never rendered in a browser. Fixed with explicit
    `@rx.var(deps=list(CATALOG_TABS.values()))`; rule: a computed var that selects a field
    dynamically declares its deps.
33. **TD DD-011 — ADDED (defect fixed).** Card forms inside `rx.foreach` captured the loop
    variable in `on_submit` (browser `ReferenceError`). Fixed: each card form carries a
    hidden input `CARD_FIELD = "__card_id__"` and submits to form-only handlers
    (`submit_elicitation_form`, `approve_edited_sampling_form`, `submit_review_edit_form`)
    that read the card id from the form data; `schema_form` gains `hidden=`. Rule: an event
    handler inside `rx.foreach` never takes the loop variable through a form's `on_submit`.
    Internal wiring, not API.
34. **API §8 table — ADDED.** `reflex_mcphost.ui.__all__` = the API §8 names minus
    `mcp_app_frame` (Phase 7) plus `HostRuntime` and `HostState` (state wiring an embedding
    app needs: `on_load=HostState.start_watch`, `HostRuntime.host`). Both get their own
    §8 rows; `tests/unit/ui/test_public_ui_api.py` reads the names from the §8 table.
35. **TD DD-015, §8 — ADDED.** The E2E tests run on Linux only (they read `/proc` for the
    process tree and listening sockets) and skip elsewhere; the CI `e2e` job is
    `ubuntu-latest`.
36. **TD §8 — ADDED.** The smoke's Ctrl-C is `SIGINT` to the run's process group (a
    terminal Ctrl-C); `uv` ignores a `SIGINT` sent to itself alone.
37. **TD DD-015 — ADDED.** The smoke runs `uv tool run --from <wheel> --compile-bytecode
    reflex-mcphost …` (= `uvx`) reusing the developer's `UV_CACHE_DIR` while
    `XDG_CACHE_HOME` is a temporary directory (the DD-016 workspace lives there).
38. **Tasks F6-09b — CONFIRMED.** The modern fixture's `--tools N` replaces its tool list
    with `N` generated echo tools (tests only, Art. 1).
39. **TD §8 — ADDED.** REQ-NFR-004 is measured in the page: a capture-phase `keydown`
    listener stamps `performance.now()` at the filter keystroke and a `MutationObserver`
    stamps the list DOM update; 20 interactions alternate a digit and Backspace in the
    Catalog filter with 50 Servers × 40 tools; nearest-rank p95. Filter keystrokes only,
    not `catalog.changed`, and no Playwright trace file — gap G-10.
40. **TD DD-011, §8 — MODIFIED (no PRD change).** REQ-NFR-004 was first missed (p95 ≈
    347 ms) because Reflex debounces a controlled `rx.input` for 300 ms by default.
    Resolution (orchestrator, option a): the Catalog, Servers and Permissions filter inputs
    use `debounce_timeout=FILTER_DEBOUNCE_MS` = 50 ms (`ui/components/filters.py`); the
    Audit text filters stay on blur. Re-measured p95 105.0 ms, median 83.8 ms, max 110 ms
    (budget p95 < 200 ms; PRD target unchanged). `tests/unit/ui/test_filter_latency.py`
    pins the debounce (≤ 50 ms) on the three views. `FILTER_DEBOUNCE_MS` is exported from
    `reflex_mcphost.ui.components`, which is not a public module (API §3.6), so it is an
    implementation constant, not API.

### Windows test fixes (`b0b8ac2`, `20c477b`)

- **No spec edit — CONFIRMED.** `tests/unit/ui/test_reflex_prompter.py` compares the trust
  card's `cwd` and origin path with the platform's path form (`str(Path(...))`), so the
  test passes on Windows; behaviour unchanged.

### CI (orchestrator report)

- The CI run for `59967db` (push to `develop`) is fully green: lint, build, test matrix
  (ubuntu / macos / windows × Python 3.11 / 3.13) and the new `e2e` job. **Tasks F6-09a,
  F6-09b — MODIFIED status `[~]` → `[x] 2026-11-01`.** F6-08 stays `[~]`: `release.yml`
  did not run (no branch-push trigger; gap G-1).

### Measurements (Tasks execution log)

- REQ-NFR-003: warm start (`run --no-browser` → `servers-panel` visible) 3.52–4.65 s
  across runs (budget < 10 s in each of 5 runs) — Linux, local; the same test passed in
  the CI `e2e` job (run for `59967db`; its timings are not recorded here).
- REQ-NFR-004: p95 105.0 ms (median 83.8, max 110) after the 50 ms debounce; 347 ms
  before (budget p95 < 200 ms) — Linux, Chromium headless, local; the same test passed in
  the CI `e2e` job (run for `59967db`).
