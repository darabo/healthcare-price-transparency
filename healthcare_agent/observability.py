"""Datadog Lapdog / LLM Observability integration.

When ``ddtrace`` is installed and ``DD_LLMOBS_ENABLED=1`` is set (or
Lapdog wraps the process), every agent run, tool call, and LLM
invocation is emitted as a structured span visible in the Lapdog
dashboard at ``lapdog.datadoghq.com`` — no Datadog account required.

If ``ddtrace`` is not installed the module exposes no-op helpers so the
rest of the codebase does not need conditional imports.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any, Generator

try:
    from ddtrace.llmobs import LLMObs as _LLMObs
    from ddtrace.llmobs.decorators import (
        agent as _agent_decorator,
        tool as _tool_decorator,
        workflow as _workflow_decorator,
    )

    LLMOBS_AVAILABLE = True
except ImportError:
    LLMOBS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Decorator helpers — wrap functions to emit Lapdog spans automatically
# ---------------------------------------------------------------------------

def trace_agent(name: str):
    """Decorator: marks a function as an LLMObs *agent* span."""
    if LLMOBS_AVAILABLE:
        return _agent_decorator(name=name)
    return lambda fn: fn


def trace_tool(name: str):
    """Decorator: marks a function as an LLMObs *tool* span."""
    if LLMOBS_AVAILABLE:
        return _tool_decorator(name=name)
    return lambda fn: fn


def trace_workflow(name: str):
    """Decorator: marks a function as an LLMObs *workflow* span."""
    if LLMOBS_AVAILABLE:
        return _workflow_decorator(name=name)
    return lambda fn: fn


# ---------------------------------------------------------------------------
# Context-manager helpers for manual span creation
# ---------------------------------------------------------------------------

@contextmanager
def llm_span(
    model_name: str,
    model_provider: str,
    input_prompt: str,
    *,
    name: str = "llm_call",
) -> Generator[dict[str, Any], None, None]:
    """Context manager that wraps an LLM call in an LLMObs span.

    Yields a mutable dict.  Set ``result["output"]`` inside the block
    to annotate the span with the model's response text.
    """
    result: dict[str, Any] = {"output": ""}
    if LLMOBS_AVAILABLE:
        span = None
        try:
            span = _LLMObs.llm(
                model_name=model_name,
                model_provider=model_provider,
                name=name,
            )
            span.__enter__()
            _LLMObs.annotate(
                span=span,
                input_data=[{"role": "user", "content": input_prompt}],
            )
        except Exception:
            span = None

        try:
            yield result
        finally:
            if span is not None:
                try:
                    _LLMObs.annotate(
                        span=span,
                        output_data=[{"role": "assistant", "content": str(result["output"])}],
                    )
                    span.__exit__(None, None, None)
                except Exception:
                    pass
    else:
        yield result


def annotate_span(**kwargs: Any) -> None:
    """Annotate the currently-active span if LLMObs is available."""
    if LLMOBS_AVAILABLE:
        try:
            _LLMObs.annotate(**kwargs)
        except Exception:
            pass
