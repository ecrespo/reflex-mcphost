# reflex-mcphost v0.1 — Data Model Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `APPROVED` |
| **Version** | 1.5 |
| **Date** | 2026-10-31 |
| **Database** | SQLite 3 (stdlib `sqlite3`, ≥ 3.37 for `STRICT` tables) via SQLModel ≥ 0.0.48 |
| **Secret store** | OS keyring (`keyring` ≥ 25.7) with `0600` JSON file fallback |
| **Related Tech Design** | `specs/technical/mcphost-architecture.md` v1.2 (DD-005…008, DD-013) |
| **Related API Spec** | `specs/api/mcphost-api-v0.1.md` v1.3 (§4.7, §4.9, §5) |
| **Governed by** | `specs/constitution.md` v1.2 |

---

## 1. Overview

reflex-mcphost persists very little, on purpose (Art. 5: tool arguments, results and
sampling messages are **never** stored). Persistent state lives in three places:

| Store | Location | Contents | Written by |
|---|---|---|---|
| **SQLite DB** `mcphost.db` | `HostConfig.data_dir` (default `platformdirs.user_data_dir("reflex-mcphost")`) | consent rules, audit records, project-server trust, era cache, secret index, schema meta | `core.consent.store`, `core.audit`, `core.registry`, `core.connection.era_cache`, `core.auth.token_store` |
| **Secret store** | OS keyring service `reflex-mcphost` / fallback `data_dir/secrets.json` | OAuth tokens + client info per Server, HMAC audit key | `core.auth.token_store`, `core.audit` |
| **Own config** | user-level `mcphost.toml` | Server definitions, overrides, host settings | `core.config.own` (schema in API §5 — not repeated here) |

Everything else (session-lifetime consent rules, catalog snapshots, log ring buffers,
pending prompts, event subscribers) is **in-memory** and listed in §8 with its bounds.

### Entity relationships

```
┌──────────────┐   rule_id (nullable, soft ref)   ┌──────────────────┐
│ consent_rule │◄─────────────────────────────────│   audit_record   │
└──────┬───────┘                                  └────────┬─────────┘
       │ server_id / fqn (soft refs to registry & catalog — not stored as tables)
       ▼                                                   │ server_id (soft ref)
  ServerRegistry (mcphost.toml + third-party files)  ◄─────┘
       ▲                    ▲                         ▲
       │ server_id          │ server_id               │ server_id (key name only)
┌──────┴────────┐   ┌───────┴──────┐          ┌───────┴──────┐        ┌────────────────────┐
│ trusted_server│   │  era_cache   │          │ secret_index │───────►│ keyring / secrets  │
└───────────────┘   └──────────────┘          └──────────────┘ names  │ .json (values)     │
                                                                       └────────────────────┘
┌──────┐
│ meta │  (singleton rows: schema_version, install_id, created_at)
└──────┘
```

There are no foreign keys to Servers or tools: those live in config files and Server
catalogs, not in the DB. References are **soft** (text) and may dangle after a Server is
removed — by design, so the audit trail survives configuration changes.

### Cross-cutting conventions

| Concern | Rule | Why |
|---|---|---|
| IDs | ULID text with type prefix (`rule_…`, `inv_…`) — API §3.3 | Sortable by creation time; no coordination |
| Timestamps | `INTEGER` **microseconds since Unix epoch, UTC** (`*_us`) | Total order, cheap range scans, no TZ ambiguity in SQLite; converted to aware `datetime` at the API boundary |
| "Any" / "none" in unique keys | empty string `''` sentinel, never `NULL` | SQLite treats `NULL`s as distinct in `UNIQUE` indexes; `''` makes the uniqueness rule hold |
| Enums | `TEXT` + `CHECK (col IN (...))` | Enforced in the DB, readable in dumps |
| Tables | `STRICT` | Type affinity surprises are bugs, not features |
| Booleans | `INTEGER CHECK (col IN (0,1))` | SQLite has no boolean |
| JSON blobs | `TEXT` with `CHECK (json_valid(col))` | Only for opaque SDK payloads (era cache) |
| `created_at_us` / `updated_at_us` | on every mutable table | Template rule; Permissions view ordering |
| Soft delete | **not used** — revoking a rule deletes it; the audit log already records history | Rules are policy, not records |
| Money / floats | none in this domain | — |

### Connection pragmas (DD-006)

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;        -- no FKs today; on for future-proofing
PRAGMA busy_timeout = 5000;
PRAGMA trusted_schema = OFF;
```

File modes: `data_dir` created `0700`; the process sets `umask(0o077)` around the first
open so `mcphost.db`, `-wal` and `-shm` are `0600` (REQ-NFR-012). On Windows the per-user
data dir ACL applies and a warning is logged (Tech Design §11, resolved).

---

## 2. Tables

### 2.1 `meta`

**Purpose:** schema version and install identity.
**Volume:** 3 rows. **Access:** read at startup; written by migrations.

```sql
CREATE TABLE meta (
  key   TEXT PRIMARY KEY CHECK (key IN ('schema_version','install_id','created_at_us')),
  value TEXT NOT NULL
) STRICT;
```

| Key | Value | Notes |
|---|---|---|
| `schema_version` | `"1"` | Bumped only by migrations (§5) |
| `install_id` | ULID | Correlates logs across restarts; never sent anywhere |
| `created_at_us` | integer as text | First-run time |

### 2.2 `consent_rule`

**Purpose:** persistent (`lifetime = always`) consent rules — API §4.7. Session rules are
in-memory only (§8).
**Volume:** typically 10–500; designed for 10,000 (REQ-NFR-001).
**Access:** full load at startup into `RuleIndex`; point writes on prompt answers,
programmatic grants, edits, revocations and suspensions.

#### Schema

```sql
CREATE TABLE consent_rule (
  rule_id          TEXT    PRIMARY KEY CHECK (rule_id GLOB 'rule_[0-9A-HJKMNP-TV-Z]*' AND length(rule_id) = 31),
  effect           TEXT    NOT NULL CHECK (effect IN ('allow','deny')),
  operation        TEXT    NOT NULL CHECK (operation IN ('tool_call','sampling')),
  target_kind      TEXT    NOT NULL CHECK (target_kind IN ('tool','server','global')),
  server_id        TEXT    NOT NULL DEFAULT '',      -- '' when target_kind = 'global'
  fqn              TEXT    NOT NULL DEFAULT '',      -- '' unless target_kind = 'tool'
  consumer_kind    TEXT    NOT NULL DEFAULT '' CHECK (consumer_kind IN ('','human','agent','app')),
  consumer_name    TEXT    NOT NULL DEFAULT '',      -- '' with consumer_kind '' = any Consumer
  origin           TEXT    NOT NULL CHECK (origin IN ('prompt','programmatic')),
  definition_hash  TEXT    NOT NULL DEFAULT '',      -- 'sha256:<hex>' for tool targets, else ''
  suspended        INTEGER NOT NULL DEFAULT 0 CHECK (suspended IN (0,1)),
  suspended_reason TEXT    NOT NULL DEFAULT '' CHECK (suspended_reason IN ('','definition_changed')),
  created_at_us    INTEGER NOT NULL,
  updated_at_us    INTEGER NOT NULL,
  CHECK ( (target_kind = 'global' AND server_id = '' AND fqn = '')
       OR (target_kind = 'server' AND server_id <> '' AND fqn = '')
       OR (target_kind = 'tool'   AND server_id <> ''
                                  AND substr(fqn, 1, length(server_id) + 2) = server_id || '::'
                                  AND length(fqn) > length(server_id) + 2
                                  AND definition_hash GLOB 'sha256:*') ),
  CHECK ( NOT (operation = 'sampling' AND target_kind = 'tool') ),
  CHECK ( (consumer_kind = '' AND consumer_name = '') OR (consumer_kind <> '' AND consumer_name <> '') ),
  CHECK ( (suspended = 0 AND suspended_reason = '') OR (suspended = 1 AND suspended_reason <> '') )
) STRICT;
```

#### Fields

| Field | Type | Req. | Default | Description | REQ |
|---|---|---|---|---|---|
| `rule_id` | TEXT | yes | generated | `rule_` + ULID | REQ-CNS-013 |
| `effect` | TEXT enum | yes | — | `allow` / `deny` | REQ-CNS-003 |
| `operation` | TEXT enum | yes | — | `tool_call` / `sampling` | REQ-CNS-001 |
| `target_kind` | TEXT enum | yes | — | `tool` > `server` > `global` specificity | REQ-CNS-003 |
| `server_id` | TEXT | cond. | `''` | Target Server | — |
| `fqn` | TEXT | cond. | `''` | Target tool FQN (`server_id::name`) | REQ-CAT-002 |
| `consumer_kind` / `consumer_name` | TEXT | no | `''` | Rule applies to this Consumer only; both `''` = any | REQ-CNS-015 |
| `origin` | TEXT enum | yes | — | `prompt` (human answer) / `programmatic` (`grant()`) | REQ-CNS-014 |
| `definition_hash` | TEXT | cond. | `''` | Tool definition hash at grant time | REQ-CNS-007, REQ-CAT-009 |
| `suspended` / `suspended_reason` | INTEGER / TEXT | yes | `0` / `''` | Set when the live hash differs; skipped by evaluation | REQ-CNS-007 |
| `created_at_us` / `updated_at_us` | INTEGER | yes | now | UTC µs | — |

Not stored: `lifetime` (always `always` here — session rules never reach the DB) and
`allow_once` answers (never stored — REQ-CNS-004).

One rule per key holds across lifetimes (API §4.7): a session rule that replaces a
persistent one for the same key deletes its row (Q-C4 `DELETE`), and an `update()` that
changes a rule's lifetime inserts or deletes the row accordingly.

**Corrupt rows** (fail validation on Q-C1 load, REQ-CNS-008) are kept, never deleted or
skipped silently: the row's Server — or every Server when the row's target is global or
unreadable — is *poisoned*, and evaluations there deny with `source="error"` until the row
is revoked or repaired.

#### Indexes

| Name | Columns | Type | Justification |
|---|---|---|---|
| PK | `rule_id` | unique | Q-C3/Q-C4 edit, revoke, suspend by id |
| `uq_consent_rule_key` | `(operation, target_kind, server_id, fqn, consumer_kind, consumer_name)` | unique | **One rule per key.** A new answer for the same key *replaces* the old rule (upsert, Q-C2), so allow/deny never coexist on one key and evaluation stays unambiguous |

No index on `updated_at_us`: the Permissions view sorts ≤ 10k rows in memory from the
`RuleIndex` (Q-C5), never in SQL.

### 2.3 `audit_record`

**Purpose:** write-once audit trail, one row per invocation attempt (tool call or sampling)
— REQ-INV-009…012, API §4.9.
**Volume:** personal use 50–2,000 rows/day; designed for 10,000/day (~350 B/row ≈ 1.3 GB
per year at the high end). No automatic retention in v0.1 (§6).
**Access:** insert at step 5 (or a single insert for early failures), one update at step 9
of the Invoker pipeline; paginated newest-first queries with optional filters (Audit view,
`AuditLog.query`).

#### Schema

```sql
CREATE TABLE audit_record (
  invocation_id   TEXT    PRIMARY KEY CHECK (invocation_id GLOB 'inv_*' AND length(invocation_id) = 30),
  status          TEXT    NOT NULL CHECK (status IN ('in_progress','finished')),
  operation       TEXT    NOT NULL CHECK (operation IN ('tool_call','sampling')),
  started_at_us   INTEGER NOT NULL,
  finished_at_us  INTEGER,
  consumer_kind   TEXT    NOT NULL CHECK (consumer_kind IN ('human','agent','app')),
  consumer_name   TEXT    NOT NULL CHECK (length(consumer_name) BETWEEN 1 AND 64),
  server_id       TEXT    NOT NULL CHECK (length(server_id) BETWEEN 1 AND 64),
  fqn             TEXT    NOT NULL DEFAULT '',          -- '' for sampling
  args_hash       TEXT    NOT NULL DEFAULT '',          -- 'hmac-sha256:<64 hex>' for tool calls, '' for sampling
  decision_source TEXT    NOT NULL CHECK (decision_source IN
                    ('rule','prompt','default_deny','error','timeout','no_prompter','not_evaluated')),
  rule_id         TEXT    NOT NULL DEFAULT '',          -- soft ref to consent_rule (may dangle)
  outcome         TEXT    CHECK (outcome IN ('success','tool_error','protocol_error','timeout','cancelled',
                    'denied','invalid_arguments','not_found','server_unavailable','audit_unavailable',
                    'connection_lost')),
  error_code      TEXT    NOT NULL DEFAULT '',          -- e.g. 'sampling_denied', 'input_rounds_exceeded', 'host_interrupted', JSON-RPC code
  duration_ms     INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
  input_rounds    INTEGER NOT NULL DEFAULT 0 CHECK (input_rounds BETWEEN 0 AND 8),
  attribution     TEXT    NOT NULL DEFAULT 'exact' CHECK (attribution IN ('exact','unattributed')),
  CHECK ( (status = 'in_progress' AND outcome IS NULL AND finished_at_us IS NULL)
       OR (status = 'finished' AND outcome IS NOT NULL AND finished_at_us IS NOT NULL AND duration_ms IS NOT NULL) ),
  CHECK ( (operation = 'tool_call' AND fqn <> '' AND args_hash GLOB 'hmac-sha256:*')
       OR (operation = 'tool_call' AND fqn <> '' AND outcome IN ('not_found','invalid_arguments') )
       OR (operation = 'sampling'  AND fqn = '' AND args_hash = '') )
) STRICT;
```

#### Fields

| Field | Type | Req. | Description | REQ |
|---|---|---|---|---|
| `invocation_id` | TEXT | yes | `inv_` + ULID; also the API `InvocationId` | REQ-INV-009 |
| `status` | TEXT enum | yes | `in_progress` between step 5 and step 9; `finished` otherwise | REQ-INV-012 |
| `operation` | TEXT enum | yes | `tool_call` / `sampling` | REQ-HSH-005 |
| `started_at_us` / `finished_at_us` | INTEGER | yes / when finished | UTC µs | REQ-INV-009 |
| `consumer_kind` / `consumer_name` | TEXT | yes | Requesting Consumer | REQ-CNS-015 |
| `server_id`, `fqn` | TEXT | yes / tool calls | Target | REQ-INV-009 |
| `args_hash` | TEXT | tool calls | HMAC-SHA256 over canonical JSON (DD-007); `arguments=None` is hashed as `{}`; empty only when arguments could not be canonicalized (`not_found`/`invalid_arguments` on non-JSON input — any other outcome fails the write closed with `audit_unavailable`) | REQ-INV-010 |
| `decision_source` | TEXT enum | yes | `ConsentOutcome.source`, or `not_evaluated` when the pipeline failed before step 4 | REQ-CNS-008…010 |
| `rule_id` | TEXT | no | Matching rule, if any | — |
| `outcome`, `error_code` | TEXT | when finished | `InvocationOutcome` + detail code | REQ-INV-002…008, REQ-INV-015, REQ-HSH-043/044 |
| `duration_ms` | INTEGER | when finished | Wall time of the attempt (including prompter wait) | REQ-INV-009 |
| `input_rounds` | INTEGER | yes | MRTR input rounds **fulfilled** (0–8); a call aborted with `input_rounds_exceeded` records 8 | REQ-INV-009, REQ-HSH-043 |
| `attribution` | TEXT enum | yes | `exact`, or `unattributed` for a legacy input request with no single in-flight Host request (its `consumer_*` is `human`/`ui`, the asked party) | REQ-HSH-047 |

**Never stored:** raw arguments, results, content blocks, sampling messages/completions,
elicitation answers, App payloads (Art. 5).

**Write-once rule:** the only permitted mutation is the single `in_progress → finished`
update (Q-A2) and the startup repair (Q-A5). Enforced in code (the repository exposes no
other writer) and by a trigger:

```sql
CREATE TRIGGER audit_record_immutable
BEFORE UPDATE ON audit_record
WHEN OLD.status = 'finished'
BEGIN
  SELECT RAISE(ABORT, 'audit_record is immutable once finished');
END;

CREATE TRIGGER audit_record_no_delete
BEFORE DELETE ON audit_record
BEGIN
  SELECT RAISE(ABORT, 'audit_record rows cannot be deleted in v0.1');
END;
```

**API visibility:** `AuditLog.query/get` return `finished` rows only, so `AuditRecord.outcome`
is always set (API §4.9 unchanged).

#### Indexes

| Name | Columns | Type | Justification |
|---|---|---|---|
| PK | `invocation_id` | unique | Q-A2 finish update; Q-A4 `get()` |
| `ix_audit_time` | `(started_at_us DESC, invocation_id DESC)` | b-tree | Q-A3 unfiltered newest-first keyset pagination (Audit view default) |
| `ix_audit_server_time` | `(server_id, started_at_us DESC, invocation_id DESC)` | b-tree | Q-A3 filter by Server (most common filter) |
| `ix_audit_fqn_time` | `(fqn, started_at_us DESC, invocation_id DESC)` | b-tree | Q-A3 filter by tool |
| `ix_audit_consumer_time` | `(consumer_kind, consumer_name, started_at_us DESC, invocation_id DESC)` | b-tree | Q-A3 filter by Consumer ("what did my agent do?") |
| `ix_audit_in_progress` | `(invocation_id) WHERE status = 'in_progress'` | partial | Q-A5 startup repair without scanning the table |

Filters on `outcome` and `operation` are low-cardinality and applied as residual
predicates on one of the indexes above (always combined with a time window in the UI);
no dedicated index (template rule: no index without a query that needs it). Equality
columns come first, then the sort keys.

### 2.4 `trusted_server`

**Purpose:** remember the Consumer's confirmation to connect a project-level Server (stdio or HTTP)
(REQ-REG-011, API §4.10 `confirm_trust`).
**Volume:** < 100 rows. **Access:** PK lookup before launching or contacting a project-origin Server.

```sql
CREATE TABLE trusted_server (
  server_id      TEXT    NOT NULL,
  fingerprint    TEXT    NOT NULL CHECK (fingerprint GLOB 'hmac-sha256:*'),
  origin_path    TEXT    NOT NULL,               -- display only
  created_at_us  INTEGER NOT NULL,
  PRIMARY KEY (server_id, fingerprint)
) STRICT;
```

`fingerprint = "hmac-sha256:" + HMAC(hmac-key, canonical_json(obj))` with `obj =
{transport: "stdio", command, resolved_command, args, cwd, origin_path, env: {name: value, …}}` or
`{transport: "http", url, origin_path, headers: {name: value, …}}` (expanded values).
Header values are covered like env values. Env values are covered by the keyed hash — so a
changed value (e.g. `NODE_OPTIONS`) re-prompts — but are never stored or shown (Art. 5);
the per-install key (§3 `hmac-key`) prevents offline guessing of low-entropy values.
Changing any part re-triggers the prompt (API §4.10). Rejections are not stored: a
rejected Server stays `failed` and asks again on the next `reconnect()`.

| Index | Columns | Justification |
|---|---|---|
| PK | `(server_id, fingerprint)` | Q-T1 trust check |

### 2.5 `era_cache`

**Purpose:** persist the detected era and `DiscoverResult` of modern Servers to skip the
probe on reconnect/restart (REQ-CON-019, Tech Design DD-002/DD-003, SDK `prior_discover`).
**Volume:** one row per Server definition (< 100). **Access:** PK read at each connect
attempt; upsert after a successful `auto` connect; delete on era-indicating failure.

```sql
CREATE TABLE era_cache (
  cache_key        TEXT    PRIMARY KEY CHECK (cache_key GLOB 'sha256:*'),
  server_id        TEXT    NOT NULL,
  era              TEXT    NOT NULL CHECK (era IN ('modern','legacy')),
  protocol_version TEXT    NOT NULL,
  discover_result  TEXT    CHECK (discover_result IS NULL OR json_valid(discover_result)),  -- modern only
  fetched_at_us    INTEGER NOT NULL,
  ttl_ms           INTEGER NOT NULL DEFAULT 0 CHECK (ttl_ms >= 0),
  CHECK ( (era = 'modern' AND discover_result IS NOT NULL) OR (era = 'legacy' AND discover_result IS NULL) )
) STRICT;
```

- `cache_key`: HTTP → `sha256("http\n" + scheme://host:port + path)` (per origin+endpoint,
  per spec); stdio → `sha256("stdio\n" + canonical_json({command, args, cwd, env: keyed_hash({name: value})}))`
  (env values covered through the install key, never stored; identical process configs share
  a row).
- Freshness: an entry is used if `now − fetched_at < max(ttl_ms, 24 h)`; legacy entries are
  advisory only (the SDK still runs `initialize`, they just skip the probe via
  `mode="legacy"`).
- `discover_result` holds the SDK `DiscoverResult.model_dump_json()` — capabilities,
  versions, identity, instructions; it contains no credentials.

### 2.6 `secret_index`

**Purpose:** list the *names* of entries this install wrote to the secret store, because
keyring backends cannot enumerate (needed for `sign_out`, Server removal cleanup and
diagnostics). Values are never in SQLite.

```sql
CREATE TABLE secret_index (
  name           TEXT    PRIMARY KEY,                  -- e.g. 'oauth:github', 'hmac-key'
  kind           TEXT    NOT NULL CHECK (kind IN ('oauth','hmac')),
  server_id      TEXT    NOT NULL DEFAULT '',
  backend        TEXT    NOT NULL CHECK (backend IN ('keyring','file')),
  updated_at_us  INTEGER NOT NULL
) STRICT;
```

| Index | Columns | Justification |
|---|---|---|
| PK | `name` | Q-S1 lookup / delete |
| `ix_secret_server` | `(server_id)` | Q-S2 cleanup when a Server is removed or signed out |

### 2.7 Index DDL (created by `0001_initial`)

```sql
CREATE UNIQUE INDEX uq_consent_rule_key
  ON consent_rule (operation, target_kind, server_id, fqn, consumer_kind, consumer_name);
CREATE INDEX ix_audit_time          ON audit_record (started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_server_time   ON audit_record (server_id, started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_fqn_time      ON audit_record (fqn, started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_consumer_time ON audit_record (consumer_kind, consumer_name, started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_in_progress   ON audit_record (invocation_id) WHERE status = 'in_progress';
CREATE INDEX ix_secret_server       ON secret_index (server_id);
```

---

## 3. Secret store layout

Service name: `reflex-mcphost`. One logical entry per name; value is JSON (UTF-8).

| Name | Value | Written | REQ |
|---|---|---|---|
| `hmac-key` | `{"v":1,"key":"<base64 32 bytes>","created_at_us":…}` | first run | REQ-INV-010, DD-007 |
| `oauth:{server_id}` | `{"v":1,"issuer":"https://…","tokens":<OAuthToken>,"client_info":<OAuthClientInformationFull or null>,"client_id_source":"configured\|cimd\|dcr","updated_at_us":…}` | after authorization / refresh / registration | REQ-AUTH-003/004/005/011 |

Rules:
- `TokenStore` (implements the SDK `TokenStorage` protocol, one instance per Server)
  reads/writes `oauth:{server_id}`. If discovery yields an `issuer` different from the
  stored one, the entry's `tokens` **and** `client_info` are discarded before the SDK
  proceeds (REQ-AUTH-011) — credentials are never reused across issuers.
- CIMD needs no stored client info (the URL is the `client_id`); `client_id_source`
  records which path was used, for diagnostics.
- `sign_out` deletes `oauth:{server_id}` and its `secret_index` row (REQ-AUTH-007).
- **File fallback** (`data_dir/secrets.json`, mode `0600`, atomic replace):
  `{"version": 1, "entries": {"<name>": <value>}}`. Selected when
  `keyring.get_keyring()` is a `fail`/`null` backend; `secret_index.backend` records which
  backend holds each entry.
- Server log ring buffers may contain secrets emitted by Servers; they are memory-only and
  pass through the redaction filter (Tech Design §6.2).

## 4. Critical queries

All queries are parameterized (no string interpolation), executed through the single DB
worker (DD-006). Targets measured with 10k rules / 1M audit rows on an SSD laptop.

### Consent

**Q-C1 — Load persistent rules at startup** (REQ-CNS-006)
```sql
SELECT * FROM consent_rule;          -- full scan, ≤ 10k rows
-- Target: < 100 ms. Frequency: once per Host start. Index: none needed.
```

**Q-C2 — Upsert rule from a prompt answer or `grant()`** (REQ-CNS-006, REQ-CNS-014)
```sql
INSERT INTO consent_rule (rule_id, effect, operation, target_kind, server_id, fqn,
                          consumer_kind, consumer_name, origin, definition_hash,
                          suspended, suspended_reason, created_at_us, updated_at_us)
VALUES (:rule_id, :effect, :operation, :target_kind, :server_id, :fqn,
        :consumer_kind, :consumer_name, :origin, :definition_hash, 0, '', :now, :now)
ON CONFLICT (operation, target_kind, server_id, fqn, consumer_kind, consumer_name)
DO UPDATE SET effect = excluded.effect, origin = excluded.origin,
              definition_hash = excluded.definition_hash,
              suspended = 0, suspended_reason = '', updated_at_us = excluded.updated_at_us
RETURNING rule_id;
-- Index: uq_consent_rule_key. Target: < 2 ms. Frequency: per persistent answer.
```
The `rule_id` of an existing key is preserved (returned), so edits keep identity.

**Q-C3 — Suspend on definition change** (REQ-CNS-007)
```sql
UPDATE consent_rule SET suspended = 1, suspended_reason = 'definition_changed', updated_at_us = :now
WHERE rule_id = :rule_id AND suspended = 0;
-- Index: PK. Target: < 2 ms.
```

**Q-C4 — Edit / revoke** (REQ-CNS-013)
```sql
UPDATE consent_rule SET effect = :effect, updated_at_us = :now WHERE rule_id = :rule_id;
DELETE FROM consent_rule WHERE rule_id = :rule_id;
-- Index: PK. Target: < 2 ms.
```

**Q-C5 — Permissions view** — served from the in-memory `RuleIndex` (persistent +
session rules merged), sorted by `updated_at` in Python; no SQL.

### Audit

**Q-A1 — Begin record (allowed call, pipeline step 5) / single-shot record (early failure)**
```sql
INSERT INTO audit_record (invocation_id, status, operation, started_at_us, consumer_kind,
  consumer_name, server_id, fqn, args_hash, decision_source, rule_id, input_rounds
  [, outcome, error_code, finished_at_us, duration_ms])
VALUES (...);
-- Index: PK. Target: < 2 ms (REQ-NFR-002 budget). Failure → outcome audit_unavailable, call not sent.
```

**Q-A2 — Finish record (step 9)**
```sql
UPDATE audit_record
SET status = 'finished', outcome = :outcome, error_code = :error_code,
    finished_at_us = :now, duration_ms = :duration_ms, input_rounds = :input_rounds
WHERE invocation_id = :invocation_id AND status = 'in_progress';
-- Index: PK. Target: < 2 ms.
```

**Q-A3 — Paginated query, newest first** (REQ-INV-011, API §4.9)
```sql
SELECT * FROM audit_record
WHERE status = 'finished'
  AND (:since_us IS NULL OR started_at_us >= :since_us)
  AND (:until_us IS NULL OR started_at_us <  :until_us)
  AND (:server_id IS NULL OR server_id = :server_id)
  AND (:fqn IS NULL OR fqn = :fqn)
  AND (:consumer_kind IS NULL OR (consumer_kind = :consumer_kind AND consumer_name = :consumer_name))
  AND (:outcome IS NULL OR outcome = :outcome)
  AND (:operation IS NULL OR operation = :operation)
  AND (:cursor_ts IS NULL OR (started_at_us, invocation_id) < (:cursor_ts, :cursor_id))
ORDER BY started_at_us DESC, invocation_id DESC
LIMIT :limit + 1;           -- +1 row to compute next_cursor
```
The repository builds the SQL **dynamically from the set filters** (dropping `IS NULL OR`
branches) so SQLite's planner can pick `ix_audit_server_time`, `ix_audit_fqn_time`,
`ix_audit_consumer_time` or `ix_audit_time`; a test asserts the chosen index with
`EXPLAIN QUERY PLAN` for each filter combination.
`next_cursor` = base64url(`"{started_at_us}:{invocation_id}"`) of the last returned row.
Target: < 20 ms per page of 50 at 1M rows. Frequency: Audit view interactions.

**Q-A4 — Get by id** (`AuditLog.get`)
```sql
SELECT * FROM audit_record WHERE invocation_id = :id AND status = 'finished';
-- Index: PK. Target: < 1 ms.
```

**Q-A5 — Startup repair of interrupted invocations** (Tech Design §3.3 error table, C.8)
```sql
UPDATE audit_record
SET status = 'finished', outcome = 'connection_lost', error_code = 'host_interrupted',
    finished_at_us = :now, duration_ms = MAX(0, (:now - started_at_us) / 1000)
WHERE status = 'in_progress';
-- Index: ix_audit_in_progress (partial). Target: < 10 ms. Frequency: once per start, before any new invocation.
```

### Registry, era cache, secrets

**Q-T1 — Trust check** (REQ-REG-011)
```sql
SELECT 1 FROM trusted_server WHERE server_id = :server_id AND fingerprint = :fingerprint;
INSERT OR IGNORE INTO trusted_server VALUES (:server_id, :fingerprint, :origin_path, :now);
-- Index: PK. Target: < 1 ms.
```

**Q-E1 — Era cache read / upsert / invalidate** (REQ-CON-019)
```sql
SELECT era, protocol_version, discover_result, fetched_at_us, ttl_ms FROM era_cache WHERE cache_key = :k;
INSERT INTO era_cache VALUES (:k, :server_id, :era, :pv, :dr, :now, :ttl)
  ON CONFLICT (cache_key) DO UPDATE SET era=excluded.era, protocol_version=excluded.protocol_version,
  discover_result=excluded.discover_result, fetched_at_us=excluded.fetched_at_us, ttl_ms=excluded.ttl_ms;
DELETE FROM era_cache WHERE cache_key = :k;
-- Index: PK. Target: < 2 ms.
```

**Q-S1 / Q-S2 — Secret index**
```sql
INSERT INTO secret_index VALUES (:name, :kind, :server_id, :backend, :now)
  ON CONFLICT (name) DO UPDATE SET backend = excluded.backend, updated_at_us = excluded.updated_at_us;
SELECT name, backend FROM secret_index WHERE server_id = :server_id;   -- ix_secret_server
DELETE FROM secret_index WHERE name = :name;
```

## 5. Migrations

- **Mechanism:** forward-only Python migrations in
  `src/reflex_mcphost/core/migrations/` named `NNNN_<slug>.py`, each exposing
  `VERSION: int` and `up(conn: sqlite3.Connection) -> None`. No Alembic (one embedded
  SQLite DB, < 10 tables — a framework adds more than it saves).
- **Runner** (at `Host.start`, before Q-A5):
  1. read `meta.schema_version` (absent → `0`, fresh DB);
  2. if behind: `VACUUM INTO 'mcphost.db.bak-v{old}'` (file mode `0600`), then apply each
     pending `up()` inside one `BEGIN IMMEDIATE … COMMIT`, bump `schema_version`;
  3. if the DB is **ahead** of the code (downgrade): refuse to start with `MCPHostError`
     naming the backup to restore (fail closed — Art. 3).
- **Rollback (`down`):** restore the pre-migration backup file. Each migration's docstring
  states what a restore loses (rows written after the upgrade).
- **v0.1:** `0001_initial.py` creates all tables, triggers and indexes in §2 and inserts
  `meta` rows.
- A test applies every migration to an empty DB and compares the resulting schema with the
  SQLModel metadata (drift check).

## 6. Retention and archiving

| Data | v0.1 policy | Rationale |
|---|---|---|
| `audit_record` | **Kept indefinitely**; rows cannot be deleted (trigger) | Audit integrity first; ~1.3 GB/year at the 10k/day design ceiling, far less in practice. Configurable retention/export is a v0.2 candidate (needs a PRD delta) |
| `consent_rule` | Until revoked | Policy |
| `trusted_server` | Until the definition changes (new fingerprint); stale rows are harmless | — |
| `era_cache` | Overwritten per key; rows for removed Servers pruned on registry reload | — |
| `secret_index` + secrets | Deleted on `sign_out` / Server removal | REQ-AUTH-007 |
| In-memory state | Process lifetime | §8 |

## 7. Backup and recovery

Local, single-user application; no server-side backups.

| Aspect | Policy |
|---|---|
| Automatic backups | Only the pre-migration snapshot (§5) |
| User backup | Documented: copy `data_dir` while the Host is stopped, or `sqlite3 mcphost.db ".backup out.db"` while running (WAL-safe) |
| Corruption | `PRAGMA quick_check` at start; on failure the Host refuses to start (fail closed) and prints the path and backup instructions (task F1-08) |
| Secrets | Not backed up by the app; losing them means re-authorizing Servers (tokens) and losing audit hash correlation (HMAC key) |
| RPO / RTO | Not applicable (no service level) |

## 8. In-memory state (not persisted)

| Structure | Owner | Bound | Contents | REQ |
|---|---|---|---|---|
| Session rules | `RuleIndex` | ≤ 10k | `ConsentRule` with `lifetime="session"` | REQ-CNS-005 |
| Catalog snapshots | `Catalog` | per Server: all entries + `stale` flag | `ToolEntry`, `ResourceEntry`, … , definition hashes, visibility | RF-CAT |
| Compiled validators | `core.schema` | LRU 1,024 keyed by `definition_hash` | `jsonschema` validators and cached compile failures | DD-010 |
| Log ring buffers | `ConnectionManager` | `log_buffer_size` (default 1,000) per Server | `LogEntry` (level, logger, timestamp, redacted text) | REQ-HSH-030 |
| Pending prompts | `PromptRegistry` | unbounded count, each with timeout (default 120 s) | `ConsentRequest`/`ElicitationRequest`/`TrustRequest` + resolver | REQ-CNS-009 |
| Event subscribers | `EventBus` | `buffer` per subscriber (default 256), drop-oldest | `HostEvent`s | API §4.11 |
| Supervisor state | `ServerSupervisor` | one per Server | `ServerStatus`, failure counter, `ClientLease` | RF-CON |
| SDK response cache | `mcp.Client` | SDK default in-memory store per Client, partition per Server | cacheable list/read results by `ttlMs` | REQ-CAT-013 |
| Roots / log level per Server | `ConnectionManager` | small | mirrored from config / `set_*` calls | REQ-HSH-020/031 |

## 9. SQLModel mapping (normative names)

```python
class ConsentRuleRow(SQLModel, table=True):
    __tablename__ = "consent_rule"
    rule_id: str = Field(primary_key=True)
    effect: str
    operation: str
    target_kind: str
    server_id: str = ""
    fqn: str = ""
    consumer_kind: str = ""
    consumer_name: str = ""
    origin: str
    definition_hash: str = ""
    suspended: bool = False
    suspended_reason: str = ""
    created_at_us: int
    updated_at_us: int

class AuditRecordRow(SQLModel, table=True):
    __tablename__ = "audit_record"
    invocation_id: str = Field(primary_key=True)
    status: str
    operation: str
    started_at_us: int
    finished_at_us: int | None = None
    consumer_kind: str
    consumer_name: str
    server_id: str
    fqn: str = ""
    args_hash: str = ""
    decision_source: str
    rule_id: str = ""
    outcome: str | None = None
    error_code: str = ""
    duration_ms: int | None = None
    input_rounds: int = 0
    attribution: str = "exact"

class TrustedServerRow(SQLModel, table=True): ...   # §2.4
class EraCacheRow(SQLModel, table=True): ...        # §2.5
class SecretIndexRow(SQLModel, table=True): ...     # §2.6
class MetaRow(SQLModel, table=True): ...            # §2.1
```

Rows are **internal** (`core.consent.store`, `core.audit`, …); the public API exposes the
frozen Pydantic models of API §4 (`ConsentRule`, `AuditRecord`), converted at the
repository boundary (µs ints → aware `datetime`, `''` sentinels → `None`). `STRICT`,
`CHECK`s, triggers and partial indexes are created by the migration's raw DDL (SQLModel
metadata is used for typing and the drift test, not for `create_all`).

## 10. Traceability

| REQ | Where |
|---|---|
| REQ-CNS-003/006/007/013/014/015 | §2.2, Q-C1…C5 |
| REQ-CNS-004/005 | §2.2 "Not stored", §8 session rules |
| REQ-INV-009/010/011/012 | §2.3, Q-A1…A5, §3 `hmac-key` |
| REQ-REG-011 | §2.4, Q-T1 |
| REQ-CON-019 | §2.5, Q-E1 |
| REQ-AUTH-003/004/005/007/011 | §3, §2.6 |
| REQ-NFR-001 | §2.2 volume + in-memory index (Tech Design DD-005) |
| REQ-NFR-012 | §1 file modes |
| Art. 5 | §2.3 "Never stored", §3, §2.4 fingerprint excludes env values |

## Constitution check

- **Art. 3** — audit is write-once (trigger + single writer), begun before the call is sent (Q-A1), and interrupted calls are closed on restart (Q-A5); downgrade or corruption refuses to start (fail closed).
- **Art. 5** — no arguments/results/messages persisted; HMAC hash only; secrets only in keyring/`0600` file with names (not values) indexed; env values excluded from fingerprints and cache keys; DB files `0600`.
- **Art. 6** — the DB never stores third-party config content; overrides live in `mcphost.toml`.
- **Art. 7** — schema drift test, `EXPLAIN QUERY PLAN` index tests, migration test.
- **Art. 8** — DB access only via the `anyio.to_thread` worker (DD-006).
- **Exceptions requested:** none.

## Open questions

- [x] Audit retention: **keep indefinitely, no deletion** in v0.1; configurable retention/export deferred to v0.2 via PRD delta (resolved 2026-10-30).

---

## Change history

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1-draft.1 | 2026-10-30 | Ernesto Crespo | Initial draft |
| 1.0 | 2026-10-30 | Ernesto Crespo | Approved; audit retention resolved |
| 1.1 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-analyze-fixes`: HMAC trust fingerprint incl. env values, `input_rounds` = fulfilled rounds |
| 1.2 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-spike-batch-a`: `audit_record.attribution` |
| 1.3 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-project-trust`: transport-specific trust fingerprint (HTTP URL + headers) |
| 1.4 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase2-impl`: `resolved_command` in the stdio fingerprint; keyed env hash in the stdio era-cache key |
| 1.5 | 2026-10-31 | Ernesto Crespo | Folded delta `2026-10-phase3-impl`: one rule per key across lifetimes, corrupt-row poisoning (§2.2); `args_hash` of `arguments=None` (§2.3); cached compile failures (§8) |
