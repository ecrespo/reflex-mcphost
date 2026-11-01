# Delta spec — 2026-11-phase8-impl

> Status: `APPROVED` (owner's standing auto-approval) · folded 2026-11-01 ·
> Source: deviations reported while implementing Phase 8 — batch Y (F8-10, F8-09, F8-08,
> F8-11) and batch Z (F8-12, F8-13, F8-14), both in commit `d36d724`; the Windows contract
> fix `bdb303d`; batch AA (F8-01, F8-02, F8-03, F8-06, commit `29d2f1d`) and batch AB
> (F8-04, F8-05, F8-07a, commit `27723f0`), all on `develop`.
> Every item was checked against the code (`core/schema.py`, `core/invoker.py`,
> `core/connection/{supervisor,transports}.py`, `core/auth/oauth.py`, `core/errors.py`,
> `core/events.py`, `core/db.py`, `core/apps/bridge.py`, `reflex_mcphost/__init__.py`,
> `toolset/__init__.py`, `ui/{install,actions,state,apps_bridge}.py`,
> `ui/proxy/{server.py,proxy.html}`, `app/{workspace,cli}.py`, `pyproject.toml`,
> `.github/workflows/{ci,release,pages}.yml`, `tests/security/test_threat_model.py`,
> `tests/contract/test_mcp_sdk_surface.py`, `tests/e2e/test_nfr.py`); where the reported
> wording and the code differed, the code wins and the item says so ("Code check").
> Constitution unaffected. PRD unaffected (no requirement changes meaning: REQ-NFR-021 and
> REQ-INV-007 hold more strictly once the SDK's unbounded output check is gone; REQ-NFR-004's
> two interaction kinds are measured as written; REQ-AUTH-007's sign-out now fails closed,
> which is what it already required). Data Model unaffected (nothing persisted changes; the
> file-mode wording "`data_dir` created `0700`" already matches item 35).

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **API 1.16 → 1.17**: §3.4 — `SecretStoreError` row finalized (operation `delete` is the
  only v0.1 value, fixed message, raised `from None`; item 38; the row and the §4.5
  `sign_out` row were written during batch AB and are versioned here) and the
  `AuthorizationError` row notes `discovery_failed` for a secret store failing at flow start
  (item 26); §4.8 — step 7 row (the only output check; codes `missing_structured_content`,
  `invalid_output_schema`, `output_schema_mismatch`; a failing result's content is not
  returned) and a paragraph on the replaced SDK result validation (item 1); §4.11 — every
  event is in `HostEvent`, `ConsentRulesChanged` is not re-exported from the package root
  (item 13); §6 `run` — pruning never touches unmarked directories or symlinks, >7-day-run
  residual (item 15); §7 — over-cap `503` may arrive as a reset (item 19), exact
  `view-navigated` message, a View may discard itself, nothing relayed after the second
  `load` (items 14, 22); §8 — state-manager mode inferred as Reflex does (item 21), an
  existing `Referrer-Policy` is kept (item 21), the refused-authorization notice text
  (item 20) and the sign-out store-failure notice (item 38); Constitution check (Art. 5);
  change history.
- **TD 1.16 → 1.17**: §0 — "Re-verified on 2026-11-01 (F8-06): no row changed" with the
  `platformdirs` 4.14.2 note (item 37); DD-002 — pid source, POSIX-only pid equality, Job
  Object verified on Windows CI, log levels, pgid-reuse residual (items 5, 6, `bdb303d`);
  DD-008 — fail-closed sign-out and `SecretStoreError`, keyring `set` / `get` failures, HMAC
  key read at start (items 23, 25–27, 38); DD-010 — implementation notes (`ContextVar`
  budget, evaluated-keys walk, per-pattern `additionalProperties`, `format: regex` via
  `regex`, measurement) and a new bullet "SDK result validation replaced" (items 1–4);
  DD-012 — connection cap and self-navigation discard as implemented (items 14, 19, 20, 22);
  DD-016 — pruning rules (`materialize` → `prune`, rename-then-delete, symlinks, residual)
  and the Windows test evidence (items 15, 16, 18); DD-017 — WebKit's empty `Referer`,
  state-manager mode inference (items 12, 21); DD-018 — contract 6 widened, still 7
  contracts (item 39); §6.1 — rows "Referrer leak" (item 12), "Tab state" (item 21), "MCP
  App View navigating its own frame" (item 14), "OAuth code interception …" now also
  covering a failed sign-out delete (item 23), "Remote `$ref` / schema bombs / ReDoS" (item
  33), "Oversized SSE events" (item 34), "Compromised or re-pointed CI dependency" (item 32)
  and a note on the test mapping (item 34); §7.1 — the `catalog.changed` timing logger
  (item 29); §8 — Architecture row (docs-snippet test, item 45), Security row (file modes,
  walk-through; items 34, 35), new Resilience row (item 28), REQ-NFR-004 (b) method (items
  29, 30), perf summary (item 31), warnings as errors (items 40, 41, 51); §9.1 — `verify` job
  as implemented and the `id-token` sentence (items 32, 49); Constitution check (Art. 5);
  change history.
- **Data Model**: not affected (header related versions left at API / TD 1.16 — only edited
  documents are bumped).
- **PRD**: not affected.
- **Plan 1.9 → 1.10**: Phase 8 table — ☑ 2026-11-01 for the tasks done locally (Tasks
  `[x]`): F8-01…F8-06, F8-08…F8-14; F8-07a stays ☐ (Tasks `[~]`), F8-15 and F8-07b stay ☐
  (owner); metadata versions; change history. No task,
  dependency or estimate change.
- **Tasks 1.17 → 1.18**: header; execution-log rows Y, Z, AA, AB completed (commits,
  measurements, delta item ranges; the `^(a|aa)+$` pipe escaped so the row renders);
  change history; **F8-04 `[~]` → `[x]` 2026-11-01** — the CI run for `27723f0` on `develop`
  is fully green (lint, build, `e2e` on three engines, test matrix ubuntu / macos / windows ×
  3.11 / 3.13 with the all-families strict REQ gate and `ResourceWarning` as an error; gap
  G-4 closed). Other status lines unchanged: F8-01…F8-03, F8-05, F8-06, F8-08…F8-14 `[x]`;
  F8-07a `[~]` (`release.yml` PR run on the
  release PR, `verify` only on the tag); F8-15 and F8-07b `[!]` (owner).

## Deferred / known gaps

| # | Gap | Owner | Where it is closed |
|---|---|---|---|
| G-1 | **Secret store failing at authorization start is reported as `discovery_failed`** (AA item 26): the controller classifies any non-SDK exception before metadata as discovery, so a keyring `get` failure when a flow starts shows reason `discovery_failed`, not a secret-store reason. Cosmetic (fails closed, the Server stays `auth_required`); now named in API §3.4 and TD DD-008. | Agent (on request) | **Closed 2026-11-01:** `AuthorizationError` reason `secret_store_unavailable` (additive; API 1.18, TD 1.18 DD-008) — delta `changes/_archive/2026-11-final-gaps` |
| G-2 | **`platformdirs` lock refresh**: 4.14.2 (2026-10-31) is inside the `>=4.14` pin, the lock has 4.14.0 (item 37). | Owner (merge the Dependabot PR) | Dependabot weekly grouped update; not required for `0.1.0` |
| G-3 | **`release.yml` PR run and `verify` job never ran.** The three-OS wheel smoke (incl. `regex` wheels, F8-10), `--version` / `servers --json` and the `check_wheel.py` gates have only run locally; `verify` runs only after `publish` on a real `v*` tag. Also closes F5-06a / F6-08 / F7-07a `[~]`. | Owner (release PR to `main`, tag) | F8-07b |
| G-4 | **F8-04 CI evidence**: strict REQ gate over all families (147/147), `ResourceWarning`-as-error suite and the F8-02 `e2e` numbers are green locally; the CI run of the push of `27723f0` decides F8-04 `[~]` → `[x]`. | — | **Closed 2026-11-01:** CI run for `27723f0` on `develop` fully green; F8-04 `[x]` |
| G-5 | **Real Safari unverified** (Plan R-8): Playwright WebKit (WPE) only. | Owner | F8-15 (manual macOS pass of the S-3 scenarios); CHANGELOG line marked `_pending_` until then |
| G-6 | **S-4 / F0-04**: CIMD with loopback port variance not accepted by a real authorization server yet. | Owner | F0-04 (real AS, or the documented DCR / configured-`client_id` fallback recorded in `S-4.md`) before F8-07b; CHANGELOG line marked `_open_` |
| G-7 | **GitHub Pages** not enabled (source "GitHub Actions"), so the CIMD URL (REQ-AUTH-012) is not live. | Owner | F8-07b (checked before the tag). 2026-11-01: Pages enabled (source "GitHub Actions") under the owner's custom domain; CIMD URL moved to `https://blog.seraph.to/reflex-mcphost/oauth/client.json` (delta `changes/_archive/2026-11-cimd-url`); live check (200, JSON, no redirect) in F8-07b after the release merge |
| G-8 | **PyPI setup**: Trusted Publisher for `release.yml`, environment `pypi` with the owner as required reviewer, `v*` tag protection. | Owner | F8-07b (F5-06b skipped). 2026-11-01: Trusted Publisher reported done by the owner; `v*` tag ruleset active and verified; environment `pypi` exists, protection (reviewer, `v*` policy) being added by the owner — F8-07b's status |
| G-9 | **Pre-release decision**: whether `0.1.0a1` / `0.1.0b1` are still published first (F5-06b, F7-07b `[!]`) or skipped for `0.1.0`. | Owner | **Closed 2026-11-01:** owner decision — skipped; the first release is `0.1.0` (F5-06b, F7-07b `[-]`) — delta `changes/_archive/2026-11-skip-prereleases` |
| G-10 | **Classifier** stays `Development Status :: 4 - Beta` for `0.1.0` (Analyze P8-25 assumption). | Owner | F8-07b release PR (may change it) |
| G-11 | **View first-navigation residual**: a View that navigates its own frame is detected and discarded, but the data carried by that first navigation is not prevented in any engine (S-3; item 14). | Accepted for v0.1 | CHANGELOG known limitations; TD §6.1 residual |
| G-12 | **pgid-reuse residual**: after the stdio group's leader has been reaped, its pgid could be reused by an unrelated process group before `killpg` runs (item 5). | Accepted for v0.1 | TD DD-002 residual |
| G-13 | **Tokens stay in memory after a failed keyring `set`** (item 25): the flow reports `flow_error`, nothing is persisted, and the next `authorize()` completes without the browser for the Host's lifetime. | Accepted for v0.1 | TD DD-008 ("tokens fall back to memory") |
| G-14 | **`ConsentRulesChanged` is not re-exported from `reflex_mcphost`** (item 13), unlike every other event class; it is reachable through the `HostEvent` union and `reflex_mcphost.core.events` (private path). API §4.11 now says so. | Agent (on request) | **Closed 2026-11-01:** re-exported (`__all__`, `tests/unit/test_public_api.py::test_every_host_event_class_is_exported`, API 1.18 §4.11) — delta `changes/_archive/2026-11-final-gaps` |
| G-15 | **Threat-model evidence for the failed sign-out delete**: this fold widens the TD §6.1 "OAuth code interception …" row instead of adding a row (a new row without an `EVIDENCE` entry would fail `tests/security/test_threat_model.py`); the row's evidence list does not yet cite `tests/integration/resilience/test_keyring_failure.py::test_keyring_delete_failure_signs_out_fail_closed` / `::test_ui_sign_out_with_a_failing_keyring_is_a_fixed_notice`. | Agent (test-only follow-up) | **Closed 2026-11-01:** both tests cited, plus `test_sign_out_vectors_cite_sign_out_evidence` — delta `changes/_archive/2026-11-final-gaps` |
| G-16 | **Smaller accepted residuals**: an over-cap proxy `503` may reach a client that already sent bytes as a TCP reset (item 19); a `run` lasting > 7 days can have its workspace pruned by another `run` (item 15); WebKit sends an empty `Referer:` header (no value; item 12); the workflow tests rely on PyYAML present only transitively (item 42); macOS / Windows REQ-NFR-001/002 numbers are informational (CI perf summary). | Accepted for v0.1 | — (PyYAML: declare it in the `test` group if the transitive path disappears) |
| G-17 | **CHANGELOG placeholders**: `## [0.1.0] - YYYY-MM-DD`, Safari `_pending_` and S-4 `_open_` "status as of the tag" (item 48). | Owner | F8-07b release PR (date, F8-15 / F0-04 outcome, version bump) |

## Full deviation list

### Batch Y (F8-10, F8-09, F8-08, F8-11 — `d36d724`)

1. **API §4.8 step 7 — MODIFIED; TD DD-010, §6.1 — ADDED (security fix).** The SDK's
   `ClientSession.call_tool` (mcp 2.3) re-validated `structuredContent` against the listed
   `outputSchema` through `self.validate_tool_result` — stock `jsonschema`, stdlib `re`,
   unbounded, inline on the event loop — **before** the Invoker's bounded step 7: a hostile
   output `pattern` hung the Host. `core/connection/supervisor.py`
   `_defer_result_validation` replaces that method per session with
   `_host_validates_tool_results` (a no-op) when the Client is entered; the Invoker's step 7
   is the only output check. Contract-pinned:
   `tests/contract/test_mcp_sdk_surface.py::test_call_tool_validates_results_through_overridable_method`
   (asserts `call_tool` routes through `self.validate_tool_result` and that method uses
   `jsonschema`). Step 7 outcomes (all `protocol_error`): `missing_structured_content` (an
   output schema but no `structuredContent`), `invalid_output_schema` (the schema cannot be
   used, incl. a pattern evaluation that timed out), `output_schema_mismatch` (message
   "structured content does not match the tool's output schema: <path>: <keyword
   message>" — the first issue, no instance values; `details` = the bounded issue list).
   The failing result's `content` and `structured_content` are dropped (`InvocationResult`
   with empty `content`, `structured_content=None`). Code check: the report listed two codes;
   the code also has `invalid_output_schema` (`_classify`). `invalid_result` stays for SDK
   `RuntimeError` / `ValidationError` (e.g. an unexpected `InputRequiredResult`) and a
   non-`CallToolResult` reply.
2. **TD DD-010 — ADDED.** The 250 ms per-`validate()` budget is held in a `ContextVar`
   (`core.schema._BUDGET`), so it is per thread / task; validators stay immutable (rebuilt per
   descent). Code check: `_BUDGET.set(_PatternBudget(...))` / `reset(token)` around each
   validation.
3. **TD DD-010 — ADDED.** The `unevaluatedProperties` replacements for 2019-09 / 2020-12
   re-implement the evaluated-keys walk, reading `validator._resolver` (read-only, no
   patching); `additionalProperties` matches each `patternProperties` pattern separately.
4. **TD DD-010 — ADDED.** `check_schema`'s `format: regex` uses `regex`
   (`_meta_format_checker`), not `re.compile`.
5. **TD DD-002 — ADDED.** F8-08 reads the pid once after the spawn from the `process` local
   of the suspended `stdio_client` generator (`transports.spawned_pid`), contract-pinned.
   Residual: once the group's leader is reaped its pgid can be reused before `killpg` runs
   (gap G-12).
6. **TD DD-002 — ADDED.** Cleanup logs carry the pgid only: an already empty group at DEBUG,
   leftovers signalled at INFO (SIGTERM) and WARNING (SIGKILL). Code check:
   `reap_process_group` also logs at DEBUG when the group is not fully signalable (EPERM).
7. **CONFIRMED (tooling).** `regex` ships no stubs: `# type: ignore[import-untyped]` with
   the reason; no new dev dependency.
8. **CONFIRMED.** `uv.lock` re-normalized the `importlib-metadata` / `zipp` marker; no
   version change.
9. **CONFIRMED (Tasks F8-10).** The Hypothesis property test draws strings of ≤ 12
   characters.

Measured (→ TD DD-010): `^(a|aa)+$` on stdlib `re` — n = 32: 193 ms, n = 40: 9,225 ms; through
`regex` with the bound ≈ 50 ms, then `SchemaCompileError("pattern evaluation timed out")`;
the event loop never blocked > 300 ms (heartbeat). Open at the time: F8-08 Windows leg,
`release.yml` three-OS wheel smoke, NFR-002 re-run — the first closed by `bdb303d` (below),
the second is gap G-3, the third done in AA.

### Windows contract fix (`bdb303d`)

- **TD DD-002 — ADDED.** `test_spawned_pid_is_readable_from_stdio_client` compares the
  spawned pid with the Server's own pid on POSIX only: on Windows a venv `python.exe` is a
  launcher that starts the base interpreter as a child, so the pids differ. Windows cleanup
  is the SDK's Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `close_process_job`,
  source-pinned); the DD-002 **Assumption** is resolved — the CI run for `bdb303d` passed the
  grandchild / process-cleanup tests on both `windows-latest` legs (Python 3.11, 3.13).
  **Tasks F8-08 — CONFIRMED `[x]`.**

### Batch Z (F8-12, F8-13, F8-14 — `d36d724`)

10. **CONFIRMED (tests).** `tests/conftest.py` autouse fixture `_memory_state_manager` sets
    Reflex's `state_manager_mode` to MEMORY for the suite (the new lifespan check refuses
    DISK); the real mode is exercised in `tests/unit/ui/test_install.py`.
11. **Tasks F8-13 — CONFIRMED.** `tests/unit/ui/test_apps_bridge.py` was edited outside the
    task's Files list (navigated-discard tests).
12. **TD DD-017, §6.1 "Referrer leak" — ADDED.** WebKit sends an empty `Referer:` header
    instead of omitting it; the E2E accepts exactly `""` on WebKit only, a non-empty value
    fails in every engine.
13. **API §4.11 — ADDED.** `ConsentRulesChanged` (`consent.rules_changed`: `rule_ids`,
    `change` ∈ `granted`, `updated`, `revoked`, `suspended`) lives in `core.events` and is a
    member of `HostEvent`; it is **not** re-exported from `reflex_mcphost` (every other event
    class is — gap G-14). Published only after a successful store write. Code check:
    `reflex_mcphost.__init__` lists `HostEvent` and the other nine event classes, not
    `ConsentRulesChanged`.
14. **API §7 Navigation, TD DD-012, §6.1 — ADDED.** A View can discard itself by posting
    `{"type": "view-navigated"}` (harmless: it only closes itself); only that exact message
    is recognised (`ui.apps_bridge.is_navigated`); forgeries from sibling frames are ignored.
15. **API §6 `run`, TD DD-016 — MODIFIED.** Pruning: sibling workspaces that carry the
    marker and whose marker is ≥ 7 days old, `.<version>-<hash>.<random>.tmp` leftovers
    ≥ 1 day; rename-then-delete; unmarked directories, symlinks and files untouched; the
    marker is touched on reuse. Residual: a `run` lasting > 7 days can have its workspace
    pruned by another `run` (gap G-16).
16. **TD DD-016 — MODIFIED.** Pruning lives in `app.workspace.materialize` → `prune`;
    `app/cli.py` is unchanged. Code check: the TD said "the CLI touches …"; now "the
    workspace step".
17. **CONFIRMED (internal).** `ui.actions.cancel_authorization(flow)` never raises (a flow
    whose `cancel` raises is logged at WARNING by type name); `ui.actions` is not public
    (API §3.6).
18. **Tasks F8-14 — CONFIRMED; TD DD-016 — ADDED.** The Windows `CTRL_BREAK_EVENT` test
    skips off Windows or without a console. The first `windows-latest` result is in: the CI
    run for `bdb303d` ran `tests/integration/app/test_cli_windows.py` and it PASSED (not
    skipped) on both legs; F8-14 `[x]`.
19. **API §7 proxy, TD DD-012 — ADDED.** An over-cap `503` (`Retry-After: 1`) may arrive as a
    TCP reset when the client had already sent bytes (accepted, gap G-16).
20. **CONFIRMED (internal names); API §8 — ADDED (notice text).** New names:
    `SandboxProxy.connections`, `MAX_CONNECTIONS = 32` (`ui.proxy.server`),
    `ui.apps_bridge.is_navigated` / `NAVIGATED_NOTICE`, `core.apps.bridge.DISCARD_REASONS`
    (`DiscardReason = Literal["navigated"]`) / `AppBridge.discard`,
    `ui.actions.cancel_authorization`, `ui.state.AUTH_URL_REFUSED`. None is in a public
    `__all__` of API §3.6 (`reflex_mcphost.ui.__all__` unchanged, so the §8 table is
    unchanged); the user-visible text of `AUTH_URL_REFUSED` is recorded in API §8.
21. **API §8, TD DD-017, §6.1 "Tab state" — ADDED.** `Referrer-Policy: no-referrer` is added
    to every UI response that does not already set one (an existing header is kept); the
    lifespan refuses a non-MEMORY state manager using Reflex's own mode inference (unset +
    Redis URL ⇒ `REDIS`, refused). Code check: `ui.install._state_manager_mode` reads
    `config._non_default_attributes` and `prerequisites.parse_redis_url()`.
22. **API §7 Navigation, TD DD-012 — ADDED.** `proxy.html` counts inner `load` events; on the
    second it posts `view-navigated` once and stops relaying that frame; the relay JS is
    unchanged (data-only).

Results: E2E 54 passed on three engines; REQ-NFR-003 max 4.57 s; REQ-NFR-004 p95 97.0 ms;
mutation checks (state-manager check removed, referrer wrapper removed) fail the tests as
expected.

### Batch AA (F8-01, F8-02, F8-03, F8-06 — `29d2f1d`)

23. **TD DD-008, §6.1 "OAuth code interception …" — ADDED (security fix); API §4.5
    `sign_out` — CONFIRMED.** Found by F8-03: `OAuthController.sign_out` called
    `store.clear()` first; a keyring `delete` that raised left the in-memory provider (old
    tokens) in place, and the next `connect` came back `ready` with three bearer requests —
    fail-open after a sign-out the user asked for. Fix (`core/auth/oauth.py`): status reason,
    flags and provider reset first, then `store.clear()`; on failure one WARNING "sign-out
    could not delete the stored credentials (<Type>)" and a raise (typed in item 38).
    Mutation-checked. The §6.1 vector is folded into the OAuth row (first column extended,
    its `EVIDENCE` prefix kept; gap G-15) rather than a new row, so the spec-parsing
    walk-through stays green without a test edit.
24. **Closed by item 38.** The re-raised error was the raw backend exception (`KeyringError`
    with backend text) and `ui.actions.server_action` catches only `MCPHostError` /
    `ValueError`.
25. **TD DD-008 — ADDED (accepted).** A keyring `set` failure: the SDK keeps the tokens it
    obtained in memory; the flow reports `flow_error`; the next `authorize()` completes
    without the browser; nothing persisted (gap G-13).
26. **API §3.4 `AuthorizationError` row, TD DD-008 — ADDED (known limitation).** A keyring
    `get` failure at flow start is reported as `discovery_failed` (the controller's
    `_classify` maps any non-SDK exception before metadata to discovery). LOW; gap G-1.
27. **TD DD-008 — ADDED.** F8-03 injects failures on `oauth:*` entries only; the HMAC key is
    read once at `Host.start()`, so a keyring failing during start-up is a start failure and
    out of the runtime tests' scope.
28. **TD §8 Resilience row — ADDED.** Crash storm at supervisor level with an injected
    recording sleep and a fixed RNG (20 cycles under the real 1…60 s backoff ≈ 6 min of
    simulated time), a real healthy Server on the same bus and task group; asserts the exact
    `backoff_delay` schedule (twice), 20 spawns, ≤ 3 records per attempt, ring ≤ 80, no task
    / fd / process leak.
29. **TD §7.1, §8 — ADDED.** REQ-NFR-004 (b): the publish time is logged by a dedicated idle
    bus subscriber started by each tab watcher (`ui.state.log_catalog_publishes`, logger
    `reflex_mcphost.ui.timing`, DEBUG), ≤ one loop iteration after `EventBus.publish`; the
    level is enabled by `configure_logging(json_lines=True)` (`app/cli.py`, one line). One
    line per tab watcher (duplicates with several tabs). Page `Date.now()` vs log `epoch_ms`
    (`time.time()`), ms resolution.
30. **TD §8 — ADDED; Tasks F8-02 — CONFIRMED.** Fixture `modern_server --toggle-every S`
    (flips `toggle_tool`, publishes `ToolsListChanged`); its fixture test lives in
    `tests/integration/test_fixture_servers.py` (outside AA's Files list).
31. **TD §8 — ADDED.** `tests/perf/conftest.py` prints the REQ-NFR-001/002 p95s in a terminal
    summary (every CI log carries the numbers).
32. **TD §6.1 "Compromised or re-pointed CI dependency", §9.1 — MODIFIED.** `pages.yml` job
    `deploy` holds `id-token: write` (`actions/deploy-pages`, environment `github-pages`);
    "`id-token: write` only on `publish`" holds for the release path only. The threat-model
    test allows exactly these two holders and none at the top level.
33. **TD §6.1 "Remote `$ref` / schema bombs / ReDoS" — MODIFIED.** Names item 1: the SDK's
    own result validation is disabled per session; the bounded step 7 is the only output
    check (contract-pinned). Evidence: contract
    `test_call_tool_validates_results_through_overridable_method`, integration
    `test_catastrophic_output_pattern_is_protocol_error`,
    `test_hostile_server_does_not_block_the_event_loop`.
34. **TD §6.1 "Oversized SSE events" — MODIFIED; §6.1 note, §8 Security row — ADDED.** The
    two rows without a test got one in `tests/security/test_threat_model.py` (SDK default
    1 MiB never overridden in `src/`; every `uses:` SHA-pinned, `id-token` placement,
    `publish` tag-gated in env `pypi`, `uv` pinned). The module parses §6.1 — a row without
    evidence fails the build.
35. **TD §8 Security row — ADDED; DM §1 file modes — CONFIRMED.** A data dir the Host creates
    is `0700`; a pre-existing one keeps its mode (files inside are `0600`). Code check:
    `core.db.open_database` chmods only when it created the directory.
36. **CONFIRMED (Tasks F8-01).** gitleaks 8.21.2 (`gitleaks git` ≡ `detect
    --log-opts=--all`): 64 commits (+ 1 merge) clean. `gitleaks dir .` shows 2 hits, both
    third-party caches under the git-ignored `spikes/S-5/iso/run1/` (not tracked, triaged).
37. **TD §0 — MODIFIED.** Every row verified 2026-11-01: MCP `2026-07-28` still current;
    ext-apps `specification/` holds `2026-01-26` + `draft`; PyPI latest == locked for mcp
    2.3.0, reflex 0.10.1, pydantic-ai-slim 2.64.0, langchain-core 1.7.3, langgraph 1.2.19,
    sqlmodel 0.0.48, keyring 25.7.0, anyio 4.15.1, pydantic 2.14.2, h11 0.16.0, jsonschema
    4.26.0, regex 2026.9.29, python-ulid 4.1.0, tomlkit 0.15.2, uritemplate 4.2.0,
    import-linter 2.15; only `platformdirs` 4.14.2 is newer than the lock (4.14.0), inside
    the pin (gap G-2). No row changed → no other delta.

Measured (Linux, local; → Tasks execution log): REQ-NFR-001 p95 0.016 ms (< 5 ms);
REQ-NFR-002 p95 2.27 ms (< 20 ms; 1.42–2.10 ms in isolated runs); REQ-NFR-003 warm start
3.55, 4.12, 3.55, 3.60, 3.58 s (max 4.12 s, < 10 s); REQ-NFR-004 (a) filter keystroke p95
100.7 ms (median 81.8, max 101.4), (b) `catalog.changed` → Catalog DOM p95 54.6 ms (median
45.3, max 55.1) — each < 200 ms over 20 interactions with 50 Servers × 40 tools.
2,142 passed, 57 skipped; E2E 56 passed.

### Batch AB (F8-04, F8-05, F8-07a — `27723f0`)

38. **API §3.4, §4.5, §8 — MODIFIED; TD DD-008 — ADDED.** Item 24 resolved: new
    `SecretStoreError(server_id, operation)` (`operation: Literal["delete"]`, another value
    raises `ValueError`) in `core.errors`, exported in `reflex_mcphost.__all__`; message
    "secret store unavailable: could not delete the credentials of '<id>'". `sign_out` keeps
    the fail-closed order and raises it `from None` (backend exception not chained, no
    backend text in a traceback). `ui.actions.server_action` maps it to the fixed notice
    `SIGN_OUT_STORE_FAILED` ("Sign out <id> failed: secret store unavailable; the stored
    credentials could not be deleted"; in `ui.actions.__all__`, which is not public — the
    text is recorded in API §8). The §3.4 / §4.5 rows were edited during the batch and are
    versioned by this fold (API 1.17). Tests: `tests/unit/core/test_errors.py`,
    `tests/unit/ui/test_servers_panel.py`, `tests/integration/resilience/test_keyring_failure.py`
    (traceback without backend text; UI path: notice, `disconnected`, next connect
    `auth_required` with 0 bearer requests). Mutation-checked (raw re-raise → 2 failures).
    Not done: a typed error at authorize start / `get` (item 26, gap G-1).
39. **TD DD-018 — MODIFIED.** Contract 6's sources are widened (`ui.models` + `ui.actions`)
    rather than a new contract, so `lint-imports` stays "7 kept" (the orchestrator expected
    8). Contract renamed "UI view models and actions are Reflex-free (F6-02, F8-04, DD-018
    contract 6)"; negative test
    `tests/architecture/test_import_contracts.py::test_ui_actions_importing_reflex_breaks_the_build`.
40. **TD §8 — ADDED.** `ResourceWarning` root causes, both test-only: (a)
    `tests/integration/test_migrations.py` opened six `sqlite3` connections and never closed
    them; collected later, the warning was attributed to whatever test triggered GC —
    `test_mrtr.py::test_undeclared_input_request` in full-suite order (found by bisecting and
    `PYTHONTRACEMALLOC=15`); fixed with a closing `connect` fixture. (b)
    `tests/conftest.py::_serve_http` never closed the Popen `stderr` pipe (~70 warnings, only
    visible with `-W always`); fixed. Phase 7 gap G-5 closed.
41. **TD §8 — ADDED.** `filterwarnings` = `error::ResourceWarning` **and**
    `error::pytest.PytestUnraisableExceptionWarning`: a `ResourceWarning` raised from a
    finalizer reaches pytest as an unraisable exception, which the first filter alone leaves
    as a warning (verified against the old leak: "16 passed, 6 warnings" with one filter, 2
    failed with both). Side effect: any exception in a finalizer fails the run.
42. **CONFIRMED.** `tests/scripts/test_ci_workflow.py` and `test_release_workflow.py` parse
    YAML with PyYAML, present transitively (test group: `langgraph` → `langchain-core`; dev:
    `pre-commit`); no dependency declared (gap G-16).
43. **CONFIRMED (TD §9.1 / CI).** `ci.yml` keeps the `--report-only` step (coverage table in
    the log) next to the strict step (no `--families`).
44. **Tasks F8-04 — CONFIRMED.** Log-level test in
    `tests/integration/ui/test_servers_panel.py`: modern-era fixture with `--logging` through
    `HostState.set_log_level` (the Server's log reaches the tab), a Server without logging →
    failure notice, label "(deprecated)" asserted; legacy `setLevel` stays covered by
    `test_legacy_requests`.
45. **TD §8 Architecture row — MODIFIED.** `tests/unit/test_library_docs.py` redesigned:
    markers `<!-- snippet: verbatim <file> | run | run model=TestModel | rxconfig | embed
    -->`; every Python block in README / `docs/` compiles (top-level `await` allowed) and its
    imports resolve; `run` blocks execute in a subprocess with isolated `HOME` / XDG,
    `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring` (`0600` file) and a tiny stdio
    `filesystem` MCP Server (`read_text_file`) written by the test (tests-only `mcp.server`
    import, Art. 1); POSIX only (Windows discovery uses `APPDATA`). The agent example runs
    with `TestModel` substituted for its one model string.
46. **CONFIRMED (docs).** Docs examples re-run: README library example (`Host()` →
    `wait_ready` → catalog → grant → call → audit) and agent example (TestModel) ran; the
    README OAuth block is a fragment (compile / imports only; the flow is covered by
    `tests/integration/auth`); `docs/library.md` adapter snippets verbatim-checked; embedder
    `rxconfig` + app snippets executed (MEMORY, `deploy_url` accepted, `install`
    registered). No example failed. The model string `anthropic:claude-sonnet-5-5` is current
    (checked 2026-11-01). `docs/library.md` said `Toolset.call` raises `KeyError` → now
    `UnknownToolError` (also a `KeyError`), matching API §4.12.
47. **CONFIRMED.** The new `docs/user-guide.md` is published by `pages.yml` too (it uploads
    all of `docs/`); `docs/oauth/client.json` unchanged (CIMD tests green).
48. **CONFIRMED (CHANGELOG).** `## [0.1.0] - YYYY-MM-DD` (a `<date>` placeholder would render
    as an HTML tag) under an empty `## [Unreleased]`; Phase 8 Added / Changed / Fixed;
    known limitations rewritten for `0.1.0` (removed: `noreferrer`, "install does not
    check"); Safari / S-4 lines carry `_pending_` / `_open_` "status as of the tag" (gap
    G-17). Machine-checked by `tests/scripts/test_release_workflow.py` (P8-24 items present;
    version still `0.1.0.dev0`).
49. **TD §9.1 — MODIFIED.** `verify`: `needs: [build, publish]` (build for the version
    output), the publish tag-gate `if`, no checkout, `permissions: contents: read`,
    `UV_NO_CACHE=1`, PyPI JSON polled 60 × 10 s, `uvx --from reflex-mcphost==$VERSION`
    `--version` / `servers --json --config <minimal>`, Linux `run --no-browser --port 3917`
    polled ≤ 15 min (180 × 5 s) for `/ping` (the first start builds the frontend), stopped with
    SIGTERM (a background job ignores SIGINT), `timeout-minutes: 40`. Runs only on a real
    tag (F8-07b, gap G-3). Code check: the TD said "needs `publish`".
50. **CONFIRMED (README).** Status "0.1.0 release candidate", Install section, final progress
    table (Phase 8 row), workspace pruning, MCP Apps limits for `0.1.0`, OAuth client
    identification + `SecretStoreError`, "Deprecated protocol features"; links to both
    guides.
51. **TD §8 — ADDED.** A third `ResourceWarning` source, found only by the E2E run once the
    filters were errors: `tests/e2e/conftest.py::AppRun` never closed the `run` CLI's text
    stdout pipe read by its pump thread (4 failures + 3 errors in the first `-m e2e` run).
    Fixed: `AppRun.close_pipe()` (join the pump ≤ 5 s, close stdout) on every `stop()` path;
    the pump tolerates a closed pipe. Re-run: 56 passed.

Results (→ Tasks execution log): **2,167 passed, 57 skipped** with `ResourceWarning` as an
error and 0 warnings (core 96.59 %, total 95.92 %); E2E **56 passed** (Chromium, Firefox,
WebKit WPE with the S-3 workaround); strict REQ gate over all families **147/147**;
REQ-NFR-003 warm start max 3.66 s; REQ-NFR-004 filter p95 94.5 ms, `catalog.changed` p95
57.2 ms; ruff, format, mypy strict, `lint-imports` (7 kept), `uv lock --check` clean;
`uv build` + `check_wheel.py` OK (`Requires-Dist: regex>=2026.9`); gitleaks history (65
commits) and working tree clean.

### Code check notes

- The fold was checked with the spec-parsing tests: `tests/security/test_threat_model.py`
  (TD §6.1 rows ↔ `EVIDENCE`), `tests/unit/test_public_api.py` (API §4 classes exported),
  `tests/unit/ui/test_public_ui_api.py` (API §8 table = `ui.__all__`) and
  `tests/scripts/test_check_req_coverage.py` — 18 passed. No class was added to an API §4
  code block and no row to the API §8 table (the new notices are prose), so both
  surface tests stay as they are.
- `reflex_mcphost.toolset.__all__` = `ToolCallOutcome`, `ToolSpec`, `Toolset`,
  `UnknownToolError`; `UnknownToolError` and `SecretStoreError` are also in
  `reflex_mcphost.__all__` (API §3.4 / §3.6 hold).
- Apart from F8-04 (CI, below), the Tasks status lines are unchanged by this fold. The F8-03 status line still says
  "Open (API decision, deviation 24)"; that item is closed by item 38 (recorded in the
  execution-log rows AA and AB).

### CI (orchestrator report)

- The CI run for `27723f0` (push to `develop`) is fully green: lint, build, the `e2e` job on
  Chromium, Firefox and WebKit, and the test matrix (ubuntu / macos / windows × Python 3.11 /
  3.13) with the all-families strict REQ gate and `ResourceWarning` as an error. **Tasks
  F8-04 — MODIFIED status `[~]` → `[x] 2026-11-01`** (gap G-4 closed; Plan ☑). F8-07a stays
  `[~]` (gap G-3); F8-15 and F8-07b stay `[!]` (owner).
