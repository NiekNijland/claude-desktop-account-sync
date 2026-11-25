import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

path = str(Path(__file__).resolve().parent.parent / "claude-account-sync")
loader = importlib.machinery.SourceFileLoader("claude_account_sync", path)
spec = importlib.util.spec_from_loader("claude_account_sync", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

ORG = "org-1"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "claude-code-sessions"
        self.a = self.root / "account-a" / ORG
        self.b = self.root / "account-b" / ORG
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kw):
        opts = dict(dry_run=False, verbose=False, schedules=False, union=False)
        opts.update(kw)
        mod.Syncer(self.root, **opts).run()

    def write(self, path, text, mtime):
        path.write_text(text)
        os.utime(path, (mtime, mtime))

    def test_newest_session_wins(self):
        self.write(self.a / "local_1.json", "old", 1000)
        self.write(self.b / "local_1.json", "new", 2000)
        self.sync()
        self.assertEqual((self.a / "local_1.json").read_text(), "new")

    def test_missing_session_is_copied(self):
        self.write(self.a / "local_2.json", "x", 1000)
        self.sync()
        self.assertEqual((self.b / "local_2.json").read_text(), "x")

    def test_deletion_propagates_and_is_trashed(self):
        self.write(self.a / "local_3.json", "x", 1000)
        self.write(self.b / "local_3.json", "x", 1000)
        self.write(self.a / "deleted_3", "", 1000)
        self.sync()
        self.assertFalse((self.b / "local_3.json").exists())
        self.assertTrue((self.b / "deleted_3").exists())
        self.assertEqual(len(list((self.root.parent / "claude-code-sessions-sync-trash").iterdir())), 2)

    def test_dry_run_changes_nothing(self):
        self.write(self.a / "local_4.json", "x", 1000)
        self.sync(dry_run=True, schedules=True)
        self.assertFalse((self.b / "local_4.json").exists())

    def test_schedules_are_off_by_default(self):
        self.write(self.a / mod.SCHEDULES, json.dumps({"scheduledTasks": []}), 2000)
        self.sync()
        self.assertFalse((self.b / mod.SCHEDULES).exists())

    def test_schedules_newest_wins_and_backs_up(self):
        self.write(self.a / mod.SCHEDULES, json.dumps({"scheduledTasks": [{"id": "old"}]}), 1000)
        self.write(self.b / mod.SCHEDULES, json.dumps({"scheduledTasks": [{"id": "new"}]}), 2000)
        self.sync(schedules=True)
        self.assertEqual(json.loads((self.a / mod.SCHEDULES).read_text())["scheduledTasks"][0]["id"], "new")
        self.assertEqual(len(list((self.root.parent / "claude-code-sessions-sync-trash").iterdir())), 1)

    def test_union_keeps_tasks_from_both(self):
        self.write(self.a / mod.SCHEDULES, json.dumps({"scheduledTasks": [{"id": "a"}]}), 1000)
        self.write(self.b / mod.SCHEDULES, json.dumps({"scheduledTasks": [{"id": "b"}]}), 2000)
        self.sync(union=True)
        for d in (self.a, self.b):
            ids = {t["id"] for t in json.loads((d / mod.SCHEDULES).read_text())["scheduledTasks"]}
            self.assertEqual(ids, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
