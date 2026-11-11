#!/usr/bin/env python3
"""Tests for owner-only queue/backup files and backup pruning."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.reflect_utils import (  # noqa: E402
    DEFAULT_MAX_BACKUPS,
    get_max_backups,
    get_queue_path,
    prune_backups,
    save_queue_at,
)

POSIX = os.name == "posix"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestPrivateFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_queue_is_owner_only(self):
        queue = self.tmp / "learnings-queue.json"
        old_umask = os.umask(0o022)
        try:
            save_queue_at(queue, [{"type": "explicit", "message": "remember: x"}])
        finally:
            os.umask(old_umask)
        self.assertEqual(_mode(queue), 0o600)

    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_existing_world_readable_queue_is_tightened_on_save(self):
        queue = self.tmp / "learnings-queue.json"
        queue.write_text("[]", encoding="utf-8")
        os.chmod(queue, 0o644)
        save_queue_at(queue, [])
        self.assertEqual(_mode(queue), 0o600)


class TestPruneBackups(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make(self, n):
        for i in range(n):
            (self.dir / f"pre-compact-2026010{1 + i // 10}-0000{i % 10}0.json").write_text("[]")

    def test_keeps_newest(self):
        self._make(5)
        (self.dir / "notes.txt").write_text("not ours")
        removed = prune_backups(self.dir, 2)
        self.assertEqual(removed, 3)
        left = sorted(p.name for p in self.dir.glob("pre-compact-*.json"))
        self.assertEqual(left, ["pre-compact-20260101-000030.json", "pre-compact-20260101-000040.json"])
        self.assertTrue((self.dir / "notes.txt").exists())

    def test_zero_keeps_everything(self):
        self._make(3)
        self.assertEqual(prune_backups(self.dir, 0), 0)
        self.assertEqual(len(list(self.dir.glob("*.json"))), 3)

    def test_missing_dir_is_fine(self):
        self.assertEqual(prune_backups(self.dir / "nope", 5), 0)

    def test_max_backups_setting(self):
        cases = {"": DEFAULT_MAX_BACKUPS, "5": 5, "0": 0, "-1": DEFAULT_MAX_BACKUPS, "lots": DEFAULT_MAX_BACKUPS}
        for raw, expected in cases.items():
            with self.subTest(raw=raw), patch.dict(os.environ, {"CLAUDE_REFLECT_MAX_BACKUPS": raw}):
                self.assertEqual(get_max_backups(), expected)


class TestPreCompactHook(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / "work" / "demo"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, extra_env=None):
        env = {**os.environ, "HOME": str(self.home), **(extra_env or {})}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "check_learnings.py")],
            cwd=str(self.project), capture_output=True, text=True, env=env, timeout=30,
        )

    def test_backup_is_private_and_old_ones_are_pruned(self):
        with patch.dict(os.environ, {"HOME": str(self.home)}):
            queue = get_queue_path(str(self.project))
        save_queue_at(queue, [{"type": "explicit", "message": "remember: use uv"}])

        backups = self.home / ".claude" / "learnings-backups"
        backups.mkdir(parents=True)
        for i in range(4):
            (backups / f"pre-compact-20250101-00000{i}.json").write_text("[]")

        result = self._run({"CLAUDE_REFLECT_MAX_BACKUPS": "2"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("backed up", result.stdout)

        left = sorted(backups.glob("pre-compact-*.json"))
        self.assertEqual(len(left), 2)
        newest = left[-1]
        self.assertTrue(newest.name.startswith("pre-compact-20"))
        self.assertNotIn("20250101", newest.name)
        self.assertEqual(json.loads(newest.read_text(encoding="utf-8"))[0]["message"], "remember: use uv")
        if POSIX:
            self.assertEqual(_mode(newest), 0o600)
            self.assertEqual(_mode(backups), 0o700)

    def test_empty_queue_writes_nothing(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".claude" / "learnings-backups").exists())


if __name__ == "__main__":
    unittest.main()
