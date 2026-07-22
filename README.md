# Worklog

Worklog is a local MCP-backed project context tool for agent-assisted work.

It keeps a deliberately small model:

- Source events are raw captured material and stay local by default.
- Session logs are human-reviewed summaries of one work session.
- Project logs are approved, living project-level state.
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

Do not use Worklog for sensitive client, patient, legal, or confidential material unless you understand where your local and shared Worklog files are stored.

When reporting bugs, include:

- agent app and version
- operating system
- command or prompt used
- expected behavior
- actual behavior
- whether the project was local-only or shared