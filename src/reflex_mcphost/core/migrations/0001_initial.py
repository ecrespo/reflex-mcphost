"""Initial schema: every table, trigger and index of Data Model §2, plus the ``meta`` rows.

Restoring the pre-migration backup of this version means starting from an empty database:
all consent rules, audit records, trust decisions and caches are lost.
"""

from __future__ import annotations

import sqlite3
import time

from ulid import ULID

from reflex_mcphost.core.migrations.runner import execute_script

VERSION = 1

DDL = """
CREATE TABLE meta (
  key   TEXT PRIMARY KEY CHECK (key IN ('schema_version','install_id','created_at_us')),
  value TEXT NOT NULL
) STRICT;

CREATE TABLE consent_rule (
  rule_id          TEXT    PRIMARY KEY CHECK (rule_id GLOB 'rule_[0-9A-HJKMNP-TV-Z]*'
                                              AND length(rule_id) = 31),
  effect           TEXT    NOT NULL CHECK (effect IN ('allow','deny')),
  operation        TEXT    NOT NULL CHECK (operation IN ('tool_call','sampling')),
  target_kind      TEXT    NOT NULL CHECK (target_kind IN ('tool','server','global')),
  server_id        TEXT    NOT NULL DEFAULT '',
  fqn              TEXT    NOT NULL DEFAULT '',
  consumer_kind    TEXT    NOT NULL DEFAULT '' CHECK (consumer_kind IN ('','human','agent','app')),
  consumer_name    TEXT    NOT NULL DEFAULT '',
  origin           TEXT    NOT NULL CHECK (origin IN ('prompt','programmatic')),
  definition_hash  TEXT    NOT NULL DEFAULT '',
  suspended        INTEGER NOT NULL DEFAULT 0 CHECK (suspended IN (0,1)),
  suspended_reason TEXT    NOT NULL DEFAULT ''
                           CHECK (suspended_reason IN ('','definition_changed')),
  created_at_us    INTEGER NOT NULL,
  updated_at_us    INTEGER NOT NULL,
  CHECK ( (target_kind = 'global' AND server_id = '' AND fqn = '')
       OR (target_kind = 'server' AND server_id <> '' AND fqn = '')
       OR (target_kind = 'tool'   AND server_id <> ''
                                  AND substr(fqn, 1, length(server_id) + 2) = server_id || '::'
                                  AND length(fqn) > length(server_id) + 2
                                  AND definition_hash GLOB 'sha256:*') ),
  CHECK ( NOT (operation = 'sampling' AND target_kind = 'tool') ),
  CHECK ( (consumer_kind = '' AND consumer_name = '')
       OR (consumer_kind <> '' AND consumer_name <> '') ),
  CHECK ( (suspended = 0 AND suspended_reason = '') OR (suspended = 1 AND suspended_reason <> '') )
) STRICT;

CREATE TABLE audit_record (
  invocation_id   TEXT    PRIMARY KEY CHECK (invocation_id GLOB 'inv_*'
                                             AND length(invocation_id) = 30),
  status          TEXT    NOT NULL CHECK (status IN ('in_progress','finished')),
  operation       TEXT    NOT NULL CHECK (operation IN ('tool_call','sampling')),
  started_at_us   INTEGER NOT NULL,
  finished_at_us  INTEGER,
  consumer_kind   TEXT    NOT NULL CHECK (consumer_kind IN ('human','agent','app')),
  consumer_name   TEXT    NOT NULL CHECK (length(consumer_name) BETWEEN 1 AND 64),
  server_id       TEXT    NOT NULL CHECK (length(server_id) BETWEEN 1 AND 64),
  fqn             TEXT    NOT NULL DEFAULT '',
  args_hash       TEXT    NOT NULL DEFAULT '',
  decision_source TEXT    NOT NULL CHECK (decision_source IN
                    ('rule','prompt','default_deny','error','timeout','no_prompter',
                     'not_evaluated')),
  rule_id         TEXT    NOT NULL DEFAULT '',
  outcome         TEXT    CHECK (outcome IN ('success','tool_error','protocol_error','timeout',
                    'cancelled','denied','invalid_arguments','not_found','server_unavailable',
                    'audit_unavailable','connection_lost')),
  error_code      TEXT    NOT NULL DEFAULT '',
  duration_ms     INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
  input_rounds    INTEGER NOT NULL DEFAULT 0 CHECK (input_rounds BETWEEN 0 AND 8),
  attribution     TEXT    NOT NULL DEFAULT 'exact' CHECK (attribution IN ('exact','unattributed')),
  CHECK ( (status = 'in_progress' AND outcome IS NULL AND finished_at_us IS NULL)
       OR (status = 'finished' AND outcome IS NOT NULL AND finished_at_us IS NOT NULL
           AND duration_ms IS NOT NULL) ),
  CHECK ( (operation = 'tool_call' AND fqn <> '' AND args_hash GLOB 'hmac-sha256:*')
       OR (operation = 'tool_call' AND fqn <> '' AND outcome IN ('not_found','invalid_arguments'))
       OR (operation = 'sampling'  AND fqn = '' AND args_hash = '') )
) STRICT;

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

CREATE TABLE trusted_server (
  server_id      TEXT    NOT NULL,
  fingerprint    TEXT    NOT NULL CHECK (fingerprint GLOB 'hmac-sha256:*'),
  origin_path    TEXT    NOT NULL,
  created_at_us  INTEGER NOT NULL,
  PRIMARY KEY (server_id, fingerprint)
) STRICT;

CREATE TABLE era_cache (
  cache_key        TEXT    PRIMARY KEY CHECK (cache_key GLOB 'sha256:*'),
  server_id        TEXT    NOT NULL,
  era              TEXT    NOT NULL CHECK (era IN ('modern','legacy')),
  protocol_version TEXT    NOT NULL,
  discover_result  TEXT    CHECK (discover_result IS NULL OR json_valid(discover_result)),
  fetched_at_us    INTEGER NOT NULL,
  ttl_ms           INTEGER NOT NULL DEFAULT 0 CHECK (ttl_ms >= 0),
  CHECK ( (era = 'modern' AND discover_result IS NOT NULL)
       OR (era = 'legacy' AND discover_result IS NULL) )
) STRICT;

CREATE TABLE secret_index (
  name           TEXT    PRIMARY KEY,
  kind           TEXT    NOT NULL CHECK (kind IN ('oauth','hmac')),
  server_id      TEXT    NOT NULL DEFAULT '',
  backend        TEXT    NOT NULL CHECK (backend IN ('keyring','file')),
  updated_at_us  INTEGER NOT NULL
) STRICT;

CREATE UNIQUE INDEX uq_consent_rule_key
  ON consent_rule (operation, target_kind, server_id, fqn, consumer_kind, consumer_name);
CREATE INDEX ix_audit_time          ON audit_record (started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_server_time   ON audit_record (server_id, started_at_us DESC,
                                                     invocation_id DESC);
CREATE INDEX ix_audit_fqn_time      ON audit_record (fqn, started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_consumer_time ON audit_record (consumer_kind, consumer_name,
                                                     started_at_us DESC, invocation_id DESC);
CREATE INDEX ix_audit_in_progress   ON audit_record (invocation_id) WHERE status = 'in_progress';
CREATE INDEX ix_secret_server       ON secret_index (server_id);
"""


def up(conn: sqlite3.Connection) -> None:
    execute_script(conn, DDL)
    now_us = time.time_ns() // 1000
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [("schema_version", "1"), ("install_id", str(ULID())), ("created_at_us", str(now_us))],
    )
