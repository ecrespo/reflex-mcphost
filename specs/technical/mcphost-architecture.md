# reflex-mcphost v0.1 — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `APPROVED` |
| **Version** | 1.17 |
| **Date** | 2026-11-01 |
| **Related PRD** | `specs/prd/mcphost-v0.1.md` v1.10 |
| **Related API Spec** | `specs/api/mcphost-api-v0.1.md` v1.17 |
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
| pydantic | `>=2.11,<3` | PyPI (models of the public API) |
| h11 | `>=0.16` | PyPI (DD-009 loopback listener, DD-012 sandbox proxy) |
| platformdirs | `>=4.14` | PyPI |
| jsonschema | `>=4.26` | PyPI |
| regex | `>=2026.9` | PyPI (2026.9.29; wheels cp310–cp315 for Linux, macOS, Windows); `timeout=` keyword on `search` / `fullmatch` (DD-010, REQ-NFR-021) |
| python-ulid | `>=4.1` | PyPI |
| tomlkit | `>=0.13` | PyPI (comment-preserving TOML writes) |
| uritemplate | `>=4.2` | PyPI (RFC 6570) |
| import-linter (dev) | `>=2.15` | PyPI |

Re-verification is required before every minor release; a change in any row goes through
a Delta Spec. Rows `regex`, `pydantic` and `h11` added 2026-11-01 (delta
`2026-11-phase8-prep`). **Re-verified on 2026-11-01 (F8-06): no row changed** — MCP
`2026-07-28` is still the current revision (versioning page); ext-apps `specification/`
holds only `2026-01-26` and `draft`; for every PyPI row the latest release equals the locked
one, except `platformdirs` 4.14.2 (2026-10-31), a patch inside the `>=4.14` pin above the
locked 4.14.0 — the lock refresh is left to Dependabot (not performed for `0.1.0`).

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
 Browser tab           │  Reflex frontend + backend on one port: 127.0.0.1:<port> (DD-016)                 │
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
| `ConnectionManager` | `core.connection.manager` | — | Public façade over supervisors; status; authorize/sign-out; logs; roots/log level. Host-injected seam `roots_writer` (declared on the class with default `None`, assigned on the instance by `Host`) persists roots via `ServerRegistry.set_roots`; `set_roots` raises `RuntimeError` when the manager is not attached to a Host | RF-CON, RF-AUTH |
| `ServerSupervisor` | `core.connection.supervisor` | `mcp.Client`, anyio | One task per Server: build Client, enter it, watch liveness, backoff, era cache | REQ-CON-001…019 |
| Transport factory | `core.connection.transports` | `mcp.client.Transport` wrapper over `stdio_client(params, errlog=<pipe write end>)`, `streamable_http_client`, `httpx2` | Build transports (stdio always as `Client(<own Transport>)`, never `Client(StdioServerParameters)`), EOF-observing wrapper for liveness, per-attempt stderr pipe (POSIX `wait_readable` reader; Windows `to_thread` reader on a dedicated limiter), headers, auth — spike S-2 | REQ-CON-005/007/012/016 |
| OAuth | `core.auth.*` | `mcp.client.auth.OAuthClientProvider`, `h11` loopback listener, `keyring` | Flow orchestration, callback server, token/client-info storage; a provider is attached only to HTTP Servers whose definition has no static headers (REQ-AUTH-009) | RF-AUTH |
| `Catalog` | `core.catalog` | `uritemplate` | Snapshots, FQNs, definition hashes, visibility, listen watcher, TTL refresh | RF-CAT |
| `ConsentPolicy` | `core.consent.*` | SQLModel | Rule index, evaluation algorithm, prompts, previews, redaction | RF-CNS |
| `Invoker` | `core.invoker`, `core.mrtr` | `jsonschema` | Pipeline, MRTR loop, timeouts, cancellation, outcomes | RF-INV, REQ-HSH-040…044 |
| Input request router | `core.handlers.*` | SDK callbacks | Elicitation, sampling, roots, logging adapters to `Prompter` / provider | RF-HSH |
| `AuditLog` | `core.audit` | SQLModel, `hmac` | Write-once records, keyed hash, queries | REQ-INV-009…012 |
| `EventBus` | `core.events` | anyio memory streams | Fan-out with drop-oldest | §4.11 API |
| MCP Apps (pure) | `core.apps` | — | Constants, CSP builder, visibility rules, bridge JSON-RPC state machine (transport-agnostic) | RF-APP |
| `Toolset` | `core.toolset` (implementation, framework-neutral) · `toolset` (public re-export) | — | Aliases, filters, outcome → agent text | RF-TLS |
| Adapters | `adapters.pydantic_ai`, `adapters.langgraph` | PydanticAI `AbstractToolset` (`pydantic-ai-slim`); LangChain `BaseTool` subclass (`langchain-core` only) — not `StructuredTool`, which drops arguments named `config`/`run_manager` with dict schemas (spike S-6) | Thin wrappers; denials, vanished aliases and (LangChain) non-object inputs as `ToolFailed` / `ToolException` + `handle_tool_error=True`; `HostToolset.toolset` read-only property (not public API) | REQ-TLS-005/006/007 |
| UI | `ui.*` | Reflex 0.10, small JS relay asset | Components, `HostState`, `ReflexPrompter`, Apps relay, Host/Origin guard | RF-UI |
| Sandbox proxy | `ui.proxy` | `h11` + `anyio` listener (like DD-009) on one port bound on `127.0.0.1` and `::1`, owned by the `ui.install` lifespan | Serves `GET /proxy` on the distinct origin `http://localhost:<proxy_port>` | REQ-APP-002 |
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
6.   state=ready; failures=0; on_ready hooks → Catalog.full_refresh(client) (cache_mode="refresh");
     a cached modern entry is first validated by one declared list request; an era-indicating
     MCPError drops the entry and retries with "auto" (no failure counted)
     ConnectionManager.wait_ready does not resolve on the ready transition alone: it also
     awaits a Host-injected ready gate for the live Client (Catalog.wait_loaded, passed once
     that Client's initial full refresh ended, success or failure); keyed by Client identity,
     so a new session (incl. an internal legacy rebuild) is gated on its own refresh;
     core.connection never imports core.catalog (Art. 4)
     legacy HTTP 404 on a session id (detected by an httpx response hook) → internal rebuild,
     no state change, bounded: > 3 rebuilds without a successful request → transport lost
     the Client body runs shielded from entry; stop/reconnect/outer cancel end it through an
     interrupt event so the stdio escalation and the legacy DELETE always run (≤ 10 s each)
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
3. validate args vs input_schema, DD-010 (invalid_arguments; schema compile failure →
   protocol_error/invalid_input_schema)
4. ConsentPolicy.evaluate(ConsentRequest) — may await Prompter (timeout excluded from call timeout)
5. AuditLog.begin(record) — writes consent source + rule id; failure (incl. arguments that
   cannot be canonicalized) → audit_unavailable, nothing sent. Early failures (steps 1–4)
   use AuditLog.record() instead; not_found/invalid_arguments on non-JSON input store an
   empty hash; if that write fails → audit_unavailable, details.attempted_outcome
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
     # re-list tools once and resend once, per leg; a refused sampling is a client-side abort
     # (nothing sent); a refused elicitation is sent as decline/cancel; ErrorData is never
     # placed in input_responses. A rejected elicitation schema or URL (REQ-HSH-012) is a
     # client-side abort on modern (like a refused sampling: InputHandlerError →
     # protocol_error/input_handler_failed), a JSON-RPC error (-32602) on legacy.
7. classify: is_error → tool_error (structured_content not validated);
   structured_content vs output_schema → protocol_error;
   MCPError → protocol_error; RuntimeError from session.call_tool (output-schema mismatch,
   unexpected InputRequiredResult) → protocol_error; transport loss mid-stream → connection_lost (no re-issue)
8. AuditLog.finish(id, outcome, duration, rounds, error_code) — never raises (logs ERROR,
   returns False); emit invocation.finished
9. if ToolEntry.ui_resource_uri → UI renders the App (Flow F)
```
Legacy-era Servers deliver input requests as server-initiated JSON-RPC requests whose
callbacks do **not** see the caller's `ContextVar` (spike S-1). Each legacy
`ServerSupervisor` keeps an **in-flight registry** of Host requests (tool calls and reads)
on that connection; a callback attributes the request to the single in-flight entry, or
marks it unattributed when there are zero or several (REQ-HSH-045/047). For an attributed
tool call, a sampling denial is recorded on the invocation and the Invoker overrides the
final outcome to `denied/sampling_denied` whatever `CallToolResult` the Server returns —
also when the call itself ends `protocol_error` (the Server answers the refused sampling
with a JSON-RPC error); the override never replaces `timeout` or `connection_lost`.
Modern connections use the `ContextVar` (visible in the Host's own loop). The same loop
— on `client.session.*` with `allow_input_required=True`, not the SDK auto-loop — serves
catalog reads (`read_resource`, `read_template`, `get_prompt`), raising
`InputRequestError` instead of returning outcomes (REQ-HSH-046); the router maps callback
refusals to distinct `ErrorData` codes so reads raise the right `InputRequestError` code.
An `InputHandlerError` raised inside a read propagates unchanged.

Audit writer (internal; API §4.9 exposes only reads): `AuditLog(db, key, *, clock=)` with
`begin`, `finish`, `record`, `query`, `get`, `mark_interrupted` (startup repair, Data Model
Q-A5). It takes `fqn: Fqn` only — the Invoker maps an unparsable FQN string to
`ValueError` first. Write failures raise `AuditUnavailableError`; `get()` of an unknown or
in-progress id raises `AuditRecordNotFoundError`.

**Flow D — Consent prompt hand-off** (REQ-CNS-002, REQ-UI-005)
```
1. ConsentPolicy creates PendingPrompt(request_id, ConsentRequest, anyio Event + slot)
2. emits consent.requested on EventBus; awaits Prompter.ask_consent(request) with timeout
3. ReflexPrompter.<method> registers a PendingPrompt(kind, request_id, payload, created_at)
   in the process-wide PromptRegistry and awaits its answer. kind ∈ {consent (tool call
   or sampling, incl. definition_changed / unattributed), sampling_review, elicitation_form,
   elicitation_url, trust}; payload = the core request object as given (ConsentRequest
   with its argument preview, SamplingResponseReview, ElicitationRequest, TrustRequest)
4. PromptRegistry publishes added / updated / removed on its own bounded change stream
   (one anyio memory stream per subscriber, drop-oldest + one "dropped" marker, like the
   EventBus); every tab's HostState watcher merges it with its EventBus subscription and
   renders consent_center from PromptRegistry.pending() (also on first load, so a tab
   opened later sees prompts raised before it)
5. first tab to answer calls PromptRegistry.resolve(request_id, answer) (idempotent;
   later answers are ignored). The entry is removed — "removed" published — when answered,
   when the core-side wait ends (consent_timeout_s, REQ-CNS-009) or when the awaiting
   coroutine is cancelled (Server disconnected, Host closing): ReflexPrompter removes it
   in a `finally`. PromptRegistry.ask joins on the request id: a second ask while the id
   is still pending replaces the payload and publishes "updated", and every asker gets the
   one answer. Elicitation form content is validated in the UI against the requested
   schema before it is sent (an invalid submit keeps the card with field errors), so a
   core re-prompt (REQ-HSH-013) is rare; when one comes after the first ask ended it is a
   new entry ("removed", then "added" with the same id) — the form is re-rendered, not
   kept open (consent.requested / consent.resolved stay on the EventBus for library users and the
   status bar; the UI does not depend on them to render)
6. answer → rules created per mapping (API §4.7) → ConsentOutcome → Invoker continues
```

**Flow E — OAuth authorization** (RF-AUTH)
```
1. HTTP Servers without static headers get one OAuthController owning the SDK
   OAuthClientProvider (TokenStore storage, client_metadata_url = oauth_client_metadata_url).
   Parked by default: redirect_handler raises AuthorizationDeferredError, so a 401 + auth
   metadata ends the attempt and the supervisor sets auth_required (no browser, no listener).
   While parked with no concrete redirect yet, the controller reserves a free loopback port
   (bind, read, close) so a DCR registration made during discovery records a port that
   step 2 can re-bind.
2. ConnectionManager.authorize(id): only in auth_required; a running flow is returned as is.
   Bind LoopbackCallbackServer on 127.0.0.1:<ephemeral> (DCR clients first try their
   registered port), set the provider's redirect URI to that port, arm the handlers and
   kick the supervisor: state auth_required → connecting. The SDK runs discovery
   (RFC 9728 → RFC 8414), registration (configured → CIMD → DCR) and calls
   redirect_handler(url) → AuthorizationFlow(authorization_url=url) is returned.
3. UI opens the URL (CLI prints it); callback_handler awaits the listener (5-min deadline
   for the whole flow).
4. GET /callback?code&state&iss: the listener MUST complete only on an authentic callback —
   state equal to the one in the authorization URL (constant time) and either an AS error
   or a code plus an iss that passes the SDK validate_authorization_response_iss on the
   discovered metadata — all checked BEFORE AuthorizationCodeResult is returned to the SDK.
   Any other /callback → 400 + failure page and the listener keeps waiting (login CSRF:
   a forged callback can neither end nor hijack the flow); other paths (and everything
   after the authentic callback) → 404, other methods → 405. A callback with our state
   refused for its iss is remembered and reported at the deadline as iss_mismatch;
   foreign-state callbacks are ignored. Callback values are registered for redaction only
   once authentic. AS error (access_denied), deadline (timeout / iss_mismatch) or cancel →
   AuthorizationAbortedError(reason): nothing redeemed, verifier dropped, listener closed.
5. The SDK exchanges the code (PKCE S256, resource indicator); TokenStore.set_tokens
   persists oauth:{server_id} (bound to the Server's canonical resource and to the current
   issuer — REQ-AUTH-004/011, Data Model §3); the same attempt continues → ready;
   flow.wait() returns. When the attempt leaves connecting without ready, wait() raises
   AuthorizationError(reason): the recorded reason, flow_error if none was recorded,
   cancelled for a stop (disconnected), an unknown Server or a closed Host.
6. Refresh: expiry → the SDK's proactive refresh. A rejected, unexpired token → the
   controller replays the request once with the in-memory token marked expired so the SDK
   refresh runs — only if the token is still the one that was sent (a concurrent request
   may have refreshed it already: then the request is just replayed). A second rejection
   or a failed refresh → auth_required (REQ-AUTH-005). A refresh response other than 200
   deletes the stored tokens (drop_tokens; client info kept) so they are never reloaded.
7. A 401 while ready marks the observed transport closed → ready → reconnecting → the
   attempt's 401 → auth_required (two state events, no direct edge). The transport's auth
   reason (client_id_required) becomes ServerStatus.reason; a transport fatal_reason
   (http_unauthorized: static headers, REQ-AUTH-009) → failed.
Discovery first (REQ-AUTH-011): TokenStore withholds stored tokens until the SDK has
discovered the AS and the issuer check passed, so the first attempt after a restart is
unauthenticated; when it reaches the parked redirect, the tokens the store now releases are
loaded and the request is replayed once with them (no prompt). Their expiry is unknown, so
the first refresh after a restart is triggered by a 401 (step 6).
Lock-free fast path: the SDK provider serializes whole requests on one lock (a long
tools/call would block e.g. a cancellation), so requests that need no auth work (the
Server never answered 401, or a valid token after discovery) are sent without it, the
controller attaching the bearer; a 401 falls through to the SDK flow and is replayed there.
Registration (inside the provider): configured client_id → client_metadata_url (CIMD, if
the AS advertises support) → DCR (deprecated) → OAuthRegistrationError → we keep
auth_required with reason client_id_required ("configure client_id") (REQ-AUTH-003).
Issuer binding: the SDK drops client info bound to another issuer (SEP-2352) and
re-registers; CIMD and configured client ids are issuer-portable, so TokenStore binds the
tokens themselves to the issuer for every client-id source (Data Model §3). A registry
reconcile that changes a Server's canonical URL deletes its oauth:{id} entry.
AuthorizationDeferredError / AuthorizationAbortedError are internal (core/auth/oauth.py)
and never leave the controller: only reason codes reach ServerStatus / AuthorizationError.
```

**Flow F — MCP App render and App-originated call** (RF-APP)
```
1. Catalog invoke (human:ui) of a tool with ui_resource_uri ends; result.consent.allowed and
   outcome ∈ {success, tool_error, cancelled, timeout, connection_lost}. Else: no View.
   Agent / App invocations never render a View (API §7). The tab's previous View, if any,
   is torn down when the new invocation **starts**, whatever its outcome (reason
   "replaced"; ui.actions.invoke returns (entry, arguments, result), ui.apps_bridge.render_view
   builds the AppSeed).
2. backend: Catalog.read_resource(server_id, ui_uri, consumer=human:ui) → pick the contents
   item whose uri == ui_uri → parse MIME (text/html;profile=mcp-app) → text or UTF-8 blob,
   ≤ 5 MiB (REQ-APP-013) → csp = core.apps.build_csp(item._meta.ui.csp) (REQ-APP-014) →
   notice if external domains declared or entries dropped (REQ-APP-011). ui.actions.open_view
   returns ViewPlan(resource, csp) | NotRendered(reason); any read exception → read_failed.
   Dropped entries are logged once, by the bridge, when the View starts.
3. mcp_app_frame renders the proxy iframe: src = http://localhost:<proxy_port>/proxy?view=<view_id>,
   sandbox="allow-scripts allow-same-origin", allow="", referrerpolicy="no-referrer".
   The View's ViewSession runs inside the tab's on_load watcher (ViewRegistry.serve_tab,
   started by watch_host), not in the invoke background task: Reflex cancels only the
   on_load chain when a tab disconnects (pinned), so a View never outlives its tab.
4. proxy → parent: ui/notifications/sandbox-proxy-ready → relay (source + origin check) →
   Reflex event → AppsBridge (ui.apps_bridge over core.apps.bridge).
5. AppsBridge → outbox prop → relay → proxy: ui/notifications/sandbox-resource-ready {html,
   csp, sandbox: "allow-scripts"}; the proxy inserts the CSP <meta> first in <head> and sets
   the inner iframe's srcdoc — the View.
6. View ui/initialize → reply (hostInfo, hostCapabilities, hostContext); View
   ui/notifications/initialized → tool-input → tool-result | tool-cancelled{reason}
   (REQ-APP-003/006/009).
7. View tools/call {name} → AppsBridge builds <server_id>::<name> → Invoker.call(consumer=
   Consumer.app(server_id)) → step 1a App scope → consent + audit → CallToolResult or
   JSON-RPC error back to the View (REQ-APP-004/008; API §7 reply table). Every View RPC →
   ConnectionManager.append_log (method → outcome, REQ-APP-012).
8. Teardown (replaced / closed / server_removed / host_closing) → ui/resource-teardown
   {reason}, wait ≤ 2 s → in-flight View calls cancelled (≤ 1 s to settle) → iframe removed
   (REQ-APP-010). replaced: a new invocation starts in the tab (or overlaps); closed: the
   "Close the App" button or selecting another catalog entry; server_removed: the
   session's own registry.changed watch; host_closing: ViewRegistry.close_all before
   Host.aclose(), all Views in parallel, ≤ 2 s in total, unfinished ones discarded.
   No teardown to a View that never sent initialized. Tab disconnect, reload or
   navigation → the watcher is cancelled and the View discarded without teardown (the
   browser already destroyed it).
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
| E.4 | callback with a foreign `state` or a bad `iss` | refused with `400` before the SDK can redeem anything; the listener keeps waiting; if no authentic callback arrives, the deadline ends the flow `iss_mismatch` (our `state`, bad `iss`) or `timeout`; verifier discarded; back to `auth_required` (`state_mismatch` stays in the vocabulary but is not produced by the listener path) |
| F.7 | App asks for a foreign / app-hidden tool | Invoker step 1a → `denied` (`app_scope`), audited, no prompt → JSON-RPC `-32602`, logged (REQ-APP-012) |
| F.2 | resource missing, wrong MIME, not UTF-8, > 5 MiB, read error | no View; notice with the reason code (REQ-APP-013) |
| F.0 | `localhost` not loopback-only, remote mode, proxy cannot bind | MCP Apps disabled (not advertised, notice); Host starts normally (DD-012) |

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
  Reads (`read_resource`, `read_template`, `get_prompt`) use the same Host loop on
  `client.session.*` with `allow_input_required=True` (amended by delta
  `2026-10-phase3-impl`: one loop, one round counter and one router for every verb).
- **Consequences:** a contract test pins the `session.call_tool` signature (spike S-1);
  the session tier bypasses the SDK response cache, which is correct for `tools/call`;
  for reads it is an accepted consequence (they always reach the Server).

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
- **Process-group cleanup (REQ-CON-008):** after a stdio Client's transport has closed —
  whatever the reason, including a Server that exited promptly on stdin EOF — the supervisor
  signals the remains of the Server's process group: POSIX `os.killpg(pgid, SIGTERM)`, then
  `SIGKILL` after 2 s if any member is left (`ProcessLookupError` = nothing left, not an
  error; `pgid` is the child pid, which `start_new_session` makes the group id, recorded
  when the process is spawned). On Windows the SDK places the child in a Job Object; closing
  it terminates the remaining members. The SDK surface used to learn the pid (or to hook the
  spawn) is pinned by `tests/contract/test_mcp_sdk_surface.py`. As implemented (F8-08): the
  pid is read once, right after the spawn, from the `process` local of the suspended
  `stdio_client` generator (`transports.spawned_pid`; the SDK exposes no other pid surface,
  contract-pinned). The contract test compares that pid with the Server's own pid on POSIX
  only: on Windows a venv `python.exe` is a launcher that starts the base interpreter as a
  child, so the two differ there (`bdb303d`). Windows cleanup is the SDK's Job Object,
  created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and closed on every shutdown
  (source-pinned; verified on both `windows-latest` legs of the CI run for `bdb303d`); the
  Host does nothing more there. Logs carry the pgid only (no command line or environment):
  an already empty group at DEBUG, leftovers signalled at INFO (SIGTERM) and WARNING
  (SIGKILL). **Residual:** once the group's leader has been reaped, its pgid can in principle
  be reused by an unrelated new process group before `killpg` runs (a narrow race, accepted
  for v0.1).

### DD-003: Capabilities are fixed per `Client`; capability changes rebuild modern Clients cheaply

- **Decision:** Callbacks are registered only for enabled features
  (`elicitation_callback` iff the prompter `supports_elicitation`; `sampling_callback` iff
  a provider is set; `list_roots_callback` iff the Server's effective roots are non-empty),
  because
  in the SDK **registering a callback is declaring the capability**. When
  `set_prompter()` / sampling configuration changes, the effective roots change between
  empty and non-empty, or a modern `set_log_level` changes the Client's `log_level`, modern
  Clients are rebuilt with
  `mode="2026-07-28", prior_discover=…` (no network negotiation, effective on the next
  request); legacy Clients keep their capabilities until an explicit `reconnect()`.
- **Context:** REQ-CON-003, REQ-HSH-006/014 and API §4.1 ("modern: next request; legacy:
  after reconnect").
- **Alternatives:** always register dispatcher callbacks (declares capabilities we may not
  serve → violates REQ-HSH-006/014); auto-reconnect legacy too (surprising process
  restarts for stdio).
- **Consequences:** the rebuild is an internal reconnect without a `reconnecting` state
  (state stays `ready`; an `server.state_changed` is *not* emitted). Documented as the
  implementation of the API contract, not a contract change. A new `Client` needs a new
  transport, so a stdio rebuild starts a new Server process (a new stdio session). The
  Catalog's supervisor lease protocol exposes `inflight` (legacy attribution) and
  `rebuilding`; a connection that ends for a modern rebuild keeps its catalog entries (no
  stale marking, no `catalog.changed`). Roots-only definition changes are not reconnects
  (API §4.3): roots are read from the registry on every roots request.

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
  Write-through failure → the prompt answer still applies once (`allow_once` semantics,
  `rule_id=None`, reason recorded) and the error is surfaced, never silently treated as
  persistent. The index holds one rule per key across lifetimes: a session rule replacing
  a persistent one deletes the stored row; `update()` moves rules into or out of the DB
  when their lifetime changes. A stored row that fails to load poisons its Server (all
  Servers for a global or unreadable row) — evaluations there deny with `source="error"`.

### DD-006: SQLite via SQLModel with a sync engine behind `anyio.to_thread`

- **Decision:** one SQLite file `data_dir/mcphost.db`, WAL mode, `synchronous=NORMAL`,
  SQLModel sync engine; all DB calls run through a single-worker
  `anyio.to_thread.run_sync` limiter (serialized writes).
- **Alternatives:** SQLAlchemy async + `aiosqlite` (extra dependency; async SQLite is a
  thread under the hood anyway); JSON files (no atomic multi-record writes for audit).
- **Consequences:** Art. 8 compliant (blocking I/O only behind `to_thread`); audit writes
  are ~sub-ms, inside the 20 ms budget. Schema details in the Data Model.
- **Pooling:** the engine uses `QueuePool(pool_size=1, max_overflow=8)`, not `NullPool`:
  closing a WAL connection forces a checkpoint, which per audit write dominated the
  REQ-NFR-002 budget (p95 ~20 ms → ~6 ms). Access stays serialized by the limiter.

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
- **Secret-store failures at runtime (REQ-NFR-020, F8-03):** sign-out fails closed — the
  in-memory OAuth provider (tokens, client info, flags) is replaced **first**, then the
  stored entry is deleted; if the delete raises, one WARNING "sign-out could not delete the
  stored credentials (<type name>)" is logged and `SecretStoreError(server_id, "delete")`
  is raised `from None` (API §3.4; fixed UI notice, API §8). The next `connect` therefore
  needs authorization again even though the stored entry may still exist. (Before this fix
  a failing delete left the old provider in place and the next `connect` was `ready` with
  bearer requests — found by F8-03.) A failing `set` after a completed flow: the flow
  reports `flow_error`, the SDK keeps the tokens it obtained in memory, nothing is
  persisted, and the next `authorize()` completes without the browser for the Host's
  lifetime (accepted: tokens fall back to memory). A failing `get` while a flow starts is
  classified as `discovery_failed` (any non-SDK exception before metadata; known
  limitation). The HMAC key is read once at `Host.start()`; a keyring failing there is a
  start failure, not covered by the runtime tests.

### DD-009: OAuth loopback callback with a minimal `h11` listener

- **Decision:** `LoopbackCallbackServer` uses `anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)`
  and `h11` (a direct dependency `h11>=0.16`, also locked transitively through
  `httpx2 → httpcore2`) to serve exactly
  `GET /callback`, then closes. Redirect URI `http://127.0.0.1:{port}/callback`.
- **Alternatives:** uvicorn/Starlette in core (pulls an ASGI stack into a Reflex-free
  core); `http.server` in a thread (blocking, Art. 8).
- **Consequences:** the CIMD document (REQ-AUTH-012) lists `http://127.0.0.1/callback`
  relying on RFC 8252 §7.3 port variance; if an authorization server rejects it, DCR and
  manual `client_id` remain (spike S-4).

### DD-010: JSON Schema validation with `jsonschema` (Draft 2020-12 default), bounded

- **Decision:** validate tool arguments (REQ-INV-002), `structuredContent` vs
  `outputSchema` (REQ-INV-007) and elicitation content (REQ-HSH-013) with `jsonschema`:
  Draft 2020-12 by default; a `$schema` naming a known draft (04, 06, 07, 2019-09,
  2020-12) selects that draft. `$ref` resolution is limited to in-document refs
  (`referencing` registry with no retrieval); validators are cached (LRU 1,024).
  Elicitation `requestedSchema` is checked against the spec's restricted subset before
  rendering (REQ-HSH-012): a flat object of primitive / enum properties, including titled
  single-select via `oneOf` (`const` + `title`), legacy `enumNames`, multi-select arrays
  of enums, and top-level `title` / `description`.
- **Bounds:** ≤ 20 errors per validation, messages redacted and capped at 200 characters;
  schema depth ≤ 64 and ≤ 10,000 nodes; `pattern` ≤ 512 characters; instance depth ≤ 64.
  Server-supplied regexes (`pattern`, `patternProperties`, and the key matching of
  `additionalProperties` / `unevaluatedProperties` / `propertyNames` that depends on them)
  are compiled and matched with the `regex` package, not stdlib `re`: per-match
  `timeout=0.05`, per-`validate()` budget 0.25 s (sum of match time), compiled patterns
  cached with the validator (REQ-NFR-021). The validator classes are
  `jsonschema.validators.extend`ed with replacements for those keywords; no `jsonschema`
  internals are patched. A `TimeoutError` or an exhausted budget raises
  `SchemaCompileError("pattern evaluation timed out")`, i.e. the existing per-call mapping
  (step 3 → `protocol_error` / `invalid_input_schema`; step 7 → `protocol_error`;
  elicitation → refused as `invalid_elicitation_schema`; UI forms → the schema is shown as
  unusable). A pattern `regex` cannot compile is a compile error as before.
  As implemented (F8-10): the per-`validate()` budget lives in a `ContextVar`
  (`core.schema._BUDGET`), so it is per thread / task and validators stay immutable (they
  are rebuilt per descent); the `unevaluatedProperties` replacements for 2019-09 / 2020-12
  re-implement the evaluated-keys walk reading the validator's resolver (no patching);
  `additionalProperties` matches each `patternProperties` pattern separately; the
  meta-schema check of `format: regex` (`check_schema`) also uses `regex`
  (`_meta_format_checker`), never `re.compile`. Measured: `^(a|aa)+$` on 40 × `a` + `!`
  takes 9,225 ms on stdlib `re` (n = 32: 193 ms) and ends after ≈ 50 ms as
  `SchemaCompileError("pattern evaluation timed out")`; a hostile Server never blocked the
  event loop > 300 ms (heartbeat).
- **SDK result validation replaced:** `ClientSession.call_tool` (mcp 2.3) re-validates every
  result's `structuredContent` against the listed `outputSchema` through
  `self.validate_tool_result` — stock `jsonschema`, stdlib `re`, unbounded, inline on the
  event loop — before the Invoker's step 7 runs, so a Server's catastrophic output pattern
  could hang the Host. The supervisor replaces that method per session with a no-op
  (`_defer_result_validation`, set when the Client is entered); the Invoker's bounded step 7
  is the only output check (API §4.8). Pinned by
  `tests/contract/test_mcp_sdk_surface.py::test_call_tool_validates_results_through_overridable_method`
  (an SDK upgrade that validates elsewhere fails the build).
- **Consequences:** no network fetches from Server-controlled schemas (Art. 5); schema
  compile errors on a tool → the tool stays listed but invocation returns
  `protocol_error` / `invalid_input_schema` with the compile detail. Unresolvable or
  remote `$ref` and `$ref` loops surface as `SchemaCompileError` at validation time
  (resolution is lazy); compile failures are cached like successes.
- **Known limits (v0.1):** patterns use `regex` (Python-flavoured) syntax, not ECMA-262,
  as with `re` before; the time bound is per call, so a Server can still make each
  validation of its own tools cost up to 250 ms. `format` is not enforced (annotation only,
  `jsonschema` default). Alternatives rejected for the bound (delta
  `2026-11-phase8-prep` D1): a syntactic pattern restriction (polynomial backtracking
  survives it), `re` in a worker thread (`re` holds the GIL, threads cannot be cancelled),
  a subprocess pool (spawn cost), not enforcing `pattern` (changes `patternProperties`
  semantics).

### DD-011: Reflex integration — Host in a lifespan task, per-tab watcher state, prompt registry

- **Decision:** `ui.install(app, host_factory)` registers, with
  `app.register_lifespan_task`, an **async context manager** (`contextlib.asynccontextmanager`)
  that builds the Host with `host_factory()`, binds the process-wide `ReflexPrompter` with
  `Host.set_prompter` (replacing any prompter the factory set), enters `async with host`
  before Reflex starts serving, publishes it in the module-level `HostRuntime`, and exits it
  after Reflex stops serving. Reflex enters and exits such lifespan tasks on its
  `AsyncExitStack`, in one task and without the 5 s cancel cap it applies to coroutine
  lifespan tasks (`_LIFESPAN_TASK_CANCEL_TIMEOUT`, Reflex 0.10.1), so `start()` and
  `aclose()` run in the same task (API §4.1) and `aclose()` keeps its 15 s budget
  (REQ-CON-009). One Host per backend process; `install()` is idempotent per `rx.App` and
  raises `RuntimeError` on a second call with a different factory. Each tab's `HostState` starts an
  `@rx.event(background=True)` watcher from the page **`on_load`** (on every page that
  needs it) that subscribes to `EventBus` and applies events inside `async with self`;
  watchers are de-duplicated per client token in a process-local registry, not in Reflex
  state (an `on_mount` chain is not cancelled on disconnect — spike S-5). `ReflexPrompter` resolves prompts through a
  process-wide `PromptRegistry` (Flow D). Snapshots (statuses, catalog) are recomputed
  from core on each relevant event, never mutated incrementally in UI state; the Permissions
  view re-queries the rules on `consent.rules_changed` (API §4.11).
- **Alternatives:** Host per tab (N copies of every stdio process — wrong); `app.modify_state`
  push from core (couples core to Reflex tokens, Art. 4).
- **Consequences:** core stays Reflex-free; multiple tabs see the same Host; first answer
  wins. The `PromptRegistry` (Flow D) is UI-layer state (`ui/prompter.py`), never core; it
  holds request payloads in memory only and never persists or logs them (argument previews
  are already redacted by core, Art. 5). Background watcher tasks started from `on_load` end
  on tab disconnect (Reflex cancels them) and restart on reconnect/reload. R-5 retired for
  the single-worker case (S-5). A `Host.start()` failure propagates out of the lifespan
  start-up, so the backend exits non-zero (CLI exit `3`, DD-016); `HostRuntime.host` raises
  `RuntimeError` before start-up and after shutdown. A contract test pins that Reflex
  awaits an async-context-manager lifespan task's exit to completion even past 5 s (S-5
  assertion #1, extended). Reflex state vars hold only JSON-serializable view models
  (`ui/models.py`), never core objects. List views (Catalog, Servers, Permissions, Audit)
  filter in the backend over the cached view model and render at most 200 rows plus a
  "N more — refine the filter" line; the Catalog, Servers and Permissions filter inputs
  apply per keystroke with a 50 ms debounce (`FILTER_DEBOUNCE_MS`,
  `ui/components/filters.py` — Reflex's 300 ms default for a controlled `rx.input` alone
  exceeds the REQ-NFR-004 budget), the Audit text filters apply on blur; snapshots are
  recomputed per affected Server on `server.state_changed` / `catalog.changed`, not for the
  whole Host (REQ-NFR-004, measured in Plan F6-09b, §8).
- **UI implementation rules (Phase 6):**
  - `HostRuntime` is a module-level singleton with `phase` `starting` / `running` /
    `stopped` (it drives the page banner); entering a second Host lifespan in one process
    while one runs raises `RuntimeError`.
  - The UI's actions are plain async functions over public core methods in `ui/actions.py`
    (Reflex-free by design); `HostState` handlers are thin wrappers (no `state ↔
    components` cycle). The watcher's `HostView` / `watch_host` take an optional
    `on_finished` callback (the `Sink` protocol has `invocation_finished`), used to refresh
    the Audit view after an invocation.
  - A computed var that picks a state field dynamically (`getattr`) declares its `deps`
    explicitly (`@rx.var(deps=…)`): Reflex's dependency tracking does not see `getattr`
    (the Catalog list never rendered without it).
  - A form rendered inside `rx.foreach` never takes the loop variable in `on_submit`: card
    forms carry a hidden input `CARD_FIELD = "__card_id__"` and submit to form-only handlers
    that read the card id from the form data (`schema_form(hidden=…)`).
  - View models (`ui/models.py`): stdio Servers show the command, never its arguments;
    HTTP Servers show `scheme://host[:port]`, never path, query or userinfo; env / header
    keys only; config-error rows carry their problem messages. Consent cards of an
    `unattributed` request show the Consumer as `unattributed` and offer only `allow_once`
    / `deny` (deny lifetime `once`, one target); sampling consent cards offer targets
    `server` / `global` only; the sampling Edit action approves once with
    `edited_sampling` (text messages only). Every card option is checked before core sees
    it. `schema_form` arrays are one value per line, with an "Edit as JSON" alternative.
    Server-provided media is shown only as `data:` URLs of PNG / JPEG / GIF / WebP (audio:
    its allowed types) up to 8 MiB of base64; anything else (SVG included) is a
    placeholder naming the type. Audit rows show `args_hash`.
- **MCP App Views (Phase 7):** a tab's View runs in its `on_load` watcher (Flow F step 3),
  served from the process-local `ui.apps_bridge.VIEWS` registry (never in Reflex state;
  `app_view` / `app_outbox` are the only state fields); selecting another catalog entry
  closes it (`closed`), starting an invocation replaces it (`replaced`). Tab state stays in
  memory through Reflex's MEMORY state manager (DD-016, Data Model §8).

### DD-012: MCP Apps — Python bridge, thin JS relay, sandbox proxy on `localhost:<proxy_port>`

- **Decision:**
  - The JSON-RPC bridge state machine (handshake ordering, method table, own-server and
    visibility checks, teardown) is pure Python in `core.apps.bridge` (transport-agnostic,
    unit-testable). `ui.apps_bridge` adapts it to Reflex events.
  - A ~100-line JS relay (`ui/assets/mcp_app_relay.js`, wrapped as a Reflex custom
    component) only moves messages between the proxy iframe and the backend and filters by
    `event.source === proxyWindow`.
  - The relay is the only code on the Host UI's origin that touches MCP App messages, and
    it handles them as **data**. Backend → relay: the `mcp_app_frame` component receives an
    `outbox` prop (a state var: list of `{seq, message}`; `message` a JSON string) and posts
    each new entry to the proxy window with target origin `http://localhost:<proxy_port>`,
    then acknowledges `seq` with a Reflex event; acknowledged entries are removed. The
    backend never builds script source from Server-provided content (no `rx.call_script`,
    `rx.script` or `eval` with message data), the resource HTML is never inserted into the
    UI DOM (it travels as a string inside `sandbox-resource-ready`), and no state var holding
    Server content is bound to `dangerouslySetInnerHTML`, `src` or `href` on the UI origin.
    Relay → backend: one Reflex event `(view_id, message JSON string)` per message accepted
    under `event.source === proxyWindow && event.origin === "http://localhost:<proxy_port>"`;
    the relay drops messages whose serialization exceeds 1 MiB; shape validation, limits and
    the method table are in Python (API §7).
  - The sandbox proxy is a minimal HTTP/1.1 listener (`h11` over `anyio`, the approach of
    DD-009 — no ASGI server: `uvicorn` is only a test dependency and Reflex's `granian`
    serves the backend as a process), owned by the Host lifespan of `ui.install`: started
    before `Host.start()`, stopped after `aclose()`. It binds one ephemeral port on
    `127.0.0.1` and on `::1` (`IPV6_V6ONLY`; IPv4 port 0 first, then IPv6 on that port; up to
    5 attempts on `EADDRINUSE`) and is addressed as `http://localhost:<proxy_port>` while the
    Host UI is addressed as `http://127.0.0.1:<port>` — different **host** and **port**, so
    the proxy's `allow-same-origin` cannot reach the UI's origin, storage or cookies (cookies
    ignore ports; they do not ignore host names). It serves only `GET /proxy` (query `view`
    = the View id, `[A-Za-z0-9_-]{1,64}`, correlation only, not a secret) with `200
    text/html; charset=utf-8`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
    `Referrer-Policy: no-referrer`, no `Set-Cookie` and no CSP header (S-3). `Host` must be
    exactly `localhost:<proxy_port>` (else `403`; an HTTP/1.1 request with a missing or
    repeated `Host` is already refused by h11 with `400`); other paths `404`, other methods
    `405`; request head ≤ 8 KiB, no body, `Connection: close`, 10 s read deadline. The full
    check order and status codes are in API §7 "Sandbox proxy". A machine without an IPv6
    loopback (`EADDRNOTAVAIL` / `EAFNOSUPPORT` / `EPROTONOSUPPORT` on `::1`) gets the IPv4
    socket only, logged at INFO — MCP Apps stay enabled (no browser there reaches `::1`
    either); any other bind `OSError` disables them (`proxy_bind_failed`). The page embeds
    the pinned UI origin server-side (the guard's resolved origin, JSON-encoded), never from
    the query or `document.referrer`.
  - The proxy HTML creates the View iframe with `srcdoc` and a CSP `<meta>` built by
    `core.apps.build_csp` (validated entries only, pinned policy — API §7, REQ-APP-014),
    `sandbox="allow-scripts"` on the inner frame, empty `allow`.
  - The App scope (own Server, `app` visibility) is enforced by the Invoker (API §4.8 step
    1a), so a rejected App call is audited like any other attempt (Art. 3); the bridge only
    builds the FQN from the View's own Server and maps the result (API §7).
  - Glossary: **App** — a Server's UI resource (`ui://…`, MIME `text/html;profile=mcp-app`);
    **View** — one rendered instance of an App in the sandbox (one per tab in v0.1); the
    Consumer **`app:<server_id>`** names the Server whose View asks, not the App.
  - **Implementation rules (Phase 7):**
    - `core.apps` = `constants.py`, `csp.py`, `resource.py` (`select_view_html`),
      `messages.py` (JSON-RPC shape), `bridge.py`; its `__init__` re-exports nothing
      (`core.invoker` imports `core.apps.constants`, `core.apps.bridge` imports
      `core.invoker`). `AppBridge(*, server_id, view_id, seed, html, host_info,
      host_context, csp, ports, teardown_timeout_s=2.0)` raises `ValueError` for a seed whose
      outcome does not render. `AppPorts` = `invoke_tool` (not `call_tool`: the Art. 3 AST
      test reserves that name), `read_resource`, `confirm_link`, `log`, `send` and the
      optional `size_changed`.
    - `proxy.html`: `<meta name="referrer" content="no-referrer">`; the pinned UI origin is
      checked as `event.source === window.parent` **and** `event.origin`, and every post
      to the parent targets it — those are the guards. A `location.ancestorOrigins[0]`
      check stops the page under a foreign ancestor as **defense in depth only**: Firefox
      masks it as `"null"` for an iframe with `referrerpolicy="no-referrer"` (the relay's),
      so `"null"` counts as unknown (a strict check rendered no View in Firefox, F7-06b).
      The HTML is parsed with `DOMParser`, only the first `sandbox-resource-ready` is
      honoured, the inner frame's `sandbox` / `allow` / `referrerpolicy` are pinned on the
      page, and `sandbox-*` notifications never reach the View.
    - `check_origin_pair` treats any resolver error for one family as that family being
      absent; at least one family must resolve, all to loopback.
    - The relay asset is linked by `ui.assets.relay_library()` through Reflex's shared-asset
      mechanism (`rx.asset(..., shared=True)`) only inside a Reflex app directory
      (`rxconfig.py` in the working directory); elsewhere it returns the module path
      without file-system writes. Called when the component is built, never at import.
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
  Chromium, Firefox and WebKit (S-3; real Safari unverified — Plan R-8). Before
  `Host.start()` the lifespan checks the origin pair: `localhost` resolved with `AF_INET`
  and with `AF_INET6` returns only loopback addresses for every family that resolves
  (`AF_UNSPEC` alone can hide `::1`), the UI origin's host is `127.0.0.1` (not remote mode),
  and the proxy listener is bound. If any check fails, MCP Apps are disabled: the extension
  is not advertised (`Host.set_mcp_apps(False)`), `HostRuntime.apps` records the reason
  (`localhost_not_loopback`, `remote_mode`, `proxy_bind_failed`), `mcp_app_frame` shows "MCP
  Apps are disabled: <reason>" and a WARNING is logged; the Host and the rest of the UI start
  normally. `CapabilityPlan` gains `mcp_apps: bool`; when set, the Client's `extensions`
  carry `mcp.client.advertise("io.modelcontextprotocol/ui", {"mimeTypes":
  ["text/html;profile=mcp-app"]})` (modern: per request; legacy: in `initialize`; pinned by
  the SDK contract test). All View state (arguments, result, HTML, outbox) is per tab and in
  memory only (Data Model §8). The constants
  `io.modelcontextprotocol/ui` and `text/html;profile=mcp-app` are defined in
  `core.apps` — **not** imported from `mcp.server.apps` (Art. 1 forbids `mcp.server`).
  The client-side advertisement uses `mcp.client.advertise(EXTENSION_ID, {...})`.
  The proxy serves at most 32 connections at a time (`SandboxProxy.MAX_CONNECTIONS`,
  counted from accept to close; over the cap `503` + `Retry-After: 1` and close without
  reading — a client that already sent bytes may see a reset instead; 1,000 refusals leak
  no task or descriptor). A View that navigates its own frame is detected by `proxy.html`
  (it counts inner `load` events; on the second it posts `{"type": "view-navigated"}` once
  and relays nothing more, the relay JS is unchanged) and discarded without teardown
  (`AppBridge.discard("navigated")`, `DISCARD_REASONS`; `ui.apps_bridge` removes the View and
  shows `NAVIGATED_NOTICE`; API §7 Navigation). Only that exact message counts, forgeries from
  a sibling frame are ignored, and a View that posts it itself only closes itself
  (harmless). Data carried by that first navigation is a residual risk (§6.1).

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
  - `playwright` enters `pyproject.toml`/`uv.lock` with the first E2E task (Plan F6-09b);
    E2E tests carry the `e2e` marker, are deselected in the matrix `test` job and run in a
    dedicated `e2e` CI job on `ubuntu-latest` only (`uv run playwright install --with-deps
    chromium`; Firefox and WebKit join in F7-06b). Headless smoke tests (Plan F6-09a) need no
    browser. The E2E tests are Linux-only (they read `/proc` for the process tree and
    listening sockets) and skip elsewhere. They run the built wheel with `uv tool run
    --from <wheel> --compile-bytecode reflex-mcphost …` (= `uvx`), reusing the developer's
    `UV_CACHE_DIR` with a temporary `XDG_CACHE_HOME` (the DD-016 workspace lives there).
  - Everyday commands: `uv sync --all-extras --all-groups`, `uv run pytest`,
    `uv run ruff check`, `uv run mypy src`, `uv run lint-imports`.
  - Legacy-era interop fixtures run a pre-2026 SDK in isolation with
    `uv run --isolated --with "mcp>=1.30,<2" python tests/fixtures/servers/legacy_server.py`
    (no second venv to maintain).
  - Release: `uv build` (sdist + wheel) → wheel and sdist checks (no `tests/`, no
    `mcp.server` imports — Art. 1) → `uv publish --dry-run` → upload with `uv publish`
    via PyPI Trusted Publishing from GitHub Actions, only from a `v*` tag (§9.1). End users
    run `uvx reflex-mcphost` (console script from `0.1.0b1` on; §9.1).
  - CI: `astral-sh/setup-uv` with cache; matrix Linux/macOS/Windows × Python 3.11/3.13.
  - Supply chain: every third-party action in every workflow is referenced by a full commit
    SHA (release tag as a trailing comment), kept current by Dependabot
    (`.github/dependabot.yml`, `github-actions`, weekly, one grouped PR); the `publish` job
    pins the `uv` version that performs the upload (§9.1).
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
- **CLI ↔ app contract:** `run` passes its options to the Reflex child only through
  environment variables — `REFLEX_MCPHOST_HOST`, `REFLEX_MCPHOST_PORT`,
  `REFLEX_MCPHOST_CONFIG` (absolute path; unset without `--config`),
  `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE` (`1` or unset), `REFLEX_MCPHOST_LOG_JSON` (`1` or
  unset); no secret is passed. The packaged `rxconfig.py` builds `api_url`, `deploy_url`
  and `cors_allowed_origins` from host and port (DD-017); the packaged app module loads
  `HostConfig.load(path)` once at import time (before the event loop serves) when a config
  path is set and its `host_factory` returns `Host(<that config> or None)`. Pre-flight in
  the CLI process, before the workspace is materialized: argument rules (exit 2),
  `HostConfig.aload(--config)` (explicit invalid file → exit 3), opening + closing the
  database in the configured data dir (failure → exit 3). Readiness: the CLI polls
  `GET /ping` with the allowed `Host` and opens the browser only after it answers (no
  deadline while the child is alive — a first run compiles the frontend); it always prints
  the URL. A child exiting non-zero before readiness → exit 3; afterwards the child's code,
  or 0 after a forwarded Ctrl-C / SIGTERM (a child exiting 0 before readiness → 0). The
  workspace is written to a temporary sibling directory, completed with a marker file
  (`.reflex-mcphost-workspace`) and renamed into place, so concurrent `run`s never see a
  half-written one; an existing directory without the marker is refused (exit 3). On every
  `run` the workspace step (`app.workspace.materialize` → `prune`; the CLI module is
  unchanged) touches the current workspace's marker (its mtime = last use) and then, best
  effort, removes sibling directories of `…/app/` that **carry the marker** and were last
  used ≥ 7 days ago, and leftover `.<version>-<hash>.<random>.tmp` directories older than 1
  day; directories without the marker, symlinks and files are never touched; a stale
  workspace is first renamed to a leftover name and then deleted, so a failure half-way
  never leaves a marker-less directory under a workspace name (it is retried as a leftover
  later); any error is logged at DEBUG and ignored (Windows: files in use). **Residual:** the
  marker is touched when a `run` starts, so a `run` that has been running for more than 7
  days can have its workspace pruned by another `run`.
- **Child environment:** the inherited environment minus every `REFLEX_MCPHOST_*` variable
  (an inherited `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE` never reaches a run without the flag)
  and minus `REFLEX_FRONTEND_ONLY` / `REFLEX_BACKEND_ONLY` (they would drop half of the
  single-port app), plus `GRANIAN_WORKERS=1`, `REFLEX_CHECK_LATEST_VERSION=false` and the
  `REFLEX_MCPHOST_*` set above.
- **Process group and signals:** the child runs in its own session / process group (POSIX
  `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`), so a terminal Ctrl-C reaches
  only the CLI, which forwards it exactly once (`SIGINT`; Windows `CTRL_BREAK_EVENT`) — a
  second `SIGINT` would cut the Host's shutdown short. SIGTERM and (POSIX) SIGHUP are
  forwarded as SIGTERM (Windows `CTRL_BREAK_EVENT`). The CLI waits 30 s (Host `aclose`
  budget 15 s plus Reflex), then kills the child; a second Ctrl-C during the wait is
  swallowed. The Windows path is covered by a Windows-only test in the CI matrix (Plan
  F8-14, `tests/integration/app/test_cli_windows.py`); where the runner offers no console,
  the test is skipped with that reason. It passed (not skipped) on both `windows-latest`
  legs of the CI run for `bdb303d`.
- **Packaged app:** `app/web/` holds `rxconfig.py` and `mcphost_app/` (copied into the
  workspace) and an `__init__.py` that makes it a regular package for mypy and
  import-linter (not copied). `rxconfig.py` also sets `backend_host`, `frontend_port` =
  `backend_port`, enables `RadixThemesPlugin` explicitly, disables `SitemapPlugin` and sets
  `state_manager_mode=StateManagerMode.MEMORY`: Reflex 0.10.1's default DISK manager would
  write tab state (the invocation panel's arguments and results, the MCP App outbox with
  the resource HTML) under `.states/` (Data Model §8); memory mode is safe because the app
  runs one worker (`GRANIAN_WORKERS=1`). Pinned by
  `tests/unit/app/test_reference_app.py::test_rxconfig_keeps_tab_state_in_memory`; an
  embedding app must do the same — the lifespan refuses to start otherwise (start-up
  `RuntimeError`, API §8). The
  app module builds `app` lazily (PEP 562 `__getattr__`, triggered by Reflex while loading
  the app; importing the module has no side effect): console logging (§7.1), the config,
  `install`, and the pages of the `PAGES` registry (`/` Servers, `/catalog`, `/permissions`,
  `/audit`), each resolving its view component by name on `reflex_mcphost.ui` (a missing
  one renders a "not available in this build" callout) with the nav, the Host banner,
  `consent_center` and `on_load=HostState.start_watch`.
- **Alternatives:** run from site-packages (read-only under `uvx`, pollutes the install);
  pre-built static export (loses backend features).
- **Consequences:** first run compiles the frontend (Reflex requires Node/Bun, which
  Reflex installs itself); REQ-NFR-003's 10 s target applies to warm runs (2.6–5.7 s on Linux in S-5); every
  warm start includes a Vite production build, so it grows with UI size — re-measured in
  Phase 6.

### DD-017: Loopback guard for the reference app

- **Decision:** a pure-ASGI middleware (`LoopbackGuard`) appended last to the app's
  `api_transformer` (outermost, after any transformer the embedding app set)
    rejects HTTP, websocket and static-UI requests whose `Host` is not `127.0.0.1:<port>` or
  whose `Origin` (when present) is not `http://127.0.0.1:<port>` (single port, DD-016) → `403`
  (REQ-NFR-011). The single allowed origin is read from the Reflex config's `deploy_url`
  (scheme `http`, host, port) at lifespan start-up; the packaged `rxconfig.py` sets
  `deploy_url = api_url = http://<host>:<port>` from the CLI's environment (DD-016). A
  missing `deploy_url`, a scheme other than `http`, a missing port, an empty host, a
  wildcard host, `localhost` / `*.localhost` → start-up `RuntimeError`. Until the origin is
  resolved the guard fails closed: HTTP → `503` "starting", websockets closed. A request
  with more than one `Host` or `Origin` header is refused; a refused websocket is closed
  with code `1008` before accept.
  `install()` refuses to start (start-up `RuntimeError`, CLI exit `3`) when that host is not
  `127.0.0.1` unless `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1` (set by the CLI only for
  `--unsafe-allow-remote` with a concrete `--host`; wildcard hosts are refused with exit 2);
  `localhost` is always refused. In remote mode the one allowed origin is
  `http://<host>:<port>`. An embedding app (REQ-UI-007) sets its own `deploy_url` to the
  `127.0.0.1` origin its users load. The 403 body names the allowed origin. `localhost` is deliberately **not** accepted for the UI: the name is
  reserved for the MCP Apps sandbox proxy (DD-012), and a UI loaded via `localhost` cannot
  reach the backend. The guard also refuses the UI page itself, so the notice "open
  http://127.0.0.1:<port>" is the `403` response body (S-5); `cors_allowed_origins` is the
  same single origin (defence in depth); the CLI always opens the `127.0.0.1` URL. Binding to non-loopback requires `--unsafe-allow-remote`
  (REQ-UI-002). An E2E test opens the UI via `localhost` and asserts a `403` with the notice text
  and that the proxy cannot read UI cookies (F7-06b).
  Every HTTP response that passes the guard (static UI and backend) gets
  `Referrer-Policy: no-referrer` unless the response already sets one; refused responses
  (`403`, `503`) carry it too, so every navigation the UI starts — `ui/open-link`, url-mode
  elicitation, authorization URLs — sends no `Referer`, whatever window features Reflex's
  external redirect uses. (Observed in the E2E: Chromium and Firefox omit the header;
  WebKit sends an empty `Referer:` — no value — which the test accepts on WebKit only; a
  non-empty value fails everywhere.) At the same start-up point where `deploy_url` is read, a Reflex
  `state_manager_mode` other than `MEMORY` is a start-up `RuntimeError` (API §8, DD-016);
  the mode checked is the one Reflex's `StateManager.create` would pick (an unset mode with
  a Redis URL configured is `REDIS`).
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
  5. `forbidden`: `reflex_mcphost.adapters` ↛ `reflex_mcphost.core` (direct imports;
     adapters reach core only through `reflex_mcphost.toolset`). The Toolset implementation
     lives in `core.toolset` so that `Host.toolset()` (in `core.host`) needs no upward
     import — import-linter rejects even a function-level `core → toolset` import.
  6. `forbidden`: `reflex_mcphost.ui.models`, `reflex_mcphost.ui.actions` ↛ `reflex`,
     `reflex_base` (indirect chains included) — "UI view models and actions are Reflex-free
     (F6-02, F8-04, DD-018 contract 6)": `ui.actions` was added to this contract's sources
     (F8-04) rather than as a new contract, so `lint-imports` reports 7 contracts kept; a
     negative test (`test_ui_actions_importing_reflex_breaks_the_build`) fails without the
     widening. `starlette` cannot be forbidden there: it is reached through the `mcp` SDK.
     `ui.install` and `ui.prompter` are Reflex-free too (`ui/__init__` resolves components
     and `HostState` lazily, so importing `reflex_mcphost.ui` imports no Reflex).
  7. `forbidden`: `reflex_mcphost.ui.proxy` ↛ `reflex`, `reflex_base` (the sandbox proxy is
     `h11` + `anyio` only, F7-03).
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
│   ├── connection/             # manager.py, supervisor.py, transports.py, era_cache.py, stderr.py, lease.py, inflight.py
│   ├── auth/                   # oauth.py, token_store.py, callback_server.py
│   ├── catalog.py
│   ├── outcomes.py             # InvocationOutcome (shared by audit and invoker)
│   ├── consent/                # outcome.py (ConsentOutcome, OperationKind, ConsentSource), models.py, policy.py, index.py, store.py, preview.py
│   ├── invoker.py
│   ├── mrtr.py                 # tools/call loop + InputRequestRouter
│   ├── handlers/               # elicitation.py, sampling.py, roots.py, logging.py (deprecated ones flagged)
│   ├── audit.py
│   ├── db.py                   # engine, WAL pragmas, thread limiter
│   ├── events.py
│   ├── schema.py               # jsonschema validators + elicitation subset check
│   ├── toolset/                # aliases.py, filters.py, toolset.py (Toolset, ToolSpec, ToolCallOutcome)
│   └── apps/                   # constants.py, csp.py, resource.py, messages.py, bridge.py (pure JSON-RPC state machine; __init__ re-exports nothing)
├── toolset/                    # __init__.py: public re-export of core.toolset (API §4.12)
├── adapters/                   # pydantic_ai.py, langgraph.py (optional extras; import reflex_mcphost.toolset only)
├── ui/
│   ├── install.py              # lifespan task, HostRuntime, guard middleware
│   ├── models.py               # JSON-serializable view models from core snapshots (no reflex import)
│   ├── state.py                # HostState + background watcher
│   ├── prompter.py             # ReflexPrompter, PromptRegistry
│   ├── actions.py              # UI actions as plain async functions over public core methods (Reflex-free)
│   ├── components/             # servers_panel.py, catalog_browser.py, consent_center.py, permissions_table.py, audit_table.py, mcp_app_frame.py, schema_form.py, filters.py (FILTER_DEBOUNCE_MS)
│   ├── apps_bridge.py
│   ├── proxy/                  # server.py (h11 listener, origin-pair check), proxy.html
│   └── assets/                 # mcp_app_relay.js, __init__.py (relay_library)
└── app/
    ├── cli.py                  # argparse: run | servers | --version
    ├── workspace.py            # DD-016
    └── web/                    # rxconfig.py, mcphost_app/mcphost_app.py (__init__.py not copied to the workspace)
tests/
├── unit/                       # pure core logic, no I/O
├── integration/                # in-process + stdio + HTTP fixture Servers
├── interop/                    # era matrix, OAuth, x-mcp-header, MRTR
├── e2e/                        # headless smoke + Playwright against the reference app (marker `e2e`)
├── fixtures/servers/           # modern_server.py, legacy_server.py, apps_server.py, auth_server.py (tests only — Art. 1)
└── fixtures/apps/              # vendored ext-apps example View (pinned sha256, `-text` in .gitattributes) + hand-written probe View + NOTICE.md (tests only)
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
class InputRequestError(MCPHostError): ...      # catalog reads (REQ-HSH-046)
class ConsentStoreError(MCPHostError): ...      # consent persistence failure
class UnknownRuleError(MCPHostError): ...       # update/revoke of an unknown rule id
class AuditUnavailableError(MCPHostError): ...  # audit write failure → audit_unavailable
class AuditRecordNotFoundError(MCPHostError, LookupError): ...
class SchemaCompileError(MCPHostError): ...     # → protocol_error / invalid_input_schema
# internal, never escape the Invoker:
class _SamplingDenied(Exception): ...           # → outcome denied / sampling_denied
class _InputRoundsExceeded(Exception): ...      # → protocol_error / input_rounds_exceeded
# internal to core/auth/oauth.py, never leave the OAuth controller (reason codes only):
class AuthorizationDeferredError(MCPHostError): ...  # parked redirect_handler
class AuthorizationAbortedError(MCPHostError): ...   # Host abort before redemption (reason)
```

Phase 4 adds no exception to `core/errors.py` (public surface unchanged).

SDK exception mapping (single function `map_sdk_error`, unit-tested):

| SDK | Invoker outcome | Catalog/Prompt read |
|---|---|---|
| `CallToolResult.is_error` | `tool_error` | — |
| `MCPError` (JSON-RPC error) | `protocol_error` | `ServerRequestError` |
| `MCPError` with code `-32022` | n/a (connect-time) | — |
| `MCPError` with code `-32000` (SDK `CONNECTION_CLOSED`) | `connection_lost` | `ServerRequestError` |
| transport exceptions (`httpx2.TransportError`, `anyio.BrokenResourceError`, `EndOfStream`) | `connection_lost` | re-issue once, then `ServerUnavailableError` |
| `TimeoutError` from our deadline | `timeout` | `TimeoutError` |
| `OAuthFlowError` | n/a | `AuthorizationError` |
| `RuntimeError` from `session.call_tool` (spike S-1) | `protocol_error` | — |
| `MCPError` `-32020` HEADER_MISMATCH (modern) | per leg: one re-list + resend, then `protocol_error` | one re-list + resend, then `ServerRequestError` |
| auto-loop `InputRequiredRoundsExceededError` | n/a | `InputRequestError(input_rounds_exceeded)` |

Never `except Exception: pass`; every swallowed exception is logged with server_id /
invocation_id (Art. 8, silent-failure rule).

## 6. Security

### 6.1 Attack surface

| Vector | Mitigation | REQ / Art. |
|---|---|---|
| Malicious project config spawning commands or exfiltrating env secrets through a remote URL/headers | Trust prompt before any process or request, per HMAC fingerprint — stdio: command, args, cwd, origin, env names **and values**, risky env names flagged; HTTP: URL, origin, header names **and values** | REQ-REG-011 |
| Project `mcphost.toml` changing host settings or loading a sampling provider | Project file limited to `[servers.*]` | REQ-REG-014 |
| Tool definition "rug pull" after trust | `definition_hash` on rules; suspend + re-ask; the Invoker re-checks the hash after consent and denies a changed tool (API §4.8 step 4a) | REQ-CNS-007, REQ-INV-016 |
| Prompt-injection via tool descriptions/annotations | Annotations display-only; descriptions shown verbatim in consent UI, never auto-approved | REQ-CAT-011 |
| Agent self-escalation (agent approves itself) | Consent scoped to requesting Consumer; only `human:*` prompters answer | REQ-CNS-015 |
| MCP App escaping to Host origin | Sandbox proxy on distinct host+port; inner `allow-scripts` only; CSP from declared domains; no permissions | REQ-APP-002/011, Art. 5 |
| App calling other Servers' / hidden tools | FQN built from the View's own Server; App scope enforced in the Invoker (API §4.8 step 1a, audited); consent + audit for every App call | REQ-APP-004/008, Art. 3 |
| Forged postMessage | Relay accepts only `event.source === proxyWindow && event.origin === <proxy origin>`; the proxy accepts only `window.parent` + pinned UI origin and `inner.contentWindow`; JSON-RPC shape validated in Python. Tested in Chromium, Firefox and WebKit (sibling iframe on the proxy origin, popup via `window.opener`, View posting to `top`, forged `sandbox-proxy-ready`), with Invoker calls counted as `audit_record` rows; a mutation run (relay `event.source` check removed) makes the test fail | REQ-APP-003 |
| Server content executing on the Host UI origin through the relay | Data-only outbox; no script built from Server content; HTML never in the UI DOM (DD-012) | REQ-APP-002, Art. 5 |
| CSP injection through declared domains (`*`, `https:`, keywords, `;`, loopback origins) | `build_csp` host-source grammar; invalid entries dropped, logged, shown in the notice; never `'unsafe-eval'` | REQ-APP-014, Art. 5 |
| View flooding consent prompts or the websocket | ≤ 4 in-flight View requests; messages ≤ 1 MiB; resources ≤ 5 MiB | REQ-APP-005/013 |
| View opening `javascript:`, `data:`, `file:` or host-less URLs via `ui/open-link` | `is_openable_link` / `is_openable_url` (≤ 2,048 characters, no whitespace or control characters) + human confirmation showing the full URL; one pending confirmation per View; the tab opens with `noopener` | REQ-APP-015 |
| Referrer leak when the UI opens a Server-provided URL (`ui/open-link`, url-mode elicitation, authorization URL) | **Mitigated:** `Referrer-Policy: no-referrer` on every UI response (DD-017), whatever window features Reflex's external redirect (`window.open(url, "_blank", "noopener")`) uses; E2E asserts in Chromium, Firefox and WebKit that the opened page sees no `Referer` value (WebKit sends an empty header, accepted only as `""`) and an empty `document.referrer`; a mutation run (guard header removed) fails it | REQ-APP-015, REQ-HSH-015, REQ-AUTH-008, Art. 5 |
| DNS rebinding / foreign requests to the sandbox proxy | Proxy checks `Host == localhost:<proxy_port>`, serves only `GET /proxy?view=<id>` (sole parameter), sets no cookie, embeds the pinned UI origin server-side; head ≤ 8 KiB, 10 s read deadline, `Connection: close`, no request logging; at most 32 concurrent connections (over the cap `503` and close) | REQ-APP-002 |
| Tab state written to disk by Reflex's state manager (Reflex 0.10.1 defaults to DISK: invocation arguments / results and the MCP App outbox with resource HTML under `.states/`) | **Mitigated:** the packaged `rxconfig.py` sets the MEMORY state manager (one worker, DD-016), pinned by a test; embedding apps must do the same — enforced at lifespan start-up (start-up `RuntimeError`, API §8; the mode is inferred as Reflex does, so an unset mode with a Redis URL is refused too) | Art. 5, Data Model §8 |
| MCP App View navigating its own frame to send data out | Not blocked by CSP in any engine (S-3). **Detected and discarded:** a second inner `load` makes the proxy post `view-navigated` once and relay nothing more; the View is discarded without teardown (in-flight calls cancelled, notice; API §7 Navigation); a forged `view-navigated` from a sibling frame is ignored, a View sending it itself only closes itself. **Residual:** data carried by that first navigation is not prevented; Views only come from Servers the user connected | Art. 5, REQ-APP-010 |
| DNS rebinding / CSRF on local backend | Loopback bind; Host/Origin middleware accepting only the single configured origin — default `127.0.0.1:<port>`; in opt-in remote mode the concrete `<host>:<port>`, never a wildcard (HTTP, websocket, static UI); repeated `Host` / `Origin` headers refused; `503` until the origin is resolved | REQ-UI-002, REQ-NFR-011 |
| Server-provided URLs opened by the UI (`javascript:`, `data:`, `file:` or host-less URL in a url-mode elicitation or an authorization URL) | Defense in depth after core's checks: the UI opens a URL (external redirect) only if `ui.models.is_openable_url` holds (`http` / `https` with a host name). Otherwise it is never rendered as a link or echoed: accepting such an elicitation is sent as `decline` with a fixed notice; a refused authorization URL gets a fixed notice and no redirect (the UI cancels the core flow: `cancelled`, listener released). Server media only as allow-listed `data:` image / audio types (no SVG) | REQ-HSH-015, REQ-AUTH-008, Art. 5 |
| OAuth code interception / mix-up / login CSRF; credentials still usable after a sign-out whose secret-store delete failed | PKCE S256, `state`, `iss` (RFC 9207), loopback-only redirect; the listener completes only on an authentic callback (forged ones get `400`, the flow keeps waiting); credentials bound to the Server's canonical resource (RFC 8707) and to the issuer for every client-id source; no bearer token before discovery and the issuer check ; sign-out forgets the in-memory credentials **before** deleting the stored entry, and a failing delete raises `SecretStoreError` (fixed notice, no backend text), so the next connect needs authorization again (DD-008; found and fixed in F8-03) | RF-AUTH, REQ-AUTH-007, REQ-NFR-020 |
| OAuth secrets in SDK error text/logs — `OAuthFlowError` (state mismatch) and `OAuthTokenError` (invalid token response) messages can contain `state` or token-response content and are logged by `mcp.client.auth.*` with `logger.exception` | `Host.start()` attaches the `SecretRedactionFilter` and an exception-text stripper (message replaced by the exception type name, `exc_info`/`args` dropped; records about the Host's own `AuthorizationDeferredError` parking signal are dropped entirely) to every `mcp.client.auth*` module logger (list pinned by the SDK contract test); `AuthorizationError.reason` and `ServerStatus.reason` carry reason codes only, never SDK message text | REQ-AUTH-008, Art. 5 |
| Payloads in SDK debug logs — `mcp.client.streamable_http`/`sse`/`session` log whole JSON-RPC messages (tool arguments, elicitation answers, sampling results) at DEBUG; jsonschema messages echo the failing value | `Host.start()` attaches `SdkPayloadStripper` (message cut to its prefix + `<payload omitted>` when it carries a JSON-RPC payload or a pydantic `input_value=`; payload-bearing exception text reduced to the type name) and the `SecretRedactionFilter` to every non-auth `mcp.client`/`mcp.shared`/`mcp.os` module logger (list pinned by the SDK contract test); `core.schema` replaces the failing value's `repr` with `value` before bounding and redacting issue messages | REQ-NFR-010, Art. 5 |
| Secrets in logs/UI/DB | `SecretStr`; logging filter redacting known secret values; redaction test | REQ-REG-007, REQ-NFR-010 |
| Low-entropy argument recovery from audit | HMAC with per-install key | DD-007 |
| Remote `$ref` / schema bombs / ReDoS | No retrieval; `jsonschema` bounded; compile errors contained per tool; Server regexes evaluated with `regex` under a 50 ms per-match / 250 ms per-validation bound — a timeout is an unusable schema, never valid input; the SDK's own result validation (`ClientSession.validate_tool_result`, stdlib `re`) is disabled per session, so the Invoker's bounded step 7 is the only output check (contract-pinned) | DD-010, REQ-NFR-021 |
| Server-provided `requestState` tampering | Opaque echo only (REQ-HSH-041); integrity is the Server's duty | Spec MRTR |
| Oversized SSE events | SDK `max_sse_event_size` default (1 MiB) kept; no Host module overrides it (tested) | — |
| Compromised or re-pointed CI dependency in the release path (a moved action tag, a malicious `uv` release) publishing a tampered wheel under our Trusted Publisher | Actions pinned to full commit SHAs in `ci.yml`, `release.yml`, `pages.yml`; Dependabot grouped weekly update PRs reviewed like code; `publish` pins `uv` and only uploads the artifacts `build` checked; `id-token: write` only on `publish` in the release path, which runs only for `v*` tags in environment `pypi` with the owner as required reviewer (outside it, only Pages' `deploy` job in `pages.yml` holds it, for `actions/deploy-pages`, environment `github-pages`; no workflow grants it at the top level — tested) | DD-015, §9.1, Art. 5 |

Every row above is mapped to the tests that prove its mitigation by
`tests/security/test_threat_model.py` (F8-01), which parses this table: a row without
evidence, a key matching no row or several, or a cited test that does not exist fails the
build — adding a row means adding its evidence there. Residual notes (View self-navigation,
real Safari F8-15, GitHub-side release settings) are kept next to the evidence.

### 6.2 Sensitive data

| Data | Classification | Storage | Access |
|---|---|---|---|
| OAuth access/refresh tokens | Secret | keyring / `0600` file, bound to resource + issuer | `TokenStore` only |
| OAuth client info (client_id/secret) | Secret | keyring / `0600` file, bound to resource + issuer | `TokenStore` only |
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
configures handlers; the CLI configures a console handler (text, or JSON lines with
`--log-json`: `ts`, `level`, `logger`, `msg`, context fields, `exc`) — exactly one, on the
`reflex_mcphost` logger at INFO, writing to stderr, carrying the `SecretRedactionFilter`,
with `propagate = False` so a root handler Reflex installs does not print every line
twice. The CLI process and the packaged app (child) both install it. Context via `LoggerAdapter` extras: `server_id`, `fqn`,
`invocation_id`, `request_id`, `era`. A `SecretRedactionFilter` replaces any registered
secret value with `**********` before emission. The same filter plus an exception-text
stripper is attached to the SDK's `mcp.client.auth*` loggers (§6.1); the library still
installs no handlers. Measurement only (REQ-NFR-004 (b), F8-02): each tab watcher starts a
dedicated, otherwise idle bus subscriber (`ui.state.log_catalog_publishes`) that logs every
`catalog.changed` at DEBUG on `reflex_mcphost.ui.timing` with its wall-clock `epoch_ms`
(`time.time()`), i.e. at most one loop iteration after `EventBus.publish`; the CLI enables
that logger at DEBUG only with `--log-json` (`configure_logging(json_lines=True)`). With
several tabs each watcher logs its own line.

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
| Architecture | Art. 1/3/4 | import-linter, AST test, wheel-contents test, docs-snippet test | layers, forbidden imports, single effect path, no tests in wheel; `tests/unit/test_library_docs.py` (F8-05): every Python block of the README and `docs/` compiles (top-level `await` allowed) and its imports resolve; blocks marked `<!-- snippet: verbatim <file> \| run \| run model=TestModel \| rxconfig \| embed -->` are checked against the test file they come from or executed in a subprocess (isolated `HOME` / XDG, `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`, a small stdio `filesystem` fixture Server written by the test; the agent example with `TestModel` for its one model string); POSIX only |
| Security | Art. 5/6 | pytest | secret-seeded redaction test (incl. `definition_changed` and `consent.rules_changed` payloads), third-party files byte-identical, file modes (`tests/security/test_file_modes.py`: under umask `0o022` a data dir the Host creates is `0700` — a pre-existing one keeps its mode — and the DB, WAL, SHM and `secrets.json` are `0600` while running and after close; POSIX), guard middleware 403s, the §6.1 walk-through (`tests/security/test_threat_model.py`) |
| Resilience | REQ-NFR-020 | pytest (`tests/integration/resilience/`) | keyring `get` / `set` / `delete` raising at runtime on `oauth:*` entries (Host keeps running, one WARNING per failed operation, fail-closed sign-out); a crash storm at supervisor level — 20 connect cycles of a crashing stdio Server under the real 1…60 s backoff with an injected recording sleep and a fixed RNG, next to a real healthy Server on the same bus and task group: exact `backoff_delay` schedule, ≤ 3 log records per attempt, ring ≤ 80, no task / fd (Linux) / process leak |
| E2E | happy paths (Linux only: `/proc`) | headless smoke (httpx) and Playwright (Python) against `reflex-mcphost run --no-browser` from the built wheel; Ctrl-C = `SIGINT` to the run's process group (`uv` ignores a `SIGINT` sent to itself alone) | smoke: `/` and `/ping` 200 via `127.0.0.1`, 403 + notice via `localhost` and a foreign `Host`/`Origin`; Playwright: consent prompt → allow → result; elicitation form; REQ-NFR-003/004 measurements; MCP Apps (F7-06b, `tests/e2e/test_mcp_apps.py`, 9 tests × Chromium/Firefox/WebKit): the S-3 contract assertions — render, App tool call through consent, cancel, teardown, isolation and forgery negatives, UI via `localhost` → 403 + notice — plus REQ-APP-013/014 notices, the App-hidden call and `ui/open-link`. One session-scoped Playwright driver serves all engines (`ENGINES`); an engine that fails to launch fails the test, never skips it. Observation: a View transcript from an init script that runs only in the proxy frame, and Invoker calls counted as `audit_record` rows (read-only SQLite). WebKit without host libraries (local): the S-3 workaround — `libmanette-0.2-0` and `libhidapi-hidraw0` from `apt-get download`, `LD_PRELOAD` through `MCPHOST_E2E_WEBKIT_PRELOAD`, `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1`; CI uses `playwright install --with-deps` |
| Performance | NFR-001/002 | pytest-benchmark | consent eval at 10k rules; Host overhead vs. in-process server |

Performance tests assert the best p95 of up to 3 measurements (load can only raise
latency). REQ-NFR-004 (F6-09b) is timed in the page: a capture-phase `keydown` listener
stamps `performance.now()` at the filter keystroke and a `MutationObserver` stamps the
Catalog list DOM update; 20 interactions alternate a digit and Backspace with 50 Servers ×
40 tools (the modern fixture's `--tools N`); nearest-rank p95. REQ-NFR-004 (b) (F8-02):
one of the 50 Servers flips a tool every second (modern fixture `--toggle-every S`, which
publishes `tools/list_changed`); the app runs with `--log-json` and the `catalog.changed`
timing line (§7.1) gives the publish time, a `MutationObserver` stamps `Date.now()` when the
filtered Catalog list gains or loses the tool; each DOM flip is paired with the latest
earlier publish line of that Server (ms resolution, same machine). Each kind is a separate
p95 over 20 interactions and must meet 200 ms. The debounce of the filter inputs is part of the budget:
50 ms (DD-011), pinned by `tests/unit/ui/test_filter_latency.py`; with Reflex's 300 ms
default the p95 was ≈ 347 ms, with 50 ms 105 ms. The REQ-NFR-001 benchmark loads 10,000 rules straight into `RuleIndex`, the
post-load in-memory state that evaluation runs against (DD-005). `tests/perf/conftest.py`
prints the REQ-NFR-001 / REQ-NFR-002 p95s in a terminal summary, so every CI leg's log
carries them (macOS / Windows numbers are informational).

Warnings: `[tool.pytest.ini_options] filterwarnings` makes `ResourceWarning` **and**
`pytest.PytestUnraisableExceptionWarning` errors (F8-04). The second is needed because a
`ResourceWarning` raised from a finalizer reaches pytest as an unraisable exception, which
the first filter alone leaves as a warning; as a side effect any exception in a finalizer
fails the run. The leaks found when this was switched on were all test-only (unclosed
`sqlite3` connections in `test_migrations.py`, the HTTP fixture's `stderr` pipe, the E2E
`AppRun` stdout pipe).

Hash-pinned test fixtures (the vendored MCP App View) are marked `-text` in
`.gitattributes`, so no checkout converts their line endings (a Windows CRLF checkout
broke the sha256 assertion, `b0efc95`).

Traceability: every test docstring starts with the REQ IDs it verifies
(`"""REQ-CNS-007: …"""`); `scripts/check_req_coverage.py` (run in CI via `uv run`) fails
if any `MUST` REQ in the PRD has no citing test.

## 9. Rollout

### 9.1 Release strategy
- `0.1.0a1` (core + toolset, no UI) → `0.1.0b1` (UI + Apps) → `0.1.0`.
- GitHub Actions: lint/type/test matrix on every PR (`ci.yml`).
- `.github/workflows/release.yml`, on every run (PR touching `release.yml`, `pyproject.toml`,
  `uv.lock` or `scripts/check_wheel.py`; `workflow_dispatch`; `v*` tag push): `uv build`
  once → `scripts/check_wheel.py dist --expect-version <tag without v | pyproject version>`
  (wheel + sdist: no `tests/`, `spikes/`, `specs/`, `changes/`, `.claude/`; no `mcp.server`
  import — Art. 1; file-name and metadata version; wheel only: every `console_scripts`
  target is a shipped module binding the named attribute, `Provides-Extra` `pydantic-ai`,
  `langgraph`, `all`, Markdown long description) → `uv publish --dry-run --trusted-publishing
  never` → install-smoke of the built artifact (no checkout) on Linux/macOS/Windows ×
  Python 3.11/3.13 in a plain and an `[all]` venv: REQ-TLS-008, installed version, loaded
  from `site-packages`, adapters raise `ImportError` naming their extra without it and
  import with `[all]`; the plain install also runs `reflex-mcphost --version` (must print
  the expected version) and `reflex-mcphost servers --json` on a minimal temporary config
  (exit 0). `check_wheel.py` also requires the packaged reference app
  (`reflex_mcphost/app/web/rxconfig.py`, `…/mcphost_app/mcphost_app.py`) in the wheel and
  rejects `.web/`, `.states/` and `reflex.lock`. Concurrency `release-<ref>`; only PR runs are cancelled in progress.
- Upload only on a pushed `v*` tag: job `publish` in GitHub environment `pypi` (owner as
  required reviewer), job-level `id-token: write`, `uv publish --trusted-publishing
  always` with the artifacts of the build job. No API token exists.
- After `publish`, job `verify` (needs `build` — for its version output — and `publish`;
  the same tag-gate `if` as `publish`; matrix `ubuntu-latest`, `macos-latest`,
  `windows-latest` × Python 3.13, `fail-fast: false`, `timeout-minutes: 40`;
  `permissions: contents: read`, no checkout, `UV_NO_CACHE=1`) polls the PyPI JSON API until
  the version is visible (60 × 10 s = ≤ 10 min) and runs `uvx --from reflex-mcphost==<version>
  reflex-mcphost --version` (must print `reflex-mcphost <version>`) and `servers --json
  --config <minimal config>`; on Linux also `run --no-browser --port 3917`, polling
  `GET /ping` for ≤ 15 min (the first start of a version builds the frontend) and then
  stopping it with SIGTERM (a background job ignores SIGINT). It holds no `id-token`; a
  failure does not un-publish (a yank is an owner decision). It runs only on a real tag
  (F8-07b); its YAML is checked by `tests/scripts/test_release_workflow.py`, which also
  checks the CHANGELOG `[0.1.0]` known-limitation items and that the version is not bumped
  before the release PR.
- Owner prerequisites: PyPI Trusted Publisher (repo `ecrespo/reflex-mcphost`, workflow
  `release.yml`, environment `pypi`); the `pypi` environment; tag protection for `v*`.
  Tagging and approving the deployment are owner actions.
- `docs/` (incl. `oauth/client.json`) is deployed by `pages.yml` on pushes to `main`,
  which the release merge triggers; the CIMD URL is frozen from `0.1.0` on (REQ-AUTH-012).
- A published version is never re-uploaded: a bad pre-release is yanked and superseded.
- `0.1.0a1` contains no console script; `[project.scripts] reflex-mcphost` is added with
  the CLI (Plan F6-08). `reflex` remains a runtime dependency in a1 (one distribution, no
  UI code shipped).
- Supply chain: all workflow actions pinned to commit SHAs and updated through Dependabot
  PRs (DD-015); the `publish` job pins `uv` (`0.11.7` at `0.1.0a1`); in the release path no
  job but `publish` holds `id-token: write`; the only other holder is Pages' `deploy` job
  (§6.1, tested).
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
| S-6 | Adapter shapes: PydanticAI 2.64 `AbstractToolset` (`get_tools`, `call_tool`), LangChain `BaseTool` subclass with dict `args_schema` (not `StructuredTool`, which drops `config`/`run_manager` arguments) | Example agents call a Host tool through consent |

## 11. Open questions

- [x] "Agent playground" page in the reference app → **deferred to v0.2** (resolved 2026-10-30).
- [x] Windows secret-file fallback → **accept** per-user data dir ACL inheritance **and log a
      warning** (resolved 2026-10-30; DD-008).

## Constitution check

- **Art. 1** — no `mcp.server` import in `src/` (DD-012 constants, DD-018 contract); Server fixtures only in `tests/fixtures/servers/`; wheel check in release (DD-015).
- **Art. 2** — §0 pins the revision set with verification date and sources; all protocol work via `mcp.Client` (DD-001); deprecated features isolated in `core.handlers` with `deprecated` flags.
- **Art. 3** — single effect path (Invoker/MRTR/sampling handler) enforced by AST test (DD-018); fail-closed flows (§3.3 error table); audit before send (Flow C step 5).
- **Art. 4** — layers + forbidden imports (DD-018); Host runs in a lifespan task without core importing Reflex (DD-011); adapters as extras (DD-015).
- **Art. 5** — DD-007, DD-008 (fail-closed sign-out, `SecretStoreError`), DD-010 (bounded Server regexes incl. output validation), DD-012, DD-017; §6 (every §6.1 row test-mapped, §8).
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
| 1.5 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase2-impl`: Flow B ready/refresh order, era-cache validation, bounded session rebuild, shielded shutdown |
| 1.6 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase3-impl`: Flow C audit/validation details, reads on the Host MRTR loop (DD-001), DD-005 one rule per key + poisoning, DD-006 pooled engine, DD-010 drafts/bounds/known limits, §5.1 modules, §5.3 exceptions + `-32000`, §8 perf measurement; Flow B step 6 ready gate for `wait_ready` |
| 1.7 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase4-prep` (D1–D3, D6–D8): Flow E rewritten (flow inside the connection attempt, parked handlers, Host pre-redemption `state`/`iss` check, refresh-once on rejection, 401 while ready, SDK issuer binding), E.4 row, Flow C step 6 elicitation rejection per era, DD-003 roots/log-level rebuild triggers, DD-009 `h11` direct dependency, §3.2 OAuth only without static headers, §6.1 SDK auth log row, §7.1 |
| 1.8 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase4-impl`: Flow E rewritten (internal `…Error` names, port reservation while parked, discovery first, authentic-callback listener — security review, flow outcome, refresh-once only for the sent token, dead tokens dropped, lock-free fast path, 401 while ready via `reconnecting`, resource + issuer binding); E.4 row; Flow C legacy sampling-denied override on `protocol_error`; DD-003 stdio rebuild respawns, catalog kept during rebuild; §3.2 `roots_writer`; §5.1 `connection/inflight.py`; §5.3 internal OAuth exceptions; §6.1/§6.2 OAuth rows |
| 1.9 | 2026-10-31 | Ernesto Crespo | F4-09b: §6.1 row for SDK payload logs and value-free schema messages (leaks found by the seeded redaction test; delta `2026-10-phase4-impl` addendum) |
| 1.10 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase5-prep` (D1, D4, D5): `Toolset` implementation in `core.toolset` with public re-export (§3.2, §5.1), DD-018 contract 5 `adapters ↛ core`; DD-015 release bullet and §9.1 release workflow (dry-run on every run, upload only from `v*` tags via environment `pypi`, owner prerequisites, no console script in `0.1.0a1`); §10 S-6 row names the `BaseTool` subclass |
| 1.11 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-10-phase5-impl` (items 6, 8, 10–13): §9.1 release workflow details (triggers, concurrency, smoke matrix on the built artifact, `check_wheel.py` checks) and supply-chain bullet; DD-015 SHA-pinned actions + Dependabot + pinned `uv` for publish; §6.1 row for a compromised CI dependency in the release path; §8 docs-snippet test; §3.2 Adapters row |
| 1.12 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase6-prep` (D1–D4, D6, D7): DD-011 async-context-manager lifespan task (no 5 s cap; same task for start/aclose), PromptRegistry semantics, view models, bounded list rendering; Flow D steps 3–5 (PromptRegistry change stream for every prompt kind); DD-015 Playwright entry and `e2e` CI job; DD-016 CLI ↔ app environment contract, pre-flight, readiness, atomic workspace; DD-017 origin from `deploy_url`, remote opt-in; §5.1 `ui/models.py`, `tests/e2e/`; §6.1 DNS-rebinding row; §8 E2E row |
| 1.13 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase6-impl`: Flow D step 5 (ask joins on the request id; re-prompt after the first ask is removed + added); DD-011 50 ms filter debounce (REQ-NFR-004), Audit filters on blur, UI implementation rules (`HostRuntime` phases, `ui/actions.py`, explicit computed-var deps, card forms via a hidden card id, view-model rules); DD-015 E2E Linux-only, `uv tool run`; DD-016 child environment, process group and signal forwarding, workspace marker, packaged app; DD-017 pure-ASGI guard, `503` while starting, repeated headers, `1008`, `deploy_url` refusals; DD-018 contract 6; §5.1 `ui/actions.py`, `filters.py`; §6.1 Server-provided URL row (defense in depth); §7.1 console handler; §8 E2E Linux-only and REQ-NFR-004 measurement note; §9.1 CLI smoke and reference-app wheel checks |
| 1.14 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase7-prep` (D1–D10): §3.1 single-port diagram; §3.2 Sandbox proxy row (`h11` listener); Flow F rewritten (UI-started invocations only, after consent and completion, outbox relay, step 1a App scope, teardown reasons) and rows F.0/F.2/F.7; DD-012 data-only relay, proxy listener rules, App scope in the Invoker, glossary, origin-pair check in the lifespan with Apps disabled on failure, `CapabilityPlan.mcp_apps`; DD-015/DD-017 F7-06b; §5.1 proxy files, `tests/fixtures/apps/`; §6.1 rows (App scope, forged messages, relay, CSP injection, flooding, `ui/open-link`, proxy); §8 E2E row |
| 1.15 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase7-impl`: Flow F steps 1–3, 8 (teardown on invocation start, `open_view` / `ViewPlan`, the View runs in the tab's `on_load` watcher, replace / close / server-removed / shutdown / disconnect semantics); DD-011 MCP App Views bullet; DD-012 Host errors from h11, IPv4-only fallback, implementation rules (`core.apps` layout and ports, `proxy.html` hardening, `ancestorOrigins` check as defense in depth only — Firefox `"null"`, resolver errors, `relay_library`); DD-016 MEMORY state manager; DD-018 contract 7 (`ui.proxy` Reflex-free); §5.1 files; §6.1 rows (forged messages tested in three engines with a mutation check, `ui/open-link`, referrer leak residual, proxy, tab state on disk mitigated, self-navigation); §8 E2E row (three engines, WebKit workaround, observation method) and hash-pinned fixtures |
| 1.16 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase8-prep` (D1–D8, D11–D14): §0 rows `regex`, `pydantic`, `h11` and the F8-06 re-verification note; DD-002 process-group cleanup after any stdio transport close (REQ-CON-008); DD-010 Server regexes through `regex` with a 50 ms / 250 ms bound (REQ-NFR-021), rejected alternatives; DD-011 Permissions view live on `consent.rules_changed`; DD-012 proxy connection cap, self-navigation detection; DD-016 workspace pruning, Windows signal test, MEMORY state manager enforced; DD-017 `Referrer-Policy: no-referrer`, state-manager start-up check; DD-018 contract 6 covers `ui.actions`; §6.1 rows (rug pull + step 4a, referrer mitigated, proxy cap, tab state enforced, self-navigation detected, refused authorization URL cancels the flow, ReDoS bound); §9.1 post-publish `verify` job |
| 1.17 | 2026-11-01 | Ernesto Crespo | Folded delta `2026-11-phase8-impl`: §0 re-verified 2026-11-01, no row changed (`platformdirs` 4.14.2 patch, lock refresh deferred); DD-002 pid source, POSIX-only pid equality, Windows Job Object verified, cleanup log levels, pgid-reuse residual; DD-008 fail-closed sign-out and `SecretStoreError`, keyring `set` / `get` failures; DD-010 as implemented (`ContextVar` budget, evaluated-keys walk, `format: regex` via `regex`) and the SDK's result validation replaced by the Invoker's step 7; DD-012 connection cap and self-navigation discard as implemented; DD-016 pruning rules (rename-then-delete, symlinks, >7-day-run residual), Windows test evidence; DD-017 WebKit empty `Referer`, state-manager mode inference; DD-018 contract 6 widened (still 7 contracts); §6.1 rows (ReDoS names the SDK bypass, OAuth row covers a failed sign-out delete, referrer, self-navigation, tab state, SSE, CI `id-token` incl. Pages' `deploy`) and the test-mapping note; §7.1 `catalog.changed` timing logger; §8 docs-snippet test, security / resilience rows, REQ-NFR-004 (b) method, perf summary, warnings as errors; §9.1 `verify` job as implemented; Constitution check |
