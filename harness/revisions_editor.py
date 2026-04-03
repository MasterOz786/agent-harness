"""
Run the revisions LLM editor without setting PYTHONPATH.

From the agent-harness repo root::

    python -m harness.revisions_editor -w . -u "Your task"
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    agents_dir = root / "agents"
    if not agents_dir.is_dir():
        print("error: expected agents/ next to harness/", file=sys.stderr)
        sys.exit(2)
    if str(agents_dir) not in sys.path:
        sys.path.insert(0, str(agents_dir))
    from revisions.agent import main as editor_main

    editor_main()


if __name__ == "__main__":
    main()
