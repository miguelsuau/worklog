#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="${repo_root}/packages/claude-code"
target_dir="${HOME}/.claude/skills/worklog"
store_dir="${HOME}/.claude/worklog/store"

"${repo_root}/scripts/build_packages.sh" >/dev/null

for required_path in \
  "${source_dir}/SKILL.md" \
  "${source_dir}/scripts/worklog_mcp_server.py" \
  "${source_dir}/lib/worklog/mcp_server.py"; do
  if [[ ! -e "${required_path}" ]]; then
    echo "Could not find ${required_path}" >&2
    exit 1
  fi
done

mkdir -p "${HOME}/.claude/skills" "${store_dir}"

if [[ -e "${target_dir}" && ! -d "${target_dir}" ]]; then
  echo "${target_dir} exists but is not a directory" >&2
  exit 1
fi

if [[ -d "${target_dir}" ]]; then
  backup_dir="${target_dir}.backup.$(date -u +%Y%m%d%H%M%S)"
  mv "${target_dir}" "${backup_dir}"
  echo "Backed up existing Worklog skill to ${backup_dir}"
fi

mkdir -p "${target_dir}"
cp "${source_dir}/SKILL.md" "${target_dir}/SKILL.md"
cp -R "${source_dir}/scripts" "${target_dir}/scripts"
cp -R "${source_dir}/lib" "${target_dir}/lib"

mcp_json="$(
  TARGET_DIR="${target_dir}" STORE_DIR="${store_dir}" python3 - <<'PY'
import json
import os

print(json.dumps({
    "command": "python3",
    "args": [
        os.path.join(os.environ["TARGET_DIR"], "scripts", "worklog_mcp_server.py"),
    ],
    "env": {
        "WORKLOG_STORE": os.environ["STORE_DIR"],
    },
    "startup_timeout_sec": 10,
    "tool_timeout_sec": 60,
}))
PY
)"

echo "Installed Worklog for Claude Code at ${target_dir}"

if command -v claude >/dev/null 2>&1; then
  if claude mcp add-json --scope user worklog "${mcp_json}"; then
    echo "Registered the Worklog MCP server with Claude Code."
  else
    echo "Claude CLI MCP registration failed." >&2
    echo "Run this command from an environment where the Claude CLI can update MCP settings:" >&2
    printf "claude mcp add-json --scope user worklog '%s'\n" "${mcp_json}" >&2
    exit 1
  fi
else
  echo "Claude CLI was not found on PATH, so MCP registration was not changed."
  echo "Run this command from an environment with the Claude CLI:"
  printf "claude mcp add-json --scope user worklog '%s'\n" "${mcp_json}"
fi

echo "Restart Claude Code or the Claude desktop Code tab, then use /worklog."
