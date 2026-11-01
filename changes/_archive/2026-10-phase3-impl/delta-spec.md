# Delta spec — 2026-10-phase3-impl

> Status: `APPROVED` (owner's standing instruction to implement without per-phase approval,
> 2026-10-31) · folded 2026-10-31 · Source: deviations reported while implementing Phase 3
> (F3-01…F3-11, batches J–L) plus one security-review correction (item 19). Constitution
> and PRD unaffected (no requirement changes meaning).

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **API 1.6 → 1.7**: §3.4 exception rows `ConsentStoreError`, `UnknownRuleError`,
  `AuditRecordNotFoundError`; §4.6 read rules (Host MRTR loop, `InputHandlerError`);
  §4.7 `grant()` hash source, model defaults and `ConsentRequest` validation, step 0 rule
  stores, step 3 (only `allow` tool rules suspended; `deny` tool rules always apply),
  step 5 corrupt-rule poisoning, step 6 write-failure / non-answer / cancellation rules,
  one rule per key, sampling `target_kind`, preview hook limits; §4.8 step 3 schema compile
  failure, step 5/early-failure audit failure, step 7 skipped for `isError`, `start()`
  progress; §4.10 `DenyAllPrompter` outcome source.
- **TD 1.5 → 1.6**: §3.2/§5.1 module layout (`core/outcomes.py`, `core/consent/outcome.py`,
  `core/consent/models.py`); Flow C steps 3, 5, 8 and the legacy/reads note; DD-001 reads
  use the Host loop; DD-005 single-key rule semantics; DD-006 pooled engine; DD-010
  drafts, limits, `$ref` handling, elicitation subset, known limits; §5.3 internal
  exceptions and `-32000` mapping, per-leg `HEADER_MISMATCH`; §8 performance measurement
  rules.
- **DM 1.4 → 1.5**: §2.2 corrupt-row handling and session-over-persistent replacement;
  §2.3 `args_hash` of `arguments=None`; §8 cached compile failures.
- **Tasks 1.7 → 1.8**: Phase 3 done; execution log row.

## Deferred / known gaps

| # | Gap | Where it is closed |
|---|---|---|
| G-1 | REQ-HSH-045 (legacy server-initiated requests through the same handlers) and the legacy-era parts of REQ-HSH-047 attribution, including the DD-004 legacy prompter-wait deadline extension, are not implemented: Phase 3 ships the in-flight registry hooks only (item 35). | Phase 4 (F4-*) |
| G-2 | Real elicitation, sampling and roots handlers: `InputRequestRouter` slots are empty; every input request is `undeclared_input_request` until filled. | Phase 4 (F4-01…F4-05) |
| G-3 | Regex ReDoS in Server-supplied `pattern` / `patternProperties` is only mitigated by the 512-character pattern cap; stdlib `re` has no timeout (item 11). | Phase 8 hardening — evaluate the `regex` package with per-match timeouts |
| G-4 | JSON Schema `format` is not enforced (`jsonschema` default: annotation only) (item 12). | Accepted for v0.1; revisit with G-3 |

## Open question (Phase 8)

- **OQ-1 — Definition TOCTOU.** A tool's definition may change between consent evaluation
  (which can wait on the `Prompter` for up to `consent_timeout_s`) and the `tools/call`
  leg. The consent decision is bound to the hash seen at evaluation time, so a changed
  definition can be invoked under the old approval. Proposal for Phase 8: re-read the
  catalog entry after consent and, if `definition_hash` differs, re-evaluate (or deny)
  before step 5. Not a v0.1 blocker: the window is bounded by the prompt and the catalog
  refresh latency, and the call is audited.

## Full deviation list

### Batch K (F3-06, F3-07)

1. **TD §5.1 — MODIFIED.** `InvocationOutcome` lives in `core/outcomes.py` (shared by
   audit and invoker); member names are lowercase as in API §4.8 (CONFIRMED for the API).
2. **TD §3.3 Flow C — ADDED.** Internal writer surface: `AuditLog(db, key, *, clock=)`
   with `begin`, `finish`, `record` (single-shot early failure), `query`, `get`,
   `mark_interrupted` (startup repair, DM Q-A5). `get()` of an unknown id or of an
   `in_progress` row raises `AuditRecordNotFoundError`; write failures raise
   `AuditUnavailableError`. API §3.4 gains the `AuditRecordNotFoundError` row.
3. **TD §3.3 Flow C step 8 — MODIFIED.** `finish(id, outcome, duration, rounds,
   error_code)` (DM Q-A2); the consent source and rule id are written by `begin()`.
   `finish()` never raises: it logs at ERROR and returns `False` (error table C.8 holds).
4. **TD §3.3 Flow C step 5 — ADDED.** `begin()` / `record()` fail closed with
   `AuditUnavailableError` when the arguments cannot be canonicalized, except `record()`
   for `not_found` / `invalid_arguments`, which stores an empty hash (`args_hash = None`).
   **DM §2.3 — ADDED:** `arguments=None` is hashed as `{}`.
5. **TD §3.3 Flow C — ADDED.** The audit writer takes `fqn: Fqn` only; the Invoker maps an
   unparsable FQN string to `ValueError` before any audit write.
6. **DM §4 Q-A5 — CONFIRMED.** Interrupted rows: outcome `connection_lost`, error code
   `host_interrupted`, status `in_progress` → `finished`.
7. **DM §2.3 — CONFIRMED.** Audit `decision_source` = `ConsentSource` plus
   `not_evaluated`.
8. **TD DD-010 — MODIFIED.** Draft 2020-12 is the default; a `$schema` naming a known
   draft (04, 06, 07, 2019-09, 2020-12) selects that draft's validator. Limits: 20 errors
   per validation, messages redacted and capped at 200 characters, schema depth 64,
   10,000 schema nodes, patterns ≤ 512 characters, instance depth 64; compiled
   validators in an LRU of 1,024 (DM §8).
9. **TD DD-010 — MODIFIED.** Unresolvable or remote `$ref` and `$ref` loops raise
   `SchemaCompileError` at validation time (lazy resolution), not at compile time;
   compile failures are cached like successes.
10. **TD DD-010 — MODIFIED.** The elicitation subset check accepts the full current spec:
    titled single-select via `oneOf` (`const` + `title`), legacy `enumNames`,
    multi-select arrays of enums, and top-level `title` / `description`.
11. **TD DD-010 — ADDED (known limit).** ReDoS is mitigated only by the pattern-length cap
    (gap G-3).
12. **TD DD-010 — ADDED (known limit).** `format` is not enforced (gap G-4).

### Batch J (F3-01…F3-05)

13. **TD §5.1 — MODIFIED.** `core/consent/outcome.py` holds `ConsentOutcome`,
    `OperationKind` and `ConsentSource` (breaks the import cycle
    `events → consent.models → catalog → events`); `core/consent/models.py` holds the rest.
14. **API §3.4 / TD §5.3 — ADDED.** `ConsentStoreError` (persistence failure surfaced by
    `grant` / `update` / `revoke`) and `UnknownRuleError(rule_id)` (`update` / `revoke` of
    an unknown id), both `MCPHostError` subclasses.
15. **API §4.7 `grant()` — ADDED.** For a `tool` target the rule's `definition_hash` is
    taken from the catalog's current entry; an unknown tool raises `UnknownEntryError`.
16. **API §4.7 step 5 / DM §2.2 — ADDED.** A stored rule that cannot be loaded (corrupt
    row, REQ-CNS-008) poisons its Server — or every Server when the row's target is global
    or unreadable: their evaluations deny with `source="error"` until the row is revoked
    or repaired. The row is kept (never silently dropped).
17. **API §4.7 / DD-005 / DM §2.2 — ADDED.** One rule per key in memory across lifetimes:
    a session rule replacing a persistent one deletes the DB row; `update()` moves a rule
    into or out of the DB when its lifetime changes.
18. **API §4.7 step 0 — MODIFIED.** Unattributed requests see `deny` rules from both the
    persistent and the session store, scoped to any Consumer.
19. **API §4.7 step 3 — MODIFIED (security review).** A definition change suspends only
    `allow` tool rules. `deny` tool rules always apply whatever the hash (a definition
    change never loosens a decision). When the current hash is unknown, `allow` tool rules
    are skipped but not suspended and `definition_changed` stays `False`. Matches PRD
    REQ-CNS-007, which speaks only of `allow_always`.
20. **API §4.7 `ConsentAnswer` — ADDED.** For `sampling`, `target_kind="tool"` maps to a
    `server` target; `edited_sampling` is honored only when the sampling is allowed.
21. **API §4.7 step 6 / DD-005 — ADDED.** A persistent answer whose write fails applies
    once (`rule_id=None`, reason recorded); a non-`ConsentAnswer` return from the prompter
    → deny `source="error"`; a cancelled prompt publishes `consent.resolved` and re-raises
    the cancellation.
22. **API §4.7 preview — ADDED.** Preview hooks run with a 5 s timeout on a deep copy of
    the arguments; the default redaction follows local `$ref` and `allOf` / `anyOf` /
    `oneOf`; values nested deeper than 64 levels are redacted whole; `redacted_fields`
    uses dotted paths with list indices (`items.0.token`).
23. **API §4.7 types — ADDED.** `ConsentRule.lifetime` defaults to `"always"`, `origin` to
    `"programmatic"`. `ConsentRequest` validation: `tool_call` requires a `tool` of the
    same Server; `sampling` requires `tool=None`; `unattributed` requires a human
    Consumer.
24. **API §4.10 — ADDED.** `DenyAllPrompter` answers `deny`, so the outcome source is
    `"prompt"` (not `"no_prompter"`).

### Batch L (F3-08…F3-11)

25. **TD DD-001 / Flow C note — MODIFIED.** Resource reads and prompt gets use the Host's
    own MRTR loop on `client.session.*` with `allow_input_required=True` (not the SDK
    auto-loop), sharing round counting and the router with `tools/call`; this bypasses the
    SDK response cache for reads.
26. **API §4.8 — ADDED.** If the single audit record of an early failure (steps 1–4)
    cannot be written, the result is `audit_unavailable` with
    `error.details["attempted_outcome"]` naming the outcome that would have been recorded.
27. **API §4.8 step 7 — MODIFIED.** `structuredContent` of an `isError: true` result is not
    validated against `output_schema` (step 8 decides).
28. **API §4.8 step 3 / TD DD-010 — MODIFIED.** An `input_schema` that fails to compile →
    `protocol_error` with `error.code="invalid_input_schema"` (no consent prompt).
29. **API §4.6 — ADDED.** In reads, an `InputHandlerError` raised by a handler propagates
    unchanged (no `InputRequestError` code maps it).
30. **TD Flow C / §5.3 — MODIFIED.** `HEADER_MISMATCH` (`-32020`) is handled per leg: one
    re-list and one resend for that leg.
31. **TD DD-006 — MODIFIED.** `core/db.py` uses `QueuePool(pool_size=1, max_overflow=8)`
    instead of `NullPool`: closing a WAL connection forced a checkpoint per write
    (REQ-NFR-002 p95 ~20 ms → ~6 ms). Serialization by the single-worker limiter is
    unchanged.
32. **TD §5.3 — ADDED.** Any JSON-RPC `-32000` error from a Server is classified
    `connection_lost` (`-32000` is the SDK's `CONNECTION_CLOSED` code).
33. **TD §8 — ADDED.** The REQ-NFR-001 benchmark loads 10,000 rules straight into
    `RuleIndex` (post-load in-memory state, DD-005).
34. **API §4.8 — CONFIRMED.** `start()` takes no `on_progress`; progress is consumed with
    `handle.progress()`. (Task F3-09 wording referred to `on_progress` for `call()` only.)
35. **Deferred.** Legacy era: the DD-004 prompter-wait extension for server-initiated
    requests and per-invocation attribution move to Phase 4 (gap G-1).
36. **TD §8 — ADDED.** Performance tests assert the best p95 of up to 3 measurements (load
    can only raise latency).

## Addendum (post-review fix, 2026-10-31)

37. MODIFIED API §4.5 `wait_ready` / TD Flow B step 6: `wait_ready` also awaits the
    Catalog's initial full refresh for the live Client (Host-injected ready gate,
    `Catalog.wait_loaded`), so the §4.1 usage (`wait_ready` then `invoker.call`) no longer
    races the catalog and returns `not_found`. Found while verifying the README example.
