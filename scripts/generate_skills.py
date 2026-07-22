#!/usr/bin/env python3
"""Render host-specific Worklog SKILL.md files from one shared body."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BODY_PATH = ROOT / "skill" / "worklog.body.md"
PLACEHOLDER = "{{HOST_INVOCATION_NOTE}}"

HOSTS = {
    "claude": {
        "path": ROOT / "packages" / "claude" / "SKILL.md",
        "description": (
            "Use Worklog when the user invokes /worklog, asks to use Worklog, or wants "
            "to track, resume, or review project work; create user-approved templates, "
            "capture session logs, author project-log rollups, and generate resume "
            "context using the installed Worklog MCP tools."
        ),
        "invocation_note": (
            "Claude can invoke this skill with `/worklog`. `$worklog`, `@worklog`, and "
            "`\\worklog` are command-like conventions from other agent hosts; if the "
            "host passes any of them through in the prompt, treat it exactly like an "
            "explicit Worklog invocation."
        ),
    },
    "codex": {
        "path": ROOT / "packages" / "codex" / "skills" / "worklog" / "SKILL.md",
        "description": (
            "Use Worklog when the user explicitly invokes $worklog, /worklog, \\worklog, "
            "or @worklog, asks to use Worklog, or wants to track, resume, or review "
            "project work; create user-approved templates, capture session logs, "
            "author project-log rollups, and generate resume context using the "
            "installed Worklog MCP tools."
        ),
        "invocation_note": (
            "Codex can invoke this skill with `$worklog` or `/worklog`. ChatGPT plugin "
            "surfaces support `@worklog`. `\\worklog` is a command-like text "
            "convention; if the host passes it through in the prompt, treat it exactly "
            "like an explicit Worklog invocation."
        ),
    },
}


def render_skill(host: dict[str, str], body: str) -> str:
    return (
        "---\n"
        "name: worklog\n"
        f"description: {host['description']}\n"
        "---\n\n"
        "# Worklog\n\n"
        f"{body.replace(PLACEHOLDER, host['invocation_note']).rstrip()}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if generated skill files are stale.",
    )
    args = parser.parse_args()

    body = BODY_PATH.read_text()
    if PLACEHOLDER not in body:
        print(f"{BODY_PATH} is missing {PLACEHOLDER}", file=sys.stderr)
        return 1

    stale: list[Path] = []
    for host in HOSTS.values():
        path = host["path"]
        rendered = render_skill(host, body)
        if args.check:
            if not path.exists() or path.read_text() != rendered:
                stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)

    if stale:
        for path in stale:
            print(f"stale generated skill: {path}", file=sys.stderr)
        print("Run scripts/generate_skills.py to update generated skills.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
