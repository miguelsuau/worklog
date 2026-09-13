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

required_skill_phrases = [
    "When a substantive Worklog-tracked task is clearly complete, draft and present the session log automatically",
    "Do not merely offer to create the session log later.",
]
skill_paths = [
    repo / "skill" / "worklog.body.md",
    repo / "packages" / "claude" / "skills" / "worklog" / "SKILL.md",
    repo / "packages" / "claude-code" / "SKILL.md",
    repo / "packages" / "codex" / "skills" / "worklog" / "SKILL.md",
]
for path in skill_paths:
    text = path.read_text(encoding="utf-8")
    for phrase in required_skill_phrases:
        if phrase not in text:
            raise SystemExit(f"{path} is missing required session-log completion guidance: {phrase}")
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
import tempfile
from pathlib import Path

repo = Path(os.environ["WORKLOG_REPO_ROOT"])
sys.path.insert(0, str(repo / "src"))

from worklog.mcp_server import (  # noqa: E402
    SESSION_LOG_REVIEW_REASONS,
    Server,
    UserError,
    project_log_approval_blockers,
    project_log_attention,
    require_session_log_review_reason,
    render_project_log_review_metadata,
    schemas,
)


draft_session_schema = next(item for item in schemas() if item["name"] == "worklog_draft_session_log")
draft_session_input_schema = draft_session_schema["inputSchema"]
if "review_reason" not in draft_session_input_schema.get("required", []):
    raise SystemExit("worklog_draft_session_log must require review_reason.")
if draft_session_input_schema["properties"]["review_reason"].get("enum") != sorted(SESSION_LOG_REVIEW_REASONS):
    raise SystemExit("worklog_draft_session_log review_reason enum is out of sync.")
if require_session_log_review_reason({"review_reason": "task_complete"}) != "task_complete":
    raise SystemExit("Expected task_complete review_reason to pass.")
try:
    require_session_log_review_reason({})
except UserError as exc:
    if "ask the user before drafting" not in str(exc):
        raise SystemExit("Missing review_reason error should explain the timing gate.")
else:
    raise SystemExit("Expected missing review_reason to block session-log drafting.")


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


SMOKE_TEMPLATE = {
    "version": 1,
    "sections": [
        {"key": "summary", "title": "Summary", "kind": "text"},
        {"key": "next_actions", "title": "Next Actions", "kind": "list"},
    ],
}


def smoke_server(store: Path) -> Server:
    os.environ["WORKLOG_STORE"] = str(store)
    return Server()


def smoke_configure_project(server: Server, project_id: str, approver: str) -> None:
    server.set_project_templates(
        {
            "project_id": project_id,
            "project_nature": "shared implementation smoke test",
            "session_log_template": SMOKE_TEMPLATE,
            "project_log_template": SMOKE_TEMPLATE,
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )
    server.update_project_members(
        {
            "project_id": project_id,
            "actor": approver,
            "operation": "merge",
            "contributors": [approver],
            "project_approvers": [approver],
            "maintainers": [approver],
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )


def smoke_approve_project_log(server: Server, project_id: str, actor: str, summary: str) -> None:
    draft = server.draft_project_log(
        {
            "project_id": project_id,
            "sections": {"summary": summary, "next_actions": ["continue implementation"]},
        }
    )
    server.approve_project_log(
        {
            "project_log_id": draft["project_log"]["id"],
            "approved_by": actor,
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )


def smoke_approve_session_log(server: Server, project_id: str, actor: str, text: str, session_id: str) -> None:
    added = server.add_event(
        {
            "project_id": project_id,
            "session_id": session_id,
            "text": text,
            "speaker": actor,
            "kind": "note",
        }
    )
    draft = server.draft_session_log(
        {
            "project_id": project_id,
            "session_id": added["session"]["id"],
            "capture": False,
            "review_reason": "task_complete",
        }
    )
    edited = server.edit_session_log(
        {
            "session_log_id": draft["session_log"]["id"],
            "sections": {"summary": text, "next_actions": ["pull the next update"]},
        }
    )
    server.approve_session_log(
        {
            "session_log_id": edited["session_log"]["id"],
            "author": actor,
            "approved_by": actor,
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )


with tempfile.TemporaryDirectory(prefix="worklog-shared-join-") as temp_name:
    temp = Path(temp_name)
    project_id = "shared_join_smoke"
    author = smoke_server(temp / "author-store")
    smoke_configure_project(author, project_id, "Author")
    smoke_approve_project_log(author, project_id, "Author", "Initial shared project log.")
    smoke_approve_session_log(author, project_id, "Author", "Initial approved shared session.", "session-one")
    shared_root = temp / "shared"
    author.configure_project_sharing(
        {
            "project_id": project_id,
            "mode": "create",
            "sharing_provider": "local_folder",
            "root": str(shared_root),
            "actor": "Author",
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )
    pushed = author.sync_project({"project_id": project_id, "direction": "push"})
    if not any(item["type"] == "session_log" for item in pushed["published"]):
        raise SystemExit("Expected initial approved session log to publish.")
    if not any(item["type"] == "project_log" for item in pushed["published"]):
        raise SystemExit("Expected initial approved project log to publish.")

    joiner = smoke_server(temp / "join-store")
    discovered = joiner.discover_shared_project(
        {
            "sharing_provider": "local_folder",
            "root": str(shared_root),
            "project_id": project_id,
            "actor": "Joiner",
        }
    )
    if discovered["artifact_counts"]["approved_session_logs"] != 1:
        raise SystemExit("Expected discovery to count one approved session log.")
    if discovered["artifact_counts"]["approved_project_logs"] != 1:
        raise SystemExit("Expected discovery to count one approved project log.")
    if not discovered["latest_project_log"]["available"]:
        raise SystemExit("Expected discovery to summarize the latest project log.")

    joined = joiner.configure_project_sharing(
        {
            **discovered["join_arguments"],
            "actor": "Joiner",
            "confirmed_by_user": True,
            "confirmation_quote": "approved",
        }
    )
    if joined["setup_state"]["status"] != "complete":
        raise SystemExit("Expected join/import to leave local setup complete.")
    backend_permissions = joined["project_sharing"].get("backend_permissions") or {}
    if backend_permissions.get("permission_status") == "pending_backend_verification":
        raise SystemExit("Join/import must not leave backend permissions pending.")
    permission_plan = backend_permissions.get("last_permission_plan") or {}
    if permission_plan.get("required") or permission_plan.get("actions"):
        raise SystemExit("Join/import must not create backend ACL actions for existing shared members.")
    if not any(item["type"] == "session_log" for item in joined["setup"]["pulled"]):
        raise SystemExit("Expected join/import to pull approved session logs.")
    if not any(item["type"] in {"project_log", "current_project_log"} for item in joined["setup"]["pulled"]):
        raise SystemExit("Expected join/import to pull approved project logs.")

    smoke_approve_session_log(author, project_id, "Author", "Second approved shared session.", "session-two")
    dry = joiner.sync_project({"project_id": project_id, "direction": "pull", "dry_run": True})
    if dry["pull_summary"]["pulled_session_logs"] < 1:
        raise SystemExit("Expected dry-run pull to report the new approved session log.")
    if not any(item["status"] == "would_pull" for item in dry["pulled"]):
        raise SystemExit("Expected dry-run pull to report would_pull.")
    actual = joiner.sync_project({"project_id": project_id, "direction": "pull"})
    if actual["pull_summary"]["pulled_session_logs"] < 1:
        raise SystemExit("Expected pull to import the new approved session log.")
    second = joiner.sync_project({"project_id": project_id, "direction": "pull", "dry_run": True})
    if second["pull_summary"]["pulled_session_logs"] != 0:
        raise SystemExit("Expected second dry-run pull to report no new session logs.")
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
claude_plugin = json.loads((repo / "packages" / "claude" / ".claude-plugin" / "plugin.json").read_text())
if claude_plugin.get("skills") != ["./skills/worklog"]:
    raise SystemExit("Claude plugin must declare the Worklog skill path explicitly.")
if "commands" in claude_plugin:
    raise SystemExit("Claude plugin should not declare commands; plugin commands are namespaced skills in Claude Code.")
claude_skill = repo / "packages" / "claude" / "skills" / "worklog" / "SKILL.md"
if not claude_skill.exists():
    raise SystemExit("Claude plugin is missing skills/worklog/SKILL.md.")
if (repo / "packages" / "claude" / "commands" / "worklog.md").exists():
    raise SystemExit("Claude plugin must not ship commands/worklog.md; plugin commands are still namespaced skills.")
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
