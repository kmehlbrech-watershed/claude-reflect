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
    get_global_queue_path,
    get_max_backups,
    get_queue_path,
    migrate_global_queue,
    prune_backups,
    save_queue_at,
)

POSIX = os.name == "posix"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _home_env(home: Path) -> dict:
    """Point Path.home() at `home` on every platform.

    POSIX reads HOME; Windows (Python 3.8+) reads USERPROFILE and ignores
    HOME. Setting only HOME would let a Windows run write to, and prune, the
    developer's real ~/.claude.
    """
    return {"HOME": str(home), "USERPROFILE": str(home)}


class _TempHome(unittest.TestCase):
    """A temporary home that Path.home() resolves to for the whole test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        patcher = patch.dict(os.environ, _home_env(self.home))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Guard: if this ever fails, the test would touch the real home.
        self.assertEqual(Path.home().resolve(), self.home)

    def tearDown(self):
        self._tmp.cleanup()


class TestPrivateFiles(_TempHome):
    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_queue_is_owner_only(self):
        queue = self.home / "learnings-queue.json"
        old_umask = os.umask(0o022)
        try:
            save_queue_at(queue, [{"type": "explicit", "message": "remember: x"}])
        finally:
            os.umask(old_umask)
        self.assertEqual(_mode(queue), 0o600)

    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_temp_file_is_owner_only_before_any_content_is_written(self):
        queue = self.home / "learnings-queue.json"
        seen = []
        real_fdopen = os.fdopen

        def spy(fd, *args, **kwargs):
            seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
            return real_fdopen(fd, *args, **kwargs)

        old_umask = os.umask(0o022)
        try:
            with patch("lib.reflect_utils.os.fdopen", side_effect=spy):
                save_queue_at(queue, [{"message": "remember: secret"}])
        finally:
            os.umask(old_umask)
        self.assertEqual(seen, [0o600])

    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_stale_world_readable_temp_file_is_replaced(self):
        queue = self.home / "learnings-queue.json"
        stale = queue.with_name(queue.name + f".tmp.{os.getpid()}")
        stale.write_text("old", encoding="utf-8")
        os.chmod(stale, 0o644)
        save_queue_at(queue, [])
        self.assertEqual(_mode(queue), 0o600)
        self.assertFalse(stale.exists())

    @unittest.skipUnless(POSIX, "file modes are POSIX-only")
    def test_existing_world_readable_queue_is_tightened_on_save(self):
        queue = self.home / "learnings-queue.json"
        queue.write_text("[]", encoding="utf-8")
        os.chmod(queue, 0o644)
        save_queue_at(queue, [])
        self.assertEqual(_mode(queue), 0o600)

    def test_migrated_queue_is_written_privately(self):
        project = self.home / "work" / "demo"
        project.mkdir(parents=True)
        global_queue = get_global_queue_path()
        global_queue.parent.mkdir(parents=True, exist_ok=True)
        global_queue.write_text(
            json.dumps([{"message": "remember: use uv", "project": str(project)}]),
            encoding="utf-8",
        )
        old_umask = os.umask(0o022) if POSIX else None
        try:
            migrate_global_queue()
        finally:
            if POSIX:
                os.umask(old_umask)
        target = get_queue_path(str(project))
        self.assertFalse(global_queue.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["message"], "remember: use uv")
        if POSIX:
            self.assertEqual(_mode(target), 0o600)

    def test_capture_creates_queue_privately(self):
        folder = self.home / ".claude" / "projects" / "-work-demo"
        folder.mkdir(parents=True)
        payload = {"prompt": "hello there", "transcript_path": str(folder / "s.jsonl")}
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "capture_learning.py")],
            input=json.dumps(payload), capture_output=True, text=True,
            env={**os.environ, **_home_env(self.home)}, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = folder / "learnings-queue.json"
        self.assertEqual(json.loads(queue.read_text(encoding="utf-8")), [])
        if POSIX:
            self.assertEqual(_mode(queue), 0o600)


class TestPruneBackups(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make(self, n):
        for i in range(n):
            (self.dir / f"pre-compact-2026010{1 + i // 10}-0000{i % 10}0-{100 + i}.json").write_text("[]")

    def _left(self):
        return sorted(p.name for p in self.dir.glob("pre-compact-*.json"))

    def test_keeps_newest(self):
        self._make(5)
        (self.dir / "notes.txt").write_text("not ours")
        removed = prune_backups(self.dir, 2)
        self.assertEqual(removed, 3)
        self.assertEqual(self._left(), ["pre-compact-20260101-000030-103.json", "pre-compact-20260101-000040-104.json"])
        self.assertTrue((self.dir / "notes.txt").exists())

    def test_stray_names_are_ignored(self):
        # "manual" sorts after any digit, so a name sort would call it the
        # newest and delete the real newest backup instead.
        self._make(3)
        for stray in ("pre-compact-manual.json", "pre-compact-2026.json", "pre-compact-20260101-000000-1.json.bak"):
            (self.dir / stray).write_text("[]")
        removed = prune_backups(self.dir, 1)
        self.assertEqual(removed, 2)
        self.assertIn("pre-compact-20260101-000020-102.json", self._left())
        self.assertIn("pre-compact-manual.json", self._left())
        self.assertIn("pre-compact-2026.json", self._left())
        self.assertTrue((self.dir / "pre-compact-20260101-000000-1.json.bak").exists())

    def test_protected_file_survives(self):
        self._make(3)
        fresh = self.dir / "pre-compact-20260101-000000-100.json"
        prune_backups(self.dir, 1, protect=fresh)
        self.assertTrue(fresh.exists())

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


class TestPreCompactHook(_TempHome):
    def setUp(self):
        super().setUp()
        self.project = self.home / "work" / "demo"
        self.project.mkdir(parents=True)
        self.backups = self.home / ".claude" / "learnings-backups"

    def _run(self, payload=None, extra_env=None, cwd=None):
        env = {**os.environ, **_home_env(self.home), **(extra_env or {})}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "check_learnings.py")],
            input=json.dumps(payload) if payload is not None else "",
            cwd=str(cwd or self.project), capture_output=True, text=True, env=env, timeout=30,
        )

    def _queue(self, folder_name, message):
        folder = self.home / ".claude" / "projects" / folder_name
        save_queue_at(folder / "learnings-queue.json", [{"type": "explicit", "message": message}])
        return folder

    def test_backup_is_private_and_old_ones_are_pruned(self):
        queue = get_queue_path(str(self.project))
        save_queue_at(queue, [{"type": "explicit", "message": "remember: use uv"}])

        mine = self.backups / queue.parent.name
        mine.mkdir(parents=True)
        for i in range(4):
            (mine / f"pre-compact-20250101-00000{i}-{i}.json").write_text("[]")
        (mine / "pre-compact-manual.json").write_text("[]")
        # A backup from before the per-project layout: left alone.
        legacy = self.backups / "pre-compact-20240101-000000.json"
        legacy.write_text("[]")

        result = self._run(extra_env={"CLAUDE_REFLECT_MAX_BACKUPS": "2"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("backed up", result.stdout)

        left = sorted(p for p in mine.glob("pre-compact-2*.json"))
        self.assertEqual(len(left), 2)
        newest = left[-1]
        self.assertNotIn("20250101", newest.name)
        self.assertRegex(newest.name, r"^pre-compact-\d{8}-\d{6}-\d+\.json$")  # timestamp + pid
        self.assertEqual(json.loads(newest.read_text(encoding="utf-8"))[0]["message"], "remember: use uv")
        self.assertTrue((mine / "pre-compact-manual.json").exists())
        self.assertTrue(legacy.exists())
        if POSIX:
            self.assertEqual(_mode(newest), 0o600)
            self.assertEqual(_mode(mine), 0o700)
            self.assertEqual(_mode(self.backups), 0o700)

    def test_pruning_is_per_project(self):
        self._queue("-proj-a", "remember: a")
        b = self._queue("-proj-b", "remember: b")
        a_dir = self.backups / "-proj-a"
        a_dir.mkdir(parents=True)
        for i in range(3):
            (a_dir / f"pre-compact-20250101-00000{i}-{i}.json").write_text("[]")

        result = self._run({"transcript_path": str(b / "session.jsonl")}, {"CLAUDE_REFLECT_MAX_BACKUPS": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(a_dir.glob("pre-compact-*.json"))), 3)
        b_backups = list((self.backups / "-proj-b").glob("pre-compact-*.json"))
        self.assertEqual(len(b_backups), 1)

    def test_transcript_path_selects_the_queue(self):
        # cwd's own queue says one thing, the payload's project another;
        # the payload wins.
        save_queue_at(get_queue_path(str(self.project)), [{"message": "from cwd"}])
        other = self._queue("-elsewhere", "from transcript")
        result = self._run({"session_id": "x", "transcript_path": str(other / "s.jsonl")})
        self.assertEqual(result.returncode, 0, result.stderr)
        (backup,) = list((self.backups / "-elsewhere").glob("pre-compact-*.json"))
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))[0]["message"], "from transcript")

    def test_without_transcript_path_falls_back_to_cwd(self):
        queue = get_queue_path(str(self.project))
        save_queue_at(queue, [{"message": "from cwd"}])
        for payload in (None, {}, {"transcript_path": "/not/under/projects/s.jsonl"}):
            with self.subTest(payload=payload):
                result = self._run(payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("backed up", result.stdout)
        self.assertTrue(list((self.backups / queue.parent.name).glob("pre-compact-*.json")))

    def test_empty_queue_writes_nothing(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
