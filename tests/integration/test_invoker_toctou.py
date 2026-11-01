"""Invoker step 4a against a real Server whose tool changes during the prompt (F8-09).

``tests/fixtures/servers/hostile_server.py`` (both eras): the prompter, while "waiting",
calls ``mutate`` (which changes ``mutable``'s description and notifies
``tools/list_changed``), waits until the catalog holds the new definition, then answers
``allow_once``. The attempt ends ``denied`` / ``definition_changed`` and the Server's call
counter for ``mutable`` stays 0; a retry is a fresh evaluation that prompts again
(REQ-CNS-007 path) and is sent.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters

from reflex_mcphost.core.consent.models import (
    ConsentAnswer,
    ConsentDecision,
    ConsentRequest,
    ConsentTarget,
)
from reflex_mcphost.core.consent.outcome import OperationKind
from reflex_mcphost.core.host import Host
from reflex_mcphost.core.invoker import InvocationOutcome
from reflex_mcphost.core.prompter import (
    ElicitationRequest,
    ElicitationResponse,
    SamplingResponseReview,
    SamplingVerdict,
    TrustRequest,
)
from tests.integration.test_host import TIMEOUT, Setup, _toml_server, setup
from tests.integration.test_invoker import BOT, records, running_host

pytestmark = pytest.mark.anyio
Out = InvocationOutcome
__all__ = ["setup"]  # re-exported fixture

HOSTILE = Path(__file__).resolve().parents[1] / "fixtures" / "servers" / "hostile_server.py"
MUTABLE = "evil::mutable"


@pytest.fixture(params=["modern", "legacy"])
def hostile_table(request: pytest.FixtureRequest) -> str:
    """The hostile fixture Server on the modern SDK or (``interop``) the isolated mcp 1.x."""
    if request.param == "legacy":
        request.applymarker(pytest.mark.interop)
        legacy: StdioServerParameters = request.getfixturevalue("legacy_stdio")
        return _toml_server([legacy.command, *legacy.args[:-1], str(HOSTILE)])
    return _toml_server([sys.executable, str(HOSTILE)])


@dataclass
class RugPullPrompter:
    """On the first prompt for ``mutable``: mutate the tool, wait for the catalog to see the
    new definition, then allow once. Later prompts allow at once."""

    host: Host | None = None
    asked: list[ConsentRequest] = field(default_factory=list)
    supports_elicitation: bool = False

    async def ask_consent(self, request: ConsentRequest) -> ConsentAnswer:
        self.asked.append(request)
        if len(self.asked) == 1:
            assert self.host is not None
            before = self.host.catalog.get_tool(MUTABLE).definition_hash
            mutated = await self.host.invoker.call("evil::mutate", {}, consumer=BOT)
            assert mutated.outcome is Out.success, mutated.error
            with anyio.fail_after(TIMEOUT):
                while self.host.catalog.get_tool(MUTABLE).definition_hash == before:  # noqa: ASYNC110 - bounded poll (fail_after) of catalog state
                    await anyio.sleep(0.02)
        return ConsentAnswer(decision=ConsentDecision.ALLOW_ONCE)

    async def review_sampling_response(self, review: SamplingResponseReview) -> SamplingVerdict:
        return SamplingVerdict(approved=False)

    async def elicit(self, request: ElicitationRequest) -> ElicitationResponse:
        return ElicitationResponse(action="decline")

    async def confirm_trust(self, request: TrustRequest) -> bool:
        return False


async def _counts(host: Host) -> dict[str, Any]:
    result = await host.invoker.call("evil::call_counts", {}, consumer=BOT)
    assert result.ok, result.error
    counts: dict[str, Any] = json.loads(result.text)
    return counts


async def test_definition_change_during_prompt_is_denied(setup: Setup, hostile_table: str) -> None:
    """REQ-INV-016, REQ-CNS-007, REQ-INV-009, Art. 3 (API §4.8 step 4a): the Server changes
    the tool while the consent prompt is open → ``denied`` / ``definition_changed``, nothing
    sent (the fixture's counter stays 0), one audit record with the prompt's decision source
    and no second prompt; a retry prompts again and is sent."""
    prompter = RugPullPrompter()
    async with running_host(setup, {"evil": hostile_table}, prompter=prompter, allow=False) as host:
        prompter.host = host
        for fqn in ("evil::mutate", "evil::call_counts"):
            await host.consent.grant(
                effect="allow", operation=OperationKind.TOOL_CALL, target=ConsentTarget.tool(fqn)
            )
        evaluated = host.catalog.get_tool(MUTABLE).definition_hash
        result = await host.invoker.call(MUTABLE, {}, consumer=BOT)
        assert result.outcome is Out.denied
        assert result.error is not None
        assert result.error.code == "definition_changed"
        current = host.catalog.get_tool(MUTABLE).definition_hash
        assert result.error.details == {"evaluated": evaluated, "current": current}
        assert len(prompter.asked) == 1
        assert (await _counts(host)).get("mutable", 0) == 0
        (record,) = await records(host, fqn=result.fqn)
        assert (record.outcome, record.error_code) == (Out.denied, "definition_changed")
        assert record.decision_source == "prompt"

        retry = await host.invoker.call(MUTABLE, {}, consumer=BOT)
        assert retry.outcome is Out.success, retry.error
        assert len(prompter.asked) == 2
        assert (await _counts(host)).get("mutable", 0) == 1
