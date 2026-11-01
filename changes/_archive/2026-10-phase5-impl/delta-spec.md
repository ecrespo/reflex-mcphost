# Delta spec — 2026-10-phase5-impl

> Status: `APPROVED` (owner's standing auto-approval) · folded 2026-11-01 ·
> Source: deviations reported while implementing Phase 5 batch O (F5-01…F5-05) and F5-06a
> plus the supply-chain hardening of the release path, all in commit `dbdc23a` on
> `develop`. Every item was checked against the code (`core/toolset/*`, `toolset/__init__.py`,
> `adapters/{pydantic_ai,langgraph}.py`, `core/host.py`, `.github/workflows/{release,ci,pages}.yml`,
> `.github/dependabot.yml`, `scripts/check_wheel.py`, `pyproject.toml`); where the reported
> wording and the code differed, the code wins and the item says so ("Code check").
> Constitution unaffected. PRD unaffected (no requirement changes meaning).

Every item below is now the current truth. Each item names its target document and
section and whether the text is **ADDED** (new normative text), **MODIFIED** (replaces or
narrows existing text) or **CONFIRMED** (implementation matches the spec; recorded for
traceability, no edit). The applied edits are summarized first.

## Applied edits

- **API 1.10 → 1.11**: §4.12 rules — `call()` raising contract (lifecycle checked on every
  call, before alias resolution; item 1), filter argument types (item 2), a Server gone
  from the registry counts as not `ready` (item 3), denial / failure text fallback when the
  result carries no `InvocationError` (item 4); §4.13 rules — the `KeyError` conversion is
  done by **both** adapters and wraps only `Toolset.call` (item 5), a non-object LangChain
  input becomes a tool error (item 6), the §4.13 example's model string is illustrative
  (item 9).
- **TD 1.10 → 1.11**: §9.1 release workflow details (triggers, concurrency, smoke matrix,
  `check_wheel.py` checks — items 10–12) and the supply-chain bullet (item 13); DD-015 release
  bullet (item 13); §6.1 new row "Compromised or re-pointed CI dependency in the release
  path" (item 13); §8 Architecture row gains the docs-snippet test (item 8); §3.2 `Adapters`
  row (item 6).
- **Plan**: not affected (no task, dependency or estimate changes; F5-06a/F5-06b rows stand).
- **Tasks 1.11 → 1.12**: execution-log row for batch O and F5-06a; F5-06a stays `[~]`
  (item 14); F5-06b stays `[!]`.

## Deferred / known gaps

| # | Gap | Where it is closed |
|---|---|---|
| G-1 | **Owner action F5-06b:** PyPI Trusted Publisher (pending publisher) for `reflex-mcphost` — owner `ecrespo`, repo `reflex-mcphost`, workflow `release.yml`, environment `pypi`; GitHub environment `pypi` with the owner as required reviewer, deployments restricted to `v*` tags, tag protection for `v*`; explicit approval of the release; then tag `v0.1.0a1` on `main`. Plan §2 PyPI row stays ☐. | Owner (F5-06b) |
| G-2 | **F5-06a PR run:** `release.yml` has only run locally (build, `check_wheel.py`, `uv publish --dry-run`, smoke). Its `pull_request` trigger fires only when one of its paths changes, so the first PR that carries `dbdc23a` to GitHub must show `build` + `smoke` green and `publish` skipped before F5-06a is `[x]`. Needs a push of `develop` / a PR (owner, or the agent on request). | F5-06a |
| G-3 | **Deviation 5 — `KeyError` masking.** Both adapters map *any* `KeyError` raised out of `Toolset.call` to "Tool is no longer available". `Toolset.call` only raises `KeyError` for an unknown alias today, but a `KeyError` escaping a future bug inside `Invoker.call` would be reported to the agent as a vanished tool instead of surfacing. Candidate: a dedicated `UnknownToolError(KeyError)` raised by `Toolset.resolve`/`call` and caught by the adapters (additive, still a `KeyError` for existing callers). | Phase 8 hardening (consider narrowing) |
| G-4 | The README / API §4.13 agent example names a real model string (`anthropic:claude-sonnet-5-5`) but was only executed with PydanticAI `TestModel` (no API key in CI, by design). | Accepted; F8-05 docs pass re-checks the example text |

## Full deviation list

### Batch O (F5-01…F5-05)

1. **API §4.12 `call()` raising contract — MODIFIED.** `Toolset` is built with
   `invoker_of`, a callable returning the Host's Invoker, not with an Invoker instance;
   `call()` evaluates it **first, on every call**, so `RuntimeError` (before `start()`) and
   `HostClosedError` (after `aclose()`) are raised on every `call()` of a Toolset that
   outlives its Host, before the alias is resolved (a lifecycle error wins over `KeyError`).
   `Host.toolset()` also checks the lifecycle when the Toolset is built. `list()` and
   `resolve()` read only the Catalog and the connection statuses and are not lifecycle-guarded.
   Code check: `Toolset.call` calls `self._invoker_of()` before `self.resolve(name)`;
   `Host.toolset` touches `self.invoker` as its guard.
2. **API §4.12 filters — ADDED.** `include` / `exclude` given as a bare `str` raise
   `TypeError` (a string would iterate as characters); so does any non-`str` entry. Code
   check: `core/toolset/filters.check_patterns` also rejects non-string entries (the report
   named only the bare-string case).
3. **API §4.12 listing — ADDED.** A catalog entry whose Server is no longer in the registry
   (`ConnectionManager.status` raises `UnknownServerError` while the Catalog still holds its
   entries) counts as not `ready`: the tool is not listed and its alias is not resolvable.
   Code check: `Toolset._listing.is_ready`.
4. **API §4.12 text table — ADDED.** When an error outcome carries no `InvocationError`
   (or an empty message), the text falls back to a fixed default: `denied` →
   `"Permission denied by the host: denied"`; any other non-success outcome →
   `"Tool call failed (<outcome>): <outcome>"`. Code check: `_agent_text`.
5. **API §4.13 rules — MODIFIED.** The conversion of a `KeyError` from `Toolset.call` into
   `"Tool is no longer available: <name>"` is implemented by **both** adapters (PydanticAI
   `ToolFailed`, LangChain `ToolException`), and the `try` wraps only the `Toolset.call`
   await. Code check: the report named only the PydanticAI adapter; `HostTool._arun` does
   the same. Any `KeyError` is converted, so an unrelated `KeyError` would be masked — gap
   G-3.
6. **API §4.13 rules — ADDED; TD §3.2 Adapters row — ADDED.** LangChain: a tool input
   that does not parse to a JSON object raises `ToolException` (so it becomes
   `ToolMessage(status="error")`, never a graph failure). PydanticAI: `HostToolset` has a
   read-only `toolset` property returning the wrapped `Toolset` (not in any `__all__`;
   implementation detail, not public API per §3.6). Code check: `HostTool._to_args_and_kwargs`,
   `HostToolset.toolset`.
7. **API §3.6 — CONFIRMED.** `Toolset`, `ToolSpec`, `ToolCallOutcome` are exported only
   from `reflex_mcphost.toolset` (its `__all__`), not from the top-level `reflex_mcphost`;
   §3.6 lists `reflex_mcphost.toolset` as a public module, so this is the specified surface.
   `tests/unit/test_public_api.py` checks that every class in an API §4 code block is
   exported by one of the two modules.
8. **TD §8 Architecture row — ADDED.** `tests/unit/test_library_docs.py` machine-checks that
   every non-import code line of the `docs/library.md` PydanticAI and LangGraph snippets
   appears verbatim in the integration test that drives it against the fixture Servers
   (`tests/integration/adapters/test_{pydantic_ai,langgraph}_agent.py`).
9. **API §4.13 example — ADDED (note).** The model string in the example (and the README) is
   illustrative; the snippet is verified with `TestModel` only (gap G-4).

### F5-06a and release hardening

10. **TD §9.1 — ADDED.** `release.yml` triggers: push of `v*` tags, `workflow_dispatch`, and
    `pull_request` limited to changes of `.github/workflows/release.yml`, `pyproject.toml`,
    `uv.lock`, `scripts/check_wheel.py`. Concurrency group `release-<ref>`;
    `cancel-in-progress` only for pull-request runs (a tag run is never cancelled
    half-way). The expected version is the tag without `v` on tag pushes, else the
    `pyproject.toml` version.
11. **TD §9.1 — ADDED.** Smoke job: Linux/macOS/Windows × Python 3.11/3.13 against the
    **built artifact** only (no checkout), in two fresh venvs — plain and `[all]`. Plain:
    REQ-TLS-008 check (no `pydantic_ai`, `langchain_core`, `langgraph`, `reflex` in
    `sys.modules` after importing `reflex_mcphost` and `reflex_mcphost.toolset`), installed
    `__version__` equals the expected version, the module is loaded from `site-packages`,
    and importing each adapter raises `ImportError` naming its extra. `[all]`: the same
    REQ-TLS-008/version check and both adapters import.
12. **TD §9.1 — ADDED.** `scripts/check_wheel.py dist [--expect-version X]` checks every wheel
    and sdist in the directory (at least one of each): forbidden paths `tests/`, `spikes/`,
    `specs/`, `changes/`, `.claude/` (wheel and sdist); no module importing `mcp.server`
    (wheel and sdist, AST); file-name and `METADATA`/`PKG-INFO` version = expected; every
    `console_scripts` entry point resolves to a module shipped in the wheel **and** that
    module binds the named attribute at top level; `Provides-Extra: pydantic-ai`,
    `langgraph`, `all`; `Description-Content-Type: text/markdown`. Code check: the sdist
    is not checked for extras or description (wheel only). `ci.yml`'s `build` job runs it
    without `--expect-version`.
13. **TD §9.1, DD-015, §6.1 — ADDED (security review).** Supply-chain hardening: every
    third-party action in `ci.yml`, `release.yml` and `pages.yml` is pinned to a full
    40-hex commit SHA with the release tag as a trailing comment (`# v5.1.0`); tags and
    branches are never used as action references. `.github/dependabot.yml` opens weekly
    grouped (`actions: ["*"]`) update PRs for the `github-actions` ecosystem, so pins do
    not rot; each such PR goes through normal review and CI (and, when it touches
    `release.yml`, the release PR run). The `publish` job pins the uv binary that performs
    the upload (`setup-uv` `version: "0.11.7"`); build and smoke use the `setup-uv`
    default. `publish` keeps job-level `permissions: contents: read, id-token: write`; no
    other job has `id-token`.
14. **Tasks F5-06a — CONFIRMED status `[~]`.** Implemented and verified locally; the PR run
    of `release.yml` is pending because the workflow runs on PRs only when its paths change
    (gap G-2).
