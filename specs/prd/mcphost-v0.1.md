# reflex-mcphost v0.1

## Product Requirements Document (PRD)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo (`ecrespo`) |
| **Status** | `APPROVED` |
| **Version** | 1.7 |
| **Date** | 2026-10-31 |
| **Reviewers** | Ernesto Crespo |
| **Last updated** | 2026-10-31 |
| **Governed by** | `specs/constitution.md` v1.2 |

> **Notation.** Acceptance criteria use EARS in English: *ubiquitous* ("THE SYSTEM
> SHALL…"), *event* ("WHEN…"), *state* ("WHILE…"), *optional* ("WHERE…"), *unwanted*
> ("IF… THEN…"). IDs are `REQ-<AREA>-NNN`, stable and never reused. Terms **Host**,
> **Client**, **Server**, **Consumer** and **FQN** have the meanings fixed in the
> Constitution's vocabulary.
>
> **Brief coverage.** The kickoff brief was truncated after the host-side handlers.
> Sections marked **[ASSUMED]** (Toolset, MCP Apps, reference app/CLI) are derived from
> the brief's positioning and must be confirmed in review (see §13).

---

## 1. Executive summary

We are building **reflex-mcphost**, an open-source MCP **Host** for Python, shipped as a
library (`reflex_mcphost`) plus a Reflex reference app runnable with
`uvx reflex-mcphost`. It lets a Python developer connect MCP Servers (reusing the configs
they already have for Claude Desktop, Cursor or VS Code), browse and invoke their tools,
resources and prompts, render Server-provided MCP Apps, and hand the same tools to any
agent framework — with every effectful call passing through a consent policy engine and
an audit log.

The problem: today the only full-featured MCP hosts are closed desktop apps or IDEs. A
Python developer who wants a host they can embed, script, test against or extend has to
assemble SDK sessions, OAuth, reconnection and permission prompts by hand, and usually
skips consent entirely. reflex-mcphost makes the host side a reusable, typed, tested
component — "the open Claude Desktop for Python developers" — whose differentiator is
consent as a first-class, auditable policy rather than a yes/no dialog.

Success is measured by time-to-first-tool-call from an existing config (< 5 min), by
interoperability with reference Servers over both transports, and by adoption (§4).

## 2. Context and problem

### 2.1 Current situation
- MCP hosts with complete feature support (sampling, elicitation, roots, OAuth, MCP Apps)
  are proprietary desktop apps or editors; their host logic is not reusable.
- The official `mcp` Python SDK provides `ClientSession` and transports, but no registry,
  reconnection supervisor, consent model, catalog cache or UI.
- Existing Reflex MCP packages on PyPI (`reflex-docs-mcp`, `compressor-reflex-mcp`) are
  Servers; nothing occupies the Host side.
- Agent frameworks (PydanticAI, LangGraph) can consume MCP tools, but each wires the SDK
  directly, with no shared permission layer between the agent and the Server.

### 2.2 Problem
1. **No reusable host.** Every Python project re-implements session lifecycle, OAuth 2.1
   and reconnection.
2. **No consent layer.** Agents call MCP tools directly; there is no scoped, persisted,
   auditable decision between "agent wants to call" and "Server executes".
3. **Config fragmentation.** Users already maintain Server definitions in 3–4 formats;
   a new host that needs yet another copy is friction.
4. **MCP Apps have no open Python renderer.** Server authors shipping UIs have no
   open host to test them in.

### 2.3 Opportunity
Own the Host side of MCP for the Python/Reflex ecosystem, complementary to
`reflex-agentkit` (agent↔user conversation): mcphost supplies tools and permissions, the
reference app composes both.

## 3. Target users

### Persona 1: Agent developer (primary)
- **Description:** Python developer building agents with PydanticAI, LangGraph or custom loops.
- **Main need:** Give an agent MCP tools with real permissioning and an audit trail, without writing host plumbing.
- **Usage frequency:** Daily during development.
- **Technical level:** High.

### Persona 2: Power user / "open desktop" user
- **Description:** Developer who uses Claude Desktop/Cursor and wants an open, local, inspectable host for the same Servers.
- **Main need:** Run `uvx reflex-mcphost`, see existing Servers, invoke tools and MCP Apps from a UI, control what is allowed.
- **Usage frequency:** Weekly.
- **Technical level:** Medium–high.

### Persona 3: MCP Server author
- **Description:** Builds MCP Servers, possibly with MCP Apps.
- **Main need:** Exercise every capability of their Server (tools, resources, prompts, sampling, elicitation, Apps) against a spec-faithful host.
- **Usage frequency:** Eventual / per release.
- **Technical level:** High.

## 4. Goals and success metrics

### 4.1 Project goals

| Goal | Metric | Target | Deadline |
|---|---|---|---|
| Interoperability | Reference Servers passing the interop suite (stdio + Streamable HTTP + one OAuth remote) | ≥ 5 Servers, 100 % of suite | v0.1 release |
| Adoption | PyPI downloads (30-day) | ≥ 1,000 | 3 months after v0.1 |
| Community | GitHub stars | ≥ 200 | 3 months after v0.1 |
| Quality | Constitution Art. 7 coverage thresholds | core ≥ 90 %, overall ≥ 80 % | Every release |

### 4.2 User goals

| User goal | Indicator |
|---|---|
| Use existing Servers immediately | Time from `uvx reflex-mcphost` to first successful tool call using an existing Claude Desktop config < 5 min (usability test, 3 users) |
| Trust what agents do | 100 % of tool calls and sampling requests have an audit record (Art. 3) |
| Wire an agent quickly | PydanticAI or LangGraph agent using Host tools in ≤ 10 lines of glue code (documented example) |

## 5. Scope

### 5.1 In scope (v0.1)
- [ ] `ServerRegistry` — multi-source config loading, precedence, env expansion, own `mcphost.toml`.
- [ ] `ConnectionManager` — lifecycle, health, reconnection, stdio supervision, Streamable HTTP + OAuth 2.1.
- [ ] `Catalog` — tools, resources, resource templates, prompts per Server, FQNs, `list_changed` refresh.
- [ ] `ConsentPolicy` + `ConsentStore` — scoped decisions, argument preview, persistence.
- [ ] `Invoker` + `AuditLog` — consented `tools/call` with timeout, cancellation, progress.
- [ ] Host-side handlers — sampling (consented, pluggable `SamplingProvider`), elicitation, roots, logging.
- [ ] **[ASSUMED]** `Toolset` — framework-neutral agent bridge; optional adapters for PydanticAI and LangGraph.
- [ ] **[ASSUMED]** MCP Apps rendering — sandboxed iframe + host-side bridge.
- [ ] **[ASSUMED]** Reflex UI components + reference app + `reflex-mcphost` CLI.

### 5.2 Out of scope
- MCP Server implementation of any kind — Constitution Art. 1.
- Agent loop / chat UI — owned by `reflex-agentkit`.
- Registry/marketplace browsing — v0.2 extension point.
- Tracing/observability UI — future `reflex-agentscope`; only the local audit trail here.
- Desktop packaging (Electron/Tauri) — v0.1 is a local Reflex web app.
- Multi-user / remote deployment of the reference app — v0.1 is single-user, localhost.
- Writing to third-party config files — Constitution Art. 6.
- Full HTTP+SSE legacy transport — read-only compatibility fallback at most (Art. 2).

### 5.3 Future considerations
- Registry browsing and one-click install (v0.2).
- Export of the audit log to `reflex-agentscope`.
- Policy-as-code files (e.g. per-project consent rules checked into a repo).
- Resource subscriptions surfaced as live UI.

## 6. Functional requirements

Priorities use MoSCoW. Each RF groups its EARS acceptance criteria; the criteria are the
testable contract (Art. 7).

**Priority rule:** a criterion's trailing tag (`— MUST` / `SHOULD` / `COULD`) overrides its
group; an untagged criterion takes its group's (or sub-group's) priority. All REQ-NFR are
`MUST`.

### RF-REG: Server registry — `MUST`

- **Description:** Load, merge and expose Server definitions from our own config and third-party configs.
- **Actor:** Host (at startup and on reload); Consumer (human) adding Servers manually.
- **Sources, in descending precedence** (a total order — Constitution Art. 6; exactly one
  file per position; 1 = highest):

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

  **Project sources** (for REQ-REG-011) are kinds 1, 3, 4 and 5; `extra` files are
  user-chosen and are not project sources. Exact paths per OS are fixed in the Technical
  Design.
- **Postconditions:** an immutable, merged list of `ServerDefinition`s, each with its origin.

| ID | Type | Criterion |
|---|---|---|
| REQ-REG-001 | event | WHEN the Host starts, THE SYSTEM SHALL load Server definitions from every source in the precedence list that exists and is readable, and expose each definition with its `server_id`, transport and origin file. |
| REQ-REG-002 | ubiquitous | THE SYSTEM SHALL parse both the `mcpServers` key (Claude Desktop, Cursor, `.mcp.json`) and the `servers` key (VS Code) into the same `ServerDefinition` model. |
| REQ-REG-003 | unwanted | IF two sources define the same `server_id`, THEN THE SYSTEM SHALL use the definition from the higher-precedence source in full (no field-level merge) and report the shadowed definition with its origin. |
| REQ-REG-004 | event | WHEN a definition value contains `${VAR}` or `${VAR:-default}`, THE SYSTEM SHALL substitute the environment variable (or the default) before connecting. |
| REQ-REG-005 | unwanted | IF a referenced variable is unset and has no default, or uses an unsupported form (e.g. VS Code `${input:…}`), THEN THE SYSTEM SHALL mark the Server `config_error` with the variable name and SHALL NOT attempt to connect it. |
| REQ-REG-006 | unwanted | IF a config file is malformed, THEN THE SYSTEM SHALL skip that file, report the file path and parse error, and continue loading the remaining sources. |
| REQ-REG-007 | ubiquitous | THE SYSTEM SHALL NOT include values of `env` entries or `headers` entries in logs, error messages or the UI, showing only their keys (Art. 5). |
| REQ-REG-008 | ubiquitous | THE SYSTEM SHALL leave every third-party config file byte-identical after any registry operation (Art. 6). |
| REQ-REG-009 | event | WHEN the Consumer adds, edits or removes a Server through the API or UI, THE SYSTEM SHALL persist the change only to the user-level `mcphost.toml`. |
| REQ-REG-010 | event | WHEN the Consumer disables a Server that originates from a third-party file, THE SYSTEM SHALL record the override in `mcphost.toml` and leave the source file untouched. |
| REQ-REG-011 | unwanted | IF a Server definition (stdio or Streamable HTTP) comes from a project source (RF-REG table) and its trust fingerprint — stdio: command, arguments, working directory, origin path and the names **and values** of its environment variables; HTTP: URL, origin path and the names **and values** of its headers — has not been trusted before, THEN THE SYSTEM SHALL require explicit Consumer confirmation (stdio: showing command, arguments, working directory and environment variable names, flagging names known to load code; HTTP: showing the URL as written in the file and the header names) before launching its process or sending any request. Environment and header values SHALL NOT be shown or stored. |
| REQ-REG-012 | unwanted | IF a `server_id` contains the FQN separator `::` or characters outside `[A-Za-z0-9_.-]`, THEN THE SYSTEM SHALL reject the definition with a validation error naming the Server. |
| REQ-REG-013 | event | WHEN the Consumer requests a reload, THE SYSTEM SHALL re-read all sources and connect, disconnect or reconnect only the Servers whose effective definition changed. |
| REQ-REG-014 | unwanted | IF a project-level `mcphost.toml` contains keys other than `schema_version` and `[servers.*]` tables, THEN THE SYSTEM SHALL ignore those keys, report a `ConfigProblem` with code `project_setting_ignored` naming each key, and continue loading. |

### RF-CON: Connection manager — `MUST`

- **Description:** Own one Client (`mcp.Client`) per enabled Server and manage its lifecycle and health.
- **Actor:** Host.
- **Health states:** `disconnected`, `connecting`, `ready`, `reconnecting`, `auth_required`, `failed`, `config_error`, `disabled`.

| ID | Type | Criterion |
|---|---|---|
| REQ-CON-001 | event | WHEN an enabled Server is registered, THE SYSTEM SHALL create exactly one Client for it, determine its era — stdio: send `server/discover` and fall back to `initialize` on any error that is not a recognized modern error or on timeout; HTTP: send a modern request and fall back to `initialize` on a `4xx` whose body is not a recognized modern error — and transition to `ready` when `server/discover` (modern) or the `initialize` handshake (legacy) completes. |
| REQ-CON-002 | ubiquitous | THE SYSTEM SHALL record the Server's era, protocol version, `serverInfo` and capabilities (from `DiscoverResult` or `InitializeResult`), update `serverInfo` when a modern result carries `io.modelcontextprotocol/serverInfo`, and SHALL only send requests the Server declared support for. |
| REQ-CON-003 | ubiquitous | THE SYSTEM SHALL declare as Client capabilities exactly the host-side features currently enabled (elicitation, sampling, roots, extension `io.modelcontextprotocol/ui`) — in every request's `_meta` for modern Servers, and in `initialize` for legacy Servers. |
| REQ-CON-004 | unwanted | IF the Server and Host share no supported protocol version (`UnsupportedProtocolVersionError` `-32022` without overlap, or a legacy `initialize` negotiating an unsupported version), THEN THE SYSTEM SHALL set state `failed` with the requested and supported versions in the reason. |
| REQ-CON-005 | unwanted | IF a `ready` Client loses its transport (stdio process exit; HTTP connection failure or `5xx` on a request or on the `subscriptions/listen` stream; legacy session stream loss), THEN THE SYSTEM SHALL set state `reconnecting` and retry with exponential backoff starting at 1 s, doubling, with jitter, every delay capped at 60 s after jitter is applied. |
| REQ-CON-006 | unwanted | IF reconnection fails 10 consecutive times, THEN THE SYSTEM SHALL set state `failed` and stop retrying until the Consumer requests a reconnect. |
| REQ-CON-007 | event | WHEN a stdio Server is started, THE SYSTEM SHALL launch its process with the configured command, args, env and cwd, and capture its stderr into that Server's log stream. |
| REQ-CON-008 | event | WHEN a stdio Client is disconnected, THE SYSTEM SHALL close stdin, wait up to 2 s for exit, then terminate, and kill after a further 2 s, terminating the Server's whole process group (Windows: its Job Object), leaving no orphaned process. |
| REQ-CON-009 | event | WHEN the Host shuts down, THE SYSTEM SHALL disconnect every Client concurrently per REQ-CON-008 within 15 s total. |
| REQ-CON-010 | ubiquitous | THE SYSTEM SHALL emit a state-change event (server_id, old state, new state, reason) on every health transition, consumable by the UI and library users. |
| REQ-CON-011 | unwanted | IF one Server fails to connect or crashes, THEN THE SYSTEM SHALL keep all other Clients unaffected. |
| REQ-CON-012 | ubiquitous | THE SYSTEM SHALL support the stdio and Streamable HTTP transports. |
| REQ-CON-013 | optional | WHERE `legacy_sse_fallback` is enabled and both modern and legacy Streamable HTTP attempts fail with `400`/`404`/`405` without a recognized modern error body, THE SYSTEM SHALL connect via the deprecated HTTP+SSE transport and flag the Client `legacy_transport`. — `COULD` |
| REQ-CON-014 | optional | WHERE a Streamable HTTP Server is legacy-era and returns `Mcp-Session-Id`, THE SYSTEM SHALL send it on every subsequent request and terminate the session (HTTP `DELETE`) on disconnect. THE SYSTEM SHALL NOT send `Mcp-Session-Id` to modern Servers. |
| REQ-CON-015 | unwanted | IF a legacy-era Streamable HTTP Server responds `404` to a request carrying a session ID, THEN THE SYSTEM SHALL re-initialize without counting it as a reconnection failure. |
| REQ-CON-016 | ubiquitous | THE SYSTEM SHALL send, on every modern Streamable HTTP POST, `Accept: application/json, text/event-stream`, `MCP-Protocol-Version` equal to the `_meta` version, `Mcp-Method`, and — for `tools/call`, `resources/read`, `prompts/get` — `Mcp-Name`, using the `=?base64?…?=` sentinel when the value is not header-safe. |
| REQ-CON-017 | ubiquitous | THE SYSTEM SHALL mirror tool arguments annotated with `x-mcp-header` into `Mcp-Param-{Name}` headers on Streamable HTTP, encoded per the spec's value-encoding rules. |
| REQ-CON-018 | unwanted | IF a tool definition received over Streamable HTTP has an invalid `x-mcp-header` annotation, THEN THE SYSTEM SHALL exclude that tool from the catalog and log a warning naming the tool and reason. |
| REQ-CON-019 | event | WHEN the era of a Server has been detected, THE SYSTEM SHALL cache it per stdio process / HTTP origin, and re-detect only if a request under the cached era fails with an era-indicating error. |

### RF-AUTH: OAuth 2.1 for remote Servers — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-AUTH-001 | event | WHEN a Streamable HTTP Server responds 401 with authorization metadata, THE SYSTEM SHALL set state `auth_required` and discover the authorization server per the pinned MCP authorization spec (protected resource metadata, then authorization server metadata). |
| REQ-AUTH-002 | event | WHEN the Consumer starts authorization, THE SYSTEM SHALL run the authorization code flow with PKCE (`S256`) via the system browser and a loopback redirect, including the resource indicator for the Server. |
| REQ-AUTH-003 | optional | WHERE no client ID is configured for a Server, THE SYSTEM SHALL use, in order: the project's Client ID Metadata Document URL if the authorization server advertises CIMD support; otherwise Dynamic Client Registration (deprecated fallback) with an appropriate `application_type`; otherwise stay `auth_required` with a message asking for a `client_id` in `mcphost.toml`. |
| REQ-AUTH-004 | event | WHEN tokens are obtained, THE SYSTEM SHALL store them in the OS keyring, or in a per-user file with mode `0600` if no keyring backend is available, and resume connecting the Server. |
| REQ-AUTH-005 | event | WHEN an access token is expired or rejected and a refresh token exists, THE SYSTEM SHALL refresh once and retry the request before setting `auth_required`. |
| REQ-AUTH-006 | unwanted | IF an authorization callback's `state` does not match, THEN THE SYSTEM SHALL refuse that callback without redeeming any code and keep waiting for an authentic one (a forged callback can neither end nor hijack the flow); IF the flow is not completed within 5 min, THEN THE SYSTEM SHALL abort the flow, discard the verifier, and keep state `auth_required`. |
| REQ-AUTH-007 | event | WHEN the Consumer signs out of a Server, THE SYSTEM SHALL delete its stored tokens and disconnect the Client. |
| REQ-AUTH-008 | ubiquitous | THE SYSTEM SHALL NOT log or display access tokens, refresh tokens, authorization codes or PKCE verifiers (Art. 5). |
| REQ-AUTH-009 | optional | WHERE a Server definition provides static headers (e.g. API key), THE SYSTEM SHALL send them and SHALL NOT start OAuth for that Server. |
| REQ-AUTH-010 | unwanted | IF the authorization response's `iss` is present and differs from the recorded issuer, or is absent while the authorization server advertises `authorization_response_iss_parameter_supported`, THEN THE SYSTEM SHALL refuse that callback without redeeming the code (RFC 9207) and, unless an authentic callback arrives first, abort the flow at its deadline with reason `iss_mismatch`. |
| REQ-AUTH-011 | ubiquitous | THE SYSTEM SHALL key persisted client credentials by authorization-server issuer, SHALL NOT reuse them with another issuer, and SHALL re-register when a Server's issuer changes. |
| REQ-AUTH-012 | ubiquitous | THE PROJECT SHALL publish its Client ID Metadata Document at a stable HTTPS URL (GitHub Pages of `ecrespo/reflex-mcphost`), versioned in the repository, listing only loopback redirect URIs. |

### RF-CAT: Catalog — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-CAT-001 | event | WHEN a Client reaches `ready`, THE SYSTEM SHALL fetch the complete lists of tools, resources, resource templates and prompts the Server declared, following pagination cursors to the end. |
| REQ-CAT-002 | ubiquitous | THE SYSTEM SHALL identify every tool, resource template and prompt by its FQN `server_id::name`, and resources by `(server_id, uri)`. |
| REQ-CAT-003 | event | WHEN a Server is `ready`, THE SYSTEM SHALL subscribe to the list changes it declares — modern: one `subscriptions/listen` stream opting in to `toolsListChanged` / `promptsListChanged` / `resourcesListChanged`; legacy: the `notifications/*/list_changed` notifications — and, on each such notification, re-fetch only that list for that Server and emit a catalog-changed event. |
| REQ-CAT-004 | state | WHILE a Server is not `ready`, THE SYSTEM SHALL keep its last known catalog visible, marked stale, and SHALL reject invocations on it. |
| REQ-CAT-005 | event | WHEN the Consumer reads a resource, THE SYSTEM SHALL call `resources/read` and return text and blob contents with their MIME types. |
| REQ-CAT-006 | event | WHEN the Consumer fills a resource template, THE SYSTEM SHALL expand it per RFC 6570 and read the resulting URI. |
| REQ-CAT-007 | event | WHEN the Consumer gets a prompt with arguments, THE SYSTEM SHALL call `prompts/get` and return the resulting messages. |
| REQ-CAT-008 | unwanted | IF a Server returns two tools with the same name, THEN THE SYSTEM SHALL keep the first, drop the duplicate, and log a warning naming the Server and tool. |
| REQ-CAT-009 | ubiquitous | THE SYSTEM SHALL compute and store a definition hash (name, description, input/output schema, annotations) for each tool. |
| REQ-CAT-010 | optional | WHERE the Server supports resource subscriptions, THE SYSTEM SHALL allow the Consumer to subscribe (modern: `resourceSubscriptions` on the listen stream; legacy: `resources/subscribe`) and SHALL emit an event on `notifications/resources/updated`. — `COULD` |
| REQ-CAT-011 | ubiquitous | THE SYSTEM SHALL treat tool annotations (`readOnlyHint`, `destructiveHint`, …) as untrusted display hints and SHALL NOT change consent decisions based on them. |
| REQ-CAT-012 | unwanted | IF a modern `subscriptions/listen` stream closes while the Server is `ready`, THEN THE SYSTEM SHALL reopen it with the REQ-CON-005 backoff and re-fetch all lists once it is reopened. |
| REQ-CAT-013 | optional | WHERE a Server does not honor list-change subscriptions, THE SYSTEM SHALL re-fetch a list when its `ttlMs` elapses, never more often than every 30 s. — `SHOULD` |
| REQ-CAT-014 | ubiquitous | THE SYSTEM SHALL record each tool's MCP Apps visibility (`_meta.ui.visibility`, default `["model","app"]`) and UI resource URI (`_meta.ui.resourceUri`, falling back to the deprecated `_meta["ui/resourceUri"]`). |

### RF-CNS: Consent policy — `MUST`

- **Description:** Decide, for every effectful operation, whether it proceeds, using scoped persisted decisions and asking the Consumer when needed.
- **Decisions:** `allow_once`, `allow_session`, `allow_always`, `deny`.
- **Targets:** `tool` (one FQN), `server` (all tools of a Server), `global` (all Servers). **Lifetimes:** once, session (current Host session), always (persisted).
- **Default:** ask.

| ID | Type | Criterion |
|---|---|---|
| REQ-CNS-001 | ubiquitous | THE SYSTEM SHALL evaluate the consent policy before every `tools/call` and every sampling request, with no code path that bypasses it (Art. 3). |
| REQ-CNS-002 | event | WHEN no stored decision matches an operation, THE SYSTEM SHALL ask the Consumer, presenting the FQN, the Server's identity, the tool description and the argument preview. |
| REQ-CNS-003 | ubiquitous | THE SYSTEM SHALL rank matching decisions by target specificity (`tool` > `server` > `global`) and, within the same target, by Consumer specificity (a rule for the requesting Consumer > a rule for any Consumer); THE SYSTEM SHALL apply only the highest-ranked decisions present, and among those `deny` SHALL win over any allow. |
| REQ-CNS-004 | event | WHEN the Consumer answers `allow_once`, THE SYSTEM SHALL allow that single invocation and store nothing. |
| REQ-CNS-005 | event | WHEN the Consumer answers `allow_session`, THE SYSTEM SHALL allow matching invocations until the Host session ends, without persisting the decision. |
| REQ-CNS-006 | event | WHEN the Consumer answers `allow_always` or `deny` with a persistent scope, THE SYSTEM SHALL persist the decision in the `ConsentStore` and apply it across restarts. |
| REQ-CNS-007 | unwanted | IF a tool's definition hash changes after an `allow_always` decision was stored for it, THEN THE SYSTEM SHALL suspend that decision and ask again, showing that the definition changed. Decisions with a `server` or `global` target are not suspended by definition changes and also cover tools added later; the consent prompt SHALL state this when the Consumer chooses such a target. |
| REQ-CNS-008 | unwanted | IF the policy cannot be evaluated (store unavailable, corrupt rule, evaluation error), THEN THE SYSTEM SHALL deny the operation and record the reason (fail closed). |
| REQ-CNS-009 | unwanted | IF a consent prompt is not answered within the configured timeout (default 120 s), THEN THE SYSTEM SHALL deny the operation. |
| REQ-CNS-010 | state | WHILE no consent prompter is attached (headless library use), THE SYSTEM SHALL deny every operation that has no matching stored or programmatic allow decision. |
| REQ-CNS-011 | optional | WHERE a library user registers an argument-preview hook for a tool or Server, THE SYSTEM SHALL use its output, instead of the default JSON rendering, in the consent prompt. |
| REQ-CNS-012 | ubiquitous | THE SYSTEM SHALL redact values of arguments whose schema marks them as secret-like (`format: password`, or names matching the configured secret pattern) in the default argument preview. |
| REQ-CNS-013 | event | WHEN the Consumer lists, edits or revokes stored decisions, THE SYSTEM SHALL apply the change to the next evaluation without restart. |
| REQ-CNS-014 | ubiquitous | THE SYSTEM SHALL let library users set decisions programmatically with the same scopes, recorded with origin `programmatic`. |
| REQ-CNS-015 | ubiquitous | THE SYSTEM SHALL evaluate consent per Consumer identity, so that a decision granted to the human UI Consumer is not implicitly granted to an agent Consumer unless its scope says so. |

### RF-INV: Invoker and audit log — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-INV-001 | event | WHEN an invocation is allowed, THE SYSTEM SHALL call `tools/call` on the owning Client with the given arguments and return the result to the Consumer. |
| REQ-INV-002 | unwanted | IF the arguments do not validate against the tool's `inputSchema`, THEN THE SYSTEM SHALL reject the invocation before asking consent, returning the validation errors. |
| REQ-INV-003 | unwanted | IF a call exceeds its timeout (default 60 s, configurable per Server and per call), THEN THE SYSTEM SHALL cancel it — stdio and legacy HTTP: `notifications/cancelled`; modern HTTP: close the request's response stream — and return outcome `timeout`. |
| REQ-INV-004 | event | WHEN the Consumer cancels an in-flight invocation, THE SYSTEM SHALL cancel it by the same transport-specific mechanism as REQ-INV-003 and return outcome `cancelled`. |
| REQ-INV-005 | event | WHEN the Server sends progress notifications for an invocation, THE SYSTEM SHALL forward progress and total (if any) to the Consumer, and reset the timeout only if configured to do so. |
| REQ-INV-006 | ubiquitous | THE SYSTEM SHALL return both unstructured content blocks (text, image, audio, resource links, embedded resources) and `structuredContent` when present. |
| REQ-INV-007 | unwanted | IF a tool declares an `outputSchema` and `structuredContent` does not validate against it, THEN THE SYSTEM SHALL return outcome `protocol_error` with the validation detail. |
| REQ-INV-008 | unwanted | IF the result has `isError: true`, THEN THE SYSTEM SHALL return outcome `tool_error` with the content, distinct from protocol errors. |
| REQ-INV-009 | ubiquitous | THE SYSTEM SHALL write exactly one `AuditLog` record per invocation attempt, including denied and invalid ones and covering all its MRTR rounds, with: timestamp (UTC), Consumer identity, FQN, argument hash, consent decision and its source, duration, outcome, number of input rounds. An attempt ending with outcome `audit_unavailable` has no record (REQ-INV-012). |
| REQ-INV-010 | ubiquitous | THE SYSTEM SHALL compute the argument hash as a keyed hash over canonical JSON, so equal arguments are correlatable locally without storing them (Art. 5). |
| REQ-INV-011 | event | WHEN the Consumer queries the audit log, THE SYSTEM SHALL return records filtered by time range, Server, FQN, Consumer and outcome, newest first. |
| REQ-INV-012 | unwanted | IF the audit record cannot be written, THEN THE SYSTEM SHALL not start the invocation and SHALL return an error (no unaudited effects). |
| REQ-INV-013 | ubiquitous | THE SYSTEM SHALL allow concurrent invocations across Servers and on the same Server. |
| REQ-INV-014 | ubiquitous | THE SYSTEM SHALL treat results lacking `resultType` (legacy Servers) as `"complete"`. |
| REQ-INV-015 | unwanted | IF the response stream of a modern `tools/call` breaks before the final response, THEN THE SYSTEM SHALL return outcome `connection_lost` without re-issuing the call; reads (`*/list`, `resources/read`, `prompts/get`, `server/discover`) SHALL be re-issued once automatically with a new request ID. |

### RF-HSH: Host-side handlers — sampling `MUST` (*deprecated, compatibility*) · elicitation `MUST` · roots `SHOULD` (*deprecated, compatibility*) · logging `SHOULD` (*deprecated, compatibility*) · MRTR `MUST`

**Sampling** — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-001 | event | WHEN a Server issues a sampling input request (MRTR `inputRequests` entry or legacy `sampling/createMessage`), THE SYSTEM SHALL evaluate consent for sampling scoped to that Server before any LLM call. The Consumer of a sampling request is the Consumer of the originating Host request (tool call or catalog read); a legacy request that cannot be attributed is handled per REQ-HSH-047. |
| REQ-HSH-002 | event | WHEN sampling consent is requested, THE SYSTEM SHALL show the Consumer the messages, system prompt, model preferences and max tokens, and allow approving, editing or rejecting the request. |
| REQ-HSH-003 | event | WHEN a sampling request is approved, THE SYSTEM SHALL route it to the configured `SamplingProvider` and return its completion to the Server. |
| REQ-HSH-004 | optional | WHERE "review sampling responses" is enabled for a Server, THE SYSTEM SHALL show the completion to the Consumer for approval before returning it. — `SHOULD` |
| REQ-HSH-005 | unwanted | IF no `SamplingProvider` is configured or the sampling request is denied, THEN THE SYSTEM SHALL — legacy: return a JSON-RPC error to the Server; modern: not retry the originating request and end it with outcome `denied` (error code `sampling_denied`) — and record the outcome in the `AuditLog`. |
| REQ-HSH-006 | ubiquitous | THE SYSTEM SHALL NOT declare the sampling Client capability unless a `SamplingProvider` is configured. |

**Elicitation** — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-010 | event | WHEN a Server issues an elicitation input request in `form` mode, THE SYSTEM SHALL render a form for its requested schema showing the requesting Server's identity and message. |
| REQ-HSH-011 | event | WHEN the Consumer submits, declines or cancels the form, THE SYSTEM SHALL return the corresponding `accept` (with content), `decline` or `cancel` action. |
| REQ-HSH-012 | unwanted | IF the requested schema uses constructs outside the spec's allowed subset, THEN THE SYSTEM SHALL reject the request without rendering — legacy: return a JSON-RPC error (`-32602`) to the Server; modern: not retry the originating request and end it with outcome `protocol_error` (error code `input_handler_failed`, `details.reason = "invalid_elicitation_schema"`), or raise `InputHandlerError` for a catalog read. |
| REQ-HSH-013 | unwanted | IF submitted content does not validate against the requested schema, THEN THE SYSTEM SHALL keep the form open with field errors and send nothing. |
| REQ-HSH-014 | state | WHILE no elicitation handler is attached (headless library use), THE SYSTEM SHALL NOT declare the elicitation capability. |
| REQ-HSH-015 | event | WHEN a Server issues an elicitation input request in `url` mode, THE SYSTEM SHALL show the full URL and requesting Server, open it in the browser only if the Consumer accepts, and return the Consumer's `accept` / `decline` / `cancel` action. — `SHOULD` |

**Roots** — `SHOULD`

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-020 | event | WHEN a Server issues a roots input request (MRTR `roots/list` entry or legacy `roots/list`), THE SYSTEM SHALL return the roots configured for that Server (default: none). — `SHOULD` |
| REQ-HSH-021 | event | WHEN the configured roots for a legacy-era Server change, THE SYSTEM SHALL send `notifications/roots/list_changed` to that Server (modern Servers obtain roots per request). — `SHOULD` |
| REQ-HSH-022 | ubiquitous | THE SYSTEM SHALL only expose `file://` roots that the Consumer configured explicitly. — `MUST` (security invariant whenever roots are served) |

**Logging** — `SHOULD`

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-030 | event | WHEN a Server emits `notifications/message` (modern: on a request's response stream; legacy: at any time) or writes to stderr (stdio), THE SYSTEM SHALL append it, with level, logger and timestamp, to that Server's log stream (bounded ring buffer, default 1,000 entries). — `SHOULD` |
| REQ-HSH-031 | event | WHEN the Consumer sets a Server's log level, THE SYSTEM SHALL — legacy: send `logging/setLevel`; modern: include `io.modelcontextprotocol/logLevel` in the `_meta` of every subsequent request to that Server. By default no level is sent. — `SHOULD` |
**Multi round-trip requests (MRTR) and legacy server-initiated requests** — `MUST`

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-040 | event | WHEN a `tools/call`, `resources/read` or `prompts/get` returns `resultType: "input_required"`, THE SYSTEM SHALL fulfill every `inputRequests` entry with the matching handler and retry the original request with a new JSON-RPC ID, the `inputResponses` map, and the exact `requestState` (if any). |
| REQ-HSH-041 | ubiquitous | THE SYSTEM SHALL NOT inspect, parse or modify `requestState`, and SHALL use input responses only for the retry of their originating request. |
| REQ-HSH-042 | ubiquitous | THE SYSTEM SHALL evaluate tool-call consent once per invocation; MRTR retries of an allowed invocation SHALL NOT re-prompt for the tool, though each sampling input request is consent-gated per REQ-HSH-001. |
| REQ-HSH-043 | unwanted | IF the same request returns `input_required` more than 8 consecutive times, THEN THE SYSTEM SHALL stop retrying and return outcome `protocol_error` with code `input_rounds_exceeded`. |
| REQ-HSH-044 | unwanted | IF an `inputRequests` entry uses a method the Host did not declare as a capability, THEN THE SYSTEM SHALL stop retrying and return `protocol_error` with code `undeclared_input_request`. |
| REQ-HSH-045 | event | WHEN a legacy-era Server sends a server-initiated sampling, elicitation or roots request, THE SYSTEM SHALL handle it with the same handlers and consent rules as the MRTR path. THE SYSTEM SHALL attribute a legacy server-initiated request to the in-flight Host request on that Server when exactly one is in flight; otherwise the request is unattributed (REQ-HSH-047). |
| REQ-HSH-046 | unwanted | IF an input request inside `resources/read` or `prompts/get` is denied, exceeds the round cap, or uses an undeclared method, THEN THE SYSTEM SHALL stop retrying and raise `InputRequestError` with code `sampling_denied`, `input_rounds_exceeded` or `undeclared_input_request` respectively. |
| REQ-HSH-047 | unwanted | IF a legacy-era input request cannot be attributed to exactly one in-flight Host request, THEN THE SYSTEM SHALL treat it as unattributed: no stored or session allow decision SHALL apply (stored deny decisions still apply), the human Consumer SHALL be asked with only `allow_once` or `deny` (once) available, the request SHALL be denied when no prompter is attached, and its audit record SHALL be marked `unattributed`. |

### RF-TLS: Toolset agent bridge — `MUST` **[ASSUMED]**

| ID | Type | Criterion |
|---|---|---|
| REQ-TLS-001 | ubiquitous | THE SYSTEM SHALL expose a framework-neutral `Toolset` that lists available tools as specs (name, description, JSON input schema) and invokes them through the `Invoker` under a given Consumer identity. |
| REQ-TLS-002 | ubiquitous | THE SYSTEM SHALL map each FQN to a deterministic, collision-free agent-facing name matching `^[A-Za-z0-9_-]{1,64}$`, and map it back on invocation. |
| REQ-TLS-003 | optional | WHERE the Consumer configures include/exclude filters (by Server or FQN glob), THE SYSTEM SHALL expose only the matching tools. |
| REQ-TLS-004 | event | WHEN the catalog changes, THE SYSTEM SHALL reflect the change in the `Toolset` listing on its next read. |
| REQ-TLS-005 | unwanted | IF consent is denied for an agent's call, THEN THE SYSTEM SHALL return a tool error message to the agent stating the denial, not raise an unhandled exception. |
| REQ-TLS-006 | optional | WHERE the `pydantic-ai` extra is installed, THE SYSTEM SHALL provide an adapter exposing the `Toolset` as a PydanticAI toolset. |
| REQ-TLS-007 | optional | WHERE the `langgraph` extra is installed, THE SYSTEM SHALL provide an adapter exposing the `Toolset` as LangChain-compatible tools usable in LangGraph. |
| REQ-TLS-008 | ubiquitous | THE SYSTEM SHALL import no agent framework when `reflex_mcphost` or `reflex_mcphost.toolset` is imported (Art. 4). |

### RF-APP: MCP Apps rendering — `MUST` **[ASSUMED]**

| ID | Type | Criterion |
|---|---|---|
| REQ-APP-001 | event | WHEN a tool with `_meta.ui.resourceUri` (`ui://…`) is invoked, THE SYSTEM SHALL fetch that resource with `resources/read` from the same Server and render it if its MIME type is `text/html;profile=mcp-app`. |
| REQ-APP-002 | ubiquitous | THE SYSTEM SHALL render MCP App Views only inside a sandbox proxy iframe (`sandbox="allow-scripts allow-same-origin"`) served from an origin distinct from the Host UI's origin, with a CSP built from the resource's `_meta.ui.csp` (`connectDomains`, `resourceDomains`, `frameDomains`, `baseUriDomains`) and the restrictive default when omitted; undeclared domains SHALL never be allowed (Art. 5). |
| REQ-APP-003 | ubiquitous | THE SYSTEM SHALL accept bridge messages only from the proxy window it created, validate their JSON-RPC shape, and SHALL NOT send any request or notification to the View before `ui/notifications/initialized`. |
| REQ-APP-004 | event | WHEN an MCP App requests a tool call through the bridge, THE SYSTEM SHALL route it through the `Invoker` (and therefore consent and audit) with a Consumer identity naming the App. |
| REQ-APP-005 | unwanted | IF the bridge receives a malformed message or a request for an unsupported method, THEN THE SYSTEM SHALL reply with an error and keep the App running. |
| REQ-APP-006 | event | WHEN the View is initialized, THE SYSTEM SHALL send `ui/notifications/tool-input` exactly once, and `ui/notifications/tool-result` when the invocation completes (always after tool-input). |
| REQ-APP-007 | ubiquitous | THE SYSTEM SHALL declare the extension `io.modelcontextprotocol/ui` with `mimeTypes: ["text/html;profile=mcp-app"]` in its client capabilities. |
| REQ-APP-008 | ubiquitous | THE SYSTEM SHALL exclude tools whose visibility lacks `"model"` from every `Toolset`, and SHALL reject App-originated `tools/call` for tools whose visibility lacks `"app"`. |
| REQ-APP-009 | event | WHEN an invocation ends `cancelled`, `timeout` or `connection_lost`, THE SYSTEM SHALL send `ui/notifications/tool-cancelled` to its View. |
| REQ-APP-010 | event | WHEN a View is removed, THE SYSTEM SHALL send `ui/resource-teardown` and wait up to 2 s for its response before destroying the iframe. |
| REQ-APP-011 | ubiquitous | THE SYSTEM SHALL grant no device permissions (`camera`, `microphone`, `geolocation`, `clipboardWrite`) to Views in v0.1, and SHALL show a notice when a resource declares external domains. |
| REQ-APP-012 | ubiquitous | THE SYSTEM SHALL record every View-initiated RPC (method, outcome) in the Server's log stream. |

### RF-UI: Reflex UI, reference app and CLI — `MUST` **[ASSUMED]**

| ID | Type | Criterion |
|---|---|---|
| REQ-UI-001 | event | WHEN the user runs `uvx reflex-mcphost` (or `reflex-mcphost run`), THE SYSTEM SHALL start the reference app bound to `127.0.0.1` and open it in the default browser. |
| REQ-UI-002 | unwanted | IF the Consumer requests binding to a non-loopback interface, THEN THE SYSTEM SHALL refuse unless `--unsafe-allow-remote` is passed, and SHALL warn on startup when it is. |
| REQ-UI-003 | ubiquitous | THE SYSTEM SHALL provide a Servers view showing each Server's state, origin, transport, capabilities and log stream, with connect/disconnect/reconnect/authorize actions. |
| REQ-UI-004 | ubiquitous | THE SYSTEM SHALL provide a Catalog view to browse tools, resources, templates and prompts, and to invoke a tool through a form generated from its `inputSchema`. |
| REQ-UI-005 | ubiquitous | THE SYSTEM SHALL render consent prompts, sampling reviews and elicitation forms as non-blocking UI that shows which Consumer is asking. |
| REQ-UI-006 | ubiquitous | THE SYSTEM SHALL provide a Permissions view (stored decisions, revoke/edit) and an Audit view (REQ-INV-011 filters). |
| REQ-UI-007 | ubiquitous | THE SYSTEM SHALL ship its Reflex components and state in `reflex_mcphost.ui` so they can be embedded in other Reflex apps. |
| REQ-UI-008 | event | WHEN the user runs `reflex-mcphost servers`, THE SYSTEM SHALL print the merged Server list with origin, and shadowed definitions, without starting the UI. — `SHOULD` |

## 7. Non-functional requirements

All measurements run on a GitHub Actions `ubuntu-latest` runner unless stated otherwise.

### Performance
- REQ-NFR-001: Consent evaluation for a stored decision SHALL take < 5 ms p95 with 10,000 stored decisions — measured as the p95 of `ConsentPolicy.evaluate` for a request decided by a stored rule, 10,000 rules loaded, ≥ 1,000 evaluations (pytest-benchmark).
- REQ-NFR-002: Host overhead per `tools/call` (excluding Server time and human consent wait) SHALL be < 20 ms p95 — measured as `Invoker.call` wall time minus the fixture Server's measured handler time, for a no-op tool allowed by a stored rule over the in-process transport, ≥ 1,000 calls.
- REQ-NFR-003: Reference app SHALL be interactive < 10 s after `reflex-mcphost run` on a warm cache, with Server connections proceeding in the background — measured from `reflex-mcphost run --no-browser` process start to the Servers view rendering (Playwright waits for `data-testid="servers-panel"`), on the second run of the same version, in every one of 5 runs. Each start runs Reflex's production frontend build; the first start of a new version may exceed the target and is not measured.
- REQ-NFR-004: The Host SHALL handle 50 configured Servers and 2,000 catalog entries without UI list rendering exceeding 200 ms — measured as the p95 over 20 interactions of the time from a filter keystroke or a `catalog.changed` event to the Catalog/Servers list DOM update (Playwright trace, Chromium).

### Security
- REQ-NFR-010: Secret redaction test (Art. 5) SHALL pass for env values, headers, tokens, and secret-like arguments.
- REQ-NFR-011: The reference app's backend SHALL reject requests whose `Host` is not `127.0.0.1:<port>` or whose `Origin` (when present) is not `http://127.0.0.1:<port>`, where `<port>` is the single port serving UI and backend (DNS-rebinding protection; `localhost` is reserved for the MCP Apps sandbox proxy); the `403` body SHALL tell the user to open `http://127.0.0.1:<port>`.
- REQ-NFR-012: SQLite database and token fallback file SHALL be created with user-only permissions.

### Availability / resilience
- REQ-NFR-020: Failure of any single Server, the keyring, or the `SamplingProvider` SHALL NOT crash the Host (complements REQ-CON-011).

### Portability
- REQ-NFR-030: Supported OS: Linux, macOS, Windows; Python ≥ 3.11. CI runs the core suite on all three.

### Observability
- REQ-NFR-040: The library SHALL log via stdlib `logging` under the `reflex_mcphost` namespace with structured context (server_id, FQN, invocation id); no tracing backend (non-goal).

## 8. Constraints and dependencies

### Technical constraints
- Official `mcp` SDK; pinned dated MCP spec revision (Art. 2).
- Core free of Reflex imports (Art. 4).
- SQLite via SQLModel for `ConsentStore` and `AuditLog`.

### Business constraints
- MIT license; single maintainer; no telemetry of any kind.

### External dependencies

| Dependency | Type | Owner | Status | Risk |
|---|---|---|---|---|
| `mcp` Python SDK | Library | MCP project | **2.3.x pinned**, dual-era `Client` | High — v2 line still moving |
| MCP specification | Standard | MCP project | **2026-07-28** (current) + **2025-11-25** (legacy) | Medium — new revisions during development |
| MCP Apps extension | Standard (extension) | MCP project | **2026-01-26** (Stable) | Medium — may still evolve |
| GitHub Pages (CIMD hosting) | Hosting | Project | Free, project-owned | Low — URL stability is a public contract |
| Reflex | Framework | Reflex Inc. | Active | Medium — iframe/postMessage integration needs custom component |
| `keyring` | Library | jaraco | Stable | Low — headless Linux lacks backend (file fallback) |
| SQLModel | Library | tiangolo | Stable | Low |
| PydanticAI / LangGraph | Optional libraries | Pydantic / LangChain | Active | Medium — adapter API drift |

## 9. User stories

### Epic: Use my existing Servers
**US-001:** As a power user, I want the Host to pick up the Servers from my Claude Desktop and Cursor configs, so that I can use them without re-entering anything.
- Acceptance: REQ-REG-001…006, REQ-REG-008, REQ-UI-001, REQ-UI-003.

**US-002:** As a power user, I want to connect a remote Server that requires OAuth, so that I can use hosted tools securely.
- Acceptance: REQ-AUTH-001…007.

### Epic: Control what tools do
**US-003:** As a power user, I want to be asked before a tool runs, with a preview of its arguments, and choose how long my answer lasts, so that nothing runs without my say.
- Acceptance: REQ-CNS-002…006, REQ-UI-005.

**US-004:** As a power user, I want to be re-asked when a Server silently changes a tool I already trusted, so that a trusted tool can't be swapped for a malicious one.
- Acceptance: REQ-CNS-007, REQ-CAT-009.

**US-005:** As any Consumer, I want an audit trail of every call, allowed or denied, so that I can review what happened.
- Acceptance: REQ-INV-009…012, REQ-UI-006.

### Epic: Give tools to my agent
**US-006:** As an agent developer, I want to hand my PydanticAI or LangGraph agent the Host's tools in a few lines, with consent enforced, so that I don't write host plumbing.
- Acceptance: REQ-TLS-001…008, REQ-CNS-010, REQ-CNS-015.

### Epic: Exercise a Server fully
**US-007:** As a Server author, I want sampling, elicitation, roots and logging to work against my Server, so that I can test those features.
- Acceptance: RF-HSH criteria.

**US-008:** As a Server author, I want my MCP App to render and call tools, so that I can ship an interactive UI.
- Acceptance: REQ-APP-001…006.

## 10. Wireframes / flows (textual)

```
Reference app — left nav: Servers | Catalog | Permissions | Audit | Settings

Servers   [● ready] github (claude_desktop_config.json, http, OAuth)   [Disconnect]
          [◌ auth_required] linear (mcphost.toml, http)                [Authorize]
          [✕ config_error] db (.mcp.json) — ${DB_URL} unset

Catalog   github::create_issue  ▸ form from inputSchema ▸ [Invoke]
             └─ consent drawer: "Human UI wants to call github::create_issue"
                args preview · [Allow once] [Allow this session] [Always] [Deny]
                scope: (•) this tool ( ) whole server
             └─ result panel (content + structuredContent) | MCP App iframe
```

## 11. Risks and mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| `mcp` SDK API changes during development | High | High | Pin SDK version; isolate SDK use in `core.connection.*` and `core.mrtr`; SDK contract tests (F1-11); interop suite in CI |
| MCP Apps extension evolves after v0.1 | Medium | Medium | Pin extension revision; bridge versioned; App rendering behind a feature flag |
| Reflex lacks native sandboxed-iframe + postMessage bridge | Medium | High | Spike early in the plan; implement as a custom Reflex component wrapping minimal JS |
| Consent prompts in Reflex state model block the event loop / UX | Medium | High | Prompts as async futures resolved by UI events; timeout per REQ-CNS-009 |
| Malicious project `.mcp.json` launching commands | Medium | High | REQ-REG-011 trust confirmation |
| Tool definition rug-pull | Medium | High | REQ-CNS-007 |
| Local web app reachable by other sites (DNS rebinding / CSRF) | Medium | High | Loopback bind + Origin/Host checks (REQ-UI-002, REQ-NFR-011) |
| Keyring unavailable on headless Linux | High | Low | `0600` file fallback (REQ-AUTH-004) |
| Deprecated features (sampling/roots/logging) removed in a future revision | High | Medium | Flagged `deprecated`; isolated handlers; drop via Delta Spec when removed |
| Era misdetection on odd legacy servers | Medium | Medium | Rely on SDK detection; per-Server `mode = "legacy"` override in `mcphost.toml` |
| Authorization servers rejecting CIMD loopback redirects | Medium | Medium | DCR fallback; manual `client_id` |

## 12. Estimated timeline (single maintainer)

| Phase | Estimated duration | Deliverable |
|---|---|---|
| Spec & design | 2 weeks | Approved artifacts + Analyze |
| Core (registry, connection, catalog, consent, invoker, handlers) | 4 weeks | `reflex_mcphost.core` with interop suite |
| Toolset + adapters | 1 week | `toolset`, `adapters.pydantic_ai`, `adapters.langgraph` |
| UI + reference app + MCP Apps | 3 weeks | `ui`, `app`, CLI |
| Hardening & release | 1 week | v0.1.0 on PyPI |

## 13. Open questions — resolved at approval (2026-10-30)

Approved with the proposed defaults: [ASSUMED] sections RF-TLS, RF-APP, RF-UI are accepted
as v0.1 scope; config precedence as in RF-REG; Consumer identities `human:ui`,
`agent:<name>`, `app:<server_id>`; reference app ships with no `SamplingProvider`
(sampling disabled by default); composition with `reflex-agentkit` is a separate example,
not v0.1 scope. Original questions kept below for traceability.

1. **Truncated brief:** confirm or replace RF-TLS, RF-APP and RF-UI — they are inferred
   from §1–2 of the brief. Is there additional v0.1 scope after "logging" (e.g. a CLI
   beyond `run`/`servers`, a headless mode, composition with `reflex-agentkit` in the
   reference app)?
2. **Config precedence:** is the proposed order (own > project > editor project > editor
   user > Claude Desktop) the one you want?
3. **Consumer identity granularity:** one identity per agent (named by the library user)
   plus `human:ui` and `app:<server_id>` — sufficient?
4. **Sampling provider in the reference app:** which provider ships by default (none /
   Anthropic / OpenAI-compatible via env)? Default proposed: none, sampling disabled.
5. **Reference app + agentkit:** does v0.1 include the agentkit chat composition, or is
   that a separate example?

## Constitution check

- **Art. 1** — no Server functionality anywhere in scope; Server fixtures only for tests (§5.2).
- **Art. 2** — dual-era MCP 2026-07-28 / 2025-11-25 (REQ-CON-001…019); deprecated features flagged as compatibility (RF-HSH header, REQ-CON-013, REQ-AUTH-003); MCP Apps 2026-01-26.
- **Art. 3** — REQ-CNS-001, REQ-CNS-008, REQ-INV-009, REQ-INV-012, REQ-HSH-001, REQ-APP-004.
- **Art. 4** — REQ-TLS-008, REQ-UI-007; core requirements stated without Reflex.
- **Art. 5** — REQ-REG-007, REQ-AUTH-004/008, REQ-CNS-012, REQ-INV-010, REQ-APP-002/003, REQ-NFR-010…012.
- **Art. 5** (cont.) — REQ-REG-011 (HMAC trust fingerprint incl. env values), REQ-REG-014 (project config cannot change host settings), REQ-NFR-011.
- **Art. 6** — REQ-REG-008/009/010; deterministic total source order (RF-REG).
- **Art. 7** — every MUST criterion above has an ID for test citation.
- **Exceptions requested:** none.

---

## Change history

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1-draft.1 | 2026-10-30 | Ernesto Crespo | Initial draft |
| 1.0 | 2026-10-30 | Ernesto Crespo | Approved; open questions resolved with defaults (§13) |
| 1.1 | 2026-10-30 | Ernesto Crespo | Folded delta `2026-10-mcp-2026-07-28` (dual-era, MRTR, CIMD, MCP Apps 2026-01-26) |
| 1.2 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-analyze-fixes` (Analyze A-01…A-25): total source order, REQ-REG-014, REQ-HSH-046, consent ranking, NFR measurement, priority rule |
| 1.3 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-a`: REQ-CON-008 SDK shutdown policy, REQ-CON-009 concurrent, legacy attribution REQ-HSH-045/047 |
| 1.4 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-b` (S-3, S-5): REQ-NFR-011 single port + 403 notice, REQ-NFR-003 build note |
| 1.5 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-project-trust`: REQ-REG-011 covers HTTP project definitions (URL + headers) |
| 1.6 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase4-prep` (D3): REQ-HSH-012 rejection channel stated per era (no meaning change) |
| 1.7 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase4-impl` (item 18, security review): REQ-AUTH-006/010 — a callback with a foreign `state` or a bad `iss` is refused (never redeemed) and the flow keeps waiting; the deadline aborts it (`timeout` / `iss_mismatch`) |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Product owner / Tech lead | Ernesto Crespo | 2026-10-30 | ☑ Approved |
