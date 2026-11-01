# Delta spec — 2026-10-phase4-impl

> Status: `APPROVED` (owner's standing auto-approval, 2026-10-31) · folded 2026-10-31 ·
> Source: deviations reported while implementing Phase 4 batches M (F4-01…F4-05) and N
> (F4-06…F4-08, F4-09a, F4-10), merged into `develop` at `2dcb2c2`, plus two
> security-review corrections (items 17 and 18). Every item was checked against the code
> (`core/handlers/*`, `core/auth/*`, `core/connection/{supervisor,manager,inflight,transports}.py`,
> `core/host.py`, `core/registry.py`, `core/catalog.py`, `core/invoker.py`); where the
> reported wording and the code differed, the code wins and the item says so
> ("Code check"). Constitution unaffected. PRD: REQ-AUTH-006 and REQ-AUTH-010 reworded
> (item 18 changes *when* a refused callback aborts the flow); no other requirement
> changes meaning.

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **PRD 1.6 → 1.7**: RF-AUTH REQ-AUTH-006 (a callback with a foreign `state` is refused and
  the flow keeps waiting; the 5-min deadline aborts) and REQ-AUTH-010 (a callback with a bad
  `iss` is refused, never redeemed; the flow ends `iss_mismatch` at its deadline) — item 18.
- **API 1.8 → 1.9**: §2.2 loopback listener rules and credential binding (items 17, 18);
  §4.3 roots-only changes are `unchanged` (item 2); §4.5 errors column (`UnknownServerError`
  on `authorize` / `sign_out` / `set_log_level` / `set_roots`, item 23), `authorize`
  outcome when the attempt ends without a reason (item 21), `set_roots` delivery and
  default roots (items 3, 4), state row `ready | HTTP 401 …` with no direct
  `ready → auth_required` edge (item 15); §4.10 re-prompt cap (item 6); §4.14 refusal codes,
  no-provider path, `@runtime_checkable`, provider source (items 7, 8, 13).
- **TD 1.7 → 1.8**: Flow E rewritten, step numbers kept (internal exception names, discovery
  first, listener authenticity, port reservation, lock-free fast path, refresh rules, flow
  outcome, 401 while ready — items 14, 15, 17–22); E.4 row (item 18); Flow C legacy note (item 10); DD-003 consequences (items 1,
  9); §3.2 `ConnectionManager.roots_writer` seam (item 5); §5.1 `connection/inflight.py`
  (item 12); §5.3 internal OAuth exceptions (items 11, 14); §6.1 D2 row and OAuth row
  (items 16, 17); §6.2 OAuth rows (item 17).
- **DM 1.6 → 1.7**: §3 `oauth:{server_id}` entry gains `resource`; rules rewritten
  (resource binding, issuer binding for every client-id source, discovery first, URL change
  on reconcile, dead tokens after a rejected refresh) — items 17, 22.
- **Plan**: not affected (no task, dependency or estimate changes; the gaps below land in
  existing tasks).
- **Tasks 1.9 → 1.10**: execution-log row for batches M and N; F4-09b untouched (in
  progress).

## Deferred / known gaps

| # | Gap | Where it is closed |
|---|---|---|
| G-1 | **S-4 real-AS CIMD acceptance** (F0-04, still blocked on access to a real AS advertising `client_id_metadata_document_supported`). CIMD is tested only against the fixture AS (F4-09a). | F0-04 → gates F8-07 / release `0.1.0` (Plan R-9) |
| G-2 | **Owner action:** enable GitHub Pages for the repository with source "GitHub Actions" so the CIMD URL (`oauth_client_metadata_url`, REQ-AUTH-012) is live; the F4-10 workflow is ready but cannot publish until then. | Owner, before F0-04 / F8-07 |
| G-3 | `token_expiry_time` is unknown after a restart: tokens reloaded from the store (discovery first) carry no expiry, so the SDK's proactive refresh cannot run and the first refresh after a restart is triggered by a `401` (refresh once, REQ-AUTH-005). Persisting the absolute expiry in the entry is a candidate (DM §3 `v` bump). | Phase 8 hardening (evaluate) |
| G-4 | No dedicated test for "`401` while `ready`, refresh succeeds → the Server is `ready` again through `reconnecting`" (the refresh-once replay inside an established session). Refresh-once is tested on the connect path and sign-out on both eras (F4-08). | F4-09b auth interop, else F8-03 |
| G-5 | `ResourceWarning`s (unclosed `sqlite3` connections) of unknown origin appear in the full test suite; no test fails and no production path is known to leak. | Phase 8 (F8-03 resilience) — locate and close |
| G-6 | REQ-NFR-020 is only partly covered: a raising or timing-out `SamplingProvider` and a failing `[sampling] provider` import are tested; keyring failure and crash storms are not. | F8-03 |

## Full deviation list

### Batch M (F4-01…F4-05)

1. **TD DD-003 consequences — ADDED.** A modern rebuild of a **stdio** Server starts a new
   Server process: a new `Client` needs a new transport, so it is a new stdio session (the
   state still stays `ready` and no `server.state_changed` is emitted). Code check: matches
   the supervisor module docstring.
2. **API §4.3 rules — MODIFIED.** A difference in `roots` only is **not** `changed`: the
   Server is reported `unchanged` and is not reconnected (roots are read live from the
   registry on every roots request; the session that must receive
   `notifications/roots/list_changed` stays alive, Analyze P4-13). A `config_error` flip
   still marks it `changed`. Code check: `registry._differs`.
3. **API §4.5 `set_roots` — MODIFIED.** Legacy `notifications/roots/list_changed` is sent
   only when the live Client **declared** roots; otherwise the change is logged and takes
   effect on the next `reconnect()`. A delivery failure is logged; the roots stay saved.
4. **API §4.5 `set_roots` — ADDED (CONFIRMED D8).** By default a Server has no roots, so
   roots are not declared: a modern `roots/list` input request ends
   `undeclared_input_request` (REQ-HSH-044).
5. **TD §3.2 — ADDED.** `ConnectionManager.roots_writer` is a Host-injected seam that
   persists a Server's roots (`ServerRegistry.set_roots`); `set_roots` raises
   `RuntimeError` when the manager is not attached to a Host. Code check: declared on the
   class with default `None` and **assigned on the instance** by `Host` (the report said
   "class-level attribute set by the Host").
6. **API §4.10 rules — ADDED.** Form elicitation re-prompts after invalid content at most
   `MAX_ATTEMPTS = 10` times (prompts in total), then answers `cancel` to the Server.
7. **API §4.14 — MODIFIED.** Legacy refusal codes: a **denial** →
   `ErrorData(-1, "Sampling request rejected by host")`; any other refusal (provider error
   or timeout, audit unavailable) → `ErrorData(-32603, "Sampling request failed")`. Modern:
   denial → `denied/sampling_denied`, other reasons → `input_handler_failed`.
8. **API §4.14 — ADDED.** No provider at request time → the request is refused as a denial
   without asking anyone and audited `denied` / `sampling_denied` with
   `decision_source = "not_evaluated"`.
9. **TD DD-003 consequences — ADDED.** The Catalog's supervisor lease protocol exposes
   `inflight` (legacy attribution) and `rebuilding`; when a connection ends for a modern
   capability rebuild the Catalog keeps its entries (no stale marking, no
   `catalog.changed`). Code check: `catalog._rebuilding`.
10. **TD Flow C legacy note — MODIFIED.** The legacy sampling-denied override
    (`denied/sampling_denied`) also applies when the call itself ends `protocol_error` (the
    Server answers the refused sampling with a JSON-RPC error), not only on a returned
    `CallToolResult`; it never overrides `timeout` or `connection_lost`. Code check:
    `invoker` lines guarded by `not deadline.expired`.
11. **TD §5.3 — CONFIRMED.** No new exception in `core/errors.py` (public surface
    unchanged). Code check: Phase 4 adds two *internal* `MCPHostError` subclasses in
    `core/auth/oauth.py` (item 14), not exported.
12. **TD §5.1 — MODIFIED.** The in-flight registry lives in `core/connection/inflight.py`
    (`InFlightRegistry`, `InFlightEntry`), not `core/inflight.py`.
13. **API §4.14 / §5.2 — ADDED.** `SamplingProvider` is `@runtime_checkable` (a configured
    factory's result is checked with `isinstance`). `[sampling]
    provider` is honored only from the user-level or an explicitly loaded `mcphost.toml`;
    a project file's `[sampling]` is ignored with `project_setting_ignored` (REQ-REG-014).
    The factory is imported in a worker thread; an import or factory failure is logged and
    disables sampling (REQ-NFR-020, gap G-6). A constructor `sampling_provider` wins.

### Batch N (F4-06…F4-08, F4-09a, F4-10)

14. **TD Flow E / §5.3 — MODIFIED.** The internal signals are named
    `AuthorizationDeferredError` (parked `redirect_handler`) and
    `AuthorizationAbortedError(reason)` (Host abort before redemption), `Error` suffix per
    ruff N818; both live in `core/auth/oauth.py` and never escape the controller (mapped to
    `ServerStatus.reason` / `AuthorizationError.reason`).
15. **API §4.5 state table / TD Flow E step 7 — MODIFIED.** CONFIRMED: a transport
    `fatal_reason` → `failed` with that reason (`http_unauthorized`, D6); the auth reason
    (`client_id_required`) recorded on the transport is propagated to `ServerStatus.reason`.
    ADDED: a `401` on a live session moves `ready → reconnecting → auth_required` (two
    `server.state_changed` events); there is no direct `ready → auth_required` edge.
16. **TD §6.1 D2 row — MODIFIED.** The exception-text stripper on the SDK auth loggers
    **drops** records whose exception is `AuthorizationDeferredError` (control flow, not an
    error); other records keep only the exception type name.
17. **DM §3 / API §2.2 / TD Flow E — MODIFIED (security review).** Normative:
    - The `oauth:{server_id}` entry MUST carry `resource`: the RFC 8707 canonical URL of the
      Server it was written for (SDK `resource_url_from_server_url`: lowercase scheme and
      host, no fragment). An entry whose `resource` differs from the Server's current
      canonical URL MUST read as absent (neither its tokens nor its client info are used)
      and is replaced by the next write; an entry without `resource`, with an unknown `v`
      or with invalid content MUST be treated as unreadable (re-authorization).
    - Tokens MUST be bound to the issuer for **every** client-id source (configured, CIMD,
      DCR), not only through the SDK's client-info binding: the entry's `issuer` is the
      issuer current when tokens or client info are written (discovered metadata, else
      `client_info.issuer`) and is never replaced by `None`; tokens are discarded when
      client info of another issuer is stored, when an issuer becomes known for tokens
      stored without one, and on read whenever the expected issuer is known and differs.
    - Discovery first (REQ-AUTH-011): stored tokens MUST be withheld (kept, not presented)
      until the SDK has discovered the authorization server and the issuer check passed. The
      first attempt after a restart is therefore unauthenticated; when it reaches the parked
      redirect, the released tokens are loaded and the request is replayed once with them
      (no prompt).
    - On a registry reconcile, a `changed` definition whose canonical URL is no longer the
      entry's `resource` MUST get `oauth:{id}` deleted (with its `secret_index` row).
    - A configured `client_id` is returned before anything stored.
18. **API §2.2 / TD Flow E step 4, E.4 / §6.1 — MODIFIED (security review); PRD
    REQ-AUTH-006/010 — MODIFIED.** Normative: the loopback listener MUST complete only on an
    **authentic** callback — `GET /callback` whose `state` matches ours (constant-time
    comparison) and which either carries an AS `error` or carries a valid `iss` (SDK
    `validate_authorization_response_iss`) and a `code`. Any other `/callback` MUST get
    `400` + the failure page and the listener MUST keep waiting (a local page or process
    can neither end nor hijack the flow — login CSRF); other paths MUST get `404` (also
    every request after the authentic callback), other methods `405`, an oversized head
    `431`. A callback with our `state` refused for its `iss` is remembered and the flow
    ends `iss_mismatch` at its deadline; foreign-`state` callbacks are ignored (the flow
    ends `timeout` unless an authentic callback arrives). Callback values (`code`, `state`,
    query) MUST be registered for redaction only once the callback is authentic, so a
    forged request cannot poison redaction. Code check: `state_mismatch` stays in the
    `AuthorizationError` vocabulary but is no longer produced by the listener path.
19. **TD Flow E — ADDED.** Lock-free fast path: the SDK provider serializes whole requests
    on one lock, so requests that need no auth work (the Server never answered `401`, or a
    valid token after discovery) are sent without it (bearer attached by the controller);
    a `401` falls back to the SDK flow and the request is replayed there. The in-memory
    token is marked expired (to force the refresh-once) only if it is still the one that
    was sent — a concurrent request may already have refreshed it, in which case the
    request is just replayed.
20. **TD Flow E step 1 — ADDED.** While parked with no concrete redirect yet, the controller
    reserves a free loopback port (bind, read, close) so a DCR registration made during
    discovery records a port that `authorize()` can re-bind.
21. **API §4.5 `authorize` / TD Flow E step 5 — ADDED.** The flow's watcher reports
    `flow_error` when the attempt leaves `connecting` without reaching `ready` and without a
    recorded reason; a stop (`disconnected` without a failure), an unknown Server or a
    closed Host → `cancelled`.
22. **TD Flow E step 6 / DM §3 — ADDED.** A refresh response other than `200` deletes the
    stored tokens (`TokenStore.drop_tokens`; client info is kept) so dead tokens are never
    reloaded. Code check: the trigger is any non-`200` refresh response, not only an
    explicit rejection.
23. **API §4.5 errors — MODIFIED; Tasks — CONFIRMED.** Host wiring of `authorize` /
    `sign_out` landed with F4-07; `authorize`, `sign_out`, `set_log_level` and `set_roots`
    raise `UnknownServerError` for an unknown id. Code check: one test,
    `tests/integration/test_host.py::test_phase4_methods_reject_unknown_ids`, covers all four.

## Addendum (F4-09b, 2026-10-31)

24. ADDED TD §6.1: the seeded redaction test (REQ-NFR-010) found two leaks, fixed test-first:
    the SDK logs whole JSON-RPC payloads at DEBUG on `mcp.client.streamable_http`/`sse`/
    `session` → `SdkPayloadStripper` + redaction filter on every non-auth SDK logger
    (`protect_sdk_loggers()`, called in `Host.start()`); jsonschema issue messages echoed
    argument values into `InvocationError.details.issues` → `core.schema` strips the value.
25. MODIFIED CI: the coverage gate (Art. 7) enforces core ≥ 90 % / total ≥ 80 % (was
    report-only since Phase 1); the strict REQ step covers REG,CON,CAT,CNS,INV,HSH,AUTH.
