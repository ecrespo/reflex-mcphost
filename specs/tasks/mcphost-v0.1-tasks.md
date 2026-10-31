# Tasks — reflex-mcphost v0.1 (Phases 0–1)

> Source specs: `specs/prd/mcphost-v0.1.md` v1.2 · `specs/api/mcphost-api-v0.1.md` v1.2 ·
> `specs/technical/mcphost-architecture.md` v1.1 · `specs/data-model/mcphost-schema.md` v1.1 ·
> `specs/plans/mcphost-v0.1.md` v1.1 · governed by `specs/constitution.md` v1.2
> Plan phases covered: **Phase 0 (spikes)** and **Phase 1 (foundation)** · Generated: 2026-10-31
> Status: `APPROVED` · Version 1.3

Tasks for Phases 2–8 are generated in this same file, one phase at a time, after the
previous phase's review (plan §6). Until then, the REQ columns of the Implementation Plan
are the coverage source for those phases.

## Conventions

- Task IDs are the plan IDs (`F<phase>-<nn>`); a task split later gets a letter suffix
  (`F1-08a`), never a new number.
- Order = execution order unless `[P]`.
- States: `[ ]` pending · `[~]` in progress · `[x] YYYY-MM-DD` done · `[!]` blocked (note).
- Every command runs from the repo root through `uv run` (DD-015).
- Every test docstring starts with the REQ IDs or spec section it verifies
  (`"""REQ-REG-012: …"""` / `"""DM §5: …"""`) so `check_req_coverage.py` can find it.
- Branching: spikes on `spike/S-n` (never merged); Phase 1 tasks on `feat/<task-id>-<slug>`
  → PR to `develop`, Conventional Commits.
- Agent execution: run the batches in §Execution batches; stop for review after each.

---

## Phase 0 — Spikes

Common rules for every spike:
- Code goes under `spikes/S-n/` on branch `spike/S-n` and is **not merged**.
- Output is `specs/plans/spikes/S-n.md` (merged to `develop` via a `docs:` PR) with the
  sections: *Question*, *Environment* (exact versions), *What was tried*, *Answer*,
  *Impact* (`DD-xxx confirmed` or `Delta Spec required: <link>`), *Contract assertions*
  (signatures/behaviors F1-11 must pin).
- Timebox = the plan estimate. Exceeding it → stop, write what is known, mark `[!]`, and
  raise it at review.

### F0-01 · S-1: MRTR surface of `session.call_tool` [P]
- **Status**: [x] 2026-10-31 — Delta Spec required (see Execution log)
- **What**: With `mcp>=2.3,<2.4`, against an in-process modern fixture and a legacy Server
  (`uv run --isolated --with "mcp>=1.30,<2"`): (a) confirm the exact signature of
  `client.session.call_tool(name, arguments, *, allow_input_required=True,
  input_responses=…, request_state=…, progress_callback=…)` and the `InputRequiredResult`
  shape; (b) drive one elicitation round and one sampling round by hand; (c) observe what a
  registered callback returning `ErrorData` produces under modern (MRTR) vs legacy
  (server-initiated request); (d) check progress notifications reach the callback.
- **REQ**: REQ-HSH-040/041/043, REQ-INV-005 (informs) · DD-001
- **Files**: `spikes/S-1/*`, `specs/plans/spikes/S-1.md`
- **Depends on**: —
- **Done**: `S-1.md` committed with a runnable snippet per item (a)–(d) and a verdict on
  DD-001.

### F0-02 · S-2: stdio stderr capture and exit detection [P]
- **Status**: [x] 2026-10-31 — Delta Spec required (see Execution log)
- **What**: Determine the types `stdio_client(params, errlog=…)` accepts in 2.3.x (file,
  text stream, anything with `write`?); whether stderr can be fed line-by-line into an
  in-memory ring buffer without a thread; and how a supervisor parked inside
  `async with Client(...)` learns that the child process exited (measure latency).
- **REQ**: REQ-CON-005/007 (informs) · DD-002
- **Files**: `spikes/S-2/*`, `specs/plans/spikes/S-2.md`
- **Depends on**: —
- **Done**: `S-2.md` shows stderr lines captured from a fixture that writes to stderr, and
  measured exit-detection latency < 1 s (or a Delta Spec link if not achievable).

### F0-03 · S-3: MCP Apps origin pair across browsers
- **Status**: [x] 2026-10-31 — DD-012 confirmed in Chromium/Firefox/WebKit; folded via delta `2026-10-spike-batch-b` (real Safari unverified, Plan R-8)
- **What**: Minimal page on `http://127.0.0.1:<p1>` embedding a proxy on
  `http://localhost:<p2>` (`sandbox="allow-scripts allow-same-origin"`), proxy creating
  an inner `srcdoc` View (`sandbox="allow-scripts"`, CSP meta). Load one example App from
  `modelcontextprotocol/ext-apps` (pinned `2026-01-26`) and relay one `tools/call` to a
  stub. Verify in Chrome, Firefox, Safari: the View renders; `document.cookie` and
  `localStorage` of the UI origin are unreachable from proxy and View; `localhost`
  resolution on IPv6-first systems (`::1`) works with the proxy bound to both stacks.
- **REQ**: REQ-APP-002/003/011 (informs) · DD-012
- **Files**: `spikes/S-3/*`, `specs/plans/spikes/S-3.md`
- **Depends on**: — (needs macOS for Safari)
- **Done**: `S-3.md` with a per-browser result table (render / tool call / isolation
  checks) and screenshots under `specs/plans/spikes/assets/`.

### F0-04 · S-4: CIMD with loopback redirect
- **Status**: [!] blocked 2026-10-31 — needs access to a real authorization server advertising `client_id_metadata_document_supported` (plan §2 prerequisite, owner action). Gates only F4-07/F4-08.
- **What**: Using `OAuthClientProvider(client_metadata_url=…)` and a draft CIMD document
  hosted temporarily (GitHub Pages branch or gist raw URL), authorize against at least one
  real authorization server that advertises `client_id_metadata_document_supported`, with
  redirect `http://127.0.0.1:<ephemeral>/callback`. Then force the DCR fallback. Record
  whether the AS accepts port variance (RFC 8252 §7.3) and the `iss` parameter behavior.
- **REQ**: REQ-AUTH-002/003/010/012 (informs) · DD-009
- **Files**: `spikes/S-4/*`, `specs/plans/spikes/S-4.md`
- **Depends on**: prerequisite "access to a real AS" (plan §2)
- **Done**: `S-4.md` records a token obtained via CIMD, or via the documented fallback with
  the AS's rejection reason. No token or code value appears in the note (Art. 5).

### F0-05 · S-5: `uvx` start and workspace materialization [P]
- **Status**: [x] 2026-10-31 — DD-011/016/017 confirmed with corrections (Linux only); folded via delta `2026-10-spike-batch-b`
- **What**: Throwaway package with a trivial Reflex 0.10 app shipped inside the wheel;
  `uvx --from ./dist/<wheel> <cli> run` materializes the workspace in
  `platformdirs.user_cache_dir(...)/app/<version>/` and starts Reflex there. Measure cold
  and warm start on Linux, macOS, Windows; confirm no writes under site-packages. Also
  prototype one `@rx.event(background=True)` watcher consuming an anyio memory stream fed
  from a lifespan task (risk R-5).
- **REQ**: REQ-UI-001, REQ-NFR-003 (informs) · DD-011, DD-016
- **Files**: `spikes/S-5/*`, `specs/plans/spikes/S-5.md`
- **Depends on**: —
- **Done**: `S-5.md` with cold/warm timings per OS (warm < 10 s target), a site-packages
  mtime check, and the watcher prototype result.

### F0-06 · S-6: Adapter shapes [P]
- **Status**: [x] 2026-10-31 — Delta Spec required (see Execution log)
- **What**: Write a stub `AbstractToolset` for PydanticAI `>=2.64,<3` (`get_tools`,
  `call_tool`) and a LangChain `StructuredTool` built from a dict `args_schema`
  (`langchain-core>=1.7`), both delegating to a fake async invoker; run one agent turn each
  with a test/fake model.
- **REQ**: REQ-TLS-006/007 (informs)
- **Files**: `spikes/S-6/*`, `specs/plans/spikes/S-6.md`
- **Depends on**: —
- **Done**: `S-6.md` with both stubs executing a tool call through the fake invoker and the
  exact base-class/method signatures to pin.

---

## Phase 1 — Foundation

### F1-01 · Project scaffold
- **Status**: [x] 2026-10-31
- **What**: `pyproject.toml` (PEP 621, hatchling, src layout, `requires-python = ">=3.11"`,
  runtime deps with the bounds of Tech Design §0, extras `pydantic-ai` / `langgraph` /
  `all`, PEP 735 groups `dev` / `test` / `docs`, console script `reflex-mcphost`);
  `uv.lock`; empty package tree per Tech Design §5.1 with `py.typed` and module docstrings;
  `src/reflex_mcphost/__init__.py` exposing `__version__`; `CHANGELOG.md` (Keep a
  Changelog, `Unreleased`); `.gitignore` additions (`spikes/`, `.web/`).
- **REQ**: — (DD-015, Art. 9)
- **Files**: `pyproject.toml`, `uv.lock`, `src/reflex_mcphost/**`, `CHANGELOG.md`, `.gitignore`
- **Depends on**: Batch A (F0-01, F0-02, F0-06) and F0-05 reviewed (F0-04 gates only F4-07/F4-08; F0-03 only Phase 7)
- **Done**: `uv sync --all-extras --all-groups` succeeds; `uv run python -c "import
  reflex_mcphost; print(reflex_mcphost.__version__)"` prints `0.1.0.dev0`; `uv build`
  produces sdist + wheel.

### F1-02 · Quality tooling
- **Status**: [x] 2026-10-31
- **What**: ruff (lint + format) config in `pyproject.toml`; `mypy --strict` for `src/`
  (pydantic plugin); `.pre-commit-config.yaml` with gitleaks, ruff, ruff-format, and local
  hooks `uv run mypy src`, `uv run lint-imports`; `CONTRIBUTING.md` section listing the
  commands.
- **REQ**: — (Art. 8)
- **Files**: `pyproject.toml`, `.pre-commit-config.yaml`, `CONTRIBUTING.md`
- **Depends on**: F1-01
- **Done**: `uv run pre-commit run --all-files` passes on the scaffold.

### F1-03 · Architecture contracts
- **Status**: [x] 2026-10-31
- **What**: import-linter contracts in `pyproject.toml` exactly as DD-018 1–4; AST test
  `tests/architecture/test_single_effect_path.py` that scans `src/` for `call_tool` /
  sampling-provider call sites and fails outside `core.invoker`, `core.mrtr`,
  `core.handlers.sampling` (passes vacuously now); a negative test that writes a temporary
  module importing `mcp.server` and asserts `lint-imports` fails.
- **REQ**: — (Art. 1, Art. 3, Art. 4, DD-018)
- **Files**: `pyproject.toml`, `tests/architecture/test_single_effect_path.py`,
  `tests/architecture/test_import_contracts.py`
- **Depends on**: F1-01
- **Done**: `uv run lint-imports` → 4 contracts kept; `uv run pytest tests/architecture`
  green.

### F1-04 · CI pipeline
- **Status**: [x] 2026-10-31 — workflow written and YAML-validated; first CI run pending the first push
- **What**: `.github/workflows/ci.yml`: jobs `lint` (ruff, ruff-format check, mypy,
  lint-imports, gitleaks) and `test` (matrix `ubuntu-latest`/`macos-latest`/
  `windows-latest` × Python `3.11`/`3.13`, `astral-sh/setup-uv` with cache,
  `uv sync --locked --all-extras --all-groups`, `uv run pytest --cov`); coverage gates:
  `reflex_mcphost.core` ≥ 90 %, total ≥ 80 % (enforced by a small step reading
  `coverage.json`, gates active once core has code — until then report-only, flagged in
  the workflow with an expiry comment pointing to F2-01).
- **REQ**: REQ-NFR-030 · Art. 7, Art. 8
- **Files**: `.github/workflows/ci.yml`, `scripts/check_coverage.py`
- **Depends on**: F1-02, F1-03
- **Done**: first PR to `develop` shows 1 lint job + 6 test jobs green.

### F1-05 · REQ coverage and wheel checks [P]
- **Status**: [x] 2026-10-31
- **What**: `scripts/check_req_coverage.py`: parse PRD `REQ-*` IDs and derive each one's
  priority with the PRD §6 priority rule (trailing tag overrides group/sub-group; all
  REQ-NFR are `MUST`); check `MUST` only; collect REQ IDs cited in test docstrings under
  `tests/`, print uncovered IDs; `--strict` exits 1 if any (report-only in CI until F8-04).
  `scripts/check_wheel.py`: open the built wheel, fail if any path starts with `tests/` or
  `spikes/`, or any module source imports `mcp.server`. Unit tests for both scripts with
  tiny fixture PRD/wheel.
- **REQ**: — (Art. 1, Art. 7)
- **Files**: `scripts/check_req_coverage.py`, `scripts/check_wheel.py`,
  `tests/scripts/test_check_req_coverage.py`, `tests/scripts/test_check_wheel.py`,
  `.github/workflows/ci.yml`
- **Depends on**: F1-04
- **Done**: both scripts run in CI; their tests green; on the current tree the coverage
  script lists all MUST REQs as uncovered (expected).

### F1-06 · Core errors and identifier types [P]
- **Status**: [x] 2026-10-31
- **What**: `core/errors.py` with the hierarchy of Tech Design §5.3 / API §3.4, including
  `InputRequestError(server_id, code)` (public
  constructors carry `server_id`, `state`, etc.; `repr` never includes secrets);
  `core/types.py`: `ServerId` (validated `^[A-Za-z0-9_.-]{1,64}$`, rejects `::`), `Fqn`
  (frozen; `parse` splits on the **first** `::`; `.server_id`, `.name`, `__str__`),
  `ConsumerKind` (`StrEnum`) and frozen Pydantic `Consumer` with `human/agent/app/parse/
  __str__` (`name` validated `^[A-Za-z0-9_.-]{1,64}$` — API §2.1), ID generators `new_invocation_id/new_rule_id/new_request_id` (`<prefix>_` +
  26-char ULID). Hypothesis tests for `Fqn` round-trip and `ServerId` validation.
- **REQ**: REQ-REG-012, REQ-CAT-002 (identifier part), REQ-CNS-015 (Consumer identity type)
- **Files**: `src/reflex_mcphost/core/errors.py`, `src/reflex_mcphost/core/types.py`,
  `tests/unit/core/test_errors.py`, `tests/unit/core/test_types.py`
- **Depends on**: F1-01
- **Done**: `uv run pytest tests/unit/core/test_types.py tests/unit/core/test_errors.py`
  green; `uv run mypy src` clean.

### F1-07 · Logging and secret redaction [P]
- **Status**: [x] 2026-10-31
- **What**: `core/secrets.py`: process-wide `SecretRegistry` (register values, `redact(str)`
  → `**********`, thread/task-safe), helpers for `SecretStr` dumping; `core/logging.py`:
  `get_logger(name)` under `reflex_mcphost.*`, `ContextAdapter` adding `server_id`, `fqn`,
  `invocation_id`, `request_id`, `era`; `SecretRedactionFilter` applied to message, args
  and extras. Library installs no handlers (only `NullHandler` on the root package logger).
  Tests: a registered secret never appears in captured records (message, `%s` args,
  extras, exception text).
- **REQ**: REQ-NFR-040, REQ-REG-007 (logging part), REQ-NFR-010 (logging part)
- **Files**: `src/reflex_mcphost/core/secrets.py`, `src/reflex_mcphost/core/logging.py`,
  `tests/unit/core/test_secrets.py`, `tests/unit/core/test_logging.py`
- **Depends on**: F1-06
- **Done**: tests green; `logging.getLogger("reflex_mcphost").handlers` contains only a
  `NullHandler` after import.

### F1-08 · Database, migrations and drift check
- **Status**: [x] 2026-10-31
- **What**: `core/db.py`: create `data_dir` with `0700`, open `mcphost.db` under
  `umask(0o077)`, apply DM §1 pragmas, SQLModel sync engine, single-worker
  `anyio.CapacityLimiter(1)` and `async def run_db(fn, *args)` wrapper over
  `anyio.to_thread.run_sync`; `core/migrations/runner.py` per DM §5 (version read, `VACUUM
  INTO` backup `0600`, apply in `BEGIN IMMEDIATE`, refuse when DB is ahead);
  `PRAGMA quick_check` on open, failure → `MCPHostError` naming the DB path and backup
  instructions (DM §7);
  `core/migrations/0001_initial.py` with every table/trigger/index of DM §2 and the `meta`
  rows; SQLModel row classes of DM §9 in `core/db_models.py`. Tests: fresh DB → version 1;
  idempotent re-run; DB-ahead → `MCPHostError` naming the backup; drift test comparing
  `sqlite_master` against SQLModel metadata; POSIX file modes `0600`/`0700`; audit delete
  blocked by trigger; a corrupted DB file refuses to open.
- **REQ**: REQ-NFR-012 · DM §1, §2, §5, §9 · DD-006
- **Files**: `src/reflex_mcphost/core/db.py`, `src/reflex_mcphost/core/db_models.py`,
  `src/reflex_mcphost/core/migrations/{__init__,runner,0001_initial}.py`,
  `tests/unit/core/test_db.py`, `tests/integration/test_migrations.py`
- **Depends on**: F1-06
- **Done**: `uv run pytest tests/unit/core/test_db.py tests/integration/test_migrations.py`
  green on all CI OSes (mode assertions skipped on Windows with a logged reason).

### F1-09 · EventBus [P]
- **Status**: [x] 2026-10-31
- **What**: `core/events.py`: `HostEvent` discriminated union (API §4.11 types — payload
  models may be placeholders typed as the final names, filled by later phases), `EventBus.
  publish` (never blocks) and `subscribe(buffer=256)` async context manager; drop-oldest
  on overflow with a single `events.dropped(count)` per overflow episode (DD-014).
- **REQ**: — (API §4.11, DD-014)
- **Files**: `src/reflex_mcphost/core/events.py`, `tests/unit/core/test_events.py`
- **Depends on**: F1-06
- **Done**: tests green, including: slow subscriber does not block publisher or a second
  subscriber; overflow yields newest events + exactly one `events.dropped`; unsubscribed
  streams are closed.

### F1-10 · Fixture Servers and pytest fixtures
- **Status**: [x] 2026-10-31
- **What**: `tests/fixtures/servers/modern_server.py` (mcp 2.3: tools incl. one with
  `outputSchema`, one slow/cancellable, one emitting progress, one needing elicitation via MRTR, one writing to stderr periodically, one crashing
  the process; resources + one template; one prompt; `--transport stdio|http --port`);
  `legacy_server.py` (same surface on `mcp>=1.30,<2`, `initialize` handshake,
  `Mcp-Session-Id`); `tests/conftest.py` fixtures `modern_stdio`, `modern_http`,
  `legacy_stdio`, `legacy_http` yielding connection parameters (HTTP via uvicorn on an
  ephemeral port, torn down after the test); marker `interop` for legacy fixtures.
- **REQ**: — (Art. 7, Tech Design §8)
- **Files**: `tests/fixtures/servers/{modern_server,legacy_server}.py`, `tests/conftest.py`,
  `tests/integration/test_fixture_servers.py`
- **Depends on**: F1-04, F0-01, F0-02
- **Done**: `uv run pytest tests/integration/test_fixture_servers.py` green: raw
  `mcp.Client` lists tools on all four fixtures and reports `protocol_version` ≥
  `2026-07-28` for modern and `2025-11-25` for legacy; `uv run python scripts/check_wheel.py`
  confirms fixtures are not in the wheel.

### F1-11 · SDK contract tests
- **Status**: [x] 2026-10-31 — plus `tests/contract/test_reflex_surface.py` (S-5 static items); S-6 behavioural items move to Phase 5, S-5 behavioural items to F6-*/F7-06
- **What**: `tests/contract/test_mcp_sdk_surface.py` pinning, via `inspect.signature` and
  minimal live calls against `modern_stdio`: `Client(...)` keyword arguments used by Tech
  Design Flow B, `session.call_tool` MRTR parameters, `InputRequiredResult` fields,
  callback signatures (elicitation, sampling, list_roots, message_handler, logging),
  `mcp.client.advertise`; plus **every** item of the *Contract assertions* sections of
  `specs/plans/spikes/S-1.md`, `S-2.md` and `S-6.md` (incl. `ContextVar` not visible in legacy
  callbacks; `Client(StdioServerParameters)` has no `errlog`; `errlog` needs `fileno()`; SDK
  stdio waits 2 s / 2 s; `HEADER_MISMATCH == -32020`).
  `tests/contract/test_adapter_surfaces.py` (skipped unless the extra is installed) for the
  S-6 signatures.
- **REQ**: — (DD-001, Tech Design §8 "Contract", risk R-2)
- **Files**: `tests/contract/test_mcp_sdk_surface.py`, `tests/contract/test_adapter_surfaces.py`
- **Depends on**: F1-10, F0-06
- **Done**: `uv run pytest tests/contract` green; temporarily renaming one pinned kwarg in a
  local monkeypatch makes the corresponding test fail (checked once, noted in the PR).

---

## Execution batches

| Batch | Tasks | Review focus |
|---|---|---|
| A | F0-01, F0-02, F0-06 | SDK assumptions in DD-001/002 and adapter shapes still hold |
| B | F0-03, F0-05, F0-04 | Isolation and packaging assumptions (DD-012, DD-016); any Delta Spec needed |
| C | F1-01, F1-02, F1-03, F1-06 | Layout, tooling and contracts match the specs before code accumulates |
| D | F1-04, F1-05, F1-07, F1-09 | CI matrix, gates, redaction design |
| E | F1-08, F1-10, F1-11 | Schema fidelity to the Data Model; fixtures adequate for Phase 2 |

## Traceability matrix (Phases 0–1)

| REQ | Tasks | Tests that cite it |
|---|---|---|
| REQ-REG-012 | F1-06 | `tests/unit/core/test_types.py` |
| REQ-CAT-002 | F1-06 (identifier type; catalog use in F2-11) | `tests/unit/core/test_types.py` |
| REQ-CNS-015 | F1-06 (Consumer type; evaluation in F3-01) | `tests/unit/core/test_types.py` |
| REQ-REG-007 | F1-07 (logging part; UI/errors in F2-02, F6-04) | `tests/unit/core/test_logging.py` |
| REQ-NFR-010 | F1-07 (logging part; full test in F4-09) | `tests/unit/core/test_logging.py` |
| REQ-NFR-012 | F1-08 (DB; token file in F4-06) | `tests/unit/core/test_db.py` |
| REQ-NFR-030 | F1-04 | CI matrix (workflow, not a pytest) |
| REQ-NFR-040 | F1-07 | `tests/unit/core/test_logging.py` |
| REQ-REG-014 | F2-02, F2-05 (Phase 2, planned) | — |
| REQ-HSH-046 | F1-06 (`InputRequestError` type), F3-10 (Phase 3, planned) | — |

Partially covered REQs above stay open in the matrix until their completing task is done.
Spike tasks (F0-*) inform REQs but cite none as verified — they produce no shipped tests.
No SHOULD requirement is deferred by Phases 0–1.

## Execution log

| Date | Tasks | Result | Notes |
|---|---|---|---|
| 2026-10-31 | F0-01, F0-02, F0-06 (Batch A) | ✅ done | S-1: DD-001 confirmed (modern); legacy input-request attribution, legacy sampling-deny and DD-004 legacy timeout need a Delta Spec. S-2: DD-002 confirmed; exit detection needs a transport wrapper (~6 ms), SDK stdio shutdown waits are 2 s/2 s vs REQ-CON-008 5 s/5 s. S-6: API §4.13 holds; LangChain adapter must be a `BaseTool` subclass. Delta Spec pending owner decision. |
| 2026-10-31 | F0-03, F0-05 (Batch B) | ✅ done | S-3: origin pair holds in 3 engines; proxy/View messaging and CSP rules pinned; View self-navigation is a residual risk. S-5: single-port prod mode, `on_load` watcher, 403-body notice. Delta `2026-10-spike-batch-b` folded (auto-approved). F0-04 still blocked. |
| 2026-10-31 | F1-01…F1-11 (Batches C–E) | ✅ done | 127 tests green locally (Linux, Python 3.13): unit, integration (4 fixture Servers, both eras), contract (SDK, adapters, Reflex), architecture. ruff/mypy strict/import-linter clean; wheel check OK. CI matrix (3 OS × 2 Python) not yet run — no push. Coverage and REQ-coverage gates report-only. |

If implementation shows a spec is wrong: **stop, update the spec (or open a Delta Spec),
then continue** — code and specs never diverge silently (Art. 9).

---

## Change history

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1-draft.1 | 2026-10-31 | Ernesto Crespo | Phases 0–1 |
| 1.0 | 2026-10-31 | Ernesto Crespo | Approved (Phases 0–1) |
| 1.1 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-analyze-fixes` (A-06, A-12, A-15, A-16, A-23, A-24) |
| 1.2 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-a` (F1-10, F1-11) |
| 1.3 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-b`; Phases 0–1 progress (F0-03, F0-05, F1-01…F1-11 done) |
