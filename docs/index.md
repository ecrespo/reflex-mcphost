# reflex-mcphost

An MCP **host** for [Reflex](https://reflex.dev): it connects to MCP Servers over stdio and
Streamable HTTP, puts a fail-closed consent layer and a local audit log in front of every
call, handles OAuth 2.1 for remote Servers, bridges tools to agents (PydanticAI, LangGraph)
and renders MCP Apps in sandboxed Views.

```bash
uv add reflex-mcphost        # the library
uvx reflex-mcphost           # the reference app
```

## Guides

- [User guide](user-guide.md): the reference app, with Servers, consent, the catalog,
  authorization and MCP Apps.
- [Library guide](library.md): the agent bridge, and embedding the Host UI in your own
  Reflex app.

## Links

- Source and changelog: [github.com/ecrespo/reflex-mcphost](https://github.com/ecrespo/reflex-mcphost)
- Package: [pypi.org/project/reflex-mcphost](https://pypi.org/project/reflex-mcphost/)
- OAuth Client ID Metadata Document: [`oauth/client.json`](oauth/client.json)
