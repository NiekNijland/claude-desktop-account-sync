# claude-desktop-account-sync

Keep the Claude desktop app's **Code sessions** (and optionally **scheduled tasks**) in step between several accounts on one Mac.

The desktop app stores the sidebar list per account, so a session started under account A is missing under account B, even though the transcripts in `~/.claude/projects` are shared. Scheduled tasks have the same problem: the task files are shared, but each account has its own list of schedules.

This script syncs the per-account metadata in the background. One Python file, no dependencies, macOS only.

> **Unofficial.** It reads and writes the app's undocumented files and may break after an app update. Quit nothing, but keep a backup of `~/Library/Application Support/Claude/claude-code-sessions` before the first run.

## How it works

For every organisation that more than one account belongs to, the folders `claude-code-sessions/<account>/<org>/` are synced with each other:

- Session metadata (`local_*.json`): the newest file wins, missing files are copied.
- Deleted sessions (`deleted_*` markers) are propagated; the metadata they remove goes to `claude-code-sessions-sync-trash/`, not to the void.
- With `--schedules`: `scheduled-tasks.json`, newest file wins. The file that gets overwritten is first copied to the trash folder.
- With `--union`: scheduled tasks are merged by id instead (the one with the newest `lastRunAt` wins per task). Use it once to combine lists that have drifted apart. A task deleted on one side comes back, so do not leave it on.

Symlinking the folders together does not work: the app fails with `ENOTDIR` and keeps sessions only in memory.

## Install

```bash
git clone https://github.com/NiekNijland/claude-desktop-account-sync.git
cd claude-desktop-account-sync
./claude-account-sync --dry-run          # see what would change
./claude-account-sync install --schedules
```

`install` copies the script to `~/.local/bin/claude-account-sync` and loads a launch agent that runs it every 2 minutes and when the session folders change. Drop `--schedules` to sync sessions only. After adding a new account, run `install` again so its folder is watched (the 2-minute interval picks it up anyway).

Log: `~/Library/Logs/claude-account-sync.log`. Remove everything with `./claude-account-sync uninstall`.

If your schedule lists already differ, run `./claude-account-sync --union` once before installing.

## Options

| Flag | Effect |
| --- | --- |
| `-n`, `--dry-run` | Print what would change, change nothing |
| `-v`, `--verbose` | Print every action |
| `--schedules` | Also sync `scheduled-tasks.json` |
| `--union` | Merge scheduled tasks by id (implies `--schedules`) |
| `--root PATH` | Session folder, or `CLAUDE_SESSIONS_ROOT` |

## Caveats

- Not tested while two accounts' apps run at the same time. Only one desktop app runs at a time, so this should not matter.
- The app may only read `scheduled-tasks.json` at startup, so a task made on one account can show up on the other after an app restart.
- A scheduled task created on both accounts at the same moment loses one of them (newest file wins).

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT
