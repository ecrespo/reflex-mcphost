# Proposal — Phase 5 preparation (toolset, adapters and `0.1.0a1`)

| Field | Value |
|---|---|
| Date | 2026-10-31 |
| Author | Ernesto Crespo |
| Status | `APPROVED` 2026-10-31 (owner's standing auto-approval, incl. D4: no console script in `0.1.0a1`) · FOLDED 2026-10-31 |
| Sources | Analyze `specs/analyze/2026-10-31-mcphost-v0.1-phase5.md` (P5-01…P5-08); spike `specs/plans/spikes/S-6.md`; import-linter 2.15 behaviour (checked 2026-10-31); installed `pydantic-ai-slim` 2.64.0, `langchain-core` 1.7.3, `uv` `publish --dry-run` (checked 2026-10-31) |
| Affects | API 1.9 → 1.10, TD 1.9 → 1.10, Plan 1.5 → 1.6 (all folded 2026-10-31); Tasks 1.11 §Phase 5 is written against this delta. PRD unchanged |

## Why

Tasking Phase 5 against the code and the installed frameworks surfaced two gaps that block
an unambiguous implementation of MUST requirements, plus smaller ones:

1. **`Host.toolset()` cannot be written without breaking the layers contract (HIGH,
   Art. 4).** API §4.1 puts `toolset()` on `Host` (`reflex_mcphost.core.host`) and returns
   a `Toolset` from `reflex_mcphost.toolset`, but DD-018 orders the layers `adapters >
   toolset > core`, so `core` may not import `toolset`. import-linter flags the import
   even inside a function body (reproduced 2026-10-31). Any workaround (dynamic
   `import_module`, a registration hook) defeats DD-018 rather than satisfying it.
2. **The alias algorithm is not collision-free as written (HIGH, REQ-TLS-002).** API §4.12
   only checks *candidates* against each other. A tool whose plain candidate happens to
   equal another tool's *hashed* alias keeps it, so two tools get the same alias. A Server
   controls its tool names and the hash is public, so this is constructible (a tool can
   shadow another Server's tool in the agent's view); a property test would also find it.
   The 8-hex hash can, in principle, collide too.

Smaller items: `ToolCallOutcome.text` is specified only for `denied`; what `Toolset.call`
may raise besides `KeyError` (cancellation, closed Host) is unstated, and a `KeyError`
from a stale LangChain snapshot would crash a LangGraph graph (S-6: `ToolNode` re-raises);
the wheel built today declares a console script `reflex_mcphost.app.cli:main` that does
not exist until F6-08, so `uvx reflex-mcphost==0.1.0a1` would fail; TD §9.1 describes one
release step that both tags and publishes, without a non-publishing verification path or
the Trusted-Publishing environment the owner must configure; TD §10 still names
`StructuredTool` for S-6.

No requirement is weakened. The public API surface (names in API §3.6) is unchanged.

## Decisions proposed

- D1 The framework-neutral Toolset implementation lives in `reflex_mcphost.core.toolset`;
  `reflex_mcphost.toolset` is the public module and re-exports it; adapters may import
  only `reflex_mcphost.toolset` (new forbidden contract `adapters ↛ core`).
- D2 Alias rule completed: a plain candidate equal to any hashed alias is hashed as well;
  a residual equality is broken by a deterministic ordinal suffix.
- D3 Agent text for every outcome; `Toolset.call` raising contract; adapters turn a
  vanished alias into a tool error.
- D4 `0.1.0a1` ships no console script; release = build/check/dry-run on every run, upload
  only from a `v*` tag in GitHub environment `pypi` via Trusted Publishing; the tag and
  upload are an owner action (Plan F5-06 split into F5-06a / F5-06b).
- D5 Clarifications and editorial fixes (filters on bare ids, collision domain, Consumer
  kinds, "per run step", TD §10 S-6 row).

## Alternatives considered

- D1: (a) remove `Host.toolset()` and add `Toolset.for_host(host, consumer, …)` in
  `reflex_mcphost.toolset` — keeps the package split but changes API §4.1 and every example
  (rejected: public API churn for an internal placement problem); (b) a dynamic
  `importlib.import_module("reflex_mcphost.toolset")` inside `Host.toolset()` — passes the
  linter only by hiding the edge (rejected: violates DD-018 in substance); (c) move the
  `toolset` layer below `core` — impossible, the Toolset needs the Catalog and Invoker.
- D2: hash every alias (rejected: unreadable names for the common case); longer hash
  (`[:47] + 16 hex`) — reduces but does not remove residual collisions and still needs a
  tie-break.
- D4: keep `[project.scripts]` and ship a stub CLI that prints "UI ships in 0.1.0b1"
  (rejected: adds a CLI surface the API §6 does not define for a1); publish from
  `workflow_dispatch` (rejected: releases must come from immutable tags); a long-lived
  PyPI API token (rejected: Trusted Publishing is DD-015 and leaves no secret to leak).

## Impact

Tasks F5-01…F5-06b (Tasks 1.11) already follow these decisions. If a decision is rejected,
the blocks citing it are revised before implementation. Owner actions outside the repo are
listed in D4 and in F5-06b; nothing is published by approving this delta.
