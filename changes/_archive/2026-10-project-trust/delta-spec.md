# Delta spec — 2026-10-project-trust

## PRD (1.4 → 1.5)

### MODIFIED REQ-REG-011
IF a Server definition (stdio or Streamable HTTP) comes from a project source (RF-REG
table) and its trust fingerprint — stdio: command, arguments, working directory, origin
path and the names **and values** of its environment variables; HTTP: URL, origin path and
the names **and values** of its headers — has not been trusted before, THEN THE SYSTEM
SHALL require explicit Consumer confirmation (stdio: showing command, arguments, working
directory and environment variable names, flagging names known to load code; HTTP: showing
the URL as written in the file and the header names) before launching its process or
sending any request. Environment and header values SHALL NOT be shown or stored.

## API (1.4 → 1.5)

- §4.5 transition row: "project Server not trusted (declined, prompter error, or no
  prompter)" → `failed` (reason `untrusted_project_server`, no retry, nothing launched or
  sent).
- §4.10 `TrustRequest`: add `transport: Literal["stdio", "http"]`, `url: str | None` (HTTP:
  the **unexpanded** template from the file), `header_keys: tuple[str, ...]` (keys only);
  stdio fields are empty for HTTP. Re-trigger rule also covers URL and header names/values.

## Data Model (1.2 → 1.3)

- §2.4: `fingerprint` = `hmac-sha256:` + HMAC(key, canonical_json(obj)) with
  `obj = {transport: "stdio", command, args, cwd, origin_path, env: {name: value}}` or
  `{transport: "http", url, origin_path, headers: {name: value}}` (expanded values). Purpose
  and access lines say "project-level Server" instead of "project-level stdio Server".

## Technical Design (1.3 → 1.4)

- §6.1 first row: "Malicious project config spawning commands **or exfiltrating env
  secrets via a remote URL/headers**" — trust prompt per transport-specific fingerprint
  before any process or request.
- §6.2: `SecretRegistry` ignores values shorter than 4 characters (redaction-poisoning
  guard).

## Tasks (1.4 → 1.5)

- F2-05b: trust applies to every project-source definition (stdio and HTTP).
- F2-07: trust is checked before building any transport.
