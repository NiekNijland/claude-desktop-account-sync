import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import plistlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

path = str(Path(__file__).resolve().parent.parent / "claude-account-sync")
loader = importlib.machinery.SourceFileLoader("claude_account_sync", path)
spec = importlib.util.spec_from_loader("claude_account_sync", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

ORG = "org-1"


def tasks(*ids, **extra):
    return json.dumps({"scheduledTasks": [{"id": i, **extra} for i in ids]})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "claude-code-sessions"
        self.trash = self.root.parent / "claude-code-sessions-sync-trash"
        self.a = self.root / "account-a" / ORG
        self.b = self.root / "account-b" / ORG
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)
        out = contextlib.redirect_stdout(io.StringIO())
        err = contextlib.redirect_stderr(io.StringIO())
        out.__enter__(), err.__enter__()
        self.addCleanup(out.__exit__, None, None, None)
        self.addCleanup(err.__exit__, None, None, None)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        mod.Syncer(self.root, **kw).run()

    def write(self, path, text, mtime=1000):
        path.write_text(text)
        os.utime(path, (mtime, mtime))

    def ids(self, d):
        return {t["id"] for t in json.loads((d / mod.SCHEDULES).read_text())["scheduledTasks"]}

    def trashed(self):
        return list(self.trash.iterdir()) if self.trash.exists() else []

    def test_newest_session_wins_and_overwritten_one_is_backed_up(self):
        self.write(self.a / "local_1.json", "old", 1000)
        self.write(self.b / "local_1.json", "new", 2000)
        self.sync()
        self.assertEqual((self.a / "local_1.json").read_text(), "new")
        self.assertEqual((self.a / "local_1.json").stat().st_mtime, 2000)
        self.assertEqual(len(self.trashed()), 1)

    def test_missing_session_is_copied(self):
        self.write(self.a / "local_2.json", "x")
        self.sync()
        self.assertEqual((self.b / "local_2.json").read_text(), "x")

    def test_recently_written_session_is_left_for_the_next_round(self):
        (self.a / "local_2.json").write_text("busy")
        self.sync()
        self.assertFalse((self.b / "local_2.json").exists())

    def test_deletion_propagates_and_is_trashed_without_collisions(self):
        self.write(self.a / "local_3.json", "x")
        self.write(self.b / "local_3.json", "x")
        self.write(self.a / "deleted_3", "")
        self.sync()
        self.assertFalse((self.b / "local_3.json").exists())
        self.assertTrue((self.b / "deleted_3").exists())
        self.assertEqual(len(self.trashed()), 2)

    def test_dry_run_changes_nothing(self):
        self.write(self.a / "local_4.json", "x")
        self.write(self.a / mod.SCHEDULES, tasks("a"))
        self.sync(dry_run=True, schedules=True)
        self.assertFalse((self.b / "local_4.json").exists())
        self.assertFalse((self.b / mod.SCHEDULES).exists())

    def test_single_account_does_nothing(self):
        shutil.rmtree(self.b.parent)
        self.write(self.a / "local_5.json", "x")
        self.sync(schedules=True)

    def test_schedules_are_off_by_default(self):
        self.write(self.a / mod.SCHEDULES, tasks("a"))
        self.sync()
        self.assertFalse((self.b / mod.SCHEDULES).exists())

    def test_schedules_newest_wins_and_backs_up(self):
        self.write(self.a / mod.SCHEDULES, tasks("old"), 1000)
        self.write(self.b / mod.SCHEDULES, tasks("new"), 2000)
        self.sync(schedules=True)
        self.assertEqual(self.ids(self.a), {"new"})
        self.assertEqual(len(self.trashed()), 1)

    def test_empty_schedule_list_never_wipes_populated_ones(self):
        self.write(self.a / mod.SCHEDULES, tasks("keep"), 1000)
        self.write(self.b / mod.SCHEDULES, tasks(), 2000)
        self.sync(schedules=True)
        self.assertEqual(self.ids(self.b), {"keep"})

    def test_corrupt_schedule_file_is_not_propagated(self):
        self.write(self.a / mod.SCHEDULES, tasks("good"), 1000)
        self.write(self.b / mod.SCHEDULES, '{"sched', 2000)
        self.sync(schedules=True)
        self.assertEqual(self.ids(self.a), {"good"})
        self.assertEqual(self.ids(self.b), {"good"})

    def test_union_keeps_tasks_from_both_and_survives_mixed_types(self):
        self.write(self.a / mod.SCHEDULES, tasks("a", lastRunAt=5), 1000)
        self.write(self.b / mod.SCHEDULES, tasks("b", "a", lastRunAt="2026-01-01"), 2000)
        self.sync(union=True)
        for d in (self.a, self.b):
            self.assertEqual(self.ids(d), {"a", "b"})

    def test_old_trash_is_pruned(self):
        self.trash.mkdir()
        old = self.trash / f"{time.time_ns() - 40 * 86400 * 10**9}_x_local_1.json"
        old.write_text("x")
        self.sync()
        self.assertFalse(old.exists())


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        patches = [
            mock.patch.object(mod, "AGENT_PLIST", base / "agent.plist"),
            mock.patch.object(mod, "INSTALLED_BIN", base / "bin" / "claude-account-sync"),
            mock.patch.object(mod, "launchctl", return_value=mock.Mock(returncode=0, stderr="")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.base = base

    def test_install_writes_plist_and_script(self):
        with contextlib.redirect_stdout(io.StringIO()):
            mod.install(Path("/x"), schedules=True, dry_run=False)
        plist = plistlib.loads(mod.AGENT_PLIST.read_bytes())
        self.assertEqual(plist["ProgramArguments"][1:], [str(mod.INSTALLED_BIN), "--root", "/x", "--schedules"])
        self.assertIn("--schedules", plist["ProgramArguments"])
        self.assertTrue(mod.INSTALLED_BIN.exists())

    def test_dry_run_writes_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            mod.install(Path("/x"), schedules=False, dry_run=True)
            mod.uninstall(dry_run=True)
        self.assertFalse(mod.AGENT_PLIST.exists())
        self.assertFalse(mod.INSTALLED_BIN.exists())


if __name__ == "__main__":
    unittest.main()
