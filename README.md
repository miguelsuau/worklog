# Worklog

Worklog is a local MCP-backed project context tool for agent-assisted work.

Worklog is built around four core concepts:

- Source events are the raw captured activity from a work session and stay local by default.
- Session logs are human-reviewed summaries of one work session.
- Project logs are approved, living project-level summaries updated from approved session logs.
- Resume context is generated from approved project logs and recent approved session logs.

Worklog does not ship predefined legal, medical, engineering, or research templates. The assistant proposes a structure from the nature of the project, then stores only the user-approved templates.

## Private Beta

This repo contains two self-contained wrappers:

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