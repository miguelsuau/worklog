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
scattered across tools, and only partly reviewed. This leaves a missing project
record: important context exists, but it is not yet reliable, reviewed, or easy
to resume from.

Worklog creates that project record by turning scattered context into a
structured, human-reviewed artifact. Source events stay local by default, each
session becomes a user-approved session log, and project logs are updated only
from approved session logs. Future agents can resume from the approved record
without treating an unreviewed transcript or ad hoc note as truth.

On teams, the missing project record shows up across people. Teams usually
share the outputs of agent work, such as code, documents, notebooks, tickets, or
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

## Install Worklog

Worklog currently supports Claude Cowork and Claude Code. Regular Claude Chat is
not supported in this beta because Worklog depends on a local MCP server, and
Chat does not currently expose that server to the skill.

The easiest install path is to ask Claude Cowork to add Worklog from the public
Worklog Beta plugin marketplace. Open Claude Desktop, switch to Cowork, and
paste:

```text
Please install Worklog from this public GitHub plugin marketplace:
https://github.com/miguelsuau/worklog

Use Claude Desktop's Customize > Plugins flow:
1. Add that repository as a plugin marketplace if it is not already listed.
2. Install Worklog from the Worklog Beta marketplace.
3. Ask me before changing settings or granting permissions.
4. When finished, tell me to start a new Cowork task and type /worklog.
```

After installation, start a new Cowork task and use:

```text
/worklog
```

Claude Code uses its own plugin manager, but it installs from the same
marketplace:

```text
/plugin marketplace add miguelsuau/worklog
/plugin install worklog@worklog-beta
/reload-plugins
```

If `/worklog` says `Unknown skill: worklog` in Claude Chat, switch to Cowork or
Claude Code. If it happens in Cowork or Claude Code, update the Worklog Beta
marketplace, make sure Worklog is installed and enabled in that host, then
restart Claude Desktop or run `/reload-plugins` in Claude Code.

## Repository Layout

The implementation lives once in `src/worklog`. The skill instructions,
metadata, and MCP launcher also have single shared sources. The Claude and
Codex packages are generated wrappers; `scripts/build_packages.sh` renders
host-specific package files and copies the shared source into each package's
`lib/` directory so marketplace installs are self-contained.

```text
src/worklog/
  mcp_server.py

skill/
  worklog.body.md

launcher/
  worklog_mcp_server.py

plugin.metadata.json

scripts/
  generate_package_files.py
  build_packages.sh
  build_claude_plugin_zip.sh
  verify_package.sh
```

Generated package files are committed for easy beta installs.
`scripts/verify_package.sh` fails if generated package files or vendored
package sources drift from their shared sources. The Claude plugin ZIP is built
into `dist/` and is not committed.

The host-specific packages are:

```text
packages/claude/
  .claude-plugin/plugin.json
  .mcp.json
  skills/worklog/SKILL.md
  scripts/worklog_mcp_server.py
  lib/worklog/

packages/claude-code/
  SKILL.md
  .claude-plugin/plugin.json
  .mcp.json
  scripts/worklog_mcp_server.py
  lib/worklog/

packages/codex/
  .codex-plugin/plugin.json
  .mcp.json
  skills/worklog/SKILL.md
  scripts/worklog_mcp_server.py
  lib/worklog/
```

The package folders differ internally because Claude Cowork expects an
installable plugin package, Claude Code's direct installer uses a single root
skill, and Codex expects skills under `skills/<name>/`.

Claude is the first supported beta target. Codex packaging is included so it can be tested from the same source.

## Maintainer Notes

Build the host packages and validate them with:

```bash
./scripts/verify_package.sh
```

For beta distribution, build a standalone Claude Cowork plugin file:

```bash
./scripts/build_claude_plugin_zip.sh
```

This writes:

```text
dist/worklog-claude-plugin.zip
```

The ZIP contains the contents of `packages/claude` at the archive root, including
`.claude-plugin/plugin.json`, `.mcp.json`, `skills/`, `scripts/`, and `lib/`.
Change the shared sources, run the package build, and regenerate the ZIP rather
than editing package files by hand.

Tagged releases whose tag starts with `v` publish this ZIP as a GitHub Release
asset through `.github/workflows/build-claude-plugin.yml`.

To test the Codex package locally:

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

- agent host and version, such as Claude Cowork, Claude Code, Codex, or ChatGPT
- model used
- operating system and relevant local setup, especially for paths, permissions, sync clients, or containers
- command or prompt used
- expected behavior
- actual behavior
- whether the project was local-only or shared, including the storage provider for shared projects
