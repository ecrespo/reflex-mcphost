# Constitution — reflex-mcphost

> Version 1.2 · Ratified: 2026-10-30 · Last amended: 2026-10-31
> Scope: `github.com/ecrespo/reflex-mcphost` (library `reflex_mcphost` + reference app)
> Rigor level: **spec-anchored** — `specs/` is the current truth; changes to shipped
> behavior enter through Delta Specs in `changes/`.

*"Your agent's tools deserve a real UI."* An open MCP **host** built with Reflex: connect
MCP servers, browse and invoke their tools, resources and prompts, render MCP Apps, and
expose everything to any agent behind a consent layer, in pure Python.

Every later artifact (PRD, API Spec, Technical Design, Data Model, Plan, Tasks) ends with a
*Constitution check* section stating which articles apply and how they are met. An
exception without a written justification is a violation.

---

## Vocabulary (normative)

These terms are used with exactly these meanings in every artifact, docstring and UI
string. Using a synonym for one of them (e.g. "plugin" for Server, "connection" for
Client, "user" for Consumer) is an Analyze finding.

| Term | Meaning |
|---|---|
| **Host** | The application that owns the user session, creates Clients and enforces consent. |
| **Client** | One Host-owned connection to exactly one Server. |
| **Server** | An external MCP process or endpoint offering tools, resources and prompts. |
| **Consumer** | Whoever calls tools through the Host: a human via the UI, or an agent via the `Toolset` bridge. |
| **Fully-qualified name (FQN)** | `server_id::name`, the only identifier the Host exposes for a tool, resource template or prompt. |
| **Era** | Protocol family of a Server: **modern** (`2026-07-28`+, per-request `_meta`, no handshake) or **legacy** (`2025-11-25` and earlier, `initialize` handshake). |
| **Dual-era** | Supporting both eras, detecting each Server's era at connect time. |
| **Input request** | A Server's request for client-side input (elicitation, sampling, roots): an MRTR `inputRequests` entry (modern) or a server-initiated JSON-RPC request (legacy). |

---

## Articles

### Art. 1 — Host, never server
THE PROJECT SHALL NOT ship an MCP server, an MCP SDK, or an agent framework. No module
under `src/reflex_mcphost/` SHALL import `mcp.server`; this is enforced by an
import-linter `forbidden` contract in CI. Test fixtures that spin up MCP servers live only
under `tests/` and are excluded from the built wheel (verified by a wheel-contents check).
*Rationale: the niche is the other side of the protocol; existing Reflex MCP packages are
servers, and scope creep into server/SDK territory dilutes the product.*

### Art. 2 — Spec fidelity on the official SDK
THE SYSTEM SHALL target the pinned revision set recorded in the Technical Design — v0.1:
MCP **`2026-07-28`** (preferred), **`2025-11-25`** (legacy fallback), MCP Apps
**`2026-01-26`**, `mcp` SDK **2.3.x** — verified against official sources and dated
(never from memory). THE SYSTEM SHALL be dual-era. THE SYSTEM SHALL use the official
`mcp` Python SDK for framing, transports and protocol lifecycle, and SHALL NOT
re-implement JSON-RPC message handling. Supported transports are stdio and Streamable
HTTP (with OAuth 2.1 for remote Servers). Features the pinned revision marks
*Deprecated* (Roots, Sampling, Logging, HTTP+SSE, Dynamic Client Registration) SHALL be
implemented only as compatibility, flagged `deprecated` in code and docs, and SHALL NOT
be extended beyond the spec. Every protocol-level behavior in a spec SHALL cite the MCP
spec section it implements.
*Rationale: a host is only as useful as its interoperability; drifting from the spec or
forking the SDK makes every Server a special case.*

### Art. 3 — Consent before effect (fail closed)
THE SYSTEM SHALL route every operation that lets a Server or Consumer cause an effect —
`tools/call`, `sampling/createMessage`, and any future effectful request — through
`ConsentPolicy` before execution. The default decision SHALL be *ask*. IF the policy
cannot be evaluated (store unavailable, malformed rule, timeout), THEN THE SYSTEM SHALL
deny. Only the `Invoker` (and the sampling handler, for sampling) SHALL call the
corresponding `ClientSession` methods; an architecture test fails the build if any other
module does. Every invocation attempt, allowed or denied, SHALL produce exactly one `AuditLog`
record. The sole exception is an attempt whose audit record cannot be written: it SHALL
cause no effect (outcome `audit_unavailable`) and SHALL be logged at ERROR.
*Rationale: consent is the product's differentiator; a single bypass path turns it into a
decorative dialog.*

### Art. 4 — Layered architecture, Reflex-free core
THE SYSTEM SHALL keep these layers, enforced by an import-linter `layers` contract:
`reflex_mcphost.core` → `reflex_mcphost.toolset` (+ optional `adapters.*`) →
`reflex_mcphost.ui` (Reflex components/state) → `reflex_mcphost.app` (reference app, CLI).
A lower layer SHALL NOT import a higher one; `core` SHALL NOT import `reflex`. Framework
adapters (PydanticAI, LangGraph) SHALL be optional extras with no import at package load
time. `reflex_mcphost` SHALL NOT depend on `reflex-agentkit`, nor vice versa; only the
reference app MAY compose them.
*Rationale: the core must be unit-testable without a browser and reusable by non-Reflex
hosts; the agent bridge must stay framework-neutral.*

### Art. 5 — Secrets and untrusted content
THE SYSTEM SHALL NOT write secrets (tokens, API keys, env values marked secret, OAuth
codes/verifiers) to logs, the `AuditLog`, exceptions shown in the UI, or SQLite; a
redaction test seeds known secrets and asserts they appear in no captured output. OAuth
tokens SHALL be stored via `keyring`, falling back to a file created with mode `0600`.
The `AuditLog` SHALL store an argument hash, not raw arguments. All Server-provided
content (tool descriptions, results, MCP App HTML, elicitation schemas) SHALL be treated
as untrusted: MCP App Views render only through a sandbox proxy served from an origin
distinct from the Host UI's origin, as required by the pinned MCP Apps revision; no
Server-provided content ever executes on the Host UI's origin; CSP is built only from the
domains the resource declares; the View communicates solely through the host-side
bridge; elicitation schemas are validated before rendering.
*Rationale: a host aggregates credentials for many Servers and runs third-party UI; it is
the highest-value target in the chain.*

### Art. 6 — Third-party configuration is read-only
THE SYSTEM SHALL read `claude_desktop_config.json`, Cursor/VS Code `mcp.json` and
project-level `.mcp.json` without modifying them, and SHALL write configuration only to
its own `mcphost.toml`. A test asserts byte-identical third-party files after every
registry operation. Precedence between sources SHALL be documented in the PRD and
deterministic.
*Rationale: users adopt the Host only if trying it cannot break the tools they already
rely on.*

### Art. 7 — Test-backed requirements
THE PROJECT SHALL cover every MUST acceptance criterion (EARS) with at least one automated
test whose name or docstring cites its `REQ-NNN`. Protocol behavior SHALL be tested
against real MCP Servers launched from `tests/` fixtures over stdio and Streamable HTTP,
not only against mocks. `reflex_mcphost.core` SHALL keep ≥ 90 % line coverage; the
overall package ≥ 80 %. A task is not Done until its tests pass in CI.
*Rationale: traceability from REQ to test is what keeps a spec-anchored project honest.*

### Art. 8 — Quality gate
THE PROJECT SHALL keep CI green on: `ruff check` + `ruff format --check`, `mypy --strict`
on `src/`, `pytest`, import-linter, and a secrets scan (gitleaks). No merge with a
disabled hook or a blanket `# type: ignore` / `noqa` without an inline reason. Public API
symbols SHALL have type hints and docstrings. I/O in `core` SHALL be async (`anyio`);
blocking calls are allowed only behind `anyio.to_thread`.
*Rationale: a library others build on must be typed, predictable and non-blocking.*

### Art. 9 — Process and language
THE PROJECT SHALL obtain an approved spec before implementing any work at or above
"medium feature" size, and SHALL update the affected spec as part of Definition of Done.
All artifacts, code, comments, docs, UI strings and commit messages SHALL be in English.
Commits SHALL follow Conventional Commits. Public API changes follow SemVer; while `0.x`,
a breaking change bumps the minor version and is listed in `CHANGELOG.md`.
*Rationale: specs that lag the code are worse than no specs; one language keeps the
project open to contributors.*

---

## Non-goals (binding for v0.1)

Proposing any of the following requires an amendment, not a feature PR.

- Not an MCP server, SDK or framework — we build on the official `mcp` Python SDK.
- Not an agent framework and not a chat UI library — that is `reflex-agentkit`.
- Not an MCP registry or marketplace — v0.1 reads existing configs and lets the user add
  Servers manually; registry browsing is a v0.2 extension point.
- Not an observability/tracing product (future `reflex-agentscope`) — the `AuditLog` is a
  local audit trail, not a tracing UI.
- No Electron/desktop packaging in v0.1 — the reference app is a Reflex web app run
  locally via `uvx reflex-mcphost`.

## Stack constraints

Decided; not re-discussed per feature. Minimum versions are confirmed in the Technical
Design.

| Concern | Decision |
|---|---|
| Language | Python ≥ 3.11 |
| Packaging | `uv`, `pyproject.toml` (hatchling), src layout; PyPI `reflex-mcphost`, import `reflex_mcphost`, CLI `reflex-mcphost` |
| Protocol | Official `mcp` Python SDK (latest stable at Technical Design time) |
| UI / app | Reflex |
| Persistence | SQLite via SQLModel (consent decisions, audit log) |
| Secrets | `keyring`, file fallback (`0600`) |
| Config | TOML (`mcphost.toml`) for our own config |
| Async | `anyio` |
| Tooling | ruff, mypy (strict), pytest + pytest-anyio, import-linter, gitleaks, pre-commit |
| CI | GitHub Actions |
| License | MIT |

## Amendments

| Date | Article | Change | Reason | Approved by |
|---|---|---|---|---|
| 2026-10-30 | All | Initial version | Project kickoff | Ernesto Crespo |
| 2026-10-30 | Vocabulary, Art. 2, Art. 5 | Era terms; pinned revision set + deprecated-as-compat rule; MCP Apps isolation by origin | MCP 2026-07-28 / Apps 2026-01-26 verification (`changes/_archive/2026-10-mcp-2026-07-28`) | Ernesto Crespo |
| 2026-10-31 | Art. 3 | Audit-unavailable exception stated explicitly | Analyze 2026-10-31 A-09 (`changes/_archive/2026-10-analyze-fixes`) | Ernesto Crespo |

## Constitution check (template for every later artifact)

```
## Constitution check
- Art. N — applies because … ; met by … (REQ-xxx / section …)
- Exceptions requested: none | Art. N — reason, scope, expiry
```
