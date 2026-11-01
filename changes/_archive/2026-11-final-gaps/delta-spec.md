# Delta spec — 2026-11-final-gaps

> Status: `APPROVED` (owner's standing auto-approval; the gaps were marked "Agent (on
> request)" / "Agent (test-only follow-up)") · folded 2026-11-01 · archived.
> Source: Phase 8 gaps G-14, G-1 and G-15 of `changes/_archive/2026-11-phase8-impl`, done
> test-first before the `0.1.0` release PR. Every item was checked against the code
> (`reflex_mcphost/__init__.py`, `core/errors.py`, `core/auth/{token_store,oauth}.py`,
> `core/connection/manager.py`, `tests/security/test_threat_model.py`).
> Constitution unaffected. PRD unaffected (REQ-AUTH-008 asks for reason codes, never SDK
> text — one more code meets it; REQ-NFR-020 fail-closed behavior unchanged). Data Model
> unaffected (nothing persisted changes). Both public changes are additive (Art. 9: no
> minor bump while `0.x`; listed in CHANGELOG `[0.1.0]` Added / known limitations).

## Items

1. **API §4.11 — MODIFIED; §3.6 — CONFIRMED (gap G-14).** `ConsentRulesChanged` is
   re-exported from `reflex_mcphost` (`_EXPORTS`, `__all__`, the `TYPE_CHECKING` imports)
   like every other event class. API §4.11's "all but `consent.rules_changed` … match that
   event on `type`" → "every one — `ConsentRulesChanged` included — is re-exported".
   Test first: `tests/unit/test_public_api.py::test_every_host_event_class_is_exported`
   (every member of the `HostEvent` union is in `__all__` and is the `core.events` class;
   failed before the export).
2. **API §3.4 `AuthorizationError` row — MODIFIED; TD DD-008 — MODIFIED (gap G-1, Phase 8
   AA deviation 26).** New reason code `secret_store_unavailable` (additive; message
   `authorization for '<id>' failed: secret_store_unavailable`, no backend text). The known
   limitation is removed. Mechanism: `TokenStore._read` catches any non-`ValueError`
   exception of `SecretStore.get` (a `ValueError` is still "unreadable content" → absent
   entry), logs one WARNING "could not read the stored credentials (<type name>)" and raises
   the internal `SecretStoreReadError` (`core.auth.token_store`, not public; fixed message,
   raised `from None`). `OAuthController._classify` maps it to `secret_store_unavailable`
   (inside the SDK attempt) and `ConnectionManager._run_flow` does the same when it raises
   from `preferred_port()` at `authorize()` start.
   **Code check:** the gap said the `get` failure was reported as `discovery_failed`; that
   holds for the connection attempt, but the failing test showed `authorize()` itself
   ended with `flow_error` — `preferred_port()` reads the stored client info before the
   attempt and its raw `KeyringError` reached the manager's generic handler. Both paths now
   report `secret_store_unavailable`.
   Option not taken: `SecretStoreError(operation="get")` — it would widen the public
   `SecretStoreOperation` literal although no Consumer-facing call raises it (the failure
   surfaces as the flow's `AuthorizationError`); one reason code is the minimal additive
   change.
   Test first: `tests/integration/resilience/test_keyring_failure.py::
   test_keyring_get_failure_at_authorize_is_secret_store_unavailable` (signed-in entry, a
   second Host with keyring `get` failing → `authorize()`'s `flow.wait()` raises reason
   `secret_store_unavailable`, no backend text in the traceback, `auth_required`, 0 bearer
   requests, the other Server serves calls, sign-in works once the keyring is back, ≤ one
   WARNING with the backend type per failure; failed before with `'flow_error'`);
   `tests/unit/core/test_errors.py::test_authorization_error_reason_codes` extended.
3. **TD §6.1 "OAuth code interception …" — CONFIRMED; tests only (gap G-15).** The row's
   `EVIDENCE` entry in `tests/security/test_threat_model.py` now cites
   `tests/integration/resilience/test_keyring_failure.py::test_keyring_delete_failure_signs_out_fail_closed`
   and `::test_ui_sign_out_with_a_failing_keyring_is_a_fixed_notice`. Test first:
   `test_sign_out_vectors_cite_sign_out_evidence` (a §6.1 vector naming a sign-out must cite
   a test whose name says `sign_out` / `signs_out`; failed before the two refs were added).
   No TD text change.

## Applied edits

- **API 1.17 → 1.18**: §3.4 `AuthorizationError` row (item 2); §4.11 note (item 1);
  Constitution check Art. 5 (no backend text in the new reason either); change history.
- **TD 1.17 → 1.18** (together with `2026-11-skip-prereleases`): DD-008 `get` failure
  (item 2); change history.
- **Plan 1.10 → 1.11, Tasks 1.18 → 1.19**: shared with `2026-11-skip-prereleases`
  (execution-log row, change history); no task added — the items are gap follow-ups under
  F8-03 / F8-01 / F8-05 scope.
- **CHANGELOG `[0.1.0]`**: Added — `ConsentRulesChanged` exported, reason
  `secret_store_unavailable`; known limitations — "reported as `discovery_failed`" →
  "fails the authorization with reason `secret_store_unavailable`".
  **`docs/library.md`** Errors section names the reason.
- **Archived delta `2026-11-phase8-impl`**: gaps G-1, G-14, G-15 closed.

## Results

Local (Linux, Python 3.13), after all three deltas (`2026-11-skip-prereleases`,
`2026-11-cimd-url`, this one): **2,172 passed, 57 skipped** (was 2,167 + 57; +5 tests:
items 1–3 and the CIMD-URL test) with `ResourceWarning` as an error; coverage core
96.64 %, total 96.01 % (`check_coverage.py` OK); `ruff check`, `ruff format --check`,
`mypy` strict (102 files), `lint-imports` (7 kept, 0 broken), `uv lock --check` clean;
strict REQ gate over all families **147/147**; `uv build` + `check_wheel.py dist` OK.
E2E (`-m e2e`) not re-run (no UI change).

## Constitution check
- Art. 5 — the new reason and `SecretStoreReadError` carry fixed text; the backend
  exception is not chained; the log line has the type name only (tested).
- Art. 7 — each item has a test that failed first.
- Art. 9 — additive public changes, recorded in API, TD and CHANGELOG.
- Exceptions requested: none.
