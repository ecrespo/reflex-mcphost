# reflex-mcphost — API Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `APPROVED` |
| **API version** | 0.1 (package `reflex-mcphost` 0.1.x) · spec v1.17 |
| **Date** | 2026-11-01 |
| **Related PRD** | `specs/prd/mcphost-v0.1.md` v1.10 |
| **Governed by** | `specs/constitution.md` v1.2 |
| **Pinned protocol set** | MCP `2026-07-28` (preferred) + `2025-11-25` (legacy), MCP Apps `2026-01-26`, `mcp` SDK 2.3.x |

---

## 1. Overview

reflex-mcphost is a **library**, so its "API" is a set of contracts rather than HTTP
endpoints. This document fixes five of them:

| § | Contract | Consumers |
|---|---|---|
| 4 | **Python public API** (`reflex_mcphost`, `reflex_mcphost.toolset`, `reflex_mcphost.adapters.*`) | Agent developers, embedding hosts, the UI layer |
| 5 | **`mcphost.toml` schema** | Power users, library users |
| 6 | **CLI** (`reflex-mcphost`) | Power users |
| 7 | **MCP Apps bridge** (iframe ⇄ Host messages) | MCP App authors (Server side) |
| 8 | **UI layer surface** (`reflex_mcphost.ui`) | Reflex app authors embedding components |

The MCP wire protocol itself is **not** specified here: it is defined by the pinned MCP
specification revision (Art. 2) and implemented by the official SDK. Where this API
mirrors MCP objects (content blocks, `CreateMessageRequestParams`, …) it re-exports the
SDK's types rather than redefining them.

Signatures below are normative for names, parameters, return types and error behavior;
internal structure is left to the Technical Design. Every element cites the REQs it serves.

## 2. Authentication and authorization

There is no authentication *to* the library (it runs in-process) or to the reference app
(single-user, loopback-only — REQ-UI-001/002, REQ-NFR-011). Two authorization concepts
exist:

**2.1 Consumer identity.** Every effectful call carries a `Consumer` (REQ-CNS-015):

| Kind | String form | Created by | Example |
|---|---|---|---|
| `human` | `human:<name>` | UI layer (`human:ui`) or library user | `human:ui` |
| `agent` | `agent:<name>` | Library user, via `Toolset` | `agent:triage-bot` |
| `app` | `app:<server_id>` | Host, for MCP App bridge calls | `app:github` |

`<name>` matches `^[A-Za-z0-9_.-]{1,64}$`. Consumer identity is a *label for consent and
audit*, not a security boundary inside a single process.

**2.2 Remote Server authorization (OAuth 2.1).** Handled by the Host on the user's behalf
(RF-AUTH); exposed through `ConnectionManager.authorize()` / `sign_out()` (§4.5). The only
network listener the library opens itself is the per-flow loopback redirect:

```http
GET http://127.0.0.1:<ephemeral-port>/callback?code=<code>&state=<state>
```

- Bound to `127.0.0.1` only, ephemeral port (a DCR client first re-binds its registered
  port), lives for one flow (≤ 5 min, REQ-AUTH-006).
- The listener MUST complete only on an **authentic** callback: `GET /callback` whose
  `state` matches the flow's (constant-time comparison) and which either carries an AS
  `error` (→ `access_denied`) or carries a `code` and an `iss` that passes RFC 9207
  validation against the discovered metadata (REQ-AUTH-010). The code is redeemed only
  after these checks.
- Any other `/callback` (foreign `state`, bad `iss`, no `code`) MUST get `400` + the page
  "Authorization failed" and the listener MUST keep waiting: a local page or process can
  neither end nor hijack the flow (login CSRF). Other paths — and every request after the
  authentic callback — get `404`, other methods `405`, a request head over 8 KiB `431`.
- A callback with our `state` refused for its `iss` is remembered: if no authentic callback
  follows, the flow ends `iss_mismatch` at its deadline; otherwise the deadline ends it
  `timeout` (verifier discarded, nothing redeemed — REQ-AUTH-006).
- Callback values (`code`, `state`, query) are registered for redaction only once the
  callback is authentic, so a forged request cannot poison redaction.
- Success → HTML page "You can close this tab", tokens stored (REQ-AUTH-004). Pages echo
  nothing and carry `Cache-Control: no-store`, `Referrer-Policy: no-referrer`.
- Client registration order: configured `client_id` → Client ID Metadata Document →
  Dynamic Client Registration (deprecated fallback) (REQ-AUTH-003). Stored credentials are
  bound to the Server's canonical URL (RFC 8707 `resource`) and to the issuer for every
  client-id source; tokens are never presented before discovery and the issuer check
  (REQ-AUTH-011, Data Model §3).
- Neither `code`, `state` nor tokens are logged (REQ-AUTH-008).

## 3. General conventions

### 3.1 Async and concurrency
- All I/O methods are `async` and runnable under `anyio` (asyncio or trio backends).
- Methods marked *sync* are pure reads of in-memory state; they never block on I/O.
- All public objects are safe to use from multiple tasks on the same event loop. They are
  **not** thread-safe; cross-thread use goes through `anyio.from_thread`.

### 3.2 Value types
- Public data types are **frozen Pydantic v2 models** (immutable, JSON-serializable via
  `model_dump(mode="json")`). Enums are `StrEnum`.
- Timestamps are timezone-aware `datetime` in UTC. Durations are `float` seconds in
  parameters and `int` milliseconds in records (`duration_ms`).
- JSON Schemas are `dict[str, Any]` exactly as received from the Server.

### 3.3 Identifiers

| Type | Format | Validation | REQ |
|---|---|---|---|
| `ServerId` | `^[A-Za-z0-9_.-]{1,64}$` | rejected at registry load | REQ-REG-012 |
| `Fqn` | `<server_id>::<name>` | `Fqn.parse(str)` raises `ValueError` | REQ-CAT-002 |
| `InvocationId` | `inv_<26-char ULID>` | generated by Host | REQ-INV-009 |
| `RuleId` | `rule_<26-char ULID>` | generated by Host | REQ-CNS-013 |
| `RequestId` | `req_<26-char ULID>` | consent/elicitation/trust prompts | REQ-CNS-002 |

`Fqn` is a frozen value type with `.server_id`, `.name`, `str(fqn)`; it splits on the
**first** `::` (Server-side names may contain `::`, `server_id` may not).

### 3.4 Error model

Two channels, chosen by whether the failure is an *expected outcome* of the operation:

1. **Outcome values** — expected results of an invocation (denied, timeout, tool error…)
   are returned as `InvocationResult.outcome`, never raised. Agents and UIs must handle
   them anyway; exceptions would make that easy to forget.
2. **Exceptions** — misuse or unavailable dependencies, all subclasses of
   `MCPHostError`:

| Exception | Raised when | REQ |
|---|---|---|
| `MCPHostError` | base class | — |
| `ConfigError` | invalid `mcphost.toml` passed explicitly to `HostConfig.load(path)`; invalid definition passed to `ServerRegistry.add/update` | REQ-REG-006, REQ-REG-012 |
| `UnknownServerError(server_id)` | operation on a `server_id` not in the registry | — |
| `UnknownEntryError(fqn)` | catalog read of a non-existent tool/template/prompt | — |
| `ServerUnavailableError(server_id, state)` | catalog read on a Server that is not `ready` | REQ-CAT-004 |
| `CapabilityNotSupportedError(server_id, capability)` | request the Server did not declare (e.g. `logging/setLevel`, subscribe) | REQ-CON-002 |
| `AuthorizationError(server_id, reason)` | OAuth flow aborted / failed; `reason` is a code, never SDK message text: `not_auth_required`, `state_mismatch`, `iss_mismatch`, `access_denied` (AS `error` on the callback), `timeout`, `cancelled`, `client_id_required`, `registration_failed`, `token_error`, `discovery_failed`, `flow_error`. v0.1: a secret store that fails while a flow starts (reading the stored entry) is reported as `discovery_failed` (known limitation) | REQ-AUTH-003/006/008/010 |
| `ServerRequestError(server_id, code, message, data)` | Server returned a JSON-RPC error to a catalog read / prompt get | — |
| `HostClosedError` | any call after `Host.aclose()` | REQ-CON-009 |
| `InputRequestError(server_id, code)` | an input request inside a catalog read was denied, exceeded the round cap or used an undeclared method; `code` ∈ `sampling_denied`, `input_rounds_exceeded`, `undeclared_input_request` | REQ-HSH-046 |
| `SecretStoreError(server_id, operation)` | the secret store (keyring or `0600` file) failed during an operation the Consumer asked for; v0.1: `sign_out` could not delete the stored credentials (`operation="delete"`, the only value in v0.1 — any other raises `ValueError`; the in-memory credentials are already forgotten). Fixed message `secret store unavailable: could not delete the credentials of '<server_id>'`; raised `from None` — the backend exception is neither chained nor quoted, so no backend text reaches a caller, a traceback or the UI (Art. 5) | REQ-AUTH-007, REQ-NFR-020 |
| `ConsentStoreError` | `ConsentPolicy.grant/update/revoke` could not persist the change (the in-memory rules are left untouched) | REQ-CNS-008 |
| `UnknownRuleError(rule_id)` | `ConsentPolicy.update/revoke` of a rule id that does not exist | REQ-CNS-013 |
| `AuditRecordNotFoundError(invocation_id)` | `AuditLog.get` of an unknown id or of a record still in progress | REQ-INV-011 |
| `UnknownToolError(name)` (also a `KeyError`) | `Toolset.resolve` / `Toolset.call` of an alias not in the current listing | REQ-TLS-004 |

Third-party config problems found during load are **not** exceptions: they are reported
as `ConfigProblem`s (§4.3) so one bad file never prevents startup (REQ-REG-006).

Error messages and `repr()` of every public type omit secret values (Art. 5): fields of
type `SecretStr` render as `'**********'`.

### 3.5 Pagination
Only the audit log is paginated (`AuditPage`, keyset cursor). Catalog lists are returned
whole: the Host already followed MCP pagination cursors to the end (REQ-CAT-001).

### 3.6 Public surface and stability
Public API = names in `__all__` of `reflex_mcphost`, `reflex_mcphost.toolset`,
`reflex_mcphost.ui`, `reflex_mcphost.adapters.pydantic_ai`,
`reflex_mcphost.adapters.langgraph`, plus §5–§7. Everything else (including
`reflex_mcphost.core.*` submodules not re-exported) is private and may change in any
release. SemVer per Art. 9.

---

## 4. Python public API

### 4.1 `Host` — facade and lifecycle

```python
class Host:
    def __init__(
        self,
        config: HostConfig | None = None,          # None → loaded in start() via await HostConfig.aload()
        *,
        prompter: Prompter | None = None,          # None → headless (§4.10)
        sampling_provider: SamplingProvider | None = None,
        platform: Platform | None = None,          # test seams: OS paths, env for ${VAR},
        env: Mapping[str, str] | None = None,      #   keyring backend; default = real ones
        keyring_backend: KeyringBackend | None = None,
    ) -> None: ...

    async def __aenter__(self) -> Self: ...        # = await start()
    async def __aexit__(self, *exc) -> None: ...   # = await aclose()
    async def start(self) -> None: ...
    async def aclose(self) -> None: ...

    registry: ServerRegistry       # §4.3
    connections: ConnectionManager # §4.5
    catalog: Catalog               # §4.6
    consent: ConsentPolicy         # §4.7
    invoker: Invoker               # §4.8
    audit: AuditLog                # §4.9
    events: EventBus               # §4.11

    def set_prompter(self, prompter: Prompter | None) -> None: ...
    def set_mcp_apps(self, enabled: bool) -> None: ...   # REQ-APP-007; default off
    def toolset(
        self,
        consumer: Consumer,
        *,
        include: Sequence[str] = ("*",),           # FQN globs or bare server ids
        exclude: Sequence[str] = (),
    ) -> Toolset: ...                              # §4.12
```

`toolset()` accepts any `Consumer` kind; consent is evaluated for that Consumer (§4.7).
App-originated calls use the bridge (§7), not a Toolset. Guarded like the other members
(`RuntimeError` before `start()`, `HostClosedError` after `aclose()`).

| Behavior | REQ |
|---|---|
| `start()` loads the registry, opens the `ConsentStore`/`AuditLog` DB, then connects every enabled Server concurrently and **returns without waiting** for them to reach `ready`; progress is observable via `events`. | REQ-REG-001, REQ-CON-001, REQ-NFR-003 |
| `start()` raises `ConfigError` only for an explicitly given, invalid `mcphost.toml`; any DB open failure raises `MCPHostError` (the Host cannot run fail-closed consent without it). | REQ-CNS-008 |
| `start()` loads configuration with `HostConfig.aload()` when none was given (`__init__` performs no I/O); all file and keyring I/O in `core` runs through `anyio.to_thread` (Art. 8). | — |
| `aclose()` disconnects all Clients within 15 s total, then closes the DB. Idempotent. | REQ-CON-009 |
| `start()` and `aclose()` run in the same task (the Host owns a task group). Members used before `start()` raise `RuntimeError`; a second `start()` raises `RuntimeError`; after `aclose()` they raise `HostClosedError`. A failed `start()` cleans up and leaves the Host closed. | — |
| Capabilities are computed from `prompter`, `sampling_provider`, roots and the MCP Apps flag (`set_mcp_apps`). **Modern** Servers receive them in every request's `_meta`, so `set_prompter()` takes effect on the next request; **legacy** Servers receive them in `initialize`, so changes apply only after `reconnect()`. The Host does **not** auto-reconnect. | REQ-CON-003, REQ-HSH-006, REQ-HSH-014, REQ-APP-007 |
| `set_mcp_apps(enabled)` turns the MCP Apps extension declaration (`io.modelcontextprotocol/ui`, `mimeTypes: ["text/html;profile=mcp-app"]`) on or off; default **off**, so a library Host that renders no Views does not declare it. Modern Servers see a change on the next request (capability rebuild, DD-003), legacy Servers from their next `initialize` (`reconnect()`). `ui.install` calls it before `start()` once its sandbox proxy runs; callable before or after `start()`. | REQ-APP-007 |

Minimal usage:

```python
from reflex_mcphost import Host, Consumer

async with Host() as host:
    await host.connections.wait_ready("github", timeout=30)
    result = await host.invoker.call(
        "github::create_issue",
        {"repo": "ecrespo/reflex-mcphost", "title": "Hello"},
        consumer=Consumer.agent("triage-bot"),
    )
    print(result.outcome, result.text)
```

### 4.2 `HostConfig`

```python
class HostConfig(BaseModel, frozen=True):
    @classmethod
    def load(
        cls,
        path: Path | None = None,     # None → user-level + project-level discovery (§5.1)
        *,
        cwd: Path | None = None,      # project root for .mcp.json etc.; default Path.cwd()
        env: Mapping[str, str] | None = None,  # for ${VAR} expansion; default os.environ
    ) -> HostConfig: ...

    data_dir: Path                    # SQLite DB, token fallback file
    consent_timeout_s: float = 120.0                     # REQ-CNS-009
    default_tool_timeout_s: float = 60.0                 # REQ-INV-003
    reset_timeout_on_progress: bool = False              # REQ-INV-005
    secret_arg_pattern: str = r"(?i)(passw(or)?d|secret|token|api[_-]?key|credential)"  # REQ-CNS-012
    legacy_sse_fallback: bool = False                    # REQ-CON-013
    log_buffer_size: int = 1000                          # REQ-HSH-030
    oauth_client_metadata_url: AnyHttpUrl = "https://ecrespo.github.io/reflex-mcphost/oauth/client.json"  # REQ-AUTH-012
    sources: SourcesConfig                               # §5.2
    servers: Mapping[ServerId, ServerDefinition]         # own definitions
    overrides: Mapping[ServerId, ServerOverride]         # REQ-REG-010
    sampling: SamplingConfig                             # §5.2 [sampling]
    config_path: Path                 # the user-level (or --config) file read and written
    cwd: Path                         # project root used for discovery
    problems: tuple[ConfigProblem, ...]                  # e.g. invalid server ids in own file
```

The user-level file (default or explicit) that is malformed, has unknown keys or an
unsupported `schema_version` raises `ConfigError` (security settings must never be ignored
silently); a missing default file means defaults and no own Servers; a missing explicit
path raises `ConfigError`. The project-level file never raises (REQ-REG-006/014).

`HostConfig` is the parsed `mcphost.toml` (§5); it does not contain third-party
definitions — those are loaded by the registry.

```python
    @classmethod
    async def aload(cls, path: Path | None = None, *, cwd: Path | None = None) -> HostConfig: ...
```

`aload()` has the same semantics as `load()` with file reads via `anyio.to_thread`.
`load()` is synchronous and intended only for code that runs before an event loop (the CLI
`servers` command, scripts); inside async code use `aload()` (Art. 8).

### 4.3 `ServerRegistry`

```python
class ServerRegistry:
    def __init__(self, config: HostConfig, *, platform: Platform | None = None,
                 env: Mapping[str, str] | None = None, events: EventBus | None = None) -> None: ...
    async def load(self) -> None: ...                            # initial state, no diff/event
    def config_errors(self) -> frozenset[ServerId]: ...          # effective defs with variable problems
    on_diff: list[Callable[[RegistryDiff], Awaitable[None]]]     # awaited after reload() and every mutation
    def definitions(self) -> list[ServerDefinition]: ...        # sync; effective, merged
    def get(self, server_id: ServerId) -> ServerDefinition: ...  # sync; UnknownServerError
    def shadowed(self) -> list[ShadowedDefinition]: ...          # sync; REQ-REG-003
    def problems(self) -> list[ConfigProblem]: ...               # sync; REQ-REG-005/006

    async def reload(self) -> RegistryDiff: ...                  # REQ-REG-013
    async def add(self, definition: ServerDefinition) -> None: ...      # REQ-REG-009
    async def update(self, definition: ServerDefinition) -> None: ...   # REQ-REG-009
    async def remove(self, server_id: ServerId) -> None: ...            # REQ-REG-009
    async def set_enabled(self, server_id: ServerId, enabled: bool) -> None: ...  # REQ-REG-010
```

| Type | Fields |
|---|---|
| `ServerDefinition` | `id: ServerId`, `transport: StdioTransport \| HttpTransport`, `enabled: bool = True`, `timeout_s: float \| None`, `roots: tuple[str, ...] = ()`, `log_level: LogLevel \| None`, `review_sampling_responses: bool = False`, `mode: Literal["auto", "modern", "legacy"] = "auto"` (forces era — REQ-CON-019 escape hatch), `origin: ConfigOrigin` (set by the registry) |
| `StdioTransport` | `kind: Literal["stdio"]`, `command: str`, `args: tuple[str, ...]`, `env: Mapping[str, SecretStr]`, `cwd: Path \| None` |
| `HttpTransport` | `kind: Literal["http"]`, `url: AnyHttpUrl`, `headers: Mapping[str, SecretStr]`, `oauth: OAuthSettings \| None` |
| `OAuthSettings` | `client_id: str \| None`, `client_secret: SecretStr \| None`, `scopes: tuple[str, ...]` |
| `ConfigOrigin` | `kind: SourceKind` (`mcphost_user`, `mcphost_project`, `mcp_json_project`, `vscode_project`, `cursor_project`, `vscode_user`, `cursor_user`, `claude_desktop`, `extra`), `path: Path \| None` (None for API-added), `precedence: int` (1 = highest; assigned by the total order of PRD RF-REG, `extra` files taking consecutive values in list order) |
| `ShadowedDefinition` | `definition: ServerDefinition`, `shadowed_by: ConfigOrigin` |
| `ConfigProblem` | `origin: ConfigOrigin`, `server_id: ServerId \| None`, `code: ConfigProblemCode` (`parse_error`, `invalid_server_id`, `unresolved_variable`, `unsupported_variable`, `invalid_definition`, `project_setting_ignored` — REQ-REG-014), `message: str` (never contains secret values) |
| `RegistryDiff` | `added`, `removed`, `changed`, `unchanged: tuple[ServerId, ...]` |

Rules:
- `env`/`headers` values are `SecretStr` **after** `${VAR}` / `${VAR:-default}` expansion
  (REQ-REG-004/007). Expansion happens at load and again on `reload()`; `SecretStr`
  equality compares values, so a changed env value marks the Server `changed`. A Server is
  also `changed` when its `config_error` flag flips. A difference in `roots` only is
  **not** a change: the Server is `unchanged` and is not reconnected (roots are read from
  the registry on every roots request, so the live session receives
  `notifications/roots/list_changed` instead — `set_roots`, §4.5). `update()` writes `${VAR}` templates
  back when they still expand to the new value, so expanded secrets are never persisted;
  every mutation is validated with the strict own-file parser before writing.
- `${workspaceFolder}` expands only in VS Code-style files; elsewhere it is
  `unsupported_variable`. Third-party `type` values: `stdio`, `http`, `streamable-http`,
  `streamableHttp`, `sse` (→ `HttpTransport.legacy_sse`); `disabled: true` → `enabled=False`.
- Servers with an `unresolved_variable`/`unsupported_variable` problem appear in
  `definitions()` and get connection state `config_error` (REQ-REG-005).
- `add/update/remove` operate **only** on own definitions and write only the user-level
  `mcphost.toml`; calling `update`/`remove` on a third-party `server_id` raises
  `ConfigError("third-party definition; use set_enabled or overrides")` (REQ-REG-008/009).
- `set_enabled` decides by the **effective** origin: an own (`mcphost_user`) definition
  gets `[servers.<id>].enabled`; a third-party or project-`mcphost.toml` definition gets
  `[overrides.<id>] enabled = …` (REQ-REG-010).
- Every mutating method is followed by an implicit connection reconcile identical to
  `reload()`'s (connect added, disconnect removed, reconnect changed).
- Trust of project-level stdio Servers (REQ-REG-011) is **not** a registry field; see
  `Prompter.confirm_trust` (§4.10) and the Data Model.

### 4.4 `Consumer`

```python
class Consumer(BaseModel, frozen=True):
    kind: ConsumerKind           # human | agent | app
    name: str
    @classmethod
    def human(cls, name: str = "ui") -> Consumer: ...
    @classmethod
    def agent(cls, name: str) -> Consumer: ...
    @classmethod
    def app(cls, server_id: ServerId) -> Consumer: ...   # Host-internal use; public for tests
    @classmethod
    def parse(cls, s: str) -> Consumer: ...              # "agent:triage-bot"
    def __str__(self) -> str: ...
```

### 4.5 `ConnectionManager`

```python
class ConnectionManager:
    def status(self, server_id: ServerId) -> ServerStatus: ...       # sync
    def statuses(self) -> list[ServerStatus]: ...                    # sync

    async def connect(self, server_id: ServerId) -> None: ...
    async def disconnect(self, server_id: ServerId) -> None: ...
    async def reconnect(self, server_id: ServerId) -> None: ...      # resets failure counter
    async def wait_ready(self, server_id: ServerId, *, timeout: float) -> ServerStatus: ...

    async def authorize(self, server_id: ServerId) -> AuthorizationFlow: ...
    async def sign_out(self, server_id: ServerId) -> None: ...

    def logs(self, server_id: ServerId) -> list[LogEntry]: ...       # sync
    def append_log(self, server_id: ServerId, entry: LogEntry) -> None: ...  # sync; MCP Apps bridge seam
    async def set_log_level(self, server_id: ServerId, level: LogLevel) -> None: ...
    async def set_roots(self, server_id: ServerId, roots: Sequence[str]) -> None: ...
```

| Method | Behavior | Errors | REQ |
|---|---|---|---|
| `connect` | No-op if `ready`/`connecting`. Starts the lifecycle; returns when the attempt is *launched*, not completed. | `UnknownServerError` | REQ-CON-001 |
| `disconnect` | Graceful stop (stdio: close stdin → 2 s → terminate → 2 s → kill the process group / Job Object — SDK policy; legacy HTTP: `DELETE` session; modern HTTP: close the listen stream). State → `disconnected`, no auto-reconnect. | `UnknownServerError` | REQ-CON-008, REQ-CON-014 |
| `reconnect` | `disconnect` + `connect`; resets backoff and the 10-failure counter. Also the way out of `failed`. | `UnknownServerError` | REQ-CON-006 |
| `wait_ready` | Resolves when the state is `ready` **and** the Catalog's initial full refresh (Flow B step 6) for the current connection has finished, successfully or not (lists that failed to load are simply absent); both are awaited within the same `timeout`. If the connection drops and comes back while waiting, it waits for the new connection's refresh, so right after it returns `catalog.*` reads and `invoker.call` see the Server's entries (§4.1 usage). | `TimeoutError`; `ServerUnavailableError` if state becomes `failed`/`config_error`/`disabled`/`auth_required` while waiting | — |
| `authorize` | Only valid in `auth_required`. Binds the loopback listener and starts a connection attempt in which the SDK runs the flow (state → `connecting`); returns once the authorization URL is known. A second call during a flow returns the same flow. The flow ends when the attempt leaves `connecting`: `ready` → success; otherwise the recorded reason, `flow_error` if none was recorded, `cancelled` for a stop (`disconnected`), an unknown Server or a closed Host. | `UnknownServerError`; `AuthorizationError(reason="not_auth_required")` if not in `auth_required` | REQ-AUTH-002 |
| `sign_out` | Cancels a running flow, disconnects, deletes stored tokens and client credentials (the whole `oauth:{server_id}` entry). Idempotent. The in-memory credentials are forgotten before the stored ones are deleted (fail closed). | `UnknownServerError`; `SecretStoreError` (stored entry could not be deleted) | REQ-AUTH-007 |
| `logs` | Snapshot of the per-Server ring buffer (server `notifications/message` + stdio stderr). | `UnknownServerError` | REQ-HSH-030, REQ-CON-007 |
| `append_log` | Host-internal seam used by the MCP Apps bridge (public for tests): appends `entry` (text, level, logger) to the Server's ring buffer after the redaction filter and publishes `server.log`; the ring stamps the time of the append (the entry's own timestamp is ignored); a parked Server (`disabled` / `config_error`) has no ring and the entry is dropped. | `UnknownServerError` (id not in the registry) | REQ-APP-012, REQ-HSH-030 |
| `set_log_level` | *Deprecated (SEP-2577), compatibility.* Legacy: sends `logging/setLevel`. Modern: stores the level and sends it as `io.modelcontextprotocol/logLevel` in each subsequent request's `_meta`. | `UnknownServerError`, `ValueError` (unknown level), `CapabilityNotSupportedError` (no logging capability), `ServerUnavailableError` | REQ-HSH-031 |
| `set_roots` | *Deprecated (SEP-2577), compatibility.* Persists roots for the Server (own definition or override; no reconnect). Legacy and connected: sends `notifications/roots/list_changed` only if the live Client declared roots (otherwise the change is logged and applies on the next `reconnect()`; a delivery failure is logged, the roots stay saved); modern: served on the next roots input request (the Client is rebuilt when the roots go between empty and non-empty, DD-003). Only absolute `file://` URIs. By default a Server has no roots, so roots are not declared and a modern `roots/list` input request ends `undeclared_input_request` (REQ-HSH-044). | `UnknownServerError`, `ValueError` for non-`file://` | REQ-HSH-021/022 |

```python
class AuthorizationFlow:
    authorization_url: str | None    # open in the system browser (UI does it; CLI prints it);
                                     # None when no browser step was needed (e.g. after a refresh)
    async def wait(self) -> None: ...  # completes on success (at once if authorization_url is None);
                                       # raises AuthorizationError (reason codes, §3.4) otherwise
    async def cancel(self) -> None: ...
```

`ServerStatus` fields: `server_id`, `state: ServerState`, `reason: str | None`,
`since: datetime`, `protocol_version: str | None`, `server_info: Implementation | None`
(SDK type), `capabilities: ServerCapabilities | None` (SDK type),
`era: Literal["modern", "legacy"] | None` (REQ-CON-002), `legacy_transport: bool`,
`consecutive_failures: int`, `next_retry_at: datetime | None`. `era`, `protocol_version`,
`server_info` and `capabilities` keep their last-known values after the Server leaves
`ready`. Failure reasons: `transport_error: <ExceptionType>` (no message text, Art. 5),
`transport_lost`, `unsupported_protocol_version: requested X, supported Y`,
`command_not_found: <exe>`, `untrusted_project_server`, `internal_error`,
`http_unauthorized` (HTTP 401 on a Server with static headers, REQ-AUTH-009: no OAuth is
started). While `auth_required`, `reason` is `auth_required` or `client_id_required`
(REQ-AUTH-003: no configured client id, no CIMD support and DCR unavailable — the UI/CLI
asks to set `[servers.<id>.oauth] client_id` in `mcphost.toml`).

**Server state machine** (REQ-CON-001…015, REQ-AUTH-001, REQ-REG-005):

```
                       registry: disabled/enabled
   ┌──────────┐   ◄───────────────────────────────►   ┌──────────┐
   │ disabled │                                       │config_err│ (unresolved/unsupported var)
   └──────────┘                                       └──────────┘
        │ enable                                           ▲ reload fixes → disconnected
        ▼                                                  │
 ┌──────────────┐ connect  ┌────────────┐ disc/ini┌───────┐│
 │ disconnected │────────►│ connecting │────────►│ ready ││
 └──────────────┘         └────────────┘         └───────┘│
   ▲    ▲  disconnect()      │   │   │ 401        │  │ transport lost
   │    └────────────────────┼───┼───┼────────────┘  ▼
   │                         │   │   ▼          ┌──────────────┐
   │                         │   │ ┌─────────────┐ │ reconnecting │──retry ok──► ready
   │                         │   │ │auth_required│ └──────────────┘
   │                         │   │ └─────────────┘   │ 10 consecutive failures
   │                         │   │  authorize ok→connecting
   │                         │   ▼ version mismatch  ▼
   │                         │ ┌────────┐ ◄──────────┘
   └──── reconnect() ────────┴─│ failed │
                               └────────┘
```

| From | Event | To |
|---|---|---|
| `disconnected` | `connect()` / startup | `connecting` |
| `connecting` | `server/discover` OK (modern) or `initialize` + `initialized` OK (legacy) | `ready` |
| `connecting` | HTTP 401 with auth metadata (no static headers) | `auth_required` |
| `connecting` | HTTP 401 on a Server with static headers | `failed` (reason `http_unauthorized`, no retry until `reconnect()` or a definition change) |
| `connecting` | authorization aborted, failed or timed out | `auth_required` |
| `connecting` | unsupported protocol version | `failed` |
| `connecting` | project Server (stdio or HTTP) not trusted (declined, prompter error, or no prompter) | `failed` (reason `untrusted_project_server`, no retry, nothing launched or sent) |
| `connecting` | stdio executable not found | `failed` (reason `command_not_found: <executable>`, no retry) |
| `connecting` | transport error | `reconnecting` |
| `ready` | transport lost | `reconnecting` |
| `ready` | HTTP 401 on the live session (no refresh possible, or the refresh-once failed) | `reconnecting` (the transport is marked closed; the reconnect attempt's 401 then leads to `auth_required` — there is no direct `ready → auth_required` edge, two `server.state_changed` events are published) |
| `ready` | legacy HTTP: 404 on session id | stays `ready`: the Client re-initializes internally, no `server.state_changed` is emitted, a `server.log` entry is recorded (REQ-CON-015) |
| `ready` | modern HTTP: listen stream `5xx` / connection failure | `reconnecting` |
| `reconnecting` | attempt OK | `ready` |
| `reconnecting` | 10th consecutive failure | `failed` |
| `reconnecting` | HTTP 401 with auth metadata (e.g. token expired while away) | `auth_required` |
| `auth_required` | `authorize()` (the flow runs inside the attempt; success → `ready`) | `connecting` |
| `failed` / `auth_required` / `reconnecting` | `reconnect()` | `connecting` |
| any connected | `disconnect()` | `disconnected` |
| any | `set_enabled(False)` | `disabled` |
| any | definition has variable problem after reload | `config_error` |

### 4.6 `Catalog`

```python
class Catalog:
    def tools(self, server_id: ServerId | None = None) -> list[ToolEntry]: ...                    # sync
    def resources(self, server_id: ServerId | None = None) -> list[ResourceEntry]: ...            # sync
    def resource_templates(self, server_id: ServerId | None = None) -> list[ResourceTemplateEntry]: ...  # sync
    def prompts(self, server_id: ServerId | None = None) -> list[PromptEntry]: ...                # sync
    def get_tool(self, fqn: Fqn | str) -> ToolEntry: ...                                          # sync; UnknownEntryError

    async def read_resource(self, server_id: ServerId, uri: str, *, consumer: Consumer = Consumer.human()) -> ResourceContents: ...  # REQ-CAT-005
    async def read_template(self, fqn: Fqn | str, params: Mapping[str, str], *, consumer: Consumer = Consumer.human()) -> ResourceContents: ...  # REQ-CAT-006
    async def get_prompt(self, fqn: Fqn | str, arguments: Mapping[str, str] | None = None, *, consumer: Consumer = Consumer.human()) -> PromptResult: ...  # REQ-CAT-007
    async def subscribe(self, server_id: ServerId, uri: str) -> None: ...                         # REQ-CAT-010 (COULD)
    async def unsubscribe(self, server_id: ServerId, uri: str) -> None: ...
```

| Type | Fields |
|---|---|
| `ToolEntry` | `fqn: Fqn`, `server_id`, `name`, `title: str \| None`, `description: str \| None`, `input_schema: dict`, `output_schema: dict \| None`, `annotations: ToolAnnotations \| None` (SDK type, display only — REQ-CAT-011), `definition_hash: str` (`sha256:<hex>`, REQ-CAT-009), `ui_resource_uri: str \| None` (`_meta.ui.resourceUri`, fallback deprecated `_meta["ui/resourceUri"]` — REQ-APP-001, REQ-CAT-014), `visibility: tuple[Literal["model", "app"], ...]` (default both — REQ-CAT-014), `stale: bool` (REQ-CAT-004) |
| `ResourceEntry` | `server_id`, `uri`, `name`, `title`, `description`, `mime_type`, `size: int \| None`, `stale` |
| `ResourceTemplateEntry` | `fqn`, `server_id`, `name`, `uri_template`, `title`, `description`, `mime_type`, `stale` |
| `PromptEntry` | `fqn`, `server_id`, `name`, `title`, `description`, `arguments: tuple[PromptArgument, ...]` (SDK), `stale` |
| `ResourceContents` | `server_id`, `uri`, `contents: tuple[TextResourceContents \| BlobResourceContents, ...]` (SDK types) |
| `PromptResult` | `fqn`, `description`, `messages: tuple[PromptMessage, ...]` (SDK type) |

Rules:
- Lists are ordered by `server_id`, then `name`; entries of non-`ready` Servers remain
  with `stale=True` (REQ-CAT-004). Async reads on a non-`ready` Server raise
  `ServerUnavailableError`.
- `definition_hash` = SHA-256 of canonical JSON (sorted keys, no whitespace) of
  `{name, title, description, inputSchema, outputSchema, annotations}`.
- **Consent scope decision:** `resources/read`, `prompts/get` and subscriptions are
  protocol *reads* and are **not** consent-gated nor audited in v0.1 (Art. 3 covers
  effectful operations: `tools/call`, sampling, App-originated tool calls). Recorded here
  so the Analyze gate can confirm it is a deliberate choice. Sampling input requests that
  arrive *inside* these reads are still consent-gated (REQ-HSH-001).
- `read_resource`, `read_template` and `get_prompt` run the MRTR loop (REQ-HSH-040…044);
  their input requests (e.g. elicitation) reach the `Prompter`. `consumer` is the Consumer
  of those input requests (consent and audit of inner sampling); a denied, over-cap or
  undeclared input request raises `InputRequestError` (REQ-HSH-046). An
  `InputHandlerError` raised by an input-request handler propagates unchanged (no
  `InputRequestError` code maps it).
- Reads run the Host's own MRTR loop on `client.session.*` with `allow_input_required=True`
  (not the SDK auto-loop), so they bypass the SDK response cache (Tech Design DD-001).
- `subscribe`/`unsubscribe`: modern → updates the `resourceSubscriptions` opted in on the
  `subscriptions/listen` stream; legacy → `resources/subscribe` / `resources/unsubscribe`.
- Tools rejected for an invalid `x-mcp-header` annotation never appear (REQ-CON-018).

### 4.7 `ConsentPolicy`

Model. A **rule** says *who* (Consumer or any), *what operation*, *on which target*,
*allow or deny*, *for how long*. The four PRD decisions map onto rules as follows:

| `ConsentDecision` (PRD vocabulary) | Effect | Lifetime | Stored? |
|---|---|---|---|
| `allow_once` | allow | this invocation | no (REQ-CNS-004) |
| `allow_session` | allow | until Host session ends | in memory (REQ-CNS-005) |
| `allow_always` | allow | persistent | `ConsentStore` (REQ-CNS-006) |
| `deny` | deny | `once` (default) \| `session` \| `always` via `ConsentAnswer.deny_lifetime` | per lifetime (REQ-CNS-006) |

> The PRD lists "session" among scopes; in this API it is a **lifetime**, not a target.
> Targets are `tool > server > global` (REQ-CNS-003). This is a terminology refinement,
> not a behavior change.

```python
class ConsentPolicy:
    def rules(self, *, include_session: bool = True) -> list[ConsentRule]: ...    # sync
    async def grant(
        self,
        *,
        effect: Literal["allow", "deny"],
        operation: OperationKind,                  # tool_call | sampling
        target: ConsentTarget,
        consumer: Consumer | None = None,          # None = any Consumer
        lifetime: Literal["session", "always"] = "always",
    ) -> ConsentRule: ...                          # origin=programmatic, REQ-CNS-014; tool target: hash from catalog, UnknownEntryError if unknown
    async def update(self, rule_id: RuleId, *, effect: ..., lifetime: ...) -> ConsentRule: ...  # REQ-CNS-013
    async def revoke(self, rule_id: RuleId) -> None: ...                                          # REQ-CNS-013
    def register_preview(self, target: ConsentTarget, hook: ArgumentPreviewHook) -> None: ...      # REQ-CNS-011

    async def evaluate(self, request: ConsentRequest) -> ConsentOutcome: ...   # used by Invoker / sampling handler
```

| Type | Fields |
|---|---|
| `OperationKind` | `tool_call`, `sampling` |
| `ConsentTarget` | `kind: Literal["tool", "server", "global"]`, `server_id: ServerId \| None`, `fqn: Fqn \| None` — constructors `ConsentTarget.tool(fqn)`, `.server(id)`, `.global_()`. `tool` is invalid for `sampling`. |
| `ConsentRule` | `id: RuleId`, `effect`, `operation`, `target`, `consumer: Consumer \| None`, `lifetime` (default `"always"`), `origin: Literal["prompt", "programmatic"]` (default `"programmatic"`), `definition_hash: str \| None` (tool target only — REQ-CNS-007), `suspended: bool`, `created_at`, `updated_at` |
| `ConsentRequest` | `id: RequestId`, `operation`, `consumer`, `server_id`, `server_name: str` (from `serverInfo` / origin), `tool: ToolEntry \| None`, `preview: ArgumentPreview`, `definition_changed: bool`, `sampling: SamplingReview \| None`, `attribution: Literal["exact", "unattributed"] = "exact"` (REQ-HSH-047; for `unattributed`, `consumer` is `Consumer.human()`, the asked party). Validated on construction: `tool_call` requires a `tool` of the same Server; `sampling` requires `tool=None`; `unattributed` requires a human Consumer. |
| `ArgumentPreview` | `format: Literal["json", "markdown", "text"]`, `body: str`, `redacted_fields: tuple[str, ...]` |
| `ConsentAnswer` | `decision: ConsentDecision`, `target_kind: Literal["tool", "server", "global"] = "tool"`, `deny_lifetime: Literal["once", "session", "always"] = "once"`, `edited_sampling: CreateMessageRequestParams \| None` (REQ-HSH-002; honored only when the sampling is allowed). For `sampling`, `target_kind="tool"` is mapped to a `server` target. For an `unattributed` request only `allow_once`, or `deny` with `deny_lifetime="once"`, is accepted; anything else is treated as `deny` (fail closed). |
| `ConsentOutcome` | `allowed: bool`, `source: Literal["rule", "prompt", "default_deny", "error", "timeout", "no_prompter"]`, `rule_id: RuleId \| None`, `reason: str \| None`, `edited_sampling: … \| None` |

`ArgumentPreviewHook = Callable[[ToolEntry, Mapping[str, Any]], ArgumentPreview | Awaitable[ArgumentPreview]]`

**Evaluation algorithm** (normative; REQ-CNS-001…015, REQ-HSH-047):

0. If `request.attribution == "unattributed"` (REQ-HSH-047): collect only matching `deny`
   rules — persistent and session, scoped to any Consumer — if any, deny
   (`source="rule"`); else go to step 4 (prompt, or `no_prompter` deny). No rule is ever
   created from the answer.

1. Collect non-suspended rules (persistent and session) matching `operation`,
   `server_id`/`fqn`, and `consumer` (`rule.consumer is None` or equal).
2. Rank each by `(target specificity: tool=3, server=2, global=1; consumer specificity:
   specific=2, any=1)`, compared lexicographically. Keep only the rules with the highest
   rank present. If any of them is `deny` → deny; else allow (REQ-CNS-003).
3. A matching **`allow`** `tool` rule whose `definition_hash` ≠ current hash is
   **suspended** (persisted), skipped, and the request is marked `definition_changed=True`
   (REQ-CNS-007). **`deny` `tool` rules always apply**, whatever the hash: a definition
   change never loosens a decision (REQ-CNS-007 speaks only of `allow_always`). When the
   current hash is unknown, matching `allow` tool rules are skipped (fail closed) but not
   suspended, and `definition_changed` stays `False`.
4. No decisive rule → if a prompter is attached, `Prompter.ask_consent` with
   `consent_timeout_s` (timeout → deny, `source="timeout"`, REQ-CNS-009); else deny with
   `source="no_prompter"` (REQ-CNS-010).
5. Any exception in steps 1–4 → deny with `source="error"` (REQ-CNS-008). A stored rule
   that cannot be loaded (corrupt row) poisons its Server — every Server when the row's
   target is global or unreadable: their evaluations deny with `source="error"` until the
   row is revoked or repaired; the row is kept, never silently dropped.
6. Prompt answers create rules per the mapping table; `target_kind="server"`/`"global"`
   widens the rule. The answering Consumer identity scopes the rule to the *requesting*
   Consumer (REQ-CNS-015) — a human approving an agent's call grants it to that agent only.
   A persistent answer whose write fails applies once (`rule_id=None`, reason recorded —
   Tech Design DD-005). A prompter return value that is not a `ConsentAnswer` → deny
   `source="error"`. A cancelled prompt publishes `consent.resolved` and re-raises the
   cancellation.

There is **one rule per key** (operation, target, Consumer) across lifetimes: a new rule
for an existing key replaces it; a session rule replacing a persistent one deletes the
stored row, and `update()` moves a rule into or out of the store when its lifetime
changes.

`server`/`global` targets are not suspended by definition changes and cover tools added
later (REQ-CNS-007); the consent prompt's wording for those targets says so.

Default preview: pretty JSON of the arguments with values replaced by `"••••••"` for
properties with `format: password`, `writeOnly: true`, or names matching
`secret_arg_pattern` (REQ-CNS-012), listed in `redacted_fields` as dotted paths with list
indices (`items.0.token`). Redaction follows local `$ref` and `allOf` / `anyOf` / `oneOf`;
values nested deeper than 64 levels are redacted whole. Preview hooks receive a deep copy
of the arguments and run with a 5 s timeout; a raising or timed-out hook falls back to the
default preview.

### 4.8 `Invoker`

```python
class Invoker:
    async def call(
        self,
        fqn: Fqn | str,
        arguments: Mapping[str, Any] | None,
        *,
        consumer: Consumer,
        timeout: float | None = None,                     # None → per-Server → default
        on_progress: ProgressCallback | None = None,
        reset_timeout_on_progress: bool | None = None,
    ) -> InvocationResult: ...

    def start(self, fqn, arguments, *, consumer, timeout=None,
              reset_timeout_on_progress=None) -> InvocationHandle: ...   # sync; schedules the call

class InvocationHandle:
    id: InvocationId
    async def result(self) -> InvocationResult: ...
    async def cancel(self) -> None: ...                     # REQ-INV-004
    def progress(self) -> AsyncIterator[Progress]: ...      # REQ-INV-005
```

`ProgressCallback = Callable[[Progress], Awaitable[None] | None]`;
`Progress(progress: float, total: float | None, message: str | None)`.

`call()` is `start()` + `result()`. If the *awaiting task* is cancelled, the Host cancels
the request (stdio and legacy HTTP: `notifications/cancelled`; modern HTTP: closes the
request's response stream — REQ-INV-003/004), records outcome `cancelled`, and re-raises
the cancellation (anyio semantics preserved).

**Pipeline (normative order):**

| Step | Failure → outcome | REQ |
|---|---|---|
| 1. Resolve FQN in catalog | `not_found` | — |
| 1a. App scope — only when `consumer.kind == app`: the FQN's Server must equal `consumer.name` and the tool's `visibility` must include `"app"` | `denied` with `error.code = "app_scope"`, `details.reason` ∈ `foreign_server`, `not_app_visible`; no consent prompt; one audit record (`decision_source = not_evaluated`, `error_code = app_scope`) | REQ-APP-004, REQ-APP-008 |
| 2. Server `ready`? | `server_unavailable` | REQ-CAT-004 |
| 3. Validate `arguments` against `input_schema` | `invalid_arguments` (no consent prompt); a schema that fails to compile → `protocol_error` / `invalid_input_schema` | REQ-INV-002 |
| 4. `ConsentPolicy.evaluate` | `denied` | REQ-CNS-001 |
| 4a. Re-resolve the FQN after consent (the evaluation may have waited on the Prompter) and compare `definition_hash` with the entry evaluated at step 4 | entry gone → `not_found`; hash differs → `denied` with `error.code = "definition_changed"` (`details` = `{"evaluated": <old hash>, "current": <new hash>}`); no second prompt in this attempt; one audit record with the step-4 `decision_source` | REQ-INV-016, REQ-CNS-007 |
| 5. Write audit record *started* | `audit_unavailable` (call not sent) | REQ-INV-012 |
| 6. `tools/call` with timeout, progress, cancellation, looping on `input_required` (≤ 8 rounds) | `timeout` / `cancelled` / `protocol_error` (incl. `input_rounds_exceeded`, `undeclared_input_request`, `input_handler_failed` with `details.reason` ∈ `invalid_elicitation_schema`, `invalid_elicitation_url`, `provider_error`, `provider_timeout`, `audit_unavailable`) / `connection_lost` / `denied` (`sampling_denied`) | REQ-INV-001/003/004/005/015, REQ-HSH-040…044 |
| 7. Validate `structuredContent` against `output_schema` (skipped for `isError: true`) — the only output check: the SDK's own result validation is disabled per session (below) | `protocol_error` with `error.code` = `missing_structured_content` (an output schema but no `structuredContent`), `invalid_output_schema` (the schema cannot be used, incl. a pattern evaluation that timed out, REQ-NFR-021) or `output_schema_mismatch` (message names the first issue — path and keyword message, no instance values —; `details` = the bounded issue list); the failing result's `content` and `structured_content` are not returned | REQ-INV-007, REQ-NFR-021 |
| 8. `isError: true` | `tool_error` | REQ-INV-008 |
| 9. Finalize audit record | — (failure here is logged; result still returned) | REQ-INV-009 |

Steps 1–4a failures (including 1a) are audited too (one record, outcome as above; step 5 is where the
record is first written for allowed calls, and a single record is written directly for
early failures). Exactly one record per attempt (REQ-INV-009). If that single record of
an early failure cannot be written, the result is `audit_unavailable` with
`error.details["attempted_outcome"]` naming the outcome that would have been recorded.
A Consumer that retries after `definition_changed` gets a fresh evaluation, in which the
REQ-CNS-007 rug-pull rules apply; the window between step 4a and the send (step 5, one
database write) is not re-checked.
The SDK's `ClientSession.call_tool` would re-validate `structuredContent` with stock
`jsonschema` (stdlib `re`, no time bound, on the event loop) before step 7; the Host
replaces that per-session check with a no-op (TD DD-010, pinned by the SDK contract test),
so a Server's output `pattern` is only ever evaluated under the REQ-NFR-021 bound. A result
that fails step 7 is unvalidated Server output and is never handed on: the
`InvocationResult` carries the error, empty `content` and no `structured_content`.
`start()` takes no `on_progress`; consume progress with `InvocationHandle.progress()`.

```python
class InvocationOutcome(StrEnum):
    success = "success"
    tool_error = "tool_error"
    protocol_error = "protocol_error"
    timeout = "timeout"
    cancelled = "cancelled"
    denied = "denied"
    invalid_arguments = "invalid_arguments"
    not_found = "not_found"
    server_unavailable = "server_unavailable"
    audit_unavailable = "audit_unavailable"
    connection_lost = "connection_lost"        # REQ-INV-015

class InvocationResult(BaseModel, frozen=True):
    id: InvocationId
    fqn: Fqn
    consumer: Consumer
    outcome: InvocationOutcome
    content: tuple[ContentBlock, ...]          # SDK content block union — REQ-INV-006
    structured_content: dict[str, Any] | None  # REQ-INV-006
    error: InvocationError | None              # code, message, details (validation errors, JSON-RPC error)
    consent: ConsentOutcome | None
    started_at: datetime
    duration_ms: int
    @property
    def ok(self) -> bool: ...                  # outcome == success
    @property
    def text(self) -> str: ...                 # concatenated text blocks (convenience for agents)
```

Timeout covers the whole invocation including MRTR rounds, **excluding** time spent
waiting on the `Prompter`. Results without `resultType` are treated as complete
(REQ-INV-014).

Concurrency: unlimited concurrent invocations, including on one Server (REQ-INV-013);
consent prompts for concurrent calls are independent requests.

### 4.9 `AuditLog`

```python
class AuditLog:
    async def query(self, query: AuditQuery = AuditQuery()) -> AuditPage: ...   # REQ-INV-011
    async def get(self, invocation_id: InvocationId) -> AuditRecord: ...

class AuditQuery(BaseModel, frozen=True):
    since: datetime | None = None
    until: datetime | None = None
    server_id: ServerId | None = None
    fqn: Fqn | None = None
    consumer: Consumer | None = None
    outcome: InvocationOutcome | None = None
    operation: OperationKind | None = None
    limit: int = 50                      # 1..500
    cursor: str | None = None            # opaque; from AuditPage.next_cursor

class AuditPage(BaseModel, frozen=True):
    records: tuple[AuditRecord, ...]     # newest first
    next_cursor: str | None

class AuditRecord(BaseModel, frozen=True):
    invocation_id: InvocationId
    operation: OperationKind
    timestamp: datetime                  # UTC, start
    consumer: Consumer
    server_id: ServerId
    fqn: Fqn | None                      # None for sampling
    args_hash: str | None                # "hmac-sha256:<hex>", REQ-INV-010; None for sampling
    decision_source: str                 # ConsentOutcome.source
    rule_id: RuleId | None
    duration_ms: int | None
    outcome: InvocationOutcome           # sampling reuses: success/denied/protocol_error/timeout
    error_code: str | None
    input_rounds: int                    # MRTR rounds fulfilled, REQ-INV-009
    attribution: Literal["exact", "unattributed"]  # REQ-HSH-047
```

There are no write methods: records are created only by the Invoker and the sampling
handler (Art. 3). No raw arguments, results or sampling messages are stored (Art. 5).

### 4.10 `Prompter` — the human-in-the-loop seam

One protocol implemented by any UI (the Reflex UI ships one; tests use fakes).

```python
class Prompter(Protocol):
    supports_elicitation: bool

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer: ...             # REQ-CNS-002, REQ-HSH-002
    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict: ...  # REQ-HSH-004
    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse: ...       # REQ-HSH-010/011
    async def confirm_trust(self, request: TrustRequest) -> bool: ...                     # REQ-REG-011
```

| Type | Fields |
|---|---|
| `SamplingReview` | `server_id`, `params: CreateMessageRequestParams` (SDK: messages, systemPrompt, modelPreferences, maxTokens, …) |
| `SamplingResponseReview` | `request_id`, `server_id`, `result: CreateMessageResult` |
| `SamplingVerdict` | `approved: bool`, `edited_result: CreateMessageResult \| None` |
| `ElicitationRequest` | `id: RequestId`, `server_id`, `server_name`, `message: str`, `mode: Literal["form", "url"]`, `requested_schema: dict \| None` (form mode; already validated against the spec's allowed subset — REQ-HSH-012), `url: str \| None` (url mode — REQ-HSH-015), `errors: Mapping[str, str]` (field → message on re-prompt — REQ-HSH-013), `attribution: Literal["exact", "unattributed"] = "exact"` (REQ-HSH-047; informs the UI only — elicitation is not consent-gated) |
| `ElicitationResponse` | `action: Literal["accept", "decline", "cancel"]`, `content: dict[str, str \| int \| float \| bool \| list[str]] \| None` |
| `TrustRequest` | `id`, `server_id`, `origin: ConfigOrigin`, `transport: Literal["stdio", "http"]`, `destination: str \| None` (HTTP: authoritative `scheme://host[:port]` of the expanded URL — no userinfo, path or query), `url_template: str \| None` (HTTP: the file's template with userinfo removed; `None` when its host differs from `destination` or for API-built definitions), `resolved_command: str \| None` (stdio: absolute path of the executable that will run), `header_keys: tuple[str, ...]` (keys only, Art. 5), `command: str`, `args: tuple[str, ...]`, `cwd: Path \| None`, `env_keys: tuple[str, ...]` (keys only, Art. 5), `risky_env_keys: tuple[str, ...]` (subset of `env_keys` from a built-in list known to load code: `NODE_OPTIONS`, `PYTHONPATH`, `PYTHONSTARTUP`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `DYLD_INSERT_LIBRARIES`, `DYLD_LIBRARY_PATH`, `PERL5OPT`, `RUBYOPT`, `JAVA_TOOL_OPTIONS`, `BASH_ENV`, `ENV`, `PATH`, `PATHEXT`, `NODE_PATH`, `PYTHONHOME`, `PYTHONUSERBASE`, `GIT_SSH_COMMAND`, `LD_AUDIT`, `DYLD_FRAMEWORK_PATH`; matched case-insensitively, reported as written) |

Rules:
- Prompter methods are invoked identically for MRTR input requests and legacy
  server-initiated requests (REQ-HSH-045).
- `elicit()` content is validated by the Host against `requested_schema` **after** the
  prompter returns; on failure the Host calls `elicit()` again with
  `ElicitationRequest.errors` populated (field → message) instead of responding to the
  Server (REQ-HSH-013), at most 10 prompts in total; after the 10th invalid answer the
  Host answers `cancel` to the Server. The Reflex prompter keeps the same form open.
- `confirm_trust` is called once per (server_id, trust fingerprint) (Data Model §2.4); a
  change to command, args, cwd, origin path, any env name or value, the URL or any header
  name or value re-triggers it. Stdio fields are empty for HTTP requests and vice versa.
  `False`, a prompter exception or no prompter → state `failed` with reason
  `untrusted_project_server`.
- `confirm_trust` is not bounded by `consent_timeout_s`: the Server stays `connecting`
  until the prompter answers, the Server is disconnected or removed (the wait is
  cancelled) or the Host closes. A UI shows the pending trust prompt until then.
- Legacy input requests are attributed per REQ-HSH-045/047; unattributed ones reach
  `ask_consent` / `elicit` with `attribution="unattributed"` and the UI says so.
- Exceptions raised by a prompter are treated as deny / `cancel` / untrusted (fail closed).
- `elicit()` and `review_sampling_response()` are bounded by `consent_timeout_s`; a
  timeout → `cancel` / rejected.
- Core never opens a browser: url-mode elicitation URLs (REQ-HSH-015) and OAuth
  authorization URLs are opened by the prompter/UI (the CLI prints them).

`DenyAllPrompter` is exported for headless use that wants explicit behavior (equivalent to
`prompter=None` except it declares no elicitation either). It answers `deny`, so the
consent outcome source is `"prompt"`, not `"no_prompter"`.

### 4.11 `EventBus`

```python
class EventBus:
    def subscribe(self, *, buffer: int = 256) -> AsyncContextManager[AsyncIterator[HostEvent]]: ...
```

`HostEvent` is a discriminated union on `type`:

| `type` | Payload | REQ |
|---|---|---|
| `server.state_changed` | `server_id`, `old`, `new`, `reason` | REQ-CON-010 |
| `registry.changed` | `diff: RegistryDiff`, `problems` | REQ-REG-013 |
| `catalog.changed` | `server_id`, `lists: tuple[Literal["tools","resources","resource_templates","prompts"], ...]` | REQ-CAT-003 |
| `resource.updated` | `server_id`, `uri` | REQ-CAT-010 |
| `invocation.started` / `.progress` / `.finished` | `invocation_id`, `fqn`, `consumer`, (`progress`) / (`outcome`, `duration_ms`) | REQ-INV-005 |
| `consent.requested` / `.resolved` | `request_id`, `operation`, `consumer`, `server_id`, `fqn` / `outcome` | REQ-UI-005 |
| `consent.rules_changed` | `rule_ids: tuple[str, ...]`, `change` ∈ `granted`, `updated`, `revoked`, `suspended` (no argument values, no patterns); published by `ConsentPolicy` after a change is persisted (or a session rule changed in memory), never for a failed write | REQ-UI-006 |
| `server.log` | `server_id`, `entry: LogEntry` | REQ-HSH-030 |
| `events.dropped` | `count` | — |

Every event type is a frozen model in `reflex_mcphost.core.events` and a member of the
`HostEvent` union; all but `consent.rules_changed` (`ConsentRulesChanged`) are also
re-exported from `reflex_mcphost` — in v0.1 match that event on `type`.

Each subscriber has its own bounded buffer; on overflow the oldest events are dropped and
a single `events.dropped` is delivered. A slow subscriber never blocks the Host.

### 4.12 `Toolset` — agent bridge (`reflex_mcphost.toolset`)

Defined in a private core module and re-exported by `reflex_mcphost.toolset`, which is
the public import path (§3.6). `Host.toolset()` constructs it.

```python
class Toolset:
    consumer: Consumer
    def list(self) -> list[ToolSpec]: ...                                  # sync; REQ-TLS-001/004
    def resolve(self, name: str) -> Fqn: ...                               # alias → FQN
    async def call(self, name: str, arguments: Mapping[str, Any] | None) -> ToolCallOutcome: ...

class ToolSpec(BaseModel, frozen=True):
    name: str               # agent-facing alias, ^[A-Za-z0-9_-]{1,64}$ — REQ-TLS-002
    fqn: Fqn
    description: str        # "[server_id] " + tool description (or title, or name)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None

class ToolCallOutcome(BaseModel, frozen=True):
    is_error: bool          # outcome != success
    text: str               # what the agent sees; for denied: "Permission denied by the host: <reason>"
    structured_content: dict[str, Any] | None
    result: InvocationResult
```

- `list()` reads the current catalog each time; tools of non-`ready` Servers and tools
  whose `visibility` lacks `"model"` are excluded (REQ-TLS-004, REQ-APP-008). `include`/`exclude` from `Host.toolset()` apply (REQ-TLS-003): entries with `::` are
  case-sensitive globs on the FQN (`github::*`, `*::search`); entries without `::` are
  globs on the server id (`"*"` = all, `"github"` = exactly Server `github`). Exclude wins.
  `include` / `exclude` given as a bare `str`, or containing a non-`str` entry, raise
  `TypeError`. A catalog entry whose Server has left the registry (status lookup raises
  `UnknownServerError` before the Catalog drops it) counts as not `ready`.
- `call()` never raises for invocation outcomes (REQ-TLS-005). It raises `UnknownToolError`
  (a subclass of both `MCPHostError` and `KeyError`, so `except KeyError` keeps working;
  `resolve()` raises it too) for an alias not in the current listing (unknown, filtered, app-only or no longer `ready`),
  `RuntimeError` / `HostClosedError` when used before `start()` / after `aclose()`, and
  propagates the cancellation of the awaiting task (the Invoker records `cancelled`,
  §4.8). The Host's lifecycle is checked on **every** `call()` and **before** the alias is
  resolved (a Toolset that outlives its Host raises `HostClosedError`, not `UnknownToolError`);
  `Host.toolset()` checks it when building the Toolset; `list()` and `resolve()` are not
  lifecycle-guarded. `call()` resolves the alias against a fresh listing, so visibility and
  filters are re-checked at call time (REQ-APP-008).
- `ToolCallOutcome.text` per outcome (`error.message` is the Invoker's bounded, redacted
  message — never argument values, Art. 5):

  | `result.outcome` | `text` |
  |---|---|
  | `success` | `result.text`; if empty and `structured_content` is set, its compact JSON |
  | `tool_error` | `result.text`, or `"The tool reported an error"` if empty |
  | `denied` | `"Permission denied by the host: " + result.error.message` |
  | any other | `"Tool call failed (<outcome>): " + result.error.message` |

  When the result carries no `InvocationError` or an empty message, the message part
  falls back to `"denied"` (for `denied`) or to the outcome name (any other outcome).

- **Alias algorithm** (deterministic for a given listing; order-independent; REQ-TLS-002):
  1. `c(t) = sanitize(server_id) + "__" + sanitize(name)`, where `sanitize` replaces
     `[^A-Za-z0-9_-]` with `_`; `h(t) = c(t)[:55] + "_" + sha256(str(fqn)).hexdigest()[:8]`.
  2. `H` = tools for which `sanitize` changed anything, `len(c(t)) > 64`, or `c(t)` equals
     the candidate of another tool in the listing.
  3. Until nothing changes: a tool not in `H` whose `c(t)` equals `h(u)` for some `u` in
     `H` joins `H`.
  4. `alias(t) = h(t)` if `t` in `H`, else `c(t)`.
  5. If two tools still share an alias (equal hashes), sort them by `str(fqn)`; the first
     keeps it, the k-th (k = 1, 2, …) gets `alias[:61] + "_" + f"{k:02d}"`, skipping any
     value already in use.

  The listing is the Toolset's own (ready Servers, model-visible, after include/exclude),
  so the same FQN may get different aliases in differently filtered Toolsets.
- No agent framework is imported by `reflex_mcphost` or `reflex_mcphost.toolset`
  (REQ-TLS-008).

### 4.13 Adapters (optional extras)

| Module | Extra | Function | REQ |
|---|---|---|---|
| `reflex_mcphost.adapters.pydantic_ai` | `reflex-mcphost[pydantic-ai]` | `as_pydantic_ai_toolset(toolset: Toolset) -> AbstractToolset` | REQ-TLS-006 |
| `reflex_mcphost.adapters.langgraph` | `reflex-mcphost[langgraph]` | `as_langchain_tools(toolset: Toolset) -> list[BaseTool]` | REQ-TLS-007 |

- Both reflect `Toolset.list()` dynamically where the framework allows it (PydanticAI
  re-lists the toolset before every model request, i.e. per run step — S-6); LangChain
  tools are a snapshot — call again after `catalog.changed`.
- Adapters return every error outcome through the framework's tool-error channel —
  PydanticAI `ToolFailed(text)`; LangChain `ToolException(text)` with
  `handle_tool_error=True` — and treat an `UnknownToolError` from `Toolset.call` (alias gone since
  the listing or snapshot) the same way with `"Tool is no longer available: <name>"`. No
  other exception escapes an adapter tool, because LangGraph's `ToolNode` re-raises it and
  fails the graph (S-6). Both adapters convert only `UnknownToolError`; the `try` covers only
  the `Toolset.call` await, and any other `KeyError` is a bug and propagates unchanged (it
  fails the agent run instead of being reported to the model as a vanished tool). A LangChain tool input that
  does not parse to a JSON object is also raised as `ToolException`. LangChain tools are
  async-only (`_run` raises `NotImplementedError`).
- Importing an adapter without its extra raises `ImportError` naming the extra.
- Exact base classes/APIs are verified against current framework versions in the
  Technical Design.
- The model string in the example below is illustrative; the documented snippets are
  executed in CI with PydanticAI `TestModel` and a fake LangChain chat model (no API key).

```python
from pydantic_ai import Agent
from reflex_mcphost import Host, Consumer
from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset

async with Host() as host:
    tools = as_pydantic_ai_toolset(host.toolset(Consumer.agent("helper"), include=["github"]))
    agent = Agent("anthropic:claude-sonnet-5-5", toolsets=[tools])
    print((await agent.run("List my open issues")).output)
```

### 4.14 `SamplingProvider`

```python
@runtime_checkable
class SamplingProvider(Protocol):
    async def create_message(
        self, params: CreateMessageRequestParams, *, server_id: ServerId
    ) -> CreateMessageResult: ...          # SDK types — REQ-HSH-003
```

*Deprecated in the pinned spec (SEP-2577); implemented as compatibility.*

Sampling handler flow (REQ-HSH-001…006, REQ-HSH-045): capability declared only if a
provider is set → on a sampling input request (MRTR `inputRequests` entry, or legacy
`sampling/createMessage`): `ConsentPolicy.evaluate(operation=sampling, target=server)`
with `ConsentRequest.sampling` populated → if denied / no provider: legacy →
`ErrorData(code=-1, message="Sampling request rejected by host")` (the SDK's user-rejection
code, spike S-1); modern → the originating request is not
retried and ends with outcome `denied`, error code `sampling_denied` → else call the
provider with `edited_sampling or params` → if `review_sampling_responses`:
`review_sampling_response` → return the result (as an `inputResponses` entry or JSON-RPC
result) → one audit record (`operation=sampling`, `fqn=None`).

The Consumer of the consent request and audit record is the originating request's
Consumer (tool call or catalog read), else unattributed (REQ-HSH-045/047).

Provider exceptions → legacy: JSON-RPC error to the Server; modern: the originating
request ends `protocol_error` / `input_handler_failed` (`details.reason="provider_error"`,
or `provider_timeout`); audit outcome `protocol_error` / `timeout` (REQ-NFR-020). Timeout =
Server's `timeout_s` or default. If the audit record cannot be begun, the provider is not
called and the sampling is refused (Art. 3).

Refusal codes on legacy: a **denial** → `ErrorData(code=-1, message="Sampling request
rejected by host")`; any other refusal (provider error or timeout, audit unavailable) →
`ErrorData(code=-32603, message="Sampling request failed")`. If no provider is set when a
request arrives, it is refused as a denial without asking anyone and audited `denied` /
`sampling_denied` with `decision_source="not_evaluated"`.

Provider source: the `Host(sampling_provider=…)` argument, else `[sampling] provider`
(`"package.module:factory"`) from the user-level or an explicitly loaded `mcphost.toml` —
a project file's `[sampling]` is ignored (`project_setting_ignored`, REQ-REG-014). The
module is imported in a worker thread and the factory's result is checked with
`isinstance(…, SamplingProvider)`; any failure is logged with its exception type and
disables sampling (the Host keeps running, REQ-NFR-020).

---

## 5. `mcphost.toml` schema

### 5.1 Location and discovery

| Level | Path | Written by Host? |
|---|---|---|
| User | `platformdirs.user_config_dir("reflex-mcphost")/mcphost.toml` (e.g. `~/.config/reflex-mcphost/mcphost.toml`) | **yes** (only file ever written — REQ-REG-009) |
| Project | `<cwd>/mcphost.toml` | no |
| Explicit | `--config PATH` / `HostConfig.load(path)` — replaces the user-level file for reads **and** writes | yes |

The project-level file contributes only `schema_version` and `[servers.*]`; any other key
is ignored with `ConfigProblem(project_setting_ignored)` (REQ-REG-014). A project
`[servers.<id>]` shadows a user-level one in full (precedence 1 vs 2, PRD RF-REG).

### 5.2 Schema

```toml
schema_version = 1                     # required; unknown future version → ConfigError

[host]
data_dir = "~/.local/share/reflex-mcphost"   # default: platformdirs.user_data_dir
consent_timeout_s = 120
default_tool_timeout_s = 60
reset_timeout_on_progress = false
secret_arg_pattern = "(?i)(passw(or)?d|secret|token|api[_-]?key|credential)"
legacy_sse_fallback = false
log_buffer_size = 1000
oauth_client_metadata_url = "https://ecrespo.github.io/reflex-mcphost/oauth/client.json"  # CIMD, REQ-AUTH-012

[sources]                              # third-party sources to read (all default true)
claude_desktop = true
cursor = true                          # user + project
vscode = true                          # user + project
project_mcp_json = true                # <cwd>/.mcp.json
extra = []                             # additional mcpServers/servers-style JSON files, precedence after project files

[sampling]
provider = ""                          # "" = disabled; else "package.module:factory" returning a SamplingProvider

[servers.filesystem]                   # own Server, stdio
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/work"]
env = { NODE_ENV = "production" }
cwd = "/home/me"
timeout_s = 30
roots = ["file:///home/me/work"]
log_level = "info"

[servers.linear]                       # own Server, Streamable HTTP + OAuth
transport = "http"
mode = "auto"                          # "auto" | "modern" | "legacy" (era override; also allowed in [overrides.<id>])
url = "https://mcp.linear.app/mcp"
review_sampling_responses = true
[servers.linear.oauth]
scopes = ["read", "write"]             # client_id optional → CIMD, then DCR fallback

[servers.internal]                     # static header auth, no OAuth (REQ-AUTH-009)
transport = "http"
url = "https://tools.example.com/mcp"
headers = { Authorization = "Bearer ${INTERNAL_TOKEN}" }

[overrides.github]                     # adjusts a third-party definition, never edits its file
enabled = false
timeout_s = 120
roots = []
```

Validation: unknown keys → `ConfigError` for own file (typos must not silently disable
security settings); `transport` must match the fields present; `${…}` allowed in
`command`, `args`, `env`, `cwd`, `url`, `headers` only.

### 5.3 Third-party formats read (read-only, Art. 6)

| Source | Top-level key | Field mapping notes |
|---|---|---|
| `claude_desktop_config.json` | `mcpServers` | `command/args/env` → stdio; `url` (+`headers`) → http |
| Cursor `mcp.json` (user `~/.cursor/`, project `.cursor/`) | `mcpServers` | same as above; `type` honored if present |
| VS Code `mcp.json` (user, project `.vscode/`) | `servers` | `type: "stdio"\|"http"\|"sse"`; `inputs` and `${input:…}` → `unsupported_variable` (REQ-REG-005) |
| `.mcp.json` (project) | `mcpServers` | as Claude Desktop; `type` honored |

`type: "sse"` maps to `HttpTransport` with legacy flag; it only connects when
`legacy_sse_fallback = true` (REQ-CON-013). OS-specific paths are fixed in the Technical
Design.

## 6. CLI — `reflex-mcphost`

| Command | Description | REQ |
|---|---|---|
| `reflex-mcphost` | Alias of `run`. | REQ-UI-001 |
| `reflex-mcphost run [--host 127.0.0.1] [--port 3000] [--no-browser] [--config PATH] [--unsafe-allow-remote] [--log-json]` | Start the reference app; UI and backend share `--port` (Reflex prod mode, DD-016). `--log-json` writes JSON log lines to the console (TD §7.1). | REQ-UI-001/002 |
| `reflex-mcphost servers [--json] [--config PATH] [--strict]` | Print the merged Server list, origins, shadowed definitions and problems; no UI, no connections. | REQ-UI-008 |
| `reflex-mcphost --version` | Print version. | — |

Behavior:
- `run` with a non-loopback `--host` and no `--unsafe-allow-remote` → exit 2 with message;
  with the flag → starts and prints a warning banner (REQ-UI-002). A wildcard `--host`
  (`0.0.0.0`, `::`, empty) is refused with exit 2 even with the flag: the guard needs one
  concrete origin (REQ-NFR-011). `localhost` and `*.localhost` are always refused (exit 2;
  the name is reserved for the MCP Apps sandbox proxy), and so is a value that is neither an
  IP address nor a valid host name. The warning banner goes to stderr; the printed / opened
  URL is `http://<host>:<port>/` (IPv6 in brackets). In remote mode the OAuth loopback
  callback and the MCP Apps sandbox proxy stay bound to the local machine, so authorization
  and MCP Apps work only from a browser on that machine.
- `run` checks before starting Reflex: argument rules (exit 2), the explicit `--config`
  file (invalid → exit 3) and that the database in the configured data dir opens (else
  exit 3). It opens the browser at `http://<host>:<port>/` only after `GET /ping` answers
  (never with `--no-browser`) and always prints that URL. Reflex exiting non-zero before
  the UI is ready → exit 3 (exiting 0 before readiness → 0); afterwards the CLI exits with
  Reflex's code, or 0 after a forwarded Ctrl-C / SIGTERM. Options reach the app only
  through the `REFLEX_MCPHOST_*` environment variables of TD DD-016; inherited
  `REFLEX_MCPHOST_*` values are never passed on.
- `run` keeps one workspace per version and packaged-source hash under
  `platformdirs.user_cache_dir("reflex-mcphost")/app/`; on every run it marks the current
  one as used and, best effort, removes workspaces of this tool (they carry its marker file)
  unused for ≥ 7 days and leftover temporary copies older than 1 day (TD DD-016).
  Directories without the marker and symlinks are never touched; a workspace used by a
  `run` that has been running for more than 7 days can be pruned by another `run`
  (residual, TD DD-016).
- `servers` table columns: `ID`, `TRANSPORT`, `ORIGIN` (source kind), `ENABLED`, `STATUS`
  (static: `ok` / `config_error` / `shadowed`); shadowed definitions are extra rows with
  `STATUS` `shadowed`, and the problems are listed below the table. An invalid own config
  → exit 3. `--json` emits
  `{"servers": [...], "shadowed": [...], "problems": [...]}` using the §4.3 models
  (`model_dump(mode="json")`, secrets as `"**********"`).

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | `servers --strict` and at least one `ConfigProblem` |
| 2 | Usage error / refused option |
| 3 | Fatal startup error (`run`: invalid own config, DB cannot open, Host start failed, Reflex exited non-zero before the UI was ready; `servers`: invalid own config) |

## 7. MCP Apps bridge contract

The Host implements the **host side** of MCP Apps `2026-01-26`
(`io.modelcontextprotocol/ui`). Capability declared only while MCP Apps rendering is
enabled (`Host.set_mcp_apps`, §4.1; REQ-APP-007):
`extensions["io.modelcontextprotocol/ui"] = {"mimeTypes": ["text/html;profile=mcp-app"]}`.
Terms: **App** = a Server's UI resource (`ui://…`); **View** = one rendered instance of it
in the sandbox (one per tab in v0.1); the Consumer `app:<server_id>` names the Server whose
View asks.

**Rendering (REQ-APP-001/002/006/011/013):**
- Trigger: an invocation started from the Host UI (`human:ui`, through `catalog_browser`)
  of a `ToolEntry` with `ui_resource_uri`, once it has **ended** with `result.consent.allowed`
  and outcome `success` / `tool_error` (→ `tool-result`) or `cancelled` / `timeout` /
  `connection_lost` (→ `tool-cancelled` with `reason` = the outcome name). Any other outcome,
  or no allowed consent, renders no View (the result panel shows the outcome). Rendering
  after completion is a v0.1 simplification; a live View during the call is a v0.2 candidate.
- Invocations by agents (`Toolset`) and by Apps never render a View: the UI holds no
  arguments or results for them (events and the audit log carry none, Art. 5) and v0.1 has
  no conversation surface. An App's own `tools/call` result goes back only to the calling
  View. One View per tab, in the tab's Catalog result panel; a new invocation in the tab
  tears the previous View down.
- The resource is read with `resources/read` from the same Server (`consumer=human:ui`);
  the `contents` item whose `uri` equals the requested URI is used. MIME parsed as a media
  type: `text/html` (case-insensitive) with parameter `profile=mcp-app`, other parameters
  (e.g. `charset`) ignored. Content: `text`, or `blob` decoded from base64 as UTF-8;
  ≤ 5 MiB (5,242,880 bytes). Otherwise no View and a notice with reason
  `missing_content`, `mime_mismatch`, `invalid_encoding`, `too_large` or `read_failed`
  (REQ-APP-013).
- The Host UI embeds a **sandbox proxy** page served on a **distinct loopback origin**
  `http://localhost:<proxy_port>/proxy?view=<view_id>` (TD DD-012; `view` is a correlation
  id, not a secret: `[A-Za-z0-9_-]{1,64}`, generated by the UI with
  `secrets.token_urlsafe(12)`). Proxy iframe: `sandbox="allow-scripts allow-same-origin"`,
  empty `allow` attribute (no device permissions in v0.1), `referrerpolicy="no-referrer"`.
  After `ui/notifications/sandbox-proxy-ready`, the Host sends
  `ui/notifications/sandbox-resource-ready` with `html`, `csp`, `sandbox: "allow-scripts"`
  (a repeated `sandbox-proxy-ready` is ignored and logged). The proxy honours only the
  first `sandbox-resource-ready`, parses the HTML with `DOMParser` (no script runs on the
  proxy origin), inserts the CSP `<meta>` as the first child of `<head>` and sets the inner
  iframe's `srcdoc` (the View); the inner iframe is always `sandbox="allow-scripts"`,
  `allow=""`, `referrerpolicy="no-referrer"`, whatever `params.sandbox` says. `sandbox-*`
  notifications are never forwarded to the View.
- `tool-input` params: `{"arguments": <the invocation's arguments>}`; `tool-result`
  params: the MCP `CallToolResult` shape rebuilt from the `InvocationResult` (`content`
  blocks as the SDK serializes them, `structuredContent` when set, `isError` always present:
  `false` for `success`, `true` for `tool_error`).
- MCP Apps are disabled (no advertisement, a "MCP Apps are disabled: <reason>" notice) in
  remote mode (§6), when `localhost` does not resolve only to loopback, or when the proxy
  cannot bind (TD DD-012). A machine without an IPv6 loopback is not a bind failure: the
  proxy then serves `127.0.0.1` only.

**CSP (REQ-APP-002/011/014):** `core.apps.build_csp(meta) -> CspResult(policy, external,
dropped)` reads `_meta.ui.csp` of the `contents` item. An entry is valid only if it is an
`https://` origin (or `wss://` in `connectDomains`) whose host is a public DNS name, with
optionally one leading `*.` label and a port, and no path, query, userinfo, quotes,
whitespace, `;` or `,`; IP literals, `localhost` / `*.localhost`, a bare `*` and
scheme-only sources are invalid; at most 32 entries per list and 253 characters per entry.
Exactly as implemented (`core.apps.csp.valid_entry`), an entry is a string of 1–253
characters matching

```
entry  = scheme [ "*." ] host [ ":" port ]          ; nothing else, not even a trailing "/"
scheme = "https://"  |  "wss://"                    ; lowercase; wss:// only in connectDomains
host   = 1*( label "." ) tld                        ; at least two labels
label  = alnum [ *61( alnum / "-" ) alnum ]         ; ≤ 63 characters
tld    = ALPHA [ *61( alnum / "-" ) alnum ]         ; starts with a letter (no dotted IPv4)
port   = 1*5DIGIT                                   ; 1–65535
```

where the last label is not `localhost`, `local`, `localdomain`, `internal`, `arpa`,
`invalid` or `test` (case-insensitive), and with `*.` the remaining host still has two
labels (`*.com` is invalid). Only the first 32 entries of a list are considered (later
ones are dropped); a duplicate is kept once; a list that is not a JSON array is dropped
whole. Invalid entries are left out, recorded in the Server's log stream (once, when the
View starts: `csp → dropped <list> entry <entry>`, level `warning`) and listed in the
notice; a recorded entry is cut to 253 characters (`…`), a non-string is shown as
`<type>`, and at most 64 are recorded per list plus one `<N more>` record. With `R` = valid `resourceDomains`, `C` = valid `connectDomains`, `F` = valid
`frameDomains`, `B` = valid `baseUriDomains`:

```
default-src 'none'; script-src 'unsafe-inline' R; style-src 'unsafe-inline' R;
img-src data: blob: R; font-src data: R; media-src data: blob: R;
connect-src C|'none'; frame-src F|'none'; base-uri B|'self'; object-src 'none';
form-action 'none'
```

The policy never contains `'unsafe-eval'`, `'strict-dynamic'`, nonces, hashes or `'self'`
outside `base-uri` (stricter than the spec's default, which the spec allows: "MAY further
restrict"; S-3 rendered the reference App with it). A notice is shown when valid external
domains are declared or entries were dropped. `_meta.ui.domain` is ignored (one proxy
origin for every View), `_meta.ui.permissions` is never granted (REQ-APP-011),
`_meta.ui.prefersBorder` is honoured (default: border).

The proxy page itself is served without a CSP header and without `frame-ancestors` (the
`srcdoc` View inherits a page CSP; the View policy is delivered by `<meta>`). Proxy ↔ View:
accept only `event.source === inner.contentWindow` and post with target `"*"` (the View
origin is opaque). Proxy ↔ Host UI: accept only `event.source === window.parent &&
event.origin === <pinned UI origin>` and post with that origin (spike S-3) — these two
checks and the pinned target origin are the guards. The page also stops when
`location.ancestorOrigins[0]` is an origin other than the pinned UI origin; that check is
**defense in depth only**: Firefox reports `"null"` there for an iframe with
`referrerpolicy="no-referrer"` (the relay's), so `"null"` counts as unknown, not foreign.

**Sandbox proxy (TD DD-012):** an `h11` listener on `127.0.0.1` and `::1` (IPv4 only
where the machine has no IPv6 loopback). Requests are checked in this order; the first
failing check answers:

| Check | Status |
|---|---|
| more than 32 connections already open (counted from accept to close) | `503` with `Retry-After: 1`, then close (no request is read) |
| HTTP/1.1 with a missing or repeated `Host` header (h11 protocol error, before routing); any other malformed head | `400` |
| request head > 8 KiB | `431` |
| `Host` other than exactly `localhost:<proxy_port>` (an HTTP/1.0 request without `Host` included) | `403` |
| target not starting with `/` (absolute-form, `*`) or path other than `/proxy` | `404` |
| method other than `GET` | `405` with `Allow: GET` |
| `Transfer-Encoding`, or `Content-Length` other than `0` | `400` |
| query other than exactly one `view=<view_id>` (`[A-Za-z0-9_-]{1,64}`, the sole parameter) | `400` |
| otherwise | `200 text/html; charset=utf-8`, the proxy page with the pinned UI origin |

Every response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer` and `Connection: close`, and no `Set-Cookie`. A client that
has not sent its whole head within 10 s is closed without a response; after a response the
proxy sends EOF and drains at most 64 KiB for at most 1 s. Nothing about a request (target,
headers, page) is logged. At most 32 connections are served at a time; a client that has
already sent bytes when it is refused over the cap may see the `503` as a connection reset.

**Transport (REQ-APP-003/005/012):** JSON-RPC 2.0 over `postMessage`. The relay on the Host
UI accepts a message only if `event.source === proxyWindow && event.origin ===
"http://localhost:<proxy_port>"` and handles messages as data only (TD DD-012: no script is
built from Server content; the HTML never enters the UI DOM). Nothing is sent to the View
before `ui/notifications/initialized`. Limits: a View message is a JSON object (batches →
`-32600`) with `jsonrpc == "2.0"`, `method` a string ≤ 128 characters, `id` a string or
integer when present, `params` an object when present, serialized ≤ 1 MiB (else `-32600`
if an id can be read, dropped otherwise); at most 4 View requests (`tools/call`,
`resources/read`) in flight per View (more → `-32000` "too many concurrent requests").
Edge cases: text that is not JSON (or not UTF-8) → `-32700` with `id: null`; an `id` that
is present but not a string or integer (`null`, a boolean, …) → `-32600` with `id: null`;
other shape errors echo the id; a message without `method` must be a response (an id and
exactly one of `result` / `error`, each an object), else `-32600`. An oversized message of
at most 4 MiB is parsed only to read its id: a request with a valid id gets `-32600`
"message too large" with that id; any other oversized message is dropped silently. The
relay already drops messages whose serialization exceeds 1 MiB.
Every View-initiated RPC is logged to the Server's log stream (`ConnectionManager.append_log`)
as a `LogEntry` with logger `mcp-app` and text `<method> → <outcome>` (`ok`, the JSON-RPC
error code, or the invocation outcome) — never params, arguments, results or HTML. Level
`info` for `ok` and `success`, `warning` for an error reply and for every other invocation
outcome (`tool_error`, `denied`, …). An invalid message is logged as `invalid message →
<code>` (`→ dropped` when it gets no reply), a repeated `sandbox-proxy-ready` as `… →
ignored`, both `warning`.

| Direction | Message | Host behavior | REQ |
|---|---|---|---|
| View → Host | `ui/initialize` | Reply with `protocolVersion: "2026-01-26"`, `hostInfo: {name: "reflex-mcphost", version: <package version>}`, `hostCapabilities` (`openLinks`, `serverTools`, `serverResources`, `logging`, `sandbox: {permissions: {}, csp: <the validated lists>}`), `hostContext: {theme: "light", platform: "web", displayMode: "inline", availableDisplayModes: ["inline"]}` (`displayMode` / `availableDisplayModes` are forced by the bridge; `locale`, `timeZone` and `containerDimensions` are omitted — optional in the spec). Accepted once, only after `sandbox-resource-ready` was sent: earlier → `-32600` "not initialized", again → `-32600` "already initialized". Before this reply only `ui/initialize` and `ping` are handled; other requests → `-32600` "not initialized", notifications dropped. | REQ-APP-003 |
| View → Host | `ui/notifications/initialized` | Unlocks Host → View traffic. | REQ-APP-003 |
| Host → View | `ui/notifications/tool-input` | Exactly once, after initialized. | REQ-APP-006 |
| Host → View | `ui/notifications/tool-input-partial` | Not sent in v0.1. | — |
| Host → View | `ui/notifications/tool-result` | Exactly once, after tool-input, for `success` / `tool_error`. | REQ-APP-006 |
| Host → View | `ui/notifications/tool-cancelled` | `{"reason": <outcome name>}` for `cancelled` / `timeout` / `connection_lost`, after tool-input. | REQ-APP-009 |
| Host → View | `ui/notifications/host-context-changed` | Not sent in v0.1: the bridge can send it once the View is initialized (`AppBridge.set_host_context`), but the UI has a fixed theme and reports no container-size change. | — |
| Host → View | `ui/resource-teardown` (request) | `{"reason"}` ∈ `replaced` (a new invocation **starts** in the tab, whatever its outcome; also an overlapping invocation of the same tab), `closed` (the frame's "Close the App" button, or the tab selects another catalog entry), `server_removed` (`registry.changed` removes the View's Server while it runs), `host_closing` (lifespan shutdown, before `Host.aclose()`: every View in parallel, ≤ 2 s **in total**; the unfinished ones are discarded); wait ≤ 2 s for the response, then cancel the View's in-flight requests (their invocations end `cancelled`; the bridge waits ≤ 1 s for them) and remove the iframe. Not sent to a View that never sent `ui/notifications/initialized`. While it runs, every View request → `-32000` "the View is closing". A tab disconnect, reload or navigation discards the View without teardown; so does a View that navigated its own frame (`navigated` is a discard reason, never sent — see below). | REQ-APP-010 |
| View → Host | `tools/call` | The bridge builds `<server_id>::<name>` from the View's bare `name` (a View can only name tools of its own Server) and calls `Invoker.call(fqn, arguments, consumer=Consumer.app(server_id))` (consent + audit — stricter than the spec's MAY). The Invoker enforces the App scope (§4.8 step 1a). Reply per the table below; a `name` that is not a non-empty string or `arguments` that are not an object → `-32602`. | REQ-APP-004, REQ-APP-008 |
| View → Host | `resources/read` | Own Server only (`consumer=Consumer.app(server_id)`); a `uri` that is not a non-empty string → `-32602`; a read error → `-32000` "resource read failed"; result > 5 MiB → `-32000` "resource too large". | — |
| View → Host | `ui/open-link` | Openable = `http` / `https` with a host name, ≤ 2,048 characters, no whitespace or control characters (`core.apps.bridge.is_openable_link`, re-checked with `ui.models.is_openable_url` before the confirmation and again before opening); a non-openable URL → `-32000` "the link cannot be opened", without asking. One confirmation per View at a time (another `ui/open-link` meanwhile → `-32000` "a link confirmation is pending"); it is shown in the View's frame area with the full URL as plain text; approve → new tab via `rx.redirect(url, is_external=True)` = `window.open(url, "_blank", "noopener")` (Reflex 0.10.1): `noopener` is guaranteed by Reflex, and the UI's `Referrer-Policy: no-referrer` (TD DD-017) means the opened site gets no `Referer`; result `{}`; decline → `-32000` "link declined". | REQ-APP-015 |
| View → Host | `ui/request-display-mode` | Only `inline` supported; responds `inline`. | — |
| View → Host | `ui/notifications/size-changed` | Width follows the container; height (a finite number) rounded and clamped to 80…4,000 px; anything else ignored. | — |
| View → Host | `notifications/message` | Appended to the Server's log stream (logger `mcp-app`, its level, `data` as text cut to 4 KiB). | REQ-HSH-030, REQ-APP-012 |
| View → Host | `ping` | Empty result. | — |
| View → Host | `ui/message`, `ui/update-model-context` | Error `-32601` "Method not supported by this host" — no conversation in v0.1 (documented deviation from the spec's SHOULD). | REQ-APP-005 |
| View → Host | malformed / other | `-32600` / `-32601`; the View keeps running. | REQ-APP-005 |

App `tools/call` replies:

| `InvocationResult.outcome` | Reply to the View |
|---|---|
| `success`, `tool_error` | `CallToolResult` (`content`, `structuredContent` when set, `isError` always present: `true` for `tool_error`) |
| `denied` with `error.code == "app_scope"` | JSON-RPC error `-32602` |
| any other | `CallToolResult{isError: true, content: [text]}` with the text of §4.12 `ToolCallOutcome.text` (never SDK message text, never argument values) |
| (the Invoker port raised) | JSON-RPC error `-32000` "tool call failed" (logged by exception type only) |

A Server that is not `ready` keeps its View; the View's calls then get the
`server_unavailable` text above.

**Navigation (TD §6.1):** `proxy.html` counts `load` events of the inner frame; the first is
the App. A second one (the View navigated its own frame, which no CSP prevents) makes the
proxy post `{"type": "view-navigated"}` to the relay (same channel and checks as
`sandbox-proxy-ready`); the bridge then discards the View **without**
`ui/resource-teardown` (log `view → navigated`), cancels its in-flight requests (their
invocations end `cancelled`), removes the iframe and shows the notice "The App navigated
away and was closed." A `view-navigated` from anything but the proxy window and origin is
ignored like any forged message; only the exact message `{"type": "view-navigated"}` is
recognised. A View can trigger its own discard by posting that message (harmless: it only
closes itself). After a second `load` the proxy relays nothing more from that frame. Data
carried by that first navigation is not prevented (residual risk).

## 8. UI layer surface — `reflex_mcphost.ui`

Embeddable Reflex pieces (REQ-UI-007). Exact Reflex state wiring is a Technical Design
concern; the public surface is:

| Name | Kind | Purpose | REQ |
|---|---|---|---|
| `install(app: rx.App, host_factory: Callable[[], Host])` | function | Registers an async-context-manager lifespan task that owns one `Host` (`start()` before serving, `aclose()` after), binds `ReflexPrompter`, installs the Host/Origin guard (single origin from the Reflex config's `deploy_url`) | REQ-UI-001, REQ-NFR-011 |
| `servers_panel()` | component | Servers view: state, origin, transport, capabilities, log stream; connect / disconnect / reconnect / authorize / sign-out, enable / disable (REQ-REG-010), reload (REQ-REG-013). No add / edit / remove form in v0.1 (library API or `mcphost.toml`; REQ-REG-009 unchanged) | REQ-UI-003 |
| `catalog_browser()` | component | Catalog + schema-generated invoke form + result | REQ-UI-004 |
| `consent_center()` | component | Pending consent / sampling consent / sampling-response review / elicitation (form and url) / trust prompts from the `PromptRegistry`; non-blocking (a side panel, never a page-blocking modal); shows the asking Consumer | REQ-UI-005 |
| `permissions_table()` | component | Rules list, edit, revoke; re-queries the rules on `consent.rules_changed` (any tab, any Consumer) | REQ-UI-006 |
| `audit_table()` | component | Audit view with REQ-INV-011 filters | REQ-UI-006 |
| `mcp_app_frame()` | component | The tab's MCP App View (sandbox proxy iframe + relay) for its current Catalog invocation; rendered by `catalog_browser` in the result panel; takes no parameter (it reads the tab's `HostState`); shows the external-domains and dropped-CSP-entries notices, the not-rendered / disabled notices, the `ui/open-link` confirmation (§7) and a "Close the App" button (teardown `closed`) | REQ-APP-001…015 |
| `ReflexPrompter` | class | `Prompter` implementation backed by the process-wide `PromptRegistry` (`supports_elicitation = True`) | REQ-UI-005 |
| `HostRuntime` | object | Module-level singleton of the backend process: `host` (the running `Host`; `RuntimeError` unless running) and `phase` (`starting` / `running` / `stopped`, drives the "Host starting… / Host stopped" banner) and `apps` (a frozen `AppsStatus`: `enabled: bool`, `reason: str \| None` ∈ `remote_mode`, `localhost_not_loopback`, `proxy_bind_failed`, `proxy_origin: str \| None` = `http://localhost:<proxy_port>` while enabled; set before `Host.start()`; outside a lifespan `AppsStatus()` — disabled, no reason, shown as `not_running`; §7) | REQ-UI-007 |
| `HostState` | class | Reflex state of a tab; an embedding app adds `on_load=HostState.start_watch` to every page that shows a component above (starts the tab's watcher, DD-011). Its other vars and handlers are not public | REQ-UI-007 |

The reference app (`reflex_mcphost.app`) is composed only from these; it has no
additional public API. `install()` refuses to start when the `deploy_url` host is not
`127.0.0.1` unless the environment sets `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1` (the CLI's
`--unsafe-allow-remote`); `localhost` / `*.localhost` are always refused, and so are a
missing `deploy_url`, a scheme other than `http`, a missing port and a wildcard host. An
embedding app sets its `deploy_url` to the `127.0.0.1` origin its users load (REQ-UI-007,
REQ-NFR-011). Until the lifespan has resolved the origin the guard answers `503`
("starting") and closes websockets; a request with a repeated `Host` or `Origin` header is
refused (`403`); a refused websocket is closed with code `1008`. One Host lifespan per
process: entering a second one while one runs raises `RuntimeError`.

**Tab state stays in memory (normative).** `HostState` holds Server-provided and
confidential data per tab — the Catalog invocation panel (arguments, results), the MCP App
View outbox (resource HTML, messages) and the other view models (Data Model §8). An app
that uses these components MUST configure Reflex's MEMORY state manager in its
`rxconfig.py` (`state_manager_mode=rx.constants.StateManagerMode.MEMORY`) and run one
backend worker; Reflex 0.10.1's default DISK manager would write that state under
`.states/`. The reference app does so (TD DD-016). The lifespan installed by `install()`
checks it at start-up, where it reads `deploy_url` (TD DD-017): a `state_manager_mode` other
than `MEMORY` is a start-up `RuntimeError` naming the setting (fail closed; the reference app
CLI then exits `3`). The mode checked is the one Reflex will create, by Reflex's own rule: an
unset `state_manager_mode` with a Redis URL configured counts as `REDIS` and is refused.

The UI opens a Server-provided URL (`consent_center`: url-mode elicitation, authorization
URL; `mcp_app_frame`: `ui/open-link`, §7) only when `ui.models.is_openable_url` holds:
`http` / `https` with a host name, at most 2,048 characters, no whitespace or control
characters. For `consent_center`, otherwise the URL is neither shown nor opened,
accepting the elicitation is sent as `decline`, and a fixed notice is shown (TD §6.1); a
refused authorization URL gets a fixed notice and no redirect, and the UI cancels the flow
(`AuthorizationFlow.cancel()`): it ends with `AuthorizationError(reason="cancelled")`, the
loopback listener is released and the Server returns to `auth_required`, so the user can
retry at once (notice: "The authorization URL is not an http(s) address; it was refused,
not opened, and the authorization was cancelled."). UI responses carry `Referrer-Policy:
no-referrer` (TD DD-017; a response that already sets a `Referrer-Policy` keeps its own),
so no opened URL learns the UI origin.
`servers_panel` shows a sign-out whose stored credentials could not be deleted
(`SecretStoreError`, §3.4) as the fixed notice "Sign out <server_id> failed: secret store
unavailable; the stored credentials could not be deleted"; the Server is signed out in
memory and the next connect needs authorization again.
Elicitation form content is validated against the requested schema before it is sent; an
invalid submit keeps the card with field errors and nothing reaches the Server.

## 9. Rate limiting

None. Single-user local library. Backpressure exists only in the `EventBus` (§4.11).

## 10. Versioning

- Package SemVer (Art. 9). The `0.1` line freezes §4–§8 names; additions are minor.
- `mcphost.toml` carries `schema_version`; v0.1 = `1`. A future `2` ships with an
  automatic, backup-first migration.
- MCP protocol version: the Host offers `2026-07-28` and falls back to `2025-11-25`
  (dual-era); it accepts any version both it (via the SDK) and the Server support;
  REQ-CON-004 fails only when there is no overlap.

## 11. Traceability matrix (PRD → API)

| PRD group | API section |
|---|---|
| RF-REG | §4.3, §5, §6 `servers`, `Prompter.confirm_trust` |
| RF-CON | §4.1, §4.5 (state machine), §4.11 |
| RF-AUTH | §2.2, §4.5 `authorize`/`sign_out`, §5.2 `oauth`/`headers` |
| RF-CAT | §4.6, §4.11 |
| RF-CNS | §4.7, §4.10 |
| RF-INV | §4.8 (incl. step 4a, REQ-INV-016), §4.9 |
| RF-HSH | §4.5 (`logs`, `set_log_level`, `set_roots`), §4.10, §4.14 |
| RF-TLS | §4.12, §4.13 |
| RF-APP | §4.1 `set_mcp_apps`, §4.5 `append_log`, §4.6 `ui_resource_uri`, §4.8 step 1a, §7, §8 `mcp_app_frame`, `HostRuntime.apps` |
| RF-UI | §6, §8 |
| NFR-010…012 | §3.4 (`SecretStr`), §4.7 preview redaction, §8 `install` |
| NFR-021 | §4.8 steps 3 and 7 (a timed-out pattern is an unusable schema) |

## Constitution check

- **Art. 1** — no server-side API; the only listener is the OAuth loopback callback (§2.2).
- **Art. 2** — MCP objects are SDK re-exports; dual-era MCP 2026-07-28 / 2025-11-25; MCP Apps 2026-01-26 wire names (§7); deprecated features marked as compatibility (§4.5, §4.14).
- **Art. 3** — single effectful path: `Invoker` (§4.8 pipeline) and sampling handler (§4.14); App tool calls go through `Invoker` with the App scope enforced and audited in step 1a (§4.8, §7); `AuditLog` has no write API (§4.9); fail-closed evaluation (§4.7 steps 4–5).
- **Art. 4** — `Toolset` and adapters are separate modules; adapters are extras (§4.12–4.13); UI surface isolated in §8.
- **Art. 5** — `SecretStr` everywhere secrets appear; no backend text in `SecretStoreError` (§3.4); Server regexes only under the REQ-NFR-021 bound, including output validation (§4.8 step 7); preview redaction; `TrustRequest` exposes env keys only; HMAC args hash; sandbox proxy on a distinct origin + validated declared-domains-only CSP + no device permissions + data-only relay + size limits + `ui/open-link` confirmation (§7).
- **Art. 6** — writes only to user-level/explicit `mcphost.toml` (§5.1); third-party edits rejected (§4.3).
- **Exceptions requested:** none. **Deliberate scoping recorded:** resource/prompt reads not consent-gated (§4.6) — confirm.

---

## Change history

| Version | Date | Changes |
|---|---|---|
| 0.1-draft.1 | 2026-10-30 | Initial draft |
| 1.0 | 2026-10-30 | Approved; resource/prompt reads confirmed as not consent-gated (§4.6) |
| 1.1 | 2026-10-30 | Folded delta `2026-10-mcp-2026-07-28` (dual-era, MRTR, CIMD, MCP Apps 2026-01-26) |
| 1.2 | 2026-10-31 | Folded delta `2026-10-analyze-fixes`: `HostConfig.aload`, `InputRequestError`, `consumer` on catalog reads, consent ranking, trust fingerprint, state-machine rows, project config limits |
| 1.3 | 2026-10-31 | Folded delta `2026-10-spike-batch-a`: attribution fields, evaluation step 0, stdio shutdown policy |
| 1.4 | 2026-10-31 | Folded delta `2026-10-spike-batch-b`: single `--port`, proxy messaging/CSP rules (S-3, S-5) |
| 1.5 | 2026-10-31 | Folded delta `2026-10-project-trust`: `TrustRequest.transport/url/header_keys`, trust row covers HTTP |
| 1.6 | 2026-10-31 | Folded delta `2026-10-phase2-impl`: Host seams/lifecycle, HostConfig fields, registry surface/rules, state row `reconnecting → auth_required`, `TrustRequest.destination/url_template/resolved_command`, risky env list |
| 1.7 | 2026-10-31 | Folded delta `2026-10-phase3-impl`: consent/audit exceptions, reads on the Host MRTR loop, `grant()` hash source, model defaults/validation, step 0 stores, step 3 suspends only `allow` tool rules, corrupt-rule poisoning, answer failure rules, one rule per key, preview limits, invoker steps 3/7 and early-failure audit, `DenyAllPrompter` source; `wait_ready` also awaits the initial catalog refresh (§4.5) |
| 1.8 | 2026-10-31 | Folded delta `2026-10-phase4-prep` (D1, D3–D6, D8): `authorize` runs the flow inside a connection attempt (state rows, `authorization_url: str \| None`), `AuthorizationError` reason codes, auth/`http_unauthorized` status reasons, `input_handler_failed` reasons, `ElicitationRequest.attribution`, prompt timeouts, core never opens a browser, legacy sampling refusal code, provider/audit failure paths |
| 1.9 | 2026-10-31 | Folded delta `2026-10-phase4-impl`: §2.2 authentic-callback listener rules and credential binding (security review); §4.3 roots-only changes are `unchanged`; §4.5 `UnknownServerError` on the Phase 4 methods, `authorize` flow outcome, `set_roots` delivery/default roots, `ready → reconnecting` row for a 401 while ready; §4.10 re-prompt cap; §4.14 `@runtime_checkable`, legacy refusal codes, no-provider path, provider source |
| 1.10 | 2026-10-31 | Folded delta `2026-10-phase5-prep` (D1–D3, D5): §4.12 placement note (core implementation, public re-export), collision-free alias algorithm, `ToolCallOutcome.text` per outcome, `call()` raising contract, filter semantics; §4.1 `toolset()` Consumer kinds; §4.13 adapter error conversion, per-run-step re-listing |
| 1.11 | 2026-11-01 | Folded delta `2026-10-phase5-impl` (items 1–6, 9): §4.12 lifecycle checked on every `call()` before alias resolution, `TypeError` for string/non-string filter entries, a Server gone from the registry is not `ready`, text fallback without `InvocationError`; §4.13 both adapters convert `KeyError` (only around `Toolset.call`), non-object LangChain input → `ToolException`, example model string illustrative |
| 1.12 | 2026-11-01 | Folded delta `2026-11-phase6-prep` (D1–D5, D7): §4.10 `confirm_trust` not time-bounded; §6 `--log-json`, wildcard-host refusal, pre-flight / readiness / exit-code rules; §8 `install` (async-context-manager lifespan, guard origin from `deploy_url`, remote opt-in), `servers_panel` scope, `consent_center` / `ReflexPrompter` on the `PromptRegistry` |
| 1.13 | 2026-11-01 | Folded delta `2026-11-phase6-impl` (items 2–4, 13, 15–17, 23, 30, 34): §6 `--host` rules (`localhost` / malformed refused, banner on stderr), options only through the environment, `servers` table rows and exit 3 on an invalid own config, child exit 0 before readiness; §8 rows `HostRuntime` and `HostState`, `install` start-up refusals, `503` while starting, repeated headers, websocket `1008`, one lifespan per process, `consent_center` URL rule and form validation |
| 1.14 | 2026-11-01 | Folded delta `2026-11-phase7-prep` (D1–D10): §4.1 `set_mcp_apps` (extension declared only where Views render); §4.5 `append_log` seam; §4.8 step 1a App scope (`denied` / `app_scope`, audited); §7 rewritten in place — rendering trigger and timing (UI-started, after consent, after completion; one View per tab), resource limits and MIME parsing, validated CSP entries and pinned policy, data-only relay with source + origin check, message limits and concurrency cap, logging format, `tools/call` reply mapping, `ui/open-link` rule, lifecycle and teardown reasons, unused `_meta.ui` fields; §8 `mcp_app_frame()` (parameter dropped) and `HostRuntime.apps`; §11 RF-APP row |
| 1.15 | 2026-11-01 | Folded delta `2026-11-phase7-impl` (items 3–12, 14, 15, 18–20, 22–24, 26–32, 34, 35): §4.5 `append_log` (time restamped, parked Servers dropped); §7 — proxy iframe `referrerpolicy` and View id grammar, what the proxy page pins, `isError` always present, CSP entry grammar exactly as implemented and the dropped-entry log line, proxy page without CSP / `frame-ancestors` and the `ancestorOrigins` check as defense in depth (Firefox `"null"`), new Sandbox proxy check / status-code table and response headers, JSON-RPC edge cases and log levels, `ui/initialize` ordering, `hostInfo` / `hostContext` as sent, `host-context-changed` not sent in v0.1, teardown reasons and semantics, port-failure and `resources/read` errors, `ui/open-link` rules (one pending, `noopener` without `noreferrer`), `size-changed` validation; §8 — `mcp_app_frame()` row, `HostRuntime.apps` row, normative MEMORY state-manager note for embedders, `is_openable_url` rule for every caller |
| 1.16 | 2026-11-01 | Folded delta `2026-11-phase8-prep` (D2, D4–D8, D10–D12): §3.4 `UnknownToolError`; §4.8 step 4a (re-resolve after consent, `definition_changed`, REQ-INV-016); §4.11 `consent.rules_changed`; §4.12 / §4.13 `UnknownToolError`, adapters convert only it; §6 workspace pruning; §7 proxy connection cap (32, `503`), self-navigation detection (`navigated` discard), `ui/open-link` without referrer; §8 MEMORY state manager enforced at lifespan start-up, `Referrer-Policy: no-referrer`, refused authorization URL cancels the flow, `permissions_table` live refresh; §11 rows |
| 1.17 | 2026-11-01 | Folded delta `2026-11-phase8-impl` (items 1, 13, 14, 15, 19, 21, 22, 26, 38): §3.4 `SecretStoreError` finalized (operation `delete` only, fixed message, raised `from None`), `discovery_failed` for a secret store failing at flow start (known limitation); §4.8 step 7 is the only output check (the SDK's `validate_tool_result` disabled per session), codes `missing_structured_content` / `invalid_output_schema` / `output_schema_mismatch`, a failing result's content not returned; §4.11 `ConsentRulesChanged` not re-exported from the package root; §6 pruning never touches unmarked directories or symlinks, >7-day-run residual; §7 over-cap `503` may arrive as a reset, exact `view-navigated` message, a View may discard itself; §8 state-manager mode inferred as Reflex does (Redis URL ⇒ refused), an existing `Referrer-Policy` kept, refused-authorization and sign-out notices |
