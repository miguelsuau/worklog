#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
package_dir="${repo_root}/packages/claude"
dist_dir="${1:-${repo_root}/dist}"
zip_path="${dist_dir}/worklog-claude-plugin.zip"

if [[ "${WORKLOG_SKIP_PACKAGE_BUILD:-}" != "1" ]]; then
  "${repo_root}/scripts/build_packages.sh" >/dev/null
fi

required_files=(
  ".claude-plugin/plugin.json"
  ".mcp.json"
  "skills/worklog/SKILL.md"
  "scripts/worklog_mcp_server.py"
  "lib/worklog/mcp_server.py"
)

for required in "${required_files[@]}"; do
  if [[ ! -f "${package_dir}/${required}" ]]; then
    echo "Missing Claude plugin package file: ${package_dir}/${required}" >&2
    exit 1
  fi
done

mkdir -p "${dist_dir}"
rm -f "${zip_path}"

export WORKLOG_CLAUDE_PACKAGE_DIR="${package_dir}"
export WORKLOG_CLAUDE_PLUGIN_ZIP="${zip_path}"

python3 - <<'PY'
from __future__ import annotations

import os
import zipfile
from pathlib import Path

package_dir = Path(os.environ["WORKLOG_CLAUDE_PACKAGE_DIR"])
zip_path = Path(os.environ["WORKLOG_CLAUDE_PLUGIN_ZIP"])
skip_names = {".DS_Store"}

with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir).as_posix()
        if path.name in skip_names or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        info = zipfile.ZipInfo.from_file(path, arcname=relative)
        info.create_system = 3
        mode = 0o100755 if os.access(path, os.X_OK) else 0o100644
        info.external_attr = (mode & 0xFFFF) << 16
        with path.open("rb") as handle:
            archive.writestr(info, handle.read(), compress_type=zipfile.ZIP_DEFLATED)
PY

echo "Built ${zip_path}"
