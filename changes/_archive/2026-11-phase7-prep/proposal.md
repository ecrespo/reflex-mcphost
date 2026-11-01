# Proposal — Phase 7 preparation (MCP Apps and `0.1.0b1`)

| Field | Value |
|---|---|
| Date | 2026-11-01 |
| Author | Ernesto Crespo |
| Status | `APPROVED` 2026-11-01 (owner's standing auto-approval for spec deltas through Phase 8) · FOLDED 2026-11-01 |
| Sources | Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase7.md` (P7-01…P7-16); spike `specs/plans/spikes/S-3.md`; MCP Apps `2026-01-26` (`github.com/modelcontextprotocol/ext-apps`, `specification/2026-01-26/apps.mdx`, read through Context7 on 2026-11-01: default CSP, CSP construction from `resources/read` `contents[]._meta.ui`, `HostCapabilities`, `ui/notifications/tool-cancelled {reason}`, `ui/resource-teardown {reason}`, visibility rules); code at `75b78e1` (`core/invoker.py` publishes `invocation.started` before consent; `ui/actions.invoke`; `core/handlers/capabilities.CapabilityPlan`; `ConnectionManager.logs` read-only; `core/apps/` and `ui/proxy/` stubs); `pyproject.toml` (`uvicorn` only in the `test` group; `h11` a runtime dependency) |
| Affects | PRD 1.8 → 1.9, API 1.13 → 1.14, TD 1.13 → 1.14, Data Model 1.7 → 1.8, Plan 1.7 → 1.8 (all folded 2026-11-01); Tasks 1.15 §Phase 7 is written against this delta. Constitution unchanged |

## Why

Tasking Phase 7 against the specs, the code and the pinned MCP Apps revision surfaced four
gaps that put a MUST requirement or a Constitution article at risk, and several open
implementation choices:

1. **App scope rejections bypass the audit (HIGH, Art. 3).** API §7 / Flow F.7 have the UI
   bridge reject an App's `tools/call` for a foreign or app-hidden tool with `-32602`
   before the Invoker. Such an attempt produces no `AuditLog` record, although Art. 3
   requires exactly one per attempt, and the check lives in the UI layer instead of core.
2. **When a View is rendered is contradictory and unscoped (HIGH, REQ-APP-001/006/009).**
   Flow F mounts the frame on `invocation.finished`, but REQ-APP-009 sends
   `tool-cancelled` to a View, which presupposes one. REQ-APP-001 says "a tool … is
   invoked" without saying by whom: events and the audit log carry no arguments or results
   (Art. 5), so the UI can only render Views for invocations it started itself. Rendering
   before consent would hand arguments the user then denies to untrusted code, which can
   send them out by navigation (S-3 residual risk).
3. **CSP injection through declared domains (HIGH, Art. 5).** "CSP built from the declared
   domains" does not bound what a declared entry may contain: a Server can declare `*`,
   `https:`, `'unsafe-eval'`, `data:`, a `;`-separated directive or a loopback origin (the
   Host UI or the proxy).
4. **The backend → View channel is unspecified (HIGH, Art. 5).** The JS relay runs on the
   Host UI's origin. If the backend reached it by building script source (e.g.
   `rx.call_script` with the message embedded), Server-provided content could execute on
   the UI origin.
5. Smaller items: no resource or message size limits; the proxy needs an HTTP server that
   is not a runtime dependency (`uvicorn` is test-only); the origin-pair start-up check lives
   in the CLI, which embedding apps do not use; the extension is advertised even by a
   library Host that cannot render Views; the bridge cannot write the Server's log stream
   (REQ-APP-012); App call results and `ui/open-link` URL schemes are unspecified; View
   lifecycle triggers; relay filter by source **and** origin (S-3); stale port names in TD.

No requirement is weakened. REQ-APP-001 and REQ-APP-007 are narrowed to what the Host can
honour (UI-started invocations; advertisement only where Views can be rendered); three
requirements are added (REQ-APP-013 resource limits, REQ-APP-014 CSP entry validation,
REQ-APP-015 `ui/open-link`).

## Decisions proposed

- D1 The **Invoker** enforces the App scope (new pipeline step 1a): for an `app:<server_id>`
  Consumer the tool must belong to that Server and be `app`-visible, else `denied`
  (`app_scope`), audited, no prompt. The bridge maps it to `-32602`.
- D2 A View is rendered only for an invocation **started from the Host UI in that tab**
  whose consent allowed it, **after** it ends with `success` / `tool_error` (→ `tool-result`)
  or `cancelled` / `timeout` / `connection_lost` (→ `tool-cancelled {reason}`). One View per
  tab. Agent and App invocations never render Views.
- D3 `build_csp` accepts only `https://` origins (plus `wss://` in `connectDomains`) with a
  public host name, optional leading `*.` and port; everything else is dropped, logged and
  shown in the notice. The default policy is the one S-3 tested (stricter than the spec's:
  no `'self'` sources, `form-action 'none'` added).
- D4 Backend → relay is **data only** (a per-View outbox prop, acknowledged by sequence
  number); never script source, never Server content in the UI DOM. The relay accepts only
  `event.source === proxyWindow && event.origin === <proxy origin>`.
- D5 Limits: UI resource ≤ 5 MiB, one matching `contents` item, MIME parsed; View messages
  ≤ 1 MiB, no batches; ≤ 4 concurrent View requests; View `resources/read` ≤ 5 MiB.
- D6 Sandbox proxy = `h11` + `anyio` listener (like DD-009) on one ephemeral port bound on
  `127.0.0.1` and `::1`, `Host` checked, `GET /proxy` only, pinned UI origin embedded
  server-side; started in the Host lifespan. The `localhost`-is-loopback check moves into
  the lifespan; on failure (or in remote mode) Apps are disabled, the Host still starts.
- D7 `Host.set_mcp_apps(enabled)` (default off) controls the extension advertisement;
  `ui.install` enables it once the proxy runs. REQ-APP-007 becomes `optional`.
- D8 `ConnectionManager.append_log(server_id, entry)` (Host-internal seam) lets the bridge
  write the Server's log stream: method + outcome, never params.
- D9 App call results: `success`/`tool_error` → `CallToolResult`; App-scope `denied` →
  `-32602`; any other outcome → `CallToolResult{isError: true}` with the Toolset text.
  `ui/open-link`: `http`/`https` with a host only, after a confirmation showing the URL.
- D10 View lifecycle: teardown reasons (`replaced`, `closed`, `server_removed`,
  `host_closing`), in-flight View calls cancelled at teardown, nothing but `ui/initialize`
  and `ping` before the `ui/initialize` response; `_meta.ui.domain` ignored, `permissions`
  never granted, `prefersBorder` honoured. Glossary App / View / `app:<server_id>`.

## Alternatives considered

- D1: keep the check in the bridge and write an audit record from the UI — the `AuditLog`
  has no write API by design (API §4.9, Art. 3), and a second writer would break "exactly
  one record per attempt" (rejected).
- D2: (a) mount the View at `invocation.started` and send `tool-input` before consent —
  leaks arguments the user may deny (rejected); (b) mount after consent while the call
  runs — needs a new core "consent allowed" event; deferred to v0.2 (live Views with
  progress); (c) render Views for agent invocations — the UI has no arguments or results
  for them (Art. 5) and no conversation surface in v0.1.
- D3: refuse the whole resource on one invalid entry — harsher than needed; dropping an
  entry can only tighten the policy, which the spec allows ("MAY further restrict").
- D4: `rx.call_script` with `json.dumps` escaping — correct only if every call site escapes
  perfectly; a data channel removes the class of bug (rejected).
- D6: Starlette under `uvicorn` — `uvicorn` would become a runtime dependency (Constitution:
  no new dependencies without need); Reflex's `granian` serves the backend as a process,
  not as an embeddable server (rejected).
- D7: keep the advertisement unconditional — a headless library Host would invite
  app-only tools nobody can call (rejected).

## Impact

Tasks F7-01…F7-08 (Tasks 1.15) follow these decisions; Plan Phase 7 grows from 8.5 d to
10 d (new F7-08 Invoker App-scope step; F7-06 split into a/b; F7-07 split into a/b).
`0.1.0b1` publishing is an **owner action** (F7-07b, `[!]`), gated on the same PyPI
prerequisites as F5-06b. Owner-dependent items are listed in the Analyze report.
