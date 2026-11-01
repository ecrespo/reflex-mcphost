# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/) (Constitution Art. 9).

## [Unreleased]

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

### Changed
- Development status classifier: `4 - Beta`.

### Fixed
- Catalog view: the list now re-renders when the catalog changes (explicit dependencies of
  `HostState.catalog_shown` / `catalog_more`).
- Consent center: elicitation, sampling-edit and review-edit forms submit again (the card id
  travels in a hidden field; the browser raised `card_rx_state_ is not defined`).
- Sandbox proxy: Views render in Firefox (Firefox reports the proxy frame's
  `location.ancestorOrigins` as `"null"` for the relay's `referrerpolicy="no-referrer"`
  iframe, which the proxy took for a foreign embedder).

### Known limitations
- MCP App Views render only for invocations started from the Host UI, and only after the
  invocation has ended (no streaming of partial input, no View for agent or App
  invocations).
- A View can still navigate its own frame to an arbitrary URL and so exfiltrate what the
  Host sent it (tool input and result); CSP has no directive that closes this. Residual
  risk, Technical Design §6.1.
- Real Safari is unverified: WebKit is covered by Playwright's WPE build only (Plan R-8; a
  manual macOS pass is planned before `0.1.0`).
- Client ID Metadata Documents against a real authorization server are not yet verified
  (spike S-4 open).
- MCP Apps are disabled in remote mode (`--unsafe-allow-remote`) and whenever the sandbox
  proxy cannot run or `localhost` does not resolve to loopback only.
- External links approved from a View open in a new tab with `noopener` but not
  `noreferrer` (Reflex's `rx.redirect(..., is_external=True)`), so the opened site may see
  the Host UI's origin as referrer.
- Embedding apps must set `state_manager_mode="memory"` in their own `rxconfig.py`:
  Reflex's default disk state manager would persist App HTML, arguments and results (the
  packaged reference app already uses the memory manager).
