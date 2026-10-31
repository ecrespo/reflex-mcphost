"""Config source discovery: per-OS paths (DD-013) and the RF-REG total order."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import platformdirs
from pydantic import BaseModel, ConfigDict

from reflex_mcphost.core.config.models import SourceKind, SourcesConfig

APP_NAME = "reflex-mcphost"
OWN_FILENAME = "mcphost.toml"

System = Literal["linux", "darwin", "windows"]


@dataclass(frozen=True)
class Platform:
    """The OS facts path discovery depends on; injectable so tests never read a real home."""

    system: System
    home: Path
    env: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def current(cls) -> Platform:
        system: System = (
            "windows"
            if sys.platform == "win32"
            else "darwin"
            if sys.platform == "darwin"
            else "linux"
        )
        return cls(system=system, home=Path.home(), env=dict(os.environ))

    def env_dir(self, name: str, fallback: Path) -> Path:
        value = self.env.get(name)
        return Path(value) if value else fallback


class SourceSpec(BaseModel):
    """One candidate config file at its RF-REG position (the file may not exist)."""

    model_config = ConfigDict(frozen=True)

    kind: SourceKind
    path: Path
    precedence: int


def default_user_file() -> Path:
    """User-level ``mcphost.toml`` (API §5.1) — the only file the Host ever writes."""
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / OWN_FILENAME


def _claude_desktop(p: Platform) -> Path:
    if p.system == "windows":
        return (
            p.env_dir("APPDATA", p.home / "AppData" / "Roaming")
            / "Claude"
            / ("claude_desktop_config.json")
        )
    if p.system == "darwin":
        return p.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return p.home / ".config" / "Claude" / "claude_desktop_config.json"


def _cursor_user(p: Platform) -> Path:
    base = p.env_dir("USERPROFILE", p.home) if p.system == "windows" else p.home
    return base / ".cursor" / "mcp.json"


def _vscode_user(p: Platform) -> Path:
    if p.system == "windows":
        return p.env_dir("APPDATA", p.home / "AppData" / "Roaming") / "Code" / "User" / "mcp.json"
    if p.system == "darwin":
        return p.home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
    return p.home / ".config" / "Code" / "User" / "mcp.json"


def _extra_path(raw: str, user_file: Path, p: Platform) -> Path:
    if raw == "~" or raw.startswith(("~/", "~\\")):
        return p.home / raw[2:]
    path = Path(raw)
    return path if path.is_absolute() else user_file.parent / path


def discover_sources(
    cwd: Path,
    *,
    user_file: Path,
    sources: SourcesConfig,
    platform: Platform,
) -> list[SourceSpec]:
    """Candidate files in RF-REG order. Precedence is positional and never renumbered when a
    source is disabled; ``extra`` files take consecutive values in list order."""
    n = len(sources.extra)
    candidates: list[tuple[SourceKind, Path, int, bool]] = [
        (SourceKind.MCPHOST_PROJECT, cwd / OWN_FILENAME, 1, True),
        (SourceKind.MCPHOST_USER, user_file, 2, True),
        (SourceKind.MCP_JSON_PROJECT, cwd / ".mcp.json", 3, sources.project_mcp_json),
        (SourceKind.CURSOR_PROJECT, cwd / ".cursor" / "mcp.json", 4, sources.cursor),
        (SourceKind.VSCODE_PROJECT, cwd / ".vscode" / "mcp.json", 5, sources.vscode),
    ]
    candidates += [
        (SourceKind.EXTRA, _extra_path(raw, user_file, platform), 6 + i, True)
        for i, raw in enumerate(sources.extra)
    ]
    candidates += [
        (SourceKind.CURSOR_USER, _cursor_user(platform), 6 + n, sources.cursor),
        (SourceKind.VSCODE_USER, _vscode_user(platform), 7 + n, sources.vscode),
        (SourceKind.CLAUDE_DESKTOP, _claude_desktop(platform), 8 + n, sources.claude_desktop),
    ]
    return [
        SourceSpec(kind=kind, path=path, precedence=prec)
        for kind, path, prec, enabled in candidates
        if enabled
    ]
