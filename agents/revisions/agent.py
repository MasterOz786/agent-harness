"""
LLM-driven revisions editor: uses OpenRouter + ``revisions.tools`` only.

Run from the agent-harness repo root (or ensure ``agents/`` and the repo root are on
``PYTHONPATH``). Requires ``OPENROUTER_API_KEY`` and optional ``OPENROUTER_MODEL``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
_AGENTS_DIR = Path(__file__).resolve().parent.parent
_AGENT_HARNESS_ROOT = _AGENTS_DIR.parent


def _ensure_import_paths() -> None:
    for p in (_AGENT_HARNESS_ROOT, _AGENTS_DIR):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


_ensure_import_paths()

from revisions.tools import MAX_VISIBLE_REVISIONS


def _run_with_openrouter(**kwargs: Any) -> str:
    from harness.openrouter import run_with_openrouter

    return run_with_openrouter(**kwargs)

__all__ = [
    "DEFAULT_EDITOR_SYSTEM_PROMPT",
    "MAX_VISIBLE_REVISIONS",
    "build_editor_system_prompt",
    "run_editor_agent",
]


def build_editor_system_prompt(
    *,
    extra_instructions: str = "",
    max_visible: int = MAX_VISIBLE_REVISIONS,
) -> str:
    """Compose the editor system prompt; ``extra_instructions`` is appended verbatim."""
    base = f"""You are a coding assistant working inside a single git workspace.

## Tools
- **bash**: run **git only** (one line in `command`, or several in `commands`). Examples: `git status`, `git diff`, `git add -A`, `git commit -m "message"`, `git checkout <commit_hash>` or `git switch -d <commit_hash>` to move HEAD. There is no shell — no pipes, `&&`, or non-git programs. Use `commands` to run multiple git lines in order.
- **file_edit**: set the **full** text of a file under the workspace (create or replace). Paths are relative to the workspace root.
- **file_delete**: remove a **file** (not a directory) under the workspace.

## Git workflow
- Inspect state with `git status`, `git diff`, `git log`.
- After editing files, stage and commit when the user wants checkpoints: `git add` then `git commit`.
- To **switch the working tree** to another revision, use `git checkout` / `git switch` with a commit hash you already saw (e.g. from `git log`).

## Commit messages (you must author these — never lazy one-liners)
When you run `git commit`, **you** write the message. Make it **detailed, professional, and descriptive** using solid practice:

1. **Subject line** (first `-m`): imperative mood (*Add*, *Fix*, *Refactor*, not *Added* / *Adds*). Prefer **Conventional Commits**: `type(scope): concise summary` with types like `feat`, `fix`, `docs`, `refactor`, `chore`, `test`. Keep the subject informative (not "update", "changes", "checkpoint", or "WIP") unless the user explicitly wants that.
2. **Body** (strongly recommended for any non-trivial change): use a **second** `git commit -m "..."` for the body (git accepts multiple `-m`; they become paragraphs). Summarize **what** changed, **why**, impacted areas, and risks or follow-ups so a reviewer does not need the full diff. Use clear sentences; use line breaks inside the quoted body when helpful.
3. Match the **actual** staged diff: read `git diff --cached` before committing so the message reflects real changes.
4. If the user only said "commit" with no style preference, still default to the above — do not default to vague subjects like "Checkpoint commit".

## Revision visibility (enforced by the harness)
- Commands that **list** history (`git log`, `git rev-list`, `git reflog`) only ever show at most **{max_visible}** entries. Plan accordingly: you cannot see older commits through those commands.
- You may still **checkout** any commit hash you have obtained earlier in the conversation (or that the user provided), as long as it exists in the repo.

## Files
- Prefer **file_edit** / **file_delete** for source changes; use **bash** only for git.
- Stay within the workspace; do not try to escape with `..` in file paths.

Be concise in final replies; use tools to do the work."""
    if extra_instructions.strip():
        return base + "\n\n## Additional instructions from the operator\n" + extra_instructions.strip()
    return base


DEFAULT_EDITOR_SYSTEM_PROMPT = build_editor_system_prompt()


def run_editor_agent(
    user_prompt: str,
    *,
    workspace: str | Path | None = None,
    system_prompt: str | None = None,
    extra_system_instructions: str = "",
    model: str | None = None,
    max_tool_rounds: int = 64,
) -> str:
    """
    One full OpenRouter tool loop with the revisions tools and editor-focused system prompt.

    If ``system_prompt`` is set, it replaces the default. Otherwise the default is used,
    optionally extended with ``extra_system_instructions``.
    """
    if system_prompt is not None:
        sys_p = system_prompt
    elif extra_system_instructions.strip():
        sys_p = build_editor_system_prompt(extra_instructions=extra_system_instructions)
    else:
        sys_p = DEFAULT_EDITOR_SYSTEM_PROMPT

    return _run_with_openrouter(
        system_prompt=sys_p,
        user_prompt=user_prompt,
        workspace=workspace,
        model=model,
        max_tool_rounds=max_tool_rounds,
    )


def _cli() -> None:
    p = argparse.ArgumentParser(
        description="Revisions LLM editor (OpenRouter + git/file tools)",
    )
    p.add_argument(
        "-w",
        "--workspace",
        default=".",
        help="Git workspace root (default: cwd)",
    )
    p.add_argument(
        "-u",
        "--user",
        default="",
        help="User task (default: read stdin)",
    )
    p.add_argument(
        "--system-file",
        type=Path,
        default=None,
        help="Replace default system prompt with this file (UTF-8)",
    )
    p.add_argument(
        "--extra-system-file",
        type=Path,
        default=None,
        help="Append this file to the default system prompt",
    )
    p.add_argument("-m", "--model", default=None, help="OpenRouter model id")
    p.add_argument(
        "--max-rounds",
        type=int,
        default=64,
        help="Max tool rounds (default: 64)",
    )
    args = p.parse_args()

    user = args.user.strip() or sys.stdin.read()
    if not user.strip():
        print("error: provide --user or stdin", file=sys.stderr)
        sys.exit(2)

    if args.system_file is not None:
        sys_p = args.system_file.read_text(encoding="utf-8")
        if args.extra_system_file is not None:
            sys_p += (
                "\n\n## Additional instructions from the operator\n"
                + args.extra_system_file.read_text(encoding="utf-8")
            )
        out = _run_with_openrouter(
            system_prompt=sys_p,
            user_prompt=user,
            workspace=args.workspace,
            model=args.model,
            max_tool_rounds=args.max_rounds,
        )
    else:
        extra = (
            args.extra_system_file.read_text(encoding="utf-8")
            if args.extra_system_file is not None
            else ""
        )
        out = run_editor_agent(
            user,
            workspace=args.workspace,
            extra_system_instructions=extra,
            model=args.model,
            max_tool_rounds=args.max_rounds,
        )

    if out:
        print(out)


def main() -> None:
    """CLI entrypoint (also used by ``python -m harness.revisions_editor``)."""
    _cli()


if __name__ == "__main__":
    main()
