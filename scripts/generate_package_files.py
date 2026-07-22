#!/usr/bin/env python3
"""Render generated Worklog package files from shared sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BODY_PATH = ROOT / "skill" / "worklog.body.md"
METADATA_PATH = ROOT / "plugin.metadata.json"
LAUNCHER_PATH = ROOT / "launcher" / "worklog_mcp_server.py"
PLACEHOLDER = "{{HOST_INVOCATION_NOTE}}"

HOSTS = {
    "claude": {
        "skill_path": ROOT / "packages" / "claude" / "SKILL.md",
        "launcher_path": ROOT / "packages" / "claude" / "scripts" / "worklog_mcp_server.py",
        "skill_description": (
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
        "skill_path": ROOT / "packages" / "codex" / "skills" / "worklog" / "SKILL.md",
        "launcher_path": ROOT / "packages" / "codex" / "scripts" / "worklog_mcp_server.py",
        "skill_description": (
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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2) + "\n"


def render_skill(host: dict[str, Any], body: str) -> str:
    return (
        "---\n"
        "name: worklog\n"
        f"description: {host['skill_description']}\n"
        "---\n\n"
        "# Worklog\n\n"
        f"{body.replace(PLACEHOLDER, host['invocation_note']).rstrip()}\n"
    )


def generated_manifests(metadata: dict[str, Any]) -> dict[Path, str]:
    author = metadata["author"]
    descriptions = metadata["descriptions"]
    name = metadata["name"]
    marketplace_name = metadata["marketplace_name"]
    category = metadata["category"]
    version = metadata["version"]

    claude_plugin = {
        "name": name,
        "description": descriptions["claude"],
        "version": version,
        "author": author,
        "mcpServers": "./.mcp.json",
    }
    codex_plugin = {
        "name": name,
        "version": version,
        "description": descriptions["codex"],
        "author": author,
        "license": metadata["license"],
        "keywords": metadata["keywords"],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": metadata["display_name"],
            "shortDescription": descriptions["short"],
            "longDescription": descriptions["long"],
            "developerName": author["name"],
            "category": category,
            "capabilities": metadata["capabilities"],
            "defaultPrompt": metadata["default_prompts"],
        },
    }
    codex_marketplace = {
        "name": marketplace_name,
        "interface": {
            "displayName": metadata["marketplace_display_name"],
        },
        "plugins": [
            {
                "name": name,
                "source": {
                    "source": "local",
                    "path": "./packages/codex",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": category,
            }
        ],
    }
    claude_marketplace = {
        "name": marketplace_name,
        "owner": author,
        "description": descriptions["marketplace"],
        "plugins": [
            {
                "name": name,
                "source": "./packages/claude",
                "description": descriptions["claude"],
                "version": version,
                "author": author,
                "category": category,
                "tags": metadata["tags"],
            }
        ],
    }
    return {
        ROOT / "packages" / "claude" / ".claude-plugin" / "plugin.json": json_text(claude_plugin),
        ROOT / "packages" / "codex" / ".codex-plugin" / "plugin.json": json_text(codex_plugin),
        ROOT / ".agents" / "plugins" / "marketplace.json": json_text(codex_marketplace),
        ROOT / ".claude-plugin" / "marketplace.json": json_text(claude_marketplace),
    }


def generated_files() -> dict[Path, str]:
    body = BODY_PATH.read_text()
    if PLACEHOLDER not in body:
        raise ValueError(f"{BODY_PATH} is missing {PLACEHOLDER}")

    metadata = read_json(METADATA_PATH)
    launcher = LAUNCHER_PATH.read_text()
    outputs = generated_manifests(metadata)

    for host in HOSTS.values():
        outputs[host["skill_path"]] = render_skill(host, body)
        outputs[host["launcher_path"]] = launcher

    return outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero if generated package files are stale.",
    )
    args = parser.parse_args()

    try:
        outputs = generated_files()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    stale: list[Path] = []
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    if stale:
        for path in stale:
            print(f"stale generated file: {path}", file=sys.stderr)
        print("Run scripts/generate_package_files.py to update generated package files.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
