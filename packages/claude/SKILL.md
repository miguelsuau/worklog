---
name: worklog
description: Use Worklog when the user invokes /worklog, asks to use Worklog, or wants to track, resume, or review project work; create user-approved templates, capture session logs, author project-log rollups, and generate resume context using the installed Worklog MCP tools.
---

# Worklog

Use the `worklog` MCP server exposed by the installed Worklog plugin as the factual backend. Worklog keeps a reviewed local work history:

Worklog turns scattered agent-work context into a reviewed project record. Use
it when important decisions, results, next steps, or constraints should survive
beyond the current transcript. Do not treat raw transcripts or ad hoc notes as
reviewed truth; Worklog state becomes trustworthy only after user approval.

- Source events are local captured material. Keep them hidden unless the user explicitly asks for raw evidence.
- A session log is the human-reviewed summary of one work session, using the user's chosen template.
- A project log is the approved, living project-level summary updated from approved session logs, using the user's chosen template.
- Resume context is generated from the latest approved project log plus recent approved session logs.
- Shared projects keep drafts local and publish only approved artifacts to the selected shared backend.

Use Worklog terms consistently: "session log", "project log", "source events", and "resume context".

## Explicit Invocation

Treat any of these as an explicit request to use Worklog:

- `$worklog`
- `@worklog`
- `/worklog`
- `\worklog`
- "use Worklog"
- "load Worklog"
- "track this in Worklog"

Claude can invoke this skill with `/worklog`. `$worklog`, `@worklog`, and `\worklog` are command-like conventions from other agent hosts; if the host passes any of them through in the prompt, treat it exactly like an explicit Worklog invocation.

When the invocation includes a task after the command, strip the invocation marker mentally and do the user's task, but use Worklog first:

1. Call `worklog_list_projects`.
2. If an existing project clearly matches the task, use that exact `project_id`.
3. If the project is unclear, ask the user which project this belongs to before creating a new one.
4. If a matching project has approved context, call `worklog_resume_context` and use it before doing the work.
5. Complete the user's requested work normally.
6. At the end of the work session, run the Session Log Review workflow unless the user says not to.

If the user invokes Worklog only to inspect or manage logs, follow the relevant setup, resume, session-log, or project-log workflow instead of doing unrelated work.

## Project Setup

When a project starts, help the user decide how the session log and project log should look. Do not assume a default format is right, and do not rely on predefined Worklog templates. The assistant should propose the structure from the nature of the project and refine it with the user.

1. Before starting setup, call `worklog_list_projects`.
2. If an existing project clearly matches the user's request, use that exact `project_id`; do not start a new project.
3. If existing projects are present but the match is unclear, show the likely candidates and ask whether this belongs to one of them.
4. Start a new project only when there are no plausible existing projects or the user explicitly confirms this is new.
5. Ask about the nature of the project when it is not obvious. Useful examples include legal matter, medical/clinical project, engineering project, research project, teaching project, finance project, or consulting engagement.
6. Call `worklog_start_project` or `worklog_recommend_templates` to get the neutral authoring brief and storage contract. If calling `worklog_start_project` after the user confirms this is new, pass `confirmed_new_project: true`.
7. Ask whether the project is local-only or shared with team members. If shared, follow the storage workflow: first call `worklog_configure_project_sharing` without `storage_provider` so Worklog can suggest providers, then ask the user to choose one provider. The first provider list should include mounted-folder options such as Google Drive, Dropbox, OneDrive, network folders, local folders, and Docker mounts, plus Git options such as GitHub, GitLab, Bitbucket, and local Git repositories. After the user chooses the provider, call it again with `storage_provider`; Worklog will build or inspect the provider backend/sync surface and then return path options for that configured provider. Ask the user to choose one path only after that provider setup step has run. If Worklog returns `sharing_setup_stage: verify_storage_provider_connection`, use an authenticated MCP/API connector first when it exposes the needed verification operation; if it does not, ask the user to authorize the specific browser, provider API, or desktop sync action needed to verify the cloud-side location.
8. As the assistant, propose a session log template and project log template that fit the user's actual project.
9. Work with the user to refine section names, ordering, and how the assistant should author project-level rollups from reviewed session logs.
10. Call `worklog_set_project_templates` only after explicit user approval. Pass the exact approved `session_log_template`, `project_log_template`, `confirmed_by_user: true`, and the user's exact `confirmation_quote`.
11. For shared projects, do not combine provider selection and path selection. The workflow is: suggest providers, user selects provider, Worklog builds/inspects that provider backend, Worklog suggests paths for that provider, user selects path, then configure sharing. If the storage provider is missing, ask a direct provider question even if the user has approved a path. Never infer the provider from an example path, mounted folder name, or "path looks good." When the user selects a path in response to Worklog's path prompt, treat that selection as the sharing confirmation: call the tool immediately with `confirmed_by_user: true` and `confirmation_quote` set to the user's exact path-selection message. Do not ask the user to repeat a boilerplate confirmation sentence.

Templates are flexible. They may contain any section names and any structure the user wants. Worklog stores the approved templates; it does not invent them. The update tools accept a `sections` object or a single `section` plus `items`/`text`, so use those for custom formats.

For example, the assistant might propose one structure for lawyers, another for doctors, another for engineers, and another for researchers. These are assistant-authored proposals, not Worklog presets.

## Shared Projects

Use shared projects when the user wants multiple team members to contribute Worklog state. The central rule is: drafts stay local; only approved artifacts are shared.

Worklog policy:

- Contributors can create drafts and approve/publish their own session logs.
- Project approvers can approve/publish project logs.
- Maintainers can change templates, sharing configuration, and permissions.

Backend permissions are configured by the agent when possible. For GitHub/GitLab/Bitbucket or Git-backed work, configure repo access and branch/code-owner protections when available. For Google Drive/Dropbox/OneDrive, a local mounted folder check is not enough: when Worklog asks for provider verification, use an authenticated MCP/API connector first when it exposes the needed operation; if it does not, ask the user to authorize the matching browser, provider API, or desktop sync action, verify the cloud-side Worklog location or uploaded artifacts, then call `worklog_configure_project_sharing` again with `storage_provider_verification`. For network folders, the provider-selection tool call returns `storage_provider_setup`; if setup is not ready, use the appropriate connector or desktop sync/mount setup before asking for a Worklog path. For mounted-folder providers, Worklog verifies the local mount and directory permissions; do not claim it authenticated with the cloud provider or verified sync unless a connector or provider-specific check actually did that. For Docker, use `shared_directory` only when the chosen path is mounted and writable to the Worklog process. For connector-backed systems, use `connector_payload` and publish the approved payloads with the relevant connector.

When adding members to an existing shared project, call `worklog_update_project_members`. Worklog may return `member_setup_stage: apply_backend_permissions`; in that case, the agent should complete the backend permission work through every available provider surface before asking the user to do it manually. Use an authenticated MCP/API connector first when it exposes the needed folder/repository permission operation. If the exposed connector cannot apply that permission, the agent should use the next available provider surface itself: browser UI, provider API, repository/admin tooling, desktop sync tooling, or another authenticated connector. If the user has already approved the member or sharing change, do not ask them to re-approve that same Worklog change; ask only for a specific missing authorization step that the agent cannot complete alone, such as connector permission, browser permission, API permission, or provider sign-in. If provider tooling opens a sign-in page, ask the user to sign in only when that authenticated surface is needed for the agent to continue. Ask the user to apply permissions manually only when no available connector, API, browser, admin, desktop, or provider surface can complete the backend permission change. Do not say the member has been added to the shared project until backend access is applied or verified and the tool succeeds with `backend_permission_verification`. For Google Drive folders, if a connector path cannot apply folder ACLs directly, use browser, Google Drive API/provider tooling, or user-applied manual sharing rather than claiming folder sharing is impossible.

Use these tools:

- `worklog_configure_project_sharing` to run the shared-storage workflow: get provider recommendations first, then path recommendations for the selected provider, then create/join a shared project after the user confirms both.
- `worklog_show_project_sharing` to inspect policy, backend status, and pending project-log updates.
- `worklog_update_project_members` to add, replace, or remove contributors, project approvers, and maintainers after setup; for shared projects, use it to coordinate backend permission application too.
- `worklog_sync_project` to pull approved member artifacts and publish local approved artifacts. Use `dry_run: true` before risky backend changes.

If a contributor approves a session log but lacks project-log approval permission, do not update the project log. The approved session log becomes a pending project-log update. A project approver should later draft and approve a project-log rollup.

## Resume Workflow

1. If the user asks to load, resume, or consult Worklog context and the `project_id` is unclear, call `worklog_list_projects`.
2. If one project clearly matches the request, use that exact `project_id`; if not, ask which existing project they mean.
3. If the project is shared, call `worklog_sync_project` with `direction: "pull"` before resuming unless the user asks for local-only state.
4. Call `worklog_resume_context` with the exact `project_id`.
5. Summarize the returned context in chat and then use it as task context. If there are pending project-log updates and the current user is a project approver, call that out clearly.

## Session Log Review

1. Capture or import source material with `worklog_capture_session`, `worklog_import_events`, or `worklog_add_event` when needed.
2. If project templates are missing or the user wants to change the format, run the Project Setup workflow first.
3. Call `worklog_draft_session_log`.
4. Show the rendered session log and ask the user whether to edit or approve it.
5. Use `worklog_edit_session_log` for requested edits. Prefer the flexible `sections` object for custom formats.
6. Call `worklog_approve_session_log` only after explicit user approval. Pass `confirmed_by_user: true` and the user's exact `confirmation_quote`.

Do not approve a session log without a `project_id`. Ask the user or edit the draft to add one.

For shared projects, approved session logs may be published even when the current user is not a project approver. Do not draft or approve a project log unless a project approver is reviewing it.

## Project Log Review

1. After a session log is approved, call `worklog_draft_project_log` with the approved `session_log_id` or the project `project_id` and no sections. With a shared project, passing `project_id` lets Worklog surface all pending approved session logs that are not yet incorporated.
2. As the assistant, author the project-log rollup yourself. Use judgment to preserve useful previous project state, incorporate the new approved session log, and place facts only in sections where they semantically belong.
3. Call `worklog_draft_project_log` again with the same `session_log_id` or `session_log_ids` plus the authored `sections` or `fields`. Worklog stores this as a draft; it should not mechanically generate the rollup.
4. Show the rendered project log draft and ask whether the user wants edits or approval.
5. Use `worklog_edit_project_log` for requested edits. Prefer the flexible `sections` object for custom formats.
6. Call `worklog_approve_project_log` only after explicit user approval. Pass `confirmed_by_user: true` and the user's exact `confirmation_quote`.

For shared projects, only a project approver should approve the project log. If approval fails because a newer approved project log exists, sync, review the new base, and draft a fresh project-log rollup.

## Important Behavior

- Never claim a session log or project log is approved until the approval tool succeeds.
- Do not infer approval from silence or general positivity.
- Treat resume context as reviewed Worklog state, not as raw event history.
- Keep source events local and out of chat unless the user asks to inspect them.
- The user's template is authoritative. Preserve their section names and format.
- If no user-approved template is configured, do not draft logs yet. Start project setup first.
- Never create or set up a new project before checking whether the work belongs to an existing Worklog project.
- Project-log rollups must be authored by the assistant from reviewed Worklog state. Do not expect Worklog to automatically classify or copy session-log content into project-log sections.
- If a prompt starts with `/worklog` or `\worklog`, do not treat it as a shell command or path. Treat it as an explicit request to use Worklog.
