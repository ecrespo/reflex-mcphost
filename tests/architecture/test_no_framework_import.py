"""Constitution Art. 4, REQ-TLS-008: the package imports no agent framework; adapters
name their extra when it is missing (F5-05, API §4.13).

Each check runs in a fresh interpreter so modules other tests imported do not leak in.
``starlette``/``uvicorn`` are not asserted: the ``mcp`` client pulls them through
``mcp.server.context``, an SDK-internal chain (Analyze P5-12).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

FRAMEWORKS = ("pydantic_ai", "langchain_core", "langgraph", "langchain", "reflex")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter, code under test control
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def test_package_and_toolset_import_no_framework() -> None:
    """REQ-TLS-008: importing `reflex_mcphost` (resolving every lazy name in __all__,
    `Host` included) and `reflex_mcphost.toolset` (touching its names) loads none of
    pydantic_ai, langchain_core, langgraph, langchain or reflex."""
    proc = _run(
        f"""
        import sys
        import reflex_mcphost
        for name in reflex_mcphost.__all__:
            getattr(reflex_mcphost, name)
        import reflex_mcphost.toolset as toolset
        for name in toolset.__all__:
            getattr(toolset, name)
        assert reflex_mcphost.Host is not None
        loaded = sorted(
            m for m in sys.modules if m.split(".")[0] in {FRAMEWORKS!r}
        )
        print("LOADED", loaded)
        assert loaded == [], loaded
        """
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "LOADED []" in proc.stdout


BLOCKER = """
import importlib.abc
import sys


class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == {blocked!r} or name.startswith({blocked!r} + "."):
            raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
        return None


sys.meta_path.insert(0, _Block())
try:
    import {adapter}
except ImportError as exc:
    print("IMPORT-ERROR", exc)
    print("CAUSE", type(exc.__cause__).__name__)
else:
    raise SystemExit("adapter imported without its framework")
"""


@pytest.mark.parametrize(
    ("adapter", "blocked", "extra"),
    [
        ("reflex_mcphost.adapters.pydantic_ai", "pydantic_ai", "reflex-mcphost[pydantic-ai]"),
        ("reflex_mcphost.adapters.langgraph", "langchain_core", "reflex-mcphost[langgraph]"),
    ],
)
def test_adapter_without_extra_names_it(adapter: str, blocked: str, extra: str) -> None:
    """REQ-TLS-008, API §4.13: with the framework unavailable, importing the adapter
    raises ImportError naming the extra, chained from the original error."""
    proc = _run(BLOCKER.format(adapter=adapter, blocked=blocked))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "IMPORT-ERROR" in proc.stdout
    assert extra in proc.stdout
    assert "CAUSE ModuleNotFoundError" in proc.stdout


def test_adapters_import_with_their_extra() -> None:
    """REQ-TLS-006, REQ-TLS-007: with the extras installed both adapters import, and the
    LangGraph adapter imports only langchain_core (not langgraph)."""
    pytest.importorskip("pydantic_ai")
    pytest.importorskip("langchain_core")
    proc = _run(
        """
        import sys
        import reflex_mcphost.adapters.pydantic_ai
        import reflex_mcphost.adapters.langgraph
        assert "langgraph" not in sys.modules, "adapter pulled langgraph"
        print("OK")
        """
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
