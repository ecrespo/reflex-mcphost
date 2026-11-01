# Proposal — Phase 8 preparation (hardening and `0.1.0`)

| Field | Value |
|---|---|
| Date | 2026-11-01 |
| Author | Ernesto Crespo |
| Status | `APPROVED` 2026-11-01 (owner's standing auto-approval for spec deltas through Phase 8) · FOLDED 2026-11-01 |
| Sources | Analyze `specs/analyze/2026-11-01-mcphost-v0.1-phase8.md` (P8-01…P8-25); deferred-gap tables of `changes/_archive/2026-10-phase2-impl`, `2026-10-phase3-impl` (G-3, OQ-1), `2026-10-phase4-impl`, `2026-10-phase5-impl`, `2026-11-phase6-impl`, `2026-11-phase7-impl`; code at `8c2f15d` (`core/schema.py` validates synchronously with stdlib `re`; `core/invoker.py` sends with the entry resolved before consent; adapters catch `KeyError`; `ui/install.py` reads `deploy_url` lazily but not `state_manager_mode`; `ui/proxy/server.py` spawns a task per connection; `app/workspace.py` never prunes); measurements: stdlib `re` `^(a\|aa)+$` on 30 × `a` + `!` 0.12 s, doubling per character; `regex` 2026.9.29 with `timeout=0.05` raises `TimeoutError` after 0.05 s on the same class of pattern; PyPI `regex` 2026.9.29 ships cp310–cp315 wheels |
| Affects | PRD 1.9 → 1.10, API 1.15 → 1.16, TD 1.15 → 1.16, Data Model 1.9 → 1.10, Plan 1.8 → 1.9 (all folded 2026-11-01); Tasks 1.17 §Phase 8 is written against this delta. Constitution unchanged |

## Why

Tasking Phase 8 against the specs, the code and the gaps earlier phases routed to it
surfaced two findings that put a Constitution article at risk and several smaller ones:

1. **A Server can freeze the whole Host (HIGH, REQ-NFR-020, Art. 5).** JSON Schema
   `pattern` / `patternProperties` from the Server are matched with stdlib `re` on the
   event loop. `re` has no timeout and holds the GIL, so one catastrophic pattern —
   even on the Server's own `structuredContent` — stops every Server, the UI and the agent
   bridge. The 512-character pattern cap does not bound backtracking.
2. **Consent → call TOCTOU (HIGH, Art. 3, OQ-1).** The Invoker sends `tools/call` with the
   catalog entry it resolved **before** consent; consent can wait on a human for minutes,
   and a Server that changes the tool meanwhile gets the call under the old approval.
3. Smaller items: embedders on Reflex's DISK state manager persist tab state (Art. 5); the
   UI leaks its origin as `Referer`; the sandbox proxy has no connection cap; View
   self-navigation is not detected; a refused authorization URL leaves the OAuth flow
   waiting; REQ-NFR-004's measurement clause is ambiguous; REQ-CON-008's clean-exit case
   needs a mechanism; adapters mask any `KeyError`; the Permissions view is not live; old
   workspaces are never pruned; TD §0 misses runtime dependencies; REQ-CON-013 is not marked
   as deferred.

No requirement is weakened. Two requirements are added (REQ-INV-016, REQ-NFR-021); REQ-NFR-004
gets an explicit measurement method with the same budget; REQ-CON-013 is marked deferred.

## Decisions proposed

- D1 Server-supplied regexes are evaluated with the **`regex`** package (new runtime
  dependency) under a per-match timeout (50 ms) and a per-validation budget (250 ms); a
  timeout makes the schema unusable for that call. New REQ-NFR-021.
- D2 Invoker pipeline step 4a: re-resolve the entry after consent; a changed
  `definition_hash` → `denied` / `definition_changed`, a vanished entry → `not_found`, no
  second prompt in the same attempt. New REQ-INV-016.
- D3 REQ-CON-008 clean exit: after the transport closes, signal what is left of the
  Server's process group (POSIX) / rely on the Job Object (Windows), mechanism pinned by
  the SDK contract test.
- D4 The lifespan refuses to start (start-up `RuntimeError`) when the Reflex config's
  `state_manager_mode` is not `MEMORY`.
- D5 The Host/Origin guard adds `Referrer-Policy: no-referrer` to every UI response.
- D6 Sandbox proxy: ≤ 32 concurrent connections, over the cap `503`.
- D7 View self-navigation is detected (second inner `load`) and the View is discarded
  (reason `navigated`, no teardown message); exfiltration by that navigation stays residual.
- D8 A refused (non-openable) authorization URL cancels the OAuth flow.
- D9 REQ-NFR-004 measurement method stated (keystroke and `catalog.changed`, in-page
  timing in Chromium driven by Playwright); budget unchanged.
- D10 `UnknownToolError(MCPHostError, KeyError)`; adapters catch only it.
- D11 Event `consent.rules_changed`; the Permissions view refreshes on it.
- D12 `run` prunes our own workspaces unused for ≥ 7 days.
- D13 Editorial / drift: TD §0 rows (`h11`, `pydantic`, `regex`), DD-018 contract 6 covers
  `ui.actions`, REQ-CON-013 deferred to v0.2, DD-010 / DD-016 / §6.1 wording.
- D14 Release path for `0.1.0`: Plan F8-07 split into F8-07a (local) / F8-07b (owner);
  new owner task F8-15 (real Safari); `release.yml` gains a post-publish `verify` job.

## Alternatives considered

- D1: (a) **restrict the pattern syntax** (no nested quantifiers, backreferences,
  lookarounds) and keep stdlib `re` — rejected: polynomial backtracking (`\d*\d*\d*x`)
  survives any practical subset short of banning quantifiers, and the analysis would rely
  on the private `re._parser`; (b) run `re` in a worker thread with a timeout — rejected:
  `re` does not release the GIL and a thread cannot be cancelled; (c) a subprocess pool —
  rejected: spawn cost (Windows) would break REQ-NFR-002 and adds a process lifecycle;
  (d) stop enforcing `pattern` (annotation only, like `format`) — rejected: it also
  changes `patternProperties` / `additionalProperties` semantics, so valid arguments would
  be refused or invalid ones accepted. The `regex` package is mature, pure C with wheels for
  every supported platform and Python, and needs no code outside `core.schema`.
- D2: re-evaluate consent (second prompt) instead of denying — rejected for v0.1: an
  unbounded loop if the Server keeps changing the tool, and two prompts for one attempt
  break "one evaluation per invocation" (REQ-HSH-042); the Consumer can retry.
- D4: a WARNING only — rejected: Art. 5 is fail closed; MCP Apps-only disabling — rejected:
  invocation arguments and results are in tab state too.
- D5: open links from the relay with `noreferrer` — rejected: covers only `ui/open-link`,
  not elicitation and authorization URLs; a response header covers every navigation.
- D7: send `ui/resource-teardown` to the navigated frame — pointless (the document is no
  longer the App) and it would wait 2 s for nothing.
- D11: poll the rules on a timer — rejected: needless DB traffic; an event is cheap and
  carries no argument values.

## Impact

Tasks F8-01…F8-15 (Tasks 1.17) follow these decisions; Plan Phase 8 grows from 5.5 d (stale: its eight tasks summed to 6 d) to
10.5 d (new F8-09 … F8-15, F8-07 split into a/b). `regex>=2026.9` is a new runtime
dependency (Art. 9: explicit spec decision, TD §0). Tagging and publishing `0.1.0`, the
S-4 closure and the real-Safari pass are **owner actions** (F8-07b, F0-04, F8-15, `[!]`).
Owner-dependent items are listed in the Analyze report.
