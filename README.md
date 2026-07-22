# Worklog

Worklog is a local MCP-backed project context tool for agent-assisted work.

## Why Worklog

Agent work often disappears into long chat transcripts. Useful decisions,
constraints, results, and next steps are mixed with raw commands, tool output,
and back-and-forth, so the important context is easy to lose.

Many agent-assisted projects try to preserve that context in notes, issue
comments, scratch reports, generated files, and tool-specific project docs (for
example, Codex's `AGENTS.md`, Claude Code's `CLAUDE.md`, or a project
`memory.md`). Those artifacts can be valuable, but they are usually informal,
scattered across tools, and only partly reviewed. This is the reviewed context
gap: important context exists, but it is not yet a reliable project record.

Worklog closes that gap by turning scattered context into a structured,
human-reviewed project record. Source events stay local by default, each
session becomes a user-approved session log, and project logs are updated only
from approved session logs. Future agents can resume from the approved record
without treating an unreviewed transcript or ad hoc note as truth.

On teams, the reviewed context gap shows up across people. Teams usually share
the outputs of agent work, such as code, documents, notebooks, tickets, or
reports, but the context behind those outputs often stays in one person's local
chat: what was tried, why decisions were made, which constraints were
discovered, and what should happen next. Shared Worklog projects turn that
hidden context into a reviewed team record, making handoffs easier, reducing
repeated discovery, and giving future agents the same approved facts without
publishing raw transcripts or unfinished drafts.

Worklog is built around four core concepts:

- Source events are the raw captured activity from a work session and stay local by default.
- Session logs are human-reviewed summaries of one work session.
- Project logs are approved, living project-level summaries updated from approved session logs.
- Resume context is generated from approved project logs and recent approved session logs.

Worklog does not ship predefined legal, medical, engineering, or research templates. The assistant proposes a structure from the nature of the project, then stores only the user-approved templates.

## Sharing

Worklog can be used for local-only projects or shared projects. The sharing
rule is deliberately conservative: draft session logs and draft project logs
stay local, and only approved artifacts are published to the selected shared
backend.

For shared projects, the assistant should guide setup in stages:

1. Ask whether the project should be shared.
2. Ask the user to choose a storage provider, such as Google Drive, Dropbox,
   OneDrive, a network folder, a Docker-mounted folder, GitHub, GitLab,
   Bitbucket, or a local Git repository.
3. Build or inspect the selected provider setup.
4. Suggest provider-specific paths or repositories.
5. Configure the project only after the user confirms the provider and
   location.

Worklog also separates project roles:

- Contributors can create drafts and approve/publish their own session logs.
- Project approvers can approve/publish project logs.
- Maintainers can change templates, sharing configuration, and permissions.

Worklog stores its own membership policy, but shared projects also need matching
backend permissions. Agents should apply provider permissions directly when an
authenticated connector, API, browser, admin tool, or desktop sync surface can
do it. If no available tool can complete the backend permission change, the
agent should ask the user to apply that provider permission manually.

## Private Beta

The implementation lives once in `src/worklog`. The skill instructions live
once in `skill/worklog.body.md`; `scripts/generate_skills.py` renders the
host-specific Claude and Codex `SKILL.md` files with only frontmatter and
invocation wording changed. The Claude and Codex packages are thin wrappers;
`scripts/build_packages.sh` copies the shared source into each package's
ignored `lib/` directory before installation.

```text
src/worklog/
  mcp_server.py

skill/
  worklog.body.md
```

The host-specific packages are:

```text
packages/claude/
  SKILL.md
  .claude-plugin/plugin.json
  .mcp.json
  scripts/worklog_mcp_server.py

packages/codex/
  .codex-plugin/plugin.json
  .mcp.json
  skills/worklog/SKILL.md
  scripts/worklog_mcp_server.py
```

The package folders differ internally because Codex expects skills under
`skills/<name>/`, while the Claude beta package is installed as a single root
skill.

Claude is the first supported beta target. Codex packaging is included so it can be tested from the same source.

## Install For Claude

From this repo:

```bash
./install-claude.sh
```

Then restart Claude Code or run:

```text
/reload-plugins
```

Use:

```text
/worklog
```

## Install For Codex

From Codex:

```bash
./scripts/build_packages.sh
codex plugin marketplace add /path/to/worklog
codex plugin add worklog@worklog-beta
```

Use either:

```text
$worklog
/worklog
```

## Beta Testing Notes

Worklog stores reviewed logs locally, and shared projects may publish approved logs to a shared folder or repository. For sensitive work, confirm the storage location and access permissions before use.

When reporting bugs, include:

- agent host and version, such as Claude Code, Codex, or ChatGPT
- model used
- operating system and relevant local setup, especially for paths, permissions, sync clients, or containers
- command or prompt used
- expected behavior
- actual behavior
- whether the project was local-only or shared, including the storage provider for shared projects
