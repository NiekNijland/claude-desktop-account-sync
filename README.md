# claude-desktop-account-sync

[![Tests](https://github.com/NiekNijland/claude-desktop-account-sync/actions/workflows/test.yml/badge.svg)](https://github.com/NiekNijland/claude-desktop-account-sync/actions/workflows/test.yml)

Keep the Claude desktop app's **Code sessions** (and optionally **scheduled tasks**) in step between several accounts on one Mac.

The desktop app stores the sidebar list per account, so a session started under account A is missing under account B, even though the transcripts in `~/.claude/projects` are shared. Scheduled tasks have the same problem: the task files are shared, but each account has its own list of schedules.

This script syncs the per-account metadata in the background. One Python file, no dependencies, macOS only.

> **Unofficial.** It reads and writes the app's undocumented files and may break after an app update. You don't need to quit the app, but keep a copy of `~/Library/Application Support/Claude/claude-code-sessions` before the first run.

## How it works

For every organisation that more than one account belongs to, the folders `claude-code-sessions/<account>/<org>/` are synced with each other. Accounts in different organisations are not synced, and with one account nothing happens.

- Session metadata (`local_*.json`): the newest file wins and is copied to the others when its content differs. A file that is not valid JSON is never copied.
- Deleted sessions (`deleted_*` markers) are propagated; the metadata they remove goes to `claude-code-sessions-sync-trash/`.
- With `--schedules`: `scheduled-tasks.json`, newest file wins. An empty or unreadable list never overwrites a populated one.
- With `--union`: scheduled tasks are merged by id instead (the most recent `lastRunAt` wins per task; ties go to the newest file). Use it once to combine lists that have drifted apart. A task deleted on one side comes back, so do not leave it on.
- A deleted session and an overwritten `scheduled-tasks.json` are first copied to the trash folder, and kept for 30 days. Routine session updates are not backed up.
- A session file, or any `scheduled-tasks.json` of the group, modified in the last 2 seconds makes that round skip it, so a half-written file is not copied around.

Symlinking the folders together does not work: the app fails with `ENOTDIR` and keeps sessions only in memory.

## Install

```bash
git clone https://github.com/NiekNijland/claude-desktop-account-sync.git
cd claude-desktop-account-sync
./claude-account-sync --dry-run --schedules    # see what would change
./claude-account-sync --union                  # once, only if your scheduled tasks differ per account
./claude-account-sync install --schedules
```

`install` copies the script to `~/.local/bin/claude-account-sync` and loads a launch agent that runs it every minute. Drop `--schedules` to sync sessions only. New accounts are picked up automatically. After `git pull`, run `install` again to update the installed copy.

Log: `~/Library/Logs/claude-account-sync.log` (emptied when it grows past 1 MB). `./claude-account-sync uninstall` removes the agent and the installed script; the log, the trash folder and the lock file (`claude-code-sessions` sibling `.claude-account-sync.lock`) stay.

## Usage

`claude-account-sync [sync|install|uninstall] [options]`; `sync` is the default.

| Flag | Effect |
| --- | --- |
| `-n`, `--dry-run` | Print what would change, change nothing, not even a lock file (also for `install` and `uninstall`) |
| `-v`, `--verbose` | Also say when there is nothing to sync |
| `--schedules` | Also sync `scheduled-tasks.json` |
| `--union` | One-off merge of scheduled tasks by id (implies `--schedules`, `sync` only) |
| `--root PATH` | Session folder, or set `CLAUDE_SESSIONS_ROOT` |

## Caveats

- The agent and the running app can touch the same file. The 2-second rule and the trash folder limit the damage, but there is no real coordination.
- The app may only read `scheduled-tasks.json` at startup, so a task made on one account can show up on the other after an app restart.
- A scheduled task created on both accounts at the same moment loses one of them (newest file wins).
- Deleting your last scheduled task on one account is undone by the next sync, because an empty list never overwrites a populated one.
- A deleted session id stays deleted: if a session with that id turns up again, it is moved to the trash.
- The launch agent starts the script through its `env python3` shebang with `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`, so any Python 3.9+ on those paths works (Homebrew first, then the system one).

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT
