# Worklog Plugin

Worklog turns captured work sessions into reviewed session logs, then rolls approved session logs into a living project log that future agents can use to resume with context.

This plugin is designed for a private beta. Drafts stay local. Shared projects publish only approved artifacts.

The Worklog implementation lives once in `src/worklog` at the repo root. Claude
and Codex packages use thin launcher scripts. Run `./scripts/build_packages.sh`
from the repo root before installing a package directly; it copies the shared
source into each package's ignored `lib/` directory.

## Claude

Install `../../claude/worklog` into Claude's skills directory to use the short slash command:

```bash
./install-claude.sh
```

Then restart Claude Code or run `/reload-plugins`, and invoke:

```text
/worklog
```

## Codex

Codex can install this plugin from the repo marketplace:

```bash
./scripts/build_packages.sh
codex plugin marketplace add /path/to/worklog
codex plugin add worklog@worklog-beta
```

Invoke with:

```text
$worklog
```

## Data

Claude stores Worklog data in the plugin data directory through `${CLAUDE_PLUGIN_DATA}/store`.

Codex uses `~/.worklog/store` unless `WORKLOG_STORE` is set.
