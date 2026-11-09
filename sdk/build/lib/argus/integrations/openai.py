"""wrap_openai(): trace chat completions of any OpenAI-compatible client (OpenAI, Ollama, ...).

Duck-typed: the `openai` package is not imported, so it isn't an SDK dependency.
Works for both `OpenAI` and `AsyncOpenAI`. Streaming (`stream=True`) is passed through;
the span then has no output or token counts.
"""

import functools
import inspect
import logging
from typing import Any

from argus.context import current_span
from argus.spans import Span

logger = logging.getLogger("argus")

_WRAPPED = "_argus_wrapped"


def wrap_openai[C](client: C) -> C:
    """Patch `client.chat.completions.create` in place and return the client."""
    completions = client.chat.completions  # type: ignore[attr-defined]
    original = completions.create
    if getattr(original, _WRAPPED, False):
        return client  # already wrapped

    @functools.wraps(original)
    def create(*args: Any, **kwargs: Any) -> Any:
        s = _start_generation(kwargs)
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            s.end(exc)
            raise
        # AsyncOpenAI returns a coroutine: finish the span when it completes.
        # (Checking the result is more reliable than inspecting `original`, whose
        # decorators can hide that it's async.)
        if inspect.isawaitable(result):
            return _finish_async(s, result)
        _record_response(s, result)
        s.end()
        return result

    setattr(create, _WRAPPED, True)
    completions.create = create
    return client


async def _finish_async(s: Span, awaitable: Any) -> Any:
    try:
        response = await awaitable
    except BaseException as exc:
        s.end(exc)
        raise
    _record_response(s, response)
    s.end()
    return response


def _start_generation(kwargs: dict[str, Any]) -> Span:
    # Not made "current": nothing can run inside an API call, so no children need it.
    s = Span("openai.chat.completions", "generation", parent=current_span())
    try:
        s.set_model(kwargs.get("model"))
        params = {k: v for k, v in kwargs.items() if k not in ("messages", "model")}
        s.set_input({"messages": kwargs.get("messages"), "params": params})
        if kwargs.get("stream"):
            s.set_metadata(stream=True)
    except Exception:
        logger.debug("argus: could not record request", exc_info=True)
    return s


def _record_response(s: Span, response: Any) -> None:
    try:
        if model := getattr(response, "model", None):
            s.set_model(model)  # the server's answer is more precise ("gpt-4o-2024-08-06")
        choices = getattr(response, "choices", None)
        if choices:
            message = choices[0].message
            s.set_output(message)
            s.set_metadata(finish_reason=getattr(choices[0], "finish_reason", None))
        usage = getattr(response, "usage", None)
        if usage is not None:
            s.set_usage(
                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
            )
    except Exception:
        logger.debug("argus: could not record response", exc_info=True)
