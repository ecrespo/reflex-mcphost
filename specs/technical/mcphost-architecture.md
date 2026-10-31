# reflex-mcphost v0.1 — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `APPROVED` |
| **Version** | 1.4 |
| **Date** | 2026-10-31 |
| **Related PRD** | `specs/prd/mcphost-v0.1.md` v1.3 |
| **Related API Spec** | `specs/api/mcphost-api-v0.1.md` v1.3 |
| **Governed by** | `specs/constitution.md` v1.2 |
| **Reviewers** | Ernesto Crespo |

---

## 0. Pinned revision set (Constitution Art. 2)

Verified against official sources on **2026-10-30**:

| Item | Pinned | Source checked |
|---|---|---|
| MCP specification (preferred) | `2026-07-28` (current) | modelcontextprotocol.io/specification/versioning, `/2026-07-28/changelog` |
| MCP specification (legacy fallback) | `2025-11-25` | `/2026-07-28/basic/versioning` (backward compatibility) |
| MCP Apps extension | `2026-01-26` (Stable), id `io.modelcontextprotocol/ui` | github.com/modelcontextprotocol/ext-apps `specification/2026-01-26/apps.mdx` |
| `mcp` Python SDK | `>=2.3,<2.4` (2.3.0, 2026-10-02) | PyPI, py.sdk.modelcontextprotocol.io (client, callbacks, protocol-versions, MRTR, subscriptions, caching, OAuth, apps) |
| Python | `>=3.11` | `mcp` 2.x and `reflex` 0.10 both require 3.11 |
| Reflex | `>=0.10.1,<0.11` | PyPI (2026-10-27), docs v0.10.0 |
| PydanticAI (extra `pydantic-ai`) | `pydantic-ai-slim>=2.64,<3` | PyPI (2026-10-30), docs v2.0; spike S-6 |
| langchain-core (extra `langgraph`) | `langchain-core>=1.7,<2`; `langgraph>=1.2,<2` only in the `test`/`docs` groups | PyPI; spike S-6 |
| SQLModel | `>=0.0.48` | PyPI |
| keyring | `>=25.7` | PyPI |
| anyio | `>=4.15` | PyPI |
| platformdirs | `>=4.14` | PyPI |
| jsonschema | `>=4.26` | PyPI |
| python-ulid | `>=4.1` | PyPI |
| tomlkit | `>=0.13` | PyPI (comment-preserving TOML writes) |
| uritemplate | `>=4.2` | PyPI (RFC 6570) |
| import-linter (dev) | `>=2.15` | PyPI |

Re-verification is required before every minor release; a change in any row goes through
a Delta Spec.

## 1. Context

reflex-mcphost is a Python **library + reference app** that plays the MCP **Host** role:
it owns one **Client** per configured **Server**, maintains a catalog, and routes every
effectful operation through a consent policy and an audit log, exposing the result to
humans (Reflex UI) and agents (`Toolset`).

The pinned SDK reshapes the engineering problem. `mcp.Client` (v2) already handles era
detection (`server/discover` probe → `initialize` fallback), the MRTR retry loop, per-
transport cancellation, `x-mcp-header` mirroring and invalid-tool dropping, response
caching by `ttlMs`, `subscriptions/listen`, and OAuth 2.1 (`OAuthClientProvider`: PKCE,
`iss` check, CIMD via `client_metadata_url`, DCR fallback). Our job is therefore not
protocol mechanics but **supervision, policy and presentation**: keep N Clients alive and
observable, put consent and audit in front of every effect, and make the host features
(elicitation, sampling, roots, MCP Apps) available through one `Prompter` seam — with a
core that has no Reflex import (Art. 4) and no `mcp.server` import (Art. 1).

The hardest parts are (a) the `Client`'s *async-with-only* lifecycle, which forces a
supervisor-task design; (b) the human-in-the-loop hand-off between SDK callbacks running
inside a request and a Reflex UI running in another task; and (c) the MCP Apps sandbox,
which needs a second loopback origin next to the Reflex app.

## 2. Technical goals

- **Correctness:** exactly one audit record per attempt; no effect without a consent
  decision; fail-closed on every policy error path (Art. 3). Byte-identical third-party
  configs (Art. 6).
- **Performance:** consent evaluation < 5 ms p95 at 10k rules (REQ-NFR-001); Host overhead
  < 20 ms p95 per call (REQ-NFR-002); UI interactive < 10 s (REQ-NFR-003).
- **Resilience:** one Server's failure never affects others (REQ-CON-011, REQ-NFR-020).
- **Maintainability:** `mypy --strict`, import-linter layers, core ≥ 90 % coverage, every
  MUST criterion cited by a test (Art. 7, Art. 8).
- **Operability (local):** structured stdlib logging; per-Server log stream in the UI; no
  telemetry.

## 3. Proposed architecture

### 3.1 High-level diagram

```
                       ┌──────────────────────── reference app (reflex_mcphost.app) ───────────────────────┐
 Browser tab           │  Reflex frontend (127.0.0.1:3000)          Reflex backend (127.0.0.1:8000)         │
 ┌──────────────┐ ws   │  ┌──────────────────────┐     events      ┌───────────────────────────────────┐   │
 │ Host UI      │◄────►│  │ reflex_mcphost.ui     │◄──────────────►│ HostState (per tab, background    │   │
 │ (servers,    │      │  │ components + JS relay │                │ watcher) · ReflexPrompter ·       │   │
 │ catalog,     │      │  └──────────┬────────────┘                │ AppsBridge (JSON-RPC, Python)     │   │
 │ consent, …)  │      │             │ postMessage                 └───────────────┬───────────────────┘   │
 │  ┌─────────┐ │      │  ┌──────────▼────────────┐                                │ in-process API        │
 │  │proxy    │─┼──────┼─►│ Sandbox proxy server  │  localhost:<proxy_port>        │                       │
 │  │iframe   │ │      │  │ (separate origin)     │                                │                       │
 │  │ └ View  │ │      │  └───────────────────────┘                                │                       │
 │  └─────────┘ │      └───────────────────────────────────────────────────────────┼───────────────────────┘
 └──────────────┘                                                                  ▼
            ┌───────────────────────────── reflex_mcphost.core (no reflex import) ─────────────────────────────┐
            │  Host ─┬─ ServerRegistry ── config sources (mcphost.toml, Claude, Cursor, VS Code, .mcp.json)     │
            │        ├─ ConnectionManager ── ServerSupervisor ×N ── mcp.Client (stdio | Streamable HTTP)       │
            │        │                         └ OAuthClientProvider + TokenStore(keyring|0600 file)           │
            │        ├─ Catalog (per-Server snapshot, listen watcher)                                         │
            │        ├─ ConsentPolicy ── RuleIndex (memory) ── ConsentStore (SQLite)                          │
            │        ├─ Invoker ── MRTR loop ── InputRequestRouter (elicitation | sampling | roots)            │
            │        ├─ AuditLog (SQLite, HMAC args hash)                                                     │
            │        └─ EventBus                                                                              │
            └──────────────────────────────────────────────────────────────────────────────────────────────────┘
                       ▲ Toolset (reflex_mcphost.toolset) ── adapters.pydantic_ai | adapters.langgraph ── agents
```

### 3.2 Components

| Component | Module | Technology | Responsibility | REQs |
|---|---|---|---|---|
| `Host` | `core.host` | anyio task group | Composition root; lifecycle; owns the task group all supervisors run in | §4.1 API |
| Config sources | `core.config.*` | `tomllib`, `tomlkit`, `json`, `platformdirs` | Parse own + third-party files, env expansion, precedence | RF-REG |
| `ServerRegistry` | `core.registry` | — | Merged definitions, shadowing, problems, mutations, reconcile diff | RF-REG |
| `ConnectionManager` | `core.connection.manager` | — | Public façade over supervisors; status; authorize/sign-out; logs | RF-CON, RF-AUTH |
| `ServerSupervisor` | `core.connection.supervisor` | `mcp.Client`, anyio | One task per Server: build Client, enter it, watch liveness, backoff, era cache | REQ-CON-001…019 |
| Transport factory | `core.connection.transports` | `mcp.client.Transport` wrapper over `stdio_client(params, errlog=<pipe write end>)`, `streamable_http_client`, `httpx2` | Build transports (stdio always as `Client(<own Transport>)`, never `Client(StdioServerParameters)`), EOF-observing wrapper for liveness, per-attempt stderr pipe (POSIX `wait_readable` reader; Windows `to_thread` reader on a dedicated limiter), headers, auth — spike S-2 | REQ-CON-005/007/012/016 |
| OAuth | `core.auth.*` | `mcp.client.auth.OAuthClientProvider`, `h11` loopback listener, `keyring` | Flow orchestration, callback server, token/client-info storage | RF-AUTH |
| `Catalog` | `core.catalog` | `uritemplate` | Snapshots, FQNs, definition hashes, visibility, listen watcher, TTL refresh | RF-CAT |
| `ConsentPolicy` | `core.consent.*` | SQLModel | Rule index, evaluation algorithm, prompts, previews, redaction | RF-CNS |
| `Invoker` | `core.invoker`, `core.mrtr` | `jsonschema` | Pipeline, MRTR loop, timeouts, cancellation, outcomes | RF-INV, REQ-HSH-040…044 |
| Input request router | `core.handlers.*` | SDK callbacks | Elicitation, sampling, roots, logging adapters to `Prompter` / provider | RF-HSH |
| `AuditLog` | `core.audit` | SQLModel, `hmac` | Write-once records, keyed hash, queries | REQ-INV-009…012 |
| `EventBus` | `core.events` | anyio memory streams | Fan-out with drop-oldest | §4.11 API |
| MCP Apps (pure) | `core.apps` | — | Constants, CSP builder, visibility rules, bridge JSON-RPC state machine (transport-agnostic) | RF-APP |
| `Toolset` | `toolset` | — | Aliases, filters, outcome → agent text | RF-TLS |
| Adapters | `adapters.pydantic_ai`, `adapters.langgraph` | PydanticAI `AbstractToolset` (`pydantic-ai-slim`); LangChain `BaseTool` subclass (`langchain-core` only) — not `StructuredTool`, which drops arguments named `config`/`run_manager` with dict schemas (spike S-6) | Thin wrappers; denials as `ToolFailed` / `ToolException` + `handle_tool_error=True` | REQ-TLS-005/006/007 |
| UI | `ui.*` | Reflex 0.10, small JS relay asset | Components, `HostState`, `ReflexPrompter`, Apps relay, Host/Origin guard | RF-UI |
| Sandbox proxy | `ui.proxy` | Starlette (Reflex transitive dep) on its own listener | Serves proxy HTML on a distinct origin | REQ-APP-002 |
| CLI + app | `app.*` | `argparse`, `reflex` CLI | `run`, `servers`; app workspace management | RF-UI §6 API |

### 3.3 Data flows

**Flow A — Startup** (REQ-REG-001, REQ-CON-001, REQ-NFR-003)
```
1. CLI/library constructs Host(config, prompter, sampling_provider); Host.start():
2.   open SQLite (WAL) in data_dir; load persistent rules into RuleIndex; load/create HMAC key
3.   ServerRegistry.load(): discover sources → parse each → expand ${VAR} → merge by precedence
     → definitions + shadowed + problems (never raises for third-party files)
4.   for each enabled definition without problems: spawn ServerSupervisor in Host task group
5.   return (connections proceed in background; events: server.state_changed)
```

**Flow B — Supervisor lifecycle / era detection** (REQ-CON-001…006, 013…015, 019)
```
1. state=connecting; build transport (stdio | httpx2 client [+ OAuthClientProvider])
2. mode = definition.mode; if "auto" and EraCache has a fresh DiscoverResult for this
   process-config/origin → Client(mode="2026-07-28", prior_discover=cached) (zero round trips)
   else Client(mode="auto")  # SDK: server/discover probe → initialize fallback
3. async with Client(transport, mode, callbacks…, extensions=[APPS], log_level=…,
                     input_required_max_rounds=8) as client:
4.   era = modern if client.protocol_version >= "2026-07-28" else legacy; record status
5.   if modern: EraCache.put(client.session.discover_result)
6.   Catalog.full_refresh(client) (cache_mode="refresh"); state=ready; failures=0
7.   start watcher: modern → client.listen(tools/prompts/resources list changed per caps)
                    legacy → message_handler routes notifications/*/list_changed
8.   park until: stop requested | transport closed — read-stream EOF observed by the Host's
     Transport wrapper (stdio and HTTP; ~6 ms after a stdio child exits, spike S-2) |
     listen SubscriptionLost/MCPError of transport class (classified by checking the
     wrapper's closed signal). message_handler does not report EOF.
9. on exit: if stop requested → state=disconnected; else state=reconnecting,
   sleep backoff(failures) = min(60, 2^failures * jitter(0.5–1.5)); failures+=1;
   failures == 10 → state=failed, park until reconnect(); loop to 1
   401 + auth metadata during 3 → state=auth_required, park until authorize() completes
   UnsupportedProtocolVersion with no overlap → state=failed (no retry)
   cached modern assumption fails (era-indicating error) → drop cache entry, retry with "auto"
```

**Flow C — Tool invocation** (RF-INV, RF-CNS, REQ-HSH-040…045)
```
1. Invoker.start(fqn, args, consumer) → InvocationHandle (task in Host task group)
2. resolve ToolEntry (not_found) → supervisor ready? (server_unavailable)
3. validate args vs input_schema, Draft 2020-12 (invalid_arguments)
4. ConsentPolicy.evaluate(ConsentRequest) — may await Prompter (timeout excluded from call timeout)
5. AuditLog.begin(record) — failure → audit_unavailable, nothing sent
6. with anyio.fail_after(timeout - prompter_wait):            # see DD-004
     rounds = 0        # input rounds fulfilled; audited as input_rounds (0–8)
     result = await session.call_tool(name, args, allow_input_required=True, progress…)
     while isinstance(result, InputRequiredResult):
         if rounds == 8 → protocol_error/input_rounds_exceeded (input_rounds = 8)
         responses = await InputRequestRouter.fulfil(result.input_requests, ctx)  # per entry:
             elicitation → Prompter.elicit (form|url); sampling → consent(sampling) → provider
             → (review); roots → configured roots; undeclared method → protocol_error
             sampling denied → raise SamplingDenied → outcome denied/sampling_denied (no retry)
         rounds += 1
         result = await session.call_tool(name, args, input_responses=responses,
                                                                                      request_state=result.request_state)
     # spike S-1: progress_callback passed BY KEYWORD on every leg; a state-only
     # InputRequiredResult (no input_requests) is retried with input_responses=None after a
     # short backoff and counts as a round; modern MCPError -32020 (HEADER_MISMATCH) →
     # re-list tools once and resend once; a refused sampling is a client-side abort
     # (nothing sent); a refused elicitation is sent as decline/cancel; ErrorData is never
     # placed in input_responses.
7. classify: is_error → tool_error; structured_content vs output_schema → protocol_error;
   MCPError → protocol_error; RuntimeError from session.call_tool (output-schema mismatch,
   unexpected InputRequiredResult) → protocol_error; transport loss mid-stream → connection_lost (no re-issue)
8. AuditLog.finish(record, outcome, duration, rounds); emit invocation.finished
9. if ToolEntry.ui_resource_uri → UI renders the App (Flow F)
```
Legacy-era Servers deliver input requests as server-initiated JSON-RPC requests whose
callbacks do **not** see the caller's `ContextVar` (spike S-1). Each legacy
`ServerSupervisor` keeps an **in-flight registry** of Host requests (tool calls and reads)
on that connection; a callback attributes the request to the single in-flight entry, or
marks it unattributed when there are zero or several (REQ-HSH-045/047). For an attributed
tool call, a sampling denial is recorded on the invocation and the Invoker overrides the
final outcome to `denied/sampling_denied` whatever `CallToolResult` the Server returns.
Modern connections use the `ContextVar` (visible in the Host's own loop and in the SDK
auto-loop used by reads). The same loop serves catalog reads (`read_resource`,
`read_template`, `get_prompt`), raising `InputRequestError` instead of returning outcomes
(REQ-HSH-046); the router maps callback refusals to distinct `ErrorData` codes so reads
raise the right `InputRequestError` code.

**Flow D — Consent prompt hand-off** (REQ-CNS-002, REQ-UI-005)
```
1. ConsentPolicy creates PendingPrompt(request_id, ConsentRequest, anyio Event + slot)
2. emits consent.requested on EventBus; awaits Prompter.ask_consent(request) with timeout
3. ReflexPrompter.ask_consent registers the pending prompt in PromptRegistry and waits
4. every open tab's HostState watcher sees consent.requested → renders it in consent_center
5. first tab to answer calls PromptRegistry.resolve(request_id, answer) (idempotent;
   later answers are ignored and the prompt disappears from other tabs on consent.resolved)
6. answer → rules created per mapping (API §4.7) → ConsentOutcome → Invoker continues
```

**Flow E — OAuth authorization** (RF-AUTH)
```
1. Supervisor gets 401 + metadata → state=auth_required (OAuth provider configured but
   redirect/callback handlers "parked" until the user starts the flow)
2. ConnectionManager.authorize(id): start LoopbackCallbackServer on 127.0.0.1:<ephemeral>;
   returns AuthorizationFlow(authorization_url) once the provider calls redirect_handler
3. UI opens the URL (CLI prints it); provider awaits callback_handler
4. callback GET /callback?code&state&iss → AuthorizationCodeResult(code, state, iss)
   → provider validates state + iss (REQ-AUTH-006/010), exchanges code (PKCE S256)
5. TokenStore.set_tokens / set_client_info keyed by (server_id, issuer) (REQ-AUTH-004/011)
6. supervisor resumes Flow B step 1; listener closed; 5-min overall deadline
Registration (inside the provider): stored client_info for this issuer → client_metadata_url
(CIMD, if AS advertises support) → DCR (deprecated) → OAuthRegistrationError → we keep
auth_required with "configure client_id" message (REQ-AUTH-003).
```

**Flow F — MCP App render and App-originated call** (RF-APP)
```
1. UI receives invocation.finished for a tool with ui_resource_uri; mounts mcp_app_frame
2. backend: Catalog.read_resource(server_id, ui_uri) → validate MIME text/html;profile=mcp-app
   → csp = core.apps.build_csp(resource._meta.ui.csp) → notice if external domains
3. iframe src = http://localhost:<proxy_port>/proxy?nonce=… (sandbox="allow-scripts allow-same-origin", allow="")
4. proxy → parent: ui/notifications/sandbox-proxy-ready → relay JS forwards to backend
5. backend → proxy: ui/notifications/sandbox-resource-ready {html, csp, sandbox}
   proxy creates inner iframe (srcdoc + CSP meta) — the View
6. View ↔ proxy ↔ relay JS ↔ Reflex event ↔ AppsBridge (Python): ui/initialize …
   initialized → tool-input → tool-result (REQ-APP-006)
7. View tools/call → AppsBridge checks own-server + visibility "app" → Invoker.call(
   consumer=Consumer.app(server_id)) → consent + audit → result back to View (REQ-APP-004/008)
8. unmount → ui/resource-teardown, wait ≤ 2 s → destroy (REQ-APP-010)
```

**Error / compensation flows**

| Step | Failure | Handling |
|---|---|---|
| A.2 | DB cannot open | `Host.start()` raises `MCPHostError`; CLI exit 3 (fail closed) |
| A.3 | third-party file malformed | `ConfigProblem(parse_error)`, file skipped |
| B.3 | stdio command not found | `failed` immediately, reason `command_not_found: <executable>` (env not included); no retry until config change or `reconnect()` |
| B.8 | listen stream lost but requests still succeed | re-listen with backoff + full refresh (REQ-CAT-012); Server stays `ready` |
| C.4 | prompter raises / times out | deny, `source=error/timeout`, audited |
| C.5 | audit begin fails | `audit_unavailable`, call never sent (REQ-INV-012) |
| C.8 | audit finish fails | logged at ERROR; result returned; record remains `in_progress` and is marked `interrupted` on next start (Data Model) |
| C.6 | Host shutdown mid-call | task group cancellation → SDK cancels request → outcome `cancelled` |
| E.4 | `state`/`iss` mismatch | `AuthorizationError`; verifier discarded; stays `auth_required` |
| F.7 | App asks for foreign/app-hidden tool | JSON-RPC `-32602`, logged (REQ-APP-012) |

## 4. Design decisions

### DD-001: Use the high-level `mcp.Client`, drop to `client.session` only for the MRTR loop

- **Decision:** All protocol traffic goes through `mcp.Client` (v2). Only `tools/call`
  uses `client.session.call_tool(..., allow_input_required=True)` with our own loop.
- **Context:** Art. 2 forbids re-implementing JSON-RPC; the SDK covers era detection,
  caching, `x-mcp-header`, cancellation and OAuth.
- **Alternatives:**

| Option | Pros | Cons |
|---|---|---|
| **`Client` + own loop for `tools/call` (chosen)** | Full SDK coverage; per-round control for audit (`input_rounds`), cap 8, no-retry on sampling deny, wall-clock excluding prompter wait | Uses the session tier for one verb (signature churn risk) |
| `Client` auto-loop everywhere | Least code | Cannot count rounds per invocation, cannot exclude prompter wait from timeout, sampling deny surfaces as generic `MCPError` |
| `ClientSession` + hand-wired transports | Maximum control | Re-implements era detection/caching the SDK already has; Art. 2 risk |

- **Justification:** the invocation is where the Constitution's guarantees live (Art. 3),
  so that one path needs explicit control; everything else uses the SDK's defaults.
  Reads (`read_resource`, `get_prompt`) use the auto-loop with
  `input_required_max_rounds=8`.
- **Consequences:** a contract test pins the `session.call_tool` signature (spike S-1);
  the session tier bypasses the response cache, which is correct for `tools/call`.

### DD-002: One supervisor task per Server owning the `async with Client` block

- **Decision:** `ServerSupervisor` is a long-lived task that builds and enters a new
  `Client` per connection attempt and parks inside the block; other tasks use the live
  client through a `ClientLease` (ref-counted, invalidated on exit).
- **Context:** the SDK `Client` has no `connect()/close()`, cannot be reused after its
  block, and must be entered and exited in the same task (anyio cancel-scope rule).
- **Alternatives:**

| Option | Pros | Cons |
|---|---|---|
| **Supervisor task per Server (chosen)** | Respects anyio scoping; isolated failure domain per Server (REQ-CON-011); natural home for backoff | Cross-task access needs a lease to avoid using a closing client |
| `AsyncExitStack` held by the manager | Fewer tasks | Entering/exiting from different tasks breaks cancel scopes; one crash can poison the stack |
| `ClientSessionGroup` (SDK) | Ready-made multi-server merge | Merged view loses per-Server health/era, consent needs per-Server identity, no reconnection policy |

- **Consequences:** reconnect = exit block + re-enter with a new `Client`. In-flight
  calls on a dying lease end `connection_lost` / `server_unavailable`.

### DD-003: Capabilities are fixed per `Client`; capability changes rebuild modern Clients cheaply

- **Decision:** Callbacks are registered only for enabled features
  (`elicitation_callback` iff the prompter `supports_elicitation`; `sampling_callback` iff
  a provider is set; `list_roots_callback` iff roots configured or roots enabled), because
  in the SDK **registering a callback is declaring the capability**. When
  `set_prompter()` / sampling configuration changes, modern Clients are rebuilt with
  `mode="2026-07-28", prior_discover=…` (no network negotiation, effective on the next
  request); legacy Clients keep their capabilities until an explicit `reconnect()`.
- **Context:** REQ-CON-003, REQ-HSH-006/014 and API §4.1 ("modern: next request; legacy:
  after reconnect").
- **Alternatives:** always register dispatcher callbacks (declares capabilities we may not
  serve → violates REQ-HSH-006/014); auto-reconnect legacy too (surprising process
  restarts for stdio).
- **Consequences:** the rebuild is an internal reconnect without a `reconnecting` state
  (state stays `ready`; an `server.state_changed` is *not* emitted). Documented as the
  implementation of the API contract, not a contract change.

### DD-004: Timeouts measured on the Server's clock, excluding human wait

- **Decision:** the call timeout is a deadline that is *paused* while the Invoker awaits
  the `Prompter` (consent, elicitation, sampling review); implemented by running each
  network leg under `anyio.fail_after(remaining)` and decrementing `remaining` by the leg
  duration only.
- **Alternatives:** single `fail_after` around the whole loop (a slow human times the tool
  out — bad UX, contradicts API §4.8); per-leg full timeout (unbounded total).
- **Consequences:** cancellation of a leg is delegated to the SDK (stdio / legacy HTTP:
  `notifications/cancelled`; modern HTTP: stream close) — REQ-INV-003/004 are satisfied by
    cancelling the awaiting scope, verified by integration tests on each transport.
- **Legacy (spike S-1):** the human wait happens inside a single `session.call_tool` leg.
  The leg runs in a `CancelScope` whose `deadline` the callback extends by the measured
  prompter wait when the request is attributed to that invocation; unattributed waits are
  not excluded (documented limitation). Verified in F3-09.

### DD-005: Consent evaluation in memory, persistence in SQLite

- **Decision:** `RuleIndex` keeps all non-suspended rules in dicts keyed by
  `(operation, target_kind, server_id, fqn)` with per-key lists split by consumer; the
  evaluation algorithm (API §4.7) does ≤ 6 dict lookups. Persistent rules are written
  through to SQLite *before* the index is updated; session rules live only in memory.
- **Alternatives:** query SQLite per evaluation (simple, ~ms per call, contention with
  audit writes); policy engine library (OPA/Cedar — new runtime, overkill for 4 scopes).
- **Consequences:** meets REQ-NFR-001 trivially; startup loads all rules (10k rows < 100 ms).
  Write-through failure → the prompt answer still applies once (`allow_once` semantics)
  and the error is surfaced, never silently treated as persistent.

### DD-006: SQLite via SQLModel with a sync engine behind `anyio.to_thread`

- **Decision:** one SQLite file `data_dir/mcphost.db`, WAL mode, `synchronous=NORMAL`,
  SQLModel sync engine; all DB calls run through a single-worker
  `anyio.to_thread.run_sync` limiter (serialized writes).
- **Alternatives:** SQLAlchemy async + `aiosqlite` (extra dependency; async SQLite is a
  thread under the hood anyway); JSON files (no atomic multi-record writes for audit).
- **Consequences:** Art. 8 compliant (blocking I/O only behind `to_thread`); audit writes
  are ~sub-ms, inside the 20 ms budget. Schema details in the Data Model.

### DD-007: Keyed argument hash (HMAC-SHA256) with a per-install key

- **Decision:** `args_hash = "hmac-sha256:" + HMAC(key, canonical_json(arguments))`,
  where canonical JSON = `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` after normalizing floats that are integral. The 32-byte key is
  generated on first run and stored in the keyring (fallback: `data_dir/secrets.json`,
  `0600`).
- **Alternatives:** plain SHA-256 (low-entropy arguments such as `{"confirm": true}` or
  short IDs are brute-forceable from the hash → leaks content); no hash (loses
  correlation).
- **Consequences:** hashes are only comparable within one installation (intended).
    Losing the key breaks correlation with older records but not their integrity. The same
  key computes the project-Server trust fingerprint (Data Model §2.4). Every `keyring` call
  and secret-file access runs via `anyio.to_thread.run_sync` on a dedicated single-worker
  limiter (Art. 8).

### DD-008: Secrets storage — keyring first, `0600` JSON file fallback, keyed by issuer

- **Decision:** `TokenStore` implements the SDK `TokenStorage` protocol per Server using
  the Data Model §3 layout: one entry `oauth:{server_id}` (service `reflex-mcphost`)
  holding `issuer`, `tokens`, `client_info` and `client_id_source`; on issuer change both
  tokens and client info are discarded (REQ-AUTH-011). Every `keyring` call and
  secret-file access runs via `anyio.to_thread.run_sync` on a dedicated single-worker
  limiter (Art. 8).
  If `keyring.get_keyring()` is a fail/null backend, fall back to
  `data_dir/secrets.json` created with `os.open(..., 0o600)`; on Windows, the file sits in
  the per-user data dir (ACL-inherited) and a warning is logged.
- **Consequences:** REQ-AUTH-004/011, REQ-NFR-012. Sign-out deletes both entries
  (REQ-AUTH-007). The same store holds the HMAC key (DD-007).

### DD-009: OAuth loopback callback with a minimal `h11` listener

- **Decision:** `LoopbackCallbackServer` uses `anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)`
  and `h11` (already a transitive dependency of `httpx2`) to serve exactly
  `GET /callback`, then closes. Redirect URI `http://127.0.0.1:{port}/callback`.
- **Alternatives:** uvicorn/Starlette in core (pulls an ASGI stack into a Reflex-free
  core); `http.server` in a thread (blocking, Art. 8).
- **Consequences:** the CIMD document (REQ-AUTH-012) lists `http://127.0.0.1/callback`
  relying on RFC 8252 §7.3 port variance; if an authorization server rejects it, DCR and
  manual `client_id` remain (spike S-4).

### DD-010: JSON Schema validation with `jsonschema` (Draft 2020-12), bounded

- **Decision:** validate tool arguments (REQ-INV-002), `structuredContent` vs
  `outputSchema` (REQ-INV-007) and elicitation content (REQ-HSH-013) with
  `jsonschema.Draft202012Validator`, `$ref` resolution limited to in-document refs
  (`referencing` registry with no retrieval), validators cached by `definition_hash`.
  Elicitation `requestedSchema` is checked against the spec's restricted subset
  (flat object of primitive / enum properties) before rendering (REQ-HSH-012).
- **Consequences:** no network fetches from Server-controlled schemas (Art. 5); schema
  compile errors on a tool → the tool stays listed but invocation returns
  `protocol_error` with the compile detail.

### DD-011: Reflex integration — Host in a lifespan task, per-tab watcher state, prompt registry

- **Decision:** `ui.install(app, host_factory)` calls `app.register_lifespan_task` to run
  the `Host` for the backend's lifetime (one Host per backend process) and stores it in a
  module-level `HostRuntime`. Each tab's `HostState` starts an
  `@rx.event(background=True)` watcher from the page **`on_load`** (on every page that
  needs it) that subscribes to `EventBus` and applies events inside `async with self`;
  watchers are de-duplicated per client token in a process-local registry, not in Reflex
  state (an `on_mount` chain is not cancelled on disconnect — spike S-5). `ReflexPrompter` resolves prompts through a
  process-wide `PromptRegistry` (Flow D). Snapshots (statuses, catalog) are recomputed
  from core on each relevant event, never mutated incrementally in UI state.
- **Alternatives:** Host per tab (N copies of every stdio process — wrong); `app.modify_state`
  push from core (couples core to Reflex tokens, Art. 4).
- **Consequences:** core stays Reflex-free; multiple tabs see the same Host; first answer
  wins. Background watcher tasks started from `on_load` end on tab disconnect (Reflex cancels
  them) and restart on reconnect/reload. R-5 retired for the single-worker case (S-5).

### DD-012: MCP Apps — Python bridge, thin JS relay, sandbox proxy on `localhost:<proxy_port>`

- **Decision:**
  - The JSON-RPC bridge state machine (handshake ordering, method table, own-server and
    visibility checks, teardown) is pure Python in `core.apps.bridge` (transport-agnostic,
    unit-testable). `ui.apps_bridge` adapts it to Reflex events.
  - A ~100-line JS relay (`ui/assets/mcp_app_relay.js`, wrapped as a Reflex custom
    component) only moves messages between the proxy iframe and the backend and filters by
    `event.source === proxyWindow`.
  - The sandbox proxy is a separate Starlette app on its own listener, started by the
    lifespan task, bound to `127.0.0.1` and `::1`, addressed as
    `http://localhost:<proxy_port>` while the Host UI is addressed as
    `http://127.0.0.1:<frontend_port>` — different **host** and **port**, so the proxy's
    `allow-same-origin` cannot reach the UI's origin, storage or cookies (cookies ignore
    ports; they do not ignore host names).
  - The proxy HTML creates the View iframe with `srcdoc` and a CSP `<meta>` built by
    `core.apps.build_csp`, `sandbox="allow-scripts"` on the inner frame, empty `allow`.
- **Alternatives:**

| Option | Pros | Cons |
|---|---|---|
| **Python bridge + JS relay (chosen)** | Consent/audit in Python on every App call (Art. 3); testable without a browser; no npm dependency | One extra hop per message (websocket) — fine for UI traffic |
| `@modelcontextprotocol/ext-apps` AppBridge (npm) in a custom component | Reference behavior for free | Tool calls still must come back to Python; npm dependency + bundling in Reflex; duplicates policy in JS |
| Proxy on same host, different port | Simpler URL | Shared cookies/`localhost`-scoped storage edge cases; weaker isolation argument |

  - Messaging (spike S-3): proxy ↔ View by window identity (`event.source ===
    inner.contentWindow`, target `"*"` — the View origin is opaque); proxy ↔ UI by
    `event.source === window.parent` plus the pinned UI origin. The proxy page sends no CSP
    stricter than the View's (the `srcdoc` View inherits it); the default View CSP never
    includes `'unsafe-eval'`.
- **Consequences:** REQ-APP-002/003 rely on a deterministic origin pair, confirmed in
  Chromium, Firefox and WebKit (S-3; real Safari unverified — Plan R-8); the CLI refuses
  to start unless `localhost` resolves, for **both** `AF_INET` and `AF_INET6`, only to
  loopback addresses (`AF_UNSPEC` alone can hide `::1`). The constants
  `io.modelcontextprotocol/ui` and `text/html;profile=mcp-app` are defined in
  `core.apps` — **not** imported from `mcp.server.apps` (Art. 1 forbids `mcp.server`).
  The client-side advertisement uses `mcp.client.advertise(EXTENSION_ID, {...})`.

### DD-013: Config parsing and writing

- **Decision:** read with `tomllib`/`json`; write our user-level `mcphost.toml` with
  `tomlkit` (preserves user comments/order), atomically (`write tmp → fsync → os.replace`).
  Third-party files are opened read-only (`"rb"`), never locked, never written; a test
  hashes them before/after each registry operation (REQ-REG-008). Paths per OS:

| Source | Linux | macOS | Windows |
|---|---|---|---|
| Claude Desktop | `~/.config/Claude/claude_desktop_config.json` | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor user | `~/.cursor/mcp.json` | same | `%USERPROFILE%\.cursor\mcp.json` |
| VS Code user | `~/.config/Code/User/mcp.json` | `~/Library/Application Support/Code/User/mcp.json` | `%APPDATA%\Code\User\mcp.json` |
| Project | `<cwd>/.mcp.json`, `<cwd>/.cursor/mcp.json`, `<cwd>/.vscode/mcp.json`, `<cwd>/mcphost.toml` | same | same |
| Own user | `platformdirs.user_config_dir("reflex-mcphost")/mcphost.toml` | same | same |

- Config files are read through `anyio.to_thread` (`HostConfig.aload`, `reload`); only
  the sync `HostConfig.load()`, used before an event loop exists, reads them directly
  (Art. 8). Source precedence follows the PRD RF-REG total order. The project-level
  `mcphost.toml` is filtered to `schema_version` + `[servers.*]` (REQ-REG-014).
- **Consequences:** `${VAR}` / `${VAR:-default}` expansion is a small regex expander;
  `${input:…}`, `${env:…}`-style VS Code forms → `unsupported_variable` (REQ-REG-005);
  VS Code `${workspaceFolder}` is expanded to `cwd` (documented).

### DD-014: Event bus on anyio memory object streams

- **Decision:** each subscriber gets a `MemoryObjectSendStream` of size `buffer`;
  publishing uses `send_nowait`; on `WouldBlock` the bus drains one oldest item from the
  subscriber's receive side, increments a dropped counter and retries, then delivers one
  `events.dropped`.
- **Consequences:** publishers never block (API §4.11); subscribers are cleaned up when
  their context manager exits.

### DD-015: Packaging, environments and releases with **uv**

- **Decision:** `uv` is the only supported package/environment manager for development,
  CI and release.
  - `pyproject.toml` (PEP 621) with `hatchling` build backend, src layout;
    `uv.lock` committed; `requires-python = ">=3.11"`.
  - Extras: `pydantic-ai`, `langgraph`, `all`. Dependency groups (PEP 735):
    `dev` (ruff, mypy, import-linter, pre-commit), `test` (pytest, pytest-cov, anyio
    pytest plugin, hypothesis, playwright), `docs`.
  - Everyday commands: `uv sync --all-extras --all-groups`, `uv run pytest`,
    `uv run ruff check`, `uv run mypy src`, `uv run lint-imports`.
  - Legacy-era interop fixtures run a pre-2026 SDK in isolation with
    `uv run --isolated --with "mcp>=1.30,<2" python tests/fixtures/servers/legacy_server.py`
    (no second venv to maintain).
  - Release: `uv build` (sdist + wheel) → wheel-contents check (no `tests/`, no
    `mcp.server` imports — Art. 1) → `uv publish` via PyPI Trusted Publishing from GitHub
    Actions. End users run `uvx reflex-mcphost`.
  - CI: `astral-sh/setup-uv` with cache; matrix Linux/macOS/Windows × Python 3.11/3.13.
- **Alternatives:** pip + pip-tools (no lockfile across extras/groups), Poetry (non-PEP 621
  extras handling, slower), Hatch envs (fine, but uv covers it plus `uvx`).
- **Consequences:** contributors need only `uv`; `uvx` is both the install and run story.

### DD-016: Running a Reflex app from an installed (possibly read-only) package

- **Decision:** the reference app's Reflex sources ship inside the wheel
  (`reflex_mcphost/app/web/`). `reflex-mcphost run` materializes a workspace at
  `platformdirs.user_cache_dir("reflex-mcphost")/app/<version>-<content-hash>/` (copy of
  `rxconfig.py` + app module when the version or the packaged sources change), then runs
  `reflex run --env prod --backend-host 127.0.0.1 --frontend-port P --backend-port P` there
  — **one** port `P` for UI and backend (prod mode requires it; `--env dev` is never used
  because Vite binds all interfaces) — with `GRANIAN_WORKERS=1` and
  `REFLEX_CHECK_LATEST_VERSION=false`; the packaged `rxconfig.py` sets
  `telemetry_enabled=False` and loopback `api_url`/`deploy_url`. It then opens the browser.
  `.web/` build artifacts live in that workspace, never in site-packages; the documented
  start command is `uvx --compile-bytecode reflex-mcphost`, and "no writes to
  site-packages" excludes `__pycache__/*.pyc` (spike S-5).
- **Alternatives:** run from site-packages (read-only under `uvx`, pollutes the install);
  pre-built static export (loses backend features).
- **Consequences:** first run compiles the frontend (Reflex requires Node/Bun, which
  Reflex installs itself); REQ-NFR-003's 10 s target applies to warm runs (2.6–5.7 s on Linux in S-5); every
  warm start includes a Vite production build, so it grows with UI size — re-measured in
  Phase 6.

### DD-017: Loopback guard for the reference app

- **Decision:** a Starlette middleware installed through `rx.App(api_transformer=...)`
    rejects HTTP, websocket and static-UI requests whose `Host` is not `127.0.0.1:<port>` or
  whose `Origin` (when present) is not `http://127.0.0.1:<port>` (single port, DD-016) → `403`
  (REQ-NFR-011). `localhost` is deliberately **not** accepted for the UI: the name is
  reserved for the MCP Apps sandbox proxy (DD-012), and a UI loaded via `localhost` cannot
  reach the backend. The guard also refuses the UI page itself, so the notice "open
  http://127.0.0.1:<port>" is the `403` response body (S-5); `cors_allowed_origins` is the
  same single origin (defence in depth); the CLI always opens the `127.0.0.1` URL. Binding to non-loopback requires `--unsafe-allow-remote`
  (REQ-UI-002). An E2E test opens the UI via `localhost` and asserts a `403` with the notice text
  and that the proxy cannot read UI cookies (F7-06).
- **Consequences:** DNS-rebinding attacks from other sites cannot reach the backend
  event websocket that can trigger tool calls.

### DD-018: Architecture enforcement

- **Decision:** import-linter contracts in `pyproject.toml`:
  1. `layers`: `reflex_mcphost.app` > `reflex_mcphost.ui` > `reflex_mcphost.adapters` >
     `reflex_mcphost.toolset` > `reflex_mcphost.core`.
  2. `forbidden`: `reflex_mcphost` ↛ `mcp.server` (Art. 1).
  3. `forbidden`: `reflex_mcphost.core`, `reflex_mcphost.toolset` ↛ `reflex`,
     `pydantic_ai`, `langchain_core`, `langgraph`, `starlette`, `fastapi` (Art. 4).
  4. `independence`: `reflex_mcphost.adapters.pydantic_ai` ⟂ `reflex_mcphost.adapters.langgraph`.
  Plus an AST test: only `core.invoker`, `core.mrtr` and `core.handlers.sampling` may
    reference `call_tool` / sampling provider calls (Art. 3). For Art. 3, `core.mrtr` is part
  of the Invoker (it implements pipeline step 6) and `core.handlers.sampling` is the
  sampling handler.

## 5. Patterns and conventions

### 5.1 Code structure

```
src/reflex_mcphost/
├── __init__.py                 # public re-exports (API §3.6)
├── py.typed
├── core/
│   ├── host.py                 # Host, HostConfig wiring
│   ├── errors.py               # MCPHostError hierarchy
│   ├── types.py                # ServerId, Fqn, Consumer, ids (ULID)
│   ├── secrets.py              # redaction helpers, SecretStr utilities
│   ├── config/                 # paths.py, expand.py, own.py (tomlkit), claude.py, cursor.py, vscode.py, mcp_json.py
│   ├── registry.py
│   ├── connection/             # manager.py, supervisor.py, transports.py, era_cache.py, stderr.py, lease.py
│   ├── auth/                   # oauth.py, token_store.py, callback_server.py
│   ├── catalog.py
│   ├── consent/                # policy.py, index.py, store.py, preview.py
│   ├── invoker.py
│   ├── mrtr.py                 # tools/call loop + InputRequestRouter
│   ├── handlers/               # elicitation.py, sampling.py, roots.py, logging.py (deprecated ones flagged)
│   ├── audit.py
│   ├── db.py                   # engine, WAL pragmas, thread limiter
│   ├── events.py
│   ├── schema.py               # jsonschema validators + elicitation subset check
│   └── apps/                   # constants.py, csp.py, bridge.py (pure JSON-RPC state machine)
├── toolset/                    # __init__.py (Toolset, ToolSpec), aliases.py, filters.py
├── adapters/                   # pydantic_ai.py, langgraph.py (optional extras)
├── ui/
│   ├── install.py              # lifespan task, HostRuntime, guard middleware
│   ├── state.py                # HostState + background watcher
│   ├── prompter.py             # ReflexPrompter, PromptRegistry
│   ├── components/             # servers_panel.py, catalog_browser.py, consent_center.py, permissions_table.py, audit_table.py, mcp_app_frame.py, schema_form.py
│   ├── apps_bridge.py
│   ├── proxy/                  # server.py, proxy.html
│   └── assets/mcp_app_relay.js
└── app/
    ├── cli.py                  # argparse: run | servers | --version
    ├── workspace.py            # DD-016
    └── web/                    # rxconfig.py, mcphost_app/mcphost_app.py
tests/
├── unit/                       # pure core logic, no I/O
├── integration/                # in-process + stdio + HTTP fixture Servers
├── interop/                    # era matrix, OAuth, x-mcp-header, MRTR
├── e2e/                        # Playwright against the reference app
└── fixtures/servers/           # modern_server.py, legacy_server.py, apps_server.py, auth_server.py (tests only — Art. 1)
docs/oauth/client.json          # CIMD document published to GitHub Pages (REQ-AUTH-012)
```

### 5.2 Patterns applied

| Pattern | Where | Why |
|---|---|---|
| Composition root | `core.host.Host` | One place wires dependencies; tests swap any part |
| Supervisor | `connection.supervisor` | Per-Server failure domain, backoff, async-with lifecycle (DD-002) |
| Lease | `connection.lease` | Safe cross-task use of a Client that may be closing |
| Pipeline | `invoker` | Normative step order (API §4.8) maps 1:1 to code and tests |
| Port/adapter | `Prompter`, `SamplingProvider`, `TokenStore` | Human/LLM/secret backends pluggable; core testable with fakes |
| Repository | `consent.store`, `audit` | SQL isolated from policy logic |
| Write-through cache | `consent.index` | REQ-NFR-001 with durable rules (DD-005) |
| Pub/sub with backpressure | `events` | UI fan-out without blocking core (DD-014) |
| State machine | `apps.bridge`, supervisor states | Explicit, testable transitions |

### 5.3 Error handling

```python
class MCPHostError(Exception): ...
class ConfigError(MCPHostError): ...
class UnknownServerError(MCPHostError): ...
class UnknownEntryError(MCPHostError): ...
class ServerUnavailableError(MCPHostError): ...
class CapabilityNotSupportedError(MCPHostError): ...
class AuthorizationError(MCPHostError): ...
class ServerRequestError(MCPHostError): ...     # wraps mcp MCPError (code, message, data)
class HostClosedError(MCPHostError): ...
# internal, never escape the Invoker:
class _SamplingDenied(Exception): ...           # → outcome denied / sampling_denied
class _InputRoundsExceeded(Exception): ...      # → protocol_error / input_rounds_exceeded
```

SDK exception mapping (single function `map_sdk_error`, unit-tested):

| SDK | Invoker outcome | Catalog/Prompt read |
|---|---|---|
| `CallToolResult.is_error` | `tool_error` | — |
| `MCPError` (JSON-RPC error) | `protocol_error` | `ServerRequestError` |
| `MCPError` with code `-32022` | n/a (connect-time) | — |
| transport exceptions (`httpx2.TransportError`, `anyio.BrokenResourceError`, `EndOfStream`) | `connection_lost` | re-issue once, then `ServerUnavailableError` |
| `TimeoutError` from our deadline | `timeout` | `TimeoutError` |
| `OAuthFlowError` | n/a | `AuthorizationError` |
| `RuntimeError` from `session.call_tool` (spike S-1) | `protocol_error` | — |
| `MCPError` `-32020` HEADER_MISMATCH (modern) | one re-list + resend, then `protocol_error` | one re-list + resend, then `ServerRequestError` |
| auto-loop `InputRequiredRoundsExceededError` | n/a | `InputRequestError(input_rounds_exceeded)` |

Never `except Exception: pass`; every swallowed exception is logged with server_id /
invocation_id (Art. 8, silent-failure rule).

## 6. Security

### 6.1 Attack surface

| Vector | Mitigation | REQ / Art. |
|---|---|---|
| Malicious project config spawning commands or exfiltrating env secrets through a remote URL/headers | Trust prompt before any process or request, per HMAC fingerprint — stdio: command, args, cwd, origin, env names **and values**, risky env names flagged; HTTP: URL, origin, header names **and values** | REQ-REG-011 |
| Project `mcphost.toml` changing host settings or loading a sampling provider | Project file limited to `[servers.*]` | REQ-REG-014 |
| Tool definition "rug pull" after trust | `definition_hash` on rules; suspend + re-ask | REQ-CNS-007 |
| Prompt-injection via tool descriptions/annotations | Annotations display-only; descriptions shown verbatim in consent UI, never auto-approved | REQ-CAT-011 |
| Agent self-escalation (agent approves itself) | Consent scoped to requesting Consumer; only `human:*` prompters answer | REQ-CNS-015 |
| MCP App escaping to Host origin | Sandbox proxy on distinct host+port; inner `allow-scripts` only; CSP from declared domains; no permissions | REQ-APP-002/011, Art. 5 |
| App calling other Servers' / hidden tools | Own-server + `visibility` check; consent + audit | REQ-APP-004/008 |
| Forged postMessage | Accept only `event.source === proxyWindow`; shape validation in Python | REQ-APP-003 |
| MCP App View navigating its own frame to send data out | Not blocked by CSP in any engine (S-3): **residual risk**; Views only come from Servers the user connected; v0.2 candidate: tear the View down on a second inner `load` | Art. 5 |
| DNS rebinding / CSRF on local backend | Loopback bind; Host/Origin middleware accepting only `127.0.0.1:<port>` (HTTP, websocket, static UI) | REQ-UI-002, REQ-NFR-011 |
| OAuth code interception / mix-up | PKCE S256, `state`, `iss` (RFC 9207), issuer-keyed credentials, loopback-only redirect | RF-AUTH |
| Secrets in logs/UI/DB | `SecretStr`; logging filter redacting known secret values; redaction test | REQ-REG-007, REQ-NFR-010 |
| Low-entropy argument recovery from audit | HMAC with per-install key | DD-007 |
| Remote `$ref` / schema bombs | No retrieval; `jsonschema` bounded; compile errors contained per tool | DD-010 |
| Server-provided `requestState` tampering | Opaque echo only (REQ-HSH-041); integrity is the Server's duty | Spec MRTR |
| Oversized SSE events | SDK `max_sse_event_size` default kept (configurable) | — |

### 6.2 Sensitive data

| Data | Classification | Storage | Access |
|---|---|---|---|
| OAuth access/refresh tokens | Secret | keyring / `0600` file | `TokenStore` only |
| OAuth client info (client_id/secret) | Secret | keyring / `0600` file, keyed by issuer | `TokenStore` only |
| Env values / headers from configs | Secret | memory only (`SecretStr`), never persisted by us | transports |
| HMAC key | Secret | keyring / `0600` file | `audit` |
| Tool arguments / results | Confidential | memory only; never in DB (hash only) | Invoker, UI session |
| Consent rules | Internal | SQLite (`0600`) | consent |
| Audit records | Internal | SQLite (`0600`) | audit (read-only API) |
| Server log streams | Internal (may contain secrets from Servers) | memory ring buffer only | UI; redaction filter applied |

The redaction registry ignores values shorter than 4 characters: masking them would
corrupt unrelated log text (an untrusted config could register `"a"`), and such values
carry no meaningful secrecy.

## 7. Observability

### 7.1 Logging
stdlib `logging`, logger tree `reflex_mcphost.*` (REQ-NFR-040). Library code never
configures handlers; the CLI configures a console handler (text, or JSON with
`--log-json`). Context via `LoggerAdapter` extras: `server_id`, `fqn`,
`invocation_id`, `request_id`, `era`. A `SecretRedactionFilter` replaces any registered
secret value with `**********` before emission.

```json
{"ts":"2026-10-30T10:00:00Z","level":"INFO","logger":"reflex_mcphost.invoker",
 "msg":"invocation finished","invocation_id":"inv_01J…","fqn":"github::create_issue",
 "consumer":"agent:triage-bot","outcome":"success","duration_ms":412,"input_rounds":1}
```

### 7.2 Metrics
No metrics backend (non-goal). In-process counters exposed for the UI status bar and
tests: invocations by outcome, consent decisions by source, reconnect attempts per
Server, dropped events.

### 7.3 Alerts
Not applicable (local single-user app). User-facing signals instead: Server state badges,
`failed`/`auth_required` banners, `events.dropped` warning, audit-unavailable error toast.

## 8. Testing strategy

| Level | Target | Tools | Covers |
|---|---|---|---|
| Unit | core ≥ 90 % lines | pytest, anyio plugin, hypothesis | config parsing/precedence/expansion, consent evaluation (property tests over rule sets), alias algorithm (property: deterministic, collision-free, regex-valid), CSP builder, bridge state machine, error mapping, redaction |
| Integration | all RF-CON/CAT/INV/HSH MUSTs | in-process fixture Servers (`Client(server)`), stdio subprocess fixtures, HTTP fixtures under uvicorn on ephemeral ports | lifecycle, MRTR (elicitation/sampling/roots, cap, deny), cancellation/timeout per transport, listen + refresh, x-mcp-header drop |
| Interop | era matrix + auth | `tests/interop/`: modern server (mcp 2.3), legacy server via `uv run --isolated --with "mcp>=1.30,<2"`, fixture OAuth AS | modern↔dual, legacy fallback, `UnsupportedProtocolVersion`, session 404 re-init, CIMD/DCR/manual, `iss` mismatch |
| Contract | SDK surface | pytest | pins `Client(...)` kwargs, `session.call_tool` signature, callback signatures, `advertise` — fail fast on SDK upgrades |
| Architecture | Art. 1/3/4 | import-linter, AST test, wheel-contents test | layers, forbidden imports, single effect path, no tests in wheel |
| Security | Art. 5/6 | pytest | secret-seeded redaction test, third-party files byte-identical, file modes `0600`, guard middleware 403s |
| E2E | happy paths | Playwright (Python) against `reflex-mcphost run` | consent prompt → allow → result; elicitation form; MCP App render + App tool call + teardown |
| Performance | NFR-001/002 | pytest-benchmark | consent eval at 10k rules; Host overhead vs. in-process server |

Traceability: every test docstring starts with the REQ IDs it verifies
(`"""REQ-CNS-007: …"""`); `scripts/check_req_coverage.py` (run in CI via `uv run`) fails
if any `MUST` REQ in the PRD has no citing test.

## 9. Rollout

### 9.1 Release strategy
- `0.1.0a1` (core + toolset, no UI) → `0.1.0b1` (UI + Apps) → `0.1.0`.
- GitHub Actions: lint/type/test matrix on every PR; on tag `v*`: `uv build`, wheel
  checks, `uv publish` (Trusted Publishing), deploy `docs/oauth/client.json` to GitHub
  Pages (URL frozen from `0.1.0` on — REQ-AUTH-012).
- Pre-commit: gitleaks, ruff, ruff-format, mypy (via `uv run`), import-linter.

### 9.2 Backward compatibility
- Nothing to migrate (greenfield). `schema_version = 1` in `mcphost.toml`; DB schema
  versioned with a `meta(schema_version)` row and forward-only migrations (Data Model).
- SDK minor upgrades (`2.4`) require the contract tests to pass and a Delta Spec if
  behavior changes.

## 10. Spikes (to schedule first in the Implementation Plan)

| ID | Question | Exit criterion |
|---|---|---|
| S-1 | `client.session.call_tool(..., allow_input_required=True, input_responses=, request_state=)` signature and progress callback in 2.3.x; behavior of a callback returning `ErrorData` under modern vs legacy | Contract test green; DD-001 confirmed or amended |
| S-2 | stdio stderr capture: `stdio_client(params, errlog=…)` accepted types; process exit detection while parked | stderr lines reach ring buffer; exit → `reconnecting` within 1 s |
| S-3 | Sandbox proxy origin pair (`127.0.0.1` UI vs `localhost` proxy) in Chrome/Firefox/Safari, IPv6 `localhost` resolution | Example MCP App from `ext-apps` renders and calls a tool in all three |
| S-4 | CIMD with loopback redirect against at least one real AS advertising `client_id_metadata_document_supported`; DCR fallback | Token obtained via CIMD or documented fallback |
| S-5 | `uvx reflex-mcphost` cold/warm start with workspace materialization (DD-016) on Linux/macOS/Windows | Warm start < 10 s; no writes to site-packages |
| S-6 | Adapter shapes: PydanticAI 2.64 `AbstractToolset` (`get_tools`, `call_tool`), LangChain `StructuredTool` with dict `args_schema` | Example agents call a Host tool through consent |

## 11. Open questions

- [x] "Agent playground" page in the reference app → **deferred to v0.2** (resolved 2026-10-30).
- [x] Windows secret-file fallback → **accept** per-user data dir ACL inheritance **and log a
      warning** (resolved 2026-10-30; DD-008).

## Constitution check

- **Art. 1** — no `mcp.server` import in `src/` (DD-012 constants, DD-018 contract); Server fixtures only in `tests/fixtures/servers/`; wheel check in release (DD-015).
- **Art. 2** — §0 pins the revision set with verification date and sources; all protocol work via `mcp.Client` (DD-001); deprecated features isolated in `core.handlers` with `deprecated` flags.
- **Art. 3** — single effect path (Invoker/MRTR/sampling handler) enforced by AST test (DD-018); fail-closed flows (§3.3 error table); audit before send (Flow C step 5).
- **Art. 4** — layers + forbidden imports (DD-018); Host runs in a lifespan task without core importing Reflex (DD-011); adapters as extras (DD-015).
- **Art. 5** — DD-007, DD-008, DD-010, DD-012, DD-017; §6.
- **Art. 6** — DD-013 read-only opens + byte-identical test.
- **Art. 7** — §8 coverage targets, interop against real Servers on both transports and eras, REQ coverage script.
- **Art. 8** — `mypy --strict`, ruff, import-linter, gitleaks in CI; blocking I/O only via `anyio.to_thread` (DD-006).
- **Art. 9** — spec-anchored; Conventional Commits; SemVer pre-releases (§9).
- **Exceptions requested:** none.

---

## Change history

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1-draft.1 | 2026-10-30 | Ernesto Crespo | Initial draft |
| 1.0 | 2026-10-30 | Ernesto Crespo | Approved; open questions resolved (§11) |
| 1.1 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-analyze-fixes`: backoff cap, round counting, reads' Consumer, DD-007/008/013/017/018, §6.1 rows |
| 1.2 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-a`: transport wrapper + stderr pipe, legacy in-flight registry, MRTR loop details, adapter base class, distributions |
| 1.3 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-b`: DD-011 `on_load` watcher, DD-012 messaging/CSP/IPv6 check, DD-016 single port + prod, DD-017 403-body notice, §6.1 residual risk |
| 1.4 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-project-trust`: §6.1 trust row covers HTTP project definitions; §6.2 redaction floor |
