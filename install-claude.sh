#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="${repo_root}/claude/worklog"
target_dir="${HOME}/.claude/skills/worklog"

if [[ ! -f "${source_dir}/SKILL.md" ]]; then
  echo "Could not find ${source_dir}/SKILL.md" >&2
  exit 1
fi

mkdir -p "${HOME}/.claude/skills"

if [[ -e "${target_dir}" && ! -d "${target_dir}" ]]; then
  echo "${target_dir} exists but is not a directory" >&2
  exit 1
fi

if [[ -d "${target_dir}" ]]; then
  backup_dir="${target_dir}.backup.$(date -u +%Y%m%d%H%M%S)"
  mv "${target_dir}" "${backup_dir}"
  echo "Backed up existing Worklog skill to ${backup_dir}"
fi

cp -R "${source_dir}" "${target_dir}"
echo "Installed Worklog for Claude at ${target_dir}"
echo "Restart Claude Code or run /reload-plugins, then use /worklog."
