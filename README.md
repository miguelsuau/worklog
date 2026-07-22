# Worklog

Worklog is a local MCP-backed project context tool for agent-assisted work.

## Why Worklog

Agent work often disappears into long chat transcripts. Useful decisions,
constraints, results, and next steps are mixed with raw commands, tool output,
and back-and-forth, so the signal is easy to lose.

Many agent-assisted projects try to preserve that signal in notes, issue
comments, scratch reports, generated files, and tool-specific project docs (for
example, Codex's `AGENTS.md`, Claude Code's `CLAUDE.md`, or a project
`memory.md`). That context can be valuable, but it is usually informal,
scattered across tools, and only partly reviewed.

Worklog turns that scattered context into a structured, human-reviewed project
record. Source events stay local by default, each session becomes a
user-approved session log, and project logs are updated only from approved
session logs. Future agents can resume from the approved record without
treating an unreviewed transcript or ad hoc note as truth.

Worklog is built around four core concepts:

- Source events are the raw captured activity from a work session and stay local by default.
- Session logs are human-reviewed summaries of one work session.
- Project logs are approved, living project-level summaries updated from approved session logs.
- Resume context is generated from approved project logs and recent approved session logs.

Worklog does not ship predefined legal, medical, engineering, or research templates. The assistant proposes a structure from the nature of the project, then stores only the user-approved templates.

## Private Beta

The implementation lives once in `src/worklog`. The Claude and Codex packages
are thin wrappers; `scripts/build_packages.sh` copies the shared source into
each package's ignored `lib/` directory before installation.

```text
src/worklog/
  mcp_server.py
```

The host-specific packages are:

```text
claude/worklog/
  SKILL.md
  .claude-plugin/plugin.json
  .mcp.json
  scripts/worklog_mcp_server.py

plugins/worklog/
  .codex-plugin/plugin.json
  .mcp.json
  skills/worklog/SKILL.md
  scripts/worklog_mcp_server.py
```

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

Use:

```text
$worklog
```

## Beta Testing Notes

Worklog stores reviewed logs locally, and shared projects may publish approved logs to a shared folder or repository. For sensitive work, confirm the storage location and access permissions before use.

When reporting bugs, include:

- agent app and version
- operating system
- command or prompt used
- expected behavior
- actual behavior
- whether the project was local-only or shared
