"""SQLModel row classes (Data Model §9, normative names).

Internal to the repositories; the public API exposes the frozen Pydantic models of API §4.
The schema itself (STRICT, CHECKs, triggers, partial indexes) is created by the raw DDL of
the migrations; this metadata is used for typing and for the drift test, never for
``create_all``.
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class MetaRow(SQLModel, table=True):
    __tablename__ = "meta"
    key: str = Field(primary_key=True)
    value: str


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


class TrustedServerRow(SQLModel, table=True):
    __tablename__ = "trusted_server"
    server_id: str = Field(primary_key=True)
    fingerprint: str = Field(primary_key=True)
    origin_path: str
    created_at_us: int


class EraCacheRow(SQLModel, table=True):
    __tablename__ = "era_cache"
    cache_key: str = Field(primary_key=True)
    server_id: str
    era: str
    protocol_version: str
    discover_result: str | None = None
    fetched_at_us: int
    ttl_ms: int = 0


class SecretIndexRow(SQLModel, table=True):
    __tablename__ = "secret_index"
    name: str = Field(primary_key=True)
    kind: str
    server_id: str = ""
    backend: str
    updated_at_us: int
