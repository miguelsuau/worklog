#!/usr/bin/env python3
"""Worklog MCP server.

Worklog is a small local ledger for agent-assisted work:

1. Capture source events for a session.
2. Turn them into a user-reviewed session log.
3. Roll approved session logs into a project log.
4. Generate resume context from approved project state.

This file is intentionally self-contained and does not depend on the earlier
prototype implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_STORE = str(Path.home() / ".worklog" / "store")
MAX_EVENT_TEXT = 5000
SHARED_LAYOUT_VERSION = 1
SHARING_BACKENDS = {"shared_directory", "git_repo", "connector_payload"}
MOUNTED_STORAGE_PROVIDERS = ["google_drive", "dropbox", "onedrive", "network_folder", "local_folder", "docker_mount"]
CLOUD_SYNC_STORAGE_PROVIDERS = ["google_drive", "dropbox", "onedrive"]
GIT_STORAGE_PROVIDERS = ["github", "gitlab", "bitbucket", "git_repo"]

DEFAULT_SESSION_TEMPLATE = {
    "name": "Unconfigured Session Log",
    "description": "No user-approved session log template has been configured yet.",
    "sections": [],
}

DEFAULT_PROJECT_TEMPLATE = {
    "name": "Unconfigured Project Log",
    "description": "No user-approved project log template has been configured yet.",
    "sections": [],
}

TEMPLATE_AUTHORING_GUIDANCE = {
    "purpose": "The LLM proposes project-specific Worklog templates in conversation; Worklog stores only the user-approved result.",
    "required_template_fields": ["name", "description", "sections"],
    "required_section_fields": ["key", "title", "kind"],
    "optional_section_fields": ["description", "draft_from", "rollup_from"],
    "section_kinds": ["text", "list"],
    "draft_from_options": ["summary", "outcomes", "decisions", "questions", "next_actions", "notes", "validation"],
    "rollup_from_options": ["summary", "outcomes", "decisions", "questions", "next_actions", "notes", "validation", "rules"],
}

DEFAULT_PERMISSION_POLICY = {
    "contributors": [],
    "project_approvers": [],
    "maintainers": [],
    "session_log_approval": "own_session",
    "project_log_approval": "listed_approvers",
    "template_changes": "maintainers_only",
}


def main() -> int:
    server = Server()
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        reply = server.reply(request)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


class Server:
    def __init__(self) -> None:
        self.db = Store(os.environ.get("WORKLOG_STORE", DEFAULT_STORE))
        self.commands: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "worklog_status": self.status,
            "worklog_list_projects": self.list_projects,
            "worklog_start_project": self.start_project,
            "worklog_recommend_templates": self.recommend_templates,
            "worklog_set_project_templates": self.set_project_templates,
            "worklog_show_project_templates": self.show_project_templates,
            "worklog_configure_project_sharing": self.configure_project_sharing,
            "worklog_show_project_sharing": self.show_project_sharing,
            "worklog_update_project_members": self.update_project_members,
            "worklog_sync_project": self.sync_project,
            "worklog_capture_session": self.capture_session,
            "worklog_import_events": self.import_events,
            "worklog_add_event": self.add_event,
            "worklog_draft_session_log": self.draft_session_log,
            "worklog_show_session_log": self.show_session_log,
            "worklog_edit_session_log": self.edit_session_log,
            "worklog_approve_session_log": self.approve_session_log,
            "worklog_draft_project_log": self.draft_project_log,
            "worklog_show_project_log": self.show_project_log,
            "worklog_edit_project_log": self.edit_project_log,
            "worklog_approve_project_log": self.approve_project_log,
            "worklog_resume_context": self.resume_context,
        }

    def reply(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in request:
            return None
        request_id = request["id"]
        method = request.get("method")
        params = request.get("params") or {}
        try:
            if method == "initialize":
                self.db.ensure()
                return ok(request_id, self.initialize(params))
            if method == "tools/list":
                return ok(request_id, {"tools": schemas()})
            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name not in self.commands:
                    raise UserError(f"Unknown Worklog tool: {name}")
                return ok(request_id, command_payload(self.commands[name](arguments)))
            return fail(request_id, -32601, f"Unknown method: {method}")
        except UserError as exc:
            return ok(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Worklog error: {exc}"}],
                },
            )
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return ok(
                request_id,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                },
            )

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": params.get("protocolVersion", MCP_PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "worklog", "version": "0.1.0"},
            "instructions": (
                "Use Worklog for a simple reviewed work history. Source events are raw "
                "local material and should normally stay hidden. Session logs are the "
                "human-reviewed summary of a work session. Project logs are the approved "
                "living project state. The user chooses the session-log and project-log "
                "templates at project setup. Project-log rollups are LLM-authored from "
                "approved session logs and approved project state; Worklog stores the "
                "draft but does not mechanically decide where facts belong. When starting a project, ask about the "
                "nature of the work first, then have the LLM propose a structure; "
                "accept custom structures. Resume context is generated from an approved "
                "project log plus recent approved session logs. For review flows, show "
                "draft text to the user first and call approval tools only after explicit "
                "confirmation with confirmed_by_user=true and confirmation_quote set to "
                "the user's words. Shared projects keep drafts local and publish only "
                "approved session logs, approved project logs, and approved project "
                "settings. Everyone may approve their own session logs; project-log "
                "approval is controlled by the project's Worklog policy and by the "
                "shared backend's real permissions. If approved session logs are not "
                "incorporated into the latest project log, surface them as pending "
                "project-log updates whenever the project is checked or resumed."
            ),
        }

    def status(self, _args: dict[str, Any]) -> dict[str, Any]:
        counts = self.db.counts()
        return {
            "store": str(self.db.root),
            "counts": counts,
            "text": lines(
                "# Worklog Status",
                "",
                f"Store: `{self.db.root}`",
                "",
                "Artifacts:",
                *[f"- {name}: {count}" for name, count in counts.items()],
            ),
        }

    def list_projects(self, _args: dict[str, Any]) -> dict[str, Any]:
        projects = project_index(self.db)
        rendered = ["# Worklog Projects", ""]
        if not projects:
            rendered.append("- None")
        for project in projects:
            rendered.append(project_line(project))
        return {"projects": projects, "text": "\n".join(rendered)}

    def start_project(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_optional(args.get("project_id"))
        project_description = clean_optional(args.get("project_description"))
        project_nature = clean_optional(args.get("project_nature"))
        known_projects = project_index(self.db)
        matches = matching_projects(
            known_projects,
            " ".join(
                part
                for part in (project_id, project_description, project_nature)
                if part
            ),
        )
        if known_projects and args.get("confirmed_new_project") is not True:
            return {
                "project_id": project_id,
                "project_description": project_description,
                "project_nature": project_nature,
                "known_projects": known_projects,
                "possible_matches": matches,
                "next_required_action": "choose_existing_project_or_confirm_new_project",
                "text": render_existing_project_check(
                    project_id,
                    project_description,
                    project_nature,
                    known_projects,
                    matches,
                ),
            }
        brief = template_authoring_brief(project_nature, project_description)
        return {
            "project_id": project_id,
            "project_description": project_description,
            "project_nature": project_nature,
            "known_projects": known_projects,
            "possible_matches": matches,
            "template_authoring": brief,
            "next_required_action": "llm_propose_templates_then_ask_user_to_refine_or_approve",
            "text": render_project_start(project_id, project_description, project_nature, brief),
        }

    def recommend_templates(self, args: dict[str, Any]) -> dict[str, Any]:
        project_nature = clean_optional(args.get("project_nature"))
        project_description = clean_optional(args.get("project_description"))
        brief = template_authoring_brief(project_nature, project_description)
        return {
            "template_authoring": brief,
            "next_required_action": "llm_propose_templates_then_ask_user_to_refine_or_approve",
            "text": render_template_authoring_brief(brief),
        }

    def set_project_templates(self, args: dict[str, Any]) -> dict[str, Any]:
        require_confirmation(args, "set project log templates")
        project_id = clean_required(args, "project_id")
        project_nature = clean_optional(args.get("project_nature")) or "user-defined"
        if not isinstance(args.get("session_log_template"), dict):
            raise UserError("session_log_template is required and must be the user-approved template object.")
        if not isinstance(args.get("project_log_template"), dict):
            raise UserError("project_log_template is required and must be the user-approved template object.")
        session_template = normalize_template(
            args.get("session_log_template"),
            fallback=DEFAULT_SESSION_TEMPLATE,
        )
        project_template = normalize_template(
            args.get("project_log_template"),
            fallback=DEFAULT_PROJECT_TEMPLATE,
        )
        require_configured_template(session_template, "session_log_template")
        require_configured_template(project_template, "project_log_template")
        existing = self.db.project_settings(project_id)
        settings = {
            "id": project_id,
            "project_id": project_id,
            "project_nature": project_nature,
            "created_or_updated_at": now(),
            "session_log_template": session_template,
            "project_log_template": project_template,
            "confirmation_quote": str(args.get("confirmation_quote")),
        }
        preserve_project_settings_fields(settings, existing)
        self.db.write("project_settings", project_id, settings)
        if sharing_enabled(settings):
            try:
                sharing_backend(settings["sharing"]).publish_settings(project_id, settings, dry_run=False)
            except UserError:
                pass
        return {
            "project_templates": settings,
            "text": render_project_templates(settings),
        }

    def show_project_templates(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_required(args, "project_id")
        settings = self.db.project_settings(project_id)
        return {
            "project_templates": settings,
            "text": render_project_templates(settings),
        }

    def configure_project_sharing(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_required(args, "project_id")
        backend_name = clean_optional(args.get("backend")) or "shared_directory"
        if backend_name not in SHARING_BACKENDS:
            raise UserError(f"Unsupported sharing backend `{backend_name}`.")
        mode = clean_optional(args.get("mode")) or "create"
        if mode not in {"create", "join"}:
            raise UserError("mode must be `create` or `join`.")
        actor = actor_from_args(args)
        storage_provider = sharing_storage_provider_key(args.get("storage_provider"))
        effective_backend = backend_for_storage_provider(storage_provider, backend_name)
        if backend_requires_location(effective_backend):
            permissions = normalize_permission_policy(args.get("permissions"), actor=actor)
            if not storage_provider:
                guidance = shared_storage_provider_guidance(project_id, backend_name)
                shared_location = shared_location_from_args(args)
                if shared_location:
                    guidance["provided_shared_location"] = shared_location
                return {
                    "project_id": project_id,
                    "backend": backend_name,
                    "sharing_setup_stage": "choose_storage_provider",
                    "storage_provider": None,
                    "shared_location": shared_location,
                    "permissions": permissions,
                    "storage_provider_guidance": guidance,
                    "next_required_action": "ask_user_to_choose_storage_provider_then_call_worklog_configure_project_sharing",
                    "text": render_storage_provider_guidance(project_id, backend_name, guidance, permissions),
                }
            if not has_shared_location(args):
                provider_setup = setup_storage_provider(project_id, effective_backend, storage_provider, args)
                guidance = shared_location_guidance(project_id, effective_backend, storage_provider, provider_setup=provider_setup)
                return {
                    "project_id": project_id,
                    "backend": effective_backend,
                    "sharing_setup_stage": "choose_shared_location",
                    "storage_provider": storage_provider,
                    "storage_provider_setup": provider_setup,
                    "shared_location": {},
                    "permissions": permissions,
                    "sharing_location_guidance": guidance,
                    "next_required_action": "ask_user_to_choose_shared_location_then_confirm_sharing_setup",
                    "text": render_shared_location_guidance(project_id, effective_backend, storage_provider, guidance, permissions),
                }
            if storage_provider_requires_cloud_verification(storage_provider):
                provider_setup = setup_storage_provider(project_id, effective_backend, storage_provider, args)
                if not provider_setup.get("provider_connection_verified"):
                    shared_location = shared_location_from_args(args)
                    return {
                        "project_id": project_id,
                        "backend": effective_backend,
                        "sharing_setup_stage": "verify_storage_provider_connection",
                        "storage_provider": storage_provider,
                        "storage_provider_setup": provider_setup,
                        "shared_location": shared_location,
                        "permissions": permissions,
                        "next_required_action": "ask_user_to_approve_provider_connector_then_verify_cloud_sync_and_call_worklog_configure_project_sharing_with_storage_provider_verification",
                        "text": render_storage_provider_verification_required(
                            project_id,
                            effective_backend,
                            storage_provider,
                            shared_location,
                            provider_setup,
                            permissions,
                        ),
                    }
            permission_plan = backend_permission_plan(
                project_id,
                effective_backend,
                storage_provider,
                shared_location_from_args(args),
                permissions,
                actor=actor,
            )
            permission_verification = backend_permission_verification_evidence(args, storage_provider, permission_plan)
            if permission_plan["required"] and not permission_verification["backend_permissions_verified"]:
                return {
                    "project_id": project_id,
                    "backend": effective_backend,
                    "sharing_setup_stage": "apply_backend_permissions",
                    "storage_provider": storage_provider,
                    "shared_location": shared_location_from_args(args),
                    "permissions": permissions,
                    "backend_permission_plan": permission_plan,
                    "backend_permission_verification": permission_verification,
                    "next_required_action": "apply_backend_permissions_with_provider_tooling_or_user_manual_share_then_call_worklog_configure_project_sharing_with_backend_permission_verification",
                    "text": render_backend_permission_plan_required(
                        project_id,
                        effective_backend,
                        storage_provider,
                        shared_location_from_args(args),
                        permission_plan,
                        permission_verification,
                    ),
                }
        require_confirmation(args, "configure project sharing")
        existing = self.db.project_settings(project_id)
        sharing = normalize_sharing_config(args, project_id=project_id, actor=actor, backend=effective_backend, mode=mode)
        permissions = normalize_permission_policy(args.get("permissions"), actor=actor)
        permission_plan = backend_permission_plan(
            project_id,
            effective_backend,
            storage_provider,
            shared_location_from_args(args),
            permissions,
            actor=actor,
        )
        permission_verification = backend_permission_verification_evidence(args, storage_provider, permission_plan)
        settings = dict(existing)
        settings.update(
            {
                "id": project_id,
                "project_id": project_id,
                "project_nature": clean_optional(args.get("project_nature")) or existing.get("project_nature") or "user-defined",
                "sharing": sharing,
                "permissions": permissions,
                "backend_permissions": normalize_backend_permissions(
                    args,
                    existing.get("backend_permissions"),
                    permission_plan,
                    permission_verification,
                ),
                "sharing_configured_at": sharing["configured_at"],
                "sharing_confirmation_quote": str(args.get("confirmation_quote")),
            }
        )
        backend = sharing_backend(sharing)
        setup = backend.configure_project(project_id, settings, mode=mode, dry_run=bool(args.get("dry_run", False)))
        if not args.get("dry_run", False):
            self.db.write("project_settings", project_id, settings)
            if mode == "join":
                setup["pulled"] = backend.pull(self.db, project_id, dry_run=False).get("pulled", [])
        return {
            "project_sharing": settings,
            "setup": setup,
            "pending_project_updates": pending_session_logs(self.db, project_id),
            "text": render_project_sharing(settings, setup=setup, pending=pending_session_logs(self.db, project_id)),
        }

    def show_project_sharing(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_required(args, "project_id")
        settings = self.db.project_settings(project_id)
        pending = pending_session_logs(self.db, project_id)
        setup = None
        if sharing_enabled(settings):
            setup = sharing_backend(settings["sharing"]).status(project_id)
        return {
            "project_sharing": settings,
            "backend_status": setup,
            "pending_project_updates": pending,
            "text": render_project_sharing(settings, setup=setup, pending=pending),
        }

    def update_project_members(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_required(args, "project_id")
        actor = actor_from_args(args)
        operation = clean_optional(args.get("operation")) or "merge"
        if operation not in {"merge", "replace", "remove"}:
            raise UserError("operation must be `merge`, `replace`, or `remove`.")
        existing = self.db.project_settings(project_id)
        current_permissions = normalize_permission_policy(existing.get("permissions"))
        requested_members = member_role_lists_from_args(args)
        permissions = update_permission_policy(current_permissions, requested_members, operation=operation)
        sharing = existing.get("sharing") if isinstance(existing.get("sharing"), dict) else {}
        storage_provider = clean_optional(sharing.get("storage_provider"))
        backend_name = clean_optional(sharing.get("backend")) or "local"
        shared_location = shared_location_from_sharing(sharing)
        permission_plan = backend_permission_plan(
            project_id,
            backend_name,
            storage_provider,
            shared_location,
            permissions,
            actor=actor,
            current_permissions=current_permissions,
            operation=operation,
        )
        permission_verification = backend_permission_verification_evidence(args, storage_provider, permission_plan)
        if sharing_enabled(existing) and permission_plan["required"] and not permission_verification["backend_permissions_verified"]:
            return {
                "project_id": project_id,
                "member_setup_stage": "apply_backend_permissions",
                "operation": operation,
                "permissions": permissions,
                "current_permissions": current_permissions,
                "requested_members": requested_members,
                "backend_permission_plan": permission_plan,
                "backend_permission_verification": permission_verification,
                "next_required_action": "apply_backend_permissions_with_provider_tooling_or_user_manual_share_then_call_worklog_update_project_members_with_backend_permission_verification",
                "text": render_backend_permission_plan_required(
                    project_id,
                    backend_name,
                    storage_provider,
                    shared_location,
                    permission_plan,
                    permission_verification,
                ),
            }
        require_confirmation(args, "update project members")
        settings = dict(existing)
        settings["permissions"] = permissions
        settings["members_updated_at"] = now()
        settings["members_update_confirmation_quote"] = str(args.get("confirmation_quote"))
        settings["backend_permissions"] = normalize_backend_permissions(
            args,
            existing.get("backend_permissions"),
            permission_plan,
            permission_verification,
        )
        setup = None
        if sharing_enabled(settings):
            backend = sharing_backend(settings["sharing"])
            if not args.get("dry_run", False):
                self.db.write("project_settings", project_id, settings)
                backend.publish_settings(project_id, settings, dry_run=False)
            setup = backend.status(project_id)
        elif not args.get("dry_run", False):
            self.db.write("project_settings", project_id, settings)
        return {
            "project_members": settings,
            "permissions": permissions,
            "backend_permission_plan": permission_plan,
            "backend_permission_verification": permission_verification,
            "backend_status": setup,
            "text": render_project_members_update(project_id, operation, permissions, permission_plan, permission_verification),
        }

    def sync_project(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_required(args, "project_id")
        direction = clean_optional(args.get("direction")) or "both"
        if direction not in {"pull", "push", "both"}:
            raise UserError("direction must be `pull`, `push`, or `both`.")
        dry_run = bool(args.get("dry_run", False))
        settings = self.db.project_settings(project_id)
        if not sharing_enabled(settings):
            raise UserError("This project has no sharing configuration.")
        backend = sharing_backend(settings["sharing"])
        result = {
            "project_id": project_id,
            "direction": direction,
            "dry_run": dry_run,
            "pulled": [],
            "published": [],
            "conflicts": [],
            "backend_status": backend.status(project_id),
        }
        if direction in {"pull", "both"}:
            pulled = backend.pull(self.db, project_id, dry_run=dry_run)
            result["pulled"].extend(pulled.get("pulled", []))
            result["conflicts"].extend(pulled.get("conflicts", []))
        if direction in {"push", "both"}:
            published = backend.push(self.db, project_id, dry_run=dry_run)
            result["published"].extend(published.get("published", []))
            result["conflicts"].extend(published.get("conflicts", []))
        settings = self.db.project_settings(project_id)
        settings["sync_state"] = {
            "last_sync_at": now(),
            "last_sync_direction": direction,
            "last_sync_dry_run": dry_run,
            "last_sync_pulled": len(result["pulled"]),
            "last_sync_published": len(result["published"]),
            "last_sync_conflicts": len(result["conflicts"]),
        }
        if not dry_run:
            self.db.write("project_settings", project_id, settings)
        result["pending_project_updates"] = pending_session_logs(self.db, project_id)
        result["text"] = render_sync_result(result)
        return result

    def capture_session(self, args: dict[str, Any]) -> dict[str, Any]:
        source_path = find_session_file(args)
        if source_path is None:
            raise UserError("No Codex session file found. Pass session_path or import events.")
        project_id = clean_optional(args.get("project_id"))
        task_id = clean_optional(args.get("task_id"))
        session = session_from_codex_file(source_path, project_id=project_id, task_id=task_id)
        session = self.db.upsert_session(session)
        return {
            "session": public_session(session),
            "text": render_session_capture(session, "Captured session"),
        }

    def import_events(self, args: dict[str, Any]) -> dict[str, Any]:
        path = Path(required(args, "path")).expanduser()
        session = session_from_event_file(
            path,
            project_id=clean_optional(args.get("project_id")),
            task_id=clean_optional(args.get("task_id")),
            session_id=clean_optional(args.get("session_id")),
        )
        session = self.db.upsert_session(session)
        return {
            "session": public_session(session),
            "text": render_session_capture(session, "Imported events"),
        }

    def add_event(self, args: dict[str, Any]) -> dict[str, Any]:
        text = clean_required(args, "text")
        session_id = clean_optional(args.get("session_id")) or current_session_id() or uid("session")
        event = {
            "id": clean_optional(args.get("event_id")) or uid("event"),
            "at": clean_optional(args.get("at")) or now(),
            "speaker": clean_optional(args.get("speaker")) or "user",
            "kind": clean_optional(args.get("kind")) or "note",
            "text": squeeze(text, MAX_EVENT_TEXT),
            "meta": dict(args.get("meta") or {}),
        }
        session = self.db.append_event(
            session_id,
            event,
            project_id=clean_optional(args.get("project_id")),
            task_id=clean_optional(args.get("task_id")),
        )
        return {
            "session": public_session(session),
            "event": event,
            "text": render_session_capture(session, "Added event"),
        }

    def draft_session_log(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = clean_optional(args.get("session_id"))
        if not session_id and args.get("capture", True):
            try:
                captured = self.capture_session(args)
                session_id = captured["session"]["id"]
            except UserError:
                pass
        if not session_id:
            latest = self.db.latest_session()
            if latest is None:
                raise UserError("No session is available. Capture or import events first.")
            session_id = latest["id"]
        session = self.db.read("sessions", session_id)
        if clean_optional(args.get("project_id")):
            session["project_id"] = clean_optional(args.get("project_id"))
            self.db.write("sessions", session["id"], session)
        templates = self.db.project_settings(session.get("project_id"))
        require_configured_template(templates["session_log_template"], "session_log_template")
        draft = make_session_log(session, templates["session_log_template"])
        self.db.write("session_logs", draft["id"], draft)
        return {"session_log": draft, "text": render_session_log(draft)}

    def show_session_log(self, args: dict[str, Any]) -> dict[str, Any]:
        log = self.db.read("session_logs", required(args, "session_log_id"))
        return {"session_log": log, "text": render_session_log(log)}

    def edit_session_log(self, args: dict[str, Any]) -> dict[str, Any]:
        log = self.db.read("session_logs", required(args, "session_log_id"))
        if log["status"] == "approved":
            raise UserError("Approved session logs are final. Draft a new one for further changes.")
        patch = dict(args.get("fields") or {})
        if isinstance(args.get("sections"), dict):
            patch["sections"] = args["sections"]
        if clean_optional(args.get("section")):
            patch.setdefault("sections", {})[str(args["section"])] = args.get("items", args.get("text", ""))
        for name in SESSION_LOG_EDITABLE:
            if name in args:
                patch[name] = args[name]
        if not patch:
            raise UserError("No session log fields were provided.")
        apply_session_patch(log, patch)
        log["updated_at"] = now()
        self.db.write("session_logs", log["id"], log)
        return {"session_log": log, "text": render_session_log(log)}

    def approve_session_log(self, args: dict[str, Any]) -> dict[str, Any]:
        require_confirmation(args, "approve session log")
        log = self.db.read("session_logs", required(args, "session_log_id"))
        if not log.get("project_id"):
            raise UserError("Add project_id before approving this session log.")
        author = clean_optional(args.get("author")) or clean_optional(args.get("approved_by"))
        if author:
            log["author"] = author
        log["status"] = "approved"
        log["approved_at"] = now()
        log["approved_by"] = clean_optional(args.get("approved_by")) or "local-user"
        log["approval_scope"] = "own_session"
        log["confirmation_quote"] = str(args.get("confirmation_quote"))
        log["updated_at"] = log["approved_at"]
        self.db.write("session_logs", log["id"], log)
        publish = publish_after_approval(self.db, log["project_id"])
        pending = pending_session_logs(self.db, log["project_id"])
        return {
            "session_log": log,
            "publish": publish,
            "pending_project_updates": pending,
            "next": "worklog_draft_project_log",
            "text": render_session_log(log)
            + "\n\n"
            + render_after_session_approval(log, publish, pending),
        }

    def draft_project_log(self, args: dict[str, Any]) -> dict[str, Any]:
        session_logs = source_session_logs_from_args(self.db, args)
        project_id = clean_optional(args.get("project_id")) or (
            session_logs[0].get("project_id") if session_logs else None
        )
        if not project_id:
            raise UserError("Provide project_id or session_log_id.")
        if not session_logs:
            session_logs = pending_session_logs(self.db, project_id)
        templates = self.db.project_settings(project_id)
        require_configured_template(templates["project_log_template"], "project_log_template")
        previous = self.db.latest_project_log(project_id, status="approved")
        draft = make_project_log(
            project_id,
            previous=previous,
            template=templates["project_log_template"],
        )
        patch = project_log_patch_from_args(args)
        if not patch:
            return {
                "project_id": project_id,
                "previous_project_log": previous,
                "session_log": session_logs[0] if len(session_logs) == 1 else None,
                "session_logs": session_logs,
                "project_log_template": templates["project_log_template"],
                "draft_seed": draft,
                "next_required_action": "llm_author_project_log_draft_then_call_worklog_draft_project_log_with_sections",
                "text": render_project_rollup_authoring(
                    project_id,
                    previous,
                    session_logs,
                    templates["project_log_template"],
                ),
            }
        apply_project_patch(draft, patch)
        draft["rollup_mode"] = "llm_authored"
        if session_logs:
            source_ids = [log["id"] for log in session_logs]
            draft["rollup_source_session_log_ids"] = source_ids
            draft["session_log_ids"] = dedupe([*draft["session_log_ids"], *source_ids])
        draft["updated_at"] = now()
        self.db.write("project_logs", draft["id"], draft)
        return {"project_log": draft, "text": render_project_log(draft)}

    def show_project_log(self, args: dict[str, Any]) -> dict[str, Any]:
        project_log_id = clean_optional(args.get("project_log_id"))
        if project_log_id:
            log = self.db.read("project_logs", project_log_id)
        else:
            project_id = clean_required(args, "project_id")
            status = clean_optional(args.get("status")) or "approved"
            log = self.db.latest_project_log(project_id, status=None if status == "any" else status)
            if log is None:
                raise UserError(f"No {status} project log found for `{project_id}`.")
        pending = pending_session_logs(self.db, log["project_id"])
        return {
            "project_log": log,
            "pending_project_updates": pending,
            "text": render_project_log(log) + "\n\n" + render_pending_project_updates(pending),
        }

    def edit_project_log(self, args: dict[str, Any]) -> dict[str, Any]:
        require_confirmation(args, "edit project log")
        log = self.db.read("project_logs", required(args, "project_log_id"))
        if log["status"] == "approved":
            raise UserError("Approved project logs are final. Draft a new one for further changes.")
        patch = project_log_patch_from_args(args)
        if not patch:
            raise UserError("No project log fields were provided.")
        apply_project_patch(log, patch)
        log["updated_at"] = now()
        self.db.write("project_logs", log["id"], log)
        return {"project_log": log, "text": render_project_log(log)}

    def approve_project_log(self, args: dict[str, Any]) -> dict[str, Any]:
        require_confirmation(args, "approve project log")
        log = self.db.read("project_logs", required(args, "project_log_id"))
        approver = clean_optional(args.get("approved_by")) or "local-user"
        settings = self.db.project_settings(log["project_id"])
        require_project_log_approver(settings, approver)
        previous = self.db.latest_project_log(log["project_id"], status="approved")
        if previous and previous["id"] != log["id"] and log.get("supersedes") != previous["id"]:
            raise UserError(
                "A newer approved project log exists. Sync, review the pending session logs, "
                "and draft a new project log from the latest approved base."
            )
        approved_at = now()
        if previous and previous["id"] != log["id"]:
            previous["status"] = "superseded"
            previous["superseded_by"] = log["id"]
            previous["updated_at"] = approved_at
            self.db.write("project_logs", previous["id"], previous)
        log["status"] = "approved"
        log["approved_at"] = approved_at
        log["approved_by"] = approver
        log["confirmation_quote"] = str(args.get("confirmation_quote"))
        log["updated_at"] = approved_at
        self.db.write("project_logs", log["id"], log)
        publish = publish_after_approval(self.db, log["project_id"])
        pending = pending_session_logs(self.db, log["project_id"])
        return {
            "project_log": log,
            "publish": publish,
            "pending_project_updates": pending,
            "text": render_project_log(log)
            + "\n\n"
            + render_sync_notice(publish)
            + "\n\n"
            + render_pending_project_updates(pending),
        }

    def resume_context(self, args: dict[str, Any]) -> dict[str, Any]:
        project_id = clean_optional(args.get("project_id"))
        if not project_id:
            projects = project_index(self.db)
            if len(projects) != 1:
                raise UserError("Provide project_id or call worklog_list_projects first.")
            project_id = projects[0]["project_id"]
        project_log = self.db.latest_project_log(project_id, status="approved")
        recent = self.db.session_logs(project_id, status="approved")[: int(args.get("recent", 3))]
        pending = pending_session_logs(self.db, project_id)
        context = {
            "id": uid("resume"),
            "project_id": project_id,
            "created_at": now(),
            "project_log_id": project_log["id"] if project_log else None,
            "session_log_ids": [log["id"] for log in recent],
            "pending_project_update_session_log_ids": [log["id"] for log in pending],
            "text": render_resume(project_id, project_log, recent, pending),
        }
        if args.get("save", True):
            self.db.write("resume_contexts", context["id"], context)
        return {"resume_context": context, "text": context["text"]}


class Store:
    folders = ("project_settings", "sessions", "session_logs", "project_logs", "resume_contexts")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in self.folders:
            (self.root / folder).mkdir(exist_ok=True)
        manifest = self.root / "manifest.json"
        if not manifest.exists():
            self._write(manifest, {"product": "worklog", "schema": 1, "created_at": now()})

    def counts(self) -> dict[str, int]:
        self.ensure()
        return {folder: len(list((self.root / folder).glob("*.json"))) for folder in self.folders}

    def read(self, folder: str, item_id: str) -> dict[str, Any]:
        path = self.path(folder, item_id)
        if not path.exists():
            raise UserError(f"No {folder[:-1]} found for id `{item_id}`.")
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, folder: str, item_id: str, value: dict[str, Any]) -> None:
        self.ensure()
        self._write(self.path(folder, item_id), value)

    def list(self, folder: str) -> list[dict[str, Any]]:
        self.ensure()
        items: list[dict[str, Any]] = []
        for path in sorted((self.root / folder).glob("*.json")):
            with path.open("r", encoding="utf-8") as handle:
                items.append(json.load(handle))
        return items

    def upsert_session(self, incoming: dict[str, Any]) -> dict[str, Any]:
        existing = None
        path = self.path("sessions", incoming["id"])
        if path.exists():
            existing = self.read("sessions", incoming["id"])
        merged = merge_sessions(existing, incoming)
        self.write("sessions", merged["id"], merged)
        return merged

    def append_event(
        self,
        session_id: str,
        event: dict[str, Any],
        *,
        project_id: str | None,
        task_id: str | None,
    ) -> dict[str, Any]:
        if self.path("sessions", session_id).exists():
            session = self.read("sessions", session_id)
        else:
            session = empty_session(session_id, source="manual", project_id=project_id, task_id=task_id)
        session["events"].append(event)
        session["events"] = sorted(unique_by_id(session["events"]), key=lambda item: (item["at"], item["id"]))
        session["project_id"] = project_id or session.get("project_id")
        session["task_id"] = task_id or session.get("task_id")
        session["started_at"] = session["events"][0]["at"]
        session["ended_at"] = session["events"][-1]["at"]
        session["updated_at"] = now()
        self.write("sessions", session_id, session)
        return session

    def latest_session(self) -> dict[str, Any] | None:
        sessions = self.list("sessions")
        if not sessions:
            return None
        return max(sessions, key=lambda item: item.get("ended_at") or item.get("updated_at") or "")

    def latest_project_log(self, project_id: str, *, status: str | None) -> dict[str, Any] | None:
        logs = [
            log
            for log in self.list("project_logs")
            if log.get("project_id") == project_id and (status is None or log.get("status") == status)
        ]
        if not logs:
            return None
        return max(logs, key=lambda item: (int(item.get("version", 0)), item.get("updated_at", "")))

    def project_settings(self, project_id: str | None) -> dict[str, Any]:
        if not project_id:
            return default_project_settings(None)
        path = self.path("project_settings", project_id)
        if not path.exists():
            return default_project_settings(project_id)
        settings = self.read("project_settings", project_id)
        settings["session_log_template"] = normalize_template(
            settings.get("session_log_template"),
            fallback=DEFAULT_SESSION_TEMPLATE,
        )
        settings["project_log_template"] = normalize_template(
            settings.get("project_log_template"),
            fallback=DEFAULT_PROJECT_TEMPLATE,
        )
        return settings

    def session_logs(self, project_id: str, *, status: str | None) -> list[dict[str, Any]]:
        logs = [
            log
            for log in self.list("session_logs")
            if log.get("project_id") == project_id and (status is None or log.get("status") == status)
        ]
        logs.sort(key=lambda item: item.get("approved_at") or item.get("updated_at") or "", reverse=True)
        return logs

    def path(self, folder: str, item_id: str) -> Path:
        if folder not in self.folders:
            raise UserError(f"Unknown Worklog folder `{folder}`.")
        return self.root / folder / f"{file_token(item_id)}.json"

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".writing")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temp.replace(path)


class SharingBackend:
    name = "backend"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def configure_project(
        self,
        project_id: str,
        settings: dict[str, Any],
        *,
        mode: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        raise UserError(f"Sharing backend `{self.name}` cannot configure projects.")

    def status(self, project_id: str) -> dict[str, Any]:
        return {"backend": self.name, "project_id": project_id}

    def pull(self, db: Store, project_id: str, *, dry_run: bool) -> dict[str, Any]:
        return {"pulled": [], "conflicts": []}

    def push(self, db: Store, project_id: str, *, dry_run: bool) -> dict[str, Any]:
        return {"published": [], "conflicts": []}

    def publish_settings(self, project_id: str, settings: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        return {"published": [], "conflicts": []}


class FileSharingBackend(SharingBackend):
    name = "shared_directory"

    def project_dir(self, project_id: str) -> Path:
        explicit = clean_optional(self.config.get("shared_project_dir"))
        if explicit:
            return Path(explicit).expanduser()
        root = clean_optional(self.config.get("root"))
        if not root:
            raise UserError(f"Sharing backend `{self.name}` requires a root path or shared_project_dir.")
        return Path(root).expanduser() / ".worklog" / "projects" / file_token(project_id)

    def configure_project(
        self,
        project_id: str,
        settings: dict[str, Any],
        *,
        mode: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        result = {
            "backend": self.name,
            "mode": mode,
            "shared_project_dir": str(project_dir),
            "created": [],
            "verified": [],
            **storage_provider_verification(self.config),
        }
        if mode == "join" and not project_dir.exists():
            raise UserError(f"Cannot join shared project; `{project_dir}` does not exist.")
        if dry_run:
            result["dry_run"] = True
            return result
        ensure_shared_project_dirs(project_dir)
        write_json_if_changed(project_dir / "project.json", shared_project_manifest(project_id, settings))
        self.publish_settings(project_id, settings, dry_run=False)
        result["verified"].append("shared project directory is readable and writable")
        return result

    def status(self, project_id: str) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        status = {
            "backend": self.name,
            "shared_project_dir": str(project_dir),
            "exists": project_dir.exists(),
            "readable": os.access(project_dir, os.R_OK) if project_dir.exists() else False,
            "writable": os.access(project_dir, os.W_OK) if project_dir.exists() else False,
            **storage_provider_verification(self.config),
        }
        return status

    def pull(self, db: Store, project_id: str, *, dry_run: bool) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        if not project_dir.exists():
            raise UserError(f"Shared project directory does not exist: `{project_dir}`.")
        pulled = []
        conflicts = []
        remote_settings = read_json_optional(project_dir / "templates.json")
        remote_permissions = read_json_optional(project_dir / "permissions.json")
        if remote_settings or remote_permissions:
            local = db.project_settings(project_id)
            merged = dict(local)
            if remote_settings:
                for key in ("project_nature", "session_log_template", "project_log_template"):
                    if key in remote_settings:
                        merged[key] = remote_settings[key]
            if remote_permissions:
                merged["permissions"] = normalize_permission_policy(remote_permissions)
            preserve_project_settings_fields(merged, local)
            if not dry_run:
                db.write("project_settings", project_id, merged)
            pulled.append({"type": "project_settings", "id": project_id})
        for folder, artifact_type in (
            ("session_logs", "session_log"),
            ("project_logs", "project_log"),
        ):
            for path in sorted((project_dir / "approved" / folder).glob("*.json")):
                artifact = read_json_optional(path)
                if not artifact:
                    continue
                artifact_id = str(artifact.get("id") or path.stem)
                local_path = db.path(folder, artifact_id)
                if local_path.exists():
                    local = db.read(folder, artifact_id)
                    if json_fingerprint(local) != json_fingerprint(artifact):
                        conflicts.append({"type": artifact_type, "id": artifact_id, "reason": "local artifact differs from shared artifact"})
                    continue
                if not dry_run:
                    db.write(folder, artifact_id, artifact)
                pulled.append({"type": artifact_type, "id": artifact_id, "path": str(path)})
        return {"pulled": pulled, "conflicts": conflicts}

    def push(self, db: Store, project_id: str, *, dry_run: bool) -> dict[str, Any]:
        settings = db.project_settings(project_id)
        project_dir = self.project_dir(project_id)
        published = []
        conflicts = []
        if not dry_run:
            ensure_shared_project_dirs(project_dir)
            write_json_if_changed(project_dir / "project.json", shared_project_manifest(project_id, settings))
        settings_result = self.publish_settings(project_id, settings, dry_run=dry_run)
        published.extend(settings_result.get("published", []))
        conflicts.extend(settings_result.get("conflicts", []))
        for log in db.session_logs(project_id, status="approved"):
            target = project_dir / "approved" / "session_logs" / f"{file_token(log['id'])}.json"
            outcome = publish_artifact(target, log, dry_run=dry_run)
            append_publish_result(published, conflicts, outcome, "session_log", log["id"])
        for log in approved_project_logs(db, project_id):
            if not project_log_approval_valid(settings, clean_optional(log.get("approved_by")) or ""):
                conflicts.append({"type": "project_log", "id": log["id"], "reason": "approved_by is not a project approver"})
                continue
            target = project_dir / "approved" / "project_logs" / f"{int(log.get('version', 0)):04d}_{file_token(log['id'])}.json"
            outcome = publish_artifact(target, log, dry_run=dry_run)
            append_publish_result(published, conflicts, outcome, "project_log", log["id"])
        latest = db.latest_project_log(project_id, status="approved")
        if latest:
            outcome = publish_artifact(project_dir / "current_project_log.json", latest, dry_run=dry_run, replace=True)
            append_publish_result(published, conflicts, outcome, "current_project_log", latest["id"])
        index = shared_project_index(db, project_id)
        outcome = publish_artifact(project_dir / "index.json", index, dry_run=dry_run, replace=True)
        append_publish_result(published, conflicts, outcome, "index", project_id)
        return {"published": published, "conflicts": conflicts}

    def publish_settings(self, project_id: str, settings: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        project_dir = self.project_dir(project_id)
        published = []
        conflicts = []
        template_payload = {
            "project_id": project_id,
            "project_nature": settings.get("project_nature"),
            "session_log_template": settings.get("session_log_template"),
            "project_log_template": settings.get("project_log_template"),
            "updated_at": now(),
        }
        permissions = normalize_permission_policy(settings.get("permissions"))
        for artifact_type, target, payload in (
            ("templates", project_dir / "templates.json", template_payload),
            ("permissions", project_dir / "permissions.json", permissions),
        ):
            outcome = publish_artifact(target, payload, dry_run=dry_run, replace=True)
            append_publish_result(published, conflicts, outcome, artifact_type, project_id)
        return {"published": published, "conflicts": conflicts}


class GitRepoBackend(FileSharingBackend):
    name = "git_repo"

    def status(self, project_id: str) -> dict[str, Any]:
        status = super().status(project_id)
        root = clean_optional(self.config.get("root"))
        repo_root = Path(root).expanduser() if root else self.project_dir(project_id)
        status["git_repo_detected"] = (repo_root / ".git").exists()
        status["git_repo_root"] = str(repo_root)
        if not status["git_repo_detected"]:
            status["attention"] = "No .git directory detected at the configured root; Worklog can write files but the agent should verify Git setup."
        return status


class ConnectorPayloadBackend(SharingBackend):
    name = "connector_payload"

    def configure_project(
        self,
        project_id: str,
        settings: dict[str, Any],
        *,
        mode: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        return {
            "backend": self.name,
            "mode": mode,
            "project_id": project_id,
            "attention": "Connector-backed sharing is configured as an agent-managed payload. The agent must use the selected connector to create folders/files and store the resulting verification in backend_permissions.",
            "dry_run": dry_run,
        }

    def push(self, db: Store, project_id: str, *, dry_run: bool) -> dict[str, Any]:
        settings = db.project_settings(project_id)
        artifacts = [
            {"type": "project_settings", "id": project_id, "payload": shared_project_manifest(project_id, settings)},
            {"type": "templates", "id": project_id, "payload": settings_payload(settings)},
            {"type": "permissions", "id": project_id, "payload": normalize_permission_policy(settings.get("permissions"))},
        ]
        artifacts.extend({"type": "session_log", "id": log["id"], "payload": log} for log in db.session_logs(project_id, status="approved"))
        artifacts.extend({"type": "project_log", "id": log["id"], "payload": log} for log in approved_project_logs(db, project_id))
        return {
            "published": [],
            "conflicts": [],
            "connector_payloads": artifacts,
            "attention": "Use the selected connector to publish these approved artifacts. Drafts are intentionally excluded.",
        }


def sharing_backend(config: dict[str, Any]) -> SharingBackend:
    backend = clean_optional(config.get("backend")) or "shared_directory"
    if backend == "shared_directory":
        return FileSharingBackend(config)
    if backend == "git_repo":
        return GitRepoBackend(config)
    if backend == "connector_payload":
        return ConnectorPayloadBackend(config)
    raise UserError(f"Unsupported sharing backend `{backend}`.")


def normalize_sharing_config(
    args: dict[str, Any],
    *,
    project_id: str,
    actor: str,
    backend: str,
    mode: str,
) -> dict[str, Any]:
    storage_provider = sharing_storage_provider_key(args.get("storage_provider"))
    backend = backend_for_storage_provider(storage_provider, backend)
    config: dict[str, Any] = {}
    for source_key, target_key in (
        ("root", "root"),
        ("shared_project_dir", "shared_project_dir"),
        ("connector", "connector"),
        ("connector_target", "connector_target"),
    ):
        if clean_optional(args.get(source_key)):
            config[target_key] = clean_optional(args.get(source_key))
    config.update(
        {
            "enabled": True,
            "backend": backend,
            "storage_provider": storage_provider,
            "storage_provider_setup": setup_storage_provider(
                project_id,
                backend,
                storage_provider,
                args,
            ),
            "mode": mode,
            "project_id": project_id,
            "publish_policy": clean_optional(args.get("publish_policy")) or "approved_only",
            "configured_at": now(),
            "configured_by": actor,
        }
    )
    if backend in {"shared_directory", "git_repo"}:
        if not clean_optional(config.get("storage_provider")):
            raise UserError(f"Sharing backend `{backend}` requires explicit `storage_provider`.")
        if not clean_optional(config.get("root")) and not clean_optional(config.get("shared_project_dir")):
            raise UserError(f"Sharing backend `{backend}` requires `root` or `shared_project_dir`.")
    return config


def backend_requires_location(backend: str) -> bool:
    return backend in {"shared_directory", "git_repo"}


def backend_for_storage_provider(storage_provider: str | None, requested_backend: str) -> str:
    if storage_provider in GIT_STORAGE_PROVIDERS:
        return "git_repo"
    if storage_provider in MOUNTED_STORAGE_PROVIDERS:
        return "shared_directory"
    return requested_backend


def storage_provider_requires_cloud_verification(storage_provider: str | None) -> bool:
    return storage_provider in CLOUD_SYNC_STORAGE_PROVIDERS


def has_shared_location(args: dict[str, Any]) -> bool:
    for key in ("root", "shared_project_dir"):
        if clean_optional(args.get(key)):
            return True
    return False


def shared_location_from_args(args: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key in ("root", "shared_project_dir"):
        value = clean_optional(args.get(key))
        if value:
            result[key] = value
    return result


def sharing_storage_provider_key(value: Any) -> str | None:
    storage_provider = clean_optional(value)
    if not storage_provider:
        return None
    key = section_key(storage_provider)
    aliases = {
        "gdrive": "google_drive",
        "google": "google_drive",
        "google_drive": "google_drive",
        "drive": "google_drive",
        "dropbox": "dropbox",
        "one_drive": "onedrive",
        "onedrive": "onedrive",
        "sharepoint": "onedrive",
        "network": "network_folder",
        "network_share": "network_folder",
        "network_folder": "network_folder",
        "local": "local_folder",
        "local_folder": "local_folder",
        "git": "git_repo",
        "github": "github",
        "github_repo": "github",
        "gitlab": "gitlab",
        "git_lab": "gitlab",
        "gitlab_repo": "gitlab",
        "bitbucket": "bitbucket",
        "bit_bucket": "bitbucket",
        "bitbucket_repo": "bitbucket",
        "git_repo": "git_repo",
        "docker": "docker_mount",
        "docker_volume": "docker_mount",
        "docker_mount": "docker_mount",
    }
    return aliases.get(key, key)


def storage_provider_verification(config: dict[str, Any]) -> dict[str, Any]:
    provider = clean_optional(config.get("storage_provider"))
    setup = config.get("storage_provider_setup") if isinstance(config.get("storage_provider_setup"), dict) else {}
    if provider in MOUNTED_STORAGE_PROVIDERS:
        verification_scope = setup.get("verification_scope") or "local_filesystem_mount"
        provider_connection_verified = bool(setup.get("provider_connection_verified", False))
        if storage_provider_requires_cloud_verification(provider) and verification_scope in {
            "local_filesystem_mount",
            "local_filesystem_mount_only",
        }:
            provider_connection_verified = False
        return {
            "storage_provider": provider,
            "storage_provider_setup_status": setup.get("setup_status") or (
                "needs_cloud_verification" if storage_provider_requires_cloud_verification(provider) else "unknown"
            ),
            "verification_scope": verification_scope,
            "provider_connection_verified": provider_connection_verified,
            "cloud_sync_verification_required": storage_provider_requires_cloud_verification(provider),
            "provider_connection_note": (
                setup.get("provider_connection_note")
                or (
                    "Worklog verified only local filesystem access to the mounted folder. It has not "
                    "verified provider authentication or cloud sync."
                    if storage_provider_requires_cloud_verification(provider)
                    else "Worklog configured the shared_directory backend and verified local filesystem "
                    "access to the mounted folder."
                )
            ),
        }
    if provider in GIT_STORAGE_PROVIDERS:
        return {
            "storage_provider": provider,
            "storage_provider_setup_status": setup.get("setup_status") or "unknown",
            "verification_scope": setup.get("verification_scope") or "local_git_worktree",
            "provider_connection_verified": bool(setup.get("provider_connection_verified", False)),
            "provider_connection_note": (
                setup.get("provider_connection_note")
                or "Worklog configured the git_repo backend locally. It did not authenticate with "
                "the Git provider or verify remote push/pull access."
            ),
        }
    return {
        "storage_provider": provider,
        "verification_scope": "worklog_backend",
        "provider_connection_verified": False,
        "provider_connection_note": "Worklog verified only its own backend setup.",
    }


def setup_storage_provider(
    project_id: str,
    backend: str,
    storage_provider: str | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    if not storage_provider:
        return {
            "setup_status": "missing_storage_provider",
            "provider_connection_verified": False,
            "suggested_roots": [],
        }
    if storage_provider in MOUNTED_STORAGE_PROVIDERS:
        return setup_mounted_storage_provider(project_id, backend, storage_provider, args)
    if storage_provider in GIT_STORAGE_PROVIDERS:
        return setup_git_storage_provider(project_id, backend, storage_provider, args)
    return {
        "storage_provider": storage_provider,
        "provider_backend": "custom",
        "setup_status": "needs_external_setup",
        "verification_scope": "custom_provider",
        "provider_connection_verified": False,
        "provider_connection_note": "Worklog does not know how to configure this storage provider automatically.",
        "suggested_roots": ["<shared-root>/Worklog"],
    }


def setup_mounted_storage_provider(
    project_id: str,
    backend: str,
    storage_provider: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    roots = mounted_provider_roots(storage_provider)
    suggested_roots = mounted_provider_suggested_roots(storage_provider, roots)
    writable_roots = [path for path in suggested_roots if local_path_ready(path)]
    local_mount_ready = bool(roots)
    cloud_sync_required = storage_provider_requires_cloud_verification(storage_provider)
    connection_evidence = storage_provider_connection_evidence(args, storage_provider)
    provider_connection_verified = bool(connection_evidence.get("provider_connection_verified"))
    provider_names = {
        "google_drive": "Google Drive",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive/SharePoint",
        "network_folder": "Network folder",
        "local_folder": "Local folder",
        "docker_mount": "Docker mount",
    }
    provider_name = provider_names.get(storage_provider, titleize(storage_provider))
    if cloud_sync_required and provider_connection_verified:
        note = (
            connection_evidence.get("provider_connection_note")
            or f"Worklog received {provider_name} connector verification evidence from the agent."
        )
        status = "ready"
        verification_scope = clean_optional(connection_evidence.get("verification_scope")) or f"{storage_provider}_connector"
    elif cloud_sync_required and local_mount_ready:
        note = (
            f"Worklog found a local {provider_name} sync/mount location, but that only verifies the "
            "local filesystem surface. The agent must ask the user to approve the provider connector, "
            "verify the cloud-side Worklog location, and call this tool again with `storage_provider_verification`."
        )
        status = "needs_cloud_verification"
        verification_scope = "local_filesystem_mount_only"
    elif local_mount_ready:
        note = (
            f"Worklog found a local {provider_name} sync/mount location. This verifies the local "
            "mounted folder surface, not provider OAuth or cloud propagation."
        )
        status = "ready"
        verification_scope = "local_filesystem_mount"
        provider_connection_verified = True
    else:
        note = (
            f"Worklog did not find a local {provider_name} sync/mount location. The agent should "
            "set up the provider connector or ask the user to connect the desktop sync/mount before choosing a path."
        )
        status = "needs_provider_connection"
        verification_scope = "local_filesystem_mount"
    return {
        "storage_provider": storage_provider,
        "provider_backend": "mounted_folder",
        "setup_status": status,
        "backend": backend,
        "verification_scope": verification_scope,
        "provider_connection_verified": provider_connection_verified,
        "local_mount_ready": local_mount_ready,
        "cloud_sync_verification_required": cloud_sync_required,
        "provider_connection_note": note,
        "storage_provider_verification": connection_evidence,
        "mount_roots": [str(path) for path in roots],
        "suggested_roots": suggested_roots,
        "writable_suggested_roots": writable_roots,
        "resulting_project_dir_examples": [
            resulting_project_dir_example(project_id, root)
            for root in suggested_roots[:3]
        ],
        "connector_hint": mounted_provider_connector_hint(storage_provider),
    }


def storage_provider_connection_evidence(args: dict[str, Any], storage_provider: str) -> dict[str, Any]:
    raw = args.get("storage_provider_verification")
    if raw is None:
        raw = args.get("provider_verification")
    if not isinstance(raw, dict):
        return {}
    evidence_provider = sharing_storage_provider_key(raw.get("storage_provider"))
    if evidence_provider and evidence_provider != storage_provider:
        return {
            "provider_connection_verified": False,
            "provider_connection_note": (
                f"Ignored verification for `{evidence_provider}` because the selected provider is `{storage_provider}`."
            ),
        }
    verified = bool(
        raw.get("provider_connection_verified")
        or raw.get("cloud_sync_verified")
        or raw.get("cloud_upload_verified")
        or raw.get("verified")
    )
    result = dict(raw)
    result["storage_provider"] = storage_provider
    result["provider_connection_verified"] = verified
    if not clean_optional(result.get("verification_scope")):
        result["verification_scope"] = f"{storage_provider}_connector"
    if not clean_optional(result.get("verified_at")) and verified:
        result["verified_at"] = now()
    return result


def setup_git_storage_provider(
    project_id: str,
    backend: str,
    storage_provider: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    candidate_root = git_root_candidate(args)
    repo_ready = (candidate_root / ".git").exists()
    explicit_location = bool(clean_optional(args.get("root")) or clean_optional(args.get("shared_project_dir")))
    suggested_roots = [str(candidate_root)] if repo_ready or explicit_location else ["<repo-root>"]
    provider_name = git_provider_display_name(storage_provider)
    return {
        "storage_provider": storage_provider,
        "provider_backend": "local_git_worktree",
        "setup_status": "ready" if repo_ready else "needs_git_worktree",
        "backend": "git_repo",
        "verification_scope": "local_git_worktree",
        "provider_connection_verified": repo_ready,
        "provider_connection_note": (
            f"Worklog found a local Git worktree. It did not verify {provider_name} remote push/pull access."
            if repo_ready
            else f"Worklog needs a local Git worktree before it can configure a {provider_name}-backed shared project."
        ),
        "suggested_roots": suggested_roots,
        "resulting_project_dir_examples": [
            resulting_project_dir_example(project_id, root)
            for root in suggested_roots[:3]
        ],
        "connector_hint": f"Use {provider_name} tooling to create/verify the remote repository and collaborator permissions when needed.",
    }


def git_root_candidate(args: dict[str, Any]) -> Path:
    root = clean_optional(args.get("root"))
    if root:
        return Path(root).expanduser()
    shared_project_dir = clean_optional(args.get("shared_project_dir"))
    if shared_project_dir:
        shared_path = Path(shared_project_dir).expanduser()
        return nearest_git_root(shared_path) or shared_path
    return nearest_git_root(Path.cwd()) or Path.cwd()


def nearest_git_root(start: Path) -> Path | None:
    for path in (start, *start.parents):
        if (path / ".git").exists():
            return path
    return None


def git_provider_display_name(storage_provider: str) -> str:
    names = {
        "github": "GitHub",
        "gitlab": "GitLab",
        "bitbucket": "Bitbucket",
        "git_repo": "Git",
    }
    return names.get(storage_provider, "Git")


def storage_provider_display_name(storage_provider: str) -> str:
    names = {
        "google_drive": "Google Drive",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive/SharePoint",
        "network_folder": "Network Folder",
        "local_folder": "Local Folder",
        "docker_mount": "Docker Mount",
        "selected_provider": "the selected provider",
    }
    if storage_provider in GIT_STORAGE_PROVIDERS:
        return git_provider_display_name(storage_provider)
    return names.get(storage_provider, titleize(storage_provider))


def mounted_provider_roots(storage_provider: str) -> list[Path]:
    home = Path.home()
    if storage_provider == "google_drive":
        return existing_cloud_storage_roots("GoogleDrive-*") + existing_paths(home / "Google Drive", home / "google_drive")
    if storage_provider == "dropbox":
        return existing_paths(home / "Dropbox", home / "dropbox")
    if storage_provider == "onedrive":
        return existing_cloud_storage_roots("OneDrive-*") + existing_paths(home / "OneDrive", home / "onedrive")
    if storage_provider == "network_folder":
        volumes = Path("/Volumes")
        return [path for path in sorted(volumes.iterdir()) if path.is_dir()] if volumes.exists() else []
    if storage_provider == "local_folder":
        return [home]
    if storage_provider == "docker_mount":
        return existing_paths(Path("/workspace"), Path("/workspaces"), Path("/worklog-shared"))
    return []


def mounted_provider_suggested_roots(storage_provider: str, roots: list[Path]) -> list[str]:
    if storage_provider == "google_drive":
        suggestions = [str(root / "My Drive" / "Worklog") if root.name.startswith("GoogleDrive-") else str(root / "Worklog") for root in roots]
        suggestions.extend(["~/Library/CloudStorage/GoogleDrive-<account>/My Drive/Worklog", "~/Google Drive/Worklog"])
        return dedupe(suggestions)
    if storage_provider == "dropbox":
        return dedupe([str(root / "Worklog") for root in roots] + ["~/Dropbox/Worklog"])
    if storage_provider == "onedrive":
        return dedupe([str(root / "Worklog") for root in roots] + ["~/Library/CloudStorage/OneDrive-<organization>/Worklog", "~/OneDrive/Worklog"])
    if storage_provider == "network_folder":
        return dedupe([str(root / "Worklog") for root in roots] + ["/Volumes/<team-share>/Worklog"])
    if storage_provider == "local_folder":
        return dedupe([str(Path.home() / "Worklog" / "Shared")])
    if storage_provider == "docker_mount":
        return dedupe([str(root / "shared-worklog") for root in roots] + ["/workspace/shared-worklog", "/worklog-shared"])
    return []


def mounted_provider_connector_hint(storage_provider: str) -> str:
    hints = {
        "google_drive": "Use the Google Drive connector or Google Drive for desktop to establish the sync surface.",
        "dropbox": "Use Dropbox desktop sync or a Dropbox connector to establish the sync surface.",
        "onedrive": "Use OneDrive/SharePoint sync or a Microsoft connector to establish the sync surface.",
        "network_folder": "Mount the team network share before selecting a Worklog root.",
        "local_folder": "Local folder setup is available, but Worklog cannot verify external sync.",
        "docker_mount": "Mount the host/shared volume into the Worklog runtime before selecting a Worklog root.",
    }
    return hints.get(storage_provider, "Set up the provider before selecting a Worklog root.")


def existing_cloud_storage_roots(pattern: str) -> list[Path]:
    cloud_root = Path.home() / "Library" / "CloudStorage"
    if not cloud_root.exists():
        return []
    return [path for path in sorted(cloud_root.glob(pattern)) if path.is_dir()]


def existing_paths(*paths: Path) -> list[Path]:
    return [path for path in paths if path.exists()]


def local_path_ready(path: str) -> bool:
    expanded = Path(path).expanduser()
    return expanded.exists() and os.access(expanded, os.R_OK) and os.access(expanded, os.W_OK)


def shared_storage_provider_guidance(project_id: str, backend: str) -> dict[str, Any]:
    return {
        "backend": backend,
        "storage_provider_options": [
            storage_provider_summary(backend, item)
            for item in storage_providers_for_backend(backend)
        ],
        "notes": [
            "Choose the storage provider before choosing the shared root path.",
            "Mounted-folder providers use `shared_directory`; GitHub, GitLab, Bitbucket, and local Git repositories use `git_repo`.",
            "Do not infer the storage provider from an example path or mounted folder name.",
            "After the user chooses a provider, call Worklog again with `storage_provider` to get path suggestions for that provider.",
        ],
    }


def storage_providers_for_backend(backend: str) -> list[str]:
    if backend == "git_repo":
        return list(GIT_STORAGE_PROVIDERS)
    if backend == "shared_directory":
        return [*MOUNTED_STORAGE_PROVIDERS, *GIT_STORAGE_PROVIDERS]
    return []


def storage_provider_summary(backend: str, storage_provider: str) -> dict[str, Any]:
    descriptions = {
        "google_drive": "Mounted Google Drive folder via Google Drive for desktop.",
        "dropbox": "Mounted Dropbox folder.",
        "onedrive": "Mounted OneDrive or SharePoint-synced folder.",
        "network_folder": "Mounted team or network share.",
        "local_folder": "Local folder that another sync app or OS sharing layer manages.",
        "docker_mount": "Docker bind mount or volume visible to the process running Worklog.",
        "github": "Local checkout of a GitHub repository.",
        "gitlab": "Local checkout of a GitLab repository.",
        "bitbucket": "Local checkout of a Bitbucket repository.",
        "git_repo": "Local Git repository root.",
    }
    return {
        "storage_provider": storage_provider,
        "backend": backend_for_storage_provider(storage_provider, backend),
        "description": descriptions.get(storage_provider, "Custom shared location provider."),
    }


def shared_location_guidance(
    project_id: str,
    backend: str,
    storage_provider: str | None,
    *,
    provider_setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    storage_provider_options = []
    selected = storage_provider
    storage_providers = [selected] if selected else storage_providers_for_backend(backend)
    for item in storage_providers:
        if item:
            storage_provider_options.append(
                storage_provider_guidance(
                    project_id,
                    backend,
                    item,
                    provider_setup=provider_setup if item == selected else None,
                )
            )
    return {
        "backend": backend,
        "selected_storage_provider": selected,
        "storage_provider_setup": provider_setup or {},
        "storage_provider_options": storage_provider_options,
        "path_argument": "root",
        "notes": [
            "Pass the shared root path as `root`.",
            "Worklog will create `.worklog/projects/<project_id>` under that root.",
            "If you pass `shared_project_dir`, Worklog uses that exact project directory instead.",
        ],
    }


def storage_provider_guidance(
    project_id: str,
    backend: str,
    storage_provider: str,
    *,
    provider_setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    home = Path.home()
    paths: list[str] = []
    description = ""
    if storage_provider == "google_drive":
        description = "Mounted Google Drive folder via Google Drive for desktop."
        paths.extend(existing_cloud_roots("GoogleDrive-*", "My Drive/Worklog"))
        paths.append("~/Library/CloudStorage/GoogleDrive-<account>/My Drive/Worklog")
        paths.append("~/Google Drive/Worklog")
    elif storage_provider == "dropbox":
        description = "Mounted Dropbox folder."
        if (home / "Dropbox").exists():
            paths.append("~/Dropbox/Worklog")
        paths.append("~/Dropbox/Worklog")
    elif storage_provider == "onedrive":
        description = "Mounted OneDrive or SharePoint-synced folder."
        paths.extend(existing_cloud_roots("OneDrive-*", "Worklog"))
        paths.append("~/Library/CloudStorage/OneDrive-<organization>/Worklog")
        paths.append("~/OneDrive/Worklog")
    elif storage_provider == "network_folder":
        description = "Mounted team or network share."
        paths.append("/Volumes/<team-share>/Worklog")
    elif storage_provider == "local_folder":
        description = "Local folder that another sync app or OS sharing layer manages."
        paths.append("~/Worklog/Shared")
    elif storage_provider == "docker_mount":
        description = "Docker bind mount or volume visible to the process running Worklog."
        paths.append("/workspace/shared-worklog")
        paths.append("/worklog-shared")
    elif storage_provider in GIT_STORAGE_PROVIDERS:
        provider_name = git_provider_display_name(storage_provider)
        description = f"Local {provider_name} repository root. Worklog writes approved artifacts under `.worklog/projects/<project_id>`."
        paths.append("<repo-root>")
        paths.append(str(Path.cwd()))
    else:
        description = "Custom shared location."
        paths.append("<shared-root>/Worklog")
    if provider_setup and provider_setup.get("suggested_roots"):
        paths = [*provider_setup["suggested_roots"], *paths]
    return {
        "storage_provider": storage_provider,
        "backend": backend_for_storage_provider(storage_provider, backend),
        "description": description,
        "storage_provider_setup_status": (provider_setup or {}).get("setup_status"),
        "suggested_roots": dedupe(paths),
        "example_root": dedupe(paths)[0] if paths else "",
        "resulting_project_dir_example": resulting_project_dir_example(project_id, dedupe(paths)[0] if paths else "<shared-root>"),
    }


def existing_cloud_roots(pattern: str, suffix: str) -> list[str]:
    roots = []
    cloud_root = Path.home() / "Library" / "CloudStorage"
    if cloud_root.exists():
        for path in sorted(cloud_root.glob(pattern)):
            roots.append(str(path / suffix))
    return roots


def resulting_project_dir_example(project_id: str, root: str) -> str:
    return f"{root.rstrip('/')}/.worklog/projects/{file_token(project_id)}"


def normalize_permission_policy(value: Any, *, actor: str | None = None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    maintainers = dedupe(text_list(source.get("maintainers")))
    project_approvers = dedupe(text_list(source.get("project_approvers")))
    contributors = dedupe(text_list(source.get("contributors")))
    if actor:
        maintainers = dedupe([*maintainers, actor])
        project_approvers = dedupe([*project_approvers, actor])
        contributors = dedupe([*contributors, actor])
    contributors = dedupe([*contributors, *project_approvers, *maintainers])
    return {
        **DEFAULT_PERMISSION_POLICY,
        "contributors": contributors,
        "project_approvers": project_approvers,
        "maintainers": maintainers,
        "session_log_approval": clean_optional(source.get("session_log_approval")) or "own_session",
        "project_log_approval": clean_optional(source.get("project_log_approval")) or "listed_approvers",
        "template_changes": clean_optional(source.get("template_changes")) or "maintainers_only",
    }


def member_role_lists_from_args(args: dict[str, Any]) -> dict[str, list[str]]:
    source = args.get("permissions") if isinstance(args.get("permissions"), dict) else {}
    return {
        "contributors": dedupe([*text_list(source.get("contributors")), *text_list(args.get("contributors"))]),
        "project_approvers": dedupe(
            [*text_list(source.get("project_approvers")), *text_list(args.get("project_approvers"))]
        ),
        "maintainers": dedupe([*text_list(source.get("maintainers")), *text_list(args.get("maintainers"))]),
    }


def update_permission_policy(
    current: dict[str, Any],
    requested_members: dict[str, list[str]],
    *,
    operation: str,
) -> dict[str, Any]:
    if operation != "replace" and not any(requested_members.values()):
        raise UserError("Provide at least one contributor, project_approver, or maintainer.")
    if operation == "replace":
        result = dict(current)
        for key in ("contributors", "project_approvers", "maintainers"):
            if requested_members[key]:
                result[key] = requested_members[key]
    elif operation == "remove":
        result = dict(current)
        for key in ("contributors", "project_approvers", "maintainers"):
            remove_set = {normalize_actor(value) for value in requested_members[key]}
            result[key] = [value for value in text_list(current.get(key)) if normalize_actor(value) not in remove_set]
    else:
        result = dict(current)
        for key in ("contributors", "project_approvers", "maintainers"):
            result[key] = dedupe([*text_list(current.get(key)), *requested_members[key]])
    return normalize_permission_policy(result)


def shared_location_from_sharing(sharing: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key in ("root", "shared_project_dir"):
        value = clean_optional(sharing.get(key))
        if value:
            result[key] = value
    return result


def backend_permission_plan(
    project_id: str,
    backend: str,
    storage_provider: str | None,
    shared_location: dict[str, str],
    permissions: dict[str, Any],
    *,
    actor: str,
    current_permissions: dict[str, Any] | None = None,
    operation: str = "merge",
) -> dict[str, Any]:
    if backend == "local" and not storage_provider:
        return {
            "required": False,
            "backend": backend,
            "storage_provider": storage_provider,
            "members": [],
            "actions": [],
            "provider_permission_note": "Local-only projects do not need backend permission changes.",
        }
    members = backend_permission_members(permissions, actor=actor)
    current_members = backend_permission_members(current_permissions or {}, actor=actor)
    if operation == "remove":
        desired_norms = {normalize_actor(item["member"]) for item in members}
        members_for_action = [member for member in current_members if normalize_actor(member["member"]) not in desired_norms]
        required = bool(members_for_action)
    else:
        members_for_action = [member for member in members if normalize_actor(member["member"]) not in {normalize_actor(item["member"]) for item in current_members}]
        if not members_for_action:
            members_for_action = members
        required = bool(members_for_action)
    provider_name = storage_provider_display_name(storage_provider or "selected_provider")
    project_dir = shared_location.get("shared_project_dir") or resulting_project_dir_example(
        project_id,
        shared_location.get("root") or "<shared-root>",
    )
    if not required:
        return {
            "required": False,
            "backend": backend,
            "storage_provider": storage_provider,
            "members": [],
            "actions": [],
            "provider_permission_note": "No backend permission changes are needed.",
        }
    if storage_provider == "google_drive":
        capability = "google_drive_folder_acl"
        note = (
            "Grant editor access on the Google Drive folder to the listed members. The agent should complete "
            "the permission work through any available connector, API, browser, or provider tooling. Ask the "
            "user to apply the permission manually only when no available provider surface can complete it; "
            "do not mark this complete until folder access is actually applied or verified."
        )
    elif storage_provider == "dropbox":
        capability = "dropbox_folder_member_acl"
        note = "Share the Dropbox folder with the listed members as editors using Dropbox connector, browser, or provider tooling."
    elif storage_provider == "onedrive":
        capability = "onedrive_sharepoint_folder_acl"
        note = "Share the OneDrive/SharePoint folder with the listed members as editors using Microsoft connector, browser, or provider tooling."
    elif storage_provider in GIT_STORAGE_PROVIDERS:
        capability = "git_repository_collaborators"
        note = "Add the listed members as repository collaborators or team members, then verify push/pull access and branch protections."
    else:
        capability = "external_backend_acl"
        note = f"Apply access for the listed members in the {provider_name} backend before finalizing Worklog policy."
    return {
        "required": True,
        "backend": backend,
        "storage_provider": storage_provider,
        "provider": provider_name,
        "capability": capability,
        "operation": operation,
        "project_id": project_id,
        "shared_location": shared_location,
        "project_dir": project_dir,
        "members": members_for_action,
        "actions": [
            {
                "action": "grant" if operation != "remove" else "revoke",
                "member": member["member"],
                "worklog_roles": member["worklog_roles"],
                "backend_role": backend_role_for_member(storage_provider, member["worklog_roles"], operation=operation),
            }
            for member in members_for_action
        ],
        "provider_permission_note": note,
    }


def backend_permission_members(permissions: dict[str, Any], *, actor: str) -> list[dict[str, Any]]:
    by_member: dict[str, dict[str, Any]] = {}
    for role_key in ("contributors", "project_approvers", "maintainers"):
        for member in text_list(permissions.get(role_key)):
            if normalize_actor(member) == normalize_actor(actor):
                continue
            normalized = normalize_actor(member)
            item = by_member.setdefault(normalized, {"member": member, "worklog_roles": []})
            item["worklog_roles"].append(role_key)
    return list(by_member.values())


def backend_role_for_member(storage_provider: str | None, worklog_roles: list[str], *, operation: str) -> str:
    if operation == "remove":
        return "remove_access"
    if storage_provider in GIT_STORAGE_PROVIDERS:
        if "maintainers" in worklog_roles:
            return "maintain_or_admin"
        return "write"
    return "editor"


def backend_permission_verification_evidence(
    args: dict[str, Any],
    storage_provider: str | None,
    permission_plan: dict[str, Any],
) -> dict[str, Any]:
    raw = args.get("backend_permission_verification")
    if raw is None:
        raw = args.get("backend_permissions")
    if not isinstance(raw, dict):
        return {
            "backend_permissions_verified": False,
            "verification_required": bool(permission_plan.get("required")),
        }
    evidence_provider = sharing_storage_provider_key(raw.get("storage_provider"))
    if evidence_provider and storage_provider and evidence_provider != storage_provider:
        return {
            "backend_permissions_verified": False,
            "verification_required": bool(permission_plan.get("required")),
            "provider_permission_note": (
                f"Ignored permission verification for `{evidence_provider}` because the selected provider is `{storage_provider}`."
            ),
        }
    verified = bool(
        raw.get("backend_permissions_verified")
        or raw.get("backend_permissions_applied")
        or raw.get("provider_permissions_applied")
        or raw.get("verified")
    )
    result = dict(raw)
    result["storage_provider"] = storage_provider
    result["backend_permissions_verified"] = verified
    result["verification_required"] = bool(permission_plan.get("required"))
    if verified and not clean_optional(result.get("verified_at")):
        result["verified_at"] = now()
    if verified and not clean_optional(result.get("verification_scope")):
        result["verification_scope"] = permission_plan.get("capability") or "backend_permissions"
    return result


def normalize_backend_permissions(
    args: dict[str, Any],
    existing_backend_permissions: Any,
    permission_plan: dict[str, Any],
    permission_verification: dict[str, Any],
) -> dict[str, Any]:
    result = dict(existing_backend_permissions) if isinstance(existing_backend_permissions, dict) else {}
    raw = args.get("backend_permissions")
    if isinstance(raw, dict):
        result.update(raw)
    result["last_permission_plan"] = permission_plan
    if permission_verification.get("backend_permissions_verified"):
        result["last_permission_verification"] = permission_verification
    elif permission_plan.get("required"):
        result["permission_status"] = "pending_backend_verification"
    else:
        result["permission_status"] = "not_required"
    if permission_verification.get("backend_permissions_verified"):
        result["permission_status"] = "verified"
    return result


def actor_from_args(args: dict[str, Any]) -> str:
    return clean_optional(args.get("actor")) or "local-user"


def preserve_project_settings_fields(settings: dict[str, Any], existing: dict[str, Any]) -> None:
    for key in ("sharing", "permissions", "backend_permissions", "sharing_configured_at", "sharing_confirmation_quote", "sync_state"):
        if key in existing and key not in settings:
            settings[key] = existing[key]


def sharing_enabled(settings: dict[str, Any]) -> bool:
    sharing = settings.get("sharing")
    return isinstance(sharing, dict) and sharing.get("enabled") is True


def require_project_log_approver(settings: dict[str, Any], approver: str) -> None:
    if not sharing_enabled(settings):
        return
    if project_log_approval_valid(settings, approver):
        return
    raise UserError(f"`{approver}` is not allowed to approve project logs for this shared project.")


def project_log_approval_valid(settings: dict[str, Any], approver: str) -> bool:
    permissions = normalize_permission_policy(settings.get("permissions"))
    if not permissions["project_approvers"] and not permissions["maintainers"]:
        return True
    normalized = normalize_actor(approver)
    allowed = {normalize_actor(value) for value in [*permissions["project_approvers"], *permissions["maintainers"]]}
    return normalized in allowed


def normalize_actor(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def source_session_logs_from_args(db: Store, args: dict[str, Any]) -> list[dict[str, Any]]:
    ids = []
    if clean_optional(args.get("session_log_id")):
        ids.append(str(args["session_log_id"]))
    if isinstance(args.get("session_log_ids"), list):
        ids.extend(str(value) for value in args["session_log_ids"])
    logs = []
    for session_log_id in dedupe(ids):
        log = db.read("session_logs", session_log_id)
        if log["status"] != "approved":
            raise UserError("Only approved session logs can update a project log.")
        logs.append(log)
    return logs


def pending_session_logs(db: Store, project_id: str) -> list[dict[str, Any]]:
    latest = db.latest_project_log(project_id, status="approved")
    incorporated = set(latest.get("session_log_ids", [])) if latest else set()
    pending = [
        log
        for log in db.session_logs(project_id, status="approved")
        if log["id"] not in incorporated
    ]
    pending.sort(key=lambda item: item.get("approved_at") or item.get("updated_at") or "")
    return pending


def approved_project_logs(db: Store, project_id: str) -> list[dict[str, Any]]:
    logs = [
        log
        for log in db.list("project_logs")
        if log.get("project_id") == project_id and log.get("status") == "approved"
    ]
    logs.sort(key=lambda item: (int(item.get("version", 0)), item.get("approved_at") or item.get("updated_at") or ""))
    return logs


def publish_after_approval(db: Store, project_id: str) -> dict[str, Any]:
    settings = db.project_settings(project_id)
    if not sharing_enabled(settings):
        return {"enabled": False, "published": [], "conflicts": [], "text": "Project sharing is not configured."}
    try:
        result = sharing_backend(settings["sharing"]).push(db, project_id, dry_run=False)
        result["enabled"] = True
        return result
    except UserError as exc:
        return {"enabled": True, "published": [], "conflicts": [{"reason": str(exc)}]}


def ensure_shared_project_dirs(project_dir: Path) -> None:
    for path in (
        project_dir,
        project_dir / "approved" / "session_logs",
        project_dir / "approved" / "project_logs",
    ):
        path.mkdir(parents=True, exist_ok=True)


def shared_project_manifest(project_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "product": "worklog",
        "schema": SHARED_LAYOUT_VERSION,
        "project_id": project_id,
        "project_nature": settings.get("project_nature"),
        "publish_policy": "approved_only",
        "updated_at": now(),
    }


def settings_payload(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": settings.get("project_id"),
        "project_nature": settings.get("project_nature"),
        "session_log_template": settings.get("session_log_template"),
        "project_log_template": settings.get("project_log_template"),
        "updated_at": now(),
    }


def shared_project_index(db: Store, project_id: str) -> dict[str, Any]:
    latest = db.latest_project_log(project_id, status="approved")
    pending = pending_session_logs(db, project_id)
    return {
        "product": "worklog",
        "schema": SHARED_LAYOUT_VERSION,
        "project_id": project_id,
        "updated_at": now(),
        "latest_project_log_id": latest["id"] if latest else None,
        "approved_session_log_ids": [log["id"] for log in db.session_logs(project_id, status="approved")],
        "approved_project_log_ids": [log["id"] for log in approved_project_logs(db, project_id)],
        "unincorporated_session_log_ids": [log["id"] for log in pending],
    }


def publish_artifact(target: Path, payload: dict[str, Any], *, dry_run: bool, replace: bool = False) -> dict[str, Any]:
    if target.exists():
        existing = read_json_optional(target)
        if existing is not None and json_fingerprint(existing) != json_fingerprint(payload):
            if replace:
                if dry_run:
                    return {"status": "would_update", "path": str(target)}
                write_json_if_changed(target, payload)
                return {"status": "updated", "path": str(target)}
            return {"status": "conflict", "path": str(target), "reason": "shared artifact already exists with different content"}
        return {"status": "unchanged", "path": str(target)}
    if dry_run:
        return {"status": "would_publish", "path": str(target)}
    write_json_if_changed(target, payload)
    return {"status": "published", "path": str(target)}


def append_publish_result(
    published: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    outcome: dict[str, Any],
    artifact_type: str,
    artifact_id: str,
) -> None:
    item = {"type": artifact_type, "id": artifact_id, **outcome}
    if outcome["status"] == "conflict":
        conflicts.append(item)
    elif outcome["status"] in {"published", "would_publish", "updated", "would_update", "unchanged"}:
        published.append(item)


def read_json_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else None


def write_json_if_changed(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = read_json_optional(path)
        if existing is not None and json_fingerprint(existing) == json_fingerprint(value):
            return
    Store._write(path, value)


def json_fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def schemas() -> list[dict[str, Any]]:
    text_array = {"type": "array", "items": {"type": "string"}}
    any_object = {"type": "object", "additionalProperties": True}
    return [
        schema("worklog_status", "Show the local Worklog store and artifact counts.", {}, []),
        schema("worklog_list_projects", "List projects known to Worklog.", {}, []),
        schema(
            "worklog_start_project",
            "Start Worklog project setup by asking about the project nature and returning guidance for LLM-authored log templates.",
            {
                "project_id": {"type": "string"},
                "project_description": {"type": "string"},
                "project_nature": {"type": "string"},
                "confirmed_new_project": {"type": "boolean"},
            },
            [],
        ),
        schema(
            "worklog_recommend_templates",
            "Return guidance for the LLM to propose project-specific session-log and project-log templates.",
            {
                "project_nature": {"type": "string"},
                "project_description": {"type": "string"},
            },
            [],
        ),
        schema(
            "worklog_set_project_templates",
            "Set the user-approved session-log and project-log templates for a project.",
            {
                "project_id": {"type": "string"},
                "project_nature": {"type": "string"},
                "session_log_template": any_object,
                "project_log_template": any_object,
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            [
                "project_id",
                "session_log_template",
                "project_log_template",
                "confirmed_by_user",
                "confirmation_quote",
            ],
        ),
        schema(
            "worklog_show_project_templates",
            "Show the configured templates for a project, or default templates if none are configured.",
            {
                "project_id": {"type": "string"},
            },
            ["project_id"],
        ),
        schema(
            "worklog_configure_project_sharing",
            "Configure or join a shared Worklog project. Stores Worklog policy and initializes the selected backend when possible.",
            {
                "project_id": {"type": "string"},
                "project_nature": {"type": "string"},
                "mode": {"type": "string", "enum": ["create", "join"]},
                "backend": {"type": "string", "enum": ["shared_directory", "git_repo", "connector_payload"]},
                "storage_provider": {"type": "string"},
                "storage_provider_verification": any_object,
                "backend_permission_verification": any_object,
                "root": {"type": "string"},
                "shared_project_dir": {"type": "string"},
                "permissions": any_object,
                "backend_permissions": any_object,
                "publish_policy": {"type": "string"},
                "actor": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            ["project_id"],
        ),
        schema(
            "worklog_show_project_sharing",
            "Show a project's sharing configuration, backend status, permissions, and pending project-log updates.",
            {
                "project_id": {"type": "string"},
            },
            ["project_id"],
        ),
        schema(
            "worklog_update_project_members",
            "Add, replace, or remove shared Worklog members and require backend permission verification when the project is shared.",
            {
                "project_id": {"type": "string"},
                "operation": {"type": "string", "enum": ["merge", "replace", "remove"]},
                "contributors": text_array,
                "project_approvers": text_array,
                "maintainers": text_array,
                "permissions": any_object,
                "backend_permission_verification": any_object,
                "backend_permissions": any_object,
                "actor": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            ["project_id"],
        ),
        schema(
            "worklog_sync_project",
            "Sync a shared project by pulling shared approved artifacts and/or publishing local approved artifacts. Drafts are never shared.",
            {
                "project_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["pull", "push", "both"]},
                "dry_run": {"type": "boolean"},
                "actor": {"type": "string"},
            },
            ["project_id"],
        ),
        schema(
            "worklog_capture_session",
            "Capture a Codex JSONL session file as local source events.",
            {
                "session_path": {"type": "string"},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            [],
        ),
        schema(
            "worklog_import_events",
            "Import a JSON or JSONL event file as source events.",
            {
                "path": {"type": "string"},
                "session_id": {"type": "string"},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            ["path"],
        ),
        schema(
            "worklog_add_event",
            "Append one source event to a session.",
            {
                "text": {"type": "string"},
                "session_id": {"type": "string"},
                "event_id": {"type": "string"},
                "at": {"type": "string"},
                "speaker": {"type": "string"},
                "kind": {"type": "string"},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
                "meta": any_object,
            },
            ["text"],
        ),
        schema(
            "worklog_draft_session_log",
            "Draft a reviewable session log. Captures the current session first by default.",
            {
                "session_id": {"type": "string"},
                "project_id": {"type": "string"},
                "capture": {"type": "boolean"},
                "session_path": {"type": "string"},
            },
            [],
        ),
        schema(
            "worklog_show_session_log",
            "Render a session log for review.",
            {"session_log_id": {"type": "string"}},
            ["session_log_id"],
        ),
        schema(
            "worklog_edit_session_log",
            "Edit a draft session log.",
            {
                "session_log_id": {"type": "string"},
                "fields": any_object,
                "title": {"type": "string"},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "sections": any_object,
                "section": {"type": "string"},
                "items": {},
                "text": {"type": "string"},
                "outcomes": text_array,
                "decisions": text_array,
                "questions": text_array,
                "next_actions": text_array,
                "notes": text_array,
            },
            ["session_log_id"],
        ),
        schema(
            "worklog_approve_session_log",
            "Approve a reviewed session log. Requires explicit user confirmation.",
            {
                "session_log_id": {"type": "string"},
                "author": {"type": "string"},
                "approved_by": {"type": "string"},
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            ["session_log_id", "confirmed_by_user", "confirmation_quote"],
        ),
        schema(
            "worklog_draft_project_log",
            "Prepare project-rollup context, or store an LLM-authored project log draft when sections/fields are provided.",
            {
                "project_id": {"type": "string"},
                "session_log_id": {"type": "string"},
                "session_log_ids": text_array,
                "fields": any_object,
                "sections": any_object,
                "section": {"type": "string"},
                "items": {},
                "text": {"type": "string"},
                "title": {"type": "string"},
                "summary": text_array,
                "state": text_array,
                "decisions": text_array,
                "rules": text_array,
                "questions": text_array,
                "next_actions": text_array,
            },
            [],
        ),
        schema(
            "worklog_show_project_log",
            "Render a project log. Without project_log_id, shows latest approved for project_id.",
            {
                "project_log_id": {"type": "string"},
                "project_id": {"type": "string"},
                "status": {"type": "string", "enum": ["draft", "approved", "superseded", "any"]},
            },
            [],
        ),
        schema(
            "worklog_edit_project_log",
            "Edit a draft project log. Requires explicit user confirmation.",
            {
                "project_log_id": {"type": "string"},
                "fields": any_object,
                "sections": any_object,
                "section": {"type": "string"},
                "items": {},
                "text": {"type": "string"},
                "summary": text_array,
                "state": text_array,
                "decisions": text_array,
                "rules": text_array,
                "questions": text_array,
                "next_actions": text_array,
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            ["project_log_id", "confirmed_by_user", "confirmation_quote"],
        ),
        schema(
            "worklog_approve_project_log",
            "Approve a reviewed project log. Requires explicit user confirmation.",
            {
                "project_log_id": {"type": "string"},
                "approved_by": {"type": "string"},
                "confirmed_by_user": {"type": "boolean"},
                "confirmation_quote": {"type": "string"},
            },
            ["project_log_id", "confirmed_by_user", "confirmation_quote"],
        ),
        schema(
            "worklog_resume_context",
            "Generate resume context from approved project/session logs.",
            {
                "project_id": {"type": "string"},
                "recent": {"type": "integer"},
                "save": {"type": "boolean"},
            },
            [],
        ),
    ]


def schema(name: str, description: str, properties: dict[str, Any], required_fields: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required_fields,
            "additionalProperties": False,
        },
    }


def ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def fail(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def command_payload(data: dict[str, Any]) -> dict[str, Any]:
    text = data.get("text") or json.dumps(data, indent=2, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "structuredContent": data}


def empty_session(
    session_id: str,
    *,
    source: str,
    project_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    stamp = now()
    return {
        "id": session_id,
        "source": source,
        "project_id": project_id,
        "task_id": task_id,
        "created_at": stamp,
        "updated_at": stamp,
        "started_at": None,
        "ended_at": None,
        "events": [],
        "source_meta": {},
    }


def session_from_codex_file(
    path: Path,
    *,
    project_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    session_id = session_id_from_file(path) or uid("session")
    session = empty_session(session_id, source="codex_jsonl", project_id=project_id, task_id=task_id)
    session["source_meta"] = {"path": str(path)}
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = codex_record_to_event(record, session_id=session_id, index=index, path=path)
            if event:
                events.append(event)
    session["events"] = sorted(unique_by_id(events), key=lambda item: (item["at"], item["id"]))
    touch_session_times(session)
    return session


def session_from_event_file(
    path: Path,
    *,
    project_id: str | None,
    task_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    payload = load_json_or_jsonl(path)
    records = payload if isinstance(payload, list) else payload.get("events", [payload])
    if not isinstance(records, list):
        raise UserError("Imported file must be JSONL, a JSON array, or an object with an events array.")
    project_id = project_id or (payload.get("project_id") if isinstance(payload, dict) else None)
    task_id = task_id or (payload.get("task_id") if isinstance(payload, dict) else None)
    session_id = session_id or (payload.get("session_id") if isinstance(payload, dict) else None)
    session = empty_session(session_id or uid("session"), source="event_file", project_id=project_id, task_id=task_id)
    session["source_meta"] = {"path": str(path)}
    events = [generic_record_to_event(record, session["id"], index) for index, record in enumerate(records, 1)]
    session["events"] = sorted(unique_by_id([event for event in events if event["text"]]), key=lambda item: (item["at"], item["id"]))
    touch_session_times(session)
    return session


def codex_record_to_event(
    record: dict[str, Any],
    *,
    session_id: str,
    index: int,
    path: Path,
) -> dict[str, Any] | None:
    if record.get("type") == "session_meta":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    payload_type = payload.get("type")
    at = str(record.get("timestamp") or now())
    speaker = "system"
    kind = str(payload_type or "event")
    text = ""
    if payload_type == "user_message":
        speaker = "user"
        kind = "message"
        text = str(payload.get("message") or "")
    elif payload_type == "agent_message":
        speaker = "assistant"
        kind = "message"
        text = str(payload.get("message") or "")
    elif payload_type == "task_complete":
        speaker = "assistant"
        kind = "completion"
        text = str(payload.get("last_agent_message") or "")
    elif payload_type == "patch_apply_end":
        speaker = "tool"
        kind = "patch"
        text = str(payload.get("stdout") or payload.get("stderr") or "")
    if not text:
        return None
    return {
        "id": stable_event_id(session_id, index, text),
        "at": at,
        "speaker": speaker,
        "kind": kind,
        "text": squeeze(text, MAX_EVENT_TEXT),
        "meta": {"source_path": str(path), "line": index, "codex_type": payload_type},
    }


def generic_record_to_event(record: Any, session_id: str, index: int) -> dict[str, Any]:
    if isinstance(record, dict):
        text = ""
        for key in ("text", "content", "message", "summary"):
            if key in record:
                text = flatten_text(record[key])
                break
        if not text:
            text = json.dumps(record, sort_keys=True)
        return {
            "id": str(record.get("id") or stable_event_id(session_id, index, text)),
            "at": str(record.get("at") or record.get("timestamp") or record.get("created_at") or now()),
            "speaker": str(record.get("speaker") or record.get("actor") or record.get("role") or "system"),
            "kind": str(record.get("kind") or record.get("type") or "event"),
            "text": squeeze(text, MAX_EVENT_TEXT),
            "meta": dict(record.get("meta") or record.get("metadata") or {}),
        }
    text = str(record)
    return {
        "id": stable_event_id(session_id, index, text),
        "at": now(),
        "speaker": "system",
        "kind": "event",
        "text": squeeze(text, MAX_EVENT_TEXT),
        "meta": {},
    }


def merge_sessions(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return incoming
    events = unique_by_id([*existing.get("events", []), *incoming.get("events", [])])
    merged = dict(existing)
    merged["events"] = sorted(events, key=lambda item: (item["at"], item["id"]))
    merged["project_id"] = incoming.get("project_id") or existing.get("project_id")
    merged["task_id"] = incoming.get("task_id") or existing.get("task_id")
    merged["source"] = incoming.get("source") or existing.get("source")
    merged["source_meta"] = {**existing.get("source_meta", {}), **incoming.get("source_meta", {})}
    merged["updated_at"] = now()
    touch_session_times(merged)
    return merged


def make_session_log(session: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    events = session["events"]
    created = now()
    first_user = first_text(events, "user")
    final_assistant = last_text(events, "assistant")
    summary_bits = [f"{len(events)} source events captured"]
    if first_user:
        summary_bits.append(f"started with: {strip_terminal_punctuation(squeeze(first_user, 180))}")
    if final_assistant:
        summary_bits.append(f"ended with: {strip_terminal_punctuation(squeeze(final_assistant, 180))}")
    source_event_ids = [event["id"] for event in events]
    if len(source_event_ids) > 5:
        source_event_ids = source_event_ids[:3] + source_event_ids[-2:]
    draft_values = {
        "summary": "; ".join(summary_bits) + ".",
        "outcomes": collect_lines(events, ("done", "created", "updated", "implemented", "fixed", "validated", "complete")),
        "decisions": collect_lines(events, ("decide", "decision", "choose", "rename", "prefer", "keep", "must", "should")),
        "questions": collect_questions(events),
        "next_actions": collect_lines(events, ("next", "todo", "remaining", "follow up", "follow-up", "need to")),
        "notes": [],
        "validation": collect_lines(events, ("test", "validated", "validation", "passed", "failed", "checked", "smoke")),
    }
    sections = draft_sections(template, draft_values)
    return {
        "id": uid("session_log"),
        "session_id": session["id"],
        "project_id": session.get("project_id"),
        "task_id": session.get("task_id"),
        "title": title_from_text(first_user) or f"Session {session['id']}",
        "status": "draft",
        "created_at": created,
        "updated_at": created,
        "approved_at": None,
        "approved_by": None,
        "template": template,
        "sections": sections,
        "source_event_count": len(events),
        "source_event_ids": dedupe(source_event_ids),
    }


def make_project_log(
    project_id: str,
    *,
    previous: dict[str, Any] | None,
    template: dict[str, Any],
) -> dict[str, Any]:
    stamp = now()
    if previous is None:
        sections = empty_sections(template)
        return {
            "id": uid("project_log"),
            "project_id": project_id,
            "title": f"Project Log: {project_id}",
            "version": 1,
            "status": "draft",
            "created_at": stamp,
            "updated_at": stamp,
            "approved_at": None,
            "approved_by": None,
            "supersedes": None,
            "base_project_log_id": None,
            "template": template,
            "sections": sections,
            "session_log_ids": [],
        }
    previous_sections = sections_from_log(previous)
    sections = empty_sections(template)
    sections.update({key: copy_section_value(value) for key, value in previous_sections.items()})
    draft = {"sections": sections, "session_log_ids": list(previous.get("session_log_ids", []))}
    draft.update(
        {
            "id": uid("project_log"),
            "project_id": project_id,
            "title": previous.get("title") or f"Project Log: {project_id}",
            "version": int(previous.get("version", 1)) + 1,
            "status": "draft",
            "created_at": stamp,
            "updated_at": stamp,
            "approved_at": None,
            "approved_by": None,
            "supersedes": previous["id"],
            "base_project_log_id": previous["id"],
            "template": template,
        }
    )
    return draft


SESSION_LOG_EDITABLE = {
    "title",
    "project_id",
    "task_id",
    "summary",
    "outcomes",
    "decisions",
    "questions",
    "next_actions",
    "notes",
    "sections",
}

PROJECT_LOG_EDITABLE = {
    "title",
    "summary",
    "state",
    "decisions",
    "rules",
    "questions",
    "next_actions",
    "sections",
}


def project_log_patch_from_args(args: dict[str, Any]) -> dict[str, Any]:
    patch = dict(args.get("fields") or {})
    if isinstance(args.get("sections"), dict):
        patch["sections"] = args["sections"]
    for name in PROJECT_LOG_EDITABLE:
        if name in args:
            patch[name] = args[name]
    if clean_optional(args.get("section")):
        section = section_key(args["section"])
        patch[section] = args.get("items", args.get("text", []))
    return patch


def apply_session_patch(log: dict[str, Any], patch: dict[str, Any]) -> None:
    for name, value in patch.items():
        if name == "sections" and isinstance(value, dict):
            sections = sections_from_log(log)
            for key, section_value_item in value.items():
                sections[section_key(key)] = normalize_section_value(section_value_item)
            log["sections"] = sections
            continue
        if name == "project_id":
            log[name] = clean_optional(value)
            continue
        if name == "task_id":
            log[name] = clean_optional(value)
            continue
        if name in {"outcomes", "decisions", "questions", "next_actions", "notes"}:
            sections = sections_from_log(log)
            sections[name] = text_list(value)
            log["sections"] = sections
            continue
        if name == "summary":
            sections = sections_from_log(log)
            sections["summary"] = str(value).strip()
            log["sections"] = sections
            continue
        if name == "title":
            log[name] = str(value).strip()
            continue
        if name not in SESSION_LOG_EDITABLE:
            sections = sections_from_log(log)
            sections[section_key(name)] = normalize_section_value(value)
            log["sections"] = sections


def apply_project_patch(log: dict[str, Any], patch: dict[str, Any]) -> None:
    for name, value in patch.items():
        if name == "sections" and isinstance(value, dict):
            sections = sections_from_log(log)
            for key, section_value_item in value.items():
                sections[section_key(key)] = normalize_section_value(section_value_item)
            log["sections"] = sections
            continue
        if name == "title":
            log[name] = str(value).strip()
            continue
        sections = sections_from_log(log)
        sections[section_key(name)] = normalize_section_value(value)
        log["sections"] = sections


def render_session_log(log: dict[str, Any]) -> str:
    output = [
        f"# Session Log: {log.get('title')}",
        "",
        f"- id: `{log['id']}`",
        f"- status: `{log['status']}`",
        f"- session_id: `{log['session_id']}`",
    ]
    if log.get("project_id"):
        output.append(f"- project_id: `{log['project_id']}`")
    if log.get("task_id"):
        output.append(f"- task_id: `{log['task_id']}`")
    render_template_sections(output, log)
    attention = []
    if not log.get("project_id"):
        attention.append("Add `project_id` before approval.")
    if not any(section_has_content(value) for value in sections_from_log(log).values()):
        attention.append("This draft has little durable project state; review the summary carefully.")
    add_section(output, "Attention", attention)
    return "\n".join(output)


def render_project_log(log: dict[str, Any]) -> str:
    output = [
        f"# {log.get('title')}",
        "",
        f"- id: `{log['id']}`",
        f"- project_id: `{log['project_id']}`",
        f"- status: `{log['status']}`",
        f"- version: {log['version']}",
    ]
    if log.get("supersedes"):
        output.append(f"- supersedes: `{log['supersedes']}`")
    render_template_sections(output, log)
    add_section(output, "Source Session Logs", log.get("session_log_ids", []))
    return "\n".join(output)


def render_project_rollup_authoring(
    project_id: str,
    previous: dict[str, Any] | None,
    session_logs: list[dict[str, Any]],
    project_template: dict[str, Any],
) -> str:
    output = [
        f"# Author Project Log Rollup: {project_id}",
        "",
        "Worklog does not generate project-log rollups automatically. The assistant should write the project-log draft from the approved source material, then call `worklog_draft_project_log` again with `sections` or `fields`.",
        "",
        "Project log sections to author:",
    ]
    for section in template_sections(project_template):
        details = [f"kind={section.get('kind', 'list')}"]
        if section.get("rollup_from"):
            details.append(f"rollup_hint={section['rollup_from']}")
        if section.get("description"):
            details.append(section["description"])
        output.append(f"- `{section['key']}`: {section['title']} ({'; '.join(details)})")
    output.extend(["", "## Previous Approved Project Log", ""])
    if previous is None:
        output.append("- None")
    else:
        output.extend(
            [
                f"- id: `{previous['id']}`",
                f"- version: {previous.get('version')}",
                "",
            ]
        )
        render_template_sections(output, previous)
    output.extend(["", "## Approved Session Logs", ""])
    if not session_logs:
        output.append("- None provided")
    else:
        for session_log in session_logs:
            output.extend(
                [
                    f"### {session_log.get('title')}",
                    "",
                    f"- id: `{session_log['id']}`",
                    f"- approved_by: `{session_log.get('approved_by') or ''}`",
                    "",
                ]
            )
            render_template_sections(output, session_log)
    output.extend(
        [
            "",
            "Next:",
            "- Author a coherent project-log draft in the user's project-log format.",
            "- Place facts only in sections where they semantically belong.",
            "- Preserve useful previous project-log content and update it with the approved session log(s).",
            "- Then call `worklog_draft_project_log` with `project_id`, the same `session_log_ids` if applicable, and the authored `sections` or `fields`.",
        ]
    )
    return "\n".join(output)


def render_resume(
    project_id: str,
    project_log: dict[str, Any] | None,
    sessions: list[dict[str, Any]],
    pending: list[dict[str, Any]],
) -> str:
    output = [f"# Resume Context: {project_id}", "", f"Generated at: `{now()}`", ""]
    if project_log is None:
        output.extend(["## Project Log", "", "- No approved project log exists yet."])
    else:
        output.extend(["## Project Log", ""])
        render_template_sections(output, project_log)
    output.extend(["", render_pending_project_updates(pending)])
    output.extend(["", "## Recent Session Logs", ""])
    if not sessions:
        output.append("- None")
    for session in sessions:
        output.extend([f"### {session.get('title')}", ""])
        render_template_sections(output, session)
    output.extend(
        [
            "",
            "## Use This Context",
            "",
            "- Treat this as reviewed Worklog state.",
            "- Do not treat raw source events as approved project-log state.",
        ]
    )
    return "\n".join(output)


def render_session_capture(session: dict[str, Any], title: str) -> str:
    return lines(
        f"# {title}",
        "",
        f"- session_id: `{session['id']}`",
        f"- source: `{session['source']}`",
        f"- project_id: `{session.get('project_id') or ''}`",
        f"- task_id: `{session.get('task_id') or ''}`",
        f"- events: {len(session.get('events', []))}",
        f"- started_at: `{session.get('started_at')}`",
        f"- ended_at: `{session.get('ended_at')}`",
    )


def add_section(output: list[str], title: str, values: Any) -> None:
    output.extend(["", f"## {title}", ""])
    if isinstance(values, str):
        output.append(values if values.strip() else "-")
        return
    if isinstance(values, dict):
        if not values:
            output.append("- None")
            return
        output.append("```json")
        output.append(json.dumps(values, indent=2, sort_keys=True))
        output.append("```")
        return
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    output.extend([f"- {value}" for value in cleaned] or ["- None"])


def render_template_sections(output: list[str], log: dict[str, Any]) -> None:
    sections = sections_from_log(log)
    rendered_keys: set[str] = set()
    for definition in template_sections(log.get("template")):
        key = definition["key"]
        rendered_keys.add(key)
        add_section(output, definition.get("title") or titleize(key), sections.get(key, []))
    for key in sorted(set(sections) - rendered_keys):
        add_section(output, titleize(key), sections[key])


def render_project_start(
    project_id: str | None,
    project_description: str | None,
    project_nature: str | None,
    brief: dict[str, Any],
) -> str:
    output = ["# Start Worklog Project", ""]
    if project_id:
        output.append(f"- project_id: `{project_id}`")
    if project_nature:
        output.append(f"- project_nature: `{project_nature}`")
    if project_description:
        output.append(f"- project_description: {project_description}")
    output.extend(
        [
            "",
            "Sharing setup:",
            "- Ask whether this project is local-only or shared with team members.",
            "- If shared, run the storage setup in order: suggest providers, user selects provider, suggest paths for that provider, user selects path.",
            "- Recommend `shared_directory` for mounted Google Drive/Dropbox/OneDrive/network/Docker paths, `git_repo` for GitHub/GitLab/Bitbucket/local Git repo roots, or `connector_payload` when an external connector must publish the approved artifacts.",
            "- If the user has not selected a provider, call `worklog_configure_project_sharing` without `storage_provider` to get provider options.",
            "- After the user selects a provider, call `worklog_configure_project_sharing` with `storage_provider` to get path options for that provider.",
            "- Do not configure sharing until the user explicitly confirms both the selected provider and selected path.",
            "- Configure sharing only after the user approves the backend, storage provider, shared location, contributors, project approvers, and maintainers.",
            "- Draft logs remain local; only approved artifacts are shared.",
        ]
    )
    output.extend(["", *render_template_authoring_lines(brief)])
    return "\n".join(output)


def render_existing_project_check(
    project_id: str | None,
    project_description: str | None,
    project_nature: str | None,
    known_projects: list[dict[str, Any]],
    matches: list[dict[str, Any]],
) -> str:
    output = ["# Existing Worklog Projects", ""]
    output.append(
        "Worklog found existing projects. Before starting a new project, choose an existing project if this work belongs to one, or ask the user to confirm that this is a new project."
    )
    if project_id or project_description or project_nature:
        output.extend(["", "Requested setup:"])
        if project_id:
            output.append(f"- project_id: `{project_id}`")
        if project_nature:
            output.append(f"- project_nature: `{project_nature}`")
        if project_description:
            output.append(f"- project_description: {project_description}")
    output.extend(["", "Possible matches:"])
    if matches:
        for project in matches:
            output.append(project_line(project))
    else:
        output.append("- None obvious")
    output.extend(["", "All known projects:"])
    for project in known_projects:
        output.append(project_line(project))
    output.extend(
        [
            "",
            "Next:",
            "- If one of these is right, use that exact `project_id` for Worklog context.",
            "- If none is right, ask the user to confirm this is a new project, then call `worklog_start_project` again with `confirmed_new_project: true`.",
        ]
    )
    return "\n".join(output)


def render_template_authoring_brief(brief: dict[str, Any]) -> str:
    return "\n".join(["# Worklog Template Authoring", "", *render_template_authoring_lines(brief)])


def render_template_authoring_lines(brief: dict[str, Any]) -> list[str]:
    output = [
        "Worklog does not provide predefined templates. The assistant should propose a project-specific structure, then refine it with the user.",
        "",
        "Ask or infer:",
    ]
    output.extend(f"- {item}" for item in brief["questions"])
    output.extend(
        [
            "",
            "The approved template objects must include:",
            "- `session_log_template`: name, description, sections",
            "- `project_log_template`: name, description, sections",
            "",
            "Each section should include `key`, `title`, and `kind` (`text` or `list`).",
            "`draft_from` can guide rough session-log drafting. `rollup_from` on project sections is only a hint for the assistant; Worklog does not automatically copy content into project sections.",
            "",
            "Available source channels:",
            "- " + ", ".join(TEMPLATE_AUTHORING_GUIDANCE["draft_from_options"]),
            "",
            "Next: the assistant proposes templates in chat, the user edits/approves them, then call `worklog_set_project_templates` with the exact approved objects.",
        ]
    )
    return output


def render_project_templates(settings: dict[str, Any]) -> str:
    output = [
        f"# Worklog Templates: {settings.get('project_id') or 'default'}",
        "",
        f"- project_nature: `{settings.get('project_nature', 'unconfigured')}`",
    ]
    for label, key in (
        ("Session Log Template", "session_log_template"),
        ("Project Log Template", "project_log_template"),
    ):
        template = settings[key]
        output.extend(["", f"## {label}", "", f"Name: {template['name']}", ""])
        if template.get("description"):
            output.extend([template["description"], ""])
        output.append("Sections:")
        for section in template_sections(template):
            details = []
            if section.get("kind"):
                details.append(f"kind={section['kind']}")
            if section.get("draft_from"):
                details.append(f"draft_from={section['draft_from']}")
            if section.get("rollup_from"):
                details.append(f"rollup_from={section['rollup_from']}")
            suffix = f" ({', '.join(details)})" if details else ""
            output.append(f"- `{section['key']}`: {section['title']}{suffix}")
    return "\n".join(output)


def render_project_sharing(
    settings: dict[str, Any],
    *,
    setup: dict[str, Any] | None,
    pending: list[dict[str, Any]],
) -> str:
    output = [f"# Worklog Sharing: {settings.get('project_id') or 'default'}", ""]
    if not sharing_enabled(settings):
        output.append("- sharing: `disabled`")
        return "\n".join(output)
    sharing = settings["sharing"]
    permissions = normalize_permission_policy(settings.get("permissions"))
    output.extend(
        [
            f"- sharing: `enabled`",
            f"- backend: `{sharing.get('backend')}`",
            f"- storage_provider: `{sharing.get('storage_provider') or ''}`",
            f"- publish_policy: `{sharing.get('publish_policy', 'approved_only')}`",
            f"- configured_by: `{sharing.get('configured_by') or ''}`",
        ]
    )
    if sharing.get("root"):
        output.append(f"- root: `{sharing['root']}`")
    if sharing.get("shared_project_dir"):
        output.append(f"- shared_project_dir: `{sharing['shared_project_dir']}`")
    add_section(output, "Worklog Policy", permissions)
    if setup:
        add_section(output, "Backend Status", setup)
    add_section(output, "Backend Permissions", settings.get("backend_permissions") or {})
    output.extend(["", render_pending_project_updates(pending)])
    output.extend(
        [
            "",
            "## Sharing Rule",
            "",
            "- Draft session logs and draft project logs remain local.",
            "- Approved session logs may be published by contributors.",
            "- Approved project logs may be published only when approved by a project approver.",
        ]
    )
    return "\n".join(output)


def render_storage_provider_guidance(
    project_id: str,
    backend: str,
    guidance: dict[str, Any],
    permissions: dict[str, Any],
) -> str:
    output = [
        f"# Choose Worklog Storage Provider: {project_id}",
        "",
        "Worklog needs the storage provider before it can suggest a shared root path.",
        "",
        f"- backend: `{backend}`",
        "- setup_stage: `choose_storage_provider`",
    ]
    if guidance.get("provided_shared_location"):
        output.extend(["", "## Provided Location", ""])
        for key, value in guidance["provided_shared_location"].items():
            output.append(f"- {key}: `{value}`")
        output.extend(
            [
                "",
                "Hold this location for now. Do not infer the storage provider from it.",
            ]
        )
    if any(option.get("backend") != backend for option in guidance.get("storage_provider_options", [])):
        output.extend(
            [
                "",
                "The selected storage provider determines the final backend: mounted folders use `shared_directory`; Git providers use `git_repo`.",
            ]
        )
    output.extend(["", "## Storage Provider Options", ""])
    for option in guidance.get("storage_provider_options", []):
        output.append(f"### {storage_provider_display_name(option['storage_provider'])}")
        output.append("")
        output.append(option["description"])
        output.append("")
        output.append(f"- backend: `{option['backend']}`")
        output.append("")
    add_section(output, "Current Worklog Policy", permissions)
    output.extend(
        [
            "",
            "## Next",
            "",
            "Ask the user to choose one storage provider.",
            "After they choose, call `worklog_configure_project_sharing` again with `storage_provider` and without final confirmation so Worklog can suggest paths for that provider.",
        ]
    )
    return "\n".join(output)


def render_shared_location_guidance(
    project_id: str,
    backend: str,
    storage_provider: str | None,
    guidance: dict[str, Any],
    permissions: dict[str, Any],
) -> str:
    output = [
        f"# Choose Shared Worklog Location: {project_id}",
        "",
        "Worklog has the selected storage provider. It now needs the shared root path before it can configure the backend.",
        "",
        f"- backend: `{backend}`",
        "- setup_stage: `choose_shared_location`",
    ]
    if storage_provider:
        output.append(f"- requested_storage_provider: `{storage_provider}`")
    if guidance.get("storage_provider_setup"):
        add_section(output, "Storage Provider Setup", guidance["storage_provider_setup"])
    output.extend(["", "## Path Options", ""])
    example_provider = storage_provider_display_name(storage_provider or "selected_provider")
    example_root = "<shared-root>"
    for option in guidance.get("storage_provider_options", []):
        output.append(f"### {storage_provider_display_name(option['storage_provider'])} Paths")
        output.append("")
        output.append(option["description"])
        output.append("")
        if option.get("example_root") and example_root == "<shared-root>":
            example_provider = storage_provider_display_name(option["storage_provider"])
            example_root = option["example_root"]
        output.append("Suggested root paths:")
        for path in option.get("suggested_roots", []):
            output.append(f"- `{path}`")
        output.append("")
        output.append(f"Project directory example: `{option.get('resulting_project_dir_example')}`")
        output.append("")
    add_section(output, "Current Worklog Policy", permissions)
    output.extend(
        [
            "",
            "## Next",
            "",
            "If the storage provider setup is ready, ask the user to choose one shared root path, or provide another path for this storage provider.",
            "If the storage provider setup is not ready, ask for approval to use the provider connector or desktop sync/mount setup first, then call Worklog again with the selected provider.",
            "For Google Drive, Dropbox, or OneDrive, a local mounted path is not enough to declare cloud sync ready. If Worklog returns `needs_cloud_verification`, ask the user to approve the connector check and pass `storage_provider_verification` after the connector verifies the cloud-side location.",
            "After they confirm the path, call `worklog_configure_project_sharing` again with `storage_provider`, `root` or `shared_project_dir`, plus `confirmed_by_user: true` and their confirmation quote.",
            "",
            "Example confirmation prompt:",
            f"- Use {example_provider} at `{example_root}` for `{project_id}`; configure sharing.",
        ]
    )
    return "\n".join(output)


def render_storage_provider_verification_required(
    project_id: str,
    backend: str,
    storage_provider: str,
    shared_location: dict[str, str],
    provider_setup: dict[str, Any],
    permissions: dict[str, Any],
) -> str:
    provider_name = storage_provider_display_name(storage_provider)
    output = [
        f"# Verify Worklog Storage Provider: {project_id}",
        "",
        f"Worklog has the selected {provider_name} location, but it has not verified the cloud-side sync/connection.",
        "",
        f"- backend: `{backend}`",
        f"- storage_provider: `{storage_provider}`",
        "- setup_stage: `verify_storage_provider_connection`",
    ]
    if shared_location:
        output.extend(["", "## Selected Location", ""])
        for key, value in shared_location.items():
            output.append(f"- {key}: `{value}`")
    add_section(output, "Storage Provider Setup", provider_setup)
    add_section(output, "Current Worklog Policy", permissions)
    output.extend(
        [
            "",
            "## Next",
            "",
            f"Use an authenticated {provider_name} MCP/API connector first when it exposes the required verification operation.",
            f"If the exposed {provider_name} MCP/API surface cannot verify this kind of location, ask the user to authorize the next provider surface: browser UI, provider API tooling, or desktop sync tooling.",
            "If the user has already approved the Worklog sharing setup, do not ask them to re-approve that same setup; ask only for the missing connector permission, browser permission, API permission, or provider sign-in.",
            "If provider tooling opens a sign-in page, tell the user exactly which provider account or surface needs sign-in, then continue from that authenticated surface.",
            "Do not claim the shared project is synced or ready from Worklog's side until connector/cloud verification succeeds.",
            "After verification succeeds, call `worklog_configure_project_sharing` again with the same selected location, `confirmed_by_user: true`, and `storage_provider_verification`.",
            "",
            "The `storage_provider_verification` object should include:",
            f"- `storage_provider`: `{storage_provider}`",
            "- `provider_connection_verified`: `true`",
            f"- `verification_scope`: `{storage_provider}_connector`",
            "- `verified_at`: current timestamp, if available",
            "- `provider_connection_note`: concise note about what the connector verified",
        ]
    )
    return "\n".join(output)


def render_backend_permission_plan_required(
    project_id: str,
    backend: str,
    storage_provider: str | None,
    shared_location: dict[str, str],
    permission_plan: dict[str, Any],
    permission_verification: dict[str, Any],
) -> str:
    provider_name = permission_plan.get("provider") or storage_provider_display_name(storage_provider or "selected_provider")
    output = [
        f"# Apply Worklog Backend Permissions: {project_id}",
        "",
        "Worklog has the requested members policy, but the shared backend permissions still need to be applied or verified.",
        "",
        f"- backend: `{backend}`",
        f"- storage_provider: `{storage_provider or ''}`",
        "- setup_stage: `apply_backend_permissions`",
    ]
    if shared_location:
        output.extend(["", "## Selected Location", ""])
        for key, value in shared_location.items():
            output.append(f"- {key}: `{value}`")
    output.extend(
        [
            "",
            "## Backend Permission Actions",
            "",
            permission_plan.get("provider_permission_note") or "Apply the listed permission changes in the storage backend.",
            "",
        ]
    )
    for action in permission_plan.get("actions", []):
        roles = ", ".join(action.get("worklog_roles", []))
        output.append(
            f"- {action.get('action')}: `{action.get('member')}` as `{action.get('backend_role')}`"
            f" for Worklog roles `{roles}`"
        )
    add_section(output, "Backend Permission Verification", permission_verification)
    output.extend(
        [
            "",
            "## Next",
            "",
            f"Use an authenticated {provider_name} MCP/API connector first when it exposes the required folder or repository permission operation.",
            f"If the exposed {provider_name} MCP/API surface cannot apply this kind of backend permission, the agent should use the next available provider surface itself: browser UI, provider API tooling, repository/admin tooling, desktop sync tooling, or another authenticated connector.",
            "If the user has already approved the member or sharing change, do not ask them to re-approve that same Worklog change; ask only for a specific missing authorization step that the agent cannot complete alone, such as connector permission, browser permission, API permission, or provider sign-in.",
            "If provider tooling opens a sign-in page, ask the user to sign in only when that authenticated surface is needed for the agent to continue.",
            "Ask the user to apply permissions manually only when no available connector, API, browser, admin, desktop, or provider surface can complete the backend permission change.",
            "Do not say the member has been added to the shared project until backend access is applied or verified.",
            "After backend permission changes succeed, re-check the backend ACL or repository permission list, then call the Worklog tool again with `backend_permission_verification`.",
            "",
            "The `backend_permission_verification` object should include:",
            f"- `storage_provider`: `{storage_provider or ''}`",
            "- `backend_permissions_verified`: `true`",
            f"- `verification_scope`: `{permission_plan.get('capability') or 'backend_permissions'}`",
            "- `members`: the members whose backend access was applied or verified",
            "- `provider_permission_note`: concise note about what changed",
        ]
    )
    return "\n".join(output)


def render_project_members_update(
    project_id: str,
    operation: str,
    permissions: dict[str, Any],
    permission_plan: dict[str, Any],
    permission_verification: dict[str, Any],
) -> str:
    output = [
        f"# Worklog Members Updated: {project_id}",
        "",
        f"- operation: `{operation}`",
        f"- backend_permission_status: `{'verified' if permission_verification.get('backend_permissions_verified') else ('not_required' if not permission_plan.get('required') else 'pending')}`",
    ]
    add_section(output, "Worklog Policy", permissions)
    add_section(output, "Backend Permission Plan", permission_plan)
    if permission_verification.get("backend_permissions_verified"):
        add_section(output, "Backend Permission Verification", permission_verification)
    output.extend(
        [
            "",
            "## Sharing Rule",
            "",
            "- Worklog policy records who may contribute, approve project logs, and maintain settings.",
            "- Backend permissions record whether those members can actually access the shared folder or repository.",
        ]
    )
    return "\n".join(output)


def render_sync_result(result: dict[str, Any]) -> str:
    output = [
        f"# Worklog Sync: {result['project_id']}",
        "",
        f"- direction: `{result['direction']}`",
        f"- dry_run: `{result['dry_run']}`",
    ]
    add_section(output, "Pulled", [sync_item_line(item) for item in result.get("pulled", [])])
    add_section(output, "Published", [sync_item_line(item) for item in result.get("published", [])])
    add_section(output, "Conflicts", [sync_item_line(item) for item in result.get("conflicts", [])])
    add_section(output, "Backend Status", result.get("backend_status") or {})
    output.extend(["", render_pending_project_updates(result.get("pending_project_updates") or [])])
    return "\n".join(output)


def render_after_session_approval(
    log: dict[str, Any],
    publish: dict[str, Any],
    pending: list[dict[str, Any]],
) -> str:
    output = [render_sync_notice(publish), "", render_pending_project_updates(pending)]
    if pending:
        output.extend(
            [
                "",
                "Next: a project approver should draft and approve a project-log rollup for the pending approved session log(s).",
            ]
        )
    else:
        output.extend(["", "Next: the project log already incorporates the approved session logs."])
    return "\n".join(output)


def render_sync_notice(publish: dict[str, Any]) -> str:
    if not publish.get("enabled"):
        return "Shared publishing: not configured."
    published = publish.get("published", [])
    conflicts = publish.get("conflicts", [])
    output = ["Shared publishing:"]
    output.append(f"- published/checked artifacts: {len(published)}")
    output.append(f"- conflicts: {len(conflicts)}")
    for conflict in conflicts:
        output.append(f"- conflict: {sync_item_line(conflict)}")
    return "\n".join(output)


def render_pending_project_updates(pending: list[dict[str, Any]]) -> str:
    output = ["## Pending Project-Log Updates", ""]
    if not pending:
        output.append("- None")
        return "\n".join(output)
    output.append(f"{len(pending)} approved session log(s) are not incorporated into the latest approved project log:")
    for log in pending:
        approved = log.get("approved_at") or log.get("updated_at") or ""
        output.append(f"- `{log['id']}`: {log.get('title') or 'Session log'} (approved_by: `{log.get('approved_by') or ''}`, approved_at: `{approved}`)")
    output.extend(
        [
            "",
            "Next: a project approver should call `worklog_draft_project_log` for this project, author the rollup, and approve the new project log.",
        ]
    )
    return "\n".join(output)


def sync_item_line(item: dict[str, Any]) -> str:
    parts = [str(item.get("type") or "item"), str(item.get("id") or "")]
    if item.get("status"):
        parts.append(f"status={item['status']}")
    if item.get("reason"):
        parts.append(f"reason={item['reason']}")
    if item.get("path"):
        parts.append(f"path={item['path']}")
    return " | ".join(part for part in parts if part)


def normalize_template(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback_sections = template_sections(fallback)
    raw_sections = source.get("sections", fallback_sections)
    if isinstance(raw_sections, dict):
        raw_sections = [
            {"key": key, **(item if isinstance(item, dict) else {"title": str(item)})}
            for key, item in raw_sections.items()
        ]
    if not isinstance(raw_sections, list) or not raw_sections:
        raw_sections = fallback_sections
    sections = []
    for item in raw_sections:
        if isinstance(item, str):
            section = {"key": section_key(item), "title": item, "kind": "list"}
        elif isinstance(item, dict):
            key = section_key(item.get("key") or item.get("title") or item.get("name") or "section")
            section = {
                "key": key,
                "title": str(item.get("title") or item.get("name") or titleize(key)),
                "kind": str(item.get("kind") or "list"),
            }
            for optional_key in ("description", "draft_from", "rollup_from"):
                if item.get(optional_key):
                    section[optional_key] = str(item[optional_key])
        else:
            continue
        sections.append(section)
    if not sections:
        sections = fallback_sections
    return {
        "name": str(source.get("name") or fallback.get("name") or "Worklog Template"),
        "description": str(source.get("description") or fallback.get("description") or ""),
        "sections": sections,
    }


def template_sections(template: Any) -> list[dict[str, str]]:
    normalized = normalize_template_no_recursion(template)
    return normalized["sections"]


def normalize_template_no_recursion(template: Any) -> dict[str, Any]:
    source = template if isinstance(template, dict) else {}
    raw_sections = source.get("sections") if isinstance(source.get("sections"), list) else []
    sections = []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        key = section_key(item.get("key") or item.get("title") or "section")
        section = {
            "key": key,
            "title": str(item.get("title") or titleize(key)),
            "kind": str(item.get("kind") or "list"),
        }
        for optional_key in ("description", "draft_from", "rollup_from"):
            if item.get(optional_key):
                section[optional_key] = str(item[optional_key])
        sections.append(section)
    return {
        "name": str(source.get("name") or "Worklog Template"),
        "description": str(source.get("description") or ""),
        "sections": sections,
    }


def default_project_settings(project_id: str | None) -> dict[str, Any]:
    return {
        "id": project_id or "default",
        "project_id": project_id,
        "project_nature": "unconfigured",
        "permissions": normalize_permission_policy(None),
        "session_log_template": normalize_template(
            DEFAULT_SESSION_TEMPLATE,
            fallback=DEFAULT_SESSION_TEMPLATE,
        ),
        "project_log_template": normalize_template(
            DEFAULT_PROJECT_TEMPLATE,
            fallback=DEFAULT_PROJECT_TEMPLATE,
        ),
    }


def template_authoring_brief(
    project_nature: str | None,
    project_description: str | None,
) -> dict[str, Any]:
    questions = [
        "What kind of project is this (for example legal, medical, engineering, research, clinical operations, education, finance, or something else)?",
        "Who will use the logs later, and what will they need to resume safely?",
        "What facts should persist at the project level versus remain only in a session log?",
        "Are there compliance, privacy, safety, privilege, or evidence/source-tracking requirements?",
        "What sections would make the project log feel natural for this work?",
    ]
    if project_nature:
        questions.insert(1, f"Given the stated project nature `{project_nature}`, what sections match this field's normal workflow?")
    if project_description:
        questions.insert(1, "Based on the project description, propose concise session and project log sections tailored to this work.")
    return {
        **TEMPLATE_AUTHORING_GUIDANCE,
        "project_nature": project_nature,
        "project_description": project_description,
        "questions": questions,
    }


def require_configured_template(template: dict[str, Any], label: str) -> None:
    if not template_sections(template):
        raise UserError(
            f"No user-approved {label} is configured for this project. Start project setup, "
            "have the assistant propose templates from the project nature, refine them with "
            "the user, then call worklog_set_project_templates."
        )


def draft_sections(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for definition in template_sections(template):
        source = definition.get("draft_from") or definition["key"]
        value = values.get(source, values.get(definition["key"], []))
        sections[definition["key"]] = normalize_section_value(value, kind=definition.get("kind"))
    return sections


def empty_sections(template: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for definition in template_sections(template):
        sections[definition["key"]] = "" if definition.get("kind") == "text" else []
    return sections


def sections_from_log(log: dict[str, Any]) -> dict[str, Any]:
    sections = log.get("sections")
    if isinstance(sections, dict):
        return sections
    log["sections"] = {}
    return log["sections"]


def normalize_section_value(value: Any, kind: str | None = None) -> Any:
    if kind == "text":
        if isinstance(value, list):
            return "\n".join(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, dict):
            return json.dumps(value, indent=2, sort_keys=True)
        return str(value).strip()
    if isinstance(value, dict):
        return value
    return text_list(value)


def copy_section_value(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def section_has_content(value: Any) -> bool:
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    if isinstance(value, dict):
        return bool(value)
    return bool(str(value).strip())


def section_key(value: Any) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return key or "section"


def titleize(key: str) -> str:
    return " ".join(part.capitalize() for part in section_key(key).split("_"))


def project_index(db: Store) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def item(project_id: str) -> dict[str, Any]:
        if project_id not in result:
            result[project_id] = {
                "project_id": project_id,
                "counts": {
                    "project_settings": 0,
                    "sessions": 0,
                    "session_logs": 0,
                    "approved_session_logs": 0,
                    "project_logs": 0,
                    "approved_project_logs": 0,
                },
            }
        return result[project_id]

    for settings in db.list("project_settings"):
        if settings.get("project_id"):
            project = item(settings["project_id"])
            project["counts"]["project_settings"] += 1
            project["project_nature"] = settings.get("project_nature")
            if sharing_enabled(settings):
                project["sharing_backend"] = settings.get("sharing", {}).get("backend")
            project["session_log_template"] = (
                settings.get("session_log_template") or {}
            ).get("name")
            project["project_log_template"] = (
                settings.get("project_log_template") or {}
            ).get("name")
    for session in db.list("sessions"):
        if session.get("project_id"):
            item(session["project_id"])["counts"]["sessions"] += 1
    for log in db.list("session_logs"):
        if log.get("project_id"):
            counts = item(log["project_id"])["counts"]
            counts["session_logs"] += 1
            if log.get("status") == "approved":
                counts["approved_session_logs"] += 1
    for log in db.list("project_logs"):
        if log.get("project_id"):
            counts = item(log["project_id"])["counts"]
            counts["project_logs"] += 1
            if log.get("status") == "approved":
                counts["approved_project_logs"] += 1
    for project_id, project in result.items():
        pending = pending_session_logs(db, project_id)
        project["pending_project_log_updates"] = len(pending)
    return [result[key] for key in sorted(result)]


def matching_projects(projects: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_tokens = token_set(query)
    if not query_tokens:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    normalized_query = normalize_for_match(query)
    for project in projects:
        haystack = " ".join(
            str(value or "")
            for value in (
                project.get("project_id"),
                project.get("project_nature"),
                project.get("session_log_template"),
                project.get("project_log_template"),
            )
        )
        project_tokens = token_set(haystack)
        score = len(query_tokens & project_tokens)
        project_id = str(project.get("project_id") or "")
        if project_id and normalize_for_match(project_id) in normalized_query:
            score += 10
        if score:
            copy = dict(project)
            copy["match_score"] = score
            scored.append((score, copy))
    scored.sort(key=lambda item: (-item[0], item[1].get("project_id", "")))
    return [project for _, project in scored[:5]]


def project_line(project: dict[str, Any]) -> str:
    counts = ", ".join(
        f"{name}: {value}"
        for name, value in project.get("counts", {}).items()
        if value
    )
    details = []
    if project.get("project_nature"):
        details.append(f"nature: `{project['project_nature']}`")
    if counts:
        details.append(counts)
    if project.get("sharing_backend"):
        details.append(f"shared: `{project['sharing_backend']}`")
    if project.get("pending_project_log_updates"):
        details.append(f"pending_project_log_updates: {project['pending_project_log_updates']}")
    if project.get("match_score"):
        details.append(f"match_score: {project['match_score']}")
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"- `{project['project_id']}`{suffix}"


def token_set(value: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "for",
        "i",
        "id",
        "like",
        "of",
        "project",
        "the",
        "this",
        "to",
        "with",
    }
    return {
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if token and token not in stopwords
    }


def normalize_for_match(value: str) -> str:
    return " ".join(
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if token
    )


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["id"],
        "source": session["source"],
        "project_id": session.get("project_id"),
        "task_id": session.get("task_id"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "event_count": len(session.get("events", [])),
    }


def find_session_file(args: dict[str, Any]) -> Path | None:
    explicit = clean_optional(args.get("session_path"))
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    for name in ("CODEX_SESSION_PATH", "CODEX_TRANSCRIPT_PATH", "CODEX_CONVERSATION_PATH"):
        value = os.environ.get(name)
        if value and Path(value).expanduser().is_file():
            return Path(value).expanduser()
    session_id = current_session_id()
    if session_id:
        found = find_session_by_id(session_id)
        if found:
            return found
    return newest_session_file()


def current_session_id() -> str | None:
    for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TASK_ID", "CODEX_CONVERSATION_ID"):
        value = clean_optional(os.environ.get(name))
        if value:
            return value
    return None


def find_session_by_id(session_id: str) -> Path | None:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    matches = [path for path in root.rglob(f"*{session_id}.jsonl") if path.is_file()]
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def newest_session_file() -> Path | None:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    files = [path for path in root.rglob("*.jsonl") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def session_id_from_file(path: Path) -> str | None:
    uuid_match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        str(path),
        re.IGNORECASE,
    )
    if uuid_match:
        return uuid_match.group(0)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _ in range(25):
                line = handle.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload")
                if isinstance(payload, dict):
                    for key in ("session_id", "thread_id", "conversation_id", "id"):
                        if clean_optional(payload.get(key)):
                            return str(payload[key]).strip()
    except OSError:
        return None
    return None


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith(("{", "[")):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def touch_session_times(session: dict[str, Any]) -> None:
    if session["events"]:
        session["started_at"] = session["events"][0]["at"]
        session["ended_at"] = session["events"][-1]["at"]
    session["updated_at"] = now()


def collect_lines(events: list[dict[str, Any]], needles: tuple[str, ...]) -> list[str]:
    values = []
    for event in events:
        text = event["text"]
        lowered = text.lower()
        if any(needle in lowered for needle in needles):
            values.append(squeeze(text, 220))
    return dedupe(values)[:12]


def collect_questions(events: list[dict[str, Any]]) -> list[str]:
    values = []
    for event in events:
        text = event["text"]
        lowered = text.lower()
        if "?" in text or "question" in lowered or "unclear" in lowered:
            values.append(squeeze(text, 220))
    return dedupe(values)[:10]


def first_text(events: list[dict[str, Any]], speaker: str) -> str | None:
    for event in events:
        if event.get("speaker") == speaker and event.get("text"):
            return event["text"]
    return None


def last_text(events: list[dict[str, Any]], speaker: str) -> str | None:
    for event in reversed(events):
        if event.get("speaker") == speaker and event.get("text"):
            return event["text"]
    return None


def title_from_text(text: str | None) -> str | None:
    if not text:
        return None
    title = re.sub(r"\s+", " ", text).strip()
    title = re.sub(r"^(i('|’)d like to|i want to|please|can you|could you)\s+", "", title, flags=re.I)
    if not title:
        return None
    return squeeze(title[:1].upper() + title[1:], 80)


def require_confirmation(args: dict[str, Any], action: str) -> None:
    if args.get("confirmed_by_user") is not True:
        raise UserError(f"Cannot {action} without confirmed_by_user=true.")
    quote = str(args.get("confirmation_quote") or "").strip()
    if len(quote) < 2:
        raise UserError(f"Cannot {action} without confirmation_quote.")
    lowered = quote.lower()
    approved = any(
        token in lowered
        for token in (
            "approve",
            "approved",
            "save",
            "saved",
            "finalize",
            "finalise",
            "commit",
            "yes",
            "looks good",
            "ship it",
            "go with",
            "use ",
            "choose ",
            "select ",
            "selected ",
            "configure sharing",
        )
    )
    if not approved:
        raise UserError(f"Cannot {action}; confirmation_quote must clearly approve the change.")


def unique_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for item in items:
        key = item["id"]
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        text = str(value).strip()
        key = re.sub(r"\s+", " ", text.lower())
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    return [str(value).strip()]


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(flatten_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            if key in value:
                return flatten_text(value[key])
    return str(value)


def clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clean_required(args: dict[str, Any], name: str) -> str:
    value = clean_optional(args.get(name))
    if not value:
        raise UserError(f"Missing required argument `{name}`.")
    return value


def required(args: dict[str, Any], name: str) -> str:
    return clean_required(args, name)


def squeeze(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 14)].rstrip() + " ... <trimmed>"


def strip_terminal_punctuation(text: str) -> str:
    return text.rstrip().rstrip(".!?")


def stable_event_id(session_id: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{index}:{text}".encode("utf-8")).hexdigest()[:12]
    return f"event_{digest}"


def file_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return token or "item"


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def lines(*parts: str) -> str:
    return "\n".join(parts)


class UserError(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
