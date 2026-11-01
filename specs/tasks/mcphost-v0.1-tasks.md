# Tasks — reflex-mcphost v0.1 (Phases 0–7)

> Source specs: `specs/prd/mcphost-v0.1.md` v1.2 · `specs/api/mcphost-api-v0.1.md` v1.2 ·
> `specs/technical/mcphost-architecture.md` v1.1 · `specs/data-model/mcphost-schema.md` v1.1 ·
> `specs/plans/mcphost-v0.1.md` v1.1 · governed by `specs/constitution.md` v1.2
> Plan phases covered: **Phase 0 (spikes)**, **Phase 1 (foundation)**, **Phase 2
> (registry, connection and catalog)**, **Phase 3 (consent, audit and invoker)** and
> **Phase 4 (host-side handlers and OAuth)**, **Phase 5 (toolset, adapters and
> `0.1.0a1`)**, **Phase 6 (Reflex UI, reference app and CLI)** and **Phase 7 (MCP Apps and
> `0.1.0b1`)** · Generated: 2026-10-31 (Phases 6–7: 2026-11-01)
> Status: `APPROVED` · Version 1.15 (Phase 7 blocks follow delta
> `changes/_archive/2026-11-phase7-prep`, approved and folded 2026-11-01 — PRD 1.9, API 1.14,
> TD 1.14, DM 1.8, Plan 1.8; Phase 6 implementation deviations folded as delta
> `changes/_archive/2026-11-phase6-impl` on 2026-11-01 — API 1.13, TD 1.13; Phase 6 blocks follow delta
> `changes/_archive/2026-11-phase6-prep`, approved and folded 2026-11-01 — PRD 1.8, API 1.12,
> TD 1.12, Plan 1.7; Phase 5 implementation deviations folded as delta
> `changes/_archive/2026-10-phase5-impl` on 2026-11-01 — API 1.11, TD 1.11; Phase 5 blocks follow delta
> `changes/_archive/2026-10-phase5-prep`, approved and folded 2026-10-31 — API 1.10, TD 1.10,
> Plan 1.6; Phase 4 blocks follow delta
> `changes/_archive/2026-10-phase4-prep`, approved and folded 2026-10-31; no Phase 4 task is
> gated by it any more. Batches M and N deviations folded as delta
> `changes/_archive/2026-10-phase4-impl`)

Tasks for Phase 8 are generated in this same file after the Phase 7 review (plan §6).
Until then, the REQ columns of the Implementation Plan are the coverage source for it.

## Conventions

- Task IDs are the plan IDs (`F<phase>-<nn>`); a task split later gets a letter suffix
  (`F1-08a`), never a new number.
- Order = execution order unless `[P]`.
- States: `[ ]` pending · `[~]` in progress · `[x] YYYY-MM-DD` done · `[!]` blocked (note).
- Every command runs from the repo root through `uv run` (DD-015).
- Every test docstring starts with the REQ IDs or spec section it verifies
  (`"""REQ-REG-012: …"""` / `"""DM §5: …"""`) so `check_req_coverage.py` can find it.
- Branching: spikes on `spike/S-n` (never merged); Phase 1+ tasks on `feat/<task-id>-<slug>`
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
- **Status**: [!] blocked 2026-10-31 — needs access to a real authorization server advertising `client_id_metadata_document_supported` (plan §2 prerequisite, owner action). Re-scoped 2026-10-31 (delta `2026-10-phase4-prep` D9): no longer gates F4-07/F4-08 (tested against the fixture AS, F4-09a); stays open and gates release `0.1.0` (F8-07).
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

## Phase 2 — Registry, connection and catalog

Common rules for Phase 2:
- Everything lives in `reflex_mcphost.core` (Tech Design §5.1); no Reflex, no adapters.
- All file, keyring and DB I/O runs through `anyio.to_thread` (Art. 8); only
  `HostConfig.load()` reads synchronously.
- Third-party fixture config files used by tests are hashed before and after every test
  module that touches the registry (shared fixture `third_party_files`, REQ-REG-008).
- Behaviour the SDK already implements (era probe, `Mcp-Session-Id`, `x-mcp-header`
  mirroring, standard headers) is **not** re-implemented (Art. 2); the task's job is the
  wiring plus a test proving the wire behaviour against the fixture Servers. If the SDK
  turns out not to implement something the PRD requires, stop and open a Delta Spec.

### F2-01 · Config paths and variable expansion
- **Status**: [x] 2026-10-31 — exceptions named `UnresolvedVariableError`/`UnsupportedVariableError` (ruff N818)
- **What**: `core/config/paths.py`: `SourceKind` StrEnum (PRD RF-REG), per-OS paths of
  DD-013 (`platformdirs` for own user file; explicit `home`/`env` injection points so tests
  never read the real home), `discover_sources(cwd, sources_cfg, extra) ->
  list[SourceSpec]` returning the total order with `precedence` 1…8+n.
  `core/config/expand.py`: `${VAR}` / `${VAR:-default}` expander over a provided
  environment mapping; `${workspaceFolder}` → `cwd`; any other `${…}` form (`${input:…}`,
  `${env:…}`, nested) → `UnsupportedVariable(name)`; unset without default →
  `UnresolvedVariable(name)`; `$$`-free (a lone `$` is literal). Expansion allowed only in
  `command`, `args`, `env`, `cwd`, `url`, `headers` (API §5.2).
- **REQ**: REQ-REG-004, REQ-REG-005 · DD-013, API §5.1
- **Files**: `src/reflex_mcphost/core/config/{__init__,paths,expand}.py`,
  `tests/unit/core/config/{__init__,test_paths,test_expand}.py`
- **Depends on**: F1-06
- **Done**: tests green: precedence order for 0/1/3 `extra` files; Linux/macOS/Windows
  path tables via injected platform; hypothesis property — expanding a string with no
  `${` is the identity; unsupported/unresolved forms name the variable.

### F2-02 · Config parsers [P]
- **Status**: [x] 2026-10-31
- **What**: `core/config/models.py`: `ServerDefinition`, `StdioTransport`,
  `HttpTransport`, `OAuthSettings`, `ConfigOrigin`, `ShadowedDefinition`,
  `ConfigProblem` (+ `ConfigProblemCode`), `ServerOverride`, `SourcesConfig`,
  `HostConfig` (API §4.2/§4.3; frozen; `env`/`headers` values `SecretStr`, registered with
  `core.secrets` after expansion). Parsers returning `ParseOutcome(definitions,
  problems)`: `core/config/own.py` (`tomllib`; unknown keys in the **user** file →
  `ConfigError`; project file filtered to `schema_version` + `[servers.*]`, every other
  key → `ConfigProblem(project_setting_ignored)`), `claude.py`/`cursor.py`/`mcp_json.py`
  (`mcpServers`, `type` honoured), `vscode.py` (`servers`, `type: stdio|http|sse`,
  `inputs`). Malformed file → one `ConfigProblem(parse_error)` naming the path and error,
  never an exception; invalid `server_id` → `invalid_server_id`. `type: "sse"` →
  `HttpTransport(legacy_sse=True)`. `HostConfig.load()` / `aload()`.
- **REQ**: REQ-REG-002, REQ-REG-005, REQ-REG-006, REQ-REG-007, REQ-REG-012, REQ-REG-014 ·
  API §4.2, §5.2, §5.3
- **Files**: `src/reflex_mcphost/core/config/{models,own,claude,cursor,vscode,mcp_json}.py`,
  `tests/unit/core/config/test_parsers.py`, `tests/fixtures/configs/**` (sample files of
  every format, including malformed ones)
- **Depends on**: F2-01
- **Done**: tests green: the same server written in each of the 5 formats parses to equal
  `ServerDefinition`s (origin aside); malformed JSON/TOML → `parse_error` and the other
  servers in other files still load; project `[host]`/`[sources]` → one
  `project_setting_ignored` per key; `str()`/`repr()` of any model and every
  `ConfigProblem.message` contain no env/header value (seeded-secret assertion).

### F2-03 · `ServerRegistry` merge
- **Status**: [x] 2026-10-31
- **What**: `core/registry.py`: `ServerRegistry.load()` (async, reads via
  `anyio.to_thread`) → effective `definitions()` (first by precedence, whole definition —
  no field merge), `shadowed()`, `problems()`, `get()`; overrides from the own file applied
  after merge (`enabled`, `timeout_s`, `roots`, `mode`); unexpanded templates kept
  privately for reload diffing; definitions with variable problems are kept and flagged
  for `config_error`.
- **REQ**: REQ-REG-001, REQ-REG-003, REQ-REG-008 · API §4.3
- **Files**: `src/reflex_mcphost/core/registry.py`, `tests/unit/core/test_registry.py`,
  `tests/conftest.py` (`third_party_files` fixture)
- **Depends on**: F2-02
- **Done**: tests green: same id in sources 1, 3 and 8 → definition from 1, two
  `ShadowedDefinition`s naming 3 and 8; every definition exposes id, transport and origin
  path; byte-identical (SHA-256) third-party files after load.

### F2-04 · Registry mutations and reload
- **Status**: [x] 2026-10-31
- **What**: `ServerRegistry.add/update/remove/set_enabled/reload`. Writes only the
  user-level (or `--config`) `mcphost.toml` with `tomlkit`, atomically (tmp in same dir →
  `fsync` → `os.replace`, mode `0600` preserved), keeping user comments and order;
  `update`/`remove` of a third-party id → `ConfigError("third-party definition; use
  set_enabled or overrides")`; `set_enabled` on third-party → `[overrides.<id>]`.
  `reload()` → `RegistryDiff` (added/removed/changed/unchanged, comparing effective
  definitions **including** expanded env values via their templates + current env) and a
  `registry.changed` event. Reconcile hook (`on_diff` callback) consumed by F2-10.
- **REQ**: REQ-REG-008, REQ-REG-009, REQ-REG-010, REQ-REG-013 · DD-013
- **Files**: `src/reflex_mcphost/core/registry.py`, `src/reflex_mcphost/core/config/own.py`,
  `tests/unit/core/test_registry_mutations.py`
- **Depends on**: F2-03, F1-09
- **Done**: tests green: add→reload round-trip; a comment in the user file survives an
  edit; a crash injected between write and replace leaves the old file intact; disabling
  a Claude Desktop server writes an override and leaves the source hash unchanged;
  changing only an env variable's value marks the server `changed`.

### F2-05a · Secret store and install HMAC key
- **Status**: [x] 2026-10-31
- **What**: `core/secret_store.py`: `SecretStore` over `keyring` (service
  `reflex-mcphost`) with the `data_dir/secrets.json` fallback (`os.open(…, 0o600)`, atomic
  replace, JSON layout of DM §3) selected when the keyring backend is `fail`/`null`; every
  call via `anyio.to_thread.run_sync` on a dedicated single-worker `CapacityLimiter`;
  `secret_index` rows record the backend (DM §2.6). `core/hmac_key.py`: load-or-create the
  32-byte `hmac-key` entry, `keyed_hash(obj) -> "hmac-sha256:<hex>"` over DD-007
  canonical JSON. Split out of F3-06/F4-06 because the trust fingerprint (F2-05b) needs
  the key in Phase 2; F3-06 and F4-06 reuse it.
- **REQ**: REQ-NFR-012 · DD-007, DD-008, DM §2.6, §3
- **Files**: `src/reflex_mcphost/core/{secret_store,hmac_key}.py`,
  `tests/unit/core/test_secret_store.py`, `tests/unit/core/test_hmac_key.py`
- **Depends on**: F1-08
- **Done**: tests green with an in-memory keyring backend and with the `fail` backend
  (file fallback, mode `0600` on POSIX); key is stable across reopen; canonical JSON
  treats `1.0` and `1` alike; the key never appears in logs (seeded-secret assertion).

### F2-05b · Project stdio trust store [P]
- **Status**: [x] 2026-10-31
- **What**: `core/trust.py`: fingerprint per DM §2.4 (`keyed_hash` of command, args, cwd,
  origin path, env names **and values**), Q-T1 lookup and insert through `Database.run`;
  `TrustRequest` model (API §4.10) with `env_keys` and `risky_env_keys` from the built-in
  list; minimal `Prompter` Protocol in `core/prompter.py` declaring `confirm_trust` (the
  remaining methods are added by F3-03); `require_trust(definition, prompter)` → `True`
  when already trusted or confirmed, `False` on decline, prompter exception or no prompter
  (fail closed). Applies to every definition (stdio **and HTTP**) whose origin kind is a
  project source (1, 3, 4, 5); the HTTP fingerprint covers URL, origin path and header
  names and values (delta `2026-10-project-trust`).
- **REQ**: REQ-REG-011, REQ-REG-014 · DM §2.4, Q-T1 · API §4.10
- **Files**: `src/reflex_mcphost/core/{trust,prompter}.py`, `tests/unit/core/test_trust.py`
- **Depends on**: F2-05a, F2-03
- **Done**: tests green: first launch asks once, second does not; changing one env value,
  arg or cwd re-asks; `NODE_OPTIONS` appears in `risky_env_keys`; `TrustRequest` and the
  stored row contain no env value; `extra` and user sources never ask; prompter raising →
  untrusted.

### F2-06 · Transport factory
- **Status**: [x] 2026-10-31
- **What**: `core/connection/transports.py`: `build_transport(definition, …)` returning
  our own `mcp.client.Transport` — stdio: `stdio_client(params, errlog=<pipe write end>)`
  with command/args/env/cwd, own process group (POSIX `start_new_session`; Windows Job
  Object via the SDK), wrapped by `ObservedTransport` exposing a `closed` event set on
  read-stream EOF (spike S-2); never `Client(StdioServerParameters)`. Streamable HTTP:
  `streamable_http_client` with an `httpx2` client carrying static `headers`
  (expanded `SecretStr` revealed only here). `core/connection/stderr.py`: per-attempt
  stderr pipe reader (POSIX `wait_readable`; Windows `to_thread` on a dedicated limiter)
  feeding a bounded `LogRing` (`log_buffer_size`) and `server.log` events. Missing
  executable → `CommandNotFound(executable)`.
- **REQ**: REQ-CON-007, REQ-CON-012, REQ-CON-016, REQ-CON-017, REQ-CON-018, REQ-AUTH-009
  (static headers only) · spike S-2, DD-001
- **Files**: `src/reflex_mcphost/core/connection/{__init__,transports,stderr}.py`,
  `tests/integration/connection/{__init__,test_transports}.py`,
  `tests/fixtures/servers/header_recorder.py` (ASGI middleware recording request headers
  of the HTTP fixture)
- **Depends on**: F1-10
- **Done**: tests green against the fixture Servers: stderr lines of `modern_stdio` land in
  the ring buffer and as `server.log` events; `closed` fires < 1 s after the child is
  killed; recorded HTTP headers show `Accept`, `MCP-Protocol-Version`, `Mcp-Method`,
  `Mcp-Name` (incl. a base64-sentinel name) and a mirrored `Mcp-Param-*` header; a fixture
  tool with an invalid `x-mcp-header` annotation is excluded with a warning naming tool and
  reason; a static `Authorization` header reaches the server and never appears in logs.

### F2-07 · `ServerSupervisor`, `ClientLease` and state machine
- **Status**: [x] 2026-10-31
- **What**: `core/connection/supervisor.py`: one task per Server owning `async with
  Client(transport, mode=…)` (DD-002), Flow B steps 1–4 and 8–9: `ServerState` StrEnum and
  the API §4.5 transition table (incl. `config_error`, `disabled`, `failed` with reasons
  `command_not_found: …` and `untrusted_project_server`, `auth_required` on HTTP 401 with
  auth metadata — the authorize flow itself is F4-07); backoff `min(60, 2^n ×
  jitter(0.5–1.5))` capped after jitter, `failed` after 10 consecutive failures, counter
  reset by `reconnect()`; `ServerStatus` (API §4.5) published as `server.state_changed`
  (`old`, `new`, `reason`) on every transition; liveness from `ObservedTransport.closed`.
  `core/connection/lease.py`: `ClientLease` (ref-counted, invalidated when the block
  exits; use after invalidation → `ServerUnavailableError`). Callbacks are not registered
  in Phase 2, so no host-side capability is declared (REQ-CON-003 Phase 2 part; completed
  in F4-05).
  Injectable clock and RNG for deterministic backoff tests.
- **REQ**: REQ-CON-001, REQ-CON-002, REQ-CON-003 (partial), REQ-CON-004, REQ-CON-005, REQ-CON-006, REQ-CON-010,
  REQ-CON-011 · DD-002, API §4.5
- **Files**: `src/reflex_mcphost/core/connection/{supervisor,lease,state}.py`,
  `tests/unit/core/connection/{__init__,test_backoff,test_state_machine}.py`,
  `tests/integration/connection/test_supervisor.py`
- **Depends on**: F2-06, F1-09
- **Done**: tests green: modern and legacy stdio reach `ready` with era, protocol version,
  `serverInfo` and capabilities recorded; killing the child → `reconnecting` → `ready`;
  a server that never starts → exactly 10 attempts then `failed`, delays within the
  jittered bounds and ≤ 60 s (fake clock); every transition produced one
  `server.state_changed`; a lease used after the block exited raises; the fixture
  records an empty client-capability set.

### F2-08 · Era cache, legacy HTTP sessions and SSE fallback
- **Status**: [~] 2026-10-31 — done except REQ-CON-013 deferred (COULD)
- **What**: `core/connection/era_cache.py`: cache key `sha256:` of the stdio process
  config (command, args, cwd, env names+values via `keyed_hash`) or the HTTP origin;
  Q-E1 read/upsert/delete via `Database.run`; supervisor uses
  `Client(mode="2026-07-28", prior_discover=…)` on a fresh hit, drops the entry and
  retries with `auto` on an era-indicating error; `definition.mode` forces an era.
  Legacy Streamable HTTP: verify `Mcp-Session-Id` is sent on every request, `DELETE` on
  disconnect, never sent to modern Servers (all SDK behaviour). A `404` on a session id
  is surfaced by the SDK as `MCPError(INVALID_REQUEST, "Session terminated")` without
  re-initializing (Analyze P2-01): the supervisor detects it on a legacy HTTP Client and
  performs an internal rebuild (exit block, new `Client`, as in DD-003) with no
  `server.state_changed`, no failure counted and a `server.log` entry; the request that
  hit the 404 fails with that error. A contract test pins the SDK's code and message.
  `legacy_sse_fallback` (COULD): connect via `sse_client` when both HTTP attempts fail
  with `400/404/405` without a modern error body, flag `legacy_transport`. Registry
  reload prunes `era_cache` rows of removed Servers (DM §6).
- **REQ**: REQ-CON-013 (COULD), REQ-CON-014, REQ-CON-015, REQ-CON-019 · DM §2.5, §6, Q-E1
- **Files**: `src/reflex_mcphost/core/connection/era_cache.py`,
  `src/reflex_mcphost/core/connection/supervisor.py`,
  `tests/integration/connection/test_era_cache.py`, `tests/interop/{__init__,test_legacy_http}.py`
- **Depends on**: F2-07, F2-05a
- **Done**: tests green: second connect of a modern Server issues no `server/discover`
  (recorded by the fixture); a cached modern entry for a server now running legacy is
  invalidated and the connect succeeds; session-id headers per era as above; forced 404
  re-init leaves state `ready`. If `legacy_sse_fallback` is not reached within the
  estimate it is deferred (COULD) and noted here.

### F2-09 · Disconnect and Host shutdown
- **Status**: [x] 2026-10-31
- **What**: graceful stop in the supervisor: stdio close stdin → 2 s → terminate → 2 s →
  kill (SDK policy, REQ-CON-008 as amended by batch A), whole process group / Job Object;
  legacy HTTP `DELETE`; modern HTTP close listen stream. Stop is shielded from outer
  cancellation. `Host.aclose()` stops all supervisors concurrently under a 15 s overall
  deadline.
- **REQ**: REQ-CON-008, REQ-CON-009
- **Files**: `src/reflex_mcphost/core/connection/supervisor.py`,
  `tests/integration/connection/test_shutdown.py`
- **Depends on**: F2-07
- **Done**: tests green: `--ignore-eof` fixture is terminated after ~2 s, `--ignore-term`
  is killed after ~4 s; a grandchild spawned by the fixture is gone after disconnect
  (POSIX); 5 stubborn servers shut down concurrently in < 15 s total.

### F2-10 · `Host` composition root and `ConnectionManager`
- **Status**: [x] 2026-10-31
- **What**: `core/host.py`: `Host(config=None, *, prompter=None, sampling_provider=None)`
  with no I/O in `__init__`; `start()` = Flow A steps 1–5 (aload config, open DB, load
  registry, spawn a supervisor per enabled definition without problems in the Host task
  group, return without waiting); `aclose()` idempotent; async context manager. Members
  not built yet (`consent`, `invoker`, `audit`, `toolset`) are absent until their phase.
  `core/connection/manager.py`: `ConnectionManager.status/statuses/connect/disconnect/
  reconnect/wait_ready/logs` (API §4.5; `authorize`, `sign_out`, `set_log_level`,
  `set_roots` raise `NotImplementedError` until Phase 4). Registry diffs reconcile
  supervisors (connect added, stop removed, restart changed). Public re-exports in
  `reflex_mcphost/__init__.py` for the Phase 2 surface.
- **REQ**: REQ-REG-001, REQ-REG-013, REQ-CON-001, REQ-CON-011 · API §4.1, §4.5, Flow A
- **Files**: `src/reflex_mcphost/core/host.py`, `src/reflex_mcphost/core/connection/manager.py`,
  `src/reflex_mcphost/__init__.py`, `tests/integration/test_host.py`
- **Depends on**: F2-04, F2-05b, F2-07, F2-09
- **Done**: tests green: `async with Host(config)` with two fixture Servers returns before
  either is `ready`, `wait_ready` resolves both; `wait_ready` on a server that becomes
  `failed` raises `ServerUnavailableError`; reload after removing one server stops only
  that supervisor; an untrusted project stdio server with no prompter ends `failed`
  (`untrusted_project_server`) while the others stay `ready`.

### F2-11 · `Catalog` snapshots
- **Status**: [x] 2026-10-31
- **What**: `core/catalog.py`: on `ready`, `full_refresh` of tools, resources, resource
  templates and prompts the server declared (following cursors to the end); entries per API
  §4.6 (`ToolEntry` … `PromptEntry`) ordered by `server_id`, `name`; FQN via `core.types.
  Fqn`; duplicate tool names → keep first + warning; `definition_hash` = `sha256:` of
  canonical JSON of `{name, title, description, inputSchema, outputSchema, annotations}`;
  `visibility` (default `("model","app")`) and `ui_resource_uri` with the deprecated
  `_meta["ui/resourceUri"]` fallback; annotations stored as display-only data with no
  logic branching on them; on leaving `ready` entries stay with `stale=True`;
  `get_tool` → `UnknownEntryError`. `catalog.changed` events.
- **REQ**: REQ-CAT-001, REQ-CAT-002, REQ-CAT-004, REQ-CAT-008, REQ-CAT-009, REQ-CAT-011,
  REQ-CAT-014 · API §4.6
- **Files**: `src/reflex_mcphost/core/catalog.py`, `tests/unit/core/test_catalog.py`,
  `tests/integration/test_catalog_refresh.py`
- **Depends on**: F2-10
- **Done**: tests green: a paginating fake session (3 pages) yields all entries; the hash
  is stable under key reordering and changes when the description changes; duplicate name
  warning names server and tool; killing a server marks its entries stale and keeps the
  other server's entries fresh; REQ-CAT-011 test asserts no catalog/consent code reads
  annotation hints (AST scan of `core/` for `readOnlyHint`/`destructiveHint` outside
  `catalog.py` model fields).

### F2-12 · Catalog watchers
- **Status**: [x] 2026-10-31
- **What**: modern: one `client.listen(...)` per ready Server opting in to the list-change
  kinds its capabilities declare; on each notification re-fetch only that list and emit
  `catalog.changed(lists=…)`; on stream close while `ready` reopen with the REQ-CON-005
  backoff and re-fetch all lists once reopened (`5xx`/connection failure → supervisor
  `reconnecting`, per API §4.5). Legacy: `message_handler` routes
  `notifications/*/list_changed`. SHOULD: TTL refresh when `ttlMs` elapses, ≥ 30 s.
- **REQ**: REQ-CAT-003, REQ-CAT-012, REQ-CAT-013 (SHOULD)
- **Files**: `src/reflex_mcphost/core/catalog.py`,
  `src/reflex_mcphost/core/connection/supervisor.py`,
  `tests/integration/test_catalog_watch.py`, fixture tool `add_tool` on
  `modern_server.py`/`listen_server.py`/`legacy_server.py` that registers a new tool and
  notifies
- **Depends on**: F2-11
- **Done**: tests green on both eras: calling `add_tool` makes the new tool appear with one
  `catalog.changed(lists=("tools",))` and no resource/prompt re-fetch; a listen stream
  closed by the fixture is reopened and lists re-fetched; TTL refresh honours the 30 s
  floor (fake clock).

### F2-13 · Resource, template and prompt reads [P]
- **Status**: [x] 2026-10-31
- **What**: `Catalog.read_resource`, `read_template` (RFC 6570 via `uritemplate`),
  `get_prompt` through a `ClientLease` using the SDK auto-loop
  (`input_required_max_rounds=8`, DD-001); non-`ready` → `ServerUnavailableError`;
  unknown FQN → `UnknownEntryError`. The `consumer` argument is accepted and stored for
  F3-10 (Consumer `ContextVar`, `InputRequestError`); with no host-side capabilities
  declared in Phase 2, any input request is undeclared and surfaces as an error. COULD:
  `subscribe`/`unsubscribe` (modern `resourceSubscriptions` on the listen stream; legacy
  `resources/subscribe`) and `resource.updated` events.
- **REQ**: REQ-CAT-005, REQ-CAT-006, REQ-CAT-007, REQ-CAT-010 (COULD) · API §4.6
- **Files**: `src/reflex_mcphost/core/catalog.py`, `tests/integration/test_catalog_reads.py`
- **Depends on**: F2-11
- **Done**: tests green on both eras: `fixture://hello` returns text with its MIME type; a
  blob resource returns base64 contents; template `fixture://items/{item}` with
  `{"item": "a b"}` expands per RFC 6570 and reads; `greet` prompt returns messages with
  the argument substituted. COULD items deferred if out of estimate, noted here.

### F2-14 · Interop suite and Phase 2 gates
- **Status**: [x] 2026-10-31
- **What**: `tests/interop/test_era_matrix.py`: modern↔modern, modern Host ↔ legacy
  Server (fallback), no version overlap → `failed` with requested and supported versions
  in the reason (fixture flag `--protocol-versions`), cached-era invalidation; crash
  isolation (three servers, one crashes repeatedly, the other two stay `ready`
  throughout). Turn the REQ-coverage gate to `--strict` for the REG/CON/CAT families
  (`check_req_coverage.py --families REG,CON,CAT`), add it to CI.
- **REQ**: REQ-CON-001, REQ-CON-004, REQ-CON-011, REQ-CON-019 · Tech Design §8
- **Files**: `tests/interop/test_era_matrix.py`, `scripts/check_req_coverage.py`,
  `tests/scripts/test_check_req_coverage.py`, `.github/workflows/ci.yml`
- **Depends on**: F2-08, F2-12, F2-13
- **Done**: `uv run pytest` green; `uv run python scripts/check_req_coverage.py --strict
  --families REG,CON,CAT` passes (all 44 MUSTs cited); third-party fixture files
  byte-identical after the whole suite; core coverage ≥ 90 %; CI green on the 6 cells.

## Phase 3 — Consent, audit and invoker

Common rules for Phase 3:
- The single effect path (Art. 3): after F3-11 no module outside `core/invoker.py` and
  `core/mrtr.py` calls `session.call_tool`/`client.call_tool` (AST test).
- Fail closed everywhere: store, prompter, preview hook, audit or validator failures deny
  or abort with nothing sent to the Server.
- Consent and audit models follow API §4.7/§4.9 and DM §2.2/§2.3 exactly; deviations go to
  a delta at phase end.

### F3-01 · Consent models and `RuleIndex`
- **Status**: [x] 2026-10-31
- **What**: `core/consent/models.py`: `OperationKind`, `ConsentTarget` (+ `tool/server/
  global_` constructors; `tool` invalid for `sampling`), `ConsentRule`, `ConsentRequest`,
  `ArgumentPreview`, `ConsentAnswer`, `ConsentDecision`, `ConsentOutcome` (API §4.7).
  `core/consent/index.py`: in-memory `RuleIndex` holding persistent + session rules with
  the normative evaluation steps 0–3 (unattributed deny-only, match, rank `(target,
  consumer)` lexicographic, deny wins within the top rank, tool-rule suspension on
  definition-hash mismatch reported to the caller). Pure, no I/O.
- **REQ**: REQ-CNS-003, REQ-CNS-015, REQ-HSH-047 (step 0) · API §4.7, DD-005
- **Files**: `src/reflex_mcphost/core/consent/{__init__,models,index}.py`,
  `tests/unit/core/consent/{__init__,test_models,test_index}.py`
- **Depends on**: F1-06
- **Done**: hypothesis property tests: adding a lower-ranked rule never changes the
  outcome; a deny at the top rank always wins; a rule for another Consumer never matches;
  unattributed requests only see deny rules.

### F3-02 · `ConsentStore`
- **Status**: [x] 2026-10-31
- **What**: `core/consent/store.py`: write-through persistence of `always` rules in
  `consent_rule` (DM §2.2, Q-C* queries) through `Database.run`; load all non-expired rules
  into the `RuleIndex` at start; programmatic `grant` (origin `programmatic`), `update`,
  `revoke`, `rules()` (session rules included on request) applied live; suspension
  persisted.
- **REQ**: REQ-CNS-006, REQ-CNS-013, REQ-CNS-014 · DM §2.2
- **Files**: `src/reflex_mcphost/core/consent/store.py`, `tests/unit/core/consent/test_store.py`
- **Depends on**: F3-01, F1-08
- **Done**: tests green: an `always` rule survives reopen; revoke/update visible to the
  next evaluation without restart; the unique key rejects duplicates by updating in place.

### F3-03 · `ConsentPolicy` and prompting
- **Status**: [x] 2026-10-31
- **What**: `core/consent/policy.py`: `ConsentPolicy` (API §4.7) — evaluation steps 0–6,
  `Prompter.ask_consent` with `consent_timeout_s` (`timeout` deny), headless deny
  (`no_prompter`), any exception → deny `error` with the reason recorded; answer → rule
  mapping (`allow_once` nothing, `allow_session` memory, `allow_always`/`deny always`
  persisted; `target_kind` widening; rule scoped to the *requesting* Consumer);
  `consent.requested` / `consent.resolved` events. Extend `core/prompter.py` `Prompter`
  with `ask_consent` (and the remaining §4.10 method signatures as stubs used by Phase 4);
  `DenyAllPrompter`. Host wiring (`host.consent`).
- **REQ**: REQ-CNS-001, REQ-CNS-002, REQ-CNS-004, REQ-CNS-005, REQ-CNS-008, REQ-CNS-009,
  REQ-CNS-010 · API §4.7, §4.10
- **Files**: `src/reflex_mcphost/core/consent/policy.py`, `src/reflex_mcphost/core/prompter.py`,
  `src/reflex_mcphost/core/host.py`, `tests/unit/core/consent/test_policy.py`
- **Depends on**: F3-02
- **Done**: fault-injection tests: store raising, prompter raising, prompter hanging past the
  timeout, no prompter → each denies with the right `source`; a human answer for an agent's
  request creates a rule for that agent only.

### F3-04 · Rug-pull protection [P]
- **Status**: [x] 2026-10-31
- **What**: suspend tool-target rules whose `definition_hash` differs from the catalog's
  current hash (persisted), re-ask with `definition_changed=True`; `server`/`global` rules
  are never suspended and cover tools added later.
- **REQ**: REQ-CNS-007
- **Files**: `src/reflex_mcphost/core/consent/{index,policy}.py`, `tests/unit/core/consent/test_rug_pull.py`
- **Depends on**: F3-03, F2-11
- **Done**: tests green: changing a fixture tool's description suspends its `always` rule
  and prompts with `definition_changed`; a server-level rule keeps allowing.

### F3-05 · Argument previews and redaction [P]
- **Status**: [x] 2026-10-31
- **What**: `core/consent/preview.py`: default pretty-JSON preview replacing values of
  properties with `format: password`, `writeOnly: true` or names matching
  `secret_arg_pattern` by `"••••••"` (nested objects/arrays included), listing
  `redacted_fields`; `register_preview(target, hook)` (sync or async), most specific target
  wins; a raising hook → default preview + warning (never blocks consent).
- **REQ**: REQ-CNS-011, REQ-CNS-012
- **Files**: `src/reflex_mcphost/core/consent/preview.py`, `tests/unit/core/consent/test_preview.py`
- **Depends on**: F3-03
- **Done**: tests green incl. seeded secrets never appearing in the preview body.

### F3-06 · `AuditLog`
- **Status**: [x] 2026-10-31
- **What**: `core/audit.py`: `AuditLog` (API §4.9) over `audit_record` (DM §2.3):
  `begin(record)` (INSERT `started`), `finish(id, outcome, duration, rounds, …)`, single-shot
  `record()` for early failures, `query(AuditQuery)` newest first with filters and
  pagination, `get()`; argument hash via `InstallKey.keyed_hash` (F2-05a); on start, mark
  rows left `started` by a previous process as `interrupted` (DM rule). Host wiring
  (`host.audit`).
- **REQ**: REQ-INV-009, REQ-INV-010, REQ-INV-011, REQ-INV-012 · DM §2.3, DD-007
- **Files**: `src/reflex_mcphost/core/audit.py`, `tests/unit/core/test_audit.py`
- **Depends on**: F1-08, F2-05a
- **Done**: tests green: filters combine; equal arguments give equal hashes, different
  installs differ; delete is blocked by the trigger; `interrupted` marking at start.

### F3-07 · Schema validation [P]
- **Status**: [x] 2026-10-31
- **What**: `core/schema.py`: Draft 2020-12 validators (`jsonschema`) cached by definition
  hash, no remote `$ref` retrieval, bounded error output; compile errors contained per tool
  (tool still listed, invocation → `protocol_error`/`invalid_arguments` with detail);
  elicitation requested-schema subset check (used by F4-01).
- **REQ**: — (DD-010; used by REQ-INV-002/007, REQ-HSH-012)
- **Files**: `src/reflex_mcphost/core/schema.py`, `tests/unit/core/test_schema.py`
- **Depends on**: F1-06
- **Done**: tests green: remote `$ref` is not fetched; a pathological schema compiles or
  fails within a bounded time; error lists are capped.

### F3-08 · `Invoker` pipeline
- **Status**: [x] 2026-10-31
- **What**: `core/invoker.py`: `Invoker.call/start`, `InvocationHandle`, `InvocationResult`,
  `InvocationOutcome`, `InvocationError` (API §4.8); pipeline steps 1–9 in normative order;
  exactly one audit record per attempt (early failures included); `map_sdk_error`
  classification (Flow C step 7); structured-content validation; concurrency (unlimited,
  same Server included); `invocation.started/finished` events. Host wiring (`host.invoker`).
- **REQ**: REQ-INV-001, REQ-INV-002, REQ-INV-006, REQ-INV-007, REQ-INV-008, REQ-INV-012,
  REQ-INV-013, REQ-INV-014
- **Files**: `src/reflex_mcphost/core/invoker.py`, `tests/integration/test_invoker.py`
- **Depends on**: F3-03, F3-06, F3-07, F2-10
- **Done**: tests green against fixture Servers on both eras: success with structured
  content; invalid args → no prompt, one audit row; `isError` → `tool_error`;
  `bad_structured` → `protocol_error`; audit `begin` failing → `audit_unavailable` and the
  fixture records no call; 20 concurrent calls on one Server all succeed.

### F3-09 · Deadlines, cancellation and progress
- **Status**: [x] 2026-10-31
- **What**: timeout covering the whole invocation including MRTR rounds but excluding
  prompter wait (DD-004); per-call → per-Server → default resolution; Consumer cancel
  (`handle.cancel()` and cancellation of the awaiting task, re-raised) using the SDK's
  transport-specific cancellation; progress forwarding (`on_progress`, `handle.progress()`,
  `invocation.progress` events); optional timeout reset on progress.
- **REQ**: REQ-INV-003, REQ-INV-004, REQ-INV-005 · DD-004
- **Files**: `src/reflex_mcphost/core/invoker.py`, `tests/integration/test_invoker_deadlines.py`
- **Depends on**: F3-08
- **Done**: tests green per transport (stdio, modern HTTP, legacy HTTP): `slow` times out →
  `timeout` and the Server observed cancellation; cancel → `cancelled`; progress forwarded
  in order; prompter wait does not consume the timeout.

### F3-10 · MRTR loop and `InputRequestRouter` skeleton
- **Status**: [x] 2026-10-31
- **What**: `core/mrtr.py`: the `tools/call` loop of Flow C step 6 (round cap 8 →
  `protocol_error/input_rounds_exceeded`, state-only results retried after a short backoff
  counting as a round, `requestState` echoed opaquely, `HEADER_MISMATCH` → re-list once and
  resend once, stream break → `connection_lost` without re-issue, fulfilled rounds counted
  into the audit); `InputRequestRouter` dispatching per method to handler slots (Phase 4
  fills elicitation/sampling/roots; until then an undeclared method → `protocol_error/
  undeclared_input_request`); Consumer `ContextVar` for catalog reads and
  `InputRequestError` raised by reads (REQ-HSH-046).
- **REQ**: REQ-HSH-040, REQ-HSH-041, REQ-HSH-042, REQ-HSH-043, REQ-HSH-044, REQ-HSH-046,
  REQ-INV-015
- **Files**: `src/reflex_mcphost/core/mrtr.py`, `src/reflex_mcphost/core/invoker.py`,
  `src/reflex_mcphost/core/catalog.py`, `tests/integration/test_mrtr.py`
- **Depends on**: F3-08
- **Done**: tests green with the modern fixture: `ask_name` with no elicitation handler →
  `undeclared_input_request`; a fake router answering rounds counts them in the audit;
  9-round tool → `input_rounds_exceeded` with `input_rounds = 8`; `requestState` echoed
  byte-identical.

### F3-11 · Single effect path and benchmarks
- **Status**: [x] 2026-10-31
- **What**: activate the AST single-effect-path test (Art. 3); benchmarks REQ-NFR-001
  (consent evaluation at 10k rules) and REQ-NFR-002 (Host overhead vs. in-process Server)
  per PRD §7 definitions, budget-asserted on Linux CI, informational elsewhere; strict
  REQ-coverage for CNS and INV families in CI.
- **REQ**: REQ-NFR-001, REQ-NFR-002 · Art. 3
- **Files**: `tests/architecture/test_single_effect_path.py`, `tests/perf/{__init__,test_benchmarks}.py`,
  `.github/workflows/ci.yml`
- **Depends on**: F3-10
- **Done**: `uv run pytest` green; `check_req_coverage.py --strict --families
  REG,CON,CAT,CNS,INV` passes; HSH-040…044/046 cited.

---

## Phase 4 — Host-side handlers and OAuth

Common rules for Phase 4:
- **One human seam.** Every human interaction goes through `Prompter` (API §4.10):
  `elicit`, `ask_consent` (sampling), `review_sampling_response`. Core never opens a
  browser — URLs (url-mode elicitation, `AuthorizationFlow.authorization_url`) are handed to
  the UI/CLI. The same handler core serves the modern MRTR slot (`InputRequestRouter`) and
  the legacy SDK callback (REQ-HSH-045); the era adapters are thin.
- **Fail closed.** No handler/prompter/provider → the capability is not declared (modern:
  `undeclared_input_request`; legacy: the SDK answers method-not-found). A prompter
  exception or timeout → `deny` (consent, sampling review) / `cancel` (elicitation, API
  §4.10). Provider exception/timeout → refusal + audit `protocol_error`/`timeout`
  (REQ-NFR-020: never crashes the Host). Any OAuth failure → `AuthorizationError`, state
  stays `auth_required`, nothing redeemed.
- **Refusal channels** (spike S-1): legacy → `ErrorData` returned from the callback (the SDK
  sends the JSON-RPC error); modern → a client-side abort (`SamplingDeniedError` /
  `InputHandlerError`), never `ErrorData` in `inputResponses`; a refused elicitation is sent
  as `decline`/`cancel`. The modern REQ-HSH-012 channel is fixed by delta
  `changes/_archive/2026-10-phase4-prep` (item D3).
- **Secrets (Art. 5).** Never log or put in exception text: tokens, refresh tokens, codes,
  PKCE verifiers, `state`, client secrets, callback query strings. Every such value the Host
  sees is registered with the redaction registry (`core/secrets.py`) at first sight. SDK
  OAuth exception messages are never copied into `AuthorizationError` or `ServerStatus`
  (reason codes only); the `mcp.client.auth` logger gets the redaction filter plus the
  exception-text stripper of delta item D2.
- **Async (Art. 8).** Keyring and secret-file I/O only through `SecretStore` (its
  single-worker `anyio.to_thread` limiter); provider-factory import via `anyio.to_thread`;
  the loopback listener is `anyio` + `h11`, never `http.server`/threads.
- **Loopback only.** `LoopbackCallbackServer` binds `127.0.0.1` (never `0.0.0.0`, `::` or
  `localhost`), one flow, ≤ 5 min, then closes.
- **Dependencies.** No new distribution is downloaded. `h11` (0.16, already in `uv.lock` via
  `mcp → httpx2 → httpcore2`) becomes a *direct* dependency `h11>=0.16` because core imports
  it (DD-009) — owner sign-off recorded in the F4-07 PR. `httpx2` stays transitive through
  `mcp` (already used by F2-06). The fixture AS uses `starlette`/`uvicorn` from the test
  group only (Art. 1).
- **Deprecated features** (sampling, roots, logging, DCR — SEP-2577/Art. 2): module and
  public docstrings say *Deprecated in the pinned spec, implemented as compatibility*; calls
  to SDK methods decorated `@deprecated` (`set_logging_level`, `send_roots_list_changed`)
  suppress `MCPDeprecationWarning` locally (`warnings.catch_warnings`), never globally.
- **Both eras.** Every HSH task's Done includes the modern (MRTR) and legacy
  (server-initiated) path — the legacy integration of F4-01…F4-03 is proven in F4-05; every
  AUTH test runs against modern **and** legacy Streamable HTTP fixtures.
- **Parallel batches and shared files.** Batch M (F4-01…F4-05) and batch N (F4-06…F4-10)
  run in parallel. Batch M owns `core/handlers/*`, `core/mrtr.py`, `core/invoker.py`,
  `core/catalog.py`, `core/connection/supervisor.py`, `core/connection/inflight.py` and the
  tool definitions of the fixture Servers. Batch N owns `core/auth/*`,
  `core/connection/transports.py`, `core/errors.py` (`AuthorizationError`), the
  `_serve_http` sections of the fixture Servers and `tests/fixtures/servers/{auth_server,
  protected}.py`. Shared: `core/connection/manager.py` (M: `set_log_level`/`set_roots`; N:
  `authorize`/`sign_out` — disjoint methods) and **`core/host.py`, wired only at the end of
  each batch** (M in F4-05: capability plan, handler registration, `set_prompter` rebuild;
  N in F4-08: `TokenStore`/`OAuthController` per Server, transport factory). The batch
  that lands second rebases; F4-09b runs after both.
- **S-4 (F0-04) is not a Phase 4 gate.** F4-07/F4-08 are implemented and tested against
  the in-repo fixture AS (F4-09a, moved before F4-07). Validation of CIMD + loopback port
  variance against a *real* authorization server stays open as F0-04 `[!]`, tracked in
  Plan §2 (prerequisite) and gating release `0.1.0` (F8-07) per delta item D9; it does not
  block this phase's Done.

### F4-01 · Elicitation handler (form and url mode)
- **Status**: [x] 2026-10-31
- **What**: `core/handlers/elicitation.py`: `ElicitationHandler(prompter_of, server_name_of,
  *, timeout_s)` with one core `async handle(ctx: InputContext, params: ElicitRequestParams)
  -> ElicitResult | Refusal` and two adapters: `as_input_handler()` (router slot
  `elicitation/create`, modern) and `as_callback(attribute)` (SDK `ElicitationFnT`, legacy;
  wired in F4-05). Form mode: `elicitation_schema_problems` (F3-07) non-empty → refusal
  *without* calling the prompter (legacy `ErrorData(INVALID_PARAMS, "Unsupported
  elicitation schema")`; modern `InputHandlerError("elicitation/create",
  "invalid_elicitation_schema")` per delta D3). Otherwise build `ElicitationRequest` (new
  `RequestId`, `server_id`, `server_name`, `message`, `mode`, `requested_schema`,
  `attribution` — field added by delta D4) and await `prompter.elicit` inside
  `ctx.prompter_wait()` (DD-004) under `timeout_s` (= `consent_timeout_s`, delta D8).
  `accept` → validate `content` with the F3-07 validator (bounded, redacted messages);
  errors → call `elicit` again with `errors` (field → message) and send nothing
  (REQ-HSH-013); valid → `ElicitResult(action="accept", content=…)`. `decline`/`cancel` →
  that action, no content. Prompter exception/timeout/absent → `cancel`. Url mode: only
  `https://` / `http://` URLs (other schemes → refusal with reason
  `invalid_elicitation_url`); `ElicitationRequest.url` carries the full URL; the prompter
  (UI) opens it only on accept (delta D8); the result is the Consumer's action. Not
  consent-gated, not audited (not an effect, Art. 3).
- **REQ**: REQ-HSH-010, REQ-HSH-011, REQ-HSH-012, REQ-HSH-013, REQ-HSH-014 (handler side),
  REQ-HSH-015 (SHOULD) · API §4.10, DD-010
- **Files**: `src/reflex_mcphost/core/handlers/elicitation.py`, `src/reflex_mcphost/core/prompter.py`
  (`ElicitationRequest.attribution`), `tests/fixtures/servers/modern_server.py` (tools
  `elicit_form`, `elicit_bad_schema`, `elicit_url`), `tests/unit/core/handlers/{__init__,test_elicitation}.py`,
  `tests/integration/handlers/{__init__,test_elicitation}.py`
- **Depends on**: F3-10, F3-07
- **Done**: unit: bad schema never reaches the fake prompter (both adapters, right refusal
  type each); invalid content re-prompts with `errors` and the fixture records no response;
  prompter raising / hanging → `cancel`; url mode passes the full URL. Integration (modern,
  in-process + Streamable HTTP): `ask_name` accept/decline/cancel reach the Server as
  sent; `elicit_bad_schema` → `protocol_error`/`input_handler_failed` with
  `details.reason="invalid_elicitation_schema"`, prompter not called; the same schema
  inside a catalog read → `InputHandlerError` propagates (API §4.6). Legacy adapter unit-tested with a fake `ClientRequestContext`; legacy integration in F4-05.

### F4-02 · Sampling handler (*deprecated, compatibility*)
- **Status**: [x] 2026-10-31
- **What**: `core/handlers/sampling.py`: `SamplingHandler(consent, audit, provider_of,
  prompter_of, settings_of)` with core `handle(ctx, params: CreateMessageRequestParams)` and
  modern/legacy adapters as in F4-01. Flow (API §4.14): `ConsentPolicy.evaluate` with
  `operation=sampling`, server target, `ConsentRequest.sampling=SamplingReview(server_id,
  params)`, the originating Consumer (`ctx.consumer`; unattributed → `Consumer.human()` +
  `attribution="unattributed"`, REQ-HSH-047) → denied / no provider → `AuditLog.record`
  (`operation=sampling`, `fqn=None`, `denied`, `error_code="sampling_denied"`) then refusal
  (modern `SamplingDeniedError` → invocation `denied/sampling_denied`, reads
  `InputRequestError("sampling_denied")`; legacy `ErrorData(-1, "Sampling request rejected
  by host")`, delta D8). Allowed → `AuditLog.begin` (failure → refusal, no provider call,
  Art. 3) → `provider.create_message(answer.edited_sampling or params, server_id=…)` under
  `anyio.fail_after(server timeout_s or default_tool_timeout_s)` → if the Server's
  `review_sampling_responses`: `review_sampling_response` inside `ctx.prompter_wait()`
  (`approved=False`, exception, timeout or no prompter → denied/`sampling_denied`;
  `edited_result` replaces the result) → `AuditLog.finish(success)` → `CreateMessageResult`.
  Provider exception → finish `protocol_error`/`provider_error`, refusal
  (`InputHandlerError(…, "provider_error")` modern → invocation `protocol_error`/
  `input_handler_failed`; `ErrorData` legacy); timeout → finish `timeout`, same refusal
  with reason `provider_timeout`. No raw messages stored (Art. 5). Exactly one audit record per sampling attempt.
  `Host` resolves the provider: constructor argument, else `[sampling].provider`
  (`package.module:factory`, imported via `anyio.to_thread` in `start()`; failure → ERROR
  log with the exception type, sampling disabled, Host keeps running — REQ-NFR-020). No
  sampling sub-capabilities (`tools`) are declared (Art. 2).
- **REQ**: REQ-HSH-001, REQ-HSH-002, REQ-HSH-003, REQ-HSH-004 (SHOULD), REQ-HSH-005,
  REQ-HSH-006 (handler side), REQ-NFR-020 (provider part) · API §4.14, Art. 3
- **Files**: `src/reflex_mcphost/core/handlers/sampling.py`, `tests/fixtures/servers/modern_server.py`
  (tool `sample`), `tests/unit/core/handlers/test_sampling.py`,
  `tests/integration/handlers/test_sampling.py`
- **Depends on**: F3-10, F3-03, F3-06
- **Done**: unit with fakes: consent evaluated before any provider call; edited params
  reach the provider; review reject → `sampling_denied`; provider raising/hanging → refusal
  + one audit row (`protocol_error`/`timeout`); audit `begin` failing → provider never
  called. Integration (modern): `two_rounds` with an allow rule → result returned and audit
  rows for the tool call and the sampling; deny → tool outcome `denied/sampling_denied` and
  the fixture saw no retry; a stored `always` sampling rule for agent A does not apply to
  agent B; review flag on → `review_sampling_response` called once.

### F4-03 [P] · Roots handler (*deprecated, compatibility*)
- **Status**: [x] 2026-10-31
- **What**: `core/handlers/roots.py`: `RootsHandler(roots_of)` → `ListRootsResult` of the
  Server's effective `roots` (own definition or `[overrides.<id>] roots`; default none),
  each re-checked to be an absolute `file://` URI (anything else dropped + warning,
  REQ-HSH-022); modern slot `roots/list` and legacy `ListRootsFnT` adapter.
  `ConnectionManager.set_roots(server_id, roots)`: `ValueError` for any non-`file://`;
  persists through a new `ServerRegistry.set_roots` (own file: `[servers.<id>] roots` or
  `[overrides.<id>] roots` — third-party files untouched, Art. 6); a roots-only diff is
  applied in place (no reconnect, so the session that must receive the notification stays
  alive); legacy + `ready` → `send_roots_list_changed()`; modern → nothing (next request);
  a change between empty and non-empty requests a capability rebuild (hook consumed by
  F4-05). No consent, no audit (not an effect).
- **REQ**: REQ-HSH-020 (SHOULD), REQ-HSH-021 (SHOULD), REQ-HSH-022 · API §4.5 `set_roots`
- **Files**: `src/reflex_mcphost/core/handlers/roots.py`, `src/reflex_mcphost/core/registry.py`
  (`set_roots`), `src/reflex_mcphost/core/connection/manager.py` (`set_roots`),
  `tests/fixtures/servers/modern_server.py` (tool `list_roots`),
  `tests/unit/core/handlers/test_roots.py`, `tests/integration/handlers/test_roots.py`
- **Depends on**: F3-10, F2-04
- **Done**: unit: only configured `file://` roots are returned, `set_roots(["https://x"])`
  raises; registry test: roots written to the own file, third-party files byte-identical,
  no reconnect diff. Integration (modern): `list_roots` returns the configured roots and
  none by default. Legacy `list_changed` delivery is proven in F4-05.

### F4-04 [P] · Logging (*deprecated, compatibility*)
- **Status**: [x] 2026-10-31
- **What**: `core/handlers/logging.py`: per-Server `LoggingFnT` appending
  `notifications/message` to the Server's `LogRing` (F2-06) with level, logger, timestamp
  and `data` rendered as text (JSON for non-strings, capped like stderr lines; redaction by
  the ring) → `server.log` event. `ConnectionManager.set_log_level(server_id, level)`:
  `ServerUnavailableError` unless `ready`; `CapabilityNotSupportedError` unless the Server
  declared `logging`; legacy → `session.set_logging_level(level)`; modern → stores the level
  (memory, DM §8) and requests a capability rebuild so the new Client carries
  `log_level=level` (`io.modelcontextprotocol/logLevel` in every request's `_meta`). Connect
  time: definition `log_level` (or the stored runtime level) → legacy `logging/setLevel`
  after `initialize` when declared; modern `Client(log_level=…)`; none by default
  (REQ-HSH-031).
- **REQ**: REQ-HSH-030 (SHOULD), REQ-HSH-031 (SHOULD) · API §4.5 `logs`/`set_log_level`
- **Files**: `src/reflex_mcphost/core/handlers/logging.py`, `src/reflex_mcphost/core/connection/manager.py`
  (`set_log_level`), `tests/fixtures/servers/modern_server.py` (tool `log`),
  `tests/unit/core/handlers/test_logging.py`, `tests/integration/handlers/test_logging.py`
- **Depends on**: F2-06, F2-07
- **Done**: unit: entries keep level/logger/timestamp, the ring stays bounded at
  `log_buffer_size`, a registered secret in `data` is masked. Integration (modern): `log`
  emits nothing without a level; after `set_log_level("debug")` the next call's
  notification lands in `logs()` and a `server.log` event; a Server without `logging` →
  `CapabilityNotSupportedError`. Legacy `setLevel` proven in F4-05.

### F4-05 · Capability declaration, legacy server-initiated requests and rebuilds
- **Status**: [x] 2026-10-31
- **What**: (1) `core/handlers/capabilities.py`: `CapabilityPlan` per Server —
  `elicitation` iff the prompter `supports_elicitation`, `sampling` iff a provider is set,
  `roots` iff the effective roots are non-empty (delta D8), plus `log_level`; the supervisor
  builds `Client(...)` callbacks from it (`elicitation_callback`, `sampling_callback`,
  `list_roots_callback` only when declared — registering is declaring, DD-003;
  `logging_callback` always, it declares nothing); `InputRequestRouter.fulfil` gates per
  Server on the same plan (undeclared for *that* Server → `undeclared_input_request`).
  (2) `core/connection/inflight.py`: `InFlightRegistry` on each legacy supervisor; the
  Invoker and catalog reads register `(invocation_id | read, consumer, deadline)` around
  every legacy leg; legacy callbacks attribute to the single entry, else unattributed
  (REQ-HSH-045/047). Attributed waits pause that invocation's `PausableDeadline` (DD-004
  legacy, deferred from F3-09); unattributed waits are not excluded (documented limit). A
  legacy sampling refusal is flagged on the entry and the Invoker overrides the final
  outcome to `denied/sampling_denied` whatever the `CallToolResult` (TD Flow C note).
  (3) Rebuilds: `Host.set_prompter`, `set_roots` (empty ↔ non-empty) and modern
  `set_log_level` recompute plans; a modern `ready` Server whose plan changed is rebuilt with
  `mode=<pinned version>, prior_discover=<current>` through the internal-rebuild path of
  REQ-CON-015 (state stays `ready`, no `server.state_changed`, no network negotiation),
  started when the lease is idle (in-flight calls are never cancelled; new `lease()` callers
  wait for the new Client); legacy Servers keep their capabilities until `reconnect()`.
  (4) Host wiring (end of batch M): handlers registered in `host.input_router`, plans and
  callbacks passed through `_make_supervisor`, legacy roots `list_changed` and `setLevel`
  hooks connected.
- **REQ**: REQ-HSH-045, REQ-HSH-047 (legacy path), REQ-CON-003, REQ-HSH-006, REQ-HSH-014,
  REQ-HSH-021, REQ-HSH-031 (legacy) · DD-003, DD-004
- **Files**: `src/reflex_mcphost/core/handlers/capabilities.py`, `src/reflex_mcphost/core/connection/{inflight,supervisor}.py`,
  `src/reflex_mcphost/core/mrtr.py`, `src/reflex_mcphost/core/invoker.py`, `src/reflex_mcphost/core/catalog.py`,
  `src/reflex_mcphost/core/host.py`, `tests/fixtures/servers/legacy_server.py` (tools
  `list_roots`, `log`, `elicit_bad_schema`; `elicit_url` only if `mcp` 1.30 supports url
  mode), `tests/integration/handlers/test_legacy_requests.py`,
  `tests/integration/connection/test_capabilities.py`, `tests/interop/test_handlers_era_matrix.py`
- **Depends on**: F4-01, F4-02, F4-03, F4-04
- **Done**: `client_caps` on both eras reports exactly the enabled set for {no prompter,
  eliciting prompter, + provider, + roots}; modern `set_prompter(None)` → next request
  declares no elicitation, no reconnect, no state event; legacy unchanged until
  `reconnect()`. Legacy (stdio and HTTP): `ask_name`/`sample` with one in-flight call →
  attributed (audit `exact`); two concurrent calls → unattributed (stored allow ignored,
  prompter offered once-only, no prompter → denied, audit `unattributed`); legacy sampling
  deny → tool outcome `denied/sampling_denied`; attributed prompter wait not counted
  against the tool timeout; `elicit_bad_schema` → JSON-RPC error and no prompt;
  `set_roots` → `notifications/roots/list_changed` received; `set_log_level` →
  `logging/setLevel` received. Era-matrix interop test covers every handler on both eras.

### F4-10 [P] · CIMD document and GitHub Pages job
- **Status**: [x] 2026-10-31 (owner: enable GitHub Pages, source "GitHub Actions")
- **What**: `docs/oauth/client.json` (CIMD): `client_id` = the default
  `oauth_client_metadata_url` (`https://ecrespo.github.io/reflex-mcphost/oauth/client.json`),
  `client_name`, `client_uri`, `redirect_uris: ["http://127.0.0.1/callback"]` only,
  `grant_types: [authorization_code, refresh_token]`, `response_types: [code]`,
  `token_endpoint_auth_method: "none"`, `application_type: "native"`.
  `.github/workflows/pages.yml` publishes `docs/` (path `oauth/client.json`) on push to
  `main` (`upload-pages-artifact` + `deploy-pages`, least-privilege permissions). Owner
  action: enable Pages (source "GitHub Actions"); the live URL is checked at release (F8-07).
- **REQ**: REQ-AUTH-012
- **Files**: `docs/oauth/client.json`, `.github/workflows/pages.yml`, `tests/unit/test_cimd_document.py`
- **Depends on**: F1-04
- **Done**: test green: the document parses as SDK `OAuthClientMetadata`, `client_id`
  equals `DEFAULT_OAUTH_CLIENT_METADATA_URL` and passes `is_valid_client_metadata_url`,
  every redirect URI is portless `http://127.0.0.1/…`, no secret-like key present.

### F4-06 [P] · `TokenStore` (issuer-keyed, on `SecretStore`)
- **Status**: [x] 2026-10-31
- **What**: `core/auth/token_store.py`: `TokenStore(secrets: SecretStore, server_id, *,
  oauth: OAuthSettings | None, cimd_url: str)` implementing the SDK `TokenStorage`
  protocol (`get_tokens`, `set_tokens`, `get_client_info`, `set_client_info`) plus
  `clear()` and `issuer`. One entry `oauth:{server_id}` (kind `oauth`) with the DM §3 layout
  (`v`, `issuer` = `client_info.issuer`, `tokens`, `client_info`, `client_id_source`,
  `updated_at_us`). A configured `client_id` (+ optional `client_secret`) is returned by
  `get_client_info` before anything stored (`client_id_source="configured"`,
  `token_endpoint_auth_method` `client_secret_post` with a secret, else `none`);
  `client_id == cimd_url` → `cimd`; otherwise `dcr`. `set_client_info` with an issuer
  different from the stored one discards stored tokens too (REQ-AUTH-011; the SDK's
  SEP-2352 binding does the in-memory part, delta D7). A corrupt/unknown-version entry reads
  as absent (+ warning without content) → re-authorization. Token, refresh-token and
  client-secret values are registered for redaction when read or written; `repr()` shows
  none. All I/O through `SecretStore` (keyring first, `0600` file fallback, Art. 8).
- **REQ**: REQ-AUTH-004 (storage), REQ-AUTH-011, REQ-NFR-012 (token file) · DD-008, DM §3
- **Files**: `src/reflex_mcphost/core/auth/token_store.py`, `tests/unit/core/auth/{__init__,test_token_store}.py`
- **Depends on**: F2-05a
- **Done**: tests green with a fake keyring and with the file fallback: round-trip of
  `OAuthToken`/`OAuthClientInformationFull`; `secrets.json` mode `0600` (POSIX) after an
  oauth write; issuer change drops tokens and client info; configured client wins over
  stored; corrupt entry → `None`; `clear()` removes the entry and its `secret_index` row;
  no seeded token in `repr`, logs or the SQLite file.

### F4-09a · Fixture OAuth authorization server and protected fixtures
- **Status**: [x] 2026-10-31
- **What**: `tests/fixtures/servers/auth_server.py` (tests only, Art. 1): a small Starlette
  app under uvicorn on `127.0.0.1:<ephemeral>` with toggles `cimd`, `dcr`,
  `iss_supported`, `iss_override` (mix-up injection), `issuer` (switchable second
  issuer), `access_ttl_s`, `reject_access_tokens`, `port_variance` (RFC 8252 §7.3):
  RFC 8414 metadata, `/authorize` (auto-approves, 302 to the redirect URI with `code`,
  `state`, `iss`), `/token` (authorization_code with PKCE S256 verification and `resource`
  check; refresh_token), `/register` (RFC 7591), CIMD resolution of the default
  `oauth_client_metadata_url` from the repo's `docs/oauth/client.json` (no network). A
  request journal records endpoints, client ids and parameter *presence* for assertions.
  `tests/fixtures/servers/protected.py`: bearer middleware + RFC 9728 protected-resource
  metadata + `401 WWW-Authenticate: Bearer resource_metadata=…` around the modern HTTP
  fixture; the legacy HTTP fixture's `_serve_http` gains the same check via a
  `--bearer-check URL` option. Pytest fixtures `oauth_as`, `protected_modern_http`,
  `protected_legacy_http` and `browser` (an `httpx2` client that follows `/authorize` to
  the loopback callback).
- **REQ**: — (test infrastructure for REQ-AUTH-001…011) · Art. 1, Art. 7
- **Files**: `tests/fixtures/servers/{auth_server,protected}.py`, `tests/fixtures/servers/legacy_server.py`
  (`_serve_http` only), `tests/integration/auth/{__init__,conftest,test_fixture_as}.py`
- **Depends on**: F1-10, F4-10
- **Done**: self-tests green: wrong PKCE verifier rejected; `iss` emitted when enabled;
  CIMD client accepted only with a loopback redirect (port variance on/off); both protected
  fixtures answer 401 with `resource_metadata` without a token and 200 with one; the wheel
  check still finds no fixture in the wheel.

### F4-07 · `LoopbackCallbackServer` and the `authorize()` flow
- **Status**: [x] 2026-10-31
- **What**: (1) `core/auth/callback_server.py`: `LoopbackCallbackServer(port=0)` async
  context manager on `anyio.create_tcp_listener(local_host="127.0.0.1")` + `h11`: serves
  only `GET /callback` (other paths 404, other methods 405), caps request head at 8 KiB and
  per-connection reads at 10 s, accepts one callback, answers a static HTML page ("You can
  close this tab" / "Authorization failed", no parameter echo, `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`), never logs the target or query, exposes
  `redirect_uri` and `await result() -> CallbackResult(code, state, iss, error)`.
  (2) `core/auth/oauth.py`: `OAuthController` per HTTP Server without static headers
  (REQ-AUTH-009, delta D6): owns the SDK `OAuthClientProvider(server_url,
  OAuthClientMetadata(redirect_uris=[…], scope, grant_types=[authorization_code,
  refresh_token], token_endpoint_auth_method="none", application_type="native"),
  storage=TokenStore, redirect_handler, callback_handler, client_metadata_url=…)` — the SDK
  does discovery (RFC 9728 → RFC 8414), PKCE S256, `state`, `resource` and code exchange
  (Art. 2). **Parked** (default): `redirect_handler` raises `AuthorizationDeferred` → the
  attempt fails, the existing 401 classification sets `auth_required` (REQ-AUTH-001); no
  browser, no listener. (3) `core/auth/flow.py` `AuthorizationFlow` (`authorization_url`,
  `wait()`, `cancel()`) and `ConnectionManager.authorize(server_id)`: not `auth_required` →
  `AuthorizationError(reason="not_auth_required")`; a running flow is returned as is;
  otherwise bind the listener, arm the controller (redirect URI with the bound port), kick
  the supervisor (state `connecting` per delta D1), return once the SDK called
  `redirect_handler`. `callback_handler` awaits the listener within the 5-min deadline and
  checks `state` (constant time, against the `state` of the authorization URL) and `iss`
  (SDK `validate_authorization_response_iss` on the discovered metadata) **before**
  handing the code to the SDK; mismatch, AS `error`, timeout or `cancel()` raise
  `AuthorizationAborted(reason)` → nothing redeemed, verifier dropped with the SDK frame,
  listener closed, state back to `auth_required`, `wait()` raises `AuthorizationError` with
  a reason code from delta D5 (never SDK message text). (4) Redaction: `code`, `state`,
  callback query and tokens registered; filter + exception-text stripper on the
  `mcp.client.auth` logger (delta D2). (5) `transports.py`: the `httpx2` client gets
  `auth=controller.provider`; a `401` while `ready` closes the observed transport so the
  supervisor reconnects and lands in `auth_required` (no supervisor edit). (6) Contract
  test pins the SDK auth surface used.
- **REQ**: REQ-AUTH-001, REQ-AUTH-002, REQ-AUTH-006, REQ-AUTH-008 (flow part),
  REQ-AUTH-010 · API §2.2, §4.5, DD-009
- **Files**: `src/reflex_mcphost/core/auth/{callback_server,oauth,flow}.py`,
  `src/reflex_mcphost/core/connection/{manager,transports}.py`, `src/reflex_mcphost/core/errors.py`,
  `pyproject.toml` (`h11>=0.16` direct), `tests/unit/core/auth/test_callback_server.py`,
  `tests/integration/auth/test_authorize.py`, `tests/contract/test_mcp_sdk_auth_surface.py`
- **Depends on**: F4-06, F4-09a, F2-07
- **Done**: unit: listener bound to `127.0.0.1` only, closed after one callback or the
  deadline, 404/405/oversize handled, caplog has no query string. Integration (modern and
  legacy protected fixtures): 401 → `auth_required` with no listener open; `authorize()` URL
  carries `code_challenge_method=S256`, `state` and `resource`; `browser` completes → tokens
  stored → `ready`; `state` mismatch → `state_mismatch`, the AS journal has no token
  request; `iss` wrong / missing-while-advertised → `iss_mismatch`, no token request;
  short injected deadline → `timeout`; `cancel()` → `cancelled`; every failure leaves
  `auth_required`. Contract test pins `OAuthClientProvider` kwargs, `TokenStorage`,
  `AuthorizationCodeResult(code, state, iss)`, `OAuthClientMetadata.application_type`
  default, `validate_authorization_response_iss` and the `OAuthContext` attributes read.

### F4-08 · Registration order, refresh-once and sign-out
- **Status**: [x] 2026-10-31
- **What**: Registration (inside the SDK, configured by us, API §2.2): configured
  `client_id` (`TokenStore`) → CIMD when the AS advertises
  `client_id_metadata_document_supported` (`HostConfig.oauth_client_metadata_url`) → DCR
  (*deprecated fallback*, INFO log saying so, `application_type="native"`) → otherwise
  (`OAuthRegistrationError`) stay `auth_required` with reason `client_id_required` and a
  message naming `[servers.<id>.oauth] client_id` in `mcphost.toml` (REQ-AUTH-003). A DCR
  client re-binds its registered loopback port when free, else an ephemeral one (port
  variance; real-AS behaviour is S-4). Refresh-once (REQ-AUTH-005): expiry → the SDK's
  proactive refresh; a *rejected* unexpired token → `OAuthController` replays the request
  once with the in-memory token marked expired so the SDK refresh runs, then retries; a
  second rejection or refresh failure → `auth_required` (delta D1 item c). Issuer change →
  credentials discarded and re-registered (REQ-AUTH-011). `ConnectionManager.sign_out`:
  cancels a running flow, `TokenStore.clear()`, disconnects → `disconnected`; idempotent;
  `UnknownServerError` for unknown ids (REQ-AUTH-007). Host wiring (end of batch N): one
  `TokenStore` + `OAuthController` per HTTP Server built in `_make_supervisor`, passed to
  the transport factory and to `ConnectionManager`.
- **REQ**: REQ-AUTH-003, REQ-AUTH-004, REQ-AUTH-005, REQ-AUTH-007, REQ-AUTH-011
- **Files**: `src/reflex_mcphost/core/auth/oauth.py`, `src/reflex_mcphost/core/connection/manager.py`,
  `src/reflex_mcphost/core/host.py`, `tests/integration/auth/{test_registration,test_refresh,test_sign_out}.py`
- **Depends on**: F4-07
- **Done**: tests green on modern and legacy protected fixtures: CIMD AS → `client_id` is
  the CIMD URL, no `/register` call, source `cimd`; non-CIMD AS → DCR with
  `application_type=native`, source `dcr`; neither → `auth_required`/`client_id_required`
  with the config hint; configured id → no CIMD/DCR; issuer switch → re-registration and
  the old `client_id` never reaches the new issuer; expired token → one refresh, no prompt;
  rejected token → exactly one refresh then success; refresh rejected → `auth_required`;
  `sign_out` → entry and `secret_index` row gone (keyring fake and file), state
  `disconnected`, next `connect` → `auth_required`.

### F4-09b · Auth interop and full secret-seeded redaction test
- **Status**: [x] 2026-10-31
- **What**: `tests/interop/test_oauth.py`: matrix {modern HTTP, legacy HTTP} × {CIMD, DCR,
  configured} full flows plus mix-up (`iss`), issuer switch and refresh, against the
  fixture AS. `tests/security/test_redaction.py` (Plan Phase 4 Done): seeds env values,
  header values, access/refresh tokens, authorization codes, PKCE verifiers (from the AS
  journal), `state`, client secrets, secret-like tool arguments and sampling text; drives
  a session through trust, OAuth, tool calls, elicitation, sampling and failures; asserts
  none appears in captured logs (all loggers incl. `mcp.*`, `httpx2`), audit rows, the
  SQLite file and WAL bytes, exception messages / `repr` (`AuthorizationError`,
  `ServerStatus.reason`, `InvocationResult.error`), the log ring or events. CI: strict REQ
  gate extended to `HSH,AUTH`; core coverage ≥ 90 %.
- **REQ**: REQ-AUTH-008, REQ-AUTH-009 (completes F2-06), REQ-NFR-010, REQ-NFR-012 · Art. 5, Art. 7
- **Files**: `tests/interop/test_oauth.py`, `tests/security/{__init__,test_redaction}.py`,
  `.github/workflows/ci.yml`
- **Depends on**: F4-08, F4-05
- **Done**: `uv run pytest` green; `check_req_coverage.py --strict --families
  REG,CON,CAT,CNS,INV,HSH,AUTH` passes; no seeded secret found anywhere; a static-header
  Server never contacts the AS (REQ-AUTH-009).

---

## Phase 5 — Toolset, adapters and 0.1.0a1

Common rules for Phase 5:
- **Delta `changes/_archive/2026-10-phase5-prep` is approved and folded (2026-10-31).**
  F5-01 follows D1 (where the `Toolset` lives) and D2 (alias collision rule, API §4.12);
  F5-02…F5-04 follow D3 (agent text per outcome, adapter error conversion); F5-06a/F5-06b
  follow D4 (`0.1.0a1` packaging and release workflow, TD §9.1). Batch O and F5-06a are
  unblocked; F5-06b stays `[!]` (owner action). The D4 `pyproject.toml` edit is code and
  lands in F5-06a.
- **Layering (Art. 4, DD-018, delta D1).** The framework-neutral implementation (aliases,
  filters, `Toolset`, `ToolSpec`, `ToolCallOutcome`) lives in `reflex_mcphost.core.toolset`
  so that `Host.toolset()` (API §4.1, in `core.host`) needs no upward import — import-linter
  rejects even a function-level `core → toolset` import (checked 2026-10-31).
  `reflex_mcphost.toolset` is the public module and only re-exports. Adapters import
  `reflex_mcphost.toolset` only, never `reflex_mcphost.core.*` (new forbidden contract), and
  each adapter imports only its own framework (existing independence contract).
- **Single effect path (Art. 3).** `Toolset.call` → `Host.invoker.call(fqn, arguments,
  consumer=toolset.consumer)`, nothing else: no `call_tool`, no `ClientLease`, no session
  access in `core.toolset` or `adapters.*` (the existing AST test covers every module;
  F5-05 adds a non-vacuous check that the toolset reaches `Invoker.call`). Consent, schema
  validation, deadlines, MRTR and audit are the Invoker's; the Toolset adds none of them.
  The PydanticAI adapter *defines* `AbstractToolset.call_tool` (framework hook) but never
  calls a `call_tool`.
- **Never crash the agent (REQ-TLS-005, delta D3).** `Toolset.call` returns a
  `ToolCallOutcome` for every `InvocationOutcome`; it raises only `KeyError` (unknown alias),
  the misuse exceptions of the Host (`RuntimeError` before `start()`, `HostClosedError`) and
  the awaiting task's cancellation (anyio semantics, API §4.8). Adapters convert an error
  outcome *and* a vanished alias into the framework's tool-error channel (S-6):
  PydanticAI `ToolFailed(text)`; LangChain `ToolException(text)` with
  `handle_tool_error=True`. LangGraph's `ToolNode` re-raises any other exception and fails
  the graph, so nothing else may escape an adapter tool.
- **Reuse the frameworks (Art. 2).** Base classes pinned by spike S-6 and re-checked against
  `.venv` on 2026-10-31 (`pydantic-ai-slim` 2.64.0, `langchain-core` 1.7.3, `langgraph`
  1.2.19, `langgraph-prebuilt` 1.1.1 — same as `uv.lock`):
  PydanticAI — subclass `pydantic_ai.toolsets.AbstractToolset[Any]` (abstract `id`,
  `get_tools(ctx)`, `call_tool(name, tool_args, ctx, tool)`), tools as
  `pydantic_ai.toolsets.ToolsetTool` over `pydantic_ai.tools.ToolDefinition(
  parameters_json_schema=…)` with a permissive `pydantic_core.SchemaValidator` (the Invoker
  validates, as `MCPToolset` does). LangChain — subclass `langchain_core.tools.BaseTool`
  (abstract `_run`) with a dict `args_schema`; **not** `StructuredTool` (drops/overrides
  arguments named `config`, `run_manager`, `self`). No JSON-schema validation, retry or
  error logic is re-implemented in the adapters.
- **Dependencies.** No new distribution. The extras stay as locked: `pydantic-ai` →
  `pydantic-ai-slim>=2.64,<3`; `langgraph` → `langchain-core>=1.7,<2`; `langgraph>=1.2,<2`
  stays in the `test` group (examples and `ToolNode` tests only; the adapter imports only
  `langchain_core`). CI already runs `uv sync --all-extras --all-groups`, so both adapters
  are tested on the 3-OS × 2-Python matrix (REQ-NFR-030). Tests set
  `PYDANTIC_AI_NO_BANNER=1` in `tests/conftest.py` before any import.
- **No real LLM.** Agent tests use PydanticAI `TestModel`/`FunctionModel` and a LangChain
  `GenericFakeChatModel` subclass that accepts `bind_tools`; no API key, no network.
- **Secrets (Art. 5).** Agent-facing text is built from `InvocationResult` content (the
  Server's own output) and `InvocationError.message` (already bounded and redacted, never
  argument values); the Toolset and adapters log at most alias, FQN and outcome, never
  arguments. The F4-09b redaction test is extended to a Toolset call with a seeded
  secret-like argument.
- **Both eras.** Toolset and adapter integration tests run against the modern and legacy
  stdio fixtures; the Toolset sees only the catalog and the Invoker, so transports are not
  multiplied.
- **Release is two tasks (outward-facing, hard to reverse).** F5-06a (workflow and checks)
  is implementable and verified without publishing. F5-06b (tag `v0.1.0a1` + PyPI upload)
  is an **owner action**, blocked until the owner configures PyPI Trusted Publishing and the
  `pypi` GitHub environment and explicitly approves the release. A PyPI file name can never
  be re-uploaded: a bad `0.1.0a1` is yanked and fixed as `0.1.0a2`.
- **Phase 4 open items do not block `0.1.0a1`.** All Phase 4 tasks are `[x]`. S-4 (F0-04,
  real-AS CIMD) gates only F8-07 / release `0.1.0` (Plan §4 spike gates, R-9, delta
  `2026-10-phase4-prep` D9); GitHub Pages enablement is needed for the CIMD URL to be live
  but the URL is frozen only from `0.1.0` on (TD §9.1). Both go into the `0.1.0a1` release
  notes (alpha: CIMD may fall back to DCR or a configured `client_id`).
- **Batch ownership.** Batch O owns `core/toolset/*`, `toolset/*`, `adapters/*`,
  `core/host.py` (`toolset()` only), `tests/{unit,integration}/{toolset,adapters}/*` and
  `tests/contract/test_adapter_surfaces.py`. Batch P owns `.github/workflows/release.yml`,
  `scripts/check_wheel.py`, `tests/scripts/test_check_wheel.py` and the release metadata in
  `pyproject.toml`; `pyproject.toml` import-linter contracts are batch O (F5-05).

### F5-01 · `Toolset` + `ToolSpec`, alias algorithm, live catalog reads
- **Status**: [x] 2026-10-31 (follows folded delta D1, D2) — `Toolset(invoker_of=…)` takes a callable so the Host's lifecycle guards apply on every `call()`
- **What**: (1) `core/toolset/aliases.py`: `sanitize(s)` (`[^A-Za-z0-9_-]` → `_`) and pure
  `assign_aliases(fqns: Iterable[Fqn]) -> dict[Fqn, str]` implementing API §4.12 plus the
  delta D2 rule: candidate `sanitize(server_id) + "__" + sanitize(name)`; hashed form
  `candidate[:55] + "_" + sha256(str(fqn)).hexdigest()[:8]` when sanitize changed anything,
  `len > 64` or candidates collide; a plain candidate equal to any hashed alias is hashed
  too; a residual equality between hashed aliases is broken by the D2 ordinal suffix.
  Deterministic for a given set (order-independent); the collision domain is the
  Toolset's own listing (after readiness, visibility and filters). (2)
  `core/toolset/toolset.py`: frozen `ToolSpec` and `ToolCallOutcome` (API §4.12) and
  `Toolset(*, catalog, status_of, invoker_of, consumer, include, exclude)`; sync `list()`
  reads `catalog.tools()` on every call and keeps entries whose Server is `ready`
  (`connections.status(id).state`) and that are not `stale`; `description` = `"[server_id]
  " + (description or title or name)`; `input_schema`/`output_schema` deep-copied per call
  so a framework that mutates a schema cannot corrupt the catalog (values exactly as
  received, API §3.2). `resolve(alias)` resolves against a fresh listing (`KeyError` if
  absent). `async call(alias, arguments)` → `resolve` → `invoker.call(fqn, arguments,
  consumer=self.consumer)` → `ToolCallOutcome` (outcome mapping completed in F5-02).
  (3) `Host.toolset(consumer, *, include=("*",), exclude=())` in `core/host.py`, guarded
  like the other members (`RuntimeError` before `start()`, `HostClosedError` after
  `aclose()`); one Toolset per call, no caching. (4) `reflex_mcphost/toolset/__init__.py`
  re-exports `Toolset`, `ToolSpec`, `ToolCallOutcome` (`__all__`).
- **REQ**: REQ-TLS-001, REQ-TLS-002, REQ-TLS-004 · API §4.1 `toolset`, §4.12
- **Files**: `src/reflex_mcphost/core/toolset/{__init__,aliases,toolset}.py`,
  `src/reflex_mcphost/toolset/__init__.py`, `src/reflex_mcphost/core/host.py`,
  `tests/unit/toolset/{__init__,test_aliases,test_toolset}.py`,
  `tests/integration/toolset/{__init__,test_toolset}.py`
- **Depends on**: F3-08 (done)
- **Done**: `test_aliases.py` (Hypothesis, ≥ 500 examples): for arbitrary sets of
  `(ServerId-valid id, arbitrary non-empty Unicode name ≤ 200 chars)` every alias matches
  `^[A-Za-z0-9_-]{1,64}$`, aliases are pairwise distinct, the map is identical for any
  permutation of the input, and `resolve(alias) == fqn`; examples: `modern::echo` →
  `modern__echo`; `a__b::c` and `a::b__c` both hashed and distinct; a name with `.`/`::`/
  non-ASCII is hashed; a 64-char candidate stays plain, 65 is hashed; the D2 adversarial
  case (a plain candidate spelled like another tool's hashed alias) yields distinct
  aliases. `test_toolset.py` (fake catalog, statuses and Invoker): non-`ready` and `stale`
  tools excluded; description format; a catalog change is visible on the next `list()`
  (REQ-TLS-004); mutating a returned `input_schema` leaves the catalog entry unchanged;
  `call` awaits the fake `Invoker.call` exactly once with the resolved FQN and the
  Toolset's `Consumer`; unknown alias → `KeyError`; `Host.toolset` before `start()` →
  `RuntimeError`, after `aclose()` → `HostClosedError`. Integration (real `Host`, modern
  and legacy stdio fixtures): `list()` contains `<id>__echo`; calling fixture `add_tool`
  via the Invoker → after `catalog.changed` the next `list()` contains the new tool;
  `call("<id>__echo", …)` with an allow rule for `agent:helper` → `is_error=False`, the
  echoed text, exactly one audit record with consumer `agent:helper`; the same call by a
  Toolset for `agent:other` without a rule and no prompter → denied.

### F5-02 · Filters, `model`-visibility exclusion, denial returned as tool error
- **Status**: [x] 2026-10-31 (follows folded delta D3) — plus the F4-09b redaction test extended to Toolset calls
- **What**: (1) `core/toolset/filters.py`: `selected(fqn, include, exclude) -> bool`; an
  entry containing `::` is a case-sensitive glob on `str(fqn)` (`fnmatch.fnmatchcase`:
  `github::*`, `*::search`); an entry without `::` is a glob on `server_id` (so the
  default `"*"` selects everything and `"github"` selects exactly Server `github`, not
  `github2`). Exclude wins. Applied in `list()` before alias assignment. (2) Tools whose
  `visibility` lacks `"model"` are excluded from every listing, so they are never
  resolvable through any Toolset (REQ-APP-008, toolset part); because `call()` resolves
  against a fresh listing, a tool that became app-only, filtered or not-ready since an
  adapter snapshot is not callable. (3) Outcome → agent text per delta D3: `success` →
  `result.text`, or the JSON of `structured_content` when there is no text block;
  `tool_error` → `result.text` (the Server's error content) or `"The tool reported an
  error"`; `denied` → `"Permission denied by the host: " + error.message` (consent reason
  or `sampling was denied`, never argument values); every other outcome →
  `"Tool call failed (<outcome>): " + error.message`; `is_error = outcome != success`;
  `structured_content` and `result` carried through.
- **REQ**: REQ-TLS-003, REQ-TLS-005, REQ-APP-008 (toolset part) · API §4.12
- **Files**: `src/reflex_mcphost/core/toolset/{filters,toolset}.py`,
  `tests/unit/toolset/{test_filters,test_outcomes}.py`,
  `tests/integration/toolset/test_consent.py`
- **Depends on**: F5-01
- **Done**: `test_filters.py` table: `("*",)` all; `["github"]` only `github::*` (not
  `github2`); `["github::*"]`; `["*::search"]`; include + exclude overlap → excluded;
  globs are case-sensitive. `test_outcomes.py` parametrized over all 11
  `InvocationOutcome` values: `is_error` iff not `success`; exact denial prefix; a seeded
  secret-like argument value never appears in any text. Visibility: the modern fixture's
  app-only tool (`_meta.ui.visibility: ["app"]`) is absent from `list()` and its candidate
  alias raises `KeyError` on `call`. `test_consent.py` (modern and legacy stdio): headless
  Host, no rule → `ToolCallOutcome(is_error=True)` with the denial text, no exception, one
  audit record `denied`; fake prompter answering deny → same; allow → success; invalid
  arguments → `is_error=True`, prompter never called; a tool removed after `list()` →
  `KeyError` (adapters convert it, F5-03/F5-04).

### F5-03 [P] · `adapters.pydantic_ai`
- **Status**: [x] 2026-10-31 (follows folded delta D3)
- **What**: `adapters/pydantic_ai.py`: import guard — `ImportError("reflex_mcphost.adapters.
  pydantic_ai needs the 'pydantic-ai' extra: pip install 'reflex-mcphost[pydantic-ai]'")`
  chained from the original. `as_pydantic_ai_toolset(toolset: Toolset) -> AbstractToolset`
  returns `HostToolset(AbstractToolset[Any])`: `id` property → `None` (API signature takes
  no id; durable execution is out of scope); `get_tools(ctx)` builds `{spec.name:
  ToolsetTool(toolset=self, tool_def=ToolDefinition(name=spec.name,
  description=spec.description, parameters_json_schema=spec.input_schema,
  metadata={"fqn": str(spec.fqn)}), max_retries=1, args_validator=<permissive dict
  validator>)}` from `toolset.list()` on **every** call (PydanticAI calls it once per run
  step → REQ-TLS-004 holds mid-run, S-6); `call_tool(name, tool_args, ctx, tool)` →
  `await toolset.call(name, tool_args)`; `is_error` → `raise ToolFailed(outcome.text)`;
  `KeyError` → `raise ToolFailed("Tool is no longer available: <name>")`; success →
  `outcome.text`. `__all__ = ["as_pydantic_ai_toolset"]`.
- **REQ**: REQ-TLS-006, REQ-TLS-005 (PydanticAI channel), REQ-TLS-004 (per-step re-listing)
  · API §4.13
- **Files**: `src/reflex_mcphost/adapters/pydantic_ai.py`,
  `tests/unit/adapters/{__init__,test_pydantic_ai}.py`,
  `tests/integration/adapters/{__init__,test_pydantic_ai_agent}.py`,
  `tests/contract/test_adapter_surfaces.py` (S-6 #7–9), `tests/conftest.py` (banner env)
- **Depends on**: F5-02
- **Done**: contract S-6 #7–9 green: with `FunctionModel`, a denial yields
  `ToolReturnPart(content=<denial text>, outcome="failed")` and the run completes;
  `get_tools` runs once per model request and a tool added between two requests of one run
  is in `AgentInfo.function_tools` of the second; `isinstance(as_pydantic_ai_toolset(t),
  AbstractToolset)` and `Agent(…, toolsets=[it])` accepts it. Unit (fake `Toolset`): alias
  vanished between `get_tools` and `call_tool` → `ToolFailed`, run continues. Integration
  (real Host, modern and legacy stdio): `Agent(TestModel(call_tools=["<id>__echo"]))` with
  an allowing prompter → tool result text in the history and one audit record for
  `agent:<name>`; denying prompter → `outcome="failed"` with the denial text, the run
  completes, audit `denied` (Plan Phase 5 Done, PydanticAI half).

### F5-04 [P] · `adapters.langgraph`
- **Status**: [x] 2026-10-31 (follows folded delta D3)
- **What**: `adapters/langgraph.py`: import guard naming `reflex-mcphost[langgraph]`;
  imports only `langchain_core`. `HostTool(BaseTool)` with fields `toolset: Toolset`
  (excluded from serialization), `handle_tool_error = True`, `args_schema` =
  `spec.input_schema` (dict), `metadata = {"fqn": str(spec.fqn)}`; overrides
  `_to_args_and_kwargs` to pass the parsed argument dict as one positional argument (S-6:
  avoids the reserved-kwarg collisions); `_arun(self, arguments)` (no `config` /
  `run_manager` parameters, so LangChain injects nothing) → `await toolset.call(self.name,
  arguments)`; `is_error` → `raise ToolException(outcome.text)`; `KeyError` → `raise
  ToolException("Tool is no longer available: <name>")`; success → `outcome.text`. `_run`
  raises `NotImplementedError("reflex-mcphost tools are async-only; use ainvoke")`.
  `as_langchain_tools(toolset) -> list[BaseTool]` builds one `HostTool` per
  `toolset.list()` entry (snapshot semantics: call again after `catalog.changed`, API
  §4.13). `__all__ = ["as_langchain_tools"]`.
- **REQ**: REQ-TLS-007, REQ-TLS-005 (LangChain channel) · API §4.13
- **Files**: `src/reflex_mcphost/adapters/langgraph.py`,
  `tests/unit/adapters/test_langgraph.py`,
  `tests/integration/adapters/test_langgraph_agent.py`,
  `tests/contract/test_adapter_surfaces.py` (S-6 #13–17)
- **Depends on**: F5-02
- **Done**: contract S-6 #13–17 green: `convert_to_openai_tool(tool)["function"]
  ["parameters"] == spec.input_schema`; `ainvoke` with a `ToolCall` returns
  `ToolMessage(status="success")` / `ToolMessage(content=<denial text>, status="error")`;
  arguments named `config`, `run_manager`, `callbacks`, `self` reach `Toolset.call`
  unchanged (fake `Toolset`); one-node `StateGraph(MessagesState)` with
  `langgraph.prebuilt.ToolNode(tools)` (default `handle_tool_errors`) turns a denial into
  `ToolMessage(status="error")` and does not raise; a second `as_langchain_tools` after a
  catalog change returns the new set. Unit: vanished alias → `status="error"`, graph does
  not raise; sync `invoke` → `NotImplementedError`. Integration (real Host, modern and
  legacy stdio): two-node `model → tools_condition → ToolNode → model` graph driven by the
  fake chat model calls `<id>__echo` — allowing prompter → `ToolMessage` with the echo
  text and a final AI message; denying prompter → `status="error"` with the denial text,
  the graph completes, audit `denied` (Plan Phase 5 Done, LangGraph half).

### F5-05 · No-framework-import test, public re-exports, library quickstart
- **Status**: [x] 2026-10-31 (follows folded delta D1) — plus `tests/unit/test_library_docs.py` (doc snippet lines = integration-test lines)
- **What**: (1) `tests/architecture/test_no_framework_import.py`: in a fresh subprocess,
  `import reflex_mcphost`, resolve every lazy name in `__all__` (incl. `Host`), `import
  reflex_mcphost.toolset` and touch its names, then assert none of `pydantic_ai`,
  `langchain_core`, `langgraph`, `langchain` and `reflex` is in `sys.modules` (REQ-TLS-008;
  `starlette`/`uvicorn` are *not* asserted — the `mcp` client pulls them through
  `mcp.server.context`, an SDK-internal chain, see Analyze P5-12). (2) Missing-extra test:
  a subprocess with a meta-path blocker for `pydantic_ai` (resp. `langchain_core`) imports
  the adapter and gets `ImportError` whose message names `reflex-mcphost[pydantic-ai]`
  (resp. `[langgraph]`). (3) Public surface (API §3.6): `reflex_mcphost.toolset.__all__`,
  both adapter `__all__`s, and a test that every `class` named in API §4 code blocks is
  exported by `reflex_mcphost` or `reflex_mcphost.toolset` — closes the drift found on
  2026-10-31 (`AuthorizationFlow`, `SamplingProvider` not re-exported at top level;
  `Toolset`, `ToolSpec`, `ToolCallOutcome` new). (4) Import-linter: new forbidden contract
  `reflex_mcphost.adapters` ↛ `reflex_mcphost.core` (direct imports; delta D1).
  (5) Art. 3 guard: `test_single_effect_path.py` gains a non-vacuous assertion that
  `reflex_mcphost.core.toolset` calls `.call(` on the Invoker and no `call_tool`.
  (6) CI: strict REQ gate extended to `TLS` (`--families REG,CON,CAT,CNS,INV,HSH,AUTH,TLS`).
  (7) Docs: `docs/library.md` (install with extras, `Host` + `Consumer.agent`, filters,
  PydanticAI and LangGraph snippets identical to the integration tests, denial as tool
  error, snapshot vs per-step listing, no real-LLM requirement for tests), README section
  "Use the Host from an agent" + progress update, `CHANGELOG.md` `[Unreleased]` entries.
- **REQ**: REQ-TLS-008 · API §3.6, Art. 3, Art. 4, Art. 7
- **Files**: `tests/architecture/{test_no_framework_import,test_single_effect_path,test_import_contracts}.py`,
  `tests/unit/test_public_api.py`, `pyproject.toml` (`[tool.importlinter]` only),
  `src/reflex_mcphost/__init__.py`, `.github/workflows/ci.yml`, `docs/library.md`,
  `README.md`, `CHANGELOG.md`
- **Depends on**: F5-03, F5-04
- **Done**: `uv run pytest` green; `uv run lint-imports` green with the new contract;
  `check_req_coverage.py --strict --families REG,CON,CAT,CNS,INV,HSH,AUTH,TLS` passes
  (REQ-TLS-001…008 each cited); the snippets in `docs/library.md` are the ones exercised by
  F5-03/F5-04 integration tests; coverage gates (core ≥ 90 %, total ≥ 80 %) hold with
  `toolset`/`adapters` included.

### F5-06a · Release workflow and pre-publish checks (no publish)
- **Status**: [~] implemented 2026-10-31, verified locally (build + check_wheel + publish dry-run + smoke); PR run of `release.yml` pending (follows folded delta D4)
- **What**: (1) `.github/workflows/release.yml` (DD-015, TD §9.1 per D4): triggers `push`
  of tags `v*`, `workflow_dispatch` and `pull_request` on changes to `release.yml`,
  `pyproject.toml`, `uv.lock` or `scripts/check_wheel.py`; top-level `permissions:
  contents: read`. Job `build` (ubuntu, Python 3.13): `uv build`; `uv run python
  scripts/check_wheel.py dist --expect-version <ver>` (`<ver>` = tag without `v` on tag
  pushes, else the `pyproject.toml` version); `uv publish --dry-run --trusted-publishing
  never dist/*` (checks the files, uploads nothing, needs no credentials — verified
  locally 2026-10-31); uploads `dist/` as an artifact. Job `smoke` (3 OS × Python 3.11/3.13):
  fresh `uv venv`, install the built wheel plain and with `[all]`, run the REQ-TLS-008
  subprocess check and import both adapters against the *installed* wheel (not the source
  tree). Job `publish`: `needs: [build, smoke]`, runs **only** `if: github.event_name ==
  'push' && startsWith(github.ref, 'refs/tags/v')`, `environment: pypi`, job-level
  `permissions: id-token: write`, downloads the artifact (no rebuild) and runs `uv publish
  --trusted-publishing always`. No token secret exists anywhere. (2) `scripts/check_wheel.py`
  extended: `--expect-version` (wheel and sdist file names and `METADATA` `Version`);
  every `console_scripts` entry point resolves to a module present in the wheel; `METADATA`
  declares `Provides-Extra: pydantic-ai`, `langgraph`, `all` and a Markdown long
  description; sdist checked for forbidden prefixes (`tests/`, `spikes/`, `specs/`,
  `changes/`, `.claude/`). (3) `pyproject.toml` (D4): `[project.scripts]` removed for
  `0.1.0a1` (the `reflex_mcphost.app.cli` target only exists from F6-08, which re-adds it);
  classifier `Development Status :: 3 - Alpha`. The version stays `0.1.0.dev0` until F5-06b.
- **REQ**: — · DD-015, TD §9.1, Art. 1, Art. 7, Art. 9
- **Files**: `.github/workflows/release.yml`, `scripts/check_wheel.py`,
  `tests/scripts/test_check_wheel.py`, `pyproject.toml` (`[project]` metadata only)
- **Depends on**: F5-05
- **Done**: locally `uv build --out-dir <tmp>` + `uv run python scripts/check_wheel.py <tmp>
  --expect-version 0.1.0.dev0` → OK and `uv publish --dry-run --trusted-publishing never
  <tmp>/*` exits 0; `test_check_wheel.py` negatives: version mismatch, a `console_scripts`
  target missing from the wheel (the current `app.cli:main` case), `tests/` in the sdist,
  missing extra → each fails with a named problem. On the PR to `develop`, `release.yml`
  runs `build` + `smoke` green and `publish` is **skipped**; no PyPI or TestPyPI upload
  happens in this task.

### F5-06b · Tag `v0.1.0a1` and publish to PyPI — OWNER ACTION
- **Status**: [!] blocked — owner action. Needs (a) PyPI Trusted Publisher (pending
  publisher) for project `reflex-mcphost`: owner `ecrespo`, repo `reflex-mcphost`, workflow
  `release.yml`, environment `pypi`; (b) GitHub environment `pypi` with the owner as
  required reviewer and deployment restricted to tags `v*` (plus a tag protection rule for
  `v*`); (c) the owner's explicit approval of this release. Plan §2 prerequisite "PyPI
  project reserved with Trusted Publishing configured" is still ☐.
- **What**: (1) Release PR `develop` → `main` (agent may prepare it on request): version
  `0.1.0a1` in `pyproject.toml`, `uv lock`, `CHANGELOG.md` `## [0.1.0a1] - <date>` (core +
  toolset + adapters; no UI, no CLI; deprecated features listed; known limits: S-4 real-AS
  CIMD open, CIMD URL live only once Pages is enabled — DCR/configured `client_id`
  fallback, token expiry unknown after restart). (2) Owner merges and pushes tag
  `v0.1.0a1` on `main`; `release.yml` runs; the owner approves the `pypi` environment
  deployment; `publish` uploads the artifacts built by `build`. (3) GitHub Release with the
  changelog section. Optional rehearsal before (owner): a TestPyPI pending publisher +
  `testpypi` environment and a `workflow_dispatch` input `index=testpypi` (only if the
  owner asks; not required). Rollback: yank on PyPI and release `0.1.0a2`; never delete
  and re-upload.
- **REQ**: — · DD-015, TD §9.1, Plan Phase 5 Done
- **Files**: `pyproject.toml` (version), `uv.lock`, `CHANGELOG.md`; tag `v0.1.0a1`
- **Depends on**: F5-06a, F5-05, Phase 4 (done), owner prerequisites above
- **Done**: `pip index versions reflex-mcphost --pre` (or the PyPI JSON API) lists
  `0.1.0a1`; on Linux, `uv run --isolated --no-project --with
  'reflex-mcphost[all]==0.1.0a1' python -c "import reflex_mcphost.toolset,
  reflex_mcphost.adapters.pydantic_ai, reflex_mcphost.adapters.langgraph"` succeeds; the
  published wheel passes `check_wheel.py --expect-version 0.1.0a1`; Plan §2 PyPI row ☑;
  execution-log row written.

## Phase 6 — Reflex UI, reference app and CLI

Common rules for Phase 6:
- **Delta `changes/_archive/2026-11-phase6-prep` is approved and folded (2026-11-01).**
  F6-01 follows D1 (Host as an async-context-manager lifespan task) and D3 (guard origin
  from `deploy_url`, remote opt-in); F6-02/F6-03 follow D2 (`PromptRegistry` for every
  prompt kind) and D7 (view models, bounded rendering); F6-08 follows D3/D4 (CLI ↔ app
  contract, exit codes); F6-03 follows D5 (trust prompts not time-bounded); F6-09a/F6-09b
  follow D6 (Playwright only in F6-09b). No task is gated by an owner action.
- **Layering (Art. 4, DD-018).** `reflex_mcphost.ui` imports `core` (and may import
  `toolset`), never `app`; `reflex_mcphost.app` composes only `ui` (API §8) plus
  `core.config` / `core.registry` / `core.db` for the CLI pre-flight and `servers`.
  `core` and `toolset` still import no `reflex` (existing contract). New forbidden
  contract (F6-02): `reflex_mcphost.ui.models` ↛ `reflex`, so mapping and filtering logic
  is testable without Reflex. `servers` and `--version` do not import `reflex` (lazy
  import in `run` only).
- **The UI is a `Prompter` (API §4.10).** Every human decision reaches core only as a
  `Prompter` return value (`ConsentAnswer`, `SamplingVerdict`, `ElicitationResponse`,
  trust `bool`) through `ReflexPrompter` → `PromptRegistry` (Flow D). The UI never calls
  `ConsentPolicy.grant`, never writes rules except through the Permissions view's
  `update`/`revoke` (REQ-CNS-013), never builds a consent outcome itself and never touches
  a `ClientSession` (the Art. 3 AST test already covers `ui.*` and `app.*`). Exceptions in
  the prompter are fail-closed in core (deny / `cancel` / untrusted).
- **One human Consumer.** Every UI-originated invocation and read uses `Consumer.human()`
  (`human:ui`) through `host.invoker.start(...)` / `host.catalog.*`; prompt cards show the
  Consumer that is asking (`human:ui`, `agent:<name>`, unattributed — REQ-UI-005,
  REQ-HSH-047).
- **Secrets and untrusted content (Art. 5).** View models carry env/header **keys** only
  (REQ-REG-007), never tokens, codes or the OAuth authorization URL (it carries `state`;
  it is opened, never rendered or logged — REQ-AUTH-008); argument previews come from core
  (already redacted, REQ-CNS-012). Server-provided text (tool descriptions, annotations,
  elicitation messages, log lines, results) is rendered as plain text — never Markdown or
  HTML; images only from `data:` URLs built from the content block's base64 and MIME type.
  The F4-09b seeded redaction test is extended to the UI view models (F6-02).
- **Reflex surface (Art. 2), checked against `.venv` on 2026-11-01** (`reflex` 0.10.1,
  `reflex-base` 0.10.1, same as `uv.lock`; `granian` 2.8.7, `starlette` 1.10.0):
  `rx.App(api_transformer=…)` (callable, `Starlette` or a sequence), `App.register_lifespan_task(task=None,
  **task_kwargs)` accepting an async context manager factory (entered on Reflex's
  `AsyncExitStack`; coroutine tasks get a 5 s cancel cap, `_LIFESPAN_TASK_CANCEL_TIMEOUT`),
  `App.add_page(..., on_load=...)`, `rx.event(background=True)` + `async with self`,
  `rx.State`, components `rx.form`, `rx.input`, `rx.text_area`, `rx.select`, `rx.switch`,
  `rx.checkbox`, `rx.drawer`, `rx.dialog`, `rx.tabs`, `rx.table`, `rx.foreach`, `rx.cond`,
  `rx.badge`, `rx.callout`, `rx.code_block`, `rx.scroll_area`, `rx.toast`, `rx.redirect(url,
  is_external=True)`. `reflex.testing.AppHarness` exists but needs Selenium (not installed):
  not used.
- **Testing approach.** (1) `ui/models.py` pure unit tests (no Reflex). (2) Reflex state unit
  tests: states built with `State(_reflex_internal_init=True)` and handlers invoked as
  `State.handler.fn(state, …)` — both pinned by a contract test (F6-01; private surface,
  Art. 2 risk accepted like S-6's `_to_args_and_kwargs`); watcher and prompt logic live in
  plain async functions driven by fake streams. (3) Integration: a real `Host` with the
  modern and legacy stdio fixtures, the real `ReflexPrompter`/`PromptRegistry`, prompts
  answered programmatically — no browser. (4) ASGI tests of the guard (`httpx.ASGITransport`
  for HTTP, raw ASGI websocket scope). (5) CLI tests with a fake process runner, readiness
  probe and `webbrowser`. (6) **F6-09a** headless smoke (marker `e2e`) against a real
  `reflex-mcphost run --no-browser` from the built wheel — `httpx` only. (7) **F6-09b**
  Playwright (Python): **`playwright` is not a dependency today** (`pyproject.toml` /
  `uv.lock`, checked 2026-11-01) although DD-015 lists it; F6-09b adds it to the `test`
  group. No Phase 6 task before F6-09b assumes it.
- **Both eras.** Prompt, servers and invoke integration tests run against the modern and
  legacy stdio fixtures (legacy covers server-initiated elicitation/sampling and the
  unattributed path). The HTTP fixtures appear only in the OAuth row of F6-04.
- **Coverage (Art. 7).** `ui` and `app` count toward the overall ≥ 80 % gate; `core` ≥ 90 %
  is unaffected. The strict REQ gate gains `UI` in F6-09a (REQ-UI-008 is `SHOULD`).
- **Dependencies.** No new runtime dependency (`reflex>=0.10.1,<0.11` is already one). The
  only new distribution is `playwright` in the `test` group (F6-09b). The fixture change in
  F6-09b (tool count option) stays under `tests/` (Art. 1).
- **Batch ownership.** Batch Q owns `ui/{__init__,install,models,state,prompter}.py`,
  `ui/components/{__init__,consent_center}.py`, `tests/unit/ui/*`,
  `tests/integration/ui/{test_watcher,test_prompts}.py`, `tests/security/test_loopback_guard.py`,
  `tests/contract/test_reflex_surface.py` and the `[tool.importlinter]` table. Batch R owns
  `ui/components/{servers_panel,schema_form,catalog_browser,permissions_table,audit_table}.py`
  and their tests; F6-06 extends `consent_center.py` after Q. Batch S owns `app/*`,
  `[project.scripts]`, `scripts/check_wheel.py`, `release.yml`. Batch T owns `tests/e2e/*`,
  the `e2e` CI job, the `test` group and the pytest marker.
- **Owner actions.** None gates Phase 6. CI evidence (matrix, `e2e` job, the F5-06a
  `release.yml` PR run) needs a push / PR to GitHub. The Plan objective "`uvx
  reflex-mcphost` from PyPI" needs a published pre-release (F5-06b now, F7-07 later — owner);
  Phase 6 verifies `uvx --from <built wheel>`.

### F6-01 · `ui.install`, `HostRuntime`, lifespan context manager, Host/Origin guard
- **Status**: [x] 2026-11-01 (follows folded delta D1, D3)
- **What**: (1) `ui/install.py`: `install(app, host_factory)` registers a
  `contextlib.asynccontextmanager` lifespan task: `host = host_factory()` →
  `host.set_prompter(<process-wide ReflexPrompter>)` → `async with host:` → publish in
  `HostRuntime` → `yield` → exit (`aclose()` with its full budget). `HostRuntime.host` raises
  `RuntimeError` before start-up and after shutdown; idempotent per `rx.App`, a second call
  with a different factory raises `RuntimeError`. `ReflexPrompter` is a stub here (its
  methods raise, i.e. fail closed) until F6-03. (2) `LoopbackGuard`, a pure-ASGI middleware
  appended to `app.api_transformer` (keeping any transformer the embedding app set): the
  single allowed origin is parsed from `get_config().deploy_url` at start-up; HTTP and static
  requests whose `Host` ≠ `<host>:<port>` or whose `Origin` is present and ≠
  `http://<host>:<port>` → `403` with an HTML body "Open http://<host>:<port>/"; websockets
  → `websocket.close` before accept; a `deploy_url` host other than `127.0.0.1` without
  `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1`, or `localhost`, → start-up `RuntimeError`; a
  `cors_allowed_origins` broader than the origin is logged as a warning. (3)
  `ui/__init__.py` `__all__` per API §8 (names land as their tasks do).
- **REQ**: REQ-NFR-011, REQ-UI-007 (install surface), REQ-CON-009 (shutdown budget kept) ·
  DD-011, DD-017, API §8
- **Files**: `src/reflex_mcphost/ui/{__init__,install}.py`, `tests/unit/ui/{__init__,test_install}.py`,
  `tests/security/test_loopback_guard.py`, `tests/integration/ui/{__init__,test_lifespan}.py`,
  `tests/contract/test_reflex_surface.py`
- **Depends on**: F5-06a (code merged at `dbdc23a`; its pending PR run does not gate Phase 6)
- **Done**: `test_loopback_guard.py` reproduces the S-5 §5 matrix on the ASGI app: `Host:
  127.0.0.1:P` → passes; `localhost:P` and `evil.example:P` → 403 whose body names
  `http://127.0.0.1:P`; `Origin: http://evil.example` / `http://localhost:P` → 403; websocket
  with a foreign `Host` and a valid `Origin` → closed before accept; no `Origin` → passes;
  remote mode (`deploy_url = http://192.0.2.10:P` + opt-in) accepts only `192.0.2.10:P`;
  `deploy_url` on `localhost`, or non-loopback without the opt-in → start-up `RuntimeError`.
  `test_install.py` (fake Host recording the current task): `start` and `aclose` run in
  the same task, the prompter is set before `start`, a `start` failure propagates out of the
  lifespan, `HostRuntime.host` raises before and after, second `install` with another
  factory → `RuntimeError`. Contract: an async-context-manager lifespan task whose exit
  sleeps 6 s is awaited to completion by `App._run_lifespan_tasks` (S-5 #1 extended);
  `api_transformer` sees `http` and `websocket` scopes (S-5 #2 behavioural);
  `State(_reflex_internal_init=True)` + `Handler.fn` work (pins the testing approach).
  `test_lifespan.py` (real Host, modern stdio fixture as an own Server, lifespan driven
  without a server): after the context exits, the fixture's process (its `pid` tool) is gone
  (REQ-CON-009).

### F6-02 · View models, `HostState` and the background watcher
- **Status**: [x] 2026-11-01 (follows folded delta D7)
- **What**: (1) `ui/models.py` (no `reflex` import; new import-linter forbidden contract
  `reflex_mcphost.ui.models` ↛ `reflex`): pure builders returning JSON-serializable rows —
  `server_rows(statuses, registry)` (id, state, reason, era, protocol version, origin kind +
  path, transport, capability names, enabled, env/header **keys**, shadowed), `catalog_rows`
  per list, `rule_rows`, `audit_rows`, `prompt_cards` (F6-03), and `page(rows, query, *,
  limit=200) -> (rows, more)` (case-insensitive substring over id/FQN/title/description).
  (2) `ui/state.py`: `HostState(rx.State)` holding only view models; page `on_load` →
  `start_watch` → `@rx.event(background=True) watch`: subscribes to `host.events` (and, from
  F6-03, the `PromptRegistry`), applies each event inside `async with self` by recomputing
  only the affected Server's rows (`server.state_changed`, `catalog.changed`, `server.log`,
  `registry.changed`; `events.dropped` → full recompute); watchers de-duplicated per client
  token in a process-local dict (never in state), tolerating cancellation without touching
  state; logs `watcher started/stopped` at INFO (token suffix only). Host not available →
  a "Host starting / stopped" banner, no exception. (3) The event-application loop is a plain
  async function `apply_events(source, sink)` so it is tested without Reflex.
- **REQ**: REQ-UI-007 (state in `ui`), REQ-CON-010 (state-change events consumed),
  REQ-REG-007 (keys only), REQ-NFR-004 (bounded rendering logic), REQ-NFR-010 (UI view
  models) · DD-011, API §8
- **Files**: `src/reflex_mcphost/ui/{models,state}.py`, `pyproject.toml` (`[tool.importlinter]`
  only), `tests/unit/ui/{test_models,test_state,test_watcher}.py`,
  `tests/integration/ui/test_watcher.py`, `tests/architecture/test_import_contracts.py`,
  `tests/security/test_redaction.py`
- **Depends on**: F6-01
- **Done**: `test_models.py`: `page` keeps ≤ 200 rows and reports the rest; 2,000 rows
  filtered in < 20 ms (pure Python, informational for REQ-NFR-004); a config with a seeded
  env value and header value → neither value appears in any row (keys do); a
  `config_error` row names `${DB_URL}` by key. `test_watcher.py` (fake stream and sink):
  only the affected Server is recomputed; `events.dropped` → full recompute; a second
  `start_watch` for the same token starts no second subscriber; after cancellation the
  token is released and a restart for the same token works (S-5 pitfall). `test_state.py`:
  handlers via `.fn`, banner when `HostRuntime` is not started. Integration (real Host,
  modern and legacy stdio): rows reach `ready` with the fixture's capabilities; calling
  `add_tool` → the catalog rows gain the tool after `catalog.changed`; the redaction test's
  seeded secrets appear in no view model. `lint-imports` green with the new contract.

### F6-03 · `ReflexPrompter`, `PromptRegistry` and `consent_center`
- **Status**: [x] 2026-11-01 (follows folded delta D2, D5)
- **What**: (1) `ui/prompter.py`: `PromptRegistry` — pending entries `PendingPrompt(kind,
  request_id, payload, created_at)` keyed by request id; `pending()`, `subscribe()` (one
  bounded anyio stream per subscriber, drop-oldest + one `dropped` marker), `resolve(id,
  answer) -> bool` (first answer wins, idempotent). `ReflexPrompter` (`supports_elicitation
  = True`) implements all four `Prompter` methods; each registers an entry, awaits its
  answer and removes it in a `finally` (answered, core timeout, cancellation);
  `confirm_trust` has no timeout of its own (D5). (2) `ui/components/consent_center.py`: a
  non-blocking side panel (drawer, never a page-blocking modal) of cards rendered from
  `PromptRegistry.pending()`; consent card: asking Consumer, FQN, Server identity, tool
  description as plain text, argument preview, "definition changed" banner (REQ-CNS-007),
  answers `allow_once` / `allow_session` / `allow_always` / `deny` (+ deny lifetime) with
  target tool / server / global and the REQ-CNS-007 statement when server/global is chosen;
  unattributed requests (REQ-HSH-047) offer only `allow_once` and `deny` (once), and the
  state handler rejects any other answer before it reaches core. Trust card (REQ-REG-011):
  command, resolved command, args, cwd, env **names** with risky ones flagged / URL template,
  destination, header **names**; Trust / Don't trust. (3) `HostState` merges the registry
  stream (watcher source 2). Sampling-consent, sampling-review and elicitation cards: F6-06.
- **REQ**: REQ-UI-005, REQ-CNS-002, REQ-CNS-004, REQ-CNS-005, REQ-CNS-006 (answers from the
  UI), REQ-CNS-007, REQ-CNS-009, REQ-HSH-047 (UI options), REQ-REG-011 (trust UI) · Flow D,
  API §4.10, §8
- **Files**: `src/reflex_mcphost/ui/{prompter,state,models}.py`,
  `src/reflex_mcphost/ui/components/{__init__,consent_center}.py`,
  `tests/unit/ui/{test_prompt_registry,test_reflex_prompter,test_consent_center}.py`,
  `tests/integration/ui/test_prompts.py`
- **Depends on**: F6-02
- **Done**: `test_prompt_registry.py`: two subscribers both see `added`; the first
  `resolve` wins, the second returns `False`; cancelling the awaiting coroutine removes the
  entry and publishes `removed`; overflow delivers one `dropped`. `test_reflex_prompter.py`:
  answer cards map to the exact `ConsentAnswer` (decision, target, deny lifetime); an
  unattributed card exposes only two answers. Integration (real Host, modern and legacy
  stdio, two `HostState` instances standing in for two tabs): a `human:ui` call with no rule
  → one card in both; `allow_once` in one → the tool runs, the card leaves both, one audit
  record; `allow_always` → a persistent rule; `consent_timeout_s = 1` → the card disappears
  and the call ends `denied`; a Toolset call for `agent:helper` → the card names
  `agent:helper`; a project `.mcp.json` stdio fixture → a trust card with env names only (a
  seeded env value is in no view model), risky names flagged; Trust → `ready`, Don't trust →
  `failed` / `untrusted_project_server`; disconnecting the Server while asked removes the
  card.

### F6-04 [P] · `servers_panel`
- **Status**: [x] 2026-11-01 (follows folded delta D7)
- **What**: Servers view (container `data-testid="servers-panel"`, REQ-NFR-003 anchor): rows
  from `server_rows`; state badge + reason (`auth_required`, `config_error`,
  `untrusted_project_server`, …); era and protocol version; origin; transport; declared
  capabilities; shadowed definitions and config problems (keys only). Actions: connect,
  disconnect, reconnect, authorize, sign-out, enable / disable (`registry.set_enabled`,
  REQ-REG-010), reload (`registry.reload`, REQ-REG-013), log level (deprecated, labelled).
  Authorize: `connections.authorize(id)` in a background event → open
  `flow.authorization_url` with `rx.redirect(url, is_external=True)` (core never opens a
  browser); the URL is never put in a state var or log; the flow's outcome or
  `AuthorizationError.reason` code is shown. Log stream per Server: `connections.logs(id)` +
  `server.log` events, bounded (last 200 rendered of the 1,000-entry buffer), plain text.
- **REQ**: REQ-UI-003, REQ-REG-007, REQ-REG-010, REQ-REG-013, REQ-CON-010, REQ-HSH-030
  (SHOULD), REQ-HSH-031 (SHOULD), REQ-AUTH-002 (UI start), REQ-AUTH-007, REQ-AUTH-008 · API §8
- **Files**: `src/reflex_mcphost/ui/components/servers_panel.py`, `src/reflex_mcphost/ui/models.py`,
  `tests/unit/ui/test_servers_panel.py`, `tests/integration/ui/test_servers_panel.py`
- **Depends on**: F6-02
- **Done**: unit (fake Host): every action handler calls exactly one public core method with
  the row's id; columns as specified. Integration (modern and legacy stdio): disconnect →
  `disconnected`; reconnect → `ready`; disabling a Server that comes from a third-party
  file writes the override to `mcphost.toml` and leaves the source byte-identical
  (`third_party_files` fixture, REQ-REG-008); editing the own `mcphost.toml` + reload →
  only the changed Server reconnects; fixture stderr lines appear in the log view. HTTP
  OAuth row (fixture AS + protected Server, F4-09a): the row is `auth_required`; Authorize
  returns an external redirect to the AS; the URL and its `state` appear in no state var
  and no captured log; Sign out → `auth_required` again.

### F6-05 · `schema_form`, `catalog_browser` and the invoke flow
- **Status**: [x] 2026-11-01 (follows folded delta D7)
- **What**: (1) `ui/components/schema_form.py`: a form generated from a tool's
  `inputSchema` (object of string — `enum` → select, `format: password` → password input —
  number / integer, boolean → switch, array of strings or enums → multi-select / one per
  line; required markers); anything outside that subset falls back to a JSON text area (the
  Invoker validates anyway, REQ-INV-002). Pure coercion `coerce_form(schema, raw) -> (args,
  errors)` in `ui/models.py`; `InvocationError.details.issues` mapped back to fields.
  Reused by F6-06 for elicitation forms. (2) `ui/components/catalog_browser.py`: tabs Tools /
  Resources / Templates / Prompts, per Server, filtered with `page()` on every keystroke
  (REQ-NFR-004); tool details: description and annotations as untrusted plain-text hints
  (REQ-CAT-011), input / output schema. Invoke → `host.invoker.start(fqn, args,
  consumer=Consumer.human())` in a background event; progress (`InvocationHandle.progress`),
  Cancel (`InvocationHandle.cancel`), result panel: text blocks, images as `data:` URLs,
  audio as a player, resource links / embedded resources as text, `structuredContent` as
  JSON; outcome badge and error message. Resources: `catalog.read_resource`; templates: a
  form for the URI variables → `read_template`; prompts: an arguments form →
  `catalog.get_prompt` messages. Tools with `ui_resource_uri` show the plain result (the
  View renders in Phase 7).
- **REQ**: REQ-UI-004, REQ-INV-006 (result content shown), REQ-CAT-005, REQ-CAT-006,
  REQ-CAT-007 (UI side), REQ-CAT-011, REQ-NFR-004 (rendering logic) · API §8
- **Files**: `src/reflex_mcphost/ui/components/{schema_form,catalog_browser}.py`,
  `src/reflex_mcphost/ui/models.py`, `tests/unit/ui/{test_schema_form,test_coerce,test_catalog_browser}.py`,
  `tests/integration/ui/test_invoke.py`
- **Depends on**: F6-02
- **Done**: `test_coerce.py` table (string / enum / password / integer / number / boolean /
  string arrays / nested object → JSON fallback, required missing → field error); Hypothesis:
  coercion never raises on arbitrary raw input. Integration (modern and legacy stdio, a
  programmatic allow rule for `human:ui`): `echo` → the echoed text; `structured` →
  JSON panel; `tool_error` → error badge with the Server's text; invalid arguments →
  `invalid_arguments` with the issue mapped to its field; `slow` + Cancel → `cancelled`;
  `progress` → progress values shown; a resource read and a prompt get show their content;
  the audit record's Consumer is `human:ui`.

### F6-06 · Elicitation forms and sampling consent / review UI
- **Status**: [x] 2026-11-01 (follows folded delta D2)
- **What**: `consent_center` cards for the remaining prompt kinds: **elicitation form**
  (Server identity and message; `schema_form` over the already-validated
  `requested_schema`; Submit / Decline / Cancel; on a re-prompt the same card is `updated`
  with `ElicitationRequest.errors` and stays open — REQ-HSH-013); **elicitation url**
  (REQ-HSH-015 SHOULD: the full URL and the Server shown; Open-and-accept opens it with an
  external redirect, Decline / Cancel never open it); **sampling consent** (REQ-HSH-002:
  messages, system prompt, model preferences, max tokens; Approve / Edit (message text →
  `ConsentAnswer.edited_sampling`) / Reject; labelled *deprecated, compatibility*);
  **sampling-response review** (REQ-HSH-004 SHOULD: the completion; Approve / Edit /
  Reject → `SamplingVerdict`). Unattributed legacy requests carry a badge (REQ-HSH-047).
- **REQ**: REQ-UI-005, REQ-HSH-002, REQ-HSH-004 (SHOULD), REQ-HSH-010, REQ-HSH-011,
  REQ-HSH-013, REQ-HSH-015 (SHOULD), REQ-HSH-047 · API §4.10
- **Files**: `src/reflex_mcphost/ui/components/consent_center.py`,
  `src/reflex_mcphost/ui/{prompter,models}.py`, `tests/unit/ui/test_prompt_cards.py`,
  `tests/integration/ui/test_handlers_ui.py`
- **Depends on**: F6-03, F6-05
- **Done**: Integration (modern and legacy stdio, prompts answered through `HostState`
  handlers): `elicit_form` → a form card; an invalid submit → the same card shows the
  field error and the Server receives nothing; a valid submit → the tool result contains
  the content; Decline / Cancel → the corresponding action reaches the Server;
  `elicit_url` → the card shows the full URL, accept → an external redirect and `accept`,
  decline → no redirect; `sample` with a fake `SamplingProvider`: the consent card shows
  the messages, system prompt and max tokens; an edited message is what the provider
  receives; with `review_sampling_responses = true` the review card appears and Reject
  refuses per era (legacy `-1` / modern `denied/sampling_denied`); a legacy request with two
  calls in flight → unattributed badge and two answers only.

### F6-07 [P] · `permissions_table` and `audit_table`
- **Status**: [x] 2026-11-01 (follows folded delta D7)
- **What**: Permissions: `consent.rules()` (persistent and session) with effect,
  operation, target, Consumer, lifetime, origin, `suspended` (definition changed), created /
  updated; Edit (effect / lifetime → `consent.update`) and Revoke (`consent.revoke`), applied
  to the next evaluation without restart (REQ-CNS-013); filter. Audit: `audit.query(AuditQuery)`
  with the REQ-INV-011 filters (time range, Server, FQN, Consumer, outcome; plus operation),
  newest first, cursor paging ("Load more"), refreshed on `invocation.finished`; rows show
  the argument hash, never arguments.
- **REQ**: REQ-UI-006, REQ-CNS-013, REQ-INV-011, REQ-INV-009 (fields shown) · API §8
- **Files**: `src/reflex_mcphost/ui/components/{permissions_table,audit_table}.py`,
  `src/reflex_mcphost/ui/models.py`, `tests/unit/ui/test_permissions_audit.py`,
  `tests/integration/ui/test_permissions_audit.py`
- **Depends on**: F6-02
- **Done**: Integration (modern stdio, headless prompter): a programmatic allow → a row;
  Revoke → the next call is denied (no rule, no prompter); Edit allow → deny → the next call
  is denied without restart; an `allow_session` rule is listed with lifetime `session`. Audit:
  each filter narrows the rows; newest first; "Load more" follows the cursor; a seeded
  secret-like argument value appears in no audit row.

### F6-08 · CLI, workspace materialization and the packaged reference app
- **Status**: [~] implemented 2026-11-01, verified locally (unit tests, build + check_wheel,
  manual warm `run --no-browser` boot, SIGINT/SIGTERM → 0); PR run of the `release.yml` CLI
  smoke pending (follows folded delta D3, D4; re-adds the console script dropped by delta
  `2026-10-phase5-prep` D4)
- **What**: (1) `app/cli.py` (argparse): `run` (also the bare command), `servers`,
  `--version`, flags per API §6. `run`: non-loopback `--host` without
  `--unsafe-allow-remote` → exit 2; wildcard host → exit 2 even with the flag; pre-flight
  `HostConfig.aload(--config)` (explicit invalid → 3) and DB open/close in the data dir (→ 3);
  materialize the workspace; spawn `sys.executable -m reflex run --env prod --backend-host H
  --frontend-port P --backend-port P` there with `GRANIAN_WORKERS=1`,
  `REFLEX_CHECK_LATEST_VERSION=false` and the DD-016 `REFLEX_MCPHOST_*` variables; poll
  `GET /ping` (allowed `Host`); print the URL; `webbrowser.open` it unless `--no-browser`;
  remote mode prints a warning banner; Ctrl-C / SIGTERM forwarded → exit 0 after the child
  ends; child non-zero before ready → 3, after → its code; console logging text or JSON
  (`--log-json`). `servers`: table `ID TRANSPORT ORIGIN ENABLED STATUS` or `--json`
  `{"servers", "shadowed", "problems"}` (secrets `**********`), `--strict` → 1 on any
  problem; no Host start, no connection, no `reflex` import. (2) `app/workspace.py`:
  `user_cache_dir("reflex-mcphost")/app/<version>-<content-hash>/` written to a temporary
  sibling and renamed into place. (3) `app/web/rxconfig.py` (telemetry off; `api_url` =
  `deploy_url` = `http://<host>:<port>` and `cors_allowed_origins` from the environment) and
  `app/web/mcphost_app/{__init__,mcphost_app}.py`: `install(app, host_factory)`; left nav
  Servers | Catalog | Permissions | Audit (no Settings page in v0.1) with `consent_center`
  on every page and `on_load=HostState.start_watch`; config loaded at import (D4).
  (4) `pyproject.toml` `[project.scripts] reflex-mcphost = "reflex_mcphost.app.cli:main"`;
  `check_wheel.py`: `reflex_mcphost/app/web/rxconfig.py` and the app module present, no
  `.web/` / `.states/` in the wheel; `release.yml` smoke (plain install): `reflex-mcphost
  --version` prints the expected version and `reflex-mcphost servers --json` with an empty
  temporary config exits 0.
- **REQ**: REQ-UI-001, REQ-UI-002, REQ-UI-008 (SHOULD), REQ-NFR-011 (origin wiring) · API §6,
  DD-016, DD-017
- **Files**: `src/reflex_mcphost/app/{__init__,cli,workspace}.py`,
  `src/reflex_mcphost/app/web/rxconfig.py`, `src/reflex_mcphost/app/web/mcphost_app/{__init__,mcphost_app}.py`,
  `pyproject.toml` (`[project.scripts]` only), `scripts/check_wheel.py`,
  `.github/workflows/release.yml` (smoke steps), `tests/unit/app/{__init__,test_cli,test_servers_cmd,test_workspace}.py`,
  `tests/scripts/test_check_wheel.py`, `tests/contract/test_reflex_surface.py`
- **Depends on**: F6-01 (pages compose F6-03…F6-07 components as they land; the full
  composition is checked in F6-09a)
- **Done**: `test_cli.py` (fake runner, probe, `webbrowser`): `--host 0.0.0.0` → 2;
  `--host 192.0.2.1` without the flag → 2, with it → banner and
  `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1`; `--config bad.toml` → 3; DB open failure → 3; child
  exits 1 before ready → 3; Ctrl-C after ready → 0; the browser is opened once at
  `http://127.0.0.1:P/` after the probe answers and never with `--no-browser`; the child's
  argv and environment are exactly the DD-016 set; bare `reflex-mcphost` behaves as `run`;
  `--version`. `test_servers_cmd.py`: table columns; `--json` shape with secrets masked;
  shadowed definitions listed; `--strict` with a problem → 1; no subprocess spawned, no
  `reflex` in `sys.modules` (subprocess check); third-party files byte-identical.
  `test_workspace.py`: a content change → a new directory; two concurrent materializations →
  one complete directory, no partial one; nothing written under the package directory.
  Contract S-5 #3: `reflex run --env prod` with different frontend and backend ports exits 1.
  `test_check_wheel.py` negatives: `.web/` in the wheel, missing `app/web/rxconfig.py` →
  named problems; the console script resolves.

### F6-09a · Headless smoke, embeddability check and Phase 6 gates
- **Status**: [x] 2026-11-01 — Done holds locally and in CI: the `e2e` job is green on the `develop` push run for `59967db` (with lint, build and the 3-OS × 2-Python test matrix) (follows folded delta D6)
- **What**: (1) `tests/e2e/test_smoke.py` (marker `e2e`): `uv build` → `uvx --from
  dist/<wheel> --compile-bytecode reflex-mcphost run --no-browser --port <free> --config
  <tmp mcphost.toml with the modern stdio fixture as an own Server>` in a temp
  `XDG_CACHE_HOME`; wait for `/ping`; `GET /` → 200 via `127.0.0.1`; `Host: localhost:P` →
  403 with the notice; foreign `Origin` → 403; socket.io polling handshake
  `/_event/?EIO=4&transport=polling` with a foreign `Host` → 403; the workspace's
  `.web/env.json` `EVENT` URL is `ws://127.0.0.1:P/_event` (S-5 #8); SIGINT → exit 0 within
  20 s and the fixture Server process is gone (REQ-CON-009 through D1); no file other than
  `__pycache__/*.pyc` added under site-packages (DD-016); `reflex-mcphost servers --json`
  against the same config. (2) `tests/integration/ui/test_embedding.py` (REQ-UI-007): a
  foreign `rx.App` with its own page calls `install(app, factory)` and adds pages using
  every component in API §8; `reflex_mcphost.ui.__all__` equals the API §8 names (minus
  `mcp_app_frame`, Phase 7); importing `reflex_mcphost.ui` imports no `reflex_mcphost.app`
  (subprocess). (3) Gates: CI job `e2e` (ubuntu-latest, Python 3.13, `uv sync --all-extras
  --all-groups --locked`, `uv run pytest -m e2e`); the matrix `test` job deselects `e2e`;
  strict REQ gate `--families REG,CON,CAT,CNS,INV,HSH,AUTH,TLS,UI`; README "Run the UI"
  section and progress, `CHANGELOG.md` `[Unreleased]` UI and CLI entries.
- **REQ**: REQ-UI-001, REQ-UI-002, REQ-UI-007, REQ-NFR-011, REQ-CON-009 · Art. 7, DD-016,
  DD-017
- **Files**: `tests/e2e/{__init__,conftest,test_smoke}.py`, `tests/integration/ui/test_embedding.py`,
  `tests/unit/ui/test_public_ui_api.py`, `pyproject.toml` (pytest marker `e2e` only),
  `.github/workflows/ci.yml`, `README.md`, `CHANGELOG.md`
- **Depends on**: F6-04, F6-06, F6-07, F6-08
- **Done**: `uv run pytest -m e2e tests/e2e/test_smoke.py` green locally on Linux (the first
  run downloads Bun and builds the frontend — network needed); `uv run pytest` (without
  `e2e`) green; `check_req_coverage.py --strict --families …,TLS,UI` passes (REQ-UI-001…007
  each cited); coverage gates hold with `ui`/`app` included; on the PR to `develop` the
  `e2e` job is green.

### F6-09b · Playwright E2E and REQ-NFR-003/004 measurements
- **Status**: [x] 2026-11-01 — flows, REQ-NFR-003 (warm starts 3.52–4.65 s) and REQ-NFR-004 (p95 105 ms after the 50 ms filter debounce, delta 2026-11-phase6-impl) green locally and in the `e2e` job of the `develop` push run for `59967db` (follows folded delta D6)
- **What**: (1) `playwright` added to `[dependency-groups].test` (`uv lock`); the `e2e` job
  runs `uv run playwright install --with-deps chromium`. (2) `tests/e2e/test_ui_flows.py`
  (Chromium, headless, against the F6-09a run fixture): consent → Allow once → the result
  panel shows the echo and the Audit view gains the row; elicitation form: invalid → field
  error, valid → result; two pages: answering in page A removes the card from page B; closing
  page B → the backend logs `watcher stopped` within 2 s (S-5 #6, `on_load` chain cancelled).
  (3) `tests/e2e/test_nfr.py`: REQ-NFR-003 — 5 warm runs (second start of the same version):
  `run --no-browser` start → `[data-testid="servers-panel"]` visible, each < 10 s;
  REQ-NFR-004 — 50 configured Servers and 2,000 catalog entries (modern fixture gains a
  `--tools N` option, tests only), p95 over 20 interactions of filter keystroke /
  `catalog.changed` → list DOM update < 200 ms (Playwright trace).
- **REQ**: REQ-NFR-003, REQ-NFR-004, REQ-UI-004, REQ-UI-005 · TD §8, PRD §7
- **Files**: `pyproject.toml` (`test` group), `uv.lock`, `tests/e2e/{test_ui_flows,test_nfr}.py`,
  `tests/fixtures/servers/modern_server.py` (tool-count option), `.github/workflows/ci.yml`
  (`e2e` job: browser install)
- **Depends on**: F6-09a
- **Done**: the `e2e` job is green with Playwright on `ubuntu-latest`; the measured warm
  starts and the REQ-NFR-004 p95 are written to the execution log. If either budget is
  missed: stop and open a Delta Spec (bounded rendering, page size, or the PRD target) — no
  silent relaxation.

## Phase 7 — MCP Apps and `0.1.0b1`

Common rules for Phase 7:
- **Delta `changes/_archive/2026-11-phase7-prep` is approved and folded (2026-11-01).**
  F7-08 follows D1 (App scope in the Invoker); F7-04/F7-05 follow D2 (UI-started
  invocations only, rendered after consent and completion, one View per tab) and D4
  (data-only relay); F7-01 follows D3 (CSP entry validation, pinned policy), D5 (limits), D7
  (`Host.set_mcp_apps`) and D8 (`ConnectionManager.append_log`); F7-02 follows D5, D9, D10;
  F7-03 follows D6 (h11 proxy listener, origin-pair check in the lifespan). The only owner
  action is F7-07b (publish).
- **Protocol (Art. 2).** MCP Apps `2026-01-26` (`specification/2026-01-26/apps.mdx`, read
  through Context7 2026-11-01): wire names, `HostCapabilities`, `tool-cancelled {reason}`,
  `ui/resource-teardown {reason}`, CSP from `resources/read` `contents[]._meta.ui.csp`,
  visibility rules. Constants live in `core.apps`, never imported from `mcp.server.apps`
  (Art. 1). The advertisement uses `mcp.client.advertise` (pinned by the SDK contract test).
- **Layering (Art. 4, DD-018).** `core.apps` is pure: no `reflex`, no `starlette`, no
  socket or file I/O; the bridge receives its ports (call tool, read resource, log, open
  link, send) as callables. `ui.proxy` uses `h11` + `anyio` only — new forbidden contract
  `reflex_mcphost.ui.proxy` ↛ `reflex`, `reflex_base` (F7-03). `ui.apps_bridge` adapts the
  core bridge to Reflex; `app` is untouched except through `ui.install`.
- **Single effect path (Art. 3).** An App's `tools/call` reaches the Server only through
  `Invoker.call(..., consumer=Consumer.app(server_id))`; the App scope is checked **in the
  Invoker** (step 1a) so every rejected attempt is audited; consent is evaluated for the
  `app:<server_id>` Consumer and answered only by the human (`consent_center` shows
  `app:<server_id>`). View `resources/read` goes through `host.catalog.read_resource` (not
  consent-gated, API §4.6). The existing AST test (no `call_tool` outside the Invoker) covers
  `ui.apps_bridge`.
- **Untrusted content (Art. 5).** Server HTML is never inserted into the UI DOM and never
  becomes script on the UI origin (D4); the relay accepts only `event.source ===
  proxyWindow && event.origin === <proxy origin>`; the proxy pins the UI origin
  server-side; CSP entries are validated (REQ-APP-014); no device permissions
  (REQ-APP-011); `ui/open-link` only `http`/`https` with a host after confirmation
  (REQ-APP-015); size and concurrency limits (REQ-APP-013, API §7). View arguments, results
  and HTML are memory-only per tab and never logged — the log stream gets `method →
  outcome` only (REQ-APP-012). The F4-09b seeded redaction test is extended to the
  `mcp-app` log entries (F7-02/F7-06a).
- **Testing approach.** (1) `core.apps` unit + Hypothesis tests (CSP builder, message
  validation, bridge state machine with fake ports). (2) Integration: a real `Host` with the
  new `apps_server.py` fixture on modern and legacy stdio, the core bridge driven by
  scripted View messages (no browser, F7-06a), then the Reflex adapter (F7-05). (3) Proxy
  HTTP tests over real sockets on `127.0.0.1` and `::1` (F7-03). (4) Static checks of the
  relay JS and of the UI code (no script built from message data, F7-04). (5) Playwright on
  Chromium, Firefox and WebKit with the S-3 contract assertions (F7-06b).
- **Fixture App (Art. 1, Art. 7).** The S-3 vendored App lived on the unmerged spike branch:
  F7-06a re-vendors `@modelcontextprotocol/server-basic-vanillajs@1.0.1` `dist/mcp-app.html`
  under `tests/fixtures/apps/` with its sha256 asserted and adds a hand-written probe View
  for the negative paths. **Assumption:** the package licence is permissive (MIT, as the
  `ext-apps` repository); F7-06a checks it and, if not, ships only the probe View.
  Fetching the tarball needs network access once.
- **Both eras.** The Apps fixture runs modern and legacy (extension in `initialize` for
  legacy, per-request for modern — REQ-APP-007).
- **Coverage (Art. 7).** `core.apps` counts toward core ≥ 90 %; the strict REQ gate gains
  `APP` in F7-07a.
- **Dependencies.** None new at runtime (`h11` already is one). The `e2e` job installs the
  Firefox and WebKit browsers (F7-06b). Fixtures stay under `tests/`.
- **Batch ownership.** Batch U owns `core/apps/*`, `core/invoker.py`,
  `core/handlers/capabilities.py`, `core/host.py`, `core/connection/{manager,supervisor}.py`,
  `tests/unit/core/apps/*`, `tests/unit/core/test_invoker_app_scope.py`,
  `tests/integration/{apps/*,test_invoker_app_scope.py}`,
  `tests/integration/connection/test_capabilities.py`, `tests/fixtures/servers/apps_server.py`,
  `tests/fixtures/apps/*`, `tests/contract/test_mcp_sdk_surface.py`. Batch V owns
  `ui/proxy/*`, `ui/install.py`, `tests/unit/ui/proxy/*`,
  `tests/integration/ui/test_proxy_lifespan.py` and the `[tool.importlinter]` table.
  Batch W owns `ui/{apps_bridge,state,models,actions,__init__}.py`,
  `ui/components/{mcp_app_frame,catalog_browser}.py`, `ui/assets/*`, their tests and
  `tests/contract/test_reflex_surface.py`; F7-05 extends `ui/install.py` after V. Batch X
  owns `tests/e2e/*`, `.github/workflows/ci.yml`, `scripts/check_wheel.py`,
  `CHANGELOG.md`, `README.md` and the `pyproject.toml` classifier.
- **Owner actions.** F7-07b (publish `0.1.0b1`) needs the same PyPI prerequisites as F5-06b.
  CI evidence (matrix, `e2e` with three engines, `release.yml` PR run) needs a push / PR.
  Real Safari (Plan R-8) is a manual macOS pass before `0.1.0` (Phase 8), not a Phase 7 gate.

### F7-01 · `core.apps` constants, MIME and CSP, extension advertisement, log seam
- **Status**: [ ] (follows folded delta D3, D5, D7, D8)
- **What**: (1) `core/apps/constants.py`: `EXTENSION_ID = "io.modelcontextprotocol/ui"`,
  `MIME_TYPE = "text/html;profile=mcp-app"`, `is_app_mime(mime) -> bool` (media-type parse:
  `text/html` case-insensitive + parameter `profile=mcp-app`, other parameters ignored),
  limits (`MAX_RESOURCE_BYTES = 5 * 2**20`, `MAX_MESSAGE_BYTES = 2**20`,
  `MAX_INFLIGHT = 4`, `TEARDOWN_TIMEOUT_S = 2.0`, height 80…4,000 px), `app_visible(entry)`.
  (2) `core/apps/csp.py`: `build_csp(meta) -> CspResult(policy, external, dropped)` per API
  §7: entry grammar (`https://` origin, `wss://` only in `connectDomains`, public DNS host,
  optional leading `*.` and port; no path/query/userinfo/quotes/whitespace/`;`/`,`; no IP
  literal, `localhost`, `*.localhost`, bare `*`, scheme-only), ≤ 32 entries × 253 chars, the
  pinned policy string (never `'unsafe-eval'`). (3) `core/apps/resource.py`:
  `select_view_html(read_result, uri) -> ViewResource | NotRendered(reason)` (matching
  `contents` item, MIME, `text` or UTF-8 `blob`, ≤ 5 MiB; reasons `missing_content`,
  `mime_mismatch`, `invalid_encoding`, `too_large`). (4) `CapabilityPlan.mcp_apps`; when set
  the Client gets `extensions` from `mcp.client.advertise(EXTENSION_ID, {"mimeTypes":
  [MIME_TYPE]})`; `Host.set_mcp_apps(enabled)` (default off) recomputes plans like
  `set_prompter` (modern rebuild, legacy at `reconnect()`). (5)
  `ConnectionManager.append_log(server_id, entry)`: redaction filter, ring buffer,
  `server.log` event, `UnknownServerError`.
- **REQ**: REQ-APP-002 (CSP part), REQ-APP-007, REQ-APP-011 (`external` flag), REQ-APP-013
  (resource checks), REQ-APP-014, REQ-APP-012 (seam) · DD-012, API §4.1, §4.5, §7
- **Files**: `src/reflex_mcphost/core/apps/{__init__,constants,csp,resource}.py`,
  `src/reflex_mcphost/core/handlers/capabilities.py`, `src/reflex_mcphost/core/host.py`,
  `src/reflex_mcphost/core/connection/{manager,supervisor}.py`,
  `tests/unit/core/apps/{__init__,test_constants,test_csp,test_resource}.py`,
  `tests/unit/core/connection/test_append_log.py`,
  `tests/integration/connection/test_capabilities.py`, `tests/contract/test_mcp_sdk_surface.py`
- **Depends on**: F2-07, F4-05 (done)
- **Done**: `test_csp.py`: the default policy equals the API §7 string exactly; negatives
  (`*`, `https:`, `'unsafe-eval'`, `data:`, `http://cdn.example.com`, `https://127.0.0.1`,
  `https://[::1]`, `https://localhost`, `https://a.localhost`, `https://x.example/p`,
  `https://u@x.example`, `https://x.example;script-src *`, `wss://` outside
  `connectDomains`, an entry with a space) each land in `dropped`; positives
  (`https://cdn.example.com`, `https://*.example.com:8443`, `wss://rt.example.com` in
  connect) appear in the right directives and set `external`; Hypothesis: for arbitrary
  string lists the policy has exactly the 11 pinned directives, no `'unsafe-eval'` and every
  source token is either a pinned keyword or a valid entry. `test_resource.py`: MIME
  variants (`TEXT/HTML; profile=mcp-app; charset=utf-8` ok, `text/html` alone rejected),
  `text` and `blob`, invalid UTF-8, exactly 5 MiB ok / +1 byte `too_large`, no matching
  item. `test_capabilities.py` (modern and legacy fixtures): the extension is declared iff
  `set_mcp_apps(True)`; modern sees a flag change on the next request, legacy after
  `reconnect()`. `test_append_log.py`: a seeded secret is masked, `server.log` published,
  unknown Server → `UnknownServerError`. SDK contract: `advertise()` result accepted by
  `Client(extensions=…)`.

### F7-08 · Invoker App-scope step (pipeline step 1a)
- **Status**: [ ] (follows folded delta D1)
- **What**: in `core/invoker.py`, after step 1 (resolve) and only for `consumer.kind ==
  app`: the FQN's `server_id` must equal `consumer.name` and `app_visible(entry)` must hold;
  otherwise conclude `denied` with `InvocationError(code="app_scope", details={"reason":
  "foreign_server" | "not_app_visible"})`, no consent evaluation, one audit record
  (`decision_source = not_evaluated`, `error_code = app_scope`). Human and agent Consumers
  are unaffected (the Toolset's `model` filter stays in F5-02).
- **REQ**: REQ-APP-004, REQ-APP-008 (App part; completes the F5-02 partial row) · Art. 3,
  API §4.8, DM §2.3
- **Files**: `src/reflex_mcphost/core/invoker.py`, `tests/unit/core/test_invoker_app_scope.py`,
  `tests/integration/test_invoker_app_scope.py`
- **Depends on**: F3-08 (done), F7-01 (`app_visible`)
- **Done**: unit (fake prompter recording calls): `Consumer.app("a")` → `b::t` →
  `denied` / `foreign_server`; → `a::model_only` → `denied` / `not_app_visible`; the prompter
  is never called; exactly one finished audit record with the fields above; `a::app_only`
  and `a::both` → normal pipeline (consent prompt names `app:a`); `human:ui` and
  `agent:x` calls of the same tools behave as before. Integration (modern stdio fixture with
  `_meta.ui.visibility` variants): same matrix against the real Server; the denied calls
  never reach it (fixture call counter).

### F7-02 · Pure-Python bridge state machine
- **Status**: [ ] (follows folded delta D5, D9, D10)
- **What**: `core/apps/messages.py` (JSON-RPC 2.0 shape validation and limits per API §7)
  and `core/apps/bridge.py`: `AppBridge(server_id, view_id, seed, host_info, host_context,
  csp, ports)` where `seed` = arguments + final `InvocationResult` (outcome, content,
  structured content) and `ports` = async callables `call_tool(name, arguments)`,
  `read_resource(uri)`, `confirm_link(url) -> bool`, `log(LogEntry)`, `send(message_json)`.
  `receive(raw)` implements: `sandbox-proxy-ready` → one `sandbox-resource-ready` (a second
  one is ignored and logged); before the `ui/initialize` reply only `ui/initialize` / `ping`
  (`-32600` "not initialized" otherwise); `initialized` → `tool-input` then `tool-result`
  (`success` / `tool_error`) or `tool-cancelled {reason}` (`cancelled` / `timeout` /
  `connection_lost`), each once; the API §7 method table incl. `ui/open-link`
  (`is_openable_url`-equivalent check in core — `http`/`https` with a host, ≤ 2,048 chars —
  then `confirm_link`), `size-changed` clamp, `notifications/message` (4 KiB); the App
  `tools/call` reply table (`app_scope` → `-32602`, other non-success → `isError` with the
  `ToolCallOutcome.text`); ≤ 4 in-flight requests (`-32000`); `log` gets `method → outcome`
  per View RPC. `teardown(reason)` sends `ui/resource-teardown`, waits ≤ 2 s, then cancels
  in-flight requests (own task group). Never raises out of `receive`.
- **REQ**: REQ-APP-003, REQ-APP-005, REQ-APP-006, REQ-APP-009, REQ-APP-010, REQ-APP-012,
  REQ-APP-015 (bridge side) · API §7
- **Files**: `src/reflex_mcphost/core/apps/{messages,bridge}.py`,
  `tests/unit/core/apps/{test_messages,test_bridge_handshake,test_bridge_methods,test_bridge_limits,test_bridge_teardown}.py`,
  `tests/security/test_redaction.py` (extended: `mcp-app` log entries)
- **Depends on**: F7-01, F7-08 (`app_scope` code)
- **Done**: zero messages sent before `initialized`; `tool-input` precedes `tool-result`,
  each exactly once (success and `tool_error` → `isError: true`); the three cancellation
  outcomes → `tool-cancelled` with the outcome as `reason` and no `tool-result`; every
  method-table row has a test; malformed inputs (not JSON, array batch, missing `jsonrpc`,
  method > 128 chars, > 1 MiB, bad `id` type) get the specified code and the bridge still
  answers `ping`; the 5th concurrent request → `-32000`; non-openable link → `-32000`
  without calling `confirm_link`; declined link → `-32000`; log entries never contain a
  seeded argument, result or HTML marker; teardown: request with the reason, response
  within 2 s → done, no response → proceeds at 2 s, an in-flight `call_tool` observes
  cancellation. Hypothesis: random message sequences never raise and never cause a send
  before `initialized`. Core coverage ≥ 90 % holds.

### F7-06a · Apps fixture Server, vendored View and bridge integration
- **Status**: [ ] (follows folded delta D1, D2, D5)
- **What**: (1) `tests/fixtures/apps/`: `basic-vanillajs-1.0.1.mcp-app.html` extracted from
  the `@modelcontextprotocol/server-basic-vanillajs@1.0.1` tarball (npm shasum
  `b693334d2febcbaf8377dd8f48eab5009312dcfa`, file sha256 recorded in S-3), `NOTICE.md`
  (source, version, licence — checked, see Assumption), and a hand-written `probe.html`
  (no dependencies) that implements the View side and exposes hooks for the negative paths
  (call an app-only / model-only tool, `resources/read`, `ui/open-link`, storage and parent
  probes, records `tool-input` / `tool-result` / `tool-cancelled` / teardown). (2)
  `tests/fixtures/servers/apps_server.py` (modern SDK; legacy via the existing isolated
  pre-2026 SDK pattern): tools `get-time` (`ui://basic`), `probe` (`ui://probe`),
  `app_only_echo` (`["app"]`), `model_only_echo` (`["model"]`), `slow` (sleeps; for cancel /
  timeout), `client_caps` (reports whether the extension was declared); resources
  `ui://basic`, `ui://probe`, `ui://wrong-mime`, `ui://too-big` (5 MiB + 1), `ui://bad-csp`
  (valid + invalid entries), `ui://external` (one valid external domain). (3)
  `tests/integration/apps/test_bridge_fixture.py`: real Host, both eras, core
  `AppBridge` with ports bound to the real Host (`Invoker.call` with `Consumer.app`,
  `catalog.read_resource`, `connections.append_log`), scripted View messages.
- **REQ**: REQ-APP-001 (resource path), REQ-APP-004, REQ-APP-007, REQ-APP-008,
  REQ-APP-012, REQ-APP-013, REQ-APP-014 · Art. 1, Art. 7
- **Files**: `tests/fixtures/servers/apps_server.py`,
  `tests/fixtures/apps/{basic-vanillajs-1.0.1.mcp-app.html,probe.html,NOTICE.md}`,
  `tests/integration/apps/{__init__,test_bridge_fixture}.py`, `tests/conftest.py` (fixture
  registration only)
- **Depends on**: F7-02
- **Done**: on modern and legacy stdio: Catalog entries carry `ui_resource_uri` and
  `visibility`; `select_view_html` accepts `ui://basic` and returns each REQ-APP-013 reason
  for the bad resources; `build_csp` on `ui://bad-csp` drops the invalid entries and the
  log stream records them; a scripted `tools/call app_only_echo` reaches the Server through
  the Invoker with a consent prompt for `app:apps` (fake human prompter allows) and one
  audit record; `model_only_echo` → `-32602`, one `denied` / `app_scope` record, the Server
  never called; `client_caps` reports the extension only after `set_mcp_apps(True)`; the
  vendored file's sha256 is asserted; `check_wheel.py` still passes (no `tests/` in the
  wheel).

### F7-03 [P] · Sandbox proxy listener, `proxy.html` and the origin-pair check
- **Status**: [ ] (follows folded delta D6; parallel with F7-08 / F7-02 / F7-06a once F7-01
  has merged `Host.set_mcp_apps`)
- **What**: (1) `ui/proxy/server.py`: `SandboxProxy` (async context manager) — `h11` over
  `anyio`, one ephemeral port bound on `127.0.0.1` and `::1` (`IPV6_V6ONLY`; v4 port 0, then
  v6 on that port; ≤ 5 attempts), serving rules of TD DD-012 (`GET /proxy` only, `Host ==
  localhost:<port>`, headers, no cookie, no CSP header, head ≤ 8 KiB, 10 s read deadline,
  `Connection: close`, `view` = `[A-Za-z0-9_-]{1,64}` else `400`); `origin` property. (2)
  `ui/proxy/proxy.html` (package data via `importlib.resources`): S-3 proxy logic with the
  pinned UI origin injected server-side as JSON; parent messages only from `window.parent` +
  that origin; one `sandbox-resource-ready` accepted; inner iframe `sandbox="allow-scripts"`,
  `allow=""`, CSP `<meta>` first in `<head>`, `srcdoc`; View messages only from
  `inner.contentWindow`; posts to the View with `"*"`, to the parent with the pinned origin.
  (3) `check_origin_pair(resolve=…)`: `localhost` via `AF_INET` and `AF_INET6`
  (`anyio.to_thread`), every resolved address loopback. (4) `ui/install.py` lifespan: remote
  mode → Apps disabled (`remote_mode`); else check → start the proxy →
  `host.set_mcp_apps(True)` → `async with host`; `HostRuntime.apps`; failure of the check or
  the bind → disabled with its reason, WARNING, the Host still starts; the proxy closes after
  `aclose()`. (5) import-linter: `reflex_mcphost.ui.proxy` ↛ `reflex`, `reflex_base`.
- **REQ**: REQ-APP-002, REQ-APP-007 (enable path), REQ-APP-011 (`allow=""`) · DD-012,
  API §7, §8 `HostRuntime`
- **Files**: `src/reflex_mcphost/ui/proxy/{__init__,server}.py`,
  `src/reflex_mcphost/ui/proxy/proxy.html`, `src/reflex_mcphost/ui/install.py`,
  `pyproject.toml` (`[tool.importlinter]` contract; package-data include if hatch needs it),
  `tests/unit/ui/proxy/{__init__,test_server,test_origin_pair}.py`,
  `tests/integration/ui/test_proxy_lifespan.py`
- **Depends on**: F6-01 (done), F7-01 (`Host.set_mcp_apps`)
- **Done**: over real sockets on both `127.0.0.1` and `::1` with `Host: localhost:P`: `200`,
  the four headers, no `Set-Cookie`, no `Content-Security-Policy`, the body carries the
  JSON-encoded UI origin and never echoes the query; `Host: 127.0.0.1:P`, `evil.example:P`
  or missing → `403`; other path → `404`; `POST` → `405`; oversized head → refused and closed;
  idle client closed at the deadline (short deadline parameter in the test); bad `view` →
  `400`; a taken port triggers the retry. Origin pair: a fake resolver returning a
  non-loopback `AF_INET6` address → disabled `localhost_not_loopback`; one family failing to
  resolve is tolerated. Lifespan (fake Host): `set_mcp_apps(True)` before `start()` when
  enabled; remote-mode `deploy_url` → never called, reason `remote_mode`; bind failure →
  `proxy_bind_failed`, Host started; the proxy port is closed after the lifespan exits.
  import-linter green with the new contract.

### F7-04 · JS relay custom component and `mcp_app_frame()`
- **Status**: [ ] (follows folded delta D2, D4, D5)
- **What**: (1) `ui/assets/mcp_app_relay.js` + a Reflex custom component `McpAppRelay`
  (props `view_id`, `proxy_src`, `proxy_origin`, `outbox`, `height`, `border`; events
  `on_message(view_id, data)`, `on_ack(view_id, seq)`): creates the proxy iframe
  (`sandbox="allow-scripts allow-same-origin"`, `allow=""`), captures `proxyWindow`, accepts a
  message only if `event.source === proxyWindow && event.origin === proxy_origin`, forwards
  its JSON string (dropped if > 1 MiB), posts new outbox entries with target
  `proxy_origin` and acknowledges their `seq`; never writes message content to the DOM, never
  evaluates it. The Reflex local-JS-component mechanism it uses is pinned in the Reflex
  contract test. (2) `ui/models.py`: `should_render(result, entry, consumer)` per D2 (pure),
  `app_view_row(...)` (notices as plain text: disabled reason, not-rendered reason, external
  domains and dropped entries; open-link confirmation text). (3) `ui/actions.py`:
  `open_view(host, entry, result)` → `catalog.read_resource(…, consumer=human:ui)` →
  `select_view_html` → `build_csp` → a view seed or a not-rendered reason (`read_failed` on a
  read error); Reflex-free. (4) `ui/components/mcp_app_frame.py`: `mcp_app_frame()` renders
  the relay or a notice; `catalog_browser` embeds it in the result panel.
- **REQ**: REQ-APP-001, REQ-APP-003 (relay filter), REQ-APP-011 (notice), REQ-APP-013
  (notice) · DD-012, API §7, §8
- **Files**: `src/reflex_mcphost/ui/assets/mcp_app_relay.js`,
  `src/reflex_mcphost/ui/components/{mcp_app_frame,catalog_browser}.py`,
  `src/reflex_mcphost/ui/{models,actions}.py`,
  `tests/unit/ui/{test_app_models,test_open_view,test_mcp_app_frame,test_relay_static}.py`,
  `tests/contract/test_reflex_surface.py`
- **Depends on**: F7-02, F7-03
- **Done**: `should_render` table over every outcome × consent (`allowed` / denied / `None`) ×
  Consumer kind (only `human:ui` with an allowed consent and the five outcomes render);
  `open_view` returns each REQ-APP-013 reason with a fake Catalog; notices render as plain
  text (a `<b>` in a declared domain or URL appears literally); `test_relay_static.py`: the
  JS contains both identity and origin checks and no `innerHTML`, `outerHTML`,
  `insertAdjacentHTML`, `document.write`, `eval`, `Function(`, `setTimeout(` with a string,
  and an AST scan of `ui/` finds no `rx.call_script` / `rx.script` fed from message or
  resource data; the component compiles in a test `rx.App`.

### F7-05 · `ui.apps_bridge`: per-tab Views, App calls, teardown
- **Status**: [ ] (follows folded delta D1, D2, D9, D10)
- **What**: `ui/apps_bridge.py`: process-local registry `{client_token: ViewSession}` (one
  View per tab) holding a core `AppBridge`, its outbox and pending link confirmation; ports:
  `call_tool` → `host.invoker.call("<server_id>::<name>", args,
  consumer=Consumer.app(server_id))`, `read_resource` → `host.catalog.read_resource(server_id,
  uri, consumer=Consumer.app(server_id))` capped at 5 MiB, `log` → `host.connections.append_log`,
  `confirm_link` → a confirmation in the frame answered by Approve / Decline, then
  `rx.redirect(url, is_external=True)` (as Phase 6 does for elicitation URLs; F7-06b checks
  the new tab has no `opener`, else a delta). `HostState` handlers `on_app_message`,
  `on_app_ack`, `close_app`; the F6-05 invoke flow calls `should_render` / `open_view` when a
  result arrives and replaces the previous View (`replaced`); the watcher tears down on
  `registry.changed` removing the Server (`server_removed`) and drops the session on tab
  disconnect; the lifespan tears every View down (`host_closing`, ≤ 2 s overall) before
  `aclose()`. `ui.__all__` gains `mcp_app_frame`.
- **REQ**: REQ-APP-004, REQ-APP-008, REQ-APP-010, REQ-APP-015, REQ-UI-007 (export) · Art. 3,
  API §7, §8
- **Files**: `src/reflex_mcphost/ui/{apps_bridge,state,actions,install,__init__}.py`,
  `tests/unit/ui/test_apps_bridge.py`, `tests/integration/ui/test_apps_bridge.py`,
  `tests/unit/ui/test_public_ui_api.py`, `tests/integration/ui/test_embedding.py`
- **Depends on**: F7-04, F7-06a (fixture)
- **Done**: integration (real Host, `apps_server` modern and legacy, real `ReflexPrompter`):
  a UI invoke of `get-time` that is allowed creates a session whose outbox gets
  `sandbox-resource-ready` only after a scripted `sandbox-proxy-ready`; a denied invoke
  creates none; a scripted App `tools/call app_only_echo` raises a consent card in the
  `PromptRegistry` showing `app:apps`, approve → reply to the View, audit Consumer
  `app:apps`; each teardown reason produces `ui/resource-teardown` with it; a second invoke
  replaces the View; removing the Server from the registry tears it down; tab disconnect
  drops the session; open-link approve → redirect, decline → `-32000`; the Art. 3 AST test
  stays green; `ui.__all__` equals the API §8 names (now incl. `mcp_app_frame`); the
  embedding test renders `mcp_app_frame()` in a foreign app.

### F7-06b · Playwright E2E on Chromium, Firefox and WebKit
- **Status**: [ ] (follows folded delta D2–D6, D9, D10)
- **What**: `tests/e2e/test_mcp_apps.py`, parametrized over the three engines, against the
  F6-09a run fixture with a config that adds `apps_server` as an own Server: the S-3
  contract assertions 1–7 and 9 (iframe attributes and CSP `<meta>`; handshake order and
  counts; App button → exactly one `Invoker.call` with `app:apps` through a consent card and
  one audit record; View `self.origin === "null"`, `localStorage` throws, `document.cookie`
  never contains the UI cookie, `parent.document` / `top.document` throw; proxy frame sees
  no UI cookie or storage and its request carried none; forged messages from a sibling
  iframe on the proxy origin, a popup via `window.opener` and the View posting to `top` never
  reach the Invoker and a forged `sandbox-proxy-ready` yields no second resource; the UI via
  `http://localhost:<port>` → 403 + notice, no App; only `script-src eval` CSP console
  errors tolerated); plus REQ-APP-013/014 notices (`ui://too-big`, `ui://bad-csp`), the
  app-hidden call (`model_only_echo` → `-32602` in the probe, one `app_scope` record), the
  `slow` tool cancelled → the probe records `tool-cancelled {reason: "cancelled"}`, a second
  invocation → the probe records `ui/resource-teardown {reason: "replaced"}`, `ui/open-link`
  (`javascript:` refused; `https://example.com` shows the confirmation and opens a tab
  without `opener` on approve). S-3 #8 (a `::1`-only listener) is covered by F7-03's socket
  tests; here the dual-stack proxy renders in all engines. CI `e2e` job installs
  `chromium firefox webkit` (`--with-deps`). The stale docstring of
  `tests/contract/test_reflex_surface.py` (#6 / #8 done in Phase 6) is corrected.
- **REQ**: REQ-APP-001, -002, -003, -004, -006, -008, -009, -010, -011, -013, -014, -015
  (E2E side) · Art. 5, TD §8, spike S-3
- **Files**: `tests/e2e/test_mcp_apps.py`, `tests/e2e/conftest.py` (apps config),
  `.github/workflows/ci.yml` (`e2e` browser install), `tests/contract/test_reflex_surface.py`
  (docstring only)
- **Depends on**: F7-05, F7-06a
- **Done**: green locally on Linux on all three engines (if WebKit lacks host libraries, use
  the S-3 workaround and record it; not a reason to skip) and in the `e2e` CI job; failures
  in isolation or forgery assertions stop the phase for a Delta Spec — never a relaxed
  assertion.

### F7-07a · `0.1.0b1` release preparation (no publish)
- **Status**: [ ]
- **What**: `scripts/check_wheel.py` requires `reflex_mcphost/ui/proxy/proxy.html` and
  `reflex_mcphost/ui/assets/mcp_app_relay.js` in the wheel (negative test); classifier
  `Development Status :: 4 - Beta`; `CHANGELOG.md` `[Unreleased]` MCP Apps entries and known
  limits (Views only for UI-started invocations, rendered after completion; View
  self-navigation exfiltration is a residual risk, TD §6.1; real Safari unverified, Plan R-8;
  S-4 real-AS CIMD open; Apps disabled in remote mode); README MCP Apps section and progress;
  CI strict REQ gate `--families REG,CON,CAT,CNS,INV,HSH,AUTH,TLS,UI,APP`. The version stays
  `0.1.0.dev0`; the bump happens in F7-07b's release PR.
- **REQ**: — · TD §9.1, DD-015, Art. 7, Art. 9
- **Files**: `scripts/check_wheel.py`, `tests/scripts/test_check_wheel.py`, `pyproject.toml`
  (classifier only), `CHANGELOG.md`, `README.md`, `.github/workflows/ci.yml` (gate families)
- **Depends on**: F7-06b
- **Done**: `uv build` + `check_wheel.py` OK and the missing-asset negative fails with a named
  problem; `check_req_coverage.py --strict --families …,UI,APP` passes (every REQ-APP MUST
  cited; REQ-APP-007 is `optional` and cited too); coverage gates hold; the `release.yml`
  PR run is green once pushed (owner, or the agent on request).

### F7-07b · Tag `v0.1.0b1` and publish to PyPI — OWNER ACTION
- **Status**: [!] blocked — owner action. Needs the F5-06b prerequisites (PyPI Trusted
  Publisher for `release.yml` + environment `pypi`; environment with the owner as required
  reviewer, tag protection for `v*`) and the owner's explicit approval of this release.
  Owner decision: publish `0.1.0a1` first (F5-06b) or go straight to `0.1.0b1`.
- **What**: (1) Release PR `develop` → `main` (agent may prepare it on request): version
  `0.1.0b1`, `uv lock`, `CHANGELOG.md` `## [0.1.0b1] - <date>` (UI, CLI, MCP Apps; known
  limits from F7-07a). (2) Owner merges, pushes tag `v0.1.0b1`, approves the `pypi`
  deployment; `publish` uploads the artifacts built by `build`. (3) GitHub Release.
  Rollback: yank and release `0.1.0b2`; never delete and re-upload.
- **REQ**: — · DD-015, TD §9.1, Plan Phase 7 Done
- **Files**: `pyproject.toml` (version), `uv.lock`, `CHANGELOG.md`; tag `v0.1.0b1`
- **Depends on**: F7-07a, owner prerequisites above
- **Done**: PyPI lists `0.1.0b1`; on Linux `uvx --from 'reflex-mcphost==0.1.0b1'
  reflex-mcphost --version` prints `0.1.0b1` and `run --no-browser` serves `/ping` (the Plan
  Phase 6 objective "from PyPI"); the published wheel passes `check_wheel.py --expect-version
  0.1.0b1`; Plan §2 PyPI row ☑; execution-log row written.

---

## Execution batches

| Batch | Tasks | Review focus |
|---|---|---|
| A | F0-01, F0-02, F0-06 | SDK assumptions in DD-001/002 and adapter shapes still hold |
| B | F0-03, F0-05, F0-04 | Isolation and packaging assumptions (DD-012, DD-016); any Delta Spec needed |
| C | F1-01, F1-02, F1-03, F1-06 | Layout, tooling and contracts match the specs before code accumulates |
| D | F1-04, F1-05, F1-07, F1-09 | CI matrix, gates, redaction design |
| E | F1-08, F1-10, F1-11 | Schema fidelity to the Data Model; fixtures adequate for Phase 2 |
| F | F2-01, F2-02, F2-03, F2-04 | Config formats and precedence match RF-REG; third-party files untouched |
| G | F2-05a, F2-05b, F2-06, F2-07 | Secret handling, trust fail-closed, supervisor state machine vs API §4.5 |
| H | F2-08, F2-09, F2-10 | Era cache and legacy HTTP rely on the SDK, not re-implementations; shutdown budget |
| I | F2-11, F2-12, F2-13, F2-14 | Catalog semantics, watcher backoff, REQ-coverage gate strict for REG/CON/CAT |
| J | F3-01…F3-05 | Evaluation algorithm matches API §4.7 step by step; fail-closed paths |
| K | F3-06, F3-07 [P with J] | Audit integrity (one record per attempt), bounded validation |
| L | F3-08…F3-11 | Pipeline order, deadlines excluding prompter wait, MRTR loop, single effect path |
| M | F4-01, F4-02, F4-03 [P], F4-04 [P], F4-05 | One `Prompter` seam on both eras; refusal channels per era (S-1); capabilities declared exactly (DD-003); legacy attribution and DD-004 legacy deadline; `core/host.py` wired in F4-05 |
| N [P with M] | F4-10 [P], F4-06, F4-09a, F4-07, F4-08 | SDK `OAuthClientProvider` reused, not re-implemented (Art. 2); loopback listener bound to `127.0.0.1`; `state`/`iss` checked before redemption; no SDK message text in errors (Art. 5); `core/host.py` wired in F4-08 |
| N′ | F4-09b (after M and N) | Auth interop on both eras; full secret-seeded redaction test; strict REQ gate for HSH/AUTH |
| O | F5-01, F5-02, F5-03 [P], F5-04 [P], F5-05 | `Toolset` only reaches `Invoker.call` (Art. 3); `core.toolset` placement keeps `core` free of upward imports (D1); alias property test incl. the D2 adversarial case; denial and vanished alias arrive as tool errors in both frameworks, LangGraph graph never raises; `AbstractToolset` / `BaseTool` subclasses, not `StructuredTool` (Art. 2); no framework imported by `reflex_mcphost`/`toolset`; strict REQ gate for TLS |
| P | F5-06a | Release workflow builds once, checks wheel + sdist, dry-runs `uv publish`, smoke-installs the wheel on 3 OS; `publish` job only on `v*` tags in environment `pypi` with job-level `id-token: write`; nothing uploaded |
| P′ | F5-06b — **owner action** | Trusted Publisher + `pypi` environment configured by the owner; explicit release approval; tag `v0.1.0a1`; installed-from-PyPI check |
| Q | F6-01, F6-02, F6-03 | Host lifespan is an async context manager (start/aclose in one task, no 5 s cap — D1); guard accepts exactly one origin from `deploy_url`, 403 body names it, websockets closed before accept; `ui.models` imports no `reflex`; watcher started from `on_load`, de-duplicated per token outside Reflex state; `PromptRegistry` covers every prompt kind, first answer wins, entries removed on answer/timeout/cancel; human decisions reach core only as `Prompter` return values |
| R | F6-04 [P], F6-05, F6-07 [P], F6-06 | UI actions call only public core methods; invocations through `Invoker.start` with `Consumer.human()`; keys-only rendering, no token / authorization URL in state or logs; Server text as plain text; bounded lists (≤ 200 rows); deprecated features labelled; unattributed requests offer two answers |
| S [P with R] | F6-08 | CLI rules and exit codes (wildcard host refused, pre-flight → 3); DD-016 environment contract; prod single port, `GRANIAN_WORKERS=1`; atomic workspace; console script resolvable and smoke-tested from the wheel; `servers` imports no `reflex` |
| T | F6-09a, F6-09b | Headless smoke from the built wheel (403 matrix, SIGINT shutdown, no site-packages writes); embeddability; strict REQ gate incl. `UI`; Playwright flows; REQ-NFR-003/004 measured, never relaxed silently |
| U | F7-01, F7-08, F7-02, F7-06a | `core.apps` pure (no Reflex, no I/O, constants not from `mcp.server`); CSP grammar + property test, pinned policy without `'unsafe-eval'`; extension declared only via `set_mcp_apps`, both eras; App scope enforced and audited in the Invoker (one `denied` / `app_scope` record, no prompt); nothing sent to a View before `initialized`; limits and reply mapping per API §7; log entries carry method → outcome only; real fixture on both eras |
| V [P with U, after F7-01] | F7-03 | Proxy on `localhost:<port>` bound on both stacks, `Host` checked, `GET /proxy` only, no cookie, no CSP header, UI origin pinned server-side; origin-pair check in the lifespan; Apps disabled (Host still starts) on failure or in remote mode; `ui.proxy` Reflex-free contract |
| W | F7-04, F7-05 | Relay is data-only (no script or DOM from Server content), filters source **and** origin; Views only for allowed UI invocations, one per tab; App calls only through `Invoker.call` with `Consumer.app`; consent card shows `app:<server_id>`; teardown reasons; `ui/open-link` confirmation; `ui.__all__` complete |
| X | F7-06b, F7-07a | S-3 contract assertions on Chromium, Firefox, WebKit (isolation and forgery negatives never relaxed); REQ-APP-013/014 notices; cancel and teardown seen by the View; wheel carries the proxy/relay assets; strict REQ gate incl. `APP`; changelog known limits |
| X′ | F7-07b — **owner action** | PyPI prerequisites (as F5-06b), explicit release approval, tag `v0.1.0b1`, installed-from-PyPI check |

## Traceability matrix (Phases 0–2)

| REQ | Tasks | Tests that cite it |
|---|---|---|
| REQ-REG-012 | F1-06 | `tests/unit/core/test_types.py` |
| REQ-CAT-002 | F1-06 (identifier type; catalog use in F2-11) | `tests/unit/core/test_types.py` |
| REQ-CNS-015 | F1-06 (Consumer type; evaluation in F3-01) | `tests/unit/core/test_types.py` |
| REQ-REG-007 | F1-07 (logging part; UI/errors in F2-02, F6-04) | `tests/unit/core/test_logging.py` |
| REQ-NFR-010 | F1-07 (logging part), F4-09b (full test) | `tests/unit/core/test_logging.py`, `tests/security/test_redaction.py` (planned) |
| REQ-NFR-012 | F1-08 (DB), F4-06 (token file), F4-09b | `tests/unit/core/test_db.py`, `tests/unit/core/auth/test_token_store.py` (planned) |
| REQ-NFR-030 | F1-04 | CI matrix (workflow, not a pytest) |
| REQ-NFR-040 | F1-07 | `tests/unit/core/test_logging.py` |
| REQ-REG-001 | F2-03, F2-10 | `tests/unit/core/test_registry.py`, `tests/integration/test_host.py` |
| REQ-REG-002 | F2-02 | `tests/unit/core/config/test_parsers.py` |
| REQ-REG-003 | F2-03 | `tests/unit/core/test_registry.py` |
| REQ-REG-004, REQ-REG-005 | F2-01, F2-02 | `tests/unit/core/config/test_expand.py`, `test_parsers.py` |
| REQ-REG-006 | F2-02 | `tests/unit/core/config/test_parsers.py` |
| REQ-REG-008 | F2-03, F2-04, F2-14 | `test_registry*.py`, `third_party_files` fixture |
| REQ-REG-009, REQ-REG-010, REQ-REG-013 | F2-04, F2-10 | `tests/unit/core/test_registry_mutations.py`, `tests/integration/test_host.py` |
| REQ-REG-011 | F2-05b | `tests/unit/core/test_trust.py` |
| REQ-REG-014 | F2-02, F2-05b | `test_parsers.py`, `test_trust.py` |
| REQ-CON-001, -002, -004…-006, -010, -011 | F2-07, F2-10, F2-14 | `tests/integration/connection/test_supervisor.py`, `tests/interop/test_era_matrix.py` |
| REQ-CON-003 | F2-07 (none declared), F4-05 (Phase 4) | `test_supervisor.py` |
| REQ-CON-007, -012, -016, -017, -018 | F2-06 | `tests/integration/connection/test_transports.py` |
| REQ-CON-008, REQ-CON-009 | F2-09 | `tests/integration/connection/test_shutdown.py` |
| REQ-CON-013 (COULD), -014, -015, -019 | F2-08 | `test_era_cache.py`, `tests/interop/test_legacy_http.py` |
| REQ-CAT-001, -002, -004, -008, -009, -011, -014 | F2-11 | `tests/unit/core/test_catalog.py`, `tests/integration/test_catalog_refresh.py` |
| REQ-CAT-003, -012, -013 (SHOULD) | F2-12 | `tests/integration/test_catalog_watch.py` |
| REQ-CAT-005…-007, -010 (COULD) | F2-13 | `tests/integration/test_catalog_reads.py` |
| REQ-AUTH-009 | F2-06 (static headers), F4-07 (no provider), F4-09b | `test_transports.py`, `tests/security/test_redaction.py` (planned) |
| REQ-HSH-046 | F1-06 (`InputRequestError` type), F3-10 (Phase 3, planned) | — |

### Phase 4 rows (planned tests)

| REQ | Tasks | Tests that will cite it |
|---|---|---|
| REQ-HSH-001, -002, -003, -005 | F4-02, F4-05 (legacy) | `tests/unit/core/handlers/test_sampling.py`, `tests/integration/handlers/test_sampling.py`, `test_legacy_requests.py` |
| REQ-HSH-004 (SHOULD) | F4-02 | `tests/integration/handlers/test_sampling.py` |
| REQ-HSH-006, REQ-HSH-014, REQ-CON-003 | F4-02/F4-01 (handler side), F4-05 | `tests/integration/connection/test_capabilities.py` |
| REQ-HSH-010…013 | F4-01, F4-05 (legacy) | `tests/unit/core/handlers/test_elicitation.py`, `tests/integration/handlers/test_elicitation.py`, `test_legacy_requests.py` |
| REQ-HSH-015 (SHOULD) | F4-01 | `tests/integration/handlers/test_elicitation.py` |
| REQ-HSH-020, -021 (SHOULD), REQ-HSH-022 | F4-03, F4-05 | `tests/unit/core/handlers/test_roots.py`, `tests/integration/handlers/test_roots.py`, `test_legacy_requests.py` |
| REQ-HSH-030, -031 (SHOULD) | F4-04, F4-05 | `tests/unit/core/handlers/test_logging.py`, `tests/integration/handlers/test_logging.py` |
| REQ-HSH-045, REQ-HSH-047 (legacy path) | F4-05 | `tests/integration/handlers/test_legacy_requests.py`, `tests/interop/test_handlers_era_matrix.py` |
| REQ-AUTH-001, -002, -006, -010 | F4-07 | `tests/integration/auth/test_authorize.py`, `tests/interop/test_oauth.py` |
| REQ-AUTH-003, -005, -007 | F4-08 | `tests/integration/auth/test_{registration,refresh,sign_out}.py` |
| REQ-AUTH-004, REQ-AUTH-011 | F4-06, F4-08 | `tests/unit/core/auth/test_token_store.py`, `test_registration.py` |
| REQ-AUTH-008 | F4-07, F4-09b | `test_authorize.py`, `tests/security/test_redaction.py` |
| REQ-AUTH-012 | F4-10 (document); live URL at F8-07 | `tests/unit/test_cimd_document.py` |
| REQ-NFR-020 (provider part) | F4-02 (rest F8-03) | `tests/unit/core/handlers/test_sampling.py` |

F4-09a cites no REQ (test infrastructure). Real-AS CIMD acceptance (F0-04, S-4) is not a
test and stays an open release item.

### Phase 5 rows (planned tests)

| REQ | Tasks | Tests that will cite it |
|---|---|---|
| REQ-TLS-001 | F5-01 | `tests/unit/toolset/test_toolset.py`, `tests/integration/toolset/test_toolset.py` |
| REQ-TLS-002 | F5-01 | `tests/unit/toolset/test_aliases.py` (Hypothesis property + D2 adversarial case) |
| REQ-TLS-003 | F5-02 | `tests/unit/toolset/test_filters.py` |
| REQ-TLS-004 | F5-01, F5-03 (per-step re-listing), F5-04 (snapshot rebuild) | `test_toolset.py` (unit + integration), `tests/contract/test_adapter_surfaces.py` (S-6 #8, #17) |
| REQ-TLS-005 | F5-02, F5-03, F5-04 | `tests/unit/toolset/test_outcomes.py`, `tests/integration/toolset/test_consent.py`, `tests/integration/adapters/test_{pydantic_ai,langgraph}_agent.py`, `test_adapter_surfaces.py` (S-6 #7, #14, #16) |
| REQ-TLS-006 | F5-03 | `tests/unit/adapters/test_pydantic_ai.py`, `tests/integration/adapters/test_pydantic_ai_agent.py`, `test_adapter_surfaces.py` (S-6 #7–9) |
| REQ-TLS-007 | F5-04 | `tests/unit/adapters/test_langgraph.py`, `tests/integration/adapters/test_langgraph_agent.py`, `test_adapter_surfaces.py` (S-6 #13–17) |
| REQ-TLS-008 | F5-05 (source tree), F5-06a (installed wheel smoke) | `tests/architecture/test_no_framework_import.py` |
| REQ-APP-008 (toolset part; App part in F7-05) | F5-02 | `tests/unit/toolset/test_toolset.py`, `tests/integration/toolset/test_consent.py` |
| REQ-NFR-010 (Toolset path) | F5-02 (extends F4-09b) | `tests/unit/toolset/test_outcomes.py`, `tests/security/test_redaction.py` |

F5-06a and F5-06b cite no REQ (release infrastructure: DD-015, TD §9.1); F5-06a is
verified by `tests/scripts/test_check_wheel.py` and the `release.yml` PR run, F5-06b by
the installed-from-PyPI check. REQ-APP-008 stays partially covered until F7-05.

### Phase 6 rows (planned tests)

| REQ | Tasks | Tests that will cite it |
|---|---|---|
| REQ-UI-001 | F6-08, F6-09a | `tests/unit/app/test_cli.py`, `tests/e2e/test_smoke.py` |
| REQ-UI-002 | F6-08, F6-09a | `tests/unit/app/test_cli.py`, `tests/security/test_loopback_guard.py` (remote origin), `tests/e2e/test_smoke.py` |
| REQ-UI-003 | F6-04 | `tests/unit/ui/test_servers_panel.py`, `tests/integration/ui/test_servers_panel.py` |
| REQ-UI-004 | F6-05, F6-09b | `tests/unit/ui/{test_schema_form,test_coerce,test_catalog_browser}.py`, `tests/integration/ui/test_invoke.py`, `tests/e2e/test_ui_flows.py` |
| REQ-UI-005 | F6-03, F6-06, F6-09b | `tests/unit/ui/{test_prompt_registry,test_reflex_prompter,test_consent_center,test_prompt_cards}.py`, `tests/integration/ui/{test_prompts,test_handlers_ui}.py`, `tests/e2e/test_ui_flows.py` |
| REQ-UI-006 | F6-07 | `tests/unit/ui/test_permissions_audit.py`, `tests/integration/ui/test_permissions_audit.py` |
| REQ-UI-007 | F6-01, F6-02, F6-09a | `tests/unit/ui/test_install.py`, `tests/integration/ui/test_embedding.py`, `tests/unit/ui/test_public_ui_api.py` |
| REQ-UI-008 (SHOULD) | F6-08 | `tests/unit/app/test_servers_cmd.py` |
| REQ-NFR-003 | F6-09b (final run F8-02) | `tests/e2e/test_nfr.py` |
| REQ-NFR-004 | F6-02/F6-05 (logic), F6-09b (measurement; final F8-02) | `tests/unit/ui/test_models.py` (informational), `tests/e2e/test_nfr.py` |
| REQ-NFR-011 | F6-01, F6-08, F6-09a | `tests/security/test_loopback_guard.py`, `tests/e2e/test_smoke.py` |
| REQ-NFR-010 (UI view models) | F6-02 (extends F4-09b) | `tests/security/test_redaction.py`, `tests/unit/ui/test_models.py` |
| REQ-CON-009 (lifespan budget) | F6-01, F6-09a | `tests/integration/ui/test_lifespan.py`, `tests/e2e/test_smoke.py` |
| REQ-CON-010, REQ-REG-007 (UI side) | F6-02, F6-04 | `tests/unit/ui/{test_watcher,test_models}.py`, `tests/integration/ui/test_watcher.py` |
| REQ-REG-010, REQ-REG-013 (UI side) | F6-04 | `tests/integration/ui/test_servers_panel.py` |
| REQ-REG-011 (trust UI) | F6-03 | `tests/integration/ui/test_prompts.py` |
| REQ-CNS-002, -004, -005, -006, -007, -009 (UI side) | F6-03 | `tests/unit/ui/test_reflex_prompter.py`, `tests/integration/ui/test_prompts.py` |
| REQ-CNS-013, REQ-INV-009, REQ-INV-011 (UI side) | F6-07 | `tests/integration/ui/test_permissions_audit.py` |
| REQ-INV-006, REQ-CAT-005…007, REQ-CAT-011 (UI side) | F6-05 | `tests/integration/ui/test_invoke.py`, `tests/unit/ui/test_catalog_browser.py` |
| REQ-HSH-002, -010, -011, -013, -047 (UI side); -004, -015 (SHOULD) | F6-03, F6-06 | `tests/unit/ui/test_prompt_cards.py`, `tests/integration/ui/test_handlers_ui.py` |
| REQ-HSH-030, -031 (SHOULD), REQ-AUTH-002, -007, -008 (UI side) | F6-04 | `tests/integration/ui/test_servers_panel.py` |

The UI-side rows re-cite requirements already covered in Phases 2–4 (their families are
in the strict gate); the Phase 6 tests add the UI path, not first coverage. REQ-NFR-003/004
stay open until F6-09b and are re-measured in F8-02; the NFR family enters the strict gate
in F8-04.

### Phase 7 rows (planned tests)

| REQ | Tasks | Tests that will cite it |
|---|---|---|
| REQ-APP-001 | F7-04, F7-06a, F7-05, F7-06b | `tests/unit/ui/{test_app_models,test_open_view}.py`, `tests/integration/apps/test_bridge_fixture.py`, `tests/integration/ui/test_apps_bridge.py`, `tests/e2e/test_mcp_apps.py` |
| REQ-APP-002 | F7-01 (CSP), F7-03 (proxy), F7-06b | `tests/unit/core/apps/test_csp.py`, `tests/unit/ui/proxy/test_server.py`, `tests/integration/ui/test_proxy_lifespan.py`, `test_mcp_apps.py` |
| REQ-APP-003 | F7-02, F7-04 (relay), F7-06b | `tests/unit/core/apps/{test_messages,test_bridge_handshake}.py`, `tests/unit/ui/test_relay_static.py`, `test_mcp_apps.py` |
| REQ-APP-004 | F7-08, F7-06a, F7-05, F7-06b | `tests/unit/core/test_invoker_app_scope.py`, `tests/integration/test_invoker_app_scope.py`, `test_bridge_fixture.py`, `test_apps_bridge.py`, `test_mcp_apps.py` |
| REQ-APP-005 | F7-02 | `tests/unit/core/apps/{test_messages,test_bridge_methods,test_bridge_limits}.py` |
| REQ-APP-006 | F7-02, F7-06b | `test_bridge_handshake.py`, `test_mcp_apps.py` |
| REQ-APP-007 (optional) | F7-01, F7-03, F7-06a | `tests/integration/connection/test_capabilities.py`, `test_proxy_lifespan.py`, `test_bridge_fixture.py` |
| REQ-APP-008 (App part; toolset part F5-02) | F7-08, F7-06a, F7-05, F7-06b | `test_invoker_app_scope.py` (unit + integration), `test_bridge_fixture.py`, `test_apps_bridge.py`, `test_mcp_apps.py` |
| REQ-APP-009 | F7-02, F7-06b | `test_bridge_handshake.py`, `test_mcp_apps.py` |
| REQ-APP-010 | F7-02, F7-05, F7-06b | `tests/unit/core/apps/test_bridge_teardown.py`, `test_apps_bridge.py`, `test_mcp_apps.py` |
| REQ-APP-011 | F7-01, F7-03, F7-04, F7-06b | `test_csp.py`, `test_server.py`, `tests/unit/ui/test_mcp_app_frame.py`, `test_mcp_apps.py` |
| REQ-APP-012 | F7-01 (seam), F7-02, F7-06a | `tests/unit/core/connection/test_append_log.py`, `test_bridge_methods.py`, `test_bridge_fixture.py`, `tests/security/test_redaction.py` |
| REQ-APP-013 | F7-01, F7-04, F7-06a, F7-06b | `tests/unit/core/apps/test_resource.py`, `test_open_view.py`, `test_bridge_fixture.py`, `test_mcp_apps.py` |
| REQ-APP-014 | F7-01, F7-06a, F7-06b | `test_csp.py`, `test_bridge_fixture.py`, `test_mcp_apps.py` |
| REQ-APP-015 | F7-02, F7-05, F7-06b | `test_bridge_methods.py`, `test_apps_bridge.py`, `test_mcp_apps.py` |
| REQ-HSH-030 (View `notifications/message`) | F7-02 | `test_bridge_methods.py` |
| REQ-UI-007 (`mcp_app_frame` export) | F7-05 | `tests/unit/ui/test_public_ui_api.py`, `tests/integration/ui/test_embedding.py` |
| REQ-NFR-010 (`mcp-app` log entries) | F7-02 (extends F4-09b) | `tests/security/test_redaction.py` |

F7-03 and F7-07a carry DD-012 / TD §9.1 items besides their REQs; F7-07b cites no REQ
(release, verified by the installed-from-PyPI check). The strict REQ gate gains `APP` in
F7-07a. REQ-APP-008 is fully covered once F7-08 is done.

Partially covered REQs above stay open in the matrix until their completing task is done.
Spike tasks (F0-*) inform REQs but cite none as verified — they produce no shipped tests.
No SHOULD requirement is deferred by Phases 0–2. COULD items REQ-CON-013 and REQ-CAT-010
may be deferred inside their task's estimate; a deferral is recorded in the task status.

## Execution log

| Date | Tasks | Result | Notes |
|---|---|---|---|
| 2026-10-31 | F0-01, F0-02, F0-06 (Batch A) | ✅ done | S-1: DD-001 confirmed (modern); legacy input-request attribution, legacy sampling-deny and DD-004 legacy timeout need a Delta Spec. S-2: DD-002 confirmed; exit detection needs a transport wrapper (~6 ms), SDK stdio shutdown waits are 2 s/2 s vs REQ-CON-008 5 s/5 s. S-6: API §4.13 holds; LangChain adapter must be a `BaseTool` subclass. Delta Spec pending owner decision. |
| 2026-10-31 | F0-03, F0-05 (Batch B) | ✅ done | S-3: origin pair holds in 3 engines; proxy/View messaging and CSP rules pinned; View self-navigation is a residual risk. S-5: single-port prod mode, `on_load` watcher, 403-body notice. Delta `2026-10-spike-batch-b` folded (auto-approved). F0-04 still blocked. |
| 2026-10-31 | F1-01…F1-11 (Batches C–E) | ✅ done | 127 tests green locally (Linux, Python 3.13): unit, integration (4 fixture Servers, both eras), contract (SDK, adapters, Reflex), architecture. ruff/mypy strict/import-linter clean; wheel check OK. CI matrix (3 OS × 2 Python) not yet run — no push. Coverage and REQ-coverage gates report-only. |
| 2026-10-31 | F2-01…F2-14 (Batches F–I) | ✅ done | 464 tests green (unit, integration, interop both eras, contract, architecture); core coverage 97 %; strict REQ gate REG/CON/CAT 44/44. Security reviews folded as deltas `2026-10-project-trust` and `2026-10-phase2-impl`. REQ-CON-013 (COULD) deferred; known REQ-CON-008 gap → F8-08. |
| 2026-10-31 | F3-01…F3-11 (Batches J–L) | ✅ done | Phase 3 complete 2026-10-31; delta `2026-10-phase3-impl` folded (API 1.7, TD 1.6, DM 1.5). 725 tests collected; strict REQ gate REG/CON/CAT/CNS/INV. Security review: definition changes suspend only `allow` tool rules. Deferred to Phase 4: REQ-HSH-045 legacy attribution / DD-004 legacy extension, real elicitation/sampling/roots handlers. Phase 8: regex ReDoS (pattern cap only), definition TOCTOU after consent (OQ-1); `format` not enforced. |
| 2026-10-31 | F4-01…F4-08, F4-09a, F4-10 (Batches M, N) | ✅ done | Phase 4 batches M and N complete 2026-10-31 (merge `2dcb2c2`); delta `2026-10-phase4-impl` folded (PRD 1.7, API 1.9, TD 1.8, DM 1.7). Security review: authentic-callback-only loopback listener (REQ-AUTH-006/010 reworded), OAuth entry bound to resource + issuer, tokens withheld until discovery. F4-09b (batch N′) still open. Known gaps: S-4 real-AS CIMD (F0-04 → F8-07); owner to enable GitHub Pages (source GitHub Actions); token expiry unknown after restart; no test for 401-while-ready refresh success; `ResourceWarning`s (unclosed sqlite) in the full suite; REQ-NFR-020 remainder → F8-03. |
| 2026-11-01 | F5-01…F5-05 (Batch O), F5-06a (Batch P) | ✅ done (F5-06a `[~]`) | Phase 5 batch O and F5-06a complete 2026-11-01 (`dbdc23a`); F5-06b owner action pending; delta `2026-10-phase5-impl` folded (API 1.11, TD 1.11). Toolset lifecycle checked on every `call()`; both adapters turn a vanished alias into a tool error; docs snippets machine-checked against the integration tests. Release path hardened: actions SHA-pinned in every workflow, Dependabot weekly grouped updates, `uv` pinned in `publish`. Open: F5-06a PR run of `release.yml` (needs a push/PR); F5-06b (Trusted Publisher, `pypi` environment, release approval, tag `v0.1.0a1` — owner); deviation 5 (`KeyError` masking in the adapters) → consider narrowing in Phase 8. |
| 2026-11-01 | F6-01…F6-03 (Batch Q) | ✅ done | Batch Q complete 2026-11-01 (`dec371a`): `ui.install` async-context-manager lifespan, `HostRuntime` (`starting` / `running` / `stopped`, one lifespan per process), Host/Origin guard (fail closed with `503` until the origin is resolved, repeated headers refused, websocket `1008`; `deploy_url` without port, non-`http` or wildcard refused), view models, `HostState` watcher, `PromptRegistry` (ask joins on the request id) and `consent_center`; contract `ui.models` Reflex-free. Deviations folded as delta `2026-11-phase6-impl` items 1–9 (API 1.13, TD 1.13). Open: re-prompt continuity of D2 not implemented (removed + added, gap G-5). |
| 2026-11-01 | F6-04…F6-07 (Batch R) | ✅ done | Batch R complete 2026-11-01 (`97443dc`): `servers_panel`, `schema_form` + `catalog_browser` + invoke flow, elicitation and sampling consent / review cards, `permissions_table`, `audit_table` (shows `args_hash`); actions in Reflex-free `ui/actions.py`. Security review (defense in depth): Server-provided URLs opened only if `http`/`https` with a host (`is_openable_url`); a refused elicitation URL → decline, a refused authorization URL → notice only. Delta `2026-11-phase6-impl` items 22–31. Deferred: media only as allow-listed `data:` types ≤ 8 MiB (G-6), Permissions view not live (G-7), refused auth URL waits for the core deadline (G-8, F8-01), deprecated log-level select untested (G-9, F8-04); browser behaviour → F6-09b. |
| 2026-11-01 | F6-08 (Batch S) | ✅ done (F6-08 `[~]`) | CLI `run` / `servers` / `--version`, workspace materialization (marker + atomic rename) and the packaged reference app (`97443dc`): child env = inherited minus `REFLEX_MCPHOST_*` and Reflex run-mode switches plus the DD-016 set; child in its own process group, Ctrl-C forwarded once, SIGTERM/SIGHUP → SIGTERM, 30 s grace; `--host` rules (wildcard, `localhost`, malformed → 2); `servers` invalid own config → 3. `release.yml` smoke gains `--version` and `servers --json`; `check_wheel.py` requires the app files. Two Windows test fixes (`b0b8ac2`, `20c477b`: trust-card paths in platform form). Delta items 10–21. Open: `release.yml` PR run (G-1); Windows Ctrl-C forwarding untested (G-3); old workspaces not pruned (G-4). |
| 2026-11-01 | F6-09a, F6-09b (Batch T) | ✅ done | `59967db`: headless smoke from the built wheel (`uv tool run`, Linux-only), embedding test, `ui.__all__` = API §8 + `HostRuntime`, `HostState` (API §8 amended), Playwright flows, strict REQ gate incl. `UI` (120/120). Two UI defects found in the browser and fixed: Catalog list never rendered (computed vars read rows via `getattr`; explicit `deps`), card forms captured the `rx.foreach` variable (hidden card-id field). **REQ-NFR-003:** warm start 3.52–4.65 s across runs (budget < 10 s, every one of 5 runs). **REQ-NFR-004:** first missed — p95 ≈ 347 ms (Reflex's 300 ms input debounce); fixed with a 50 ms filter debounce (Catalog, Servers, Permissions; Audit on blur) — p95 105.0 ms, median 83.8, max 110 (budget p95 < 200 ms, PRD target unchanged), in-page timing, filter keystrokes only (G-10 → F8-02). Delta items 32–40. CI run for `59967db` (push to `develop`) fully green: lint, build, test matrix (ubuntu/macos/windows × 3.11/3.13) and the new `e2e` job — F6-09a and F6-09b `[x]` (G-2 closed). |

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
| 1.4 | 2026-10-31 | Ernesto Crespo | Phase 2 tasks F2-01…F2-14 (F2-05 split into F2-05a secret store + HMAC key, F2-05b trust store); batches F–I; traceability for REG/CON/CAT. Approved under owner's auto-approval |
| 1.5 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-project-trust` (F2-05b trust covers HTTP project definitions) |
| 1.6 | 2026-10-31 | Ernesto Crespo | Phase 2 done (F2-01…F2-14); folded delta `2026-10-phase2-impl` |
| 1.7 | 2026-10-31 | Ernesto Crespo | Phase 3 tasks F3-01…F3-11, batches J–L (auto-approved per owner instruction) |
| 1.8 | 2026-10-31 | Ernesto Crespo | Phase 3 done (F3-01…F3-11); folded delta `2026-10-phase3-impl` |
| 1.9 | 2026-10-31 | Ernesto Crespo | Phase 4 tasks F4-01…F4-10 (F4-09 split into F4-09a fixture AS, moved before F4-07, and F4-09b interop + redaction), batches M/N/N′, Phase 4 traceability rows; F0-04 re-scoped to gate F8-07 instead of F4-07/F4-08. Delta `2026-10-phase4-prep` approved and folded the same day (PRD 1.6, API 1.8, TD 1.7, DM 1.6, Plan 1.5) |
| 1.10 | 2026-10-31 | Ernesto Crespo | Phase 4 batches M and N done (F4-01…F4-08, F4-09a, F4-10; merge `2dcb2c2`); folded delta `2026-10-phase4-impl` (PRD 1.7, API 1.9, TD 1.8, DM 1.7); execution-log row. F4-09b status unchanged |
| 1.11 | 2026-10-31 | Ernesto Crespo | Phase 5 tasks F5-01…F5-05, F5-06a (release workflow, no publish) and F5-06b (tag + PyPI publish, owner action, blocked); batches O/P/P′; Phase 5 traceability rows. Written against delta `2026-10-phase5-prep`, approved (owner's standing auto-approval) and folded the same day (API 1.10, TD 1.10, Plan 1.6; archived under `changes/_archive/`); Analyze `specs/analyze/2026-10-31-mcphost-v0.1-phase5.md` PASS |
| 1.12 | 2026-11-01 | Ernesto Crespo | Phase 5 batch O and F5-06a done (`dbdc23a`); folded delta `2026-10-phase5-impl` (API 1.11, TD 1.11; Plan unaffected); execution-log row. F5-06a stays `[~]` (PR run pending), F5-06b stays `[!]` (owner action) |
| 1.13 | 2026-11-01 | Ernesto Crespo | Phase 6 tasks F6-01…F6-08, F6-09a (headless smoke, embeddability, strict `UI` gate) and F6-09b (Playwright E2E, REQ-NFR-003/004); batches Q/R/S/T; Phase 6 traceability rows. Written against delta `2026-11-phase6-prep`, approved (owner's standing auto-approval) and folded the same day (PRD 1.8, API 1.12, TD 1.12, Plan 1.7; archived under `changes/_archive/`); Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase6.md` PASS after the fold |
| 1.14 | 2026-11-01 | Ernesto Crespo | Phase 6 batches Q, R, S and T implemented (`dec371a`, `97443dc`, `59967db`; Windows test fixes `b0b8ac2`, `20c477b`); folded delta `2026-11-phase6-impl` (API 1.13, TD 1.13; PRD, DM and Plan unaffected); execution-log rows with the REQ-NFR-003/004 measurements. F6-09a and F6-09b `[x]` (CI run for `59967db` on `develop` green, `e2e` job included); F6-08 stays `[~]` (`release.yml` PR run pending) |
| 1.15 | 2026-11-01 | Ernesto Crespo | Phase 7 tasks F7-01…F7-05, F7-06a (fixture + bridge integration), F7-06b (Playwright, three engines), F7-07a (release preparation) and F7-07b (tag + PyPI publish, owner action, blocked), new F7-08 (Invoker App-scope step); batches U/V/W/X/X′; Phase 7 traceability rows. Written against delta `2026-11-phase7-prep`, approved (owner's standing auto-approval) and folded the same day (PRD 1.9, API 1.14, TD 1.14, DM 1.8, Plan 1.8; archived under `changes/_archive/`); Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase7.md` PASS after the fold |
