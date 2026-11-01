# Delta Spec — 2026-11-phase8-prep

> Status: `APPROVED` (owner's standing auto-approval, 2026-11-01) · folded 2026-11-01 ·
> Source: Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase8.md`.
> Each item names its target document and section and whether the text is **ADDED**,
> **MODIFIED** or **CONFIRMED**. Code changes implied here land in the Phase 8 tasks.
> Assumptions are marked **Assumption**.

## Applied edits

- **PRD 1.9 → 1.10**: REQ-INV-016 ADDED (D2); REQ-NFR-021 ADDED (D1); REQ-NFR-004
  measurement clause MODIFIED (D9); REQ-CON-013 marked deferred (D13); Constitution check
  lines.
- **API 1.15 → 1.16**: §3.4 `UnknownToolError` (D10); §4.8 pipeline step 4a and code
  `definition_changed` (D2); §4.11 `consent.rules_changed` (D11); §4.12 / §4.13
  `UnknownToolError` (D10); §6 workspace pruning (D12); §7 proxy connection cap (D6),
  self-navigation (D7), `ui/open-link` referrer (D5); §8 state-manager check (D4), referrer
  policy (D5), refused authorization URL (D8); §11 rows.
- **TD 1.15 → 1.16**: §0 rows `h11`, `pydantic`, `regex` (D1, D13); DD-002 consequence
  (D3); DD-010 bounds and known limits (D1); DD-012 consequences (D6, D7); DD-016 pruning,
  MEMORY check, Windows note (D4, D12); DD-017 `Referrer-Policy` (D5); DD-018 contract 6
  (D13); §6.1 rows (D1, D2, D4–D8); §9.1 `verify` job (D14).
- **Data Model 1.9 → 1.10**: §2.3 note `definition_changed` (D2); §8 rows "Compiled
  validators" (D1) and "Reflex tab state" (D4); header related versions.
- **Plan 1.8 → 1.9**: Phase 8 table (F8-01…F8-15, F8-07a/b), duration 5.5 (stale; 6 d of tasks) → 10.5 d, Done
  criteria; Constitution check; metadata versions (D14).
- **Tasks 1.17**: Phase 8 written against this delta.

## D1 — Bounded evaluation of Server-supplied regexes (P8-01) · HIGH

**PRD §7 Availability / resilience — ADDED** after REQ-NFR-020:

> - REQ-NFR-021: Regular expressions supplied by a Server (JSON Schema `pattern`,
>   `patternProperties`, and the property-name matching they imply) SHALL be evaluated
>   with a time bound — at most 50 ms per match and 250 ms per schema validation — so that
>   no Server-supplied pattern can block the Host for longer; a bound that is hit SHALL make
>   that validation fail as an unusable schema, never as valid input.

**TD §0 — ADDED** row: `regex` | `>=2026.9` | PyPI (2026.9.29, 2026-09-29; wheels cp310–cp315
for Linux, macOS, Windows), `timeout=` keyword on `search` / `fullmatch`.

**TD DD-010 — MODIFIED**:
- **Bounds** gains: "Server-supplied regexes (`pattern`, `patternProperties`, and the
  key matching of `additionalProperties` / `unevaluatedProperties` / `propertyNames` that
  depends on them) are compiled and matched with the `regex` package, not stdlib `re`:
  per-match `timeout=0.05`, per-`validate()` budget 0.25 s (sum of match time), compiled
  patterns cached with the validator. The validator classes are `jsonschema.validators.extend`ed
  with replacements for those keywords; no `jsonschema` internals are patched. A
  `TimeoutError` or an exhausted budget raises `SchemaCompileError("pattern evaluation
  timed out")`, i.e. the existing per-call mapping (step 3 → `protocol_error` /
  `invalid_input_schema`; step 7 → `protocol_error`; elicitation → the request is refused
  as `invalid_elicitation_schema`; UI forms → the form shows the schema as unusable). A
  pattern `regex` cannot compile is a compile error as today."
- **Known limits (v0.1)** — the ReDoS sentence is replaced by: "Patterns use `regex`
  (Python-flavoured) syntax, not ECMA-262; the difference is the same as with `re` before.
  The time bound is per call: a Server can still make each validation of its tools cost up
  to 250 ms." `format` stays annotation only.

**API §4.8 step 3 / step 7 — CONFIRMED** (no new outcome: a timeout is a schema the Host
cannot use).

**TD §6.1 "Remote `$ref` / schema bombs" — MODIFIED** mitigation: "… compile errors
contained per tool; Server regexes evaluated with `regex` under a 50 ms / 250 ms bound
(REQ-NFR-021)". REQ column: DD-010, REQ-NFR-021.

**Data Model §8 "Compiled validators" — MODIFIED**: contents "`jsonschema` validators
(extended with the bounded `regex` keywords) and cached compile failures".

**`pyproject.toml`** (code, F8-10): `regex>=2026.9` in `[project] dependencies`; `uv.lock`
refreshed. **Assumption:** `regex` keeps shipping wheels for every Python in the CI matrix;
F8-06 re-verifies at release time.

## D2 — Re-resolve the tool after consent (P8-02, OQ-1) · HIGH

**PRD RF-INV — ADDED** row after REQ-INV-015:

> | REQ-INV-016 | unwanted | IF a tool's definition hash changes, or the tool disappears
> from the catalog, between the consent evaluation of an invocation and its `tools/call`,
> THEN THE SYSTEM SHALL NOT send the call and SHALL return outcome `denied` (code
> `definition_changed`) or `not_found` respectively, audited as one record. |

**API §4.8 pipeline — ADDED** row between steps 4 and 5:

| Step | Failure → outcome | REQ |
|---|---|---|
| 4a. Re-resolve the FQN after consent (the evaluation may have waited on the Prompter) and compare `definition_hash` with the entry evaluated at step 4 | entry gone → `not_found`; hash differs → `denied` with `error.code = "definition_changed"` (`details` = `{"evaluated": <old hash>, "current": <new hash>}`); no second prompt in this attempt; one audit record with the step-4 `decision_source` | REQ-INV-016, REQ-CNS-007 |

The text after the table gains: "Steps 1–4a failures are audited …". A Consumer that retries
gets a fresh evaluation, in which the REQ-CNS-007 rug-pull rules apply. The window between
step 4a and the send (step 5 `AuditLog.begin`, one DB write) is not re-checked.

**Data Model §2.3 note — ADDED**: "A post-consent definition change (API §4.8 step 4a) is an
ordinary record: `outcome = 'denied'`, `error_code = 'definition_changed'`,
`decision_source` = the evaluated one — no schema change."

**TD §6.1 "Tool definition rug pull after trust" — MODIFIED** mitigation: "`definition_hash`
on rules; suspend + re-ask; the Invoker re-checks the hash after consent and denies a
changed tool (step 4a)". REQ column: REQ-CNS-007, REQ-INV-016.

## D3 — REQ-CON-008 after a clean stdio exit (P8-04) · MEDIUM

**TD DD-002 Consequences — ADDED**: "After a stdio Client's transport has closed — whatever
the reason, including a Server that exited promptly on stdin EOF — the supervisor signals
the remains of the Server's process group: POSIX `os.killpg(pgid, SIGTERM)`, then `SIGKILL`
after 2 s if any member is left (`ProcessLookupError` = nothing left, not an error; `pgid`
is the child pid, which `start_new_session` makes the group id, recorded when the process
is spawned). On Windows the SDK places the child in a Job Object; closing it terminates the
remaining members. The SDK surface used to learn the pid (or to hook the spawn) is pinned by
`tests/contract/test_mcp_sdk_surface.py`. **Assumption:** the SDK's Windows Job Object is
created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; F8-08 verifies it on `windows-latest`,
otherwise a Delta Spec." The PRD text of REQ-CON-008 is unchanged (CONFIRMED).

## D4 — MEMORY state manager enforced (P8-05) · MEDIUM

**API §8 "Tab state stays in memory (normative)" — MODIFIED** last sentence: "The
reference app does so (TD DD-016). The lifespan installed by `install()` checks it at
start-up, where it reads `deploy_url` (TD DD-017): a `state_manager_mode` other than
`MEMORY` is a start-up `RuntimeError` naming the setting (fail closed; the reference app
CLI then exits `3`)."

**TD DD-016 — MODIFIED** ("an embedding app must do the same (API §8)") → "… an embedding
app must do the same; the lifespan refuses to start otherwise (API §8)". **TD §6.1** "Tab
state written to disk" row: "… embedding apps must do the same — enforced at lifespan
start-up (start-up `RuntimeError`)". **Data Model §8 "Reflex tab state" — MODIFIED**:
"embedding apps must do the same; the lifespan refuses any other mode (API §8)".

## D5 — No referrer from the UI (P8-06) · MEDIUM

**TD DD-017 — ADDED**: "Every HTTP response that passes the guard (static UI and backend)
gets `Referrer-Policy: no-referrer` unless the response already sets one; refused responses
(`403`, `503`) carry it too. Every navigation the UI starts — `ui/open-link`, url-mode
elicitation, authorization URLs — therefore sends no `Referer`, whatever window features
Reflex's external redirect uses."

**API §7 `ui/open-link` row — MODIFIED** tail: "… `noopener` is guaranteed by Reflex; the
UI's `Referrer-Policy: no-referrer` (TD DD-017) means the opened site gets no `Referer`;
result `{}`; decline → `-32000` "link declined"." **API §8** (paragraph on opening
Server-provided URLs) — ADDED sentence: "UI responses carry `Referrer-Policy: no-referrer`,
so no opened URL learns the UI origin."

**TD §6.1 "Referrer leak on an approved `ui/open-link`" — MODIFIED**: vector "Referrer leak
when the UI opens a Server-provided URL"; mitigation "**Mitigated:** `Referrer-Policy:
no-referrer` on every UI response (DD-017); E2E asserts the opened page sees no `Referer`
and an empty `document.referrer`"; REQ column REQ-APP-015, REQ-HSH-015, REQ-AUTH-008,
Art. 5.

## D6 — Sandbox proxy connection cap (P8-07) · MEDIUM

**API §7 Sandbox proxy check table — ADDED** first row: "more than 32 connections already
open (counted from accept to close) | `503` with `Retry-After: 1`, then close (no request
is read)". The paragraph after the table gains: "At most 32 connections are served at a
time."

**TD DD-012 / §6.1 "DNS rebinding / foreign requests to the sandbox proxy" — MODIFIED**: "…
no request logging; at most 32 concurrent connections (over the cap `503` and close)".

## D7 — View self-navigation detected (P8-08) · MEDIUM

**API §7 Lifecycle — ADDED**: "Navigation: `proxy.html` counts `load` events of the inner
frame; the first is the App. A second one (the View navigated its own frame, which no
CSP prevents — TD §6.1) makes the proxy post `{"type": "view-navigated"}` to the relay
(same channel and checks as `sandbox-proxy-ready`); the bridge then discards the View
**without** `ui/resource-teardown` (reason `navigated` in the log, `view → navigated`),
cancels its in-flight requests (their invocations end `cancelled`), removes the iframe and
shows the notice "The App navigated away and was closed." A `view-navigated` from anything
but the proxy window and origin is ignored like any forged message."

**API §7 `ui/resource-teardown` row — MODIFIED**: the reasons list gains "(`navigated` is a
discard reason, never sent — see Lifecycle)".

**TD §6.1 "MCP App View navigating its own frame" — MODIFIED** mitigation: "Not blocked by
CSP in any engine (S-3). **Detected:** a second inner `load` discards the View (no further
bridge traffic, in-flight calls cancelled, notice). **Residual:** data carried by that first
navigation is not prevented; Views only come from Servers the user connected." TD DD-012
Consequences gains the same sentence.

## D8 — A refused authorization URL cancels the flow (P8-09) · MEDIUM

**API §8** (paragraph on opening Server-provided URLs) — MODIFIED: "… a refused
authorization URL gets a fixed notice and no redirect, and the UI cancels the flow
(`AuthorizationFlow.cancel()`): it ends with `AuthorizationError(reason="cancelled")`, the
loopback listener is released and the Server returns to `auth_required`, so the user can
retry at once."

**TD §6.1 "Server-provided URLs opened by the UI" — MODIFIED**: "(the core flow then ends at
its deadline)" → "(the UI cancels the core flow: `cancelled`, listener released)".

## D9 — REQ-NFR-004 measurement method (P8-10) · MEDIUM

**PRD REQ-NFR-004 — MODIFIED** (budget unchanged):

> - REQ-NFR-004: The Host SHALL handle 50 configured Servers and 2,000 catalog entries
>   without UI list rendering exceeding 200 ms — measured in Chromium driven by Playwright,
>   for each of two interaction kinds separately, as the p95 over ≥ 20 interactions of the
>   time to the Catalog/Servers list DOM update (observed in the page) from (a) the filter
>   input event of a keystroke (in-page `performance.now()`), and (b) the Host's
>   `catalog.changed` publish time (wall clock, same machine, recorded by the reference app
>   at DEBUG). Each kind SHALL meet the budget.

**Tasks** (F8-02): the `catalog.changed` timestamp is emitted only when
`REFLEX_MCPHOST_LOG_JSON=1` at DEBUG under `reflex_mcphost.ui` (no new public API).

## D10 — `UnknownToolError` (P8-12) · LOW

**API §3.4 exception table — ADDED** row: "`UnknownToolError(name)` (also a `KeyError`) |
`Toolset.resolve` / `Toolset.call` of an alias not in the current listing | REQ-TLS-004".

**API §4.12 — MODIFIED**: "It raises `KeyError` for an alias …" → "It raises
`UnknownToolError` (a subclass of both `MCPHostError` and `KeyError`, so existing `except
KeyError` code keeps working) for an alias …"; `resolve()` likewise.

**API §4.13 — MODIFIED**: "treat a `KeyError` from `Toolset.call` … any `KeyError` from it is
treated as a vanished alias — narrowing is a Phase 8 candidate, delta `2026-10-phase5-impl`
G-3" → "treat an `UnknownToolError` from `Toolset.call` (alias gone since the listing or
snapshot) the same way … Only `UnknownToolError` is converted; any other `KeyError` is a bug
and propagates unchanged (it fails the agent run instead of being reported to the model as
a vanished tool)."

## D11 — `consent.rules_changed` (P8-13) · LOW

**API §4.11 events — ADDED** row: "`consent.rules_changed` | `rule_ids: tuple[str, ...]`,
`change` ∈ `granted`, `updated`, `revoked`, `suspended` (no targets' argument values, no
patterns) | REQ-UI-006". Published by `ConsentPolicy` after a change is persisted (and
after a session rule changes in memory); never for a failed write.

**API §8** `permissions_table` row — ADDED: "re-queries the rules on `consent.rules_changed`
(any tab, any Consumer)". TD DD-011 watcher bullet gains the event.

## D12 — Workspace pruning (P8-14) · LOW

**TD DD-016 — MODIFIED** "Old workspaces are not pruned in v0.1." → "On every `run` the CLI
touches the current workspace's marker (its mtime = last use) and then, best effort,
removes sibling directories of `…/app/` that **carry the marker** and were last used ≥ 7
days ago, and leftover `.<name>.*.tmp` directories older than 1 day; directories without
the marker are never touched; any error is logged at DEBUG and ignored (Windows: files in
use)." **API §6 `run` — ADDED** one sentence with the same rule and the workspace location.

**TD DD-016 Process group and signals — MODIFIED**: "The Windows path is not covered by a
test in v0.1." → "The Windows path is covered by a Windows-only test in the CI matrix (F8-14);
where the runner offers no console, the test is skipped with that reason."

## D13 — Editorial and drift (P8-16, P8-17, P8-18) · LOW

- **TD §0 — ADDED** rows `h11` `>=0.16` (DD-009 listener, DD-012 proxy) and `pydantic`
  `>=2.11,<3`; sentence after the table: "Re-verified on <date> (F8-06)" is filled by F8-06.
- **TD DD-018 contract 6 — MODIFIED**: "`reflex_mcphost.ui.models`, `reflex_mcphost.ui.actions`
  ↛ `reflex`, `reflex_base` (indirect chains included)".
- **PRD REQ-CON-013 — MODIFIED**: trailing tag "— `COULD` (deferred to v0.2, not in 0.1.0)".
- **PRD Constitution check — MODIFIED**: Art. 3 adds REQ-INV-016; Art. 5 adds REQ-NFR-021.

## D14 — `0.1.0` release path (P8-20…P8-23) · INFO

- **Plan §3 Phase 8 — MODIFIED**: F8-07 split into **F8-07a** (local release preparation:
  `CHANGELOG.md` `[0.1.0]` draft with the known limitations of Analyze P8-24, README final,
  `release.yml` `verify` job, CIMD document check; no tag) and **F8-07b** (owner action: S-4
  closed via F0-04, release PR, tag `v0.1.0`, approve `pypi`, CIMD URL live); new tasks
  F8-09…F8-15; Phase 8 5.5 d (stale) → 10.5 d.
- **TD §9.1 — ADDED**: "After `publish`, job `verify` (needs `publish`; matrix
  `ubuntu-latest`, `macos-latest`, `windows-latest` × Python 3.13) waits until the version
  is visible on PyPI (≤ 10 min) and runs `uvx --from reflex-mcphost==<version>
  reflex-mcphost --version` (must print the version) and `servers --json` on a minimal
  config; on Linux also `run --no-browser` until `GET /ping` answers. It holds no
  `id-token`; a failure does not un-publish (yank is an owner decision)."
- **Assumption (P8-25):** the classifier stays `Development Status :: 4 - Beta` for `0.1.0`.

## CONFIRMED (no edit)

- REQ-NFR-001/002/003 definitions and budgets (F8-02 re-measures them unchanged).
- REQ-NFR-020 text: F8-03 adds tests for keyring failure and crash storms, no wording change.
- REQ-NFR-030: met by the CI matrix; F8-04 adds the citing test.
- Toolset `include` / `exclude` stay `fnmatchcase` globs (backtracking-safe translation);
  UI filters stay substring matches; `secret_arg_pattern` stays owner configuration (P8-19).
- Accepted for v0.1, listed in the CHANGELOG known limitations (P8-24): JSON Schema `format`
  not enforced; token expiry unknown after restart (first `401` refreshes once); elicitation
  re-prompt shows a new card; Server media allow-list; `host-context-changed` never sent;
  E2E Linux-only; REQ-CON-013 deferred.
