# reflex-mcphost user guide

This guide is about the reference app: `reflex-mcphost run` (or plain `uvx reflex-mcphost`).
For the library, agents and embedding the UI in your own Reflex app, see
[`library.md`](library.md).

## Start

```bash
uvx --compile-bytecode reflex-mcphost            # opens http://127.0.0.1:3000/
uvx reflex-mcphost run --port 3917 --no-browser --config ./mcphost.toml
uvx reflex-mcphost servers --json                # the merged Server list; no UI, no connections
```

Open exactly `http://127.0.0.1:<port>/`. Any other address — `localhost` included — gets a
`403` page naming the right one: the backend accepts a single origin, which protects it
from DNS rebinding and from other sites in your browser. The first start of a version
downloads Bun and builds the frontend (about a minute, network needed); later starts take
a few seconds. Ctrl-C disconnects every Server and exits. Exit codes: `0` normal, `2` a
refused option, `3` a start-up failure (invalid config, database, Reflex).

The app is built in a per-user cache directory (`~/.cache/reflex-mcphost/app/` on Linux),
one directory per installed version, never in site-packages. Directories of other versions
that have not been used for 7 days, and leftovers of an interrupted build older than a day,
are removed at start; only directories the CLI created and marked are touched.

## Servers

The **Servers** view lists every Server the Host knows, merged from these files (highest
precedence first): the project `mcphost.toml`, your user `mcphost.toml`, the project
`.mcp.json`, Cursor and VS Code project files, `extra` files, then the Cursor, VS Code and
Claude Desktop user files. A Server defined in two files comes from the higher one in full;
the other is shown as shadowed. Third-party files are only read, never written: disabling
a Server that comes from one writes `[overrides.<id>] enabled = false` to your own
`mcphost.toml`.

Each row shows the state (`connecting`, `ready`, `auth_required`, `failed`, …), the
protocol era and version, origin file, transport, capabilities, the names (never the
values) of environment variables and headers, and problems. Actions: Connect, Disconnect,
Reconnect, Enable / Disable, Authorize and Sign out (OAuth Servers), Logs, and Reload
(re-reads every file; only changed Servers reconnect). There is no add / edit form in
`0.1.0`: edit `mcphost.toml` (see API Spec §5) and press Reload.

A Server defined in a **project** file (the project `mcphost.toml`, `.mcp.json`, the
`.cursor/` or `.vscode/` files) runs only after you trust it: a card shows the command,
arguments, working directory and environment (or URL and headers). Any change to these
asks again. A project `mcphost.toml` can define Servers only; other settings in it are
ignored and reported.

**OAuth.** A remote Server that needs authorization shows `auth_required`. Authorize opens
the authorization server's page in a new tab (only an `http`/`https` address; otherwise the
flow is cancelled and a notice says so). After you sign in, the Server connects. Tokens are
kept in the OS keyring (or a `0600` file when there is none) and bound to that Server and
authorization server. Sign out forgets them; if the secret store cannot delete the stored
entry, the notice says so and the Server is still signed out for this session.

## Catalog and invoking tools

The **Catalog** lists the tools, resources, resource templates and prompts of every ready
Server by fully-qualified name (`server_id::name`). A tool's form is generated from its
input schema; the result shows its text, structured content and allowed media. Server
descriptions and results are shown as plain text, never as HTML or Markdown.

## Consent and rules

Every tool call — yours, an agent's or an MCP App's — passes the consent policy first. With
no matching rule the side panel asks, showing the tool, its Server, its description, the
arguments (secret-like values redacted) and who is asking. Answers:

- **Allow once** — this call only; nothing is stored.
- **Allow for this session** — until the Host stops; not stored.
- **Allow always** / **Deny** — stored and applied across restarts.

An unanswered prompt is denied after 120 s. Rules target one tool, every tool of a Server,
or everything, and one Consumer or all: the most specific matching rules win, and among
them a deny beats an allow. A rule for the UI user does not cover an agent. If a tool's
definition changes after you allowed it always, that rule is suspended and you are asked
again; if it changes between your answer and the call, the call is denied and audited (no
second prompt).

The **Permissions** view lists the stored rules; edit or revoke them there. Changes apply
to the next call and appear in every open tab.

## Audit

The **Audit** view shows one record per invocation attempt, allowed or not — Consumer,
FQN, outcome, decision source, duration and an argument hash (never the arguments
themselves) — newest first, filtered by time range, Server, FQN, Consumer and outcome.

## Server requests: elicitation and sampling

- **Elicitation (form).** A Server can ask you for input; the side panel renders a form
  for its schema (only the schema subset the spec allows; anything else is refused) with
  the Server's name. Submit, decline or cancel; invalid input keeps the form open.
- **Elicitation (URL).** The full URL and the Server are shown; it opens only if you
  accept and only if it is an `http`/`https` address.
- **Sampling** — *deprecated in the pinned MCP revision; compatibility only.* Offered only
  when `[sampling] provider` in your user `mcphost.toml` names a provider. Each request
  shows a consent card (messages, system prompt, model preferences, max tokens) where you
  approve, edit or reject it; with `review_sampling_responses = true` on the Server you
  also approve the completion before it is returned.
- **Roots** — *deprecated, compatibility.* A Server sees only the `file://` roots you list
  in its `roots`; none by default.
- **Logging** — *deprecated, compatibility.* The "Log level (deprecated)" select sets the
  level a Server should log at; nothing is requested by default. The Server's stderr and
  log messages appear under Logs.

## MCP Apps

When you invoke a tool whose definition points at a UI resource, consent allows it and the
call ends, the Server's App appears below the result. It runs in a sandbox: a separate
proxy on `http://localhost:<proxy_port>` (a different origin from the UI) inside an
opaque-origin frame without camera, microphone, geolocation or clipboard, with a content
security policy built only from the domains the resource declares (invalid entries are
dropped and listed in a notice). The App reaches the Host only through messages: every tool
call it makes goes through consent as the Consumer `app:<server_id>` and is audited, and
only for its own Server's App-visible tools. A link it wants to open is shown in full and
opens in a new tab (without a referrer) only if you approve. Every View message is logged
by method and outcome only.

Limits in `0.1.0`:

- Views render only for invocations started from the UI, and only after they end.
- One View per tab; a new invocation, Close, removing the Server or stopping the Host tears
  it down.
- A View that navigates its own frame away is closed ("The App navigated away and was
  closed."), but the navigation itself may already have carried what the View was given.
- Apps are disabled in remote mode and when `localhost` does not resolve to loopback only.
- Real Safari has not been verified; WebKit is covered through Playwright's build.

## Remote mode

`--host <address> --unsafe-allow-remote` binds another interface. **Anyone who can reach
that address can drive the Host** — connect Servers, answer consent prompts and run tools
with your credentials. The Host warns at start-up, MCP Apps are disabled, and wildcard
addresses (`0.0.0.0`, `::`) are refused. Use it only on a network you control.
