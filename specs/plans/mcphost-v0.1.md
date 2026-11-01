# reflex-mcphost v0.1 — Implementation Plan

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `APPROVED` |
| **Version** | 1.13 |
| **Date** | 2026-11-01 |
| **PRD** | `specs/prd/mcphost-v0.1.md` v1.11 |
| **API Spec** | `specs/api/mcphost-api-v0.1.md` v1.18 |
| **Tech Design** | `specs/technical/mcphost-architecture.md` v1.19 |
| **Data Model** | `specs/data-model/mcphost-schema.md` v1.10 |
| **Governed by** | `specs/constitution.md` v1.2 |

---

## 1. Implementation summary

Nine phases. **Phase 0** burns down the six technical unknowns from Tech Design §10
before any production code depends on them. **Phases 1–4** build `reflex_mcphost.core`
bottom-up (foundation → registry/connection/catalog → consent/audit/invoker → host-side
handlers/OAuth), each phase verified against real fixture Servers on both eras and both
transports (Art. 7). **Phase 5** adds the `Toolset` and adapters (planned to ship
`0.1.0a1`, core + toolset, no UI). **Phases 6–7** add the Reflex UI, reference app, CLI and
MCP Apps (planned to ship `0.1.0b1`). **Phase 8** hardens and ships `0.1.0`. Owner decision
2026-11-01 (delta `2026-11-skip-prereleases`): both pre-releases are skipped — the first
PyPI release is `0.1.0` (F8-07b).

Every phase ends with a releasable, CI-green `develop` branch; nothing is merged to `main`
except release tags.

| | |
|---|---|
| **Total estimated effort** | ≈ 90 working days (≈ 18 weeks), estimates already include the ×1.5 realism factor (guide §5) |
| **Team** | 1 maintainer (Ernesto Crespo), AI-agent assisted; `[P]` marks tasks an agent can run in parallel |
| **Planned start** | 2026-11-02 |
| **Target dates** | ~~`0.1.0a1` 2027-02-12~~ · ~~`0.1.0b1` 2027-03-12~~ (both skipped 2026-11-01) · `0.1.0` 2027-03-23 (includes 2 weeks of year-end buffer) |

> **Deviation from PRD §12.** The PRD estimated ~11 weeks (incl. 2 weeks of spec work
> already spent). This plan, decomposed to ≤ 2-day tasks, comes out at ≈ 18 weeks. The
> gap is mainly the dual-era interop matrix, OAuth and MCP Apps isolation, which the PRD
> estimate under-weighted. See §5, risk R-1, for the scope levers if the date matters
> more than scope.

## 2. Prerequisites

| Prerequisite | Owner | Status | Due |
|---|---|---|---|
| Constitution, PRD, API Spec, Tech Design, Data Model approved | Ernesto Crespo | ☑ Done (2026-10-30) | — |
| This plan approved | Ernesto Crespo | ☑ Done (2026-10-31) | — |
| Tasks artifact (`specs/tasks/mcphost-v0.1-tasks.md`) approved for Phases 0–1 | Ernesto Crespo | ☑ Done (2026-10-31) | — |
| Analyze gate passed (`11-CHECKLIST-ANALYZE`) | Ernesto Crespo | ☑ Done (2026-10-31; findings folded via `changes/_archive/2026-10-analyze-fixes`) | — |
| GitHub repo `ecrespo/reflex-mcphost`: Actions enabled, Pages enabled, branch protection on `main` | Ernesto Crespo | ☑ Done 2026-11-01: Actions ☑; Pages ☑ (source "GitHub Actions", custom domain `blog.seraph.to`; docs site and `https://blog.seraph.to/reflex-mcphost/oauth/client.json` live — `200`, `application/json`, no redirect, body = `docs/oauth/client.json`); branch protection ☑ (owner request, 2026-11-01): `main` — pull request required (0 approvals, solo maintainer), required `ci` checks `lint`, `build`, `e2e` and the six `test` matrix jobs, conversations resolved, no force push, no deletion, admins may bypass; `develop` — no force push, no deletion, enforced for admins (direct pushes allowed) | Phase 1 |
| PyPI project `reflex-mcphost` reserved with Trusted Publishing configured and GitHub environment `pypi` (gated F5-06b; since 2026-11-01 gates F8-07b) | Ernesto Crespo | ☑ Done 2026-11-01 (Trusted Publisher used by `publish`; environment `pypi`: required reviewer `ecrespo`, deployment policy tags `v*`; `v*` tag ruleset active; `0.1.0` published, F8-07b) | Phase 8 (F8-07b) |
| Access to ≥ 1 real authorization server advertising `client_id_metadata_document_supported` (spike S-4) | Ernesto Crespo | ⊘ Waived 2026-11-01 (owner decision: none available; S-4 closed with plan B — CIMD offered first, DCR / configured `client_id` fallbacks tested, real-AS CIMD unverified, `spikes/S-4.md`; R-9 accepted) | Phase 0 |
| macOS and Windows machines (or CI runners) for S-3 / S-5 manual checks | Ernesto Crespo | ⊘ Waived 2026-11-01 (owner decision: no macOS / Safari available — the real Safari pass F8-15 is waived, R-8 accepted; macOS and Windows are covered by the CI runners) | Phase 0 |

## 3. Implementation phases

Conventions for every phase:
- Task IDs `F<phase>-<nn>` are stable; the Tasks artifact expands each into
  agent-executable steps and cites the same ID.
- "REQ" lists the PRD criteria a task's tests must cite (Art. 7). Tasks without a REQ
  cite the Tech Design / Data Model section or Constitution article they implement.
- Estimates are in working days (d); no task exceeds 2 d.

---

### Phase 0: Technical spikes

**Duration:** 5 d · **Objective:** answer Tech Design §10 S-1…S-6 with throwaway code.

Spike code lives on `spike/S-n` branches under `spikes/` and is never merged. Each spike
produces a findings note `specs/plans/spikes/S-n.md` (question, what was tried, answer,
impact on DDs). A spike that fails its exit criterion triggers a Delta Spec in `changes/`
**before** the phase that depends on it starts.

| ID | Task | Estimate | Depends on | Unblocks | Status |
|---|---|---|---|---|---|
| F0-01 | S-1: `session.call_tool(..., allow_input_required=True, input_responses=, request_state=)` + progress callback; callback returning `ErrorData` under modern vs legacy | 1 d | — | F1-11, Phase 3 | ☐ |
| F0-02 | S-2: `stdio_client(errlog=…)` accepted types; process-exit detection while parked (< 1 s) | 0.5 d | — | F2-06, F2-07 | ☐ |
| F0-03 | S-3: origin pair `127.0.0.1` UI vs `localhost` proxy; render an `ext-apps` example App and call a tool in Chrome/Firefox/Safari; IPv6 `localhost` | 1.5 d | — | Phase 7 | ✅ |
| F0-04 | S-4: CIMD with loopback redirect against a real AS; DCR fallback | 1 d | AS access | F8-07 (release `0.1.0`); not Phase 4 (fixture AS, F4-09a); no longer a gate since 2026-11-01 | ☑ 2026-11-01 (plan B, owner decision: fallback chain recorded, real-AS CIMD unverified — `spikes/S-4.md`) |
| F0-05 | S-5: `uvx` cold/warm start with workspace materialization on Linux/macOS/Windows | 0.5 d | — | F6-08 | ✅ |
| F0-06 | S-6: PydanticAI 2.64 `AbstractToolset` and LangChain `StructuredTool` with dict `args_schema` | 0.5 d | — | Phase 5 | ☐ |

#### Done criteria
- Six findings notes committed; each states "DD-xxx confirmed" or links a Delta Spec.
- Contract assertions discovered in S-1/S-2/S-6 listed for promotion in F1-11.

---

### Phase 1: Foundation

**Duration:** 10 d · **Objective:** a CI-enforced skeleton where every Constitution gate
already runs, plus the shared core plumbing and the fixture Servers all later tests use.

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F1-01 | Scaffold: `pyproject.toml` (hatchling, src layout, extras, PEP 735 groups), `uv.lock`, `py.typed`, empty package tree per Tech Design §5.1, `CHANGELOG.md` | DD-015, Art. 9 | 0.5 d | Phase 0 | ☐ |
| F1-02 | Quality tooling: ruff, ruff-format, `mypy --strict`, pre-commit (gitleaks, ruff, mypy, import-linter via `uv run`) | Art. 8 | 0.5 d | F1-01 | ☐ |
| F1-03 | import-linter contracts 1–4 + AST single-effect-path test (initially vacuous) | Art. 1/3/4, DD-018 | 1 d | F1-01 | ☐ |
| F1-04 | GitHub Actions: Linux/macOS/Windows × Python 3.11/3.13, `setup-uv` cache, coverage gates (core ≥ 90 %, overall ≥ 80 %) | REQ-NFR-030, Art. 7/8 | 1 d | F1-02 | ☐ |
| F1-05 [P] | `scripts/check_req_coverage.py` (fails on uncited MUST REQ) + wheel-contents check (no `tests/`, no `mcp.server`) | Art. 1/7 | 1 d | F1-04 | ☐ |
| F1-06 [P] | `core.errors`, `core.types` (`ServerId` validation, `Fqn`, `Consumer`, ULID ids) | REQ-REG-012, API §3.3/§4.4 | 1 d | F1-01 | ☐ |
| F1-07 [P] | Logging: `reflex_mcphost.*` logger tree, context `LoggerAdapter`, `SecretRedactionFilter`, `core.secrets` | REQ-NFR-040, REQ-REG-007 | 1 d | F1-06 | ☐ |
| F1-08 | `core.db` (WAL pragmas, single-worker thread limiter, `0600` file) + migration runner + `0001_initial` + schema drift test + `PRAGMA quick_check` refuse-to-start | DM §2/§5, DD-006, REQ-NFR-012 | 1.5 d | F1-06 | ☐ |
| F1-09 [P] | `core.events` EventBus with drop-oldest + `events.dropped` | DD-014, API §4.11 | 0.5 d | F1-06 | ☐ |
| F1-10 | Fixture Servers under `tests/fixtures/servers/`: `modern_server.py` (stdio + HTTP under uvicorn on ephemeral ports), `legacy_server.py` (`uv run --isolated --with "mcp>=1.30,<2"`); pytest fixtures | Art. 7, Tech Design §8 | 1.5 d | F1-04 | ☐ |
| F1-11 | SDK contract tests (`Client(...)` kwargs, `session.call_tool`, callback signatures, `advertise`) from spike findings | DD-001, Tech Design §8 | 0.5 d | F1-10 | ☐ |

#### Done criteria
- CI green on all 6 matrix cells; import-linter, gitleaks and REQ-coverage script run (the
  latter in report-only mode until Phase 2 adds the first cited tests).
- `0001_initial` applies to an empty DB; drift test passes; DB file mode is `0600` on POSIX.
- A test connects raw `mcp.Client` to each fixture Server over stdio and HTTP, both eras.

---

### Phase 2: Registry, connection and catalog

**Duration:** 18 d · **Objective:** the Host discovers Servers from all config sources,
keeps one supervised Client per Server alive across both eras and transports, and
maintains a live catalog.

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F2-01 | Config paths per OS + `${VAR}` / `${VAR:-default}` expander, unsupported forms | REQ-REG-004/005, DD-013 | 1 d | F1-06 | ☐ |
| F2-02 [P] | Parsers: own `mcphost.toml`, Claude Desktop, Cursor, VS Code, `.mcp.json` → `ServerDefinition`; malformed file → `ConfigProblem`; project `mcphost.toml` filtered to `[servers.*]` | REQ-REG-002/006/014, API §5 | 1.5 d | F2-01 | ☐ |
| F2-03 | `ServerRegistry` merge: precedence, whole-definition shadowing, problems; byte-identical third-party test | REQ-REG-001/003/008 | 1 d | F2-02 | ☐ |
| F2-04 | Registry mutations: atomic `tomlkit` writes, disable overrides for third-party Servers, reload diff | REQ-REG-009/010/013 | 1.5 d | F2-03 | ☐ |
| F2-05a | `SecretStore` (keyring + `0600` file fallback, single-worker limiter, `secret_index`) + install HMAC key | REQ-NFR-012, DD-007/008, DM §3 | 0.5 d | F1-08 | ☐ |
| F2-05b [P] | Project stdio trust store (`trusted_server`, HMAC fingerprint incl. env values, risky-env flagging) | REQ-REG-011/014, DM §2.4 | 1 d | F2-03, F2-05a | ☐ |
| F2-06 | Transport factory: stdio (command/args/env/cwd, stderr ring buffer), Streamable HTTP headers, static headers, `x-mcp-header` mirroring / invalid-tool drop | REQ-CON-007/012/016/017/018, REQ-AUTH-009 | 1.5 d | F1-10 | ☐ |
| F2-07 | `ServerSupervisor` + `ClientLease`: state machine, backoff with jitter, `failed` after 10, state events, failure isolation | REQ-CON-001/002/004/005/006/010/011, DD-002 | 2 d | F2-06, F1-09 | ☐ |
| F2-08 | Era detection + `EraCache` (`era_cache` table); legacy `Mcp-Session-Id`, 404 re-init, `legacy_sse_fallback`; prune `era_cache` rows of removed Servers on reload (DM §6) | REQ-CON-013/014/015/019, DM §2.5 | 1.5 d | F2-07 | ☐ |
| F2-09 | stdio shutdown sequence (stdin close → 2 s → terminate → 2 s → kill, SDK policy) + Host shutdown ≤ 15 s | REQ-CON-008/009 | 1 d | F2-07 | ☐ |
| F2-10 | `Host` composition root (Flow A) + `ConnectionManager` façade (status, reconnect, logs) | API §4.1/§4.5 | 1 d | F2-04, F2-07 | ☐ |
| F2-11 | `Catalog` snapshots: FQNs, duplicate drop, definition hash, visibility + UI resource URI, stale marking | REQ-CAT-001/002/004/008/009/011/014 | 1.5 d | F2-10 | ☐ |
| F2-12 | Catalog watchers: modern `subscriptions/listen`, legacy `list_changed`, re-listen with backoff, `ttlMs` refresh | REQ-CAT-003/012/013 | 1.5 d | F2-11 | ☐ |
| F2-13 [P] | Reads: `resources/read`, RFC 6570 templates, `prompts/get`, resource subscriptions | REQ-CAT-005/006/007/010 | 1 d | F2-11 | ☐ |
| F2-14 | Interop suite: era matrix (modern↔modern, legacy fallback, no overlap → `failed`, cached-era invalidation), crash isolation | REQ-CON-*, Tech Design §8 | 1 d | F2-08, F2-12 | ☐ |

#### Done criteria
- Every REQ-REG / REQ-CON / REQ-CAT MUST cited by at least one passing test.
- Killing one fixture Server's process leaves the others `ready` (REQ-CON-011).
- Third-party config files hash-identical after the whole Phase 2 suite (REQ-REG-008).

---

### Phase 3: Consent, audit and invoker

**Duration:** 13.5 d · **Objective:** the single effect path — no `tools/call` without a
consent decision and exactly one audit record (Art. 3).

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F3-01 | Consent models + `RuleIndex` evaluation algorithm (rank = target then Consumer specificity, deny wins within rank) with hypothesis property tests | REQ-CNS-003/015, API §4.7, DD-005 | 1.5 d | F1-06 | ☐ |
| F3-02 | `ConsentStore` write-through, programmatic rules, list/edit/revoke live | REQ-CNS-006/013/014, DM §2.2 | 1 d | F3-01, F1-08 | ☐ |
| F3-03 | `ConsentPolicy` prompting: `Prompter` port, `PendingPrompt`, answer→rule mapping, timeout, headless deny, fail-closed paths | REQ-CNS-001/002/004/005/008/009/010 | 1.5 d | F3-02 | ☐ |
| F3-04 [P] | Rug-pull: suspend rules on definition-hash change and re-ask | REQ-CNS-007 | 0.5 d | F3-03, F2-11 | ☐ |
| F3-05 [P] | Argument previews (hooks + default JSON) and secret-like redaction | REQ-CNS-011/012 | 1 d | F3-03 | ☐ |
| F3-06 | `AuditLog`: HMAC key from F2-05a, canonical JSON, `begin`/`finish`, `interrupted` marking at start, filtered queries | REQ-INV-009/010/011/012, DD-007, DM §2.3 | 1.5 d | F1-08, F2-05a | ☐ |
| F3-07 [P] | `core.schema`: bounded Draft 2020-12 validators cached by definition hash; elicitation subset check | DD-010 | 1 d | F1-06 | ☐ |
| F3-08 | `Invoker` pipeline (Flow C steps 1–5, 7–8), outcomes, `map_sdk_error`, concurrency | REQ-INV-001/002/006/007/008/012/013/014 | 2 d | F3-03, F3-06, F3-07, F2-10 | ☐ |
| F3-09 | Deadlines excluding prompter wait; timeout and Consumer cancel per transport; progress forwarding | REQ-INV-003/004/005, DD-004 | 1.5 d | F3-08 | ☐ |
| F3-10 | MRTR loop + `InputRequestRouter` skeleton: round cap 8, undeclared method, opaque `requestState`, consent once, stream break → `connection_lost`; fulfilled-round counting; Consumer `ContextVar` for reads + `InputRequestError` | REQ-HSH-040/041/042/043/044/046, REQ-INV-015 | 1.5 d | F3-08 | ☐ |
| F3-11 | Activate AST single-effect-path test; benchmarks REQ-NFR-001 (10k rules) and REQ-NFR-002 | REQ-NFR-001/002, Art. 3 | 0.5 d | F3-10 | ☐ |

#### Done criteria
- All REQ-CNS / REQ-INV MUSTs and REQ-HSH-040…044 cited by passing tests.
- Fault-injection tests: store unavailable, prompter raises, audit `begin` fails → denied /
  `audit_unavailable`, nothing sent to the Server, exactly one audit record.
- Benchmarks under budget on the Linux CI runner.

---

### Phase 4: Host-side handlers and OAuth

**Duration:** 11 d · **Objective:** elicitation, sampling, roots and logging through one
`Prompter` seam on both eras; OAuth 2.1 for remote Servers.

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F4-01 | Elicitation handler: form (schema subset, validation loop) and url mode | REQ-HSH-010/011/012/013/014/015 | 1.5 d | F3-10 | ☐ |
| F4-02 | Sampling handler (`deprecated` flag): consent(sampling), `SamplingProvider`, optional review, deny paths | REQ-HSH-001/002/003/004/005/006 | 1.5 d | F3-10 | ☐ |
| F4-03 [P] | Roots handler (`deprecated`): explicit `file://` only, legacy `list_changed` | REQ-HSH-020/021/022 | 0.5 d | F3-10 | ☐ |
| F4-04 [P] | Logging (`deprecated`): `notifications/message` + stderr into per-Server stream, set level per era | REQ-HSH-030/031 | 1 d | F2-06 | ☐ |
| F4-05 | Legacy server-initiated requests (in-flight attribution); capability declaration and modern-Client rebuild on prompter/roots/log-level change | REQ-HSH-045, REQ-CON-003, DD-003 | 1 d | F4-01, F4-02, F4-03, F4-04 | ☐ |
| F4-06 [P] | `TokenStore` on the F2-05a `SecretStore`, issuer-keyed | REQ-AUTH-004/011, REQ-NFR-012, DD-008 | 0.5 d | F2-05a | ☐ |
| F4-07 | `LoopbackCallbackServer` (h11) + `authorize()` flow: PKCE S256, `state`, `iss`, 5-min deadline | REQ-AUTH-001/002/006/010, DD-009 | 1.5 d | F4-06, F4-09a, F2-07 | ☐ |
| F4-08 | Registration order (CIMD → DCR → manual `client_id`), refresh-once, sign-out | REQ-AUTH-003/005/007 | 1 d | F4-07 | ☐ |
| F4-09a | Fixture OAuth AS and protected fixtures (runs before F4-07) | Art. 1, Art. 7 | 0.75 d | F1-10, F4-10 | ☐ |
| F4-09b | Auth interop tests (both eras); full secret-seeded redaction test | REQ-AUTH-008, REQ-NFR-010, Art. 5 | 0.75 d | F4-08, F4-05 | ☐ |
| F4-10 [P] | `docs/oauth/client.json` + GitHub Pages deploy job | REQ-AUTH-012 | 0.5 d | F1-04 | ☐ |

#### Done criteria
- All REQ-HSH and REQ-AUTH MUSTs cited by passing tests on both eras.
- Redaction test seeds env values, headers, tokens, codes, verifiers and secret-like
  arguments and finds none in logs, audit rows, SQLite or exception messages.
- `reflex_mcphost.core` coverage ≥ 90 %.

---

### Phase 5: Toolset, adapters and `0.1.0a1`

**Duration:** 5 d · **Objective:** agents can use Host tools through consent; first PyPI
pre-release. Can start after Phase 3 in parallel with Phase 4.

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F5-01 | `Toolset` + `ToolSpec`, alias algorithm (property: deterministic, collision-free, regex-valid), live catalog reads | REQ-TLS-001/002/004 | 1.5 d | F3-08 | ☐ |
| F5-02 | Filters, `model`-visibility exclusion, denial returned as tool error | REQ-TLS-003/005, REQ-APP-008 (toolset part) | 0.5 d | F5-01 | ☐ |
| F5-03 [P] | `adapters.pydantic_ai` | REQ-TLS-006 | 1 d | F5-02 | ☐ |
| F5-04 [P] | `adapters.langgraph` | REQ-TLS-007 | 1 d | F5-02 | ☐ |
| F5-05 | No-framework-import test, public re-exports (API §3.6), library quickstart docs | REQ-TLS-008 | 0.5 d | F5-03, F5-04 | ☐ |
| F5-06a | Release workflow: build once, wheel/sdist checks, `uv publish --dry-run`, install smoke on 3 OS; `publish` job gated on `v*` tag + environment `pypi` | DD-015, Tech Design §9.1 | 0.4 d | F5-05 | ☐ |
| F5-06b | **Owner action**: Trusted Publisher + environment configured, release approved, tag `v0.1.0a1`, publish | DD-015, Tech Design §9.1 | 0.1 d | F5-06a, Phase 4 | ⊘ skipped 2026-11-01 (owner decision: first release is `0.1.0`, F8-07b) |

#### Done criteria
- Example PydanticAI and LangGraph agents call a fixture tool through consent and receive a
  denial as a tool error.
- ~~`0.1.0a1` installable from PyPI~~ (skipped 2026-11-01: no pre-release; PyPI from `0.1.0`, F8-07b); wheel check passes.

---

### Phase 6: Reflex UI, reference app and CLI

**Duration:** 13.5 d · **Objective:** `uvx reflex-mcphost` gives a working local Host UI
(verified from the built wheel in Phase 6; from PyPI once `0.1.0` is published — F8-07b,
owner; the planned pre-releases F5-06b / F7-07b were skipped 2026-11-01).

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F6-01 | `ui.install`: async-context-manager lifespan task, `HostRuntime`, Host/Origin guard middleware (single origin from `deploy_url`) + 403-body notice | DD-011, DD-017, REQ-NFR-011 | 1.5 d | F5-06a | ☐ |
| F6-02 | `HostState` + background watcher, snapshot recompute on events | DD-011, API §8 | 1.5 d | F6-01 | ☐ |
| F6-03 | `ReflexPrompter` + `PromptRegistry` (every prompt kind, change stream, first answer wins) + `consent_center` | REQ-UI-005, REQ-CNS-002 | 1.5 d | F6-02 | ☐ |
| F6-04 [P] | `servers_panel`: state, origin, transport, capabilities, log stream, connect/disconnect/reconnect/authorize/sign-out, enable/disable, reload | REQ-UI-003 | 1.5 d | F6-02 | ☐ |
| F6-05 | `schema_form` + `catalog_browser` + invoke flow | REQ-UI-004 | 2 d | F6-02 | ☐ |
| F6-06 | Elicitation form and sampling review UI | REQ-UI-005, REQ-HSH-010/015/004 | 1 d | F6-03, F6-05 | ☐ |
| F6-07 [P] | `permissions_table` + `audit_table` | REQ-UI-006 | 1 d | F6-02 | ☐ |
| F6-08 | CLI `run` / `servers` / `--version` (single `--port`, prod mode — DD-016), workspace materialization, loopback refusal | REQ-UI-001/002/008, DD-016 | 1.5 d | F6-01 | ☐ |
| F6-09a | Headless smoke: built wheel → `uvx --from <wheel> reflex-mcphost run --no-browser`; guard 403s; `servers --json`; embeddability check (`ui` in a foreign app) | REQ-UI-001/002/007, REQ-NFR-011 | 0.5 d | F6-06, F6-08 | ☐ |
| F6-09b | Playwright E2E: consent → allow → result; elicitation form; NFR-003/004 checks; `playwright` in the `test` group; `e2e` CI job | REQ-NFR-003/004, REQ-UI-004/005 | 1.5 d | F6-09a | ☐ |

#### Done criteria
- All REQ-UI MUSTs cited by passing tests; E2E green on Linux CI.
- Guard middleware returns `403` for foreign `Host`/`Origin` (HTTP and websocket).
- Warm start < 10 s on the GitHub Actions `ubuntu-latest` runner (PRD §7 definition).

---

### Phase 7: MCP Apps and `0.1.0b1`

**Duration:** 10 d · **Objective:** render MCP App Views in an isolated sandbox with
App-originated calls going through consent and audit; prepare `0.1.0b1` (publishing was an
owner action, F7-07b — skipped 2026-11-01, the first release is `0.1.0`).

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F7-01 | `core.apps` constants, MIME parsing, `build_csp` with entry validation, visibility rule; `Host.set_mcp_apps` + `CapabilityPlan.mcp_apps` extension advertisement; `ConnectionManager.append_log` | REQ-APP-002/007/011/013/014, DD-012 | 1 d | F2-07, F4-05 | ☐ |
| F7-08 | Invoker pipeline step 1a: App scope (own Server, `app` visibility) → `denied` / `app_scope`, audited, no prompt | REQ-APP-004/008, Art. 3 | 0.5 d | F3-08, F7-01 | ☐ |
| F7-02 | Pure-Python bridge state machine: handshake order, method table, limits, error replies, tool-input/result/cancelled, App call reply mapping, teardown, logging | REQ-APP-003/005/006/009/010/012/015 | 2 d | F7-01, F7-08 | ☐ |
| F7-06a | `apps_server.py` fixture + vendored ext-apps View + probe View; bridge integration against the real fixture on both eras (no browser) | REQ-APP-001/004/008/012, Art. 7 | 0.5 d | F7-02 | ☐ |
| F7-03 [P] | Sandbox proxy (`h11` listener, `localhost:<proxy_port>` on `127.0.0.1` + `::1`) + `proxy.html`; origin-pair check in the lifespan, Apps disabled on failure / remote mode | REQ-APP-002/011, DD-012 | 1.5 d | F6-01 | ☐ |
| F7-04 | JS relay custom component (data-only outbox, source + origin filter) + `mcp_app_frame()` (render trigger, resource read, MIME/size checks, notices) | REQ-APP-001/003/011/013 | 1.5 d | F7-02, F7-03 | ☐ |
| F7-05 | `ui.apps_bridge`: per-tab View, `Invoker.call` with `Consumer.app(server_id)`, `resources/read`, `ui/open-link` confirmation, teardown triggers; `ui.__all__` gains `mcp_app_frame` | REQ-APP-004/008/010/015 | 1 d | F7-04 | ☐ |
| F7-06b | Playwright E2E on Chromium/Firefox/WebKit: S-3 contract assertions (render, App tool call through consent, cancel, teardown, isolation and forgery negatives, UI via `localhost` → 403 + notice) | Art. 5, Tech Design §8 | 1.5 d | F7-05, F7-06a | ☐ |
| F7-07a | `0.1.0b1` release preparation: changelog, Beta classifier, wheel check for proxy/relay assets, strict REQ gate `APP`, README | Tech Design §9.1 | 0.4 d | F7-06b | ☐ |
| F7-07b | **Owner action**: release approved, tag `v0.1.0b1`, publish | Tech Design §9.1 | 0.1 d | F7-07a, F5-06b prerequisites | ⊘ skipped 2026-11-01 (owner decision: first release is `0.1.0`, F8-07b) |

#### Done criteria
- All REQ-APP MUSTs cited by passing tests; E2E green on three browser engines.
- A View attempting to read the Host UI's storage/cookies or call a foreign/app-hidden tool
  fails (negative E2E tests); the app-hidden attempt leaves exactly one `denied` /
  `app_scope` audit record.
- ~~`0.1.0b1` installable from PyPI (after the owner action F7-07b)~~ (skipped 2026-11-01: PyPI from `0.1.0`, F8-07b).

---

### Phase 8: Hardening and `0.1.0`

**Duration:** 10.5 d · **Objective:** production-ready release. (Plan 1.8 said 5.5 d; it had
not been updated when F8-08 was added in 1.4 — the eight original tasks sum to 6 d. Delta
`2026-11-phase8-prep` adds F8-09…F8-15 and splits F8-07.)

| ID | Task | REQ / ref | Estimate | Depends on | Status |
|---|---|---|---|---|---|
| F8-08 | Close REQ-CON-008 gap: after any stdio transport close (incl. a clean exit), signal what is left of the Server's process group / Job Object (TD DD-002) | REQ-CON-008 | 0.5 d | F2-09 | ☑ 2026-11-01 |
| F8-09 | Invoker step 4a: re-resolve after consent, `definition_changed` / `not_found`, audited | REQ-INV-016, REQ-CNS-007, Art. 3 | 0.5 d | F3-08 | ☑ 2026-11-01 |
| F8-10 | Bounded Server regexes: `regex` dependency, extended `jsonschema` keywords, 50 ms / 250 ms bound | REQ-NFR-021, DD-010 | 1 d | F3-07 | ☑ 2026-11-01 |
| F8-11 [P] | `UnknownToolError(MCPHostError, KeyError)`; adapters convert only it | REQ-TLS-004/006/007 | 0.25 d | F5-03, F5-04 | ☑ 2026-11-01 |
| F8-12 [P] | UI hardening: `Referrer-Policy: no-referrer`, MEMORY state-manager start-up check, refused authorization URL cancels the flow, `consent.rules_changed` + live Permissions view | REQ-UI-006/007, REQ-NFR-011, REQ-APP-015, Art. 5 | 1 d | F6-01, F6-07 | ☑ 2026-11-01 |
| F8-13 [P] | MCP Apps hardening: proxy connection cap (32, `503`), View self-navigation detection (`navigated` discard) | REQ-APP-002/010, Art. 5 | 1 d | F7-03, F7-05 | ☑ 2026-11-01 |
| F8-14 [P] | CLI hardening: workspace pruning, Windows `CTRL_BREAK_EVENT` forwarding test | REQ-UI-001, DD-016 | 0.5 d | F6-08 | ☑ 2026-11-01 |
| F8-01 | Security walk-through of Tech Design §6.1 (one test or note per vector), file modes, gitleaks history scan | Art. 5, REQ-NFR-010/011/012 | 1 d | F8-09, F8-10, F8-12, F8-13 | ☑ 2026-11-01 |
| F8-02 [P] | Final benchmarks REQ-NFR-001…004 per PRD §7 definitions (`ubuntu-latest`; REQ-NFR-004 both interaction kinds), informational on macOS/Windows | REQ-NFR-001/002/003/004 | 1 d | F8-08, F8-09, F8-10, F8-12 | ☑ 2026-11-01 |
| F8-03 [P] | Resilience tests: keyring failure, provider raises, Server crash storms | REQ-NFR-020 | 0.5 d | F8-08 | ☑ 2026-11-01 |
| F8-06 [P] | Re-verify the pinned revision set (Tech Design §0); Delta Spec if any row changed | Art. 2 | 0.5 d | F8-10 | ☑ 2026-11-01 |
| F8-04 | REQ-coverage script strict over all families (NFR included); close gaps (REQ-NFR-030 test, log-level select, `ui.actions` contract, `ResourceWarning` as error); overall coverage ≥ 80 % | Art. 7, REQ-NFR-030 | 1 d | F8-01, F8-02, F8-03 | ☑ 2026-11-01 |
| F8-05 [P] | Docs: README, user guide, library guide (embedder rules), deprecated-feature notes; examples re-run | Art. 2/9 | 1 d | F8-11, F8-12, F8-14 | ☑ 2026-11-01 |
| F8-07a | `0.1.0` release preparation (no tag): `CHANGELOG.md` `[0.1.0]` draft with known limitations, `release.yml` post-publish `verify` job, CIMD document check | REQ-AUTH-012, Art. 9, TD §9.1 | 0.4 d | ☑ 2026-11-01 | ☐ |
| F8-15 | **Owner action**: real Safari manual pass of the S-3 scenarios on macOS (R-8) | Art. 5, R-8 | 0.25 d | F8-13 | ⊘ waived 2026-11-01 (owner decision: no Safari available; Playwright WebKit is the `0.1.0` coverage, R-8 accepted) |
| F8-07b | **Owner action**: PyPI / GitHub prerequisites (formerly F5-06b's; `pypi` environment protection — required reviewer + `v*` tag policy); release PR, tag `v0.1.0`, publish — the first PyPI release; CIMD URL frozen and live. No longer waits on S-4 (closed with plan B) or F8-15 (waived), owner decisions 2026-11-01 | REQ-AUTH-012, Art. 9 | 0.1 d | ☑ 2026-11-01 (`0.1.0` on PyPI; `verify` see Tasks F8-07b) | ☐ |

#### Done criteria
- ☑ No open critical/high findings; all §6.1 vectors covered (test or written note).
- ☑ Strict REQ gate green over **all** families, NFR included; coverage gates hold.
- ☑ `uvx reflex-mcphost` works from PyPI on Linux, macOS and Windows (the `release.yml`
  `verify` job after F8-07b: every verification step green on the three OS, 2026-11-01;
  the Windows job was marked red only by setup-uv's post-job cache save, fixed in
  `release.yml` for later releases).
- ☑ Every spec updated to reflect implementation changes (Art. 9).

---

## 4. Dependency map

```
Phase 0: Spikes (S-1…S-6)
   │
   ▼
Phase 1: Foundation ──▶ Phase 2: Registry/Connection/Catalog ──▶ Phase 3: Consent/Audit/Invoker
                                                                    │
                                              ┌─────────────────────┴───────────────┐
                                              ▼                                     ▼
                                   Phase 4: Handlers + OAuth            Phase 5: Toolset + adapters
                                              │                                     │
                                              └──────────────┬──────────────────────┘
                                                             ▼  (0.1.0a1 skipped)
                                                  Phase 6: UI + app + CLI
                                                             │
                                                             ▼
                                                  Phase 7: MCP Apps  (0.1.0b1 skipped)
                                                             │
                                                             ▼
                                                  Phase 8: Hardening ──▶ 0.1.0

Spike gates:  S-1 → Phase 3 · S-2 → F2-06/07 · S-3 → Phase 7 · S-4 → (none; closed 2026-11-01, plan B) · S-5 → F6-08 · S-6 → Phase 5
```

## 5. Implementation risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R-1 | Schedule ≈ 18 weeks vs PRD's ~9 weeks of build | High | Medium | Scope levers, in order: (1) ship `0.1.0` without MCP Apps (move RF-APP to 0.2 via PRD delta, −10 d); (2) defer legacy SSE fallback REQ-CON-013 (−0.5 d); (3) defer sampling review REQ-HSH-004 | Ernesto Crespo |
| R-2 | `mcp` 2.3.x patch changes a surface DD-001 relies on | Medium | High | Contract tests (F1-11) run on every PR; pin `<2.4`; Delta Spec on break | Ernesto Crespo |
| R-3 | A spike fails its exit criterion | Medium | High | Gate rule (Phase 0): Delta Spec before dependent phase; Phase 0 runs first so re-design costs only spec edits | Ernesto Crespo |
| R-4 | Legacy fixture via `uv run --isolated` flaky on Windows CI | Medium | Medium | Mark interop legacy job as required on Linux only, informational on Windows until stable | Ernesto Crespo |
| R-5 | Reflex 0.10 background-task / lifespan behavior differs from DD-011 assumptions | Medium | High | Prototype the watcher during S-5; F6-01/02 first in Phase 6 | Ernesto Crespo |
| R-6 | Safari/WebKit behavior for the origin pair (S-3) | Medium | Medium | Playwright WebKit in CI; document unsupported browsers if needed (spec delta) | Ernesto Crespo |
| R-7 | Agent-generated code drifts from specs | Medium | Medium | First run of each phase: 3–5 tasks, review, then scale; REQ-cited tests; Analyze before implement | Ernesto Crespo |
| R-8 | Real Safari (CFNetwork/ITP) differs from Playwright WebKit on `localhost`/`127.0.0.1` cookies or IPv6 loopback (S-3) | Low | High | **Accepted 2026-11-01** (owner decision: no macOS / Safari available; F8-15 waived). Coverage for `0.1.0`: Playwright WebKit (WPE) in the CI E2E on three engines; real Safari unverified, a CHANGELOG `[0.1.0]` known limitation. A Safari report or a later manual pass → Delta Spec and patch release | Ernesto Crespo |
| R-9 | No real AS accepts the CIMD document with a portless loopback redirect (S-4 closed with plan B, real AS unverified) | Medium | Medium | **Accepted 2026-11-01** (owner decision, plan B): DCR and configured `client_id` paths (tested in F4-08 / F4-09b against the fixture AS); CHANGELOG `[0.1.0]` known limitation; a real AS later → re-open S-4, fix in a patch release (`spikes/S-4.md`) | Ernesto Crespo |

## 6. Tracking

Single maintainer, so ceremonies are lightweight:

| Ritual | Frequency | Purpose |
|---|---|---|
| Task status update | On every merged task | Tick ☐ → ☑ in this plan and in the Tasks file (any session can resume) |
| Phase review | End of each phase | Check Done criteria; update specs (Art. 9); decide on R-1 levers |
| Spec sync | Whenever implementation contradicts a spec | Delta Spec in `changes/`, folded on approval |

## 7. Definition of Done (global)

- [ ] Code merged to `develop` via PR with Conventional Commit messages
- [ ] Tests passing in CI (unit + integration + interop; e2e where applicable); each test
      docstring cites its REQ IDs
- [ ] Coverage gates met (core ≥ 90 %, overall ≥ 80 %)
- [ ] `ruff`, `mypy --strict`, import-linter, gitleaks green; no unexplained ignores
- [ ] Public symbols typed and documented
- [ ] Affected specs updated (or Delta Spec opened) — Art. 9
- [ ] No known debt without a tracked issue

## Constitution check

- **Art. 1** — F1-03 forbidden contract, F1-05 wheel-contents check, fixtures only under `tests/` (F1-10, F4-09, F7-06a).
- **Art. 2** — Phase 0 verifies SDK surfaces before use; F1-11 contract tests; deprecated handlers flagged (F4-02/03/04); F8-06 re-verifies the pinned set.
- **Art. 3** — Phase 3 is dedicated to the single effect path; F3-11 activates the AST test; fail-closed fault-injection in Phase 3 Done criteria; App calls through Invoker (F7-05, with the App scope enforced and audited in the Invoker — F7-08); consent re-checked against the definition before the call (F8-09).
- **Art. 4** — F1-03 layers contract from day one; adapters as extras (F5-03/04); no-framework-import test and `adapters ↛ core` contract (F5-05).
- **Art. 5** — F1-07, F3-05, F3-06, F4-06, F4-09 redaction test, F7-01 validated CSP, F7-03 origin isolation, F7-04 data-only relay, F8-10 bounded Server regexes, F8-12 / F8-13 UI and MCP Apps hardening, F8-01.
- **Art. 6** — F2-03 byte-identical test; Phase 2 Done criteria.
- **Art. 7** — REQ column on every task; F1-05 coverage script; real fixture Servers on both transports and eras (F1-10, F2-14, F4-09); strict gate over all families in F8-04.
- **Art. 8** — F1-02/F1-04 gates before any feature code; DB behind `anyio.to_thread` (F1-08).
- **Art. 9** — Conventional Commits and spec updates in global DoD; SemVer (the planned a1/b1 pre-releases skipped by owner decision 2026-11-01; first release `0.1.0`).
- **Exceptions requested:** none.

---

## Change history

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1-draft.1 | 2026-10-31 | Ernesto Crespo | Initial draft |
| 1.0 | 2026-10-31 | Ernesto Crespo | Approved |
| 1.1 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-analyze-fixes` (A-04/05/06/07/13/15/25 task scope) |
| 1.2 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-b` (S-3, S-5): F0-03/F0-05 done, F6-08 single port, F7-06 403 check, R-8 |
| 1.3 | 2026-10-31 | Ernesto Crespo | Phase 2 tasking: F2-05 split (F2-05a secret store + HMAC key moved forward from F3-06/F4-06; F4-06 −0.5 d); F2-09 row aligned with REQ-CON-008 2 s/2 s (editorial, was stale since batch A) |
| 1.4 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase2-impl`: F8-08 (process-group cleanup after clean exit) |
| 1.5 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase4-prep` (D9): F4-09 split into F4-09a (before F4-07) / F4-09b; F4-05/F4-07 deps; S-4 gates F8-07 instead of F4-07/08; risk R-9 |
| 1.6 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase5-prep` (D4): F5-06 split into F5-06a (release workflow, no publish) and F5-06b (owner action: tag + publish); F6-01 depends on F5-06a; §2 PyPI prerequisite names the `pypi` environment |
| 1.7 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase6-prep` (D1, D2, D6, D7): F6-01 async-context-manager lifespan + guard origin from `deploy_url`; F6-03 PromptRegistry for every prompt kind; F6-04 enable/disable/reload; F6-09 split into F6-09a (headless smoke, 0.5 d) and F6-09b (Playwright E2E, 1.5 d); Phase 6 13 d → 13.5 d; metadata versions refreshed |
| 1.8 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase7-prep`: Phase 7 table — F7-01 widened (MIME, CSP entry validation, `set_mcp_apps`, `append_log`), new F7-08 (Invoker App-scope step, Art. 3), F7-06 split into F7-06a (fixture + bridge integration) / F7-06b (Playwright, 3 engines), F7-07 split into F7-07a (release prep) / F7-07b (owner action: tag + publish); Phase 7 8.5 d → 10 d; Done criteria (audited app-scope denial, PyPI after F7-07b); Phase 8 tasks depend on F7-07a; R-1 lever −10 d; metadata versions refreshed |
| 1.9 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase8-prep` (D14 and the task impact of D1–D13): Phase 8 table — new F8-09 (Invoker step 4a), F8-10 (bounded Server regexes, `regex` dependency), F8-11 (`UnknownToolError`), F8-12 (UI hardening), F8-13 (MCP Apps hardening), F8-14 (CLI hardening), F8-15 (owner: real Safari); F8-07 split into F8-07a (release preparation) / F8-07b (owner action: S-4, tag, publish); dependencies re-cut for parallel batches; Phase 8 5.5 d (stale; tasks summed to 6 d) → 10.5 d; Done criteria (all-families strict gate, post-publish `verify` job); §4 S-4 → F8-07b; R-8 / R-9 point at F8-15 / F8-07b; Constitution check; metadata versions refreshed |
| 1.10 | 2026-11-01 | Ernesto Crespo | Phase 8 progress (delta `2026-11-phase8-impl` folded): ☑ F8-01…F8-06, F8-08…F8-14 (Tasks `[x]`; F8-04 after the green CI run for `27723f0`); F8-07a stays ☐ (Tasks `[~]`: `release.yml` PR run pending), F8-15 and F8-07b stay ☐ (owner actions); no task, dependency or estimate change; metadata versions refreshed (API 1.17, TD 1.17) |
| 1.11 | 2026-11-01 | Ernesto Crespo | Folded deltas `2026-11-skip-prereleases`, `2026-11-cimd-url` (§2 GitHub row names the CIMD URL on `blog.seraph.to`) and `2026-11-final-gaps`: owner decision — no `0.1.0a1` / `0.1.0b1`, the first PyPI release is `0.1.0`; F5-06b and F7-07b ⊘ skipped (Tasks `[-]`), their Done criteria struck, §1 summary, target dates, §4 diagram and Art. 9 line; §2 PyPI and GitHub prerequisite rows re-stated as of 2026-11-01 (now gate F8-07b); F8-07b row names the moved prerequisites; metadata versions (PRD 1.11, API 1.18, TD 1.18). No estimate or dependency change |
| 1.12 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-safari-s4-decisions` (owner decisions): F8-15 ⊘ waived (no Safari; Playwright WebKit is the `0.1.0` coverage); F0-04 ☑ closed with plan B (`spikes/S-4.md`); F8-07b depends on F8-07a only (row text names the `pypi` environment protection); §2 real-AS and macOS/Windows rows ⊘ waived; §4 spike gates (S-4 no longer gates); R-8 and R-9 accepted; metadata TD 1.19. No estimate change |
| 1.13 | 2026-11-01 | Ernesto Crespo | Release `0.1.0` (F8-07b): §2 PyPI and GitHub rows ☑ (branch protection on `main` and `develop` configured on the owner's request); F8-07a, F8-07b ☑; Phase 8 Done criteria ticked. v0.1 complete. |
