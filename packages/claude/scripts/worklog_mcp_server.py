#!/usr/bin/env python3
"""Worklog MCP launcher for host integration packages.

The implementation lives in src/worklog during development and is copied into
each package's lib/ directory by scripts/build_packages.sh for installation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root_for(script: Path) -> Path:
    if script.parent.name == "launcher":
        return script.parents[1]
    return script.parents[3]


def add_worklog_source() -> None:
    script = Path(__file__).resolve()
    repo_root = repo_root_for(script)
    candidates: list[Path] = []

    if os.environ.get("WORKLOG_REPO_ROOT"):
        candidates.append(Path(os.environ["WORKLOG_REPO_ROOT"]) / "src")

    candidates.extend(
        [
            script.parents[1] / "lib",
            repo_root / "src",
        ]
    )

    for candidate in candidates:
        if (candidate / "worklog" / "mcp_server.py").exists():
            sys.path.insert(0, str(candidate))
            return

    raise RuntimeError(
        "Could not find Worklog shared source. Run scripts/build_packages.sh "
        "from the repo before installing this package."
    )


add_worklog_source()

from worklog.mcp_server import main


if __name__ == "__main__":
    raise SystemExit(main())
