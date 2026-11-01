# Delta spec — 2026-11-skip-prereleases

> Status: `APPROVED` (owner decision, 2026-11-01) · folded 2026-11-01 · archived.
> Source: the owner's decision of 2026-11-01 to publish no pre-release — no `0.1.0a1`, no
> `0.1.0b1`; the first PyPI release is `0.1.0` (F8-07b). Closes Phase 8 gap G-9
> (`changes/_archive/2026-11-phase8-impl`); restates G-7 / G-8.
> Constitution unaffected (Art. 9 asks for SemVer, not for pre-releases). PRD, API Spec and
> Data Model unaffected (no requirement, interface or schema changes; REQ-AUTH-012's URL is
> frozen "from `0.1.0` on", which already assumed `0.1.0` as the freezing release).

## Applied edits

- **Tasks 1.18 → 1.19** (together with `2026-11-final-gaps`):
  - Conventions — **ADDED** state `[-]` skipped (date and reason; the task will not be done).
    No skipped/cancelled convention existed before.
  - **F5-06b MODIFIED** `[!]` → `[-] skipped 2026-11-01 — owner decision: first release is
    0.1.0 (F8-07b)`; its PyPI prerequisites now gate F8-07b; the block is kept as the record
    of what was planned.
  - **F7-07b MODIFIED** `[!]` → `[-] skipped 2026-11-01 — owner decision: first release is
    0.1.0 (F8-07b)`.
  - **F8-07b MODIFIED** (status stays `[!]`, owner action): the "Owner decision: whether
    `0.1.0a1` / `0.1.0b1` are still published first" sentence is replaced by the decision,
    and the prerequisites are restated as of 2026-11-01 — PyPI Trusted Publisher **reported
    done by the owner**; GitHub environment `pypi` **exists, not yet protected** (the owner
    is adding the required reviewer and the `v*` deployment policy); `v*` tag ruleset
    `tags` **configured and verified**; GitHub Pages **enabled** under the owner's custom
    domain (not deployed until the release merge; the CIMD URL move is delta
    `2026-11-cimd-url`). See "Code check" below.
  - Execution batches P′ and X′ struck (skipped); Phase 6 / Phase 7 owner-action notes;
    execution-log row; header; change history.
- **Plan 1.10 → 1.11**: §1 summary and target dates (a1 / b1 skipped); §2 GitHub and PyPI
  prerequisite rows restated as of 2026-11-01 (the PyPI row now gates F8-07b); F5-06b and
  F7-07b rows ⊘ skipped; Phase 5 / Phase 7 Done criteria for a1 / b1 struck; Phase 6 / 7
  objectives; F8-07b row names the moved prerequisites; §4 diagram; Constitution check Art.
  9 line; metadata versions; change history.
- **TD 1.17 → 1.18** (together with `2026-11-final-gaps`): §9.1 — **MODIFIED** "`0.1.0a1` →
  `0.1.0b1` → `0.1.0`" → "the first PyPI release is `0.1.0`; the planned a1 / b1 were
  skipped"; the "`0.1.0a1` contains no console script" bullet rewritten as history; "a bad
  pre-release is yanked" → "a bad release is yanked and superseded (`0.1.1` for `0.1.0`)";
  the `uv` pin "`0.11.7` at `0.1.0a1`" → "as of 2026-11-01". DD-015 toolchain line: console
  script "from `0.1.0b1` on" → "from the first release, `0.1.0`, on". Change history.
- **CHANGELOG**: the `[0.1.0]` intro says no `0.1.0a1` / `0.1.0b1` pre-releases were
  published (owner decision 2026-11-01). No other CHANGELOG line mentioned a1 / b1.
- **README**: unchanged — the Phase 5 and Phase 7 progress rows already read "✅ done" with
  no "publish pending" wording, and no line mentions a1 / b1 (checked).
- **Archived delta `2026-11-phase8-impl`**: gaps table — G-9 closed; G-7 and G-8 "Where it
  is closed" restated with the 2026-11-01 state.

## Code check (GitHub state, `gh api` on 2026-11-01)

The orchestrator's brief reported "environments empty, no rulesets, Pages 404". Re-checked
while folding, the repository had changed (the owner was configuring it the same day):

- `repos/ecrespo/reflex-mcphost/environments` → `github-pages` (branch policy) and `pypi`
  (created 2026-11-01 12:41 UTC, `protection_rules: []`, `deployment_branch_policy: null`).
- `repos/ecrespo/reflex-mcphost/rulesets` → ruleset `tags` (target `tag`, enforcement
  `active`, include `refs/tags/v*`; rules creation, update, deletion, non-fast-forward;
  bypass: repository admin role).
- `repos/ecrespo/reflex-mcphost/pages` → `build_type: workflow`, `html_url:
  https://blog.seraph.to/reflex-mcphost/` (custom domain of the owner's user site).
- `https://ecrespo.github.io/reflex-mcphost/oauth/client.json` → `301` to
  `https://blog.seraph.to/reflex-mcphost/oauth/client.json` → `404` (not deployed yet).

F8-07b's status records this state (as corrected by the orchestrator the same day: the
owner is adding the `pypi` environment's protection), not the brief's first report. The
redirect was decided by the owner the same day: the CIMD URL moves to the custom domain —
delta `changes/_archive/2026-11-cimd-url`.

## Constitution check
- Art. 9 — SemVer kept; the change is recorded in the Plan, Tasks, TD and CHANGELOG.
- Exceptions requested: none.
