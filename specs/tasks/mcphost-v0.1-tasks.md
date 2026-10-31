# Tasks — reflex-mcphost v0.1 (Phases 0–4)

> Source specs: `specs/prd/mcphost-v0.1.md` v1.2 · `specs/api/mcphost-api-v0.1.md` v1.2 ·
> `specs/technical/mcphost-architecture.md` v1.1 · `specs/data-model/mcphost-schema.md` v1.1 ·
> `specs/plans/mcphost-v0.1.md` v1.1 · governed by `specs/constitution.md` v1.2
> Plan phases covered: **Phase 0 (spikes)**, **Phase 1 (foundation)**, **Phase 2
> (registry, connection and catalog)**, **Phase 3 (consent, audit and invoker)** and
> **Phase 4 (host-side handlers and OAuth)** · Generated: 2026-10-31
> Status: `APPROVED` · Version 1.9 (Phase 4 blocks follow delta
> `changes/_archive/2026-10-phase4-prep`, approved and folded 2026-10-31; no Phase 4 task is
> gated by it any more)

Tasks for Phases 5–8 are generated in this same file, one phase at a time, after the
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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
