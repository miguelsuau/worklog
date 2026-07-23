#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${repo_root}/src/worklog"

python3 "${repo_root}/scripts/generate_package_files.py"

if [[ ! -f "${source_dir}/mcp_server.py" ]]; then
  echo "Could not find shared Worklog source at ${source_dir}" >&2
  exit 1
fi

for package_dir in \
  "${repo_root}/packages/claude" \
  "${repo_root}/packages/claude-code" \
  "${repo_root}/packages/codex"; do
  rm -rf "${package_dir}/lib/worklog"
  mkdir -p "${package_dir}/lib"
  cp -R "${source_dir}" "${package_dir}/lib/worklog"
done

echo "Built Claude plugin, Claude Code, and Codex Worklog packages."
