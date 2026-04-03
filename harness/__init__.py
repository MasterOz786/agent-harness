"""Harness: OpenRouter-backed LLM loop + agent tools."""

from typing import Any

__all__ = ["run_with_openrouter", "schemas_to_openai_tools"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import openrouter

        return getattr(openrouter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
