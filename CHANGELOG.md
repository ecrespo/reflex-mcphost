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

### Fixed
- Catalog view: the list now re-renders when the catalog changes (explicit dependencies of
  `HostState.catalog_shown` / `catalog_more`).
- Consent center: elicitation, sampling-edit and review-edit forms submit again (the card id
  travels in a hidden field; the browser raised `card_rx_state_ is not defined`).
