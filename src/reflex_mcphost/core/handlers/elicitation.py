"""Elicitation handler, form and url mode (REQ-HSH-010…015, API §4.10, DD-010).

Form mode: a ``requestedSchema`` outside the spec's allowed subset
(``elicitation_schema_problems``) is refused *without* calling the prompter (REQ-HSH-012:
legacy ``ErrorData(-32602, "Unsupported elicitation schema")``; modern
``InputHandlerError("elicitation/create", "invalid_elicitation_schema")``, delta D3).
Otherwise the Consumer is asked through ``Prompter.elicit`` inside ``ctx.prompter_wait()``
(the wait does not count against the invocation's budget, DD-004), bounded by
``timeout_s`` (= ``consent_timeout_s``, delta D8). Accepted content is validated with the
bounded F3-07 validator; on failure the prompter is asked again with ``errors`` (field →
message) and nothing is sent (REQ-HSH-013), at most ``MAX_ATTEMPTS`` times (then
``cancel``). ``decline``/``cancel`` carry no content. A missing prompter, one that does not
support elicitation, an exception, a timeout or a malformed answer → ``cancel`` (fail
closed).

Url mode: only ``http://`` and ``https://`` URLs with a host are shown (anything else is
refused, reason ``invalid_elicitation_url``); ``ElicitationRequest.url`` carries the full
URL and the UI opens it only on accept — core never opens a browser (REQ-HSH-015, delta
D8). Elicitation is not consent-gated and not audited: it is not an effect (Art. 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

import anyio
import mcp_types as types
from mcp.client import ClientRequestContext
from mcp.client.session import ElicitationFnT

from reflex_mcphost.core.handlers.base import Attribute, Refusal
from reflex_mcphost.core.logging import ContextAdapter, get_logger
from reflex_mcphost.core.mrtr import ELICITATION, InputContext, InputHandler, InputHandlerError
from reflex_mcphost.core.prompter import ElicitationRequest, ElicitationResponse, Prompter
from reflex_mcphost.core.schema import (
    SchemaCompileError,
    SchemaValidation,
    compile_schema,
    elicitation_schema_problems,
)
from reflex_mcphost.core.types import new_request_id

__all__ = ["INVALID_SCHEMA", "INVALID_URL", "ElicitationHandler"]

INVALID_SCHEMA = "invalid_elicitation_schema"
INVALID_URL = "invalid_elicitation_url"
_URL_SCHEMES = frozenset({"http", "https"})
_CANCEL = types.ElicitResult(action="cancel")
_log = get_logger(__name__)

PrompterLookup = Callable[[], Prompter | None]
ServerNameLookup = Callable[[str], str]


def _refusal(reason: str, message: str) -> Refusal:
    return Refusal(reason, types.ErrorData(code=types.INVALID_PARAMS, message=message))


def _valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in _URL_SCHEMES and bool(parts.hostname)


def _field_errors(
    schema: dict[str, Any], content: dict[str, Any], v: SchemaValidation
) -> dict[str, str]:
    """Field → first message (REQ-HSH-013). Issues on the object itself (``required``,
    ``additionalProperties``) are mapped to the properties they name."""
    errors: dict[str, str] = {}
    for issue in v.issues:
        field = issue.path.lstrip("/").split("/", 1)[0]
        if field:
            errors.setdefault(field, issue.message)
    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in content:
                errors.setdefault(name, "this field is required")
    if v.issues and not errors:
        errors[""] = v.issues[0].message
    return errors


class ElicitationHandler:
    """One elicitation core for both eras (REQ-HSH-045)."""

    MAX_ATTEMPTS = 10
    """Prompts per request before an endless invalid answer is turned into ``cancel``."""

    def __init__(
        self,
        prompter_of: PrompterLookup,
        server_name_of: ServerNameLookup,
        *,
        timeout_s: float,
    ) -> None:
        self._prompter_of = prompter_of
        self._server_name_of = server_name_of
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------ core

    async def handle(
        self, ctx: InputContext, params: types.ElicitRequestParams
    ) -> types.ElicitResult | Refusal:
        """Ask the Consumer; a refusal is returned, never raised."""
        log = ContextAdapter(_log, server_id=ctx.server_id, invocation_id=ctx.invocation_id)
        if isinstance(params, types.ElicitRequestURLParams):
            if not _valid_url(params.url):
                log.warning("elicitation refused: url mode needs an http(s) URL")
                return _refusal(INVALID_URL, "Unsupported elicitation URL")
            request = self._request(ctx, params.message, mode="url", url=params.url)
            response = await self._ask(ctx, request, log)
            if response is None:
                return _CANCEL
            return types.ElicitResult(action=response.action)
        schema: dict[str, Any] = dict(params.requested_schema)
        if elicitation_schema_problems(schema):
            log.warning("elicitation refused: the requested schema is outside the allowed subset")
            return _refusal(INVALID_SCHEMA, "Unsupported elicitation schema")
        try:
            compiled = compile_schema(schema)
        except SchemaCompileError:
            log.warning("elicitation refused: the requested schema does not compile")
            return _refusal(INVALID_SCHEMA, "Unsupported elicitation schema")
        request = self._request(ctx, params.message, mode="form", schema=schema)
        for _ in range(self.MAX_ATTEMPTS):
            response = await self._ask(ctx, request, log)
            if response is None:
                return _CANCEL
            if response.action != "accept":
                return types.ElicitResult(action=response.action)
            content: dict[str, Any] = dict(response.content or {})
            validation = compiled.validate(content)
            if validation.ok:
                return types.ElicitResult(action="accept", content=content)
            request = request.model_copy(
                update={"errors": _field_errors(schema, content, validation)}
            )
        log.warning("elicitation cancelled: no valid content after %d prompts", self.MAX_ATTEMPTS)
        return _CANCEL

    def _request(
        self,
        ctx: InputContext,
        message: str,
        *,
        mode: Literal["form", "url"],
        schema: dict[str, Any] | None = None,
        url: str | None = None,
    ) -> ElicitationRequest:
        try:
            server_name = self._server_name_of(ctx.server_id)
        except Exception:
            server_name = ctx.server_id
        return ElicitationRequest(
            id=new_request_id(),
            server_id=ctx.server_id,
            server_name=server_name,
            message=message,
            mode=mode,
            requested_schema=schema,
            url=url,
            attribution=ctx.attribution,
        )

    async def _ask(
        self, ctx: InputContext, request: ElicitationRequest, log: ContextAdapter
    ) -> ElicitationResponse | None:
        """The prompter's answer, or ``None`` for cancel (absent, raising, timed out)."""
        prompter = self._prompter_of()
        if prompter is None or not getattr(prompter, "supports_elicitation", False):
            log.info("elicitation cancelled: no eliciting prompter attached")
            return None
        try:
            with ctx.prompter_wait(), anyio.fail_after(self.timeout_s):
                answer: object = await prompter.elicit(request)
        except TimeoutError:
            log.warning("elicitation not answered within %g s; cancel", self.timeout_s)
            return None
        except Exception as exc:
            log.warning("elicitation prompter failed (%s); cancel", type(exc).__name__)
            return None
        if not isinstance(answer, ElicitationResponse):
            log.warning("elicitation prompter returned %s; cancel", type(answer).__name__)
            return None
        return answer

    # ------------------------------------------------------------------ adapters

    def as_input_handler(self) -> InputHandler:
        """The modern router slot ``elicitation/create`` (refusal → ``InputHandlerError``)."""

        async def slot(ctx: InputContext, request: types.InputRequest) -> types.InputResponse:
            if not isinstance(request, types.ElicitRequest):  # pragma: no cover - router keyed
                raise InputHandlerError(ELICITATION, "unexpected_request")
            result = await self.handle(ctx, request.params)
            if isinstance(result, Refusal):
                raise InputHandlerError(ELICITATION, result.reason)
            return result

        return slot

    def as_callback(self, attribute: Attribute) -> ElicitationFnT:
        """The legacy SDK ``elicitation_callback`` (refusal → ``ErrorData``)."""

        async def callback(
            context: ClientRequestContext, params: types.ElicitRequestParams
        ) -> types.ElicitResult | types.ErrorData:
            result = await self.handle(attribute(ELICITATION, str(context.request_id)), params)
            return result.legacy if isinstance(result, Refusal) else result

        return callback
