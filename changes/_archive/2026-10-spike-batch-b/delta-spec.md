# Delta Spec — spike batch B (S-3, S-5)

## PRD (1.3 → 1.4)

**MODIFIED REQ-NFR-011** — The reference app's backend SHALL reject requests whose `Host`
is not `127.0.0.1:<port>` or whose `Origin` (when present) is not `http://127.0.0.1:<port>`,
where `<port>` is the single port serving UI and backend (DNS-rebinding protection;
`localhost` is reserved for the MCP Apps sandbox proxy); the `403` body SHALL tell the user
to open `http://127.0.0.1:<port>`.

**MODIFIED REQ-NFR-003** — add: "Each start runs Reflex's production frontend build; the
first start of a new version may exceed the target and is not measured."

## API (1.3 → 1.4)

- §6 `run`: `reflex-mcphost run [--host 127.0.0.1] [--port 3000] [--no-browser]
  [--config PATH] [--unsafe-allow-remote]`; UI and backend share `--port`.
- §7 Rendering: the proxy page is served without a CSP stricter than the View's
  (`srcdoc` inherits it; the View policy is delivered by `<meta>`); the default CSP never
  includes `'unsafe-eval'`. Proxy ↔ View: accept only `event.source ===
  inner.contentWindow`, post with target `"*"` (the View origin is opaque). Proxy ↔ Host UI:
  accept only `event.source === window.parent && event.origin === <pinned UI origin>`, post
  with that origin.

## Technical Design (1.2 → 1.3)

- **DD-011**: watcher started from the page `on_load` (every page needing it), de-duplicated
  per client token in a process-local registry (not Reflex state); `on_mount` chains are
  not cancelled on disconnect (S-5). R-5 retired for the single-worker case.
- **DD-012**: add the S-3 messaging/CSP rules above; the CLI startup check resolves
  `localhost` with both `AF_INET` and `AF_INET6` and requires every address to be loopback;
  real Safari unverified (Plan R-8).
- **DD-016**: `reflex run --env prod --backend-host 127.0.0.1 --frontend-port P
  --backend-port P` (one port), `GRANIAN_WORKERS=1`, `REFLEX_CHECK_LATEST_VERSION=false`;
  packaged `rxconfig.py` sets `telemetry_enabled=False` and loopback `api_url`/`deploy_url`;
  never `--env dev`. Workspace at `…/app/<version>-<content-hash>/`. Start command
  `uvx --compile-bytecode reflex-mcphost`; "no writes to site-packages" excludes
  `__pycache__/*.pyc`. Every warm start includes a Vite build (grows with UI size).
- **DD-017**: single port; `Host == 127.0.0.1:<port>`, `Origin ∈ {absent,
  http://127.0.0.1:<port>}`; the notice is the 403 body (the frontend cannot run when
  refused); `cors_allowed_origins` = the same origin; F7-06 opens `localhost` and expects
  403 + notice text.
- **§6.1** new row: "MCP App View navigating its own frame to send data out — not blocked by
  CSP in any engine (S-3); residual risk, Views come only from Servers the user connected;
  v0.2 candidate: tear down on a second inner `load`."

## Plan (1.1 → 1.2)

- F0-03, F0-05 done; F6-08 uses a single `--port`; F7-06 expects 403 + notice on
  `localhost`; new risk **R-8** "real Safari differs from WebKit (CFNetwork/ITP)" —
  mitigation: manual pass of `spikes/S-3` scenarios on macOS before 0.1.0.

## Tasks (1.2 → 1.3)

- F0-03, F0-05 `[x] 2026-10-31`; F1-11 also delivers `tests/contract/test_reflex_surface.py`
  (S-5 static items); execution-log rows for batch B and Phase 1.
