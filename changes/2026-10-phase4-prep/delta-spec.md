# Delta Spec — 2026-10-phase4-prep

> Status: `PROPOSED` · Source: Analyze `specs/analyze/2026-10-31-mcphost-v0.1-phase4.md`.
> Each item names its target document and section and whether the text is **ADDED**,
> **MODIFIED** or **CONFIRMED**.

## D1 — OAuth flow inside the connection attempt (P4-01, P4-07) · HIGH

**TD Flow E — MODIFIED** (replaces steps 1–6):

```
1. HTTP Servers without static headers get one OAuthController owning the SDK
   OAuthClientProvider (TokenStore storage, client_metadata_url = oauth_client_metadata_url).
   Parked by default: redirect_handler raises AuthorizationDeferred, so a 401 + auth
   metadata ends the attempt and the supervisor sets auth_required (no browser, no listener).
2. ConnectionManager.authorize(id): only in auth_required; a running flow is returned as is.
   Bind LoopbackCallbackServer on 127.0.0.1:<ephemeral> (DCR clients first try their
   registered port), set the provider's redirect URI to that port, arm the handlers and
   kick the supervisor: state auth_required → connecting. The SDK runs discovery
   (RFC 9728 → RFC 8414), registration (configured → CIMD → DCR) and calls
   redirect_handler(url) → AuthorizationFlow(authorization_url=url) is returned.
3. UI opens the URL (CLI prints it); callback_handler awaits the listener (5-min deadline
   for the whole flow).
4. GET /callback?code&state&iss: the Host checks state (constant time, against the state in
   the authorization URL) and iss (SDK validate_authorization_response_iss on the
   discovered metadata) BEFORE returning AuthorizationCodeResult to the SDK; mismatch, AS
   error, timeout or cancel → AuthorizationAborted(reason): nothing redeemed, verifier
   dropped, listener closed.
5. The SDK exchanges the code (PKCE S256, resource indicator); TokenStore.set_tokens
   persists oauth:{server_id} (issuer field from client_info.issuer); the same attempt
   continues → ready; flow.wait() returns. Any failure → auth_required, wait() raises
   AuthorizationError(reason).
6. Refresh: expiry → the SDK's proactive refresh. A rejected, unexpired token → the
   controller replays the request once with the in-memory token marked expired so the SDK
   refresh runs; a second rejection or a failed refresh → auth_required (REQ-AUTH-005).
7. A 401 while ready marks the observed transport closed → reconnecting → the attempt's
   401 → auth_required (existing transition).
```

**API §4.5 — MODIFIED**: transition row `auth_required | authorize().wait() OK |
connecting` becomes `auth_required | authorize() | connecting` (the flow runs inside the
attempt) and a new row `connecting | authorization aborted / failed / timed out |
auth_required`. `authorize` behaviour: "Only valid in `auth_required`; returns once the
authorization URL is known; a second call during a flow returns the same flow."
`AuthorizationFlow.authorization_url: str | None` (`None` when the attempt succeeded
without a browser step, e.g. after a refresh; `wait()` then returns at once).

## D2 — SDK auth logs and exception text (P4-02) · HIGH

**TD §6.1 — ADDED row**: "OAuth secrets in SDK error text/logs — `OAuthFlowError`
(state mismatch) and `OAuthTokenError` (invalid token response) messages can contain
`state` or token-response content and are logged by `mcp.client.auth.*` with
`logger.exception` → `Host.start()` attaches the `SecretRedactionFilter` and an
exception-text stripper (message replaced by the exception type name, `exc_info`/`args`
dropped) to every `mcp.client.auth*` module logger (list pinned by the SDK contract test);
`AuthorizationError.reason` and `ServerStatus.reason` carry reason codes only, never SDK
message text — REQ-AUTH-008, Art. 5".
**TD §7.1 — ADDED**: the same sentence in short; the library still installs no handlers.

## D3 — REQ-HSH-012 per era (P4-03) · HIGH

**PRD RF-HSH — MODIFIED REQ-HSH-012**: "IF the requested schema uses constructs outside
the spec's allowed subset, THEN THE SYSTEM SHALL reject the request without rendering —
legacy: return a JSON-RPC error (`-32602`) to the Server; modern: not retry the
originating request and end it with outcome `protocol_error` (error code
`input_handler_failed`, `details.reason = "invalid_elicitation_schema"`), or raise
`InputHandlerError` for a catalog read."
**API §4.8 step 6 — MODIFIED**: `protocol_error` list adds `input_handler_failed`
(already emitted since F3-10) with `details.reason` ∈ `invalid_elicitation_schema`,
`invalid_elicitation_url`, `provider_error`, `provider_timeout`, `audit_unavailable`.
**TD Flow C step 6 — ADDED**: "a rejected elicitation schema or URL is a client-side abort
on modern (like a refused sampling), a JSON-RPC error on legacy."

## D4 — `ElicitationRequest.attribution` (P4-04) · MEDIUM

**API §4.10 — MODIFIED** `ElicitationRequest` fields: add `attribution:
Literal["exact", "unattributed"] = "exact"` (REQ-HSH-047; informs the UI only —
elicitation is not consent-gated).

## D5 — Reason vocabularies (P4-05) · MEDIUM

**API §3.4 / §4.5 — ADDED**: `AuthorizationError.reason` ∈ `not_auth_required`,
`state_mismatch`, `iss_mismatch`, `access_denied` (AS `error` on the callback),
`timeout`, `cancelled`, `client_id_required`, `registration_failed`, `token_error`,
`discovery_failed`, `flow_error`. `ServerStatus.reason` while `auth_required`:
`auth_required` (default) or `client_id_required` (REQ-AUTH-003: the UI/CLI shows "set
`[servers.<id>.oauth] client_id` in mcphost.toml").

## D6 — Static headers and 401 (P4-06) · MEDIUM

**API §4.5 — ADDED** failure reason `http_unauthorized` and transition `connecting |
HTTP 401 on a Server with static headers | failed` (no retry until `reconnect()` or a
definition change). **TD §3.2 OAuth row — ADDED**: "an OAuth provider is attached only to
HTTP Servers whose definition has no static headers (REQ-AUTH-009)."

## D7 — Issuer binding (P4-08) · MEDIUM

**DM §3 rules — MODIFIED** first rule: "`TokenStore` implements the SDK `TokenStorage`
protocol. Issuer binding is enforced by the SDK (SEP-2352: stored client info whose
`issuer` differs from the discovered one is dropped together with the tokens, then
re-registered); `TokenStore` persists `client_info.issuer` as the entry's `issuer` and, on
`set_client_info` with a different issuer, discards the stored tokens too. CIMD client ids
are issuer-portable (the SDK's rule); a configured `client_id` carries no issuer."
**TD Flow E old step 5 — MODIFIED** terminology: "keyed by server id, bound to issuer"
(was "keyed by (server_id, issuer)").

## D8 — Clarifications (P4-09, P4-10, P4-11, P4-12) · LOW

- **API §4.10 — ADDED**: `elicit()` and `review_sampling_response()` are bounded by
  `consent_timeout_s`; timeout → `cancel` / rejected.
- **TD DD-003 — MODIFIED**: "`list_roots_callback` iff the Server's effective roots are
  non-empty" (replaces "roots configured or roots enabled"); a change between empty and
  non-empty triggers the modern rebuild like `set_prompter`; modern `set_log_level` uses the
  same rebuild (Client `log_level`).
- **API §4.10 — ADDED**: "Core never opens a browser: url-mode elicitation and OAuth URLs
  are opened by the prompter/UI (CLI prints them)."
- **API §4.14 — MODIFIED**: legacy refusal is `ErrorData(code=-1, message="Sampling request
  rejected by host")` (the code the SDK uses for user rejection, S-1 c); an audit write
  failure before the provider call refuses the sampling (Art. 3).

## D9 — S-4 scope and F4-09 split (P4-14, P4-18) · INFO

**Plan §3 Phase 4 — MODIFIED**: F4-09 → F4-09a "Fixture OAuth AS and protected fixtures"
(0.75 d, deps F1-10, F4-10; before F4-07) and F4-09b "Auth interop and full redaction
test" (0.75 d, deps F4-08, F4-05); F4-07 depends on F4-06, F4-09a, F2-07; F4-05 also
depends on F4-03, F4-04. **Plan §4** spike gates: `S-4 → F8-07` (was `S-4 → F4-07/08`).
**Plan §3 Phase 8 — MODIFIED** F8-07 depends on F0-04 as well: "real-AS CIMD acceptance
with loopback port variance verified, or the documented DCR/manual fallback recorded in
`S-4.md`". **Plan §5 — ADDED** risk: "No real AS accepts the CIMD document with a portless
loopback redirect → mitigation: DCR and configured `client_id` paths (tested in F4-08);
release note."
**Tasks**: already applied in 1.9 (F0-04 status, F4-09a/F4-09b, batches M/N/N′).

## CONFIRMED (no edit)

- API §2.2 registration order (configured → CIMD → DCR) matches PRD REQ-AUTH-003 and the
  SDK (`should_use_client_metadata_url`, DCR fallback with `application_type="native"`).
- PKCE S256, `state`, RFC 9207 `iss` and RFC 8707 `resource` are produced/validated by the
  SDK provider (Art. 2); the Host adds only the pre-redemption checks of D1 step 4.
- DD-009 `h11` listener holds; `h11` becomes a direct dependency (already locked
  transitively through `httpcore2`, no new download).
