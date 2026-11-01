# Proposal — Align specs with MCP 2026-07-28, MCP Apps 2026-01-26 and `mcp` SDK 2.3

> Status: approved and folded into specs/ (2026-10-30) — archived
> Affected base specs: `specs/constitution.md` v1.0, `specs/prd/mcphost-v0.1.md` v1.0,
> `specs/api/mcphost-api-v0.1.md` v1.0 · Date: 2026-10-30 · Author: Ernesto Crespo

## Problem / motivation

Constitution Art. 2 requires pinning the MCP specification revision *verified against the
official site* during Technical Design. That verification (2026-10-30) found that the
approved PRD and API Spec were written against the handshake-based model of
`2025-11-25`, while the **current** revision is **`2026-07-28`**, which:

1. Removes the `initialize` / `notifications/initialized` handshake: every request carries
   `io.modelcontextprotocol/protocolVersion`, `…/clientCapabilities` and `…/clientInfo`
   in `_meta`; servers MUST implement `server/discover` (SEP-2575).
2. Removes protocol sessions (`Mcp-Session-Id`), the HTTP GET stream, SSE resumability,
   `ping`, `logging/setLevel`, `notifications/roots/list_changed`, `resources/subscribe`;
   adds `subscriptions/listen` and mandatory `Mcp-Method` / `Mcp-Name` / `x-mcp-header`
   handling on Streamable HTTP (SEP-2575, SEP-2243).
3. Replaces server-initiated requests (sampling, elicitation, roots) with **Multi
   Round-Trip Requests**: `tools/call`, `resources/read` and `prompts/get` may return
   `resultType: "input_required"` with `inputRequests` + `requestState`; the client
   retries with `inputResponses` (SEP-2322).
4. Deprecates **Roots, Sampling and Logging** (SEP-2577) — still functional for ≥ 12
   months — and Dynamic Client Registration in favor of **Client ID Metadata Documents**;
   requires `iss` validation (RFC 9207) and issuer-keyed client credentials.

Also verified:
- **MCP Apps** extension `io.modelcontextprotocol/ui`, spec `2026-01-26` (Stable): web
  hosts MUST render Views through a **sandbox proxy on a different origin** whose iframe
  has `allow-scripts allow-same-origin`; tools carry `_meta.ui.resourceUri` and
  `_meta.ui.visibility` (`model` / `app`), with MUST rules on visibility. This contradicts
  the current wording of Constitution Art. 5 ("without `allow-same-origin`").
- **`mcp` SDK 2.3.0** (2026-10-02): `Client` is **dual-era** (probes `server/discover`,
  falls back to `initialize`), runs the MRTR loop itself, exposes `client.listen(...)`,
  validates `iss`, ships an `Apps` extension. Python ≥ 3.11.

Decisions taken with the product owner (2026-10-30):
- **Dual-era** support: prefer `2026-07-28`, fall back to `2025-11-25` handshake.
- Deprecated features kept as **compatibility**: sampling stays `MUST` (consent-gated),
  roots → `SHOULD`, logging limited to stderr + per-request messages, elicitation stays
  `MUST`.
- OAuth registration: **preconfigured client_id → CIMD (published on GitHub Pages) →
  DCR fallback**.
- Changes recorded as this Delta Spec, folded into `specs/` on approval, **before** the
  Technical Design.

## Scope

**Changes:** connection lifecycle and era detection; HTTP request metadata; catalog
refresh via `subscriptions/listen`; MRTR handling for all host-side handlers; logging
and roots semantics; cancellation per transport; OAuth registration and issuer checks;
MCP Apps sandbox, bridge and tool visibility; matching API surface; Constitution Art. 2,
Art. 5 and vocabulary.

**Does not change:** the consent model (rules, scopes, lifetimes, evaluation algorithm),
audit log contents, registry and config sources, `Toolset` alias algorithm, CLI, UI
views, non-goals, stack (except pinning versions).

## Impact

- **Contract consumers:** none yet (pre-implementation). API names are preserved; changes
  are additive fields (`era`, `visibility`, `mode`), one new outcome (`connection_lost`),
  and rewritten behavior notes.
- **Data:** none yet (Data Model not written); the Data Model must include era cache,
  issuer-keyed OAuth client credentials, and MRTR round count per audit record.
- **Infrastructure:** a GitHub Pages site hosting the CIMD document becomes a release
  artifact (stable URL is part of the public contract).
- **Risks:**
  - Under MRTR there is no way to *decline* a sampling input request in-band; the Host
    ends the invocation instead (REQ-HSH-005 modified). To verify against SDK behavior
    in the Technical Design.
  - A broken modern response stream loses the request; auto re-issuing `tools/call`
    could duplicate side effects. Proposed interpretation: reads are re-issued
    automatically, `tools/call` is not (REQ-INV-015). To confirm against SDK defaults.
  - CIMD with loopback redirect URIs depends on authorization servers honoring RFC 8252
    port variance.

## Lifecycle note

Nothing is implemented, so this change is **folded into `specs/` immediately on
approval** (no implementation PR to wait for). Its tasks are not a separate
`tasks.md`; they are generated with the main Tasks artifact. The folder is then moved to
`changes/_archive/`.

## Constitution check

- **Art. 2** — this change *is* the Art. 2 verification; it amends Art. 2 to pin the
  revision set (see delta). Requires amendment entry.
- **Art. 3** — strengthened: MRTR retries stay inside one consented, audited invocation;
  App-originated calls stay consent-gated (stricter than the Apps spec's MAY).
- **Art. 5** — amended: isolation is defined as *distinct origin + sandbox proxy* instead
  of "no `allow-same-origin`", matching the MCP Apps MUSTs while keeping the Host origin
  unreachable from Server content.
- **Exceptions requested:** none.
