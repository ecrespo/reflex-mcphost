# Proposal — Phase 4 preparation (handlers and OAuth)

| Field | Value |
|---|---|
| Date | 2026-10-31 |
| Author | Ernesto Crespo |
| Status | `APPROVED` 2026-10-31 (owner's standing auto-approval, incl. `h11>=0.16` as a direct dependency) · FOLDED 2026-10-31 |
| Sources | Analyze `specs/analyze/2026-10-31-mcphost-v0.1-phase4.md` (P4-01…P4-09, P4-12…P4-14, P4-18); spike `specs/plans/spikes/S-1.md` (c); installed SDK `mcp` 2.3.0 `mcp/client/auth/*` (read 2026-10-31) |
| Affects | PRD 1.5 → 1.6, API 1.7 → 1.8, TD 1.6 → 1.7, DM 1.5 → 1.6, Plan 1.4 → 1.5, Tasks 1.9 (written against this delta) |

## Why

Tasking Phase 4 against the installed SDK surfaced three gaps that block an unambiguous
implementation of MUST requirements, plus several smaller ones:

1. **OAuth flow mechanics (HIGH).** The SDK's `OAuthClientProvider` runs the *whole*
   authorization inline in the `httpx2` auth flow on the first 401 (discovery →
   registration → `redirect_handler` → `callback_handler` → token exchange). TD Flow E says
   the handlers are "parked" until the user starts the flow but does not say how, and the
   API state table says `auth_required → connecting` only after `authorize().wait()`
   succeeds, which cannot hold when the flow runs inside a connection attempt. The SDK also
   refreshes only on *expiry*, not when an unexpired token is rejected (REQ-AUTH-005), and
   a 401 while `ready` has no defined path to `auth_required`.
2. **SDK auth error text carries secret-adjacent values (HIGH, Art. 5).** `OAuthFlowError`
   for a state mismatch embeds both `state` values; `OAuthTokenError("Invalid token
   response: {e}")` embeds a pydantic error that can echo the token response body; the SDK
   logs them with `logger.exception("OAuth flow error")` on `mcp.client.auth.*` loggers,
   which our `SecretRedactionFilter` (attached to `reflex_mcphost.*` loggers only) never
   sees. API §2.2 promises `state` and tokens are never logged.
3. **REQ-HSH-012 has no modern channel (HIGH).** "Reject with a JSON-RPC error" is only
   possible for legacy server-initiated requests; in MRTR an `ErrorData` cannot be placed
   in `inputResponses` (S-1 c). The modern behaviour is unspecified.

Smaller items: `ElicitationRequest` lacks the `attribution` field that API §4.10 rules
promise; `AuthorizationError.reason` and auth `ServerStatus.reason` values are not
enumerated; REQ-AUTH-009 does not say what a 401 on a static-header Server does; the DM
rule on issuer change assumes `TokenStore` sees the issuer, which the SDK `TokenStorage`
protocol does not provide; elicitation / review prompt timeouts, "roots enabled", the
party that opens url-mode URLs and the legacy sampling refusal code are unstated; S-4
gating is mis-scoped (it can no longer block F4-07/F4-08 now that a fixture AS exists).

No requirement is weakened. PRD wording changes only clarify REQ-HSH-012 per era.

## Decisions proposed

- D1 Authorization runs inside a supervisor connection attempt; `authorize()` arms it;
  states `auth_required → connecting → ready | auth_required`; Host-side refresh-once on a
  rejected token; 401 while `ready` → reconnect → `auth_required`.
- D2 Exception-text stripper + redaction filter on the SDK auth loggers; reason codes only.
- D3 Modern bad elicitation schema → client-side abort (`input_handler_failed`,
  `details.reason="invalid_elicitation_schema"`); legacy → JSON-RPC `-32602`.
- D4 `ElicitationRequest.attribution`.
- D5 Reason vocabularies for `AuthorizationError` and auth `ServerStatus.reason`.
- D6 Any static header → no OAuth provider; a 401 then → `failed` (`http_unauthorized`).
- D7 Issuer binding is the SDK's (SEP-2352); `TokenStore` persists `client_info.issuer`.
- D8 Small clarifications (timeouts, roots declaration, URL opening, refusal code).
- D9 S-4 / F0-04 gates F8-07 (release `0.1.0`), not F4-07/F4-08; F4-09 split.

## Alternatives considered

- D1: a separate probe request outside the supervisor to run the flow (rejected: needs a
  synthetic MCP request — legacy `initialize` would create a session to tear down — and
  duplicates transport setup); subclassing the provider's private `_auth_flow` (rejected,
  Art. 2).
- D3: answer `decline` on the modern path (rejected: misrepresents the Consumer's choice
  to the Server).
- D6: start OAuth anyway when headers are non-auth (rejected for v0.1: the PRD text says
  "static headers" without qualification; revisit with a real case).

## Impact

Tasks F4-01, F4-02, F4-05, F4-07, F4-08, F4-09a/b already follow these decisions. If
rejected, those blocks must be revised before implementation.
