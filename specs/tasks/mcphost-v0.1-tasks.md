# Tasks — reflex-mcphost v0.1 (Phases 0–2)

> Source specs: `specs/prd/mcphost-v0.1.md` v1.2 · `specs/api/mcphost-api-v0.1.md` v1.2 ·
> `specs/technical/mcphost-architecture.md` v1.1 · `specs/data-model/mcphost-schema.md` v1.1 ·
> `specs/plans/mcphost-v0.1.md` v1.1 · governed by `specs/constitution.md` v1.2
> Plan phases covered: **Phase 0 (spikes)**, **Phase 1 (foundation)** and **Phase 2
> (registry, connection and catalog)** · Generated: 2026-10-31
> Status: `APPROVED` · Version 1.5

Tasks for Phases 3–8 are generated in this same file, one phase at a time, after the
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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
- **Status**: [ ]
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

## Traceability matrix (Phases 0–2)

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
| REQ-AUTH-009 | F2-06 (static headers), F4-09 | `test_transports.py` |
| REQ-HSH-046 | F1-06 (`InputRequestError` type), F3-10 (Phase 3, planned) | — |

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
