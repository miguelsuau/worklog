#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WORKLOG_REPO_ROOT="${repo_root}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/worklog-pycache}"

python3 -m py_compile "${repo_root}/plugins/worklog/scripts/worklog_mcp_server.py"
python3 - <<'PY'
import json
import os
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
files = [
    repo / ".claude-plugin" / "marketplace.json",
    repo / ".agents" / "plugins" / "marketplace.json",
    repo / "claude" / "worklog" / ".claude-plugin" / "plugin.json",
    repo / "plugins" / "worklog" / ".codex-plugin" / "plugin.json",
    repo / "claude" / "worklog" / ".mcp.json",
    repo / "plugins" / "worklog" / ".mcp.json",
]
for file in files:
    json.loads(file.read_text())
claude_server = repo / "claude" / "worklog" / "scripts" / "worklog_mcp_server.py"
codex_server = repo / "plugins" / "worklog" / "scripts" / "worklog_mcp_server.py"
if claude_server.read_bytes() != codex_server.read_bytes():
    raise SystemExit("Claude and Codex MCP server copies differ.")
print("Worklog package files are valid JSON and the MCP server compiles.")
PY
