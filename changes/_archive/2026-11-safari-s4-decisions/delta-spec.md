# Delta spec — 2026-11-safari-s4-decisions

> Status: `APPROVED` (owner decision, 2026-11-01) · folded 2026-11-01 · archived.
> Source: the owner's decision of 2026-11-01 — "No tengo Safari ni cómo instalarlo, pasemos
> ese punto, aplica plan B" — covering the two release items still open before F8-07b:
> **(1)** the real Safari manual pass (F8-15, Plan R-8) is waived; **(2)** spike S-4 (F0-04)
> closes with plan B — no real CIMD-capable authorization server is available.
> Constitution unaffected (no article changes; Art. 7 is met by the existing tests, Art. 9 by
> recording the decision here and in the Plan, Tasks, TD and CHANGELOG). PRD, API Spec and
> Data Model unaffected: no requirement, interface or schema changes (REQ-AUTH-003/012
> already define the CIMD → DCR → configured `client_id` order and the frozen URL; the PRD
> §11 risk row "Authorization servers rejecting CIMD loopback redirects" already names the
> fallbacks and has no S-4 gate wording).

## Decisions

1. **F8-15 waived.** The owner has no macOS / Safari. Safari coverage for `0.1.0` is
   Playwright WebKit (WPE) in the CI E2E on three engines (F7-06b). Real Safari stays
   unverified: a CHANGELOG `[0.1.0]` known limitation and an accepted residual risk (Plan
   R-8). F8-15 is no longer a prerequisite of F8-07b.
2. **S-4 / F0-04 closed with plan B.** `0.1.0` ships with the tested fallback chain: the
   CIMD document published at `https://blog.seraph.to/reflex-mcphost/oauth/client.json` is
   offered first (when the AS advertises CIMD), then DCR, then a configured `client_id`.
   CIMD interop against a real AS is unverified (known limitation; Plan R-9 accepted).
   Re-opening: a real AS later → re-run the F0-04 procedure; a rejection → Delta Spec and a
   patch release, never a change of the frozen URL.

## Applied edits

- **New `specs/plans/spikes/S-4.md`** (style of S-1…S-6): question, environment, what was
  verified (fixture AS tests under `tests/integration/auth/` and `tests/interop/test_oauth.py`:
  CIMD first, CIMD redirect rules, DCR fallback, configured `client_id`, issuer / resource
  binding, both eras; `tests/unit/test_cimd_document.py`), what was not (a real AS fetching
  the CIMD URL; real `iss` behaviour), answer, decision (plan B, owner 2026-11-01), residual
  risk, impact, re-opening, contract assertions (none new).
- **`specs/plans/spikes/S-3.md`**: the "Real Safari remains unverified" impact bullet notes
  the F8-15 waiver (no change history in spike notes).
- **Tasks 1.19 → 1.20**:
  - **F0-04 MODIFIED** `[!]` → `[x] 2026-11-01 — closed with plan B`. `[x]` rather than
    `[-]`: the task's deliverable (`S-4.md`) exists and its Done criterion is met in its
    fallback branch ("a token obtained … via the documented fallback"); `[-]` means "the task
    will not be done", which is not the case. The status states what is unverified and that
    no AS rejection reason exists because no real AS was reached.
  - **F8-15 MODIFIED** `[!]` → `[-] waived 2026-11-01 — owner decision: no Safari available;
    WebKit (Playwright) is the coverage for 0.1.0` (the `[-]` convention of Tasks 1.19); the
    block is kept as the record of the planned pass.
  - **F8-07b MODIFIED** (stays `[!]`, owner action): title "Tag `v0.1.0`, publish to PyPI";
    status — needs only the PyPI / GitHub prerequisites (in particular the `pypi`
    environment's required reviewer and `v*` tag deployment policy, being configured by the
    owner) and the owner's explicit approval; no longer waits on S-4 or F8-15. Depends on:
    F8-07a + owner prerequisites. Done: S-4.md already closed; Plan §2 real-AS / macOS rows ⊘.
  - Phase 4 note (S-4 not a Phase 4 gate), Phase 8 common rules and owner-actions bullet,
    batch AB′, traceability notes (Phase 4 and Phase 8), execution-log row, header, change
    history.
- **Plan 1.11 → 1.12**: §2 real-AS row and macOS/Windows row ⊘ waived; F0-04 row ☑ (plan
  B); F8-15 row ⊘ waived; F8-07b row (no S-4 text, depends on F8-07a only, names the `pypi`
  environment protection); §4 spike gates (S-4 gates nothing); R-8 and R-9 accepted with the
  plan-B / WebKit wording; metadata TD 1.19; change history.
- **TD 1.18 → 1.19**: DD-009 consequences (S-4 closed with plan B, real-AS unverified,
  R-9); DD-012 consequences and the §6.1 residual-notes paragraph (F8-15 waived, R-8
  accepted). The §10 spike table keeps the original questions and exit criteria. Change
  history.
- **CHANGELOG** `[0.1.0]` known limitations: the `_pending_` Safari line and the `_open_`
  S-4 line replaced with final text (real Safari not verified, WebKit via Playwright; CIMD
  not verified against a real AS, DCR / configured `client_id` fallbacks tested, the
  `client_id` configuration key named). The needles "Real Safari" and "spike S-4" of
  `tests/scripts/test_release_workflow.py` are kept.
- **README**: progress rows Phase 0 ("S-4 … closed with plan B") and Phase 8 (owner actions
  left: tag and publish; Safari pass waived; S-4 closed).
- Unchanged: `docs/user-guide.md` ("Real Safari has not been verified; WebKit is covered
  through Playwright's build" is already final). The NOTES string for "MCP App escaping to
  Host origin" in `tests/security/test_threat_model.py` still says "owner-only manual pass
  (F8-15)" — test code, out of scope of this docs-only delta; reword on the next code change.

## Finding while folding (not fixed here)

`.gitignore` line 223 (`spikes/`) also matches `specs/plans/spikes/`, so **none of the spike
notes S-1…S-6 (nor the new S-4.md or the `assets/` folder) is tracked by git** — `git ls-files
specs/plans/spikes` is empty although the Plan, Tasks and TD cite them. Anchoring the pattern
(`/spikes/`, the throwaway spike code at the repository root) and adding the notes is an owner
call; until then `git add -f specs/plans/spikes` is needed to commit them.

## Constitution check
- Art. 5 — no secret, token or code value in `S-4.md`; MCP Apps isolation unchanged, only
  its Safari verification is waived (residual risk recorded, Plan R-8).
- Art. 7 — nothing is claimed as verified beyond existing tests; the unverified items are
  release-note known limitations.
- Art. 9 — decisions recorded in Plan, Tasks, TD, spike note, CHANGELOG and README.
- Exceptions requested: none.
