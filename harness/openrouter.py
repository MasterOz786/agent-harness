"""
OpenRouter (OpenAI-compatible) chat loop with revisions agent tools.

Set OPENROUTER_API_KEY in the environment or in a ``.env`` file at the repo root
(loaded automatically). Optional: OPENROUTER_MODEL, OPENROUTER_BASE_URL,
OPENROUTER_HTTP_REFERER, OPENROUTER_APP_TITLE.

You supply system and user prompts; the model issues tool calls; the harness
executes them via revisions.invoke_tool and returns results until the model stops
calling tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Repo layout: agent-harness/agents/revisions
_ROOT = Path(__file__).resolve().parent.parent
_AGENTS = _ROOT / "agents"
if _AGENTS.is_dir() and str(_AGENTS) not in sys.path:
    sys.path.insert(0, str(_AGENTS))

from dotenv import load_dotenv
from openai import OpenAI
from revisions.tools import invoke_tool, tool_schemas

_ENV_LOADED = False


def _load_dotenv_files() -> None:
    """Load ``.env`` from repo root and cwd (does not override existing env vars)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    load_dotenv(_ROOT / ".env")
    load_dotenv()


def schemas_to_openai_tools(schema_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map revisions ``tool_schemas()`` entries to OpenAI / OpenRouter ``tools`` items."""
    out: list[dict[str, Any]] = []
    for s in schema_entries:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": s["parameters"],
                },
            }
        )
    return out


def _openrouter_client() -> OpenAI:
    _load_dotenv_files()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    default_headers: dict[str, str] = {}
    ref = os.environ.get("OPENROUTER_HTTP_REFERER", "").strip()
    if ref:
        default_headers["HTTP-Referer"] = ref
    title = os.environ.get("OPENROUTER_APP_TITLE", "agent-harness").strip()
    if title:
        default_headers["X-Title"] = title
    kw: dict[str, Any] = {"api_key": key, "base_url": base}
    if default_headers:
        kw["default_headers"] = default_headers
    return OpenAI(**kw)


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Serialize an SDK assistant message for the next API request."""
    d: dict[str, Any] = {"role": msg.role}
    if getattr(msg, "content", None):
        d["content"] = msg.content
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tcs
        ]
    return d


def run_with_openrouter(
    *,
    system_prompt: str,
    user_prompt: str,
    workspace: str | Path | None = None,
    model: str | None = None,
    max_tool_rounds: int = 64,
) -> str:
    """
    Send prompts to OpenRouter, run tool calls against ``revisions`` tools, repeat.

    Returns the final assistant **text** content from the last model message
    (may be empty if the model only used tools).
    """
    client = _openrouter_client()
    model = model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    ws = Path(workspace or os.getcwd()).resolve()

    tools = schemas_to_openai_tools(tool_schemas())
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    final_text = ""
    for _ in range(max_tool_rounds):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        choice = response.choices[0]
        msg = choice.message
        messages.append(_message_to_dict(msg))

        tcs = getattr(msg, "tool_calls", None) or []
        if not tcs:
            final_text = (msg.content or "").strip()
            break

        for tc in tcs:
            name = tc.function.name
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError as e:
                args = {}
                result = {"ok": False, "error": f"invalid tool arguments JSON: {e}"}
            else:
                result = invoke_tool(name, args, workspace=str(ws))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return final_text


def _main() -> None:
    p = argparse.ArgumentParser(description="Run revisions agent via OpenRouter")
    p.add_argument(
        "--workspace",
        "-w",
        default=".",
        help="Working directory for git and file tools (default: cwd)",
    )
    p.add_argument(
        "--model",
        "-m",
        default=None,
        help="OpenRouter model id (default: env OPENROUTER_MODEL or openai/gpt-4o-mini)",
    )
    p.add_argument(
        "--system",
        "-s",
        default="",
        help="System prompt string (use --system-file for long text)",
    )
    p.add_argument(
        "--system-file",
        type=Path,
        default=None,
        help="Read system prompt from this file (UTF-8)",
    )
    p.add_argument(
        "--user",
        "-u",
        default="",
        help="User message (default: read stdin if empty)",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=64,
        help="Max tool-call rounds (default: 64)",
    )
    args = p.parse_args()

    system = args.system
    if args.system_file is not None:
        system = args.system_file.read_text(encoding="utf-8")
    user = args.user
    if not user.strip():
        user = sys.stdin.read()

    if not user.strip():
        print("error: provide --user or pipe a user message on stdin", file=sys.stderr)
        sys.exit(2)

    out = run_with_openrouter(
        system_prompt=system or "You are a helpful assistant with git and file tools.",
        user_prompt=user,
        workspace=args.workspace,
        model=args.model,
        max_tool_rounds=args.max_rounds,
    )
    if out:
        print(out)


if __name__ == "__main__":
    _main()
