# Delta Spec — 2026-10-phase5-prep

> Status: `PROPOSED` · Source: Analyze `specs/analyze/2026-10-31-mcphost-v0.1-phase5.md`.
> Each item names its target document and section and whether the text is **ADDED**,
> **MODIFIED** or **CONFIRMED**. Nothing here is folded until the owner approves.

## Edits on approval

- **API 1.9 → 1.10**: §4.12 placement note (D1), alias algorithm (D2), outcome text and
  raising contract (D3), filter and Consumer clarifications (D5); §4.13 adapter error
  conversion (D3), "per run step" (D5).
- **TD 1.9 → 1.10**: §3.2 component row "`Toolset`" (D1); §5.1 layout (D1); DD-018
  contract 5 (D1); DD-015 release bullet and §9.1 (D4); §10 S-6 row (D5).
- **Plan 1.5 → 1.6**: Phase 5 row F5-06 split into F5-06a / F5-06b; §2 prerequisite text
  (D4).
- **Tasks 1.11**: already written against this delta (§Phase 5).

## D1 — Toolset placement vs layering (P5-01) · HIGH

**TD §3.2 component table, row `Toolset` — MODIFIED**: module column
`toolset` → `core.toolset` (implementation; framework-neutral) · `toolset` (public
re-export).

**TD §5.1 layout — MODIFIED**:

```
├── core/
│   ├── toolset/                # aliases.py, filters.py, toolset.py (Toolset, ToolSpec, ToolCallOutcome)
├── toolset/                    # __init__.py: public re-export of core.toolset (API §4.12)
├── adapters/                   # pydantic_ai.py, langgraph.py (optional extras; import reflex_mcphost.toolset only)
```

**TD DD-018 — ADDED** contract 5: `forbidden`: `reflex_mcphost.adapters` ↛
`reflex_mcphost.core` (direct imports; adapters reach core only through
`reflex_mcphost.toolset`). Contracts 1–4 **CONFIRMED** unchanged (`core` and `toolset`
still import no framework; `core.toolset` is inside `core`, so the Art. 4 forbidden
contract covers it).

**API §4.12 — ADDED** (after the heading): "Defined in a private core module and
re-exported by `reflex_mcphost.toolset`, which is the public import path (§3.6).
`Host.toolset()` constructs it."

Rationale: `Host.toolset()` (API §4.1) lives in `core.host`; import-linter rejects a
`core → toolset` import even at function level. The public surface does not change.

## D2 — Collision-free aliases (P5-02) · HIGH

**API §4.12, bullet "Alias algorithm" — MODIFIED** (replaces it):

```
Alias algorithm (deterministic for a given listing; order-independent):
1. c(t) = sanitize(server_id) + "__" + sanitize(name), where sanitize replaces
   [^A-Za-z0-9_-] with "_"; h(t) = c(t)[:55] + "_" + sha256(str(fqn)).hexdigest()[:8].
2. H = tools for which sanitize changed anything, len(c(t)) > 64, or c(t) equals the
   candidate of another tool in the listing.
3. Until nothing changes: a tool not in H whose c(t) equals h(u) for some u in H joins H.
4. alias(t) = h(t) if t in H else c(t).
5. If two tools still share an alias (two equal hashes), sort them by str(fqn); the first
   keeps it, the k-th (k = 1, 2, …) gets alias[:61] + "_" + f"{k:02d}", skipping any value
   already in use.
The listing is the Toolset's own (ready Servers, model-visible, after include/exclude),
so the same FQN may get different aliases in differently filtered Toolsets.
```

Rationale: as written, a tool whose plain name equals another tool's hashed alias keeps
it (constructible by a Server, since names and the hash are public) and REQ-TLS-002
"collision-free" fails. Step 5 makes the guarantee unconditional.

## D3 — Agent-visible outcomes and errors (P5-03, P5-04, P5-05) · MEDIUM

**API §4.12, `ToolCallOutcome.text` — ADDED** mapping:

| `result.outcome` | `text` |
|---|---|
| `success` | `result.text`; if empty and `structured_content` is set, its compact JSON |
| `tool_error` | `result.text`, or `"The tool reported an error"` if empty |
| `denied` | `"Permission denied by the host: " + result.error.message` |
| any other | `"Tool call failed (<outcome>): " + result.error.message` |

`error.message` is the Invoker's bounded, redacted message (never argument values, Art. 5).

**API §4.12, `call()` bullet — MODIFIED**: "`call()` never raises for invocation outcomes
(REQ-TLS-005). It raises `KeyError` for an alias not in the current listing (unknown,
filtered, app-only or no longer `ready`), `RuntimeError` / `HostClosedError` when used
before `start()` / after `aclose()`, and propagates the cancellation of the awaiting task
(the Invoker records `cancelled`, §4.8). `call()` resolves the alias against a fresh
listing, so visibility and filters are re-checked at call time (REQ-APP-008)."

**API §4.13 — ADDED** bullet: "Adapters return every error outcome through the
framework's tool-error channel — PydanticAI `ToolFailed(text)`; LangChain
`ToolException(text)` with `handle_tool_error=True` — and treat a `KeyError` from
`Toolset.call` (alias gone since the listing or snapshot) the same way with
`"Tool is no longer available: <name>"`. No other exception escapes an adapter tool,
because LangGraph's `ToolNode` re-raises it and fails the graph (S-6). LangChain tools are
async-only (`_run` raises `NotImplementedError`)."

## D4 — `0.1.0a1` packaging and release (P5-06, P5-07) · MEDIUM

**TD DD-015 release bullet and §9.1 — MODIFIED** (replaces the GitHub Actions bullet of
§9.1):

```
- .github/workflows/release.yml, on every run (PR touching release inputs,
  workflow_dispatch, tag): uv build once → wheel + sdist checks (no tests/, spikes/,
  specs/, changes/; no mcp.server import — Art. 1; version = tag; every console script
  resolvable; extras declared) → `uv publish --dry-run` → install-smoke of the built wheel
  on Linux/macOS/Windows.
- Upload only on a pushed `v*` tag: job `publish` in GitHub environment `pypi` (owner as
  required reviewer), job-level `id-token: write`, `uv publish --trusted-publishing
  always` with the artifacts of the build job. No API token exists.
- Owner prerequisites: PyPI Trusted Publisher (repo ecrespo/reflex-mcphost, workflow
  release.yml, environment pypi); the `pypi` environment; tag protection for `v*`.
  Tagging and approving the deployment are owner actions.
- docs/ (incl. oauth/client.json) is deployed by pages.yml on pushes to main, which the
  release merge triggers; the CIMD URL is frozen from 0.1.0 on (REQ-AUTH-012).
- A published version is never re-uploaded: a bad pre-release is yanked and superseded.
```

**TD §9.1 — ADDED**: "`0.1.0a1` contains no console script; `[project.scripts]
reflex-mcphost` is added with the CLI (Plan F6-08). `reflex` remains a runtime dependency
in a1 (one distribution, no UI code shipped)."

**Plan Phase 5 table — MODIFIED**: row F5-06 replaced by

| ID | Task | REQ / ref | Estimate | Depends on |
|---|---|---|---|---|
| F5-06a | Release workflow: build once, wheel/sdist checks, `uv publish --dry-run`, install smoke on 3 OS; `publish` job gated on `v*` tag + environment `pypi` | DD-015, TD §9.1 | 0.4 d | F5-05 |
| F5-06b | **Owner action**: Trusted Publisher + environment configured, release approved, tag `v0.1.0a1`, publish | DD-015, TD §9.1 | 0.1 d | F5-06a, Phase 4 |

Plan F6-01 "Depends on F5-06" → **F5-06a** (UI work does not wait for the upload). Plan §2
row "PyPI project reserved with Trusted Publishing configured" gains "and GitHub
environment `pypi`; gates F5-06b only".

## D5 — Clarifications and editorial (P5-09, P5-10, P5-11, P5-13) · LOW

- **API §4.12 filters — MODIFIED**: "Entries with `::` are case-sensitive globs on the
  FQN; entries without `::` are globs on the server id (`"*"` = all, `"github"` = exactly
  Server `github`). Exclude wins."
- **API §4.1 `toolset()` — ADDED**: "Any `Consumer` kind is accepted; consent is evaluated
  for that Consumer (§4.7). App-originated calls use the bridge (§7), not a Toolset."
- **API §4.13 — MODIFIED**: "(PydanticAI toolsets are re-listed per run)" → "(PydanticAI
  re-lists the toolset before every model request, i.e. per run step — S-6)".
- **TD §10 S-6 row — MODIFIED**: "LangChain `StructuredTool` with dict `args_schema`" →
  "LangChain `BaseTool` subclass with dict `args_schema` (not `StructuredTool`, which
  drops `config`/`run_manager` arguments)" — aligns with the §3.2 row folded in
  `2026-10-spike-batch-a`.

## CONFIRMED (no edit)

- API §4.13 signatures `as_pydantic_ai_toolset(toolset) -> AbstractToolset` and
  `as_langchain_tools(toolset) -> list[BaseTool]` (S-6; re-checked against
  `pydantic-ai-slim` 2.64.0 and `langchain-core` 1.7.3 in `.venv`).
- TD §0 extras bounds (`pydantic-ai-slim>=2.64,<3`, `langchain-core>=1.7,<2`;
  `langgraph>=1.2,<2` test group only) match `pyproject.toml` and `uv.lock`.
- REQ-TLS-001…008 wording (PRD 1.7) — no PRD edit.
- S-4 / F0-04 gates only F8-07 (delta `2026-10-phase4-prep` D9); it does not gate
  `0.1.0a1`.
