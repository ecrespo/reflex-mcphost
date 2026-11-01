# Delta spec — 2026-11-phase7-impl

> Status: `APPROVED` (owner's standing auto-approval) · folded 2026-11-01 ·
> Source: deviations reported while implementing Phase 7 — batch U (F7-01, F7-08, F7-02,
> F7-06a, commit `648c437`) and its fixture fix (`b0efc95`, `.gitattributes`), batch V
> (F7-03, commit `b832bc4`), batch W (F7-04, F7-05, commit `4ad199b`, including the
> orchestrator's Reflex state-manager fix in `app/web/rxconfig.py`) and batch X (F7-06b,
> F7-07a, commit `4b6bbc8`), all on `develop`.
> Every item was checked against the code (`core/apps/**`, `core/invoker.py`,
> `core/host.py`, `core/connection/manager.py`, `core/handlers/capabilities.py`,
> `ui/{apps_bridge,actions,models,state,install}.py`, `ui/proxy/**`, `ui/assets/**`,
> `ui/components/mcp_app_frame.py`, `app/web/rxconfig.py`, `tests/e2e/**`,
> `.github/workflows/ci.yml`, `scripts/check_wheel.py`, `.gitattributes`); where the
> reported wording and the code differed, the code wins and the item says so ("Code check").
> Constitution unaffected. PRD unaffected (no requirement changes meaning: the CSP grammar
> of item 4 is a stricter reading of REQ-APP-014's "public DNS name", item 30's missing
> `noreferrer` is outside REQ-APP-015's wording, and REQ-NFR-003/004 targets are met).
> Plan unaffected (no task, dependency or estimate change).

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **API 1.14 → 1.15**: §4.5 `append_log` row (time restamped by the ring, parked Servers
  dropped; item 7); §7 — rendering bullets (proxy iframe `referrerpolicy`, View id grammar,
  what the proxy page pins; items 18, 23), `tool-result` `isError` always present (item
  14), the CSP entry grammar exactly as implemented (item 4) and the dropped-entry log line
  (item 6), the proxy page note (no CSP header, `ancestorOrigins` defense in depth; items
  18, 35), a new **Sandbox proxy** route / header / status-code table (items 19, 22, 23),
  JSON-RPC edge cases and log levels (items 5, 11, 12), the method table (`hostContext`,
  `ui/initialize` ordering, `host-context-changed` not sent, teardown reasons and
  semantics, port failures, `resources/read` errors, `ui/open-link` rules incl.
  `noopener` without `noreferrer`, `size-changed` validation; items 3, 8–11, 15, 27–30,
  32, 34), the App `tools/call` reply table (item 9); §8 — `mcp_app_frame()` row (no
  parameter, Close button, item 29), `HostRuntime.apps` row (item 24), the
  `is_openable_url` rule for every caller (item 26) and a normative note: **an embedding app
  MUST use Reflex's MEMORY state manager** (item 31).
- **TD 1.14 → 1.15**: Flow F (steps 1, 3, 8 — the View runs in the tab's `on_load`
  watcher; replace / close / server-removed / disconnect semantics; items 27–29, 34);
  DD-011 (one bullet: MCP App Views in the watcher, select closes; items 27, 29); DD-012
  (bridge ports and package layout, proxy rules as implemented — HTTP/1.1 `Host` errors
  from h11, check order, IPv4-only fallback, page hardening, `ancestorOrigins` check is
  defense in depth only and Firefox masks it as `"null"`; relay asset; items 1, 2, 18–23,
  33, 35); DD-016 (packaged `rxconfig.py` sets the MEMORY state manager; item 31); DD-018
  (contract 7 `ui.proxy` ↛ `reflex`, `reflex_base`; item 21); §5.1 (`core/apps/` files,
  `ui/assets/__init__.py`, `.gitattributes`); §6.1 (rows: forged messages — tested in three
  engines with a mutation check; `ui/open-link` — referrer leak residual; new row "Tab
  state written to disk by Reflex's state manager" — mitigated by MEMORY mode; proxy row;
  items 23, 30, 31, 39); §8 (E2E row: three engines, WebKit host-library workaround,
  observation through `audit_record` rows and a proxy-frame init script, launch failure
  fails; hash-pinned fixtures; items 37–40, `b0efc95`).
- **Data Model 1.8 → 1.9**: §8 — the MCP App Views row states the enforcement (Reflex
  MEMORY state manager), new row "Reflex tab state" (the Catalog invocation panel and the
  other `HostState` view models, memory only by the same setting; item 31); header related
  versions.
- **Plan**: not affected.
- **Tasks 1.15 → 1.16**: header; F7-05 status line (tab state now in memory, item 31);
  execution-log rows for batches U, V and W, batch X row completed (test counts, delta);
  change history. Status: F7-01…F7-05, F7-06a, F7-08 `[x]` (unchanged); **F7-06b `[~]` →
  `[x] 2026-11-01`** — the CI run for `4b6bbc8` (push to `develop`) is fully green,
  including the `e2e` job on Chromium, Firefox and WebKit (`--with-deps` on
  `ubuntu-latest`, no local workaround needed there; gap G-9 closed); F7-07a stays `[~]`
  (`release.yml` PR run pending — it runs only on the PR to `main`, gap G-7); F7-07b stays
  `[!]` (owner action).

## Deferred / known gaps

| # | Gap | Where it is closed |
|---|---|---|
| G-1 | **`ui/open-link` sends a referrer.** `rx.redirect(url, is_external=True)` is `window.open(url, "_blank", "noopener")` in Reflex 0.10.1 (pinned in `tests/contract/test_reflex_surface.py`): `noopener` is guaranteed, `noreferrer` is not, so the opened site sees the UI origin (`http://127.0.0.1:<port>/`) under the default referrer policy. Low impact (a loopback origin, no path secrets), but the prep delta promised `noopener,noreferrer` (item 30). | Phase 8 — F8-01 (consider a `<meta name="referrer" content="no-referrer">` / `Referrer-Policy` on the UI, or a relay-side `window.open` with `noreferrer`) |
| G-2 | **View self-navigation is not detected.** A View can navigate its own frame to send data out; no engine's CSP blocks it (S-3 note 4). TD §6.1 residual risk. | Phase 8 — F8-01 (tear the View down on a second inner `load`, v0.2 candidate otherwise); residual for `0.1.0b1` (CHANGELOG known limits) |
| G-3 | **No concurrent-connection cap on the sandbox proxy.** Each accepted connection is bounded (8 KiB head, 10 s read deadline, `Connection: close`, ≤ 64 KiB / 1 s drain) but their number is not; the listener is loopback-only. | Phase 8 — F8-01 hardening candidate |
| G-4 | **`ui/notifications/host-context-changed` is never sent.** The bridge implements it (`AppBridge.set_host_context`), but the UI has no theme switch (fixed `light`) and does not report container size changes (item 3). | Accepted for v0.1 |
| G-5 | **Six `ResourceWarning`s (unclosed sqlite connection) in the `test_mrtr` tests** during the full suite (seen since Phase 4; tests pass). | Phase 8 — F8-04 (backlog item) |
| G-6 | **Real Safari unverified** (Plan R-8): Playwright WebKit 27.2 (WPE) only. | Owner — manual macOS pass of the S-3 scenarios before `0.1.0` (Phase 8) |
| G-7 | **`release.yml` PR run and the `0.1.0b1` publish.** F7-07a's release preparation (and F6-08's / F5-06a's smoke) has only run locally; `release.yml` runs on PRs that change its paths, `v*` tags and `workflow_dispatch`. F7-07b (tag + PyPI) is an owner action. | F7-07a (push / PR — owner, or the agent on request); F7-07b (owner) |
| G-8 | **E2E (incl. MCP Apps) not run on Windows / macOS.** The E2E suite is Linux-only by design (`/proc`; TD §8, DD-015); the CI `e2e` job is `ubuntu-latest`. | Accepted by design (Phase 8 F8-02 runs informational benchmarks on macOS/Windows only) |
| G-9 | **CI `e2e` job on three engines.** Had run only locally (Chromium 156, Firefox 157, WebKit 27.2 through the S-3 workaround). | **Closed 2026-11-01:** CI run for `4b6bbc8` (push to `develop`) fully green, `e2e` job on Chromium, Firefox and WebKit with `--with-deps` on `ubuntu-latest` (no workaround needed); F7-06b `[x]` |
| G-10 | **The state-manager rule for embedders is not enforced.** API §8 now requires the MEMORY state manager (item 31); `ui.install` does not check `rx.Config.state_manager_mode`, so an embedding app on Reflex's default DISK manager writes tab state (App HTML, arguments, results) under `.states/`. | Phase 8 — F8-05 (embedding docs); F8-01 (consider a WARNING or refusal in `install`) |

## Full deviation list

### Batch U (F7-01, F7-08, F7-02, F7-06a)

1. **API §7 `tools/call` row — CONFIRMED; TD DD-012 — ADDED.** The bridge's port to the
   Invoker is `AppPorts.invoke_tool(name, arguments)` (not `call_tool`: the Art. 3 AST test
   reserves that name for `core.invoker`, `core.mrtr` and the sampling handler). The ports
   are `invoke_tool`, `read_resource`, `confirm_link`, `log`, `send` and the optional
   `size_changed` (`core/apps/bridge.py`). Internal (`core.apps` is not public, API §3.6).
2. **TD DD-012 — ADDED (internal).** `AppBridge(*, server_id, view_id, seed, html,
   host_info, host_context, csp, ports, teardown_timeout_s=2.0)`: keyword-only, the
   resource HTML is passed explicitly, the teardown wait is injectable (tests). Code check:
   `AppBridge.__init__`.
3. **API §7 `host-context-changed` row — MODIFIED.** `AppBridge.set_host_context(**changes)`
   sends `ui/notifications/host-context-changed` once the View is initialized (before that
   it only updates the initial context), but the UI never calls it in v0.1 (fixed theme,
   no container-size reporting) — the row now says "not sent in v0.1"; gap G-4. The
   optional `size_changed` port carries the clamped View height to the frame.
4. **API §7 CSP — MODIFIED; PRD REQ-APP-014 — CONFIRMED.** Entry grammar as implemented
   (`core.apps.csp.valid_entry`): a string of 1–253 characters; scheme `https://`
   (lowercase; `wss://` only in `connectDomains`); then exactly `[*.]host[:port]`, where
   `host` has at least two dot-separated labels (letters, digits, inner hyphens, ≤ 63
   characters each) and its last label starts with a letter (no dotted IPv4); with `*.` the
   remaining host still needs two labels (`*.com` refused); port 1–65535; nothing after
   (no path — not even `/` —, query, fragment, userinfo, quotes, whitespace, `;`, `,`);
   last labels `localhost`, `local`, `localdomain`, `internal`, `arpa`, `invalid`, `test`
   refused. Only the first 32 entries of a list are considered (the rest are dropped);
   duplicates are kept once; a non-list value drops the whole list; at most 64 dropped
   entries are recorded per list plus one `<N more>` record; an entry is recorded cut to
   253 characters (`…`), a non-string as `<type>`. Stricter than the spec's letter and a
   stricter reading of REQ-APP-014's "public DNS name" — no PRD change.
5. **API §7 Transport — ADDED.** JSON-RPC edge cases (`core.apps.messages.parse_message`):
   not JSON (or not UTF-8) → `-32700` with `id: null`; an `id` that is present but not a
   string or integer (incl. `null`, booleans) → `-32600` with `id: null`; `jsonrpc`, method
   or params errors echo the id; a message without `method` must be a response with an id
   and exactly one of `result` / `error` (objects), else `-32600`. An oversized message
   (> 1 MiB) of at most 4 MiB is parsed only to read its id: a request with a valid id gets
   `-32600` "message too large" with that id; anything else oversized is dropped silently.
6. **API §7 Transport, TD Flow F step 2 — ADDED.** Dropped CSP entries are logged once by
   the bridge when the View starts (`csp → dropped <list> entry <entry>`, level `warning`,
   logger `mcp-app`); `ui.actions.open_view` does not log them again.
7. **API §4.5 `append_log` row — MODIFIED.** The ring stamps the time of the append (the
   entry's own timestamp is ignored) and an entry for a parked Server (disabled /
   config_error: no ring) is dropped; `UnknownServerError` for an id not in the registry.
   Code check: `ConnectionManager.append_log` passes only text, level and logger to
   `LogRing.add`.
8. **API §7 `ui/open-link` row — MODIFIED.** One pending confirmation per View: another
   `ui/open-link` meanwhile → `-32000` "a link confirmation is pending". A non-openable URL
   is refused without asking (`-32000` "the link cannot be opened"); a declined one →
   `-32000` "link declined". Openable = `http` / `https` with a host name, ≤ 2,048
   characters, no whitespace or control characters (`core.apps.bridge.is_openable_link`,
   re-checked by `ui.models.is_openable_url` before the confirmation is shown and again
   before the redirect).
9. **API §7 replies — MODIFIED.** A port that raises: `tools/call` → `-32000` "tool call
   failed", `resources/read` → `-32000` "resource read failed"; a `resources/read` result
   over 5 MiB → `-32000` "resource too large" (enforced in the bridge); `tools/call` without
   a string `name` / object `arguments` and `resources/read` without a string `uri` →
   `-32602`; `denied` / `app_scope` → `-32602`; any other non-success outcome →
   `CallToolResult{isError: true}` with the `ToolCallOutcome.text`.
10. **API §7 teardown row — MODIFIED.** During a teardown every View request gets `-32000`
    "the View is closing"; no `ui/resource-teardown` is sent to a View that never sent
    `ui/notifications/initialized`; after the ≤ 2 s wait the in-flight requests' cancel
    scopes are cancelled and the bridge waits ≤ 1 s for them to finish (their invocations
    record `cancelled`); an unknown reason raises `ValueError`; `teardown` is idempotent.
11. **API §7 `ui/initialize` row — MODIFIED.** `ui/initialize` before
    `sandbox-resource-ready` was sent → `-32600` "not initialized"; a second one → `-32600`
    "already initialized". Before the Host's reply only `ui/initialize` and `ping` are
    handled (other requests `-32600`, notifications dropped).
12. **API §7 Transport — MODIFIED.** Log level per View RPC: `info` for `ok` and `success`,
    `warning` for an error reply **and** for every other invocation outcome (`tool_error`,
    `denied`, …); an invalid message is logged as `invalid message → <code>` (or `→
    dropped`); a repeated `sandbox-proxy-ready` as `… → ignored` (`warning`).
13. **TD DD-012 — ADDED (internal).** The `AppBridge` constructor raises `ValueError` for a
    seed whose outcome does not render (`RENDERED_OUTCOMES` = `success`, `tool_error`,
    `cancelled`, `timeout`, `connection_lost`) — a second guard after `ui.models.should_render`.
14. **API §7 `tool-result` bullet — MODIFIED.** `isError` is always present (a boolean:
    `false` for `success`, `true` otherwise) in `tool-result` params and App `tools/call`
    results.
15. **API §7 `ui/initialize` row — MODIFIED.** `hostContext` forces `displayMode: "inline"`
    and `availableDisplayModes: ["inline"]` over whatever the adapter passes (item 32 for
    the rest). `protocolVersion` is `2026-01-26`; `hostInfo` is `{name: "reflex-mcphost",
    version: <package version>}`.
16. **TD §5.1 — CONFIRMED.** Fixture extras (tests only, Art. 1):
    `tests/fixtures/servers/apps_server.py` also serves `ui://bad-utf8`, `ui://missing`,
    a `call_counts` tool (per-tool call counter) and an empty resource-templates list.
17. **Tasks F7-01 — CONFIRMED.** Extra test file `tests/unit/core/apps/test_advertisement.py`;
    `tests/unit/core/connection/test_append_log.py` is named in F7-01's Files.

Other batch U items:

- **TD DD-012 — ADDED.** `core/apps/__init__.py` re-exports nothing (`core.invoker`
  imports `core.apps.constants` while `core.apps.bridge` imports `core.invoker`); callers
  import the submodules.
- **API §4.1 — CONFIRMED.** `Host.set_mcp_apps` has no getter (the spec defines only the
  setter).
- **API §4.8 step 1a — CONFIRMED.** `InvocationError(code="app_scope")` with
  `details.reason` ∈ `foreign_server`, `not_app_visible`.
- **TD §5.1 — CONFIRMED.** `tests/fixtures/apps/`: vendored
  `basic-vanillajs-1.0.1.mcp-app.html` (MIT; npm shasum and file sha256 asserted by the
  tests), hand-written `probe.html`, `NOTICE.md`.

### Fixture fix (`b0efc95`)

- **TD §8 — ADDED.** `.gitattributes` marks `tests/fixtures/apps/*.html` as `-text`:
  Windows checkouts converted the hash-pinned vendored View to CRLF and broke its sha256
  assertion. Hash-pinned fixtures are never line-ending converted.

### Batch V (F7-03)

18. **API §7, TD DD-012 — CONFIRMED / ADDED.** `proxy.html` is served without a CSP header
    and without `frame-ancestors` (the `srcdoc` View would inherit a page CSP; DD-012, API
    §7). Mitigations on the page: the pinned UI origin is checked as both `event.source ===
    window.parent` and `event.origin`, every post to the parent targets the pinned UI
    origin, and a `location.ancestorOrigins` guard (defense in depth only, item 35).
19. **API §7 proxy table, TD DD-012 — MODIFIED.** `Host` errors: an HTTP/1.1 request with
    a missing or repeated `Host` header → `400` (h11 protocol error, before routing); an
    HTTP/1.0 request without `Host` → `403`; any other `Host` than exactly
    `localhost:<proxy_port>` → `403`.
20. **TD DD-012 — MODIFIED.** Binding: IPv4 port 0, then IPv6 `::1` on that port. A machine
    without an IPv6 loopback (`EADDRNOTAVAIL`, `EAFNOSUPPORT`, `EPROTONOSUPPORT`) serves
    `127.0.0.1` only, with an INFO log — MCP Apps stay enabled (no browser there can reach
    `::1` either). `EADDRINUSE` on `::1` is retried (≤ 5 attempts); any other `OSError` →
    MCP Apps disabled with `proxy_bind_failed`.
21. **TD DD-018 — ADDED (contract 7).** Import-linter contract "MCP Apps sandbox proxy is
    Reflex-free": `reflex_mcphost.ui.proxy` ↛ `reflex`, `reflex_base` (7 contracts now).
    The fake Hosts of `tests/unit/ui/test_install.py` / `test_loopback_guard.py` gained
    `set_mcp_apps` (tests only).
22. **API §7 proxy table, TD DD-012 — ADDED.** Check order `Host` → target → path → method →
    body → `view` query. A target that does not start with `/` (absolute-form, `*`) → `404`;
    other paths `404`; other methods `405` with `Allow: GET`; `Transfer-Encoding` or a
    non-zero `Content-Length` → `400`. `check_origin_pair`: a resolver error of any kind for
    one family counts as "family absent". After the response the proxy sends EOF and
    drains ≤ 64 KiB for ≤ 1 s so closing does not reset the response.
23. **API §7 proxy table and rendering bullets, TD DD-012, §6.1 — ADDED.** `view` is the
    sole query parameter, exactly once, `[A-Za-z0-9_-]{1,64}` (else `400`); the UI
    generates it with `secrets.token_urlsafe(12)` (correlation only). Request head ≤ 8 KiB
    (else `431`); a client that has not sent its head within 10 s is closed without a
    response; every response has `Connection: close`, `Cache-Control: no-store`,
    `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`; no `Set-Cookie`;
    nothing about requests is logged. The page: `<meta name="referrer"
    content="no-referrer">`; the inner iframe is always `sandbox="allow-scripts"`,
    `allow=""`, `referrerpolicy="no-referrer"` whatever `params.sandbox` says; only the
    first `sandbox-resource-ready` is honoured; the HTML is parsed with `DOMParser` (no
    script runs) and the CSP `<meta>` becomes the first child of `<head>`; `sandbox-*`
    notifications are never forwarded to the View.
24. **API §8 `HostRuntime` row, TD DD-012 — MODIFIED.** `HostRuntime.apps` is a frozen
    `AppsStatus(enabled, reason, proxy_origin)` with `reason` ∈ `remote_mode`,
    `localhost_not_loopback`, `proxy_bind_failed`; outside a lifespan it is
    `AppsStatus()` (disabled, no reason — the frame then shows reason `not_running`).
    Code check: the report said `_start_apps` runs "first" in the lifespan; it runs after
    the origin is resolved, `host_factory()` and `set_prompter`, and before `Host.start()`
    (DD-012 wording confirmed); the proxy exits after `aclose()`.

Deferred items of batch V: View self-navigation detection (G-2), concurrent-connection cap
(G-3); WebKit / Safari → F7-06b (done locally) and R-8 (G-6).

### Batch W (F7-04, F7-05)

25. **TD Flow F step 2 — ADDED (internal).** `ui.actions.open_view(host, entry)` returns
    `ViewPlan(resource, csp)` or `NotRendered(reason)` (any read exception →
    `read_failed`, logged by type name only); the `AppSeed` (arguments + result) is built
    in `ui.apps_bridge.render_view`; `ui.actions.invoke` returns `(entry, arguments,
    result)` once the invocation ended, `None` when nothing was invoked.
26. **API §8 — MODIFIED (stricter only).** `ui.models.is_openable_url` now also requires
    ≤ 2,048 characters and no whitespace or control characters (API §7 rules) — for every
    caller, including the Phase 6 url-mode elicitation and authorization URLs.
27. **TD Flow F step 3, DD-011 — MODIFIED.** The View runs inside the tab's `on_load`
    watcher (`ViewRegistry.serve_tab`, started by `watch_host`), not in the
    `invoke_tool` background task: Reflex cancels only the `on_load` chain when a tab
    disconnects (pinned). Tab close, navigation and reload therefore discard the View
    without teardown. While a View runs, `ViewSession._watch_registry` follows
    `registry.changed` and tears it down with `server_removed` when its Server is removed.
28. **API §7 teardown row, TD Flow F step 1 — MODIFIED.** `replaced` is sent when a new
    invocation **starts** in the tab, whatever its outcome (`HostState.invoke_tool` closes
    the current View before invoking); `ViewRegistry.open` also replaces for overlapping
    invocations of one tab.
29. **API §7 teardown row, §8 `mcp_app_frame()` row, TD Flow F step 8 — MODIFIED.**
    `closed` is sent by the frame's "Close the App" button **and** when the tab selects
    another catalog entry.
30. **API §7 `ui/open-link` row, TD §6.1 — MODIFIED.** Approve opens the URL with
    `rx.redirect(url, is_external=True)` = `window.open(url, "_blank", "noopener")`
    (Reflex 0.10.1, pinned in `tests/contract/test_reflex_surface.py`): `noopener` is
    guaranteed, `noreferrer` is **not** (the opened site sees the UI origin under the
    default referrer policy). The spec said `noopener,noreferrer`; now a residual risk —
    gap G-1 (Phase 8).
31. **API §8 note, TD DD-016 and §6.1, DM §8 — ADDED (security fix, orchestrator).**
    Reflex 0.10.1's default state manager is DISK: tab state (the Catalog invocation panel
    with arguments and results, `app_outbox` with the resource HTML, …) was written under
    `.states/`, contradicting DM §8. The packaged `rxconfig.py` sets
    `state_manager_mode=StateManagerMode.MEMORY`, safe because the app runs one worker
    (`GRANIAN_WORKERS=1`, DD-016); `tests/unit/app/test_reference_app.py::test_rxconfig_keeps_tab_state_in_memory`
    pins it. Embedding apps MUST set the same in their own `rxconfig.py` (normative API §8
    note; not checked by `install` — gap G-10; docs F8-05).
32. **API §7 `ui/initialize` row — MODIFIED.** `hostContext` = `{theme: "light", platform:
    "web", displayMode: "inline", availableDisplayModes: ["inline"]}`; `locale`, `timeZone`
    and `containerDimensions` are omitted (the spec makes them optional).
33. **TD DD-012 — ADDED.** `ui.assets.relay_library()` links the relay through Reflex's
    shared-asset mechanism (`rx.asset(..., shared=True)`) only inside a Reflex app directory
    (`rxconfig.py` in the working directory); elsewhere it returns the module path without
    touching the file system. Called when the component is built, never at import.
34. **API §7 teardown row, TD Flow F step 8 — MODIFIED.** Shutdown: `ViewRegistry.close_all
    ("host_closing", timeout_s=2.0)` tears every View down in parallel within 2 s **in
    total** (before `Host.aclose()`); Views not finished by then are discarded when their
    watcher is cancelled.

Deferred items of batch W: `host-context-changed` never sent (G-4); six sqlite
`ResourceWarning`s in `test_mrtr` (G-5).

### Batch X (F7-06b, F7-07a)

35. **API §7 proxy note, TD DD-012 — MODIFIED (defect fixed).** Firefox masks
    `location.ancestorOrigins` as `"null"` for an iframe with
    `referrerpolicy="no-referrer"` (the relay sets it), so `proxy.html` returned before
    `sandbox-proxy-ready` and no View rendered in Firefox. Now `"null"` means unknown; the
    proxy stops only when the first ancestor is a different, non-`"null"` origin. The
    ancestor check is defense in depth only — the guards are the pinned `postMessage` target
    and the `event.source` + `event.origin` checks.
36. **TD §5.1 — CONFIRMED.** Fixture tools added to `tests/fixtures/servers/apps_server.py`:
    `slow_view` (as `slow`, with the `ui://probe` View; cancellable), `too_big_view`
    (`ui://too-big`), `bad_csp_view` (`ui://bad-csp`). Code check: the report said
    `apps_server.py`; it lives under `tests/fixtures/servers/`.
37. **TD §8 — ADDED.** E2E harness: one session-scoped Playwright driver (`Engines.get`)
    shared by every test; the existing `browser` fixture returns its Chromium; `ENGINES =
    ("chromium", "firefox", "webkit")`; `WEBKIT_PRELOAD_ENV = "MCPHOST_E2E_WEBKIT_PRELOAD"`;
    `apps_argv()`; an engine that fails to launch **fails** the test, never skips it (the
    suite-level Linux / `uv` skips are unchanged).
38. **Tasks F7-06b — CONFIRMED.** Harness `_select` re-clicks a catalog entry up to 5 times
    (`SELECT_ATTEMPTS`) for a Firefox re-render race after the filter keystroke; no
    security assertion is touched.
39. **TD §8, §6.1 — ADDED.** Observation method: the View transcript comes from an init
    script that runs only in the proxy frame; Invoker calls are counted as `audit_record`
    rows read through a read-only SQLite connection (one per attempt, Art. 3). The forgery
    test was validated by a mutation run (the relay's `event.source` check removed → the
    test fails with four cards).
40. **TD §8 — ADDED.** WebKit runs locally through the S-3 workaround (`apt-get download`
    `libmanette-0.2-0` and `libhidapi-hidraw0`, `LD_PRELOAD` via
    `MCPHOST_E2E_WEBKIT_PRELOAD`, `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1`); CI
    installs the host libraries with `playwright install --with-deps`.

### Measurements and results (Tasks execution log)

- Batch U (`648c437`): 1,732 tests green. Batch V (`b832bc4`): 1,816. Batch W
  (`4ad199b`): 2,008. Batch X (`4b6bbc8`): 2,010 non-E2E tests + 42 E2E (2,052 collected;
  code check: `pytest --collect-only` — 2,052 total, 42 with `-m e2e`).
- MCP Apps E2E: 9 tests per engine — Chromium 156 9/9, Firefox 157 9/9, WebKit 27.2 9/9;
  three consecutive runs flake-free; mutation check as in item 39. Strict REQ gate incl.
  `APP` 135/135.
- REQ-NFR-003: warm start max 3.56 s (budget < 10 s). REQ-NFR-004: p95 97.9 ms (budget
  < 200 ms) — Linux, local, Chromium headless.

### CI (orchestrator report)

- The CI run for `4b6bbc8` (push to `develop`) is fully green, including the `e2e` job with
  Chromium, Firefox and WebKit (`playwright install --with-deps` on `ubuntu-latest`; the
  local WebKit workaround is not needed there). **Tasks F7-06b — MODIFIED status `[~]` →
  `[x] 2026-11-01`** (gap G-9 closed). F7-07a stays `[~]`: `release.yml` runs only on the
  PR to `main` (gap G-7). F7-07b stays `[!]` (owner).
