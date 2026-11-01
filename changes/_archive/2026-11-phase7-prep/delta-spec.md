# Delta Spec — 2026-11-phase7-prep

> Status: `APPROVED` (owner's standing auto-approval, 2026-11-01) · folded 2026-11-01 ·
> Source: Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase7.md`.
> Each item names its target document and section and whether the text is **ADDED**,
> **MODIFIED** or **CONFIRMED**. Code changes implied here land in the Phase 7 tasks.
> Protocol facts are from MCP Apps `2026-01-26` (`specification/2026-01-26/apps.mdx`, read
> through Context7 on 2026-11-01); assumptions are marked **Assumption**.

## Applied edits

- **PRD 1.8 → 1.9**: REQ-APP-001 (D2), REQ-APP-006 (D2), REQ-APP-007 (D7) MODIFIED;
  REQ-APP-013 (D5), REQ-APP-014 (D3), REQ-APP-015 (D9) ADDED; Constitution check lines.
- **API 1.13 → 1.14**: §4.1 `set_mcp_apps` (D7); §4.5 `append_log` (D8); §4.8 pipeline step
  1a and `app_scope` (D1); §7 rewritten in place — rendering trigger (D2), CSP (D3), relay
  channel (D4), limits (D5), proxy (D6), message table rows `tools/call`, `resources/read`,
  `ui/open-link`, `ui/notifications/size-changed`, `ui/resource-teardown`,
  `ui/notifications/tool-cancelled`, lifecycle (D9, D10); §8 `mcp_app_frame()` and
  `HostRuntime` rows (D2, D6); §11 RF-APP row.
- **TD 1.13 → 1.14**: §3.1 diagram ports (P7-13); §3.2 Sandbox proxy row (D6); Flow F
  rewritten (D1, D2, D4, D6, D10) and row F.7 (D1); DD-012 decision, glossary and
  consequences (D3–D7, D10); DD-015 E2E browsers (F7-06b); §5.1 files; §6.1 rows (D1, D3,
  D4, D5, D6, D9); §8 E2E row.
- **Data Model 1.7 → 1.8**: §8 in-memory rows (D2, D5); header related versions refreshed.
  No schema change: App-scope denials use `decision_source = 'not_evaluated'`,
  `outcome = 'denied'`, `error_code = 'app_scope'` (free text column).
- **Plan 1.7 → 1.8**: Phase 7 table (F7-01…F7-08, F7-06a/b, F7-07a/b), duration 8.5 → 10 d,
  Done criteria; metadata versions.
- **Tasks 1.15**: Phase 7 written against this delta.

## D1 — The Invoker enforces the App scope (P7-01) · HIGH

**API §4.8 pipeline — ADDED** row between steps 1 and 2:

| Step | Failure → outcome | REQ |
|---|---|---|
| 1a. App scope (only when `consumer.kind == app`): the FQN's Server must equal `consumer.name` and the tool's `visibility` must include `"app"` | `denied`, `error.code = "app_scope"`, `details.reason` ∈ `foreign_server`, `not_app_visible`; no consent prompt; one audit record (`decision_source = not_evaluated`, `error_code = app_scope`) | REQ-APP-004, REQ-APP-008 |

**API §7 `tools/call` row — MODIFIED**: "The bridge builds the FQN `<server_id>::<name>`
from the View's bare `name` (a View can only name tools of its own Server) and calls
`Invoker.call(fqn, arguments, consumer=Consumer.app(server_id))` (consent + audit — stricter
than the spec's MAY). The Invoker enforces the App scope (§4.8 step 1a); a `denied` result
with `error.code == "app_scope"` is answered with JSON-RPC `-32602`. Other results: D9."

**TD Flow F step 7 and row F.7 — MODIFIED**: see the Flow F text in D2; F.7 "App asks for a
foreign / app-hidden tool → Invoker step 1a `denied` (`app_scope`), audited, no prompt →
JSON-RPC `-32602`, logged (REQ-APP-012)".

**TD §6.1 "App calling other Servers' / hidden tools" — MODIFIED** mitigation: "FQN built
from the View's own Server; App scope enforced in the Invoker (step 1a, audited); consent +
audit for every App call".

## D2 — Which invocations render a View, and when (P7-02) · HIGH

**PRD REQ-APP-001 — MODIFIED**:

> WHEN an invocation started from the Host UI of a tool with `_meta.ui.resourceUri`
> (`ui://…`) ends after consent allowed it, with outcome `success`, `tool_error`,
> `cancelled`, `timeout` or `connection_lost`, THE SYSTEM SHALL fetch that resource with
> `resources/read` from the same Server and render it if its MIME type is
> `text/html;profile=mcp-app`.

**PRD REQ-APP-006 — MODIFIED**:

> WHEN the View is initialized, THE SYSTEM SHALL send `ui/notifications/tool-input` exactly
> once and then, for an outcome `success` or `tool_error`, `ui/notifications/tool-result`
> exactly once (always after tool-input).

**API §7 Rendering — MODIFIED** (trigger bullet replaced):

- Trigger: an invocation started from the Host UI (`human:ui`, through `catalog_browser`)
  of a `ToolEntry` with `ui_resource_uri`, once it has **ended** with `result.consent.allowed`
  and outcome `success` / `tool_error` (→ `tool-result`) or `cancelled` / `timeout` /
  `connection_lost` (→ `tool-cancelled` with `reason` = the outcome name). Any other outcome,
  or no allowed consent, renders no View (the result panel shows the outcome as today).
- Invocations by agents (`Toolset`) and by Apps never render a View: the UI holds no
  arguments or results for them (events and the audit log carry none, Art. 5) and v0.1 has
  no conversation surface. An App's own `tools/call` result goes back only to the calling
  View.
- One View per tab: it belongs to the tab's current Catalog result panel; a new invocation in
  the tab tears the previous View down (D10).
- `tool-input` params: `{"arguments": <the invocation's arguments>}`; `tool-result` params:
  the MCP `CallToolResult` shape rebuilt from the `InvocationResult` (`content` blocks as the
  SDK serializes them, `structuredContent` when set, `isError: true` for `tool_error`).
- Rendering after completion is a v0.1 simplification the spec allows (the Host chooses when
  to render); a live View during the call (needs a core "consent allowed" signal) is a v0.2
  candidate.

**TD Flow F — MODIFIED** (replaces steps 1–8):

```
1. Catalog invoke (human:ui) of a tool with ui_resource_uri ends; result.consent.allowed and
   outcome ∈ {success, tool_error, cancelled, timeout, connection_lost} (D2). Else: no View.
   The tab's previous View, if any, is torn down first (reason "replaced").
2. backend: Catalog.read_resource(server_id, ui_uri, consumer=human:ui) → pick the contents
   item whose uri == ui_uri → parse MIME (text/html;profile=mcp-app) → text or UTF-8 blob,
   ≤ 5 MiB (REQ-APP-013) → csp = core.apps.build_csp(item._meta.ui.csp) (REQ-APP-014) →
   notice if external domains declared or entries dropped (REQ-APP-011).
3. mcp_app_frame renders the proxy iframe: src = http://localhost:<proxy_port>/proxy?view=<view_id>,
   sandbox="allow-scripts allow-same-origin", allow="".
4. proxy → parent: ui/notifications/sandbox-proxy-ready → relay (source + origin check) →
   Reflex event → AppsBridge.
5. AppsBridge → outbox → relay → proxy: ui/notifications/sandbox-resource-ready {html, csp,
   sandbox: "allow-scripts"}; the proxy inserts the CSP <meta> first in <head> and sets the
   inner iframe's srcdoc — the View.
6. View ui/initialize → reply (hostInfo, hostCapabilities, hostContext); View
   ui/notifications/initialized → tool-input → tool-result | tool-cancelled{reason}
   (REQ-APP-003/006/009).
7. View tools/call {name} → AppsBridge builds <server_id>::<name> → Invoker.call(consumer=
   Consumer.app(server_id)) → step 1a App scope → consent + audit → CallToolResult or
   JSON-RPC error back to the View (REQ-APP-004/008, D9).
8. Teardown (replaced / closed / server_removed / host_closing) → ui/resource-teardown
   {reason}, wait ≤ 2 s → in-flight View calls cancelled → iframe removed (REQ-APP-010).
   Tab disconnect → state discarded without teardown (the browser already destroyed it).
```

**Data Model §8 — ADDED** row: "MCP App Views | `ui.apps_bridge` (per tab) | one View per
tab; resource ≤ 5 MiB | arguments, result content, resource HTML, CSP, bridge state, outbox;
memory only, never persisted or logged; dropped on teardown / tab disconnect | RF-APP".

## D3 — CSP entries are validated; default policy pinned (P7-03) · HIGH

**PRD REQ-APP-014 — ADDED** (unwanted):

> IF an entry of a resource's `_meta.ui.csp` lists is not an `https://` origin (or a
> `wss://` origin in `connectDomains`) whose host is a public DNS name — optionally with one
> leading `*.` label and a port, and without path, query, userinfo, quotes, whitespace, `;`
> or `,` — THEN THE SYSTEM SHALL leave that entry out of the policy, record it in the
> Server's log stream, and list it in the View's external-domains notice.

IP literals, `localhost`, `*.localhost`, a bare `*` and scheme-only sources (`https:`) are
never valid. At most 32 entries per list and 253 characters per entry are considered; the
rest is dropped the same way.

**API §7 CSP — MODIFIED**: `build_csp(meta) -> CspResult(policy, external, dropped)`; the
policy is, with `R` = valid `resourceDomains`, `C` = valid `connectDomains`, `F` = valid
`frameDomains`, `B` = valid `baseUriDomains`:

```
default-src 'none'; script-src 'unsafe-inline' R; style-src 'unsafe-inline' R;
img-src data: blob: R; font-src data: R; media-src data: blob: R;
connect-src C|'none'; frame-src F|'none'; base-uri B|'self'; object-src 'none';
form-action 'none'
```

It never contains `'unsafe-eval'`, `'strict-dynamic'`, nonces, hashes or `'self'` outside
`base-uri`. **Assumption (recorded):** the spec's default policy uses `'self'` for scripts,
styles, images and media; in DD-012's layout `'self'` would name the proxy origin, which
serves nothing but `proxy.html`, so omitting it only restricts further (the spec: "Host MAY
further restrict but MUST NOT allow undeclared domains"); S-3 rendered the reference App
with this policy minus `form-action`. `form-action 'none'` closes form submission as an
exfiltration path (navigation is still open — §6.1 residual risk).

**TD §6.1 — ADDED** row: "CSP injection through declared domains (`*`, `https:`, keywords,
`;`, loopback origins) | `build_csp` host-source grammar; invalid entries dropped, logged,
shown in the notice; never `'unsafe-eval'` | REQ-APP-014, Art. 5".

## D4 — Backend → relay is a data channel (P7-04, P7-12) · HIGH

**TD DD-012 — ADDED** (decision bullet after the relay bullet):

> The relay is the only code on the Host UI's origin that touches MCP App messages, and it
> handles them as data. Backend → relay: the `mcp_app_frame` component receives an `outbox`
> prop (a state var: list of `{seq, message}`; `message` a JSON string) and posts each new
> entry to the proxy window with target origin `http://localhost:<proxy_port>`, then
> acknowledges `seq` with a Reflex event; acknowledged entries are removed. The backend
> never builds script source from Server-provided content (no `rx.call_script`,
> `rx.script` or `eval` with message data), the resource HTML is never inserted into the UI
> DOM (it travels as a string inside `sandbox-resource-ready`), and no state var holding
> Server content is bound to `dangerouslySetInnerHTML`, `src` or `href` on the UI origin.
> Relay → backend: one Reflex event `(view_id, message JSON string)` per message accepted
> under `event.source === proxyWindow && event.origin === "http://localhost:<proxy_port>"`
> (S-3 checked both); the relay drops messages whose serialization exceeds 1 MiB; shape
> validation happens in Python.

**TD §6.1 "Forged postMessage" — MODIFIED** mitigation: "Relay accepts only `event.source
=== proxyWindow && event.origin === <proxy origin>`; the proxy accepts only `window.parent`
+ pinned UI origin and `inner.contentWindow`; JSON-RPC shape validated in Python".
**TD §6.1 — ADDED** row: "Server content executing on the Host UI origin through the relay |
Data-only outbox; no script built from Server content; HTML never in the UI DOM | Art. 5,
REQ-APP-002".

## D5 — Resource and message limits (P7-05) · MEDIUM

**PRD REQ-APP-013 — ADDED** (unwanted):

> IF the UI resource read for a View has no `contents` item for the requested URI, a MIME
> type other than `text/html;profile=mcp-app`, content that is not valid UTF-8, or content
> larger than 5 MiB, THEN THE SYSTEM SHALL NOT render it and SHALL show a notice naming
> the reason.

**API §7 Limits — ADDED**:
- MIME: parsed as a media type — `text/html` (case-insensitive) with parameter
  `profile=mcp-app`; other parameters (e.g. `charset=utf-8`) ignored. Content: the item's
  `text`, or its `blob` decoded from base64 as UTF-8; ≤ 5 MiB (5,242,880 bytes, UTF-8).
  Notice reason codes: `missing_content`, `mime_mismatch`, `invalid_encoding`, `too_large`,
  `read_failed`.
- View → Host messages: a JSON object (batches → `-32600`), `jsonrpc == "2.0"`, `method` a
  string ≤ 128 characters, `id` a string or integer when present, `params` an object when
  present; serialized ≤ 1 MiB (else `-32600` if an id can be read, dropped otherwise).
- At most 4 View requests (`tools/call`, `resources/read`) in flight per View; more →
  `-32000` "too many concurrent requests".
- View `resources/read`: own Server only; a result larger than 5 MiB → `-32000` "resource
  too large".
- `ui/notifications/size-changed`: width follows the container; height clamped to
  80…4,000 px.

**TD §6.1 — ADDED** row: "View floods consent prompts or the websocket | ≤ 4 in-flight View
requests; messages ≤ 1 MiB; resources ≤ 5 MiB | REQ-APP-005/013".

## D6 — Sandbox proxy listener and the origin-pair check (P7-06, P7-08, P7-14, P7-15) · MEDIUM

**TD DD-012 decision — MODIFIED** (proxy bullet replaced):

> The sandbox proxy is a minimal HTTP/1.1 listener (`h11` over `anyio`, the approach of
> DD-009 — no ASGI server: `uvicorn` is only a test dependency and Reflex's `granian` serves
> the backend as a process), owned by the Host lifespan of `ui.install`: started before
> `Host.start()`, stopped after `aclose()`. It binds one ephemeral port on `127.0.0.1` and on
> `::1` (`IPV6_V6ONLY`; bind IPv4 port 0, then IPv6 on that port; up to 5 attempts on
> `EADDRINUSE`) and is addressed as `http://localhost:<proxy_port>` while the Host UI is
> `http://127.0.0.1:<port>` — different **host** and **port**, so the proxy's
> `allow-same-origin` cannot reach the UI's origin, storage or cookies. It serves only
> `GET /proxy` (query `view` = the View id, `[A-Za-z0-9_-]{1,64}`, correlation only, not a
> secret) with `200 text/html; charset=utf-8`, `Cache-Control: no-store`,
> `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, no `Set-Cookie` and no
> CSP header (S-3). `Host` must be exactly `localhost:<proxy_port>` (else `403`); other paths
> `404`, other methods `405`; request head ≤ 8 KiB, no body, `Connection: close`, 10 s read
> deadline. The page embeds the pinned UI origin server-side (the guard's resolved origin,
> JSON-encoded), never from the query or `document.referrer`.

**TD DD-012 consequences — MODIFIED** ("the CLI refuses to start unless …" replaced):

> Before `Host.start()` the lifespan checks the origin pair: `localhost` resolved with
> `AF_INET` and with `AF_INET6` returns only loopback addresses for every family that
> resolves (`AF_UNSPEC` alone can hide `::1`), the UI origin's host is `127.0.0.1` (not
> remote mode), and the proxy listener is bound. If any check fails, MCP Apps are disabled:
> the extension is not advertised (D7), `HostRuntime.apps` records the reason
> (`localhost_not_loopback`, `remote_mode`, `proxy_bind_failed`), `mcp_app_frame` shows
> "MCP Apps are disabled: <reason>" and a WARNING is logged; the Host and the rest of the UI
> start normally.

**API §8 `HostRuntime` row — MODIFIED**: adds `apps` (`AppsStatus`: `enabled: bool`,
`reason: str | None`, `proxy_origin: str | None`).

## D7 — Advertisement only where Views can render (P7-07) · MEDIUM

**PRD REQ-APP-007 — MODIFIED** (type `optional`):

> WHERE MCP Apps rendering is enabled (the Reflex UI enables it once its sandbox proxy
> runs), THE SYSTEM SHALL declare the extension `io.modelcontextprotocol/ui` with
> `mimeTypes: ["text/html;profile=mcp-app"]` in its client capabilities, and SHALL NOT
> declare it otherwise.

**API §4.1 — ADDED** member `def set_mcp_apps(self, enabled: bool) -> None` and behavior
row: "`set_mcp_apps(enabled)` turns the MCP Apps extension declaration on or off (default
off, REQ-APP-007). It is part of the capability inputs: modern Servers see it on the next
request (capability rebuild, DD-003), legacy Servers from their next `initialize`
(`reconnect()`). `ui.install` calls it before `start()`; callable before or after
`start()`." The capability-inputs row reads "computed from `prompter`,
`sampling_provider`, roots and the MCP Apps flag (`set_mcp_apps`)".

**TD DD-012 consequences — ADDED**: `CapabilityPlan` gains `mcp_apps: bool`; when set, the
Client gets `extensions` = `mcp.client.advertise("io.modelcontextprotocol/ui", {"mimeTypes":
["text/html;profile=mcp-app"]})` (pinned by the SDK contract test).

## D8 — The bridge writes the Server's log stream (P7-09) · MEDIUM

**API §4.5 — ADDED** member `def append_log(self, server_id: ServerId, entry: LogEntry) ->
None` (sync) and row: "Host-internal seam used by the MCP Apps bridge (public for tests):
appends to the Server's ring buffer after the redaction filter and publishes `server.log`.
`UnknownServerError`" (REQ-APP-012, REQ-HSH-030).

**API §7 Transport — MODIFIED**: "Every View-initiated RPC is logged to the Server's log
stream (REQ-APP-012) as a `LogEntry` with logger `mcp-app`, level `info` (`warning` for an
error reply) and text `<method> → <outcome>` (`ok`, JSON-RPC error code, or the invocation
outcome) — never params, arguments, results or HTML. A View's `notifications/message` is
appended with logger `mcp-app`, its level and its `data` rendered as text, cut to 4 KiB."

## D9 — App call results and `ui/open-link` (P7-10, P7-11) · MEDIUM

**API §7 — ADDED** (after the message table): App `tools/call` result mapping:

| `InvocationResult.outcome` | Reply to the View |
|---|---|
| `success`, `tool_error` | `CallToolResult` (`content`, `structuredContent` when set, `isError: true` for `tool_error`) |
| `denied` with `error.code == "app_scope"` | JSON-RPC error `-32602` |
| any other | `CallToolResult{isError: true, content: [text]}` with the text of API §4.12 `ToolCallOutcome.text` (never SDK message text, never argument values) |

**PRD REQ-APP-015 — ADDED** (event):

> WHEN a View requests `ui/open-link`, THE SYSTEM SHALL open the URL in a new browser tab
> only if it is an `http` or `https` URL with a host name and the user approves a
> confirmation showing the full URL; otherwise it SHALL reply with an error.

**API §7 `ui/open-link` row — MODIFIED**: "URL checked with `ui.models.is_openable_url`
(`http`/`https` with a host, ≤ 2,048 characters); a confirmation in the View's frame area
shows the full URL as plain text; approve → new tab (`noopener,noreferrer`), result `{}`;
decline or a non-openable URL → `-32000`. The confirmation is answered in the tab that
shows the View." `hostCapabilities` advertises `openLinks`, `serverTools`,
`serverResources`, `logging` and `sandbox: {permissions: {}, csp: <the validated lists>}`.

**TD §6.1 — ADDED** row: "View opening `javascript:`, `data:`, `file:` or host-less URLs via
`ui/open-link` | `is_openable_url` + human confirmation showing the full URL | REQ-APP-015".

## D10 — View lifecycle and unused metadata (P7-16, P7-17) · LOW

**API §7 Lifecycle — ADDED**:
- Before the Host has answered `ui/initialize`, only `ui/initialize` and `ping` are handled;
  other requests → `-32600` "not initialized", notifications are dropped.
- Teardown triggers: a new invocation in the tab (`replaced`), the user closing the panel
  (`closed`), the Server leaving the registry (`server_removed`), Host shutdown
  (`host_closing`); `ui/resource-teardown {reason}`, wait ≤ 2 s for the response, then
  cancel the View's in-flight requests (their invocations end `cancelled`) and remove the
  iframe. A tab disconnect discards the View's state without teardown.
- A Server going non-`ready` keeps the View; its calls return `server_unavailable` (D9).
- `ui/notifications/tool-cancelled` params: `{"reason": <outcome name>}`.
- `_meta.ui.domain` is ignored (one proxy origin for all Views in v0.1), `_meta.ui.permissions`
  is never granted (REQ-APP-011), `_meta.ui.prefersBorder` is honoured (default: border).

**TD DD-012 — ADDED** glossary: "**App** — a Server's UI resource (`ui://…`, MIME
`text/html;profile=mcp-app`); **View** — one rendered instance of an App in the sandbox
(one per tab in v0.1); the Consumer **`app:<server_id>`** names the Server whose View
asks, not the App." No Constitution vocabulary change (these are MCP Apps terms).

**TD §3.1 diagram, DD-012 — MODIFIED** (P7-13, drift): the reference app box reads
"Reflex frontend + backend (127.0.0.1:<port>, one port — DD-016)"; DD-012 says
`http://127.0.0.1:<port>` instead of `<frontend_port>`.

**API §8 `mcp_app_frame` row — MODIFIED**: "`mcp_app_frame()` | component | The tab's MCP
App View (sandbox proxy iframe + relay) for its current Catalog invocation; rendered by
`catalog_browser` in the result panel; shows the external-domains notice, the
not-rendered / disabled notices and the `ui/open-link` confirmation | REQ-APP-001…015".
(The `invocation_id` parameter is dropped: one View per tab, bound to the tab's invocation;
the name was not yet exported.)
