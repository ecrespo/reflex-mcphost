# reflex-mcphost

An open MCP host built with Reflex. Connect MCP servers (stdio and Streamable HTTP), browse
their tools, resources and prompts, render MCP Apps, and expose everything to any agent with
a consent layer — all in pure Python. Library plus a reference desktop-style app: the open
Claude Desktop for Python developers.

> **Status: pre-alpha (`0.1.0.dev0`).** The host core works as a library: it discovers
> Servers from every config source, keeps one supervised connection per Server on both
> protocol eras, maintains a live catalog, and invokes tools through a fail-closed consent
> layer with a local audit log. Host-side handlers (elicitation, sampling, roots), OAuth and
> the UI come next. Nothing is published to PyPI yet and the `reflex-mcphost` command is not
> usable until Phase 6.

## What it will do (v0.1)

- **Host, never server** — speaks to MCP servers as a client, on both protocol eras:
  `2026-07-28` (modern, MRTR input requests) and `2025-11-25` (legacy).
- **Registry** of servers from `mcphost.toml` (user and project), with a deterministic
  source precedence and secrets kept in the OS keyring.
- **Catalog** of tools, resources and prompts across servers, addressed by FQN
  (`server_id::name`).
- **Consent as a first-class, auditable policy** — `allow | ask | deny` rules per consumer
  (human, agent, app), fail-closed by default, every decision recorded in a local audit log.
- **Adapters** that expose the host's toolset to PydanticAI and LangChain/LangGraph agents.
- **Reflex UI and reference app**, including MCP Apps rendered in sandboxed views.

## Progress

| Phase | Scope | State |
|---|---|---|
| 0 | Technical spikes (SDK surface, stdio, MCP Apps origins, Reflex, adapters) | ✅ done · S-4 (OAuth/CIMD) blocked on a real authorization server |
| 1 | Foundation: packaging, quality gates, CI, `core` (errors, types, secrets, logging, events, SQLite + migrations), fixture servers, contract tests | ✅ done |
| 2 | Registry (5 config formats, precedence, trust for project Servers), supervised connections (stdio + Streamable HTTP, both eras, backoff, era cache, graceful shutdown), live catalog (watchers, reads) | ✅ done |
| 3 | Consent (rules, ranking, prompts, rug-pull protection), audit log, invoker (single effect path, MRTR loop, deadlines, cancellation, progress) | ✅ done |
| 4 | Host-side handlers and OAuth | ⏳ next |
| 5 | Toolset, adapters and `0.1.0a1` | — |
| 6 | Reflex UI, reference app and CLI | — |
| 7 | MCP Apps and `0.1.0b1` | — |
| 8 | Hardening and `0.1.0` | — |

Detailed task status lives in [`specs/tasks/mcphost-v0.1-tasks.md`](specs/tasks/mcphost-v0.1-tasks.md).

## Using the library today

```python
import anyio
from reflex_mcphost import ConsentTarget, Consumer, Host, OperationKind


async def main() -> None:
    async with Host() as host:  # reads mcphost.toml + Claude/Cursor/VS Code configs
        await host.connections.wait_ready("filesystem", timeout=30)
        for tool in host.catalog.tools():
            print(tool.fqn, tool.description)

        agent = Consumer.agent("helper")
        # Headless (no Prompter): anything without an allow rule is denied and audited.
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("filesystem"),
            consumer=agent,
        )
        result = await host.invoker.call(
            "filesystem::read_text_file", {"path": "/tmp/hello.txt"}, consumer=agent
        )
        print(result.outcome, result.content)

        page = await host.audit.query()  # every attempt, newest first
        for record in page.records:
            print(record.fqn, record.outcome)


anyio.run(main)
```

Every tool call goes through one path: resolve → Server ready → validate arguments →
consent → audit → `tools/call`. Failures (denied, timeout, invalid arguments, …) come back
as an `InvocationResult.outcome`, never as a silent bypass.

## Development

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/) (the only supported package
manager).

```bash
uv sync --all-extras --all-groups      # install
uv run pytest                          # tests (legacy-era tests need uv on PATH)
uv run ruff check . && uv run ruff format --check .
uv run mypy                            # strict
uv run lint-imports                    # layer contracts (core → toolset → adapters → ui → app)
```

Quality gates (Constitution Art. 7–8): core coverage ≥ 90 %, total ≥ 80 %, mypy strict,
import-linter contracts, gitleaks. CI runs on Linux, macOS and Windows with Python 3.11
and 3.13.

## How this project is built

reflex-mcphost follows **spec-driven design** (spec-anchored): no code without an approved
spec.

- [`specs/constitution.md`](specs/constitution.md) — non-negotiable principles.
- [`specs/prd/`](specs/prd/) — requirements with EARS acceptance criteria (`REQ-*` IDs).
- [`specs/api/`](specs/api/), [`specs/technical/`](specs/technical/),
  [`specs/data-model/`](specs/data-model/) — contract, design and schema.
- [`specs/plans/`](specs/plans/) and [`specs/tasks/`](specs/tasks/) — phased plan and
  executable tasks; every task cites the `REQ` it implements, every test cites its criterion.
- [`changes/`](changes/) — Delta Specs; approved ones are folded into `specs/` and archived.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

## License

MIT — see [LICENSE](LICENSE).
