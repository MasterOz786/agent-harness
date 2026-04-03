"""Revisions package — tools (git / files) and optional LLM editor agent."""

from importlib import import_module
from typing import Any

__all__ = [
    "DEFAULT_EDITOR_SYSTEM_PROMPT",
    "MAX_VISIBLE_REVISIONS",
    "TOOLS",
    "bash",
    "build_editor_system_prompt",
    "file_delete",
    "file_edit",
    "invoke_tool",
    "revisions_tools_spec",
    "run_editor_agent",
]

_TOOLS_EXPORTS = frozenset(
    {
        "MAX_VISIBLE_REVISIONS",
        "TOOLS",
        "bash",
        "file_delete",
        "file_edit",
        "invoke_tool",
        "revisions_tools_spec",
    }
)
_AGENT_EXPORTS = frozenset(
    {
        "DEFAULT_EDITOR_SYSTEM_PROMPT",
        "build_editor_system_prompt",
        "run_editor_agent",
    }
)


def __getattr__(name: str) -> Any:
    if name in _TOOLS_EXPORTS:
        mod = import_module("revisions.tools")
        return getattr(mod, name)
    if name in _AGENT_EXPORTS:
        mod = import_module("revisions.agent")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
