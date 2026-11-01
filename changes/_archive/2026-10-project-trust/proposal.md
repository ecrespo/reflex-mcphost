# Proposal — 2026-10-project-trust

> Status: `APPROVED` (owner's standing auto-approval, 2026-10-31) · folded 2026-10-31

## Why

An automated security review of the Phase 2 registry code (commit `d7b0fbe`) found that
REQ-REG-011 only gates **stdio** definitions from project sources. A cloned repository can
ship a `.mcp.json` / `.cursor/mcp.json` / `.vscode/mcp.json` / `mcphost.toml` with a
Streamable HTTP Server whose `url` or `headers` contain `${GITHUB_TOKEN}`-style
placeholders. Expansion is legitimate, but auto-connecting would send host environment
secrets to a third party with no Consumer decision — a violation of Constitution Art. 3
(fail closed) and Art. 5 (secrets).

Two lesser findings in the same review:
- **Redaction poisoning** — an untrusted config could register a one-character "secret"
  and corrupt every log line. Mitigation: `SecretRegistry` ignores values shorter than
  4 characters (no meaningful secrecy at that length).
- **Project definitions shadow user ones** (precedence 1 vs 2, by design in RF-REG). Kept:
  shadowing is whole-definition (no user headers/env carry over), OAuth credentials are
  issuer-keyed and discarded on issuer change (REQ-AUTH-011), and — with this delta — any
  project definition needs explicit trust before it connects.

## What changes

- PRD REQ-REG-011 covers every project-source definition; the HTTP fingerprint covers URL,
  origin path and header names **and values**; no process is launched **and no request is
  sent** before trust.
- API §4.10 `TrustRequest` gains `transport`, `url` (unexpanded template) and
  `header_keys`; API §4.5 state row reworded.
- DM §2.4 fingerprint defined per transport.
- TD §6.1 attack-surface row updated; TD §6.2 notes the 4-character redaction floor.
- Tasks F2-05b / F2-07 wording.

## Impact

Phase 2 tasks F2-05b and F2-07 (in progress) implement the wider rule; UI work in F6-*
renders the HTTP variant of the trust prompt. No schema change (`trusted_server` is
transport-agnostic).
