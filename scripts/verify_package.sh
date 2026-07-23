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
WORKLOG_SKIP_PACKAGE_BUILD=1 "${repo_root}/scripts/build_claude_plugin_zip.sh" >/dev/null

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
import json
import os
import zipfile
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
files = [
    repo / "plugin.metadata.json",
    repo / ".claude-plugin" / "marketplace.json",
    repo / ".agents" / "plugins" / "marketplace.json",
    repo / "packages" / "claude" / ".claude-plugin" / "plugin.json",
    repo / "packages" / "claude-code" / ".claude-plugin" / "plugin.json",
    repo / "packages" / "codex" / ".codex-plugin" / "plugin.json",
    repo / "packages" / "claude" / ".mcp.json",
    repo / "packages" / "claude-code" / ".mcp.json",
    repo / "packages" / "codex" / ".mcp.json",
]
for file in files:
    json.loads(file.read_text())
source = repo / "src" / "worklog" / "mcp_server.py"
for package in [repo / "packages" / "claude", repo / "packages" / "claude-code", repo / "packages" / "codex"]:
    vendored = package / "lib" / "worklog" / "mcp_server.py"
    if vendored.read_bytes() != source.read_bytes():
        raise SystemExit(f"{vendored} differs from shared source.")

zip_path = repo / "dist" / "worklog-claude-plugin.zip"
required_zip_members = {
    ".claude-plugin/plugin.json",
    ".mcp.json",
    "skills/worklog/SKILL.md",
    "scripts/worklog_mcp_server.py",
    "lib/worklog/__init__.py",
    "lib/worklog/mcp_server.py",
}
with zipfile.ZipFile(zip_path) as archive:
    names = set(archive.namelist())
    missing = sorted(required_zip_members - names)
    if missing:
        raise SystemExit(f"Claude plugin ZIP is missing: {', '.join(missing)}")
    bad = [
        name
        for name in names
        if name.startswith("packages/claude/")
        or name.startswith("__MACOSX/")
        or name.endswith(".pyc")
        or "/__pycache__/" in name
        or name.endswith("/.DS_Store")
    ]
    if bad:
        raise SystemExit(f"Claude plugin ZIP contains generated junk or nested paths: {bad[:5]}")
    plugin = json.loads(archive.read(".claude-plugin/plugin.json"))
    mcp = json.loads(archive.read(".mcp.json"))
    if plugin.get("name") != "worklog":
        raise SystemExit("Claude plugin ZIP manifest name is not worklog.")
    if plugin.get("skills") != "./skills/":
        raise SystemExit("Claude plugin ZIP manifest does not point at ./skills/.")
    if "worklog" not in mcp.get("mcpServers", {}):
        raise SystemExit("Claude plugin ZIP MCP config does not define the worklog server.")
print("Worklog package files are valid JSON, generated files and vendored package sources are current, package builds are current, the Claude plugin ZIP is valid, and the MCP server compiles.")
PY
