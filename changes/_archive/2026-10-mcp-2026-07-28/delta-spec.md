# Delta — Align specs with MCP 2026-07-28, MCP Apps 2026-01-26 and `mcp` SDK 2.3

Each entry references the base document and section. Modified REQs keep their IDs; new
REQs take new IDs; removed IDs are retired forever.

Sources verified 2026-10-30:
- MCP spec `2026-07-28` — versioning, changelog, MRTR, Streamable HTTP pages on modelcontextprotocol.io
- MCP Apps `2026-01-26` — `ext-apps/specification/2026-01-26/apps.mdx`
- `mcp` SDK 2.3.0 — PyPI, py.sdk.modelcontextprotocol.io "What's new in v2"

---

## 1. `specs/constitution.md` → v1.1

### MODIFIED — Vocabulary (ADDED terms)

| Term | Meaning |
|---|---|
| **Era** | Protocol family of a Server: **modern** (`2026-07-28`+, per-request `_meta`, no handshake) or **legacy** (`2025-11-25` and earlier, `initialize` handshake). |
| **Dual-era** | Supporting both eras, detecting each Server's era at connect time. |
| **Input request** | A Server's request for client-side input (elicitation, sampling, roots): an MRTR `inputRequests` entry (modern) or a server-initiated JSON-RPC request (legacy). |

### MODIFIED — Art. 2 (Spec fidelity on the official SDK)

- **Before:** "…target one pinned, dated revision… Supported transports are stdio and
  Streamable HTTP… legacy HTTP+SSE MAY be supported only as a read-only compatibility
  fallback."
- **After:** THE SYSTEM SHALL target the pinned revision set recorded in the Technical
  Design — v0.1: MCP **`2026-07-28`** (preferred), **`2025-11-25`** (legacy fallback),
  MCP Apps **`2026-01-26`**, `mcp` SDK **2.3.x** — verified against official sources and
  dated. THE SYSTEM SHALL be dual-era. Features the pinned revision marks *Deprecated*
  (Roots, Sampling, Logging, HTTP+SSE, Dynamic Client Registration) SHALL be implemented
  only as compatibility, flagged `deprecated` in code and docs, and SHALL NOT be extended
  beyond the spec. The rest of the article is unchanged.
- **Reason:** verification found the current revision and the deprecations; "one
  revision" cannot express dual-era support.

### MODIFIED — Art. 5 (Secrets and untrusted content), MCP Apps sentence

- **Before:** "MCP Apps render only in a sandboxed iframe without `allow-same-origin`,
  communicating solely through the host-side bridge."
- **After:** "MCP App Views SHALL render only through a sandbox proxy served from an
  origin distinct from the Host UI's origin, as required by the pinned MCP Apps revision;
  no Server-provided content SHALL ever execute on the Host UI's origin; CSP SHALL be
  built only from the domains the resource declares; the View communicates solely through
  the host-side bridge."
- **Reason:** MCP Apps 2026-01-26 requires the proxy iframe to carry `allow-same-origin`
  on a *different* origin; the security property we care about (Server content can't
  reach the Host origin) is expressed by origin separation, not by that attribute.

### ADDED — Amendments table row

| 2026-10-30 | Vocabulary, Art. 2, Art. 5 | Era terms; pinned revision set + deprecated-as-compat rule; MCP Apps isolation by origin | MCP 2026-07-28 / Apps 2026-01-26 verification | Ernesto Crespo |

---

## 2. `specs/prd/mcphost-v0.1.md` → v1.1

### MODIFIED — RF-CON

**REQ-CON-001** (event)
- **Before:** create one Client, perform `initialize`, send `notifications/initialized`, → `ready`.
- **After:** WHEN an enabled Server is registered, THE SYSTEM SHALL create exactly one
  Client for it, determine its era — stdio: send `server/discover` and fall back to
  `initialize` on any error that is not a recognized modern error or on timeout; HTTP:
  send a modern request and fall back to `initialize` on a `4xx` whose body is not a
  recognized modern error — and transition to `ready` when `server/discover` (modern) or
  the `initialize` handshake (legacy) completes.
- **Reason:** handshake removed in 2026-07-28 (SEP-2575); dual-era decision.

**REQ-CON-002** (ubiquitous)
- **After:** THE SYSTEM SHALL record the Server's era, protocol version, `serverInfo`
  and capabilities (from `DiscoverResult` or `InitializeResult`), update `serverInfo`
  when a modern result carries `io.modelcontextprotocol/serverInfo`, and SHALL only send
  requests the Server declared support for.

**REQ-CON-003** (ubiquitous)
- **After:** THE SYSTEM SHALL declare as Client capabilities exactly the host-side
  features currently enabled (elicitation, sampling, roots, extension
  `io.modelcontextprotocol/ui`) — in every request's `_meta` for modern Servers, and in
  `initialize` for legacy Servers.

**REQ-CON-004** (unwanted)
- **After:** IF the Server and Host share no supported protocol version
  (`UnsupportedProtocolVersionError` `-32022` without overlap, or a legacy `initialize`
  negotiating an unsupported version), THEN THE SYSTEM SHALL set state `failed` with the
  requested and supported versions in the reason.

**REQ-CON-005** (unwanted)
- **After:** IF a `ready` Client loses its transport (stdio process exit; HTTP connection
  failure or `5xx` on a request or on the `subscriptions/listen` stream; legacy session
  stream loss), THEN THE SYSTEM SHALL set state `reconnecting` and retry with exponential
  backoff starting at 1 s, doubling, with jitter, capped at 60 s.

**REQ-CON-013** (optional, `COULD`)
- **After:** WHERE `legacy_sse_fallback` is enabled and both modern and legacy Streamable
  HTTP attempts fail with `400`/`404`/`405` without a recognized modern error body, THE
  SYSTEM SHALL connect via the deprecated HTTP+SSE transport and flag the Client
  `legacy_transport`.

**REQ-CON-014** (optional)
- **Before:** WHEN a Streamable HTTP Server returns a session ID…
- **After:** WHERE a Streamable HTTP Server is legacy-era and returns `Mcp-Session-Id`,
  THE SYSTEM SHALL send it on every subsequent request and terminate the session (HTTP
  `DELETE`) on disconnect. THE SYSTEM SHALL NOT send `Mcp-Session-Id` to modern Servers.

**REQ-CON-015** (unwanted)
- **After:** IF a legacy-era Streamable HTTP Server responds `404` to a request carrying
  a session ID, THEN THE SYSTEM SHALL re-initialize without counting it as a
  reconnection failure.

### ADDED — RF-CON

| ID | Type | Criterion |
|---|---|---|
| REQ-CON-016 | ubiquitous | THE SYSTEM SHALL send, on every modern Streamable HTTP POST, `Accept: application/json, text/event-stream`, `MCP-Protocol-Version` equal to the `_meta` version, `Mcp-Method`, and — for `tools/call`, `resources/read`, `prompts/get` — `Mcp-Name`, using the `=?base64?…?=` sentinel when the value is not header-safe. |
| REQ-CON-017 | ubiquitous | THE SYSTEM SHALL mirror tool arguments annotated with `x-mcp-header` into `Mcp-Param-{Name}` headers on Streamable HTTP, encoded per the spec's value-encoding rules. |
| REQ-CON-018 | unwanted | IF a tool definition received over Streamable HTTP has an invalid `x-mcp-header` annotation, THEN THE SYSTEM SHALL exclude that tool from the catalog and log a warning naming the tool and reason. |
| REQ-CON-019 | event | WHEN the era of a Server has been detected, THE SYSTEM SHALL cache it per stdio process / HTTP origin, and re-detect only if a request under the cached era fails with an era-indicating error. |

### MODIFIED — RF-CAT

**REQ-CAT-003** (event)
- **After:** WHEN a Server is `ready`, THE SYSTEM SHALL subscribe to list changes it
  declares — modern: one `subscriptions/listen` stream opting in to
  `toolsListChanged` / `promptsListChanged` / `resourcesListChanged`; legacy: the
  `notifications/*/list_changed` notifications — and, on each such notification,
  re-fetch only that list for that Server and emit a catalog-changed event.

**REQ-CAT-010** (optional, `COULD`)
- **After:** WHERE the Server supports resource subscriptions, THE SYSTEM SHALL allow the
  Consumer to subscribe (modern: `resourceSubscriptions` on the listen stream; legacy:
  `resources/subscribe`) and SHALL emit an event on `notifications/resources/updated`.

### ADDED — RF-CAT

| ID | Type | Criterion |
|---|---|---|
| REQ-CAT-012 | unwanted | IF a modern `subscriptions/listen` stream closes while the Server is `ready`, THEN THE SYSTEM SHALL reopen it with the REQ-CON-005 backoff and re-fetch all lists once it is reopened. |
| REQ-CAT-013 | optional | WHERE a Server does not honor list-change subscriptions, THE SYSTEM SHALL re-fetch a list when its `ttlMs` elapses, never more often than every 30 s. — `SHOULD` |
| REQ-CAT-014 | ubiquitous | THE SYSTEM SHALL record each tool's MCP Apps visibility (`_meta.ui.visibility`, default `["model","app"]`) and UI resource URI (`_meta.ui.resourceUri`, falling back to the deprecated `_meta["ui/resourceUri"]`). |

### MODIFIED — RF-INV

**REQ-INV-003** (unwanted)
- **After:** IF a call exceeds its timeout, THEN THE SYSTEM SHALL cancel it — stdio and
  legacy HTTP: `notifications/cancelled`; modern HTTP: close the request's response
  stream — and return outcome `timeout`.

**REQ-INV-004** (event)
- **After:** WHEN the Consumer cancels an in-flight invocation, THE SYSTEM SHALL cancel it
  by the same transport-specific mechanism as REQ-INV-003 and return outcome `cancelled`.

**REQ-INV-009** (ubiquitous) — field list extended with `input_rounds` (number of MRTR
rounds). One record per invocation covers all its rounds.

### ADDED — RF-INV

| ID | Type | Criterion |
|---|---|---|
| REQ-INV-014 | ubiquitous | THE SYSTEM SHALL treat results lacking `resultType` (legacy Servers) as `"complete"`. |
| REQ-INV-015 | unwanted | IF the response stream of a modern `tools/call` breaks before the final response, THEN THE SYSTEM SHALL return outcome `connection_lost` without re-issuing the call; reads (`*/list`, `resources/read`, `prompts/get`, `server/discover`) SHALL be re-issued once automatically with a new request ID. |

### MODIFIED — RF-HSH (header and per-handler triggers)

Header becomes: **RF-HSH: Host-side handlers** — elicitation `MUST`; sampling `MUST`
(*deprecated in spec, compatibility*); roots `SHOULD` (*deprecated, compatibility*);
logging `SHOULD` (*deprecated, compatibility*).

**REQ-HSH-001** (event)
- **After:** WHEN a Server issues a sampling input request (MRTR `inputRequests` entry or
  legacy `sampling/createMessage`), THE SYSTEM SHALL evaluate consent for sampling scoped
  to that Server before any LLM call.

**REQ-HSH-005** (unwanted)
- **After:** IF no `SamplingProvider` is configured or the sampling request is denied,
  THEN THE SYSTEM SHALL — legacy: return a JSON-RPC error to the Server; modern: not
  retry the originating request and end it with outcome `denied` (error code
  `sampling_denied`) — and record the outcome in the `AuditLog`.

**REQ-HSH-010** (event)
- **After:** WHEN a Server issues an elicitation input request in `form` mode, THE SYSTEM
  SHALL render a form for its requested schema showing the requesting Server's identity
  and message.

**REQ-HSH-020** (event, `SHOULD`)
- **After:** WHEN a Server issues a roots input request (MRTR `roots/list` entry or legacy
  `roots/list`), THE SYSTEM SHALL return the roots configured for that Server (default:
  none).

**REQ-HSH-021** (event, `SHOULD`)
- **After:** WHEN the configured roots for a legacy-era Server change, THE SYSTEM SHALL
  send `notifications/roots/list_changed`. (Modern Servers obtain roots per request.)

**REQ-HSH-030** (event, `SHOULD`)
- **After:** WHEN a Server emits `notifications/message` (modern: on a request's response
  stream; legacy: at any time) or writes to stderr (stdio), THE SYSTEM SHALL append it,
  with level, logger and timestamp, to that Server's bounded log stream.

**REQ-HSH-031** (event, `SHOULD`)
- **After:** WHEN the Consumer sets a Server's log level, THE SYSTEM SHALL — legacy: send
  `logging/setLevel`; modern: include `io.modelcontextprotocol/logLevel` in the `_meta`
  of every subsequent request to that Server. By default no level is sent.

### ADDED — RF-HSH (MRTR and URL elicitation)

| ID | Type | Criterion |
|---|---|---|
| REQ-HSH-040 | event | WHEN a `tools/call`, `resources/read` or `prompts/get` returns `resultType: "input_required"`, THE SYSTEM SHALL fulfill every `inputRequests` entry with the matching handler and retry the original request with a new JSON-RPC ID, the `inputResponses` map, and the exact `requestState` (if any). |
| REQ-HSH-041 | ubiquitous | THE SYSTEM SHALL NOT inspect, parse or modify `requestState`, and SHALL use input responses only for the retry of their originating request. |
| REQ-HSH-042 | ubiquitous | THE SYSTEM SHALL evaluate tool-call consent once per invocation; MRTR retries of an allowed invocation SHALL NOT re-prompt for the tool, though each sampling input request is consent-gated per REQ-HSH-001. |
| REQ-HSH-043 | unwanted | IF the same request returns `input_required` more than 8 consecutive times, THEN THE SYSTEM SHALL stop retrying and return outcome `protocol_error` with code `input_rounds_exceeded`. |
| REQ-HSH-044 | unwanted | IF an `inputRequests` entry uses a method the Host did not declare as a capability, THEN THE SYSTEM SHALL stop retrying and return `protocol_error` with code `undeclared_input_request`. |
| REQ-HSH-045 | event | WHEN a legacy-era Server sends a server-initiated sampling, elicitation or roots request, THE SYSTEM SHALL handle it with the same handlers and consent rules as the MRTR path. |
| REQ-HSH-015 | event | WHEN a Server issues an elicitation input request in `url` mode, THE SYSTEM SHALL show the full URL and requesting Server, open it in the browser only if the Consumer accepts, and return the Consumer's `accept` / `decline` / `cancel` action. — `SHOULD` |

### MODIFIED — RF-AUTH

**REQ-AUTH-003** (optional)
- **Before:** WHERE DCR is supported and no client ID is configured, register a client.
- **After:** WHERE no client ID is configured for a Server, THE SYSTEM SHALL use, in
  order: the project's Client ID Metadata Document URL if the authorization server
  advertises CIMD support; otherwise Dynamic Client Registration (deprecated fallback)
  with an appropriate `application_type`; otherwise stay `auth_required` with a message
  asking for a `client_id` in `mcphost.toml`.

### ADDED — RF-AUTH

| ID | Type | Criterion |
|---|---|---|
| REQ-AUTH-010 | unwanted | IF the authorization response's `iss` is present and differs from the recorded issuer, or is absent while the authorization server advertises `authorization_response_iss_parameter_supported`, THEN THE SYSTEM SHALL abort the flow before redeeming the code (RFC 9207). |
| REQ-AUTH-011 | ubiquitous | THE SYSTEM SHALL key persisted client credentials by authorization-server issuer, SHALL NOT reuse them with another issuer, and SHALL re-register when a Server's issuer changes. |
| REQ-AUTH-012 | ubiquitous | THE PROJECT SHALL publish its Client ID Metadata Document at a stable HTTPS URL (GitHub Pages of `ecrespo/reflex-mcphost`), versioned in the repository, listing only loopback redirect URIs. |

### MODIFIED — RF-APP

**REQ-APP-001** (event)
- **After:** WHEN a tool with `_meta.ui.resourceUri` (`ui://…`) is invoked, THE SYSTEM
  SHALL fetch that resource with `resources/read` from the same Server and render it if
  its MIME type is `text/html;profile=mcp-app`.

**REQ-APP-002** (ubiquitous)
- **After:** THE SYSTEM SHALL render MCP App Views only inside a sandbox proxy iframe
  (`sandbox="allow-scripts allow-same-origin"`) served from an origin distinct from the
  Host UI's origin, with a CSP built from the resource's `_meta.ui.csp`
  (`connectDomains`, `resourceDomains`, `frameDomains`, `baseUriDomains`) and the
  restrictive default when omitted; undeclared domains SHALL never be allowed.

**REQ-APP-003** (ubiquitous)
- **After:** THE SYSTEM SHALL accept bridge messages only from the proxy window it
  created, validate their JSON-RPC shape, and SHALL NOT send any request or notification
  to the View before `ui/notifications/initialized`.

**REQ-APP-006** (event)
- **After:** WHEN the View is initialized, THE SYSTEM SHALL send
  `ui/notifications/tool-input` exactly once, and `ui/notifications/tool-result` when the
  invocation completes (always after tool-input).

### ADDED — RF-APP

| ID | Type | Criterion |
|---|---|---|
| REQ-APP-007 | ubiquitous | THE SYSTEM SHALL declare the extension `io.modelcontextprotocol/ui` with `mimeTypes: ["text/html;profile=mcp-app"]` in its client capabilities. |
| REQ-APP-008 | ubiquitous | THE SYSTEM SHALL exclude tools whose visibility lacks `"model"` from every `Toolset`, and SHALL reject App-originated `tools/call` for tools whose visibility lacks `"app"`. |
| REQ-APP-009 | event | WHEN an invocation is cancelled or times out, THE SYSTEM SHALL send `ui/notifications/tool-cancelled` to its View. |
| REQ-APP-010 | event | WHEN a View is removed, THE SYSTEM SHALL send `ui/resource-teardown` and wait up to 2 s for its response before destroying the iframe. |
| REQ-APP-011 | ubiquitous | THE SYSTEM SHALL grant no device permissions (`camera`, `microphone`, `geolocation`, `clipboardWrite`) to Views in v0.1, and SHALL show a notice when a resource declares external domains. |
| REQ-APP-012 | ubiquitous | THE SYSTEM SHALL record every View-initiated RPC (method, outcome) in the Server's log stream. |

### MODIFIED — §8 External dependencies (rows)

| Dependency | Status | Risk |
|---|---|---|
| `mcp` Python SDK | **2.3.x pinned**, dual-era `Client` | High — v2 line still moving |
| MCP specification | **2026-07-28** (current) + **2025-11-25** (legacy) | Medium |
| MCP Apps extension | **2026-01-26** (Stable) | Medium |
| GitHub Pages (CIMD hosting) — **ADDED** | Free, project-owned | Low — URL stability is a public contract |

### MODIFIED — §11 Risks (ADDED rows)

| Risk | P | I | Mitigation |
|---|---|---|---|
| Deprecated features (sampling/roots/logging) removed in a future revision | High | Medium | Flagged `deprecated`; isolated handlers; drop via Delta Spec when removed |
| Era misdetection on odd legacy servers | Medium | Medium | Rely on SDK detection; per-Server `mode = "legacy"` override in `mcphost.toml` |
| Authorization servers rejecting CIMD loopback redirects | Medium | Medium | DCR fallback; manual `client_id` |

---

## 3. `specs/api/mcphost-api-v0.1.md` → v1.1

### MODIFIED — Metadata / §1
Add row: **Pinned protocol set:** MCP `2026-07-28` (preferred) + `2025-11-25` (legacy),
MCP Apps `2026-01-26`, `mcp` SDK 2.3.x.

### MODIFIED — §2.2 OAuth loopback callback
Callback also receives `iss`; validated per REQ-AUTH-010 before redeeming the code.
Registration order per REQ-AUTH-003. Client credentials stored keyed by issuer
(REQ-AUTH-011).

### MODIFIED — §4.1 `Host`, capabilities row
- **After:** Capabilities are computed from `prompter`, `sampling_provider`, roots and the
  Apps extension. **Modern** Servers receive them on every request, so `set_prompter()`
  takes effect on the next request; **legacy** Servers receive them in `initialize`, so
  changes apply only after `reconnect()`. The Host does not auto-reconnect.

### MODIFIED — §4.2 `HostConfig` / §5.2 `mcphost.toml`
ADDED keys:
```toml
[host]
oauth_client_metadata_url = "https://ecrespo.github.io/reflex-mcphost/oauth/client.json"  # default; REQ-AUTH-012

[servers.<id>]                # also allowed in [overrides.<id>]
mode = "auto"                 # "auto" | "modern" | "legacy" — forces era (REQ-CON-019 escape hatch)
```

### MODIFIED — §4.5 `ConnectionManager`
- `ServerStatus` ADDED field `era: Literal["modern", "legacy"] | None`.
- `disconnect` (modern HTTP): closes the listen stream; no `DELETE` (no session).
- `set_log_level` row → legacy: `logging/setLevel`; modern: stored and sent as
  `io.modelcontextprotocol/logLevel` in each request's `_meta`. `CapabilityNotSupportedError`
  only when the Server declares no logging capability. Marked *deprecated (SEP-2577)*.
- `set_roots` row → legacy: sends `notifications/roots/list_changed`; modern: stored and
  served on the next roots input request. Marked *deprecated (SEP-2577)*.
- State machine: `connecting → ready` on "discover OK (modern) or initialize +
  initialized OK (legacy)"; the `ready → connecting` on HTTP 404 transition is
  **legacy-only**; `ready → reconnecting` also on listen-stream `5xx`/connection failure.

### MODIFIED — §4.6 `Catalog`
- `ToolEntry` ADDED field `visibility: tuple[Literal["model", "app"], ...]`
  (REQ-CAT-014); `ui_resource_uri` source is `_meta.ui.resourceUri` with deprecated flat
  key fallback.
- `subscribe`/`unsubscribe`: modern → updates the opted-in `resourceSubscriptions` of the
  listen stream; legacy → `resources/subscribe`/`unsubscribe`.
- `read_resource`, `read_template`, `get_prompt` run the MRTR loop (REQ-HSH-040); their
  input requests (e.g. elicitation) reach the `Prompter`. Not consent-gated (unchanged
  decision), but sampling input requests inside them **are** (REQ-HSH-001).
- Tools rejected for invalid `x-mcp-header` never appear (REQ-CON-018).

### MODIFIED — §4.8 `Invoker`
- `InvocationOutcome` ADDED `connection_lost = "connection_lost"` (REQ-INV-015).
- Pipeline step 6 becomes: "`tools/call` with timeout, progress and cancellation,
  **looping on `input_required`** (≤ 8 rounds, REQ-HSH-040…044); outcomes `timeout` /
  `cancelled` / `protocol_error` / `connection_lost` / `denied` (`sampling_denied`)".
- Cancellation note: stdio & legacy HTTP → `notifications/cancelled`; modern HTTP →
  close the response stream (REQ-INV-003/004).
- Timeout covers the whole invocation including MRTR rounds, **excluding** time spent
  waiting on the `Prompter`.

### MODIFIED — §4.9 `AuditRecord`
ADDED field `input_rounds: int` (REQ-INV-009).

### MODIFIED — §4.10 `Prompter`
- `ElicitationRequest` ADDED `mode: Literal["form", "url"]`, `url: str | None`;
  `requested_schema` is `None` in `url` mode (REQ-HSH-015).
- Prompter methods are invoked identically for MRTR and legacy input requests
  (REQ-HSH-045).

### MODIFIED — §4.12 `Toolset`
`list()` also excludes tools whose `visibility` lacks `"model"` (REQ-APP-008).

### MODIFIED — §4.14 `SamplingProvider`
Trigger: MRTR sampling input request or legacy `sampling/createMessage`. Denial / no
provider: legacy → JSON-RPC error; modern → originating request ends `denied` with
`sampling_denied` (REQ-HSH-005). Marked *deprecated (SEP-2577), compatibility*.

### MODIFIED — §7 MCP Apps bridge (replaces "Rendering", "Transport" and the method table)

**Rendering (REQ-APP-001/002/011):** resource read via `resources/read`, MIME
`text/html;profile=mcp-app`. The Host UI embeds a **sandbox proxy** page served by the
reference app's backend on a **distinct loopback origin** (exact scheme/port decided in
the Technical Design). Proxy iframe: `sandbox="allow-scripts allow-same-origin"`, empty
`allow` (no device permissions). The Host delivers the HTML to the proxy with
`ui/notifications/sandbox-resource-ready` (`html`, `csp`, `sandbox`) after
`ui/notifications/sandbox-proxy-ready`. CSP from `_meta.ui.csp`; restrictive default.

**Transport (REQ-APP-003):** JSON-RPC 2.0 over `postMessage`; accepted only from the proxy
window; nothing sent to the View before `ui/notifications/initialized`.

| Direction | Message | Host behavior | REQ |
|---|---|---|---|
| View → Host | `ui/initialize` | Reply with host info, `hostCapabilities` (no `openLinks` without confirmation, no message), `hostContext` (theme, `displayMode: "inline"`, container dimensions, locale, time zone). | — |
| View → Host | `ui/notifications/initialized` | Unlocks Host → View traffic. | REQ-APP-003 |
| Host → View | `ui/notifications/tool-input` | Once, after initialized. | REQ-APP-006 |
| Host → View | `ui/notifications/tool-input-partial` | Not sent in v0.1 (no streamed inputs). | — |
| Host → View | `ui/notifications/tool-result` | On completion, after tool-input. | REQ-APP-006 |
| Host → View | `ui/notifications/tool-cancelled` | On `cancelled` / `timeout` / `connection_lost`. | REQ-APP-009 |
| Host → View | `ui/notifications/host-context-changed` | On theme or container size change. | — |
| Host → View | `ui/resource-teardown` (request) | Before removing the View; wait ≤ 2 s. | REQ-APP-010 |
| View → Host | `tools/call` | Own Server only; tool visibility must include `"app"`; via `Invoker` with `Consumer.app(server_id)` (consent + audit). Else `-32602`. | REQ-APP-004, REQ-APP-008 |
| View → Host | `resources/read` | Own Server only. | — |
| View → Host | `ui/open-link` | Confirmation with full URL; open in new tab only on approval. | — |
| View → Host | `ui/request-display-mode` | Only `inline` supported; respond with `inline`. | — |
| View → Host | `ui/notifications/size-changed` | Resize iframe within container bounds. | — |
| View → Host | `notifications/message` | Append to Server log stream. | REQ-HSH-030 |
| View → Host | `ping` | Respond empty result. | — |
| View → Host | `ui/message`, `ui/update-model-context` | `-32601` "Method not supported by this host" — no conversation in v0.1 (documented deviation from the spec's SHOULD). | REQ-APP-005 |
| View → Host | malformed / other | `-32600` / `-32601`; View keeps running. | REQ-APP-005 |

Every View-initiated RPC is logged (REQ-APP-012).

### MODIFIED — §10 Versioning
- **After:** The Host offers MCP `2026-07-28` and falls back to `2025-11-25`; it accepts
  any version both it (via the SDK) and the Server support; REQ-CON-004 fails only on no
  overlap.

---

## REMOVED

None. (No REQ ID is retired; deprecated behaviors are kept as compatibility.)

## Delta self-check (mini-Analyze)

| Check | Result |
|---|---|
| Contradiction with Constitution | Resolved by the Art. 2 / Art. 5 amendments in this delta. |
| Terminology | New terms (era, dual-era, input request) added to the Constitution vocabulary before use. |
| Traceability | Every modified/added REQ is referenced by an API section above; new API fields cite REQs. |
| Testability | Each new criterion names an observable outcome (header present, outcome code, message sent, tool excluded). |
| Open items for Technical Design | (1) SDK behavior for declining sampling under MRTR; (2) SDK default on broken modern streams vs. REQ-INV-015; (3) sandbox-proxy origin scheme in Reflex; (4) CIMD loopback redirect acceptance. |
