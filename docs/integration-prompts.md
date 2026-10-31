# Integration prompts — reflex-mcphost

Part of the cross-project integration map (2026-10-31): #1 AG-UI prismal → agentkit,
#2 OTel → agentlens, #3 ACP/A2A Umbral ⇄ prismal, #4 JSON-RPC Umbral → xtermjs,
**#5 shared MCP host** (this file).

Counterparts: `prismal/docs/integration-prompts.md` (I5-A), `umbral/docs/integration-prompts.md` (I5-B).
Preconditions: the v0.1 tasks in specs/tasks/mcphost-v0.1-tasks.md that deliver the
config loader, the consent policy engine and the audit log.

---

## Prompt I5-C · Export the consent policy and the server config for other hosts

```
You are working in reflex-mcphost. Read specs/prd/mcphost-v0.1.md (consent as a
first-class auditable policy), specs/data-model/mcphost-schema.md and
specs/api/mcphost-api-v0.1.md.

Goal: the policy and the server list that mcphost manages become portable
files that prismal (engine) and Umbral (terminal) read, so one decision made in
mcphost's UI applies everywhere; and decisions made elsewhere can be imported
back for audit correlation.

Scope:
1. `mcphost export servers` → the standard `mcpServers` JSON (stdio and
   Streamable HTTP), secrets as `${ENV}` references, never values.
2. `mcphost export policy` → a versioned JSON document: for each server+tool
   (FQN), allow | ask | deny, with rule id, author, created_at and an optional
   expiry; plus a detached signature so Umbral's trust store can verify it.
   Document the schema under docs/ and freeze it with a JSON Schema in the repo.
3. `mcphost import audit <file>` reads prismal's or Umbral's audit records that
   carry a rule id and shows them in the audit view next to mcphost's own.
4. Library API: the same three operations as functions, so a Reflex app that
   embeds mcphost can do it without the CLI.
5. Tests: golden files for both exports; schema validation; a round trip test
   that imports what was exported.

Acceptance (EARS):
- THE SYSTEM SHALL never write a secret value into an exported file.
- IF a policy rule has expired THEN the export SHALL omit it and the audit view
  SHALL still show its past decisions.

Out of scope: pushing policies to other hosts over the network; sharing code.
```
