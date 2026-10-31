# Proposal — Fold spike batch A findings (S-1, S-2, S-6)

> Status: approved (auto-approval by owner instruction) and folded into specs/ (2026-10-31) — archived
> Affected base specs: `specs/prd/mcphost-v0.1.md` v1.2, `specs/api/mcphost-api-v0.1.md` v1.2,
> `specs/technical/mcphost-architecture.md` v1.1, `specs/data-model/mcphost-schema.md` v1.1,
> `specs/tasks/mcphost-v0.1-tasks.md` v1.1
> Date: 2026-10-31 · Author: Ernesto Crespo

## Problem / motivation

Spikes S-1, S-2 and S-6 (`specs/plans/spikes/`) tested the pinned `mcp` 2.3.0 SDK,
PydanticAI 2.64 and LangChain 1.x against the design. Most decisions held (DD-001 for the
modern era, DD-002, API §4.13). Four things did not:

1. **Legacy attribution (S-1).** On legacy-era connections the SDK runs input-request
   callbacks outside the caller's context: the `ContextVar` that ties a sampling or
   elicitation request to its invocation is not visible. As designed, every legacy input
   request would fall back to `human:ui`, so an agent-triggered sampling request could be
   decided by the human's stored rules (weaker consent, Art. 3). The same gap breaks the
   legacy sampling-deny outcome and the DD-004 timeout pause on legacy.
2. **Own MRTR loop details (S-1).** Calling `session.call_tool` directly skips the SDK's
   `-32020` HEADER_MISMATCH re-list/resend and state-only `InputRequiredResult` handling;
   an output-schema mismatch raises `RuntimeError`, not `MCPError`; `ErrorData` cannot be
   placed in `input_responses`.
3. **stdio supervision (S-2).** The SDK never wakes a supervisor parked in
   `async with Client(...)` when the child exits; `message_handler` does not report EOF.
   `Client(StdioServerParameters)` cannot capture stderr; `errlog` needs a real `fileno()`.
   The SDK's shutdown waits are 2 s / 2 s (REQ-CON-008 says 5 s / 5 s) and kill the process
   group.
4. **LangChain adapter (S-6).** `StructuredTool` with a dict `args_schema` silently drops
   arguments named `config` / `run_manager`; the adapter must be a `BaseTool` subclass.

## Owner decisions (2026-10-31)

- **Legacy attribution:** per-Server in-flight registry, **fail closed** when ambiguous —
  exactly one in-flight Host request on that legacy Server → attribute to it; otherwise the
  request is *unattributed*: no stored allow rule applies, the human is always asked
  (allow once / deny only), headless → deny. Concurrency (REQ-INV-013) is kept.
- **stdio shutdown:** adopt the SDK's 2 s / 2 s policy; amend REQ-CON-008. No patching of
  SDK module constants.

## Scope

**Changes:** REQ-CON-008, REQ-CON-009 (wording), REQ-HSH-001/045 (attribution), new
REQ-HSH-047; API `ConsentRequest`/`ConsentAnswer`/`AuditRecord` fields, evaluation
algorithm step 0, `disconnect` row; Tech Design Flow B step 8, Flow C loop, DD-004, §3.2
rows, error mapping; Data Model `audit_record.attribution`; Tasks F1-11 contract list.

**Does not change:** product scope, the consent model for attributed requests, any other
REQ, plan phases or estimates.

## Impact

- **Contract consumers:** none yet. API additions only (`ConsentRequest.attribution`,
  `AuditRecord.attribution`); `ConsentAnswer` validation tightened for unattributed
  requests.
- **Data:** new column in `audit_record` (no data exists; `0001_initial` written with it).
- **Risks:** `CancelScope.deadline` extension for legacy timeouts (DD-004) was not exercised
  in S-1 — verified in F3-09; the Windows stderr reader (`to_thread`) was read from source,
  not run — verified by the Windows CI cell in F2-06.

## Lifecycle note

Pre-implementation: folded into `specs/` on approval, folder moved to `changes/_archive/`.

## Constitution check

- **Art. 2** — strengthened: shutdown follows the SDK as-is; no SDK internals patched.
- **Art. 3** — strengthened: ambiguous legacy input requests can never be auto-allowed by
  stored rules; every one is consent-gated by a human or denied, and audited with its
  attribution.
- **Art. 8** — Windows stderr reader runs behind `anyio.to_thread` with a dedicated limiter.
- **Exceptions requested:** none.
