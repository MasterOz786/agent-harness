"""
Revisions toolset: exposes bash (git-only), file edit, and file delete as tools.

The ``bash`` tool runs ``git`` with full CLI argv (any subcommand and flags), optional
``env`` and ``stdin``, and optional sequential ``commands`` — still no general shell
(so no ``|``, ``;``, or non-git programs).

All file paths are resolved under ``workspace`` (default: current working directory)
to reduce accidental writes outside the project.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

_COMMIT_JUGAADISM_PATH = os.environ.get(
    "COMMIT_JUGAADISM_PATH",
    str(Path(__file__).resolve().parent / "commit-jugaadism"),
)

# History listings (git log / rev-list / reflog) are capped to this many entries.
MAX_VISIBLE_REVISIONS = 5

_REVISION_LISTING_SUBCOMMANDS = frozenset({"log", "rev-list", "reflog"})


def _workspace_root(workspace: str | Path | None) -> Path:
    root = Path(workspace or os.getcwd()).resolve()
    return root


def _safe_path(workspace: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``workspace``; reject path traversal."""
    candidate = (workspace / relative_path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as e:
        raise ValueError(f"path escapes workspace: {relative_path!r}") from e
    return candidate


def _git_argv_from_command(command: str) -> list[str]:
    """
    Parse ``command`` as a single git invocation (no shell).

    The first token must be exactly ``git`` so pipes, ``;``, subshells, and
    other programs are rejected.
    """
    s = command.strip()
    if not s:
        raise ValueError("empty command")
    try:
        argv = shlex.split(s, posix=True)
    except ValueError as e:
        raise ValueError(f"invalid quoting: {e}") from e
    if not argv:
        raise ValueError("empty command")
    if argv[0] != "git":
        raise ValueError(
            "only `git` is allowed: pass a single git invocation (e.g. `git status`), "
            "no shell, pipes, or other commands"
        )
    return argv


def _git_subcommand_index(argv: list[str]) -> int | None:
    """Index of the git subcommand (e.g. ``log``), after common global options."""
    i = 1
    n = len(argv)
    while i < n:
        a = argv[i]
        if a in (
            "--no-pager",
            "--no-replace-objects",
            "--bare",
            "--paginate",
            "-p",
            "-v",
            "--version",
            "-h",
            "--help",
        ):
            i += 1
            continue
        if a == "-c":
            i += 2
            continue
        if a.startswith("-c") and "=" in a:
            i += 1
            continue
        if a in ("-C", "--work-tree", "--git-dir", "--namespace"):
            i += 2
            continue
        if a.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
            i += 1
            continue
        if a.startswith("-C") and len(a) > 2:
            i += 1
            continue
        if not a.startswith("-") or a == "-":
            return i
        return None
    return None


def _parse_uint(s: str) -> int | None:
    try:
        v = int(s, 10)
        return v if v >= 0 else None
    except ValueError:
        return None


def _strip_max_count_options(tail: list[str], sub: str) -> tuple[list[str], int | None]:
    """
    Remove ``-n`` / ``--max-count`` / ``log``'s ``-N`` count forms from args after subcommand.

    Returns ``(new_tail, last_limit)`` where ``last_limit`` mimics git's last-specified-wins.
    """
    out: list[str] = []
    limits: list[int] = []
    i = 0
    n = len(tail)
    while i < n:
        a = tail[i]
        if a in ("-n", "--max-count"):
            if i + 1 < n:
                v = _parse_uint(tail[i + 1])
                if v is not None:
                    limits.append(v)
                i += 2
                continue
            i += 1
            continue
        if a.startswith("--max-count="):
            v = _parse_uint(a.split("=", 1)[1])
            if v is not None:
                limits.append(v)
            i += 1
            continue
        m = re.fullmatch(r"-(\d+)", a)
        if m and sub == "log":
            limits.append(int(m.group(1)))
            i += 1
            continue
        out.append(a)
        i += 1
    last = limits[-1] if limits else None
    return out, last


def cap_git_revision_listing_argv(
    argv: list[str],
    max_revisions: int = MAX_VISIBLE_REVISIONS,
) -> list[str]:
    """
    For ``git log``, ``git rev-list``, and ``git reflog``, enforce ``--max-count``
    at most ``max_revisions`` (and at most the user's own limit if lower).
    """
    idx = _git_subcommand_index(argv)
    if idx is None:
        return argv
    sub = argv[idx]
    if sub not in _REVISION_LISTING_SUBCOMMANDS:
        return argv
    head = argv[: idx + 1]
    tail = argv[idx + 1 :]
    new_tail, user_last = _strip_max_count_options(list(tail), sub)
    if user_last is not None:
        eff = min(max_revisions, user_last)
    else:
        eff = max_revisions
    return head + ["--max-count", str(eff)] + new_tail


def _git_env(extra: dict[str, str] | None) -> dict[str, str]:
    """Merge ``extra`` into a copy of the process environment (string values only)."""
    run_env: dict[str, str] = dict(os.environ)
    run_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if not extra:
        return run_env
    for k, v in extra.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("env keys and values must be strings")
        run_env[k] = v
    return run_env


def _bash_command_lines(
    command: str | None,
    commands: Sequence[str] | None,
) -> list[str]:
    if commands is not None:
        if command is not None:
            raise ValueError("pass either `command` or `commands`, not both")
        if not isinstance(commands, (list, tuple)):
            raise ValueError("`commands` must be a list of strings")
        if not commands:
            raise ValueError("`commands` must be non-empty")
        out: list[str] = []
        for i, line in enumerate(commands):
            if not isinstance(line, str):
                raise ValueError(f"`commands[{i}]` must be a string")
            out.append(line)
        return out
    if command is None:
        raise ValueError("pass `command` (string) or `commands` (list of strings)")
    if not isinstance(command, str):
        raise ValueError("`command` must be a string")
    return [command]


def bash(
    command: str | None = None,
    *,
    commands: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    workspace: str | Path | None = None,
    timeout_sec: float | None = 120.0,
) -> dict[str, Any]:
    """
    Run **only** ``git`` with full CLI power: any subcommand, flags, ``-c`` config,
    ``--work-tree`` / ``-C``, etc. ``cwd`` is ``workspace``.

    Pass either ``command`` (one line) or ``commands`` (run in order, stop on first
    non-zero exit). Each line is tokenized with ``shlex``; there is no shell.

    ``env`` is merged into the subprocess environment (e.g. ``GIT_TRACE``,
    ``GIT_SSH_COMMAND``). ``stdin`` is sent to the **first** invocation only
    (e.g. ``git apply``, ``git am``).

    Returns ``stdout`` / ``stderr`` concatenated across steps, final ``returncode``,
    ``ok``, and ``steps`` (per-invocation detail) when more than one step runs.
    """
    root = _workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    try:
        lines = _bash_command_lines(command, commands)
        run_env = _git_env(env)
    except ValueError as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
        }

    steps_out: list[dict[str, Any]] = []
    combined_out: list[str] = []
    combined_err: list[str] = []
    last_code = 0

    for idx, line in enumerate(lines):
        try:
            argv = cap_git_revision_listing_argv(_git_argv_from_command(line))
        except ValueError as e:
            return {
                "ok": False,
                "returncode": -1,
                "stdout": "".join(combined_out),
                "stderr": "".join(combined_err) + str(e),
                "failed_step": idx,
                "steps": steps_out,
            }
        input_data = stdin if idx == 0 and stdin is not None else None
        proc = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=run_env,
            input=input_data,
        )
        last_code = proc.returncode
        combined_out.append(proc.stdout)
        combined_err.append(proc.stderr)
        step = {
            "index": idx,
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        steps_out.append(step)
        if proc.returncode != 0:
            out: dict[str, Any] = {
                "ok": False,
                "returncode": proc.returncode,
                "stdout": "".join(combined_out),
                "stderr": "".join(combined_err),
                "failed_step": idx,
                "steps": steps_out,
            }
            return out

    result: dict[str, Any] = {
        "ok": True,
        "returncode": last_code,
        "stdout": "".join(combined_out),
        "stderr": "".join(combined_err),
    }
    if len(steps_out) > 1:
        result["steps"] = steps_out
    return result


def file_edit(
    path: str,
    content: str,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """
    Create or overwrite a file at ``path`` (relative to ``workspace``) with ``content``.
    Creates parent directories as needed.
    """
    root = _workspace_root(workspace)
    target = _safe_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}


def ai_commit(
    *,
    dry_run: bool = False,
    no_stage: bool = False,
    workspace: str | Path | None = None,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    """
    Stage all changes, generate an AI commit message via commit-jugaadism,
    then create the commit (or just print the message when ``dry_run`` is True).

    The ``commit-jugaadism`` CLI lives in ``COMMIT_JUGAADISM_PATH`` (env var),
    defaulting to a sibling repo next to ``agent-harness``.
    """
    try:
        jugaadism_root = Path(_COMMIT_JUGAADISM_PATH).resolve()
    except Exception as e:
        return {"ok": False, "error": f"resolve COMMIT_JUGAADISM_PATH: {e}"}

    node_bin = shutil.which("node")
    if not node_bin:
        return {"ok": False, "error": "node binary not found on PATH"}

    cli_script = jugaadism_root / "src" / "cli.js"
    if not cli_script.is_file():
        return {"ok": False, "error": f"commit-jugaadism CLI missing: {cli_script}"}

    root = _workspace_root(workspace)
    run_env = dict(os.environ)
    run_env["NODE_NO_WARNINGS"] = "1"

    argv: list[str] = [node_bin, str(cli_script)]
    if dry_run:
        argv.append("--dry-run")
    if no_stage:
        argv.append("--no-stage")

    try:
        proc = subprocess.run(
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ai_commit: timed out"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }

    result: dict[str, Any] = {
        "ok": True,
        "returncode": 0,
        "stdout": proc.stdout.strip(),
    }
    if dry_run:
        result["dry_run"] = True
    return result


def file_delete(
    path: str,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Delete a file at ``path`` (relative to ``workspace``). Fails if not a file."""
    root = _workspace_root(workspace)
    target = _safe_path(root, path)
    if not target.is_file():
        return {"ok": False, "error": f"not a file or missing: {target}"}
    target.unlink()
    return {"ok": True, "path": str(target)}


ToolFn = Callable[..., dict[str, Any]]

TOOLS: dict[str, ToolFn] = {
    "bash": bash,
    "ai_commit": ai_commit,
    "file_edit": file_edit,
    "file_delete": file_delete,
}


def tool_schemas() -> list[dict[str, Any]]:
    """JSON-serializable tool descriptions for LLM / harness integration."""
    return [
        {
            "name": "bash",
            "description": (
                "Full native `git` CLI: any subcommand and flags (incl. `-c`, `-C`, worktree, "
                "aliases resolved by git). Optional `env` (e.g. GIT_TRACE, GIT_SSH_COMMAND), "
                "`stdin` on the first step only. "
                "You must pass either `command` (one line) OR `commands` (non-empty array of lines), "
                "never both. "
                "No general shell (no `|`, `;`, non-git binaries). Cwd is the workspace. "
                "For `git log`, `git rev-list`, and `git reflog`, history is capped to at most "
                f"{MAX_VISIBLE_REVISIONS} entries (your `-n`/`--max-count` is respected if lower)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "One git command line (shlex-split; must start with git). Omit if using `commands`.",
                    },
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple git lines, run sequentially; stop on first failure. Omit if using `command`.",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Extra environment for git (merged over process env)",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Stdin for the first git invocation only (e.g. patch for git apply)",
                    },
                    "timeout_sec": {
                        "type": "number",
                        "description": "Optional timeout in seconds per invocation (default 120)",
                    },
                },
            },
        },
        {
            "name": "ai_commit",
            "description": (
                "Stage all changes, generate an AI commit message via commit-jugaadism "
                "(OpenRouter), and create the commit. Use `dry_run=true` to preview the "
                "message without committing. Use `no_stage=true` to skip `git add`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, only generate and print the commit message without committing",
                    },
                    "no_stage": {
                        "type": "boolean",
                        "description": "If true, skip git add (use when changes are already staged)",
                    },
                },
            },
        },
        {
            "name": "file_edit",
            "description": "Create or overwrite a file under the workspace with the given UTF-8 text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to workspace"},
                    "content": {"type": "string", "description": "Full new file contents"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "file_delete",
            "description": "Delete a regular file under the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to workspace"},
                },
                "required": ["path"],
            },
        },
    ]


def invoke_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Dispatch a single tool by name. Unknown tools return ok=False."""
    arguments = dict(arguments or {})
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool: {name!r}"}
    fn = TOOLS[name]
    kwargs = dict(arguments)
    if workspace is not None:
        kwargs["workspace"] = workspace
    try:
        return fn(**kwargs)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {name!r}: {e}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git: command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def revisions_tools_spec() -> dict[str, Any]:
    """Tool pack descriptor: slug, tool names, and JSON schemas."""
    return {
        "name": "revisions",
        "tools": list(TOOLS.keys()),
        "schemas": tool_schemas(),
        "max_visible_revisions": MAX_VISIBLE_REVISIONS,
    }


def _cli() -> None:
    """Minimal CLI: `python -m revisions.tools invoke <tool> '{"key":...}'` or `info`."""
    if len(sys.argv) < 2:
        print(json.dumps(revisions_tools_spec(), indent=2))
        return
    cmd = sys.argv[1]
    if cmd == "info":
        print(json.dumps(revisions_tools_spec(), indent=2))
        return
    if cmd == "invoke" and len(sys.argv) >= 4:
        tool_name = sys.argv[2]
        args = json.loads(sys.argv[3])
        ws = os.environ.get("REVISIONS_WORKSPACE")
        out = invoke_tool(tool_name, args, workspace=ws)
        print(json.dumps(out, indent=2))
        sys.exit(0 if out.get("ok") else 1)
    print(
        "usage: python -m revisions.tools [info | invoke <tool> '<json-args>']",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    _cli()
