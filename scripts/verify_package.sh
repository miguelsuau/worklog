#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WORKLOG_REPO_ROOT="${repo_root}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/worklog-pycache}"

python3 "${repo_root}/scripts/generate_skills.py" --check
"${repo_root}/scripts/build_packages.sh" >/dev/null

python3 -m py_compile \
  "${repo_root}/scripts/generate_skills.py" \
  "${repo_root}/src/worklog/mcp_server.py" \
  "${repo_root}/packages/claude/scripts/worklog_mcp_server.py" \
  "${repo_root}/packages/codex/scripts/worklog_mcp_server.py" \
  "${repo_root}/packages/claude/lib/worklog/mcp_server.py" \
  "${repo_root}/packages/codex/lib/worklog/mcp_server.py"

python3 - <<'PY'
import json
import os
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
files = [
    repo / ".claude-plugin" / "marketplace.json",
    repo / ".agents" / "plugins" / "marketplace.json",
    repo / "packages" / "claude" / ".claude-plugin" / "plugin.json",
    repo / "packages" / "codex" / ".codex-plugin" / "plugin.json",
    repo / "packages" / "claude" / ".mcp.json",
    repo / "packages" / "codex" / ".mcp.json",
]
for file in files:
    json.loads(file.read_text())
source = repo / "src" / "worklog" / "mcp_server.py"
for package in [repo / "packages" / "claude", repo / "packages" / "codex"]:
    vendored = package / "lib" / "worklog" / "mcp_server.py"
    if vendored.read_bytes() != source.read_bytes():
        raise SystemExit(f"{vendored} differs from shared source.")
print("Worklog package files are valid JSON, generated skills are current, package builds are current, and the MCP server compiles.")
PY
