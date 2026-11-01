# Using reflex-mcphost as a library

This guide covers the agent bridge (the `Toolset` and the PydanticAI and LangGraph
adapters) and embedding the Host UI in your own Reflex app. For the reference app, see
[`user-guide.md`](user-guide.md).

The Host exposes the tools of every connected MCP Server to agents through a
framework-neutral `Toolset` (API Spec §4.12) and two optional adapters: PydanticAI and
LangChain/LangGraph (§4.13). Every agent call goes through the same path as any other tool
call — Server ready → argument validation → consent → audit → `tools/call` — so an agent
cannot bypass consent, and every attempt leaves one audit record.

## Install

```bash
pip install reflex-mcphost                 # core + Toolset, no agent framework
pip install 'reflex-mcphost[pydantic-ai]'  # + PydanticAI adapter (pydantic-ai-slim)
pip install 'reflex-mcphost[langgraph]'    # + LangChain adapter (langchain-core)
pip install 'reflex-mcphost[all]'
```

`import reflex_mcphost` and `import reflex_mcphost.toolset` never import an agent
framework. Importing an adapter without its extra raises `ImportError` naming the extra.
The LangGraph adapter imports only `langchain_core`; install `langgraph` yourself to build
graphs.

## The Toolset

```python
from reflex_mcphost import Consumer, Host

async with Host() as host:
    toolset = host.toolset(
        Consumer.agent("helper"), include=["github"], exclude=["github::delete_*"]
    )
    for spec in toolset.list():  # sync, always the live catalog
        print(spec.name, spec.fqn, spec.description)
    outcome = await toolset.call("github__search_issues", {"query": "is:open"})
    print(outcome.is_error, outcome.text)
```

- **Consumer.** Consent rules and audit records are per Consumer: give each agent its own
  `Consumer.agent("<name>")` and grant it rules (`host.consent.grant(..., consumer=...)`).
  A headless Host (no `Prompter`) denies anything without an allow rule.
- **Listing.** `list()` reads the catalog on every call: only tools of `ready` Servers that
  are not stale and whose MCP Apps visibility includes `"model"` (app-only tools are never
  exposed to agents).
- **Filters.** `include` / `exclude` entries with `::` are case-sensitive globs on the FQN
  (`github::*`, `*::search`); entries without `::` are globs on the server id (`"*"` = all,
  `"github"` = exactly Server `github`, not `github2`). Exclude wins.
- **Names.** Agents see an alias `<server_id>__<name>` matching `^[A-Za-z0-9_-]{1,64}$`.
  Names that need sanitizing, are too long or would collide get a hash suffix
  (`<prefix>_<8 hex>`). Aliases are deterministic for a given listing, but a tool that
  appears later can make an existing alias change (both colliding tools become hashed);
  `toolset.resolve(alias)` maps an alias back to its FQN.
- **Outcomes.** `call()` never raises for an invocation outcome. It returns a
  `ToolCallOutcome` with `is_error` (any outcome other than `success`) and the text the
  agent sees:

  | Outcome | `text` |
  |---|---|
  | `success` | the tool's text content (or its structured content as compact JSON) |
  | `tool_error` | the tool's error content, or `The tool reported an error` |
  | `denied` | `Permission denied by the host: <reason>` |
  | anything else | `Tool call failed (<outcome>): <message>` |

  It raises `UnknownToolError` (a subclass of both `MCPHostError` and `KeyError`, so
  `except KeyError` keeps working) for an alias not in the current listing (unknown,
  filtered, app-only or its Server not `ready`), `RuntimeError` / `HostClosedError` before
  `start()` / after `aclose()`, and propagates cancellation. Texts never contain argument
  values. The adapters turn only `UnknownToolError` into "Tool is no longer available";
  any other exception propagates.

## PydanticAI

`as_pydantic_ai_toolset(toolset)` returns a PydanticAI `AbstractToolset`. PydanticAI
re-lists it before every model request, so tools added or removed during a run are picked
up at the next step. Error outcomes reach the model as a failed tool return
(`ToolFailed`), without consuming the retry budget, and the run continues.

<!-- snippet: verbatim tests/integration/adapters/test_pydantic_ai_agent.py -->
```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from reflex_mcphost import Consumer
from reflex_mcphost.adapters.pydantic_ai import as_pydantic_ai_toolset

tools = as_pydantic_ai_toolset(host.toolset(Consumer.agent("helper"), include=[server_id]))
agent = Agent(TestModel(call_tools=[f"{server_id}__echo"]), toolsets=[tools])
run = await agent.run("Echo something")
```

Replace `TestModel(...)` with any PydanticAI model (`"anthropic:..."`, `"openai:..."`) in
an application. This exact snippet runs in
`tests/integration/adapters/test_pydantic_ai_agent.py` against the fixture Servers of both
protocol eras, once with an allowing and once with a denying prompter.

## LangChain / LangGraph

`as_langchain_tools(toolset)` returns one async-only `BaseTool` per listed tool (sync
`invoke` raises `NotImplementedError`; use `ainvoke`). The list is a **snapshot**: build it
again after a `catalog.changed` event. Error outcomes — and a tool that vanished since the
snapshot — become `ToolMessage(status="error")`, so a `ToolNode` never fails the graph.
Arguments named `config`, `run_manager`, `callbacks` or `self` reach the tool unchanged.

<!-- snippet: verbatim tests/integration/adapters/test_langgraph_agent.py -->
```python
from typing import Any

from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from reflex_mcphost import Consumer
from reflex_mcphost.adapters.langgraph import as_langchain_tools


def build_agent(model: Any, tools: list[Any]) -> Any:
    """model → (tool calls?) → ToolNode → model, until the model answers."""
    llm = model.bind_tools(tools)

    async def call_model(state: MessagesState) -> dict[str, Any]:
        return {"messages": [await llm.ainvoke(state["messages"])]}

    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()


tools = as_langchain_tools(host.toolset(Consumer.agent("helper"), include=[server_id]))
state = await build_agent(model, tools).ainvoke({"messages": [("user", "echo hello")]})
```

`build_agent` and the call are copied from
`tests/integration/adapters/test_langgraph_agent.py`, where `model` is a scripted fake chat
model; any LangChain chat model with tool calling works the same way.

## Testing agents without an LLM

Nothing here needs an API key. The project's own tests use PydanticAI `TestModel` /
`FunctionModel` and a LangChain `GenericFakeChatModel` subclass that accepts `bind_tools`,
against real fixture MCP Servers over stdio, so consent prompts, denials and audit records
are exercised end to end.

## Embedding the UI in your Reflex app

`reflex_mcphost.ui` (API Spec §8) gives you the reference app's pieces — `servers_panel`,
`catalog_browser`, `consent_center`, `permissions_table`, `audit_table`, `mcp_app_frame` —
and `install(app, host_factory)`, which makes your `rx.App` own one `Host` for the backend's
lifespan, binds the UI prompter and guards the backend. The rules below are checked at
start-up; a violation is a `RuntimeError` naming the setting, not a silent fallback.

<!-- snippet: rxconfig -->
```python
import reflex as rx

config = rx.Config(
    app_name="my_app",
    deploy_url="http://127.0.0.1:3000",
    api_url="http://127.0.0.1:3000",
    cors_allowed_origins=["http://127.0.0.1:3000"],
    state_manager_mode=rx.constants.StateManagerMode.MEMORY,
)
```

<!-- snippet: embed -->
```python
import reflex as rx

from reflex_mcphost import Host
from reflex_mcphost.ui import HostState, catalog_browser, consent_center, install


def tools_page() -> rx.Component:
    return rx.hstack(catalog_browser(), consent_center())


app = rx.App()
app.add_page(tools_page, route="/tools", on_load=HostState.start_watch)
install(app, Host)  # one Host per backend process, started and closed with the app
```

- **MEMORY state manager — mandatory.** Tab state holds Server-provided and confidential
  data (invocation arguments and results, MCP App HTML). Reflex's default DISK manager
  would write it under `.states/`, Redis would send it to a server, so `install()` refuses
  to start unless `state_manager_mode` is `MEMORY` (an unset mode with a Redis URL counts
  as Redis).
- **One backend worker.** The Host, its consent prompts and the tabs' watchers live in the
  backend process; run exactly one worker (the reference app sets `GRANIAN_WORKERS=1`).
  A second Host lifespan in the same process raises `RuntimeError`.
- **`deploy_url` on `127.0.0.1`.** The guard accepts exactly one origin, taken from
  `deploy_url`: `http://127.0.0.1:<port>`. `localhost` is always refused (it is reserved
  for the MCP Apps sandbox proxy), and so are a missing `deploy_url` or port, another
  scheme and wildcard hosts; another interface needs
  `REFLEX_MCPHOST_UNSAFE_ALLOW_REMOTE=1`. Requests with another `Host` / `Origin` get a
  `403` naming the right address; websockets are closed with `1008`.
- **`Referrer-Policy: no-referrer`.** Every response the guard lets through carries it
  (an existing `Referrer-Policy` of yours is kept), so pages opened from the UI never see
  its URL.
- **Pages.** Add `on_load=HostState.start_watch` to every page that shows one of the
  components; it starts that tab's watcher.

## Errors

Every public exception is an `MCPHostError` (API Spec §3.4). Invocation outcomes (denied,
timeout, tool error, …) are values, never exceptions. Worth catching in an embedding app:
`UnknownToolError` (see above), `AuthorizationError` (`reason` is a fixed code —
`secret_store_unavailable` when the stored credentials could not be read),
`ConsentStoreError` (a rule change could not be stored) and `SecretStoreError` (a sign-out
could not delete the stored credentials — the in-memory ones are already forgotten). No
exception message carries tokens, argument values or a backend's own error text.
