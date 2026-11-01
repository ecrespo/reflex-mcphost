"""Configuration models (API §4.2, §4.3, §5.2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

import anyio
import platformdirs
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, PrivateAttr, SecretStr

from reflex_mcphost.core.types import ServerId


class SourceKind(StrEnum):
    """Config source kinds, listed in PRD RF-REG precedence order (1 = highest)."""

    MCPHOST_PROJECT = "mcphost_project"
    MCPHOST_USER = "mcphost_user"
    MCP_JSON_PROJECT = "mcp_json_project"
    CURSOR_PROJECT = "cursor_project"
    VSCODE_PROJECT = "vscode_project"
    EXTRA = "extra"
    CURSOR_USER = "cursor_user"
    VSCODE_USER = "vscode_user"
    CLAUDE_DESKTOP = "claude_desktop"


PROJECT_SOURCES = frozenset(
    {
        SourceKind.MCPHOST_PROJECT,
        SourceKind.MCP_JSON_PROJECT,
        SourceKind.CURSOR_PROJECT,
        SourceKind.VSCODE_PROJECT,
    }
)
"""Project sources for the trust rule (REQ-REG-011); ``extra`` files are user-chosen."""


class SourcesConfig(BaseModel):
    """``[sources]`` table of ``mcphost.toml`` (API §5.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claude_desktop: bool = True
    cursor: bool = True
    vscode: bool = True
    project_mcp_json: bool = True
    extra: tuple[str, ...] = ()


LogLevel = Literal["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]
"""MCP logging levels (RFC 5424 names, MCP spec `logging/setLevel`)."""

Mode = Literal["auto", "modern", "legacy"]
"""Era override (REQ-CON-019 escape hatch)."""


class ConfigOrigin(BaseModel):
    """Where a definition or problem comes from (API §4.3)."""

    model_config = ConfigDict(frozen=True)

    kind: SourceKind
    path: Path | None = None  # None for definitions added through the API
    precedence: int


def _api_origin() -> ConfigOrigin:
    return ConfigOrigin(kind=SourceKind.MCPHOST_USER, path=None, precedence=2)


class OAuthSettings(BaseModel):
    """OAuth client settings of an HTTP Server (API §4.3, §5.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str | None = None
    client_secret: SecretStr | None = None
    scopes: tuple[str, ...] = ()


class StdioTransport(BaseModel):
    """A Server launched as a child process speaking MCP over stdio."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["stdio"] = "stdio"
    command: Annotated[str, Field(min_length=1)]
    args: tuple[str, ...] = ()
    env: Mapping[str, SecretStr] = Field(default_factory=dict)
    cwd: Path | None = None


class HttpTransport(BaseModel):
    """A remote Server over Streamable HTTP (``legacy_sse``: deprecated HTTP+SSE, REQ-CON-013).

    ``url`` is the expanded URL. The URL as written in the config file (``${VAR}`` left
    unexpanded) is kept privately in ``url_template`` for the trust prompt (API §4.10); it
    takes no part in equality, hashing, dumps or ``repr``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["http"] = "http"
    url: AnyHttpUrl
    headers: Mapping[str, SecretStr] = Field(default_factory=dict)
    oauth: OAuthSettings | None = None
    legacy_sse: bool = False
    _url_template: str | None = PrivateAttr(default=None)

    @property
    def url_template(self) -> str | None:
        """The unexpanded URL from the config file; ``None`` for API-built definitions."""
        return self._url_template

    def remember_url_template(self, template: str) -> None:
        """Record the unexpanded URL (config parsers only; the model stays frozen)."""
        self._url_template = template

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HttpTransport):
            return NotImplemented
        return self.__dict__ == other.__dict__  # fields only: the template is private

    def __hash__(self) -> int:
        # `headers` is a dict (unhashable); equal transports still hash equal without it
        return hash((type(self), self.url, self.oauth, self.legacy_sse))


Transport = Annotated[StdioTransport | HttpTransport, Field(discriminator="kind")]


class ServerDefinition(BaseModel):
    """One Server definition (API §4.3). ``env``/``headers`` values are expanded secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ServerId
    transport: Transport
    enabled: bool = True
    timeout_s: Annotated[float, Field(gt=0)] | None = None
    roots: tuple[str, ...] = ()
    log_level: LogLevel | None = None
    review_sampling_responses: bool = False
    mode: Mode = "auto"
    origin: ConfigOrigin = Field(default_factory=_api_origin)


class ShadowedDefinition(BaseModel):
    """A definition hidden by a higher-precedence source defining the same id (REQ-REG-003)."""

    model_config = ConfigDict(frozen=True)

    definition: ServerDefinition
    shadowed_by: ConfigOrigin


class ConfigProblemCode(StrEnum):
    PARSE_ERROR = "parse_error"
    INVALID_SERVER_ID = "invalid_server_id"
    UNRESOLVED_VARIABLE = "unresolved_variable"
    UNSUPPORTED_VARIABLE = "unsupported_variable"
    INVALID_DEFINITION = "invalid_definition"
    PROJECT_SETTING_IGNORED = "project_setting_ignored"


VARIABLE_PROBLEMS = frozenset(
    {ConfigProblemCode.UNRESOLVED_VARIABLE, ConfigProblemCode.UNSUPPORTED_VARIABLE}
)
"""Problems that put a Server in ``config_error`` (REQ-REG-005)."""


class ConfigProblem(BaseModel):
    """A non-fatal config issue found while loading (REQ-REG-005/006/012/014).

    ``message`` never contains secret values: it names files, fields and variables only.
    """

    model_config = ConfigDict(frozen=True)

    origin: ConfigOrigin
    server_id: ServerId | None = None
    code: ConfigProblemCode
    message: str


class ServerOverride(BaseModel):
    """``[overrides.<id>]``: adjusts any definition without editing its file (REQ-REG-010)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool | None = None
    timeout_s: Annotated[float, Field(gt=0)] | None = None
    roots: tuple[str, ...] | None = None
    mode: Mode | None = None


class SamplingConfig(BaseModel):
    """``[sampling]`` table (API §5.2); ``""`` disables sampling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = ""


class RegistryDiff(BaseModel):
    """Result of a registry reload (REQ-REG-013); every tuple is sorted."""

    model_config = ConfigDict(frozen=True)

    added: tuple[ServerId, ...] = ()
    removed: tuple[ServerId, ...] = ()
    changed: tuple[ServerId, ...] = ()
    unchanged: tuple[ServerId, ...] = ()


@dataclass(frozen=True)
class ParseOutcome:
    """What one config file contributed: its definitions and its problems."""

    definitions: tuple[ServerDefinition, ...] = ()
    problems: tuple[ConfigProblem, ...] = ()


DEFAULT_SECRET_ARG_PATTERN = r"(?i)(passw(or)?d|secret|token|api[_-]?key|credential)"  # noqa: S105 - a regex, not a password
DEFAULT_OAUTH_CLIENT_METADATA_URL = "https://blog.seraph.to/reflex-mcphost/oauth/client.json"


def _default_data_dir() -> Path:
    return Path(platformdirs.user_data_dir("reflex-mcphost", appauthor=False))


def _default_user_file() -> Path:
    from reflex_mcphost.core.config.paths import default_user_file  # deferred: import cycle

    return default_user_file()


class HostConfig(BaseModel):
    """The parsed user-level ``mcphost.toml`` (API §4.2, §5). Third-party definitions are
    loaded by the registry; ``servers`` holds the own (user-level) definitions only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_dir: Path = Field(default_factory=_default_data_dir)
    consent_timeout_s: Annotated[float, Field(gt=0)] = 120.0
    default_tool_timeout_s: Annotated[float, Field(gt=0)] = 60.0
    reset_timeout_on_progress: bool = False
    secret_arg_pattern: str = DEFAULT_SECRET_ARG_PATTERN
    legacy_sse_fallback: bool = False
    log_buffer_size: Annotated[int, Field(ge=1)] = 1000
    oauth_client_metadata_url: AnyHttpUrl = AnyHttpUrl(DEFAULT_OAUTH_CLIENT_METADATA_URL)
    sources: SourcesConfig = SourcesConfig()
    sampling: SamplingConfig = SamplingConfig()
    servers: Mapping[ServerId, ServerDefinition] = Field(default_factory=dict)
    overrides: Mapping[ServerId, ServerOverride] = Field(default_factory=dict)
    config_path: Path = Field(default_factory=_default_user_file)
    cwd: Path = Field(default_factory=Path.cwd)
    problems: tuple[ConfigProblem, ...] = ()

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> HostConfig:
        """Read the user-level file (``path`` replaces it, like ``--config``) synchronously.

        For code that runs before an event loop; async code uses ``aload()`` (Art. 8).
        Raises ``ConfigError`` for an invalid file or a missing explicit ``path``.
        """
        from reflex_mcphost.core.config.own import load_user  # deferred: import cycle

        return load_user(path, cwd=cwd, env=env)

    @classmethod
    async def aload(
        cls,
        path: Path | None = None,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> HostConfig:
        """``load()`` with the file read in a worker thread."""
        return await anyio.to_thread.run_sync(partial(cls.load, path, cwd=cwd, env=env))
