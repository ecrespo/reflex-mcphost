# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/) (Constitution Art. 9).

## [Unreleased]

## [0.1.0] - YYYY-MM-DD

First release (date set in the release PR, F8-07b). Highlights: an MCP **host** on both
protocol eras (`2026-07-28`, `2025-11-25`) over stdio and Streamable HTTP, a fail-closed
consent layer with a local audit log, OAuth 2.1 for remote Servers, an agent bridge
(`Toolset`, PydanticAI, LangGraph), an embeddable Reflex UI with the `reflex-mcphost`
reference app and CLI, and MCP Apps (`2026-01-26`) in sandboxed Views.

### Added
- Project scaffold: `pyproject.toml` (hatchling, src layout, extras `pydantic-ai`,
  `langgraph`, `all`; dependency groups `dev`, `test`, `docs`), package skeleton.
- Agent bridge (Phase 5): `Host.toolset(consumer, include=, exclude=)` and
  `reflex_mcphost.toolset` (`Toolset`, `ToolSpec`, `ToolCallOutcome`) — live listing of
  model-visible tools of ready Servers, deterministic collision-free aliases
  (`^[A-Za-z0-9_-]{1,64}$`), FQN / server-id glob filters, calls only through the
  consent-checked `Invoker`, every outcome (including denial) returned as agent text.
- `reflex_mcphost.adapters.pydantic_ai.as_pydantic_ai_toolset` (extra `pydantic-ai`): a
  PydanticAI `AbstractToolset`, re-listed every run step; errors as `ToolFailed`.
- `reflex_mcphost.adapters.langgraph.as_langchain_tools` (extra `langgraph`): async-only
  LangChain `BaseTool`s with the Server's JSON schema; errors as
  `ToolMessage(status="error")`, so a LangGraph `ToolNode` never fails the graph.
- `AuthorizationFlow` and `SamplingProvider` are exported from `reflex_mcphost`.
- `docs/library.md`: using the Host from PydanticAI and LangGraph agents.
- Reflex UI (Phase 6, `reflex_mcphost.ui`): `install(app, host_factory)` (one Host per
  backend as a lifespan task, `ReflexPrompter` bound, Host/Origin loopback guard from the
  Reflex `deploy_url`), the components `servers_panel`, `catalog_browser`,
  `consent_center`, `permissions_table` and `audit_table`, `HostState` and `HostRuntime`;
  embeddable in any Reflex app.
- CLI `reflex-mcphost` (API §6): `run` (also the bare command; `--host`, `--port`,
  `--no-browser`, `--config`, `--unsafe-allow-remote`, `--log-json`; UI and backend on one
  loopback port, exit codes 0/2/3), `servers [--json] [--strict] [--config]` and
  `--version`. The packaged reference app (Servers, Catalog, Permissions, Audit) runs from
  a per-user workspace, never from site-packages.
- E2E tests (marker `e2e`, CI job `e2e`): headless smoke of the built wheel and Playwright
  flows; `playwright` joins the `test` dependency group.
- MCP Apps (Phase 7, MCP Apps `2026-01-26`): an allowed invocation started from the Host
  UI of a tool with `_meta.ui.resourceUri` renders the Server's App as a View after it
  ends (`mcp_app_frame()`, shown by `catalog_browser`; one View per tab). Views run in a
  sandbox proxy on `http://localhost:<proxy_port>` (an origin distinct from the UI's
  `http://127.0.0.1:<port>`, served by a minimal `h11` listener on `127.0.0.1` and `::1`)
  inside an opaque-origin `srcdoc` frame (`sandbox="allow-scripts"`, no device
  permissions) with a CSP built only from the resource's validated `_meta.ui.csp` entries
  (invalid entries are left out, logged and listed in a notice; no `'unsafe-eval'`).
- App bridge: `ui/initialize`, `tool-input` / `tool-result` / `tool-cancelled`,
  `ui/resource-teardown` (`replaced`, `closed`, `server_removed`, `host_closing`; ≤ 2 s),
  `tools/call` through the `Invoker` as the Consumer `app:<server_id>` (consent card and
  audit record per call; only the View's own Server and App-visible tools), `resources/read`,
  `ui/open-link` (`http`/`https` only, after a confirmation showing the full URL),
  `notifications/message` and `size-changed`; every View RPC is logged as
  `method → outcome` in the Server's log stream, never with its content. The relay on the
  UI origin is data-only and accepts messages only from the proxy window it created.
- The Client declares the `io.modelcontextprotocol/ui` extension (both eras) only while
  Views can render (`Host.set_mcp_apps`).
- Resource limits (API §7): no View for a resource without content for its URI, of another
  MIME type, not valid UTF-8 or larger than 5 MiB — a notice names the reason; a 1 MiB
  message limit on the relay.
- `scripts/check_wheel.py` requires the MCP Apps assets
  (`reflex_mcphost/ui/proxy/proxy.html`, `reflex_mcphost/ui/assets/mcp_app_relay.js`) in
  the wheel.
- E2E: MCP Apps on Chromium, Firefox and WebKit (the S-3 isolation and forgery contract);
  the CI `e2e` job installs the three engines.

- Hardening (Phase 8):
  - Server-provided regular expressions (`pattern`, `patternProperties`, `format: regex`
    checks) run only through the `regex` engine with a 50 ms per-match and 250 ms
    per-validation bound; a catastrophic pattern ends the request instead of blocking the
    event loop. New runtime dependency `regex>=2026.9`.
  - The `Invoker` re-resolves a tool after consent (step 4a): a definition that changed
    while the prompt was open denies the call (`definition_changed`, one audit record, no
    second prompt).
  - Every stdio transport close signals the Server's whole process group (POSIX) / Job
    Object (Windows), so grandchildren of a Server do not outlive it.
  - `UnknownToolError` (also a `KeyError`) for an alias not in the current `Toolset`
    listing; `SecretStoreError` when a sign-out cannot delete the stored credentials.
  - Event `consent.rules_changed` (rule ids and change kind, never argument values); the
    Permissions view refreshes in every tab.
  - The UI guard adds `Referrer-Policy: no-referrer` to every response; `install()` refuses
    to start with a state manager other than MEMORY.
  - The sandbox proxy caps concurrent connections at 32 (`503`, `Retry-After: 1`); a View
    that navigates its own frame away is closed.
  - The CLI prunes workspaces of other versions unused for 7 days (and interrupted build
    leftovers after 1 day); only directories it created and marked.
- `docs/user-guide.md` (the reference app) and the embedder rules in `docs/library.md`;
  README and docs examples are executed by the test suite.
- `release.yml` job `verify`: after `publish`, installs the release from PyPI on Linux,
  macOS and Windows and runs `--version`, `servers --json` and (Linux) `run --no-browser`
  until `/ping` answers; it holds no `id-token`.

### Changed
- Development status classifier: `4 - Beta`.
- The `mcp` SDK's own tool-result validation is turned off per session; the Invoker's
  bounded output-schema check is the only one (the SDK's would run Server patterns
  unbounded). A failing result is `protocol_error` with code `output_schema_mismatch` or
  `missing_structured_content`, and its content is dropped.
- The adapters convert only `UnknownToolError` into "Tool is no longer available"; any
  other exception propagates.
- CI: the REQ-coverage gate is strict for every requirement family; a `ResourceWarning`
  fails the test run.

### Fixed
- Catalog view: the list now re-renders when the catalog changes (explicit dependencies of
  `HostState.catalog_shown` / `catalog_more`).
- Consent center: elicitation, sampling-edit and review-edit forms submit again (the card id
  travels in a hidden field; the browser raised `card_rx_state_ is not defined`).
- Sandbox proxy: Views render in Firefox (Firefox reports the proxy frame's
  `location.ancestorOrigins` as `"null"` for the relay's `referrerpolicy="no-referrer"`
  iframe, which the proxy took for a foreign embedder).
- Sign-out forgets the in-memory tokens before deleting the stored ones: a secret store
  that fails during sign-out can no longer leave the Server signed in.
- A refused authorization URL cancels the OAuth flow at once (the Server is back in
  `auth_required` and Authorize works again) instead of waiting for the flow's deadline.
- Links opened from an MCP App View no longer send the Host UI's address as referrer.

### Known limitations
- JSON Schema `format` is not enforced (annotation only) for tool arguments and results.
- Token expiry is unknown after a restart: tokens reloaded from the secret store carry no
  expiry, so the first `401` triggers the one refresh instead of a proactive one.
- A Server that re-prompts an elicitation (invalid content) gets a new consent card with
  the same id; the previous form is not kept open.
- Server media in results: only `data:` images of type PNG, JPEG, GIF or WebP up to
  8 MiB are shown; SVG and other types are a placeholder naming the type.
- `ui/notifications/host-context-changed` is never sent to Views (fixed light theme, no
  container size reports).
- The E2E suite (headless smoke, Playwright, MCP Apps) runs on Linux only; macOS and Windows
  run every other test.
- HTTP+SSE transport fallback (REQ-CON-013) is deferred to v0.2; VS Code `type: "sse"`
  entries do not connect.
- MCP App Views render only for invocations started from the Host UI, and only after the
  invocation has ended (no streaming of partial input, no View for agent or App
  invocations).
- A View that navigates its own frame away is detected and closed, but the navigation
  itself may already have carried what the Host sent the View (tool input and result); CSP
  has no directive that prevents it. Residual risk, Technical Design §6.1.
- Real Safari: WebKit is covered by Playwright's WPE build; the manual Safari pass
  (F8-15, Plan R-8) — status as of the tag: _pending_.
- Client ID Metadata Documents against a real authorization server (spike S-4) — status as
  of the tag: _open_; the DCR / configured-`client_id` fallbacks are tested against the
  fixture authorization server.
- MCP Apps are disabled in remote mode (`--unsafe-allow-remote`) and whenever the sandbox
  proxy cannot run or `localhost` does not resolve to loopback only.
- Tool results are checked against `outputSchema` only by the Host's bounded validator; the
  `mcp` SDK's own result validation is bypassed by design (see Changed).
- Process-group cleanup (POSIX) signals the group by the leader's id after the leader has
  exited; in the short window before the signal the id could in theory be reused.
- If the keyring fails while tokens are saved, the tokens stay in memory for the Host's
  lifetime only (nothing is persisted; sign in again after a restart). A keyring that fails
  while tokens are read is reported as `discovery_failed`.
- A Host data directory that already exists keeps its mode (the files inside are `0600`);
  one the Host creates is `0700`.
- Workspace pruning removes another version's workspace unused for 7 days even if a
  long-running session of that version still uses it.
- `reflex_mcphost.ui.models` reaches `starlette` through the `mcp` SDK, so its import
  contract forbids only `reflex`.
- Embedding apps must set `state_manager_mode="memory"` and run one backend worker (the UI
  refuses to start otherwise; see `docs/library.md`).
