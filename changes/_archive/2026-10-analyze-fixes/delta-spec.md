# Delta — Resolve Analyze findings of 2026-10-31

Each entry references the base document and section and the Analyze finding (`A-nn`) it
resolves. Modified REQs keep their IDs; new REQs take new IDs; no ID is removed.

Version bumps on fold: constitution 1.2 · PRD 1.2 · API 1.2 · Tech Design 1.1 ·
Data Model 1.1 · Plan 1.1 · Tasks 1.1.

---

## 1. `specs/constitution.md` → v1.2

### MODIFIED — Art. 3, last sentence (A-09)
- **Before:** "Every invocation, allowed or denied, SHALL produce exactly one `AuditLog`
  record."
- **After:** "Every invocation attempt, allowed or denied, SHALL produce exactly one
  `AuditLog` record. The sole exception is an attempt whose audit record cannot be
  written: it SHALL cause no effect (outcome `audit_unavailable`) and SHALL be logged at
  ERROR."
- **Reason:** the old wording was unsatisfiable for `audit_unavailable` (REQ-INV-012).

### ADDED — Amendments table row
| 2026-10-31 | Art. 3 | Audit-unavailable exception stated explicitly | Analyze 2026-10-31 A-09 (`changes/2026-10-analyze-fixes`) | Ernesto Crespo |

---

## 2. `specs/prd/mcphost-v0.1.md` → v1.2

### MODIFIED — Metadata (A-22)
- "Governed by `specs/constitution.md` v1.0" → "v1.2".

### MODIFIED — RF-REG "Sources, in descending precedence" (A-01, A-25)
- **Before:** five levels; level 3 groups `.vscode/mcp.json` and `.cursor/mcp.json`;
  level 4 groups Cursor and VS Code user configs; `extra` files unplaced.
- **After:** a total order (1 = highest). Within a level there is exactly one file.

  | # | `SourceKind` | File |
  |---|---|---|
  | 1 | `mcphost_project` | `<cwd>/mcphost.toml` (only `schema_version` and `[servers.*]` are read — REQ-REG-014) |
  | 2 | `mcphost_user` | user-level `mcphost.toml` (or `--config PATH`) |
  | 3 | `mcp_json_project` | `<cwd>/.mcp.json` |
  | 4 | `cursor_project` | `<cwd>/.cursor/mcp.json` |
  | 5 | `vscode_project` | `<cwd>/.vscode/mcp.json` |
  | 6…5+n | `extra` | `[sources] extra` files, in list order |
  | 6+n | `cursor_user` | Cursor user `mcp.json` |
  | 7+n | `vscode_user` | VS Code user `mcp.json` |
  | 8+n | `claude_desktop` | `claude_desktop_config.json` |

  **Project sources** (for REQ-REG-011) are kinds 1, 3, 4 and 5. `extra` files are
  user-chosen and are not project sources.
- **Reason:** Constitution Art. 6 requires deterministic precedence written in the PRD.

### MODIFIED — REQ-REG-011 (A-04)
- **After:** IF a stdio Server definition comes from a project source (RF-REG table) and
  its trust fingerprint — covering command, arguments, working directory, origin path and
  the names **and values** of its environment variables — has not been trusted before,
  THEN THE SYSTEM SHALL require explicit Consumer confirmation (showing command, arguments,
  working directory and environment variable names, flagging names known to load code)
  before launching its process. Environment values SHALL NOT be shown or stored.

### ADDED — REQ-REG-014 (unwanted) — `MUST` (A-25)
IF a project-level `mcphost.toml` contains keys other than `schema_version` and
`[servers.*]` tables, THEN THE SYSTEM SHALL ignore those keys, report a `ConfigProblem`
with code `project_setting_ignored` naming each key, and continue loading.

### MODIFIED — RF-CON description (A-18)
- "Own one Client (`ClientSession`) per enabled Server" → "Own one Client (`mcp.Client`)
  per enabled Server".

### MODIFIED — REQ-CON-005 (A-10)
- **After (tail):** "…retry with exponential backoff starting at 1 s, doubling, with
  jitter, **every delay capped at 60 s after jitter is applied**."

### MODIFIED — RF-CNS "Scopes" (A-17)
- **Before:** "Scopes: `tool`, `server`, `session`, `global`."
- **After:** "Targets: `tool` (one FQN), `server` (all tools of a Server), `global` (all
  Servers). Lifetimes: once, session (current Host session), always (persisted)."

### MODIFIED — REQ-CNS-003 (A-05)
- **After:** THE SYSTEM SHALL rank matching decisions by target specificity
  (`tool` > `server` > `global`) and, within the same target, by Consumer specificity
  (a rule for the requesting Consumer > a rule for any Consumer); THE SYSTEM SHALL apply
  only the highest-ranked decisions present, and among those `deny` SHALL win over any
  allow.

### MODIFIED — REQ-CNS-007, added sentence (A-14)
- **Added:** "Decisions with a `server` or `global` target are not suspended by
  definition changes and also cover tools added later; the consent prompt SHALL state this
  when the Consumer chooses such a target."

### MODIFIED — REQ-INV-009, added sentence (A-09)
- **Added:** "An attempt ending with outcome `audit_unavailable` has no record
  (REQ-INV-012)."

### MODIFIED — REQ-HSH-001 and REQ-HSH-045, added sentence (A-06)
- **Added to REQ-HSH-045:** "A server-initiated request that is not correlated with an
  in-flight Host request SHALL be attributed to Consumer `human:ui`."
- **Added to REQ-HSH-001:** "The Consumer of a sampling request is the Consumer of the
  originating Host request (tool call or catalog read), or `human:ui` per REQ-HSH-045."

### ADDED — REQ-HSH-046 (unwanted) — `MUST` (A-06)
IF an input request inside `resources/read` or `prompts/get` is denied, exceeds the round
cap, or uses an undeclared method, THEN THE SYSTEM SHALL stop retrying and raise
`InputRequestError` with code `sampling_denied`, `input_rounds_exceeded` or
`undeclared_input_request` respectively.

### MODIFIED — RF-HSH priorities and REQ priority rule (A-12)
- **Added at the top of §6:** "Priority rule: a criterion's trailing tag
  (`— MUST`/`SHOULD`/`COULD`) overrides its group; an untagged criterion takes its
  group's priority. All REQ-NFR are `MUST`."
- RF-HSH heading becomes: "Sampling `MUST` (deprecated, compatibility) · Elicitation
  `MUST` · Roots `SHOULD` (deprecated, compatibility) · Logging `SHOULD` (deprecated,
  compatibility) · MRTR `MUST`", with each sub-heading carrying its priority.
- REQ-HSH-022 gets the tag `— MUST` (security invariant whenever roots are served).

### MODIFIED — REQ-APP-009 (A-21)
- **After:** WHEN an invocation ends `cancelled`, `timeout` or `connection_lost`, THE
  SYSTEM SHALL send `ui/notifications/tool-cancelled` to its View.

### MODIFIED — §7 Non-functional requirements (A-13)
All measurements on a GitHub Actions `ubuntu-latest` runner unless stated.
- **REQ-NFR-001:** p95 of `ConsentPolicy.evaluate` for a request decided by a stored rule,
  10,000 rules loaded, ≥ 1,000 evaluations (pytest-benchmark) — < 5 ms.
- **REQ-NFR-002:** p95 of `Invoker.call` wall time minus the fixture Server's measured
  handler time, for a no-op tool allowed by a stored rule over the in-process transport,
  ≥ 1,000 calls — < 20 ms.
- **REQ-NFR-003:** time from `reflex-mcphost run --no-browser` process start to the Servers
  view rendering (Playwright waits for `data-testid="servers-panel"`), on a warm cache
  (second run of the same version), every one of 5 runs — < 10 s.
- **REQ-NFR-004:** with 50 fixture Servers and 2,000 catalog entries, p95 over 20
  interactions of the time from a filter keystroke or a `catalog.changed` event to the
  Catalog/Servers list DOM update (Playwright trace, Chromium) — < 200 ms.
- **REQ-NFR-011, clarified (A-03):** "…whose `Host` is not `127.0.0.1:<backend_port>` or
  whose `Origin` (when present) is not `http://127.0.0.1:<frontend_port>`."

### MODIFIED — §11 Risks, first row (A-19)
- Mitigation "isolate SDK use in `core.transport`/`core.session` adapters" → "isolate SDK
  use in `core.connection.*` and `core.mrtr`; SDK contract tests (F1-11)".

---

## 3. `specs/api/mcphost-api-v0.1.md` → v1.2

### MODIFIED — §3.4 Error model (A-06)
- **Added row:** `InputRequestError(server_id, code)` — an input request inside a catalog
  read was denied / exceeded the round cap / used an undeclared method; `code` ∈
  `sampling_denied`, `input_rounds_exceeded`, `undeclared_input_request` — REQ-HSH-046.

### MODIFIED — §4.1 `Host` (A-02)
- `config: HostConfig | None = None  # None → loaded in start() via await HostConfig.aload()`
  (no file I/O in `__init__`).
- Behavior row added: "`start()` loads configuration with `HostConfig.aload()` when none
  was given; all file and keyring I/O in `core` runs through `anyio.to_thread`."

### MODIFIED — §4.2 `HostConfig` (A-02)
- **Added:** `@classmethod async def aload(cls, path=None, *, cwd=None) -> HostConfig`
  (same semantics as `load`, file reads via `anyio.to_thread`).
- **Added note:** "`load()` is synchronous and intended only for code that runs before an
  event loop (the CLI `servers` command, scripts). Inside async code use `aload()`."

### MODIFIED — §4.3 `ConfigOrigin` and `ConfigProblem` (A-01, A-25)
- `ConfigOrigin.precedence`: "assigned by the total order of PRD RF-REG (1 = highest;
  `extra` files take consecutive values in list order)".
- `ConfigProblemCode` gains `project_setting_ignored` (REQ-REG-014).

### MODIFIED — §4.5 Server state machine table (A-11)
- **Added rows:**
  | `connecting` | project stdio Server not trusted (declined, prompter error, or no prompter) | `failed` (reason `untrusted_project_server`, no retry) |
  | `connecting` | stdio executable not found | `failed` (reason `command_not_found: <executable>`, no retry) |
- **Modified row:** `ready` | legacy HTTP: 404 on session id | **stays `ready`**: the
  Client re-initializes internally, no `server.state_changed` is emitted, a `server.log`
  entry is recorded (REQ-CON-015).

### MODIFIED — §4.6 `Catalog` (A-06)
- `read_resource`, `read_template`, `get_prompt` gain keyword `consumer: Consumer =
  Consumer.human()`; it is the Consumer for input requests (elicitation, sampling)
  raised during the read. They raise `InputRequestError` per REQ-HSH-046.

### MODIFIED — §4.7 Evaluation algorithm, steps 1–2 (A-05, A-14)
- **After:**
  1. Collect non-suspended rules (persistent and session) matching `operation`,
     `server_id`/`fqn`, and `consumer` (`rule.consumer is None` or equal).
  2. Rank each by `(target specificity: tool=3, server=2, global=1; consumer
     specificity: specific=2, any=1)`, compared lexicographically. Keep only rules with
     the highest rank present. If any of them is `deny` → deny; else allow.
- **Added note after step 6:** "`server`/`global` targets are not suspended by
  definition changes and cover tools added later (REQ-CNS-007); the consent prompt's
  wording for those targets says so."

### MODIFIED — §4.10 `Prompter` (A-04, A-06)
- `TrustRequest` gains `risky_env_keys: tuple[str, ...]` — env names from a built-in list
  known to load code (`NODE_OPTIONS`, `PYTHONPATH`, `PYTHONSTARTUP`, `LD_PRELOAD`,
  `LD_LIBRARY_PATH`, `DYLD_INSERT_LIBRARIES`, `DYLD_LIBRARY_PATH`, `PERL5OPT`,
  `RUBYOPT`, `JAVA_TOOL_OPTIONS`, `BASH_ENV`, `ENV`).
- **Rule replaced:** "`confirm_trust` is called once per (server_id, trust fingerprint)
  (Data Model §2.4); a change to command, args, cwd, origin path, or any env name or
  value re-triggers it. `False`, a prompter exception or no prompter → state `failed`
  with reason `untrusted_project_server`."
- **Added rule:** "Server-initiated input requests not correlated with an in-flight Host
  request use `Consumer.human()` (REQ-HSH-045)."

### MODIFIED — §4.14 Sampling handler flow (A-06)
- **Added:** "The Consumer of the consent request and audit record is the originating
  request's Consumer (tool call or catalog read), else `human:ui`."

### MODIFIED — §5.1 Location and discovery (A-25)
- **Replaced** "Project-level keys override user-level keys (deep merge…)" with:
  "The project-level file contributes only `schema_version` and `[servers.*]`; other keys
  are ignored with `ConfigProblem(project_setting_ignored)` (REQ-REG-014). A project
  `[servers.<id>]` shadows a user-level one in full (precedence 1 vs 2)."

### MODIFIED — §7 table, `tool-cancelled` row (A-21)
- REQ column: REQ-APP-009 (now names `connection_lost` too) — text unchanged.

---

## 4. `specs/technical/mcphost-architecture.md` → v1.1

### MODIFIED — §0 PydanticAI row (A-22)
- "PyPI (2026-10-31)" → "PyPI (2026-10-30)".

### MODIFIED — Flow B step 9 (A-10)
- `sleep backoff(failures) = min(60, 2^failures * jitter(0.5–1.5))`.
- Error table B.3 row → "stdio command not found | `failed` immediately, reason
  `command_not_found: <executable>` (env not included); no retry until config change or
  `reconnect()`" (A-11).

### MODIFIED — Flow C step 6 (A-07)
```
rounds = 0                      # input rounds fulfilled; audited as input_rounds (0–8)
result = await session.call_tool(name, args, allow_input_required=True, progress…)
while isinstance(result, InputRequiredResult):
    if rounds == 8 → protocol_error/input_rounds_exceeded (input_rounds = 8)
    responses = await InputRequestRouter.fulfil(result.input_requests, ctx)
    rounds += 1
    result = await session.call_tool(name, args, input_responses=responses,
                                      request_state=result.request_state)
```
The same loop (with `InputRequestError` instead of outcomes) serves catalog reads; the
`ContextVar` invocation context carries the Consumer for tool calls **and** reads (A-06).

### MODIFIED — DD-007 / DD-008 (A-02, A-08)
- **DD-008 After:** "`TokenStore` implements the SDK `TokenStorage` protocol per Server
  using the Data Model §3 layout: one entry `oauth:{server_id}` holding `issuer`,
  `tokens`, `client_info` and `client_id_source`; on issuer change both tokens and
  client info are discarded (REQ-AUTH-011)." The `oauth:{server_id}:{issuer}` and
  `client:{issuer}:{server_url_hash}` names are removed.
- **Added to DD-007 and DD-008:** "Every `keyring` call and secret-file access runs via
  `anyio.to_thread.run_sync` on a dedicated single-worker limiter (Art. 8)."

### MODIFIED — DD-013 (A-02, A-25)
- **Added:** "Config files are read through `anyio.to_thread` (`aload`, `reload`); only
  the sync `HostConfig.load()` used before an event loop reads them directly. The
  project-level `mcphost.toml` is filtered to `schema_version` + `[servers.*]`
  (REQ-REG-014)." Precedence follows the PRD RF-REG total order (A-01).

### MODIFIED — DD-017 (A-03)
- **After:** "…rejects HTTP and websocket requests whose `Host` is not
  `127.0.0.1:<backend_port>` or whose `Origin` (when present) is not
  `http://127.0.0.1:<frontend_port>` → `403`. `localhost` is deliberately **not**
  accepted for the UI: the name is reserved for the MCP Apps sandbox proxy (DD-012), and
  a UI loaded via `localhost` cannot reach the backend. The frontend shows a static notice
  'open http://127.0.0.1:<port>' when `location.hostname !== '127.0.0.1'`. The CLI always
  opens the `127.0.0.1` URL."
- **Added test:** E2E opening the UI via `http://localhost:<frontend_port>` gets no
  backend connection; the proxy cannot read UI cookies (F7-06).

### MODIFIED — DD-018 (A-20)
- **Added:** "For Art. 3, `core.mrtr` is part of the Invoker (it implements pipeline step
  6); `core.handlers.sampling` is the sampling handler."

### MODIFIED — §6.1 Attack surface (A-04, A-25)
- Row "Malicious project `.mcp.json`…" → "Malicious project config spawning commands |
  Trust prompt per HMAC fingerprint over command, args, cwd, origin, env names **and
  values**; risky env names flagged | REQ-REG-011".
- **Added row:** "Project `mcphost.toml` changing host settings or loading a sampling
  provider | Project file limited to `[servers.*]` | REQ-REG-014".

---

## 5. `specs/data-model/mcphost-schema.md` → v1.1

### MODIFIED — §2.3 `audit_record` field `input_rounds` (A-07)
- Description → "MRTR input rounds **fulfilled** (0–8); a call aborted with
  `input_rounds_exceeded` records 8". `CHECK` unchanged.

### MODIFIED — §2.4 `trusted_server` (A-04)
- `fingerprint TEXT NOT NULL CHECK (fingerprint GLOB 'hmac-sha256:*')`.
- **After:** `fingerprint = "hmac-sha256:" + HMAC(hmac-key, canonical_json({command,
  args, cwd, origin_path, env: {name: value, …}}))`. Env values are covered by the keyed
  hash but never stored or shown; the per-install key (§3 `hmac-key`) prevents offline
  guessing of low-entropy values.

### MODIFIED — §7 Corruption row (A-15)
- Unchanged text; now implemented by task F1-08 (see §6 below).

---

## 6. `specs/plans/mcphost-v0.1.md` → v1.1 and `specs/tasks/mcphost-v0.1-tasks.md` → v1.1

### MODIFIED — Plan F1-08 / Tasks F1-08 (A-15)
- **Added to What:** "`PRAGMA quick_check` on open; failure → `MCPHostError` naming the
  DB path and backup instructions (DM §7)." **Added to Done:** a test with a corrupted
  DB file refuses to open.

### MODIFIED — Plan F2-08 (A-15)
- **Added:** "prune `era_cache` rows of removed Servers on registry reload (DM §6)".

### MODIFIED — Plan F2-05 (A-04)
- REQ column adds REQ-REG-014; task text: "trust store with HMAC fingerprint incl. env
  values, risky-env flagging; project `mcphost.toml` filtering".

### MODIFIED — Plan F2-02 (A-25), F3-01 (A-05), F3-10 (A-06, A-07), F6-01 (A-03)
- F2-02 REQ adds REQ-REG-014 (project-file filtering in the parser).
- F3-01 adds: "property test for the ranking of REQ-CNS-003 (target then Consumer
  specificity, deny-wins within rank)".
- F3-10 REQ adds REQ-HSH-046; adds "Consumer `ContextVar` for reads; fulfilled-round
  counting".
- F6-01 adds "127.0.0.1-only Host/Origin rule + frontend notice".

### MODIFIED — Plan F6-09 and F8-02 (A-13)
- "reference machine" → "GitHub Actions `ubuntu-latest` runner, per PRD §7 definitions".

### MODIFIED — Tasks F1-01 dependency (A-16)
- **Before:** "Phase 0 findings reviewed".
- **After:** "Batch A (F0-01, F0-02, F0-06) and F0-05 reviewed". F0-04 (S-4) gates only
  F4-07/F4-08; F0-03 (S-3) gates only Phase 7. Plan §4 spike-gate line unchanged.

### MODIFIED — Tasks F1-03 (A-23)
- Drop the `[P]` marker (shares `pyproject.toml` with F1-02).

### MODIFIED — Tasks F1-05 (A-12)
- **What:** the script derives each REQ's priority from the PRD priority rule (trailing
  tag overrides group; NFR = MUST) and checks MUST only.

### MODIFIED — Tasks F1-06 (A-24)
- **Added:** `Consumer` name validation `^[A-Za-z0-9_.-]{1,64}$` (API §2.1), covered by
  the hypothesis tests; `InputRequestError` added to `core/errors.py` (A-06).

### MODIFIED — Tasks traceability matrix
- Adds `REQ-REG-014 | F2-02, F2-05 (Phase 2) | —` and `REQ-HSH-046 | F3-10 (Phase 3) | —`
  as planned rows.

---

## Coverage of the Analyze report

| Finding | Resolved in |
|---|---|
| A-01 | §2 RF-REG, §3 §4.3, §4 DD-013 |
| A-02 | §3 §4.1/§4.2, §4 DD-007/008/013 |
| A-03 | §2 REQ-NFR-011, §4 DD-017, §6 F6-01 |
| A-04 | §2 REQ-REG-011, §3 §4.10, §4 §6.1, §5 §2.4, §6 F2-05 |
| A-05 | §2 REQ-CNS-003, §3 §4.7, §6 F3-01 |
| A-06 | §2 REQ-HSH-001/045/046, §3 §3.4/§4.6/§4.10/§4.14, §4 Flow C, §6 F3-10/F1-06 |
| A-07 | §4 Flow C, §5 §2.3 |
| A-08 | §4 DD-008 |
| A-09 | §1 Art. 3, §2 REQ-INV-009 |
| A-10 | §2 REQ-CON-005, §4 Flow B |
| A-11 | §3 §4.5, §4 Flow B error table |
| A-12 | §2 priority rule + RF-HSH, §6 F1-05 |
| A-13 | §2 §7, §6 F6-09/F8-02 |
| A-14 | §2 REQ-CNS-007, §3 §4.7 |
| A-15 | §5 §7, §6 F1-08/F2-08 |
| A-16 | §6 F1-01 |
| A-17 … A-24 | §2 (A-17, A-18, A-19, A-21, A-22), §3 §7 (A-21), §4 §0 / DD-018 (A-20, A-22), §6 (A-23, A-24) |
| A-25 (new) | §2 RF-REG + REQ-REG-014, §3 §4.3/§5.1, §4 DD-013/§6.1, §6 F2-02/F2-05 |
