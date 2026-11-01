# Proposal — Resolve Analyze findings of 2026-10-31

> Status: approved and folded into specs/ (2026-10-31) — archived
> Affected base specs: `specs/constitution.md` v1.1, `specs/prd/mcphost-v0.1.md` v1.1,
> `specs/api/mcphost-api-v0.1.md` v1.1, `specs/technical/mcphost-architecture.md` v1.0,
> `specs/data-model/mcphost-schema.md` v1.0, `specs/plans/mcphost-v0.1.md` v1.0,
> `specs/tasks/mcphost-v0.1-tasks.md` v1.0
> Date: 2026-10-31 · Author: Ernesto Crespo

## Problem / motivation

The Analyze gate (`specs/analyze/2026-10-31-mcphost-v0.1.md`) returned **FIX CRITICALS
FIRST**: 2 CRITICAL (Constitution Art. 6 and Art. 8 violations), 6 HIGH (gaps an agent
would fill by guessing, three of them security-relevant), 8 MEDIUM, 8 LOW. Writing this
delta surfaced one more gap, **A-25**: a project-level `mcphost.toml` (attacker-controlled
in a cloned repository) can set `[host]`, `[sources]` and `[sampling] provider` (an
importable factory path), which lets a repository change security settings or load code.

## Scope

**Changes:** source precedence order; async/threaded I/O rules in `core`; loopback guard
for the reference app; project-Server trust fingerprint; consent rule ranking; Consumer
attribution and errors for input requests outside `tools/call`; MRTR round counting;
secret-store layout in the Tech Design; audit "exactly one record" wording; backoff cap;
state-machine rows; per-REQ priority; NFR measurement definitions; documented rug-pull
scope; plan/task coverage fixes; editorial fixes; restriction of project-level
`mcphost.toml` (A-25).

**Does not change:** product scope, non-goals, stack, pinned revision set, public API
names (only additions: one keyword parameter, one exception, one config-problem code, one
`TrustRequest` field), database tables (one `CHECK` changes), phase structure and
estimates of the plan.

## Impact

- **Contract consumers:** none yet (pre-implementation). API additions are backward
  compatible; `HostConfig.load()` keeps its signature but is documented as
  pre-event-loop only.
- **Data:** `trusted_server.fingerprint` prefix changes from `sha256:` to
  `hmac-sha256:` — no data exists yet, so `0001_initial` is simply written with the new
  `CHECK`.
- **Risks:** the precedence order between Cursor and VS Code files (A-01) and the
  consent ranking (A-05) are product choices; the proposed values are defaults the owner
  may flip during review without affecting anything else in this delta.

## Lifecycle note

Nothing is implemented, so — as with `2026-10-mcp-2026-07-28` — this change is **folded
into `specs/` immediately on approval** and the folder moved to `changes/_archive/`. No
separate `tasks.md`: the only task changes (A-15, A-16, A-23, A-24) are edits to the
existing Plan and Tasks artifacts, listed in the delta.

## Constitution check

- **Art. 3** — amended wording (A-09) states the one case without an audit record
  (`audit_unavailable`, no effect); consent ranking made unambiguous (A-05); sampling
  outside `tools/call` gets a defined Consumer, so it is consent-gated and audited (A-06).
  Requires an amendment entry.
- **Art. 5** — strengthened: UI pinned to `127.0.0.1` so the Apps origin pair holds
  (A-03); trust fingerprint covers env values without storing them (A-04); project config
  cannot change security settings or load code (A-25).
- **Art. 6** — met: total, deterministic source order written in the PRD (A-01).
- **Art. 8** — met: every blocking call in `core` (files, keyring) goes through
  `anyio.to_thread`; the only sync loader runs before an event loop exists (A-02). No
  exception requested.
- **Exceptions requested:** none.
