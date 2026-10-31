# Delta — Fold spike batch A findings (S-1, S-2, S-6)

Version bumps on fold: PRD 1.3 · API 1.3 · Tech Design 1.2 · Data Model 1.2 · Tasks 1.2.
Sources: `specs/plans/spikes/S-1.md`, `S-2.md`, `S-6.md` (2026-10-31, `mcp` 2.3.0).

---

## 1. `specs/prd/mcphost-v0.1.md` → v1.3

### MODIFIED — REQ-CON-008 (S-2)
- **Before:** "…close stdin, wait up to 5 s for exit, then terminate, and kill after a
  further 5 s, leaving no orphaned process."
- **After:** WHEN a stdio Client is disconnected, THE SYSTEM SHALL close stdin, wait up to
  2 s for exit, then terminate, and kill after a further 2 s, terminating the Server's whole
  process group (Windows: its Job Object), leaving no orphaned process.
- **Reason:** owner decision — adopt the pinned SDK's shutdown policy instead of patching
  undocumented module constants (Art. 2).

### MODIFIED — REQ-CON-009 (S-2)
- **After:** WHEN the Host shuts down, THE SYSTEM SHALL disconnect every Client
  concurrently per REQ-CON-008 within 15 s total.

### MODIFIED — REQ-HSH-001, last sentence (S-1)
- **After:** "The Consumer of a sampling request is the Consumer of the originating Host
  request (tool call or catalog read); a legacy request that cannot be attributed is
  handled per REQ-HSH-047."

### MODIFIED — REQ-HSH-045, last sentence (S-1)
- **Before:** "A server-initiated request that is not correlated with an in-flight Host
  request SHALL be attributed to Consumer `human:ui`."
- **After:** "THE SYSTEM SHALL attribute a legacy server-initiated request to the in-flight
  Host request on that Server when exactly one is in flight; otherwise the request is
  unattributed (REQ-HSH-047)."

### ADDED — REQ-HSH-047 (unwanted) — `MUST` (S-1)
IF a legacy-era input request cannot be attributed to exactly one in-flight Host request,
THEN THE SYSTEM SHALL treat it as unattributed: no stored or session allow decision SHALL
apply (stored deny decisions still apply), the human Consumer SHALL be asked with only
`allow_once` or `deny` (once) available, the request SHALL be denied when no prompter is
attached, and its audit record SHALL be marked `unattributed`.

---

## 2. `specs/api/mcphost-api-v0.1.md` → v1.3

### MODIFIED — §4.5 `disconnect` row (S-2)
- "stdio: close stdin → 5 s → terminate → 5 s → kill" → "stdio: close stdin → 2 s →
  terminate → 2 s → kill (process group / Job Object; SDK policy, REQ-CON-008)".

### MODIFIED — §4.7 types (S-1)
- `ConsentRequest` gains `attribution: Literal["exact", "unattributed"] = "exact"`; for
  `unattributed`, `consumer` is `Consumer.human()` (the asked party).
- `ConsentAnswer`: for an `unattributed` request only `decision ∈ {allow_once, deny}` with
  `deny_lifetime="once"` is accepted; anything else is treated as `deny` (fail closed).

### MODIFIED — §4.7 Evaluation algorithm (S-1)
- **Added step 0:** "If `request.attribution == "unattributed"` (REQ-HSH-047): collect only
  matching stored `deny` rules for any Consumer — if any, deny (`source="rule"`); else go
  to step 4 (prompt or `no_prompter` deny). No rule is ever created from the answer."

### MODIFIED — §4.9 `AuditRecord` (S-1)
- Adds `attribution: Literal["exact", "unattributed"]`.

### MODIFIED — §4.10 rule (S-1)
- **Before:** "Server-initiated input requests not correlated with an in-flight Host
  request use `Consumer.human()` (REQ-HSH-045)."
- **After:** "Legacy input requests are attributed per REQ-HSH-045/047; unattributed ones
  reach `ask_consent` / `elicit` with `attribution="unattributed"` and the UI says so."

### MODIFIED — §4.14 (S-1)
- "…else `human:ui` (REQ-HSH-001/045)" → "…else unattributed (REQ-HSH-047)".

---

## 3. `specs/technical/mcphost-architecture.md` → v1.2

### MODIFIED — §3.2 rows (S-2, S-6)
- **Transport factory:** technology "`mcp.client.Transport` wrapper over
  `stdio_client(params, errlog=<pipe write end>)`, `streamable_http_client`, `httpx2`";
  responsibility adds "EOF-observing wrapper (liveness), per-attempt stderr pipe (POSIX
  `wait_readable` reader; Windows `to_thread` reader on a dedicated limiter)". stdio is
  always built as `Client(<own Transport>)`, never `Client(StdioServerParameters)`.
- **Adapters:** "PydanticAI `AbstractToolset` (`pydantic-ai-slim`); LangChain `BaseTool`
  subclass (`langchain-core` only; `langgraph` for examples)" — not `StructuredTool`,
  which drops arguments named `config` / `run_manager` with dict schemas.

### MODIFIED — §0 rows (S-6)
- PydanticAI row: "`pydantic-ai-slim>=2.64,<3`" (extra `pydantic-ai`).
- LangGraph row: "extra `langgraph` → `langchain-core>=1.7,<2`; `langgraph>=1.2,<2` only in
  the `test`/`docs` groups".

### MODIFIED — Flow B step 8 (S-2)
- **After:** "park until: stop requested | transport closed — read-stream EOF observed by
  the Host's `Transport` wrapper (stdio and HTTP; ~6 ms after a stdio child exits) |
  listen `SubscriptionLost`/`MCPError` of transport class (classified by checking the
  wrapper's closed signal). `message_handler` does not report EOF."

### MODIFIED — Flow C step 6 and legacy paragraph (S-1)
- Loop additions: (a) a state-only `InputRequiredResult` (no `input_requests`) is retried
  with `input_responses=None` after a short backoff and counts as a round; (b) on a modern
  connection, `MCPError.code == -32020` (HEADER_MISMATCH) → re-list tools once and resend
  once; (c) `RuntimeError` from `session.call_tool` (output-schema mismatch, unexpected
  `InputRequiredResult`) → `protocol_error`; (d) a refused sampling is a client-side abort
  (nothing sent); a refused elicitation is sent as `decline`/`cancel`; `ErrorData` is never
  placed in `input_responses`. `progress_callback` is passed **by keyword** on every leg.
- Legacy paragraph replaced: "Legacy-era Servers deliver input requests as server-initiated
  JSON-RPC requests whose callbacks do not see the caller's `ContextVar`. Each legacy
  `ServerSupervisor` keeps an **in-flight registry** of Host requests (tool calls and reads)
  on that connection; a callback attributes the request to the single in-flight entry, or
  marks it unattributed when there are zero or several (REQ-HSH-045/047). For an attributed
  tool call, a sampling denial is recorded on the invocation and the Invoker overrides the
  final outcome to `denied/sampling_denied` whatever `CallToolResult` the Server returns.
  Modern connections keep the `ContextVar` (it is visible in the Host's own loop and in the
  SDK auto-loop used by reads); the router maps callback refusals to distinct `ErrorData`
  codes so reads raise the right `InputRequestError` code."

### MODIFIED — DD-004 (S-1)
- **Added:** "On legacy connections the human wait happens inside a single
  `session.call_tool` leg. The leg runs in a `CancelScope` whose `deadline` the callback
  extends by the measured prompter wait when the request is attributed to that invocation;
  unattributed waits are not excluded (documented limitation). Verified in F3-09."

### MODIFIED — §5.3 SDK exception mapping (S-1)
- **Added rows:** "`RuntimeError` from `session.call_tool` → `protocol_error`";
  "`MCPError` `-32020` (modern) → one re-list + resend, then `protocol_error`";
  "auto-loop `InputRequiredRoundsExceededError` → `InputRequestError(input_rounds_exceeded)`".

---

## 4. `specs/data-model/mcphost-schema.md` → v1.2

### MODIFIED — §2.3 `audit_record` (S-1)
- **Added column:** `attribution TEXT NOT NULL DEFAULT 'exact' CHECK (attribution IN
  ('exact','unattributed'))` — REQ-HSH-047; for `unattributed` rows, `consumer_*` is
  `human`/`ui` (the asked party).
- SQLModel `AuditRecordRow` gains `attribution: str = "exact"`.

---

## 5. `specs/tasks/mcphost-v0.1-tasks.md` → v1.2

### MODIFIED — F1-11 (S-1, S-2, S-6)
- **What:** pin every item of the *Contract assertions* sections of
  `specs/plans/spikes/S-1.md`, `S-2.md` and `S-6.md` (including: `ContextVar` **not**
  visible in legacy callbacks; `Client(StdioServerParameters)` has no `errlog`; `errlog`
  needs `fileno()`; SDK stdio waits 2 s / 2 s; `HEADER_MISMATCH == -32020`).

### MODIFIED — F1-10 (S-2)
- Fixture servers gain a "write to stderr periodically" tool and a "crash now" tool.
