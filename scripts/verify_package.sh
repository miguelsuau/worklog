#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export WORKLOG_REPO_ROOT="${repo_root}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/worklog-pycache}"

python3 "${repo_root}/scripts/generate_package_files.py" --check

python3 - <<'PY'
import os
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
source = repo / "src" / "worklog" / "mcp_server.py"
packages = [
    repo / "packages" / "claude",
    repo / "packages" / "claude-code",
    repo / "packages" / "codex",
]
for package in packages:
    vendored = package / "lib" / "worklog" / "mcp_server.py"
    if vendored.read_bytes() != source.read_bytes():
        raise SystemExit(f"{vendored} differs from shared source.")
PY

"${repo_root}/scripts/build_packages.sh" >/dev/null

python3 -m py_compile \
  "${repo_root}/scripts/generate_package_files.py" \
  "${repo_root}/launcher/worklog_mcp_server.py" \
  "${repo_root}/src/worklog/mcp_server.py" \
  "${repo_root}/packages/claude/scripts/worklog_mcp_server.py" \
  "${repo_root}/packages/claude-code/scripts/worklog_mcp_server.py" \
  "${repo_root}/packages/codex/scripts/worklog_mcp_server.py" \
  "${repo_root}/packages/claude/lib/worklog/mcp_server.py" \
  "${repo_root}/packages/claude-code/lib/worklog/mcp_server.py" \
  "${repo_root}/packages/codex/lib/worklog/mcp_server.py"

python3 - <<'PY'
import os
import sys
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
sys.path.insert(0, str(repo / "src"))

from worklog.mcp_server import (  # noqa: E402
    project_log_approval_blockers,
    project_log_attention,
    render_project_log_review_metadata,
)


def project_log(next_actions):
    return {
        "id": "project_log_guardrail_test",
        "project_id": "project_guardrail_test",
        "status": "draft",
        "version": 2,
        "session_log_ids": [],
        "template": {
            "sections": [
                {
                    "key": "next_actions",
                    "title": "Next Actions",
                    "kind": "list",
                }
            ]
        },
        "sections": {
            "next_actions": next_actions,
        },
    }


blocked = project_log(["Review this project-log draft and either request edits or explicitly approve it."])
if not project_log_approval_blockers(blocked):
    raise SystemExit("Expected exact project-log review boilerplate to block approval.")
if "Attention" not in render_project_log_review_metadata(blocked, []):
    raise SystemExit("Expected project-log review boilerplate to appear in review metadata.")

warned = project_log(["Please review this project-log draft with the project approver."])
if not project_log_attention(warned):
    raise SystemExit("Expected likely project-log review boilerplate to produce attention.")
if project_log_approval_blockers(warned):
    raise SystemExit("Only exact project-log review boilerplate should block approval.")

clean = project_log(["Verify live Worklog MCP responses in a fresh or reloaded task."])
if project_log_attention(clean):
    raise SystemExit("Durable next actions should not trigger project-log review boilerplate attention.")
PY

python3 - <<'PY'
import json
import os
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
files = [
    repo / "plugin.metadata.json",
    repo / ".claude-plugin" / "marketplace.json",
    repo / ".agents" / "plugins" / "marketplace.json",
    repo / "packages" / "claude" / ".claude-plugin" / "plugin.json",
    repo / "packages" / "codex" / ".codex-plugin" / "plugin.json",
    repo / "packages" / "claude" / ".mcp.json",
    repo / "packages" / "codex" / ".mcp.json",
]
for file in files:
    json.loads(file.read_text())
for forbidden in [
    repo / "packages" / "claude-code" / ".claude-plugin" / "plugin.json",
    repo / "packages" / "claude-code" / ".mcp.json",
]:
    if forbidden.exists():
        raise SystemExit(f"{forbidden} must not exist in the standalone Claude Code skill package.")
source = repo / "src" / "worklog" / "mcp_server.py"
for package in [repo / "packages" / "claude", repo / "packages" / "claude-code", repo / "packages" / "codex"]:
    vendored = package / "lib" / "worklog" / "mcp_server.py"
    if vendored.read_bytes() != source.read_bytes():
        raise SystemExit(f"{vendored} differs from shared source.")
print("Worklog package files are valid JSON, generated files and vendored package sources are current, package builds are current, and the MCP server compiles.")
PY
