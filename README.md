# reflex-mcphost

An open MCP host built with Reflex. Connect MCP servers (stdio and Streamable HTTP), browse
their tools, resources and prompts, render MCP Apps, and expose everything to any agent with
a consent layer — all in pure Python. Library plus a reference desktop-style app: the open
Claude Desktop for Python developers.

> **Status: `0.1.0` release candidate** (the package version stays `0.1.0.dev0` until the
> release PR; nothing is on PyPI yet). The host core works as a library: it discovers
> Servers from every config source, keeps one supervised connection per Server on both
> protocol eras, maintains a live catalog, invokes tools through a fail-closed consent layer
> with a local audit log, answers Server requests (elicitation, sampling, roots, logging)
> through one `Prompter` seam, signs in to remote Servers with OAuth 2.1, and exposes the
> tools to agents (framework-neutral `Toolset`, PydanticAI and LangGraph adapters), with an
> embeddable Reflex UI and the `reflex-mcphost` reference app and CLI, which renders MCP
> Apps in sandboxed Views. Known limitations of `0.1.0` are listed in
> [`CHANGELOG.md`](CHANGELOG.md).

## What it does (v0.1)

- **Host, never server** — speaks to MCP servers as a client, on both protocol eras:
  `2026-07-28` (modern, MRTR input requests) and `2025-11-25` (legacy).
- **Registry** of servers from `mcphost.toml` (user and project) and the Claude Desktop,
  Cursor, VS Code and `.mcp.json` files (read-only), with a deterministic source precedence
  and secrets kept in the OS keyring.
- **Catalog** of tools, resources and prompts across servers, addressed by FQN
  (`server_id::name`).
- **Consent as a first-class, auditable policy** — `allow | ask | deny` rules per consumer
  (human, agent, app), fail-closed by default, every decision recorded in a local audit log.
- **Adapters** that expose the host's toolset to PydanticAI and LangChain/LangGraph agents.
- **Reflex UI and reference app**, including MCP Apps rendered in sandboxed views.

Guides: [`docs/user-guide.md`](docs/user-guide.md) (the reference app: Servers, consent,
audit, elicitation and sampling, MCP Apps, remote mode) and
[`docs/library.md`](docs/library.md) (the library, agents, embedding the UI in your own
Reflex app).

## Install

```bash
uvx --compile-bytecode reflex-mcphost      # run the reference app without installing it
pip install reflex-mcphost                 # the library (core, Toolset, Reflex UI, CLI)
pip install 'reflex-mcphost[pydantic-ai]'  # + PydanticAI adapter; [langgraph]; [all]
```

Python ≥ 3.11 on Linux, macOS or Windows. `--compile-bytecode` makes the first start of a
fresh `uvx` environment faster (the reference app imports a lot of code once).

## Progress

| Phase | Scope | State |
|---|---|---|
| 0 | Technical spikes (SDK surface, stdio, MCP Apps origins, Reflex, adapters) | ✅ done · S-4 (OAuth/CIMD against a real authorization server) closed with plan B — no real authorization server available; the CIMD → DCR → configured `client_id` chain is tested against a fixture server, real-server CIMD unverified |
| 1 | Foundation: packaging, quality gates, CI, `core` (errors, types, secrets, logging, events, SQLite + migrations), fixture servers, contract tests | ✅ done |
| 2 | Registry (5 config formats, precedence, trust for project Servers), supervised connections (stdio + Streamable HTTP, both eras, backoff, era cache, graceful shutdown), live catalog (watchers, reads) | ✅ done |
| 3 | Consent (rules, ranking, prompts, rug-pull protection), audit log, invoker (single effect path, MRTR loop, deadlines, cancellation, progress) | ✅ done |
| 4 | Host-side handlers (elicitation, sampling, roots, logging) and OAuth 2.1 (PKCE, CIMD → DCR → manual `client_id`, refresh, sign-out, issuer- and resource-bound tokens) | ✅ done |
| 5 | Toolset (aliases, filters, consent-checked calls), PydanticAI and LangGraph adapters, release workflow | ✅ done |
| 6 | Reflex UI (embeddable components, consent side panel), reference app and CLI (`reflex-mcphost run` / `servers`), headless + Playwright E2E | ✅ done |
| 7 | MCP Apps (sandbox proxy on a distinct origin, data-only relay, App bridge through the consent-checked `Invoker`, per-tab Views, teardown, `ui/open-link`), E2E on Chromium, Firefox and WebKit | ✅ done |
| 8 | Hardening: bounded Server regexes, definition re-check after consent, process-group cleanup, `Referrer-Policy`, MEMORY state manager enforced, proxy connection cap, navigated Views closed, workspace pruning, threat-model walk-through, benchmarks, resilience, strict REQ gate, docs, `0.1.0` preparation | ✅ done · owner actions left: tag `v0.1.0` and PyPI publish (real Safari pass waived — WebKit via Playwright is the coverage; S-4 closed with plan B) |

Detailed task status lives in [`specs/tasks/mcphost-v0.1-tasks.md`](specs/tasks/mcphost-v0.1-tasks.md).

## Run the UI

```bash
uvx --compile-bytecode reflex-mcphost            # = reflex-mcphost run; opens http://127.0.0.1:3000/
uvx reflex-mcphost run --port 3917 --no-browser --config ./mcphost.toml
uvx reflex-mcphost servers --json                # merged Server list, no UI, no connections
```

`run` serves the reference app (Servers, Catalog, Permissions, Audit, and a consent side
panel) on **one** loopback port, `http://127.0.0.1:<port>/`; open exactly that address —
`localhost` and any other `Host`/`Origin` get a `403` naming it (DNS-rebinding guard). The
first start of a version installs Bun and builds the frontend (network needed, about a
minute); later starts take a few seconds. The app's build workspace lives in the user cache
(`~/.cache/reflex-mcphost/app/` on Linux), never in site-packages; one directory per
installed version, and directories of other versions unused for 7 days are pruned at start
(leftovers of an interrupted build after 1 day; only directories the CLI created and
marked). Binding another
interface needs `--host <address> --unsafe-allow-remote` (anyone who can reach it can drive
the Host); wildcard addresses are refused. Ctrl-C shuts the Host down (every Server
disconnected) and exits 0. Exit codes: `2` refused option, `3` start-up failure (invalid
config, database, Reflex). To embed the same views in your own Reflex app, call
`reflex_mcphost.ui.install(app, host_factory)` and use the components of
`reflex_mcphost.ui` (API §8).

### MCP Apps

When you invoke a tool from the Catalog whose definition points at a UI resource
(`_meta.ui.resourceUri`, MIME `text/html;profile=mcp-app`) and consent allows it, the
Server's App is shown below the result once the invocation ends (MCP Apps `2026-01-26`).
The View never runs on the UI's origin: it is served through a sandbox proxy on
`http://localhost:<proxy_port>` (a separate listener on `127.0.0.1` and `::1`) inside an
opaque-origin `srcdoc` frame (`sandbox="allow-scripts"`, no camera, microphone,
geolocation or clipboard), with a content security policy built only from the domains the
resource declares (invalid ones are dropped and listed in a notice). Every tool call the
App makes goes through the same consent layer as yours, as the Consumer
`app:<server_id>` — you see a consent card and the call is audited — and only for its own
Server's App-visible tools. A link the App wants to open is shown in full and opens in a new
tab only after you approve it. A new invocation, the Close button, removing the Server or
stopping the Host tears the View down.

Limits in `0.1.0`: Views render only for invocations started from the UI and only after
they end; a View that navigates its own frame away is closed ("The App navigated away and
was closed."), but that navigation may already have carried what the View was given
(Technical Design §6.1); Apps are off in remote mode (`--unsafe-allow-remote`) or when
`localhost` does not resolve to loopback only. Links opened from a View carry no referrer.
If you embed `reflex_mcphost.ui` in your own app, read the embedder rules in
[`docs/library.md`](docs/library.md#embedding-the-ui-in-your-reflex-app): the UI refuses to
start with any state manager other than MEMORY.

## Using the library

<!-- snippet: run -->
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

### Remote Servers with OAuth

```python
flow = await host.connections.authorize("linear")  # Server is in `auth_required`
print("Open in your browser:", flow.authorization_url)  # the core never opens one itself
await host.connections.wait_ready("linear", timeout=300)
```

Tokens live in the OS keyring (or a `0600` file fallback), bound to the authorization
server's issuer and to the Server URL; `sign_out("linear")` deletes them (if the secret
store cannot, it raises `SecretStoreError` after forgetting the in-memory tokens). The
client is identified by the project's Client ID Metadata Document
(`https://blog.seraph.to/reflex-mcphost/oauth/client.json`), then Dynamic Client
Registration (*deprecated, compatibility*), then a `client_id` you set in `mcphost.toml`.

### Use the Host from an agent

<!-- snippet: run model=TestModel -->
```python
import anyio
from pydantic_ai import Agent

from reflex_mcphost import ConsentTarget, Consumer, Host, OperationKind
from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset


async def main() -> None:
    async with Host() as host:
        await host.connections.wait_ready("filesystem", timeout=30)
        helper = Consumer.agent("helper")
        await host.consent.grant(
            effect="allow",
            operation=OperationKind.TOOL_CALL,
            target=ConsentTarget.server("filesystem"),
            consumer=helper,
        )
        tools = as_pydantic_ai_toolset(host.toolset(helper, include=["filesystem"]))
        agent = Agent("anthropic:claude-sonnet-5-5", toolsets=[tools])  # any PydanticAI model
        result = await agent.run("What does /tmp/hello.txt say?")
        print(result.output)


anyio.run(main)
```

Agents see aliases such as `filesystem__read_text_file`. A denied call is not an exception:
the model receives `Permission denied by the host: …` as a failed tool result and the run
goes on; the attempt is in the audit log under `agent:helper`. Install the extra with
`pip install 'reflex-mcphost[pydantic-ai]'` (or `[langgraph]` for
`as_langchain_tools`). Filters, outcome texts, LangGraph and testing without an LLM
(PydanticAI `TestModel`) are in [`docs/library.md`](docs/library.md).

### Deprecated protocol features

The pinned MCP revision marks some features *Deprecated*; the Host implements them only as
compatibility (Constitution Art. 2) and labels them so in code and UI:

- **Sampling** — served only when you configure a `SamplingProvider`, always behind a
  consent card (and an optional response review).
- **Roots** — only `file://` roots you configure per Server; none by default.
- **Logging** — the Servers view's "Log level (deprecated)" select or
  `connections.set_log_level`; no level is sent by default.
- **Dynamic Client Registration** — the second OAuth fallback, after the Client ID
  Metadata Document.
- **HTTP+SSE transport** — not shipped in `0.1.0` (REQ-CON-013 deferred); use stdio or
  Streamable HTTP.

## Development

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/) (the only supported package
manager).

```bash
uv sync --all-extras --all-groups      # install
uv run pytest                          # tests (legacy-era tests need uv on PATH)
uv run playwright install --with-deps chromium firefox webkit   # once, for the E2E tests
uv run pytest -m e2e                   # E2E: built wheel + frontend build (network), Linux
uv run ruff check . && uv run ruff format --check .
uv run mypy                            # strict
uv run lint-imports                    # layer contracts (core → toolset → adapters → ui → app)
```

Without root for `--with-deps`, WebKit may lack system libraries: extract them
(`apt-get download libmanette-0.2-0 libhidapi-hidraw0`, `dpkg-deb -x`) and run the E2E
tests with `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` and
`MCPHOST_E2E_WEBKIT_PRELOAD=<dir>/libhidapi-hidraw.so.0:<dir>/libmanette-0.2.so.0` (passed
as `LD_PRELOAD` to WebKit only; spike S-3).

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
