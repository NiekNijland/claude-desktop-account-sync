import contextlib
import fcntl
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


def J(value):
    return json.dumps({"v": value})


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

    def test_newest_session_wins_without_filling_the_trash(self):
        self.write(self.a / "local_1.json", J("old"), 1000)
        self.write(self.b / "local_1.json", J("new"), 2000)
        self.sync()
        self.assertEqual((self.a / "local_1.json").read_text(), J("new"))
        self.assertEqual((self.a / "local_1.json").stat().st_mtime, 2000)
        self.assertEqual(self.trashed(), [])

    def test_missing_session_is_copied(self):
        self.write(self.a / "local_2.json", J("x"))
        self.sync()
        self.assertEqual((self.b / "local_2.json").read_text(), J("x"))

    def test_recently_written_session_is_left_for_the_next_round(self):
        (self.a / "local_2.json").write_text(J("busy"))
        self.sync()
        self.assertFalse((self.b / "local_2.json").exists())

    def test_deletion_propagates_and_is_trashed_without_collisions(self):
        self.write(self.a / "local_3.json", J("x"))
        self.write(self.b / "local_3.json", J("x"))
        self.write(self.a / "deleted_3", "")
        self.sync()
        self.assertFalse((self.b / "local_3.json").exists())
        self.assertTrue((self.b / "deleted_3").exists())
        self.assertEqual(len(self.trashed()), 2)

    def test_dry_run_changes_nothing(self):
        self.write(self.a / "local_4.json", J("x"))
        self.write(self.a / mod.SCHEDULES, tasks("a"))
        self.sync(dry_run=True, schedules=True)
        self.assertFalse((self.b / "local_4.json").exists())
        self.assertFalse((self.b / mod.SCHEDULES).exists())

    def test_single_account_does_nothing(self):
        shutil.rmtree(self.b.parent)
        self.write(self.a / "local_5.json", J("x"))
        self.sync(schedules=True)
        self.assertEqual(self.trashed(), [])

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
        old, fresh, folder = self.trash / "legacy_name.json", self.trash / "fresh.json", self.trash / "dir"
        for f in (old, fresh):
            f.write_text("x")
        folder.mkdir()
        os.utime(old, (1000, 1000))
        self.sync()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists() and folder.exists())

    def test_recent_corrupt_schedule_file_blocks_the_round(self):
        self.write(self.a / mod.SCHEDULES, tasks("good"), 1000)
        (self.b / mod.SCHEDULES).write_text('{"sched')
        self.sync(schedules=True)
        self.assertEqual((self.b / mod.SCHEDULES).read_text(), '{"sched')

    def test_wrong_shaped_schedule_json_does_not_crash(self):
        self.write(self.a / mod.SCHEDULES, tasks("good"), 1000)
        self.write(self.b / mod.SCHEDULES, '{"scheduledTasks": null}', 2000)
        self.sync(schedules=True)
        self.assertEqual(self.ids(self.b), {"good"})
        self.write(self.b / mod.SCHEDULES, "[]", 3000)
        self.sync(schedules=True)
        self.assertEqual(self.ids(self.b), {"good"})

    def test_sessions_differing_by_less_than_a_second_converge(self):
        self.write(self.a / "local_6.json", J("X"), 1000.0)
        self.write(self.b / "local_6.json", J("Y"), 1000.5)
        self.sync()
        self.assertEqual((self.a / "local_6.json").read_text(), (self.b / "local_6.json").read_text())

    def test_corrupt_session_file_is_not_propagated(self):
        self.write(self.a / "local_7.json", '{"half', 2000)
        self.sync()
        self.assertFalse((self.b / "local_7.json").exists())

    def test_union_compares_last_run_as_time_not_text(self):
        self.write(self.a / mod.SCHEDULES, tasks("t", lastRunAt=9, v="old"), 2000)
        self.write(self.b / mod.SCHEDULES, tasks("t", lastRunAt=10, v="new"), 1000)
        self.sync(union=True)
        merged = json.loads((self.a / mod.SCHEDULES).read_text())["scheduledTasks"]
        self.assertEqual(merged[0]["v"], "new")

    def test_three_accounts_in_one_organisation(self):
        c = self.root / "account-c" / ORG
        c.mkdir(parents=True)
        self.write(c / "local_8.json", J("x"))
        self.sync()
        for d in (self.a, self.b):
            self.assertTrue((d / "local_8.json").exists())

    def test_deleted_session_id_stays_deleted(self):
        self.write(self.a / "deleted_9", "")
        self.write(self.b / "local_9.json", J("back"))
        self.sync()
        self.assertFalse((self.b / "local_9.json").exists())

    def test_failed_write_leaves_no_temp_file(self):
        self.write(self.a / "local_10.json", J("x"))
        with mock.patch.object(mod.os, "replace", side_effect=OSError("boom")):
            self.sync()
        self.assertEqual([p for p in self.b.iterdir() if p.name.endswith(".tmp")], [])

    def test_dry_run_leaves_no_lock_file_and_a_held_lock_stops_a_sync(self):
        self.sync(dry_run=True)
        self.assertFalse((self.root.parent / ".claude-account-sync.lock").exists())
        with open(self.root.parent / ".claude-account-sync.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaises(SystemExit):
                self.sync()


class LogTest(unittest.TestCase):
    def test_log_is_truncated_when_too_big_and_left_alone_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sync.log"
            with mock.patch.object(mod, "LOG_FILE", log), mock.patch.object(mod, "MAX_LOG_BYTES", 10):
                mod.rotate_log()
                log.write_text("short")
                mod.rotate_log()
                self.assertEqual(log.read_text(), "short")
                log.write_text("x" * 11)
                mod.rotate_log()
                self.assertEqual(log.read_text(), "")


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
        self.assertEqual(plist["ProgramArguments"], [str(mod.INSTALLED_BIN), "--root", "/x", "--schedules"])
        self.assertIn("--schedules", plist["ProgramArguments"])
        self.assertTrue(mod.INSTALLED_BIN.exists())

    def test_install_from_the_installed_copy_does_not_copy_onto_itself(self):
        mod.INSTALLED_BIN.parent.mkdir(parents=True)
        mod.INSTALLED_BIN.write_text("x")
        with mock.patch.object(mod, "__file__", str(mod.INSTALLED_BIN)), contextlib.redirect_stdout(io.StringIO()):
            mod.install(Path("/x"), schedules=False, dry_run=False)

    def test_failed_bootstrap_exits(self):
        mod.launchctl.return_value = mock.Mock(returncode=5, stderr="boom")
        with mock.patch.object(mod.time, "sleep"), self.assertRaises(SystemExit):
            mod.install(Path("/x"), schedules=False, dry_run=False)

    def test_uninstall_removes_agent_and_script(self):
        with contextlib.redirect_stdout(io.StringIO()):
            mod.install(Path("/x"), schedules=False, dry_run=False)
            mod.uninstall(dry_run=False)
        self.assertFalse(mod.AGENT_PLIST.exists() or mod.INSTALLED_BIN.exists())

    def test_union_is_rejected_for_install(self):
        with mock.patch.object(mod.sys, "argv", ["x", "install", "--union"]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mod.main()

    def test_dry_run_writes_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            mod.install(Path("/x"), schedules=False, dry_run=True)
            mod.uninstall(dry_run=True)
        self.assertFalse(mod.AGENT_PLIST.exists())
        self.assertFalse(mod.INSTALLED_BIN.exists())


if __name__ == "__main__":
    unittest.main()
