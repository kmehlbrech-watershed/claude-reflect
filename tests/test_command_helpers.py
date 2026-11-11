#!/usr/bin/env python3
"""Script flags that replace the shell one-liners and inline Python the
slash commands used to run (each of which prompted for permission)."""
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.reflect_utils import get_claude_dir, get_project_folder_name  # noqa: E402

import extract_tool_errors  # noqa: E402
import memory_tiers  # noqa: E402
import read_queue  # noqa: E402


class _TempHome(unittest.TestCase):
    """A fake HOME with a project directory inside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / "work" / "demo"
        self.project.mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.home)}
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        with patch.dict(os.environ, self.env, clear=True):
            self.session_dir = (get_claude_dir() / "projects"
                                / get_project_folder_name(str(self.project)))

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, name, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / name), *args],
            capture_output=True, text=True, env=self.env, cwd=str(self.project), timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None


class TestProjectPathsInitialized(_TempHome):
    def test_not_initialized_until_marked(self):
        self.assertFalse(self.run_script("project_paths.py")["initialized"])
        out = self.run_script("project_paths.py", "--mark-initialized")
        self.assertTrue(out["initialized"])
        marker = self.session_dir / ".reflect-initialized"
        self.assertTrue(marker.is_file())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        # Idempotent.
        self.assertTrue(self.run_script("project_paths.py", "--mark-initialized")["initialized"])


class TestProjectPathsAgentsMdEnv(_TempHome):
    def _env(self, value):
        env = {k: v for k, v in self.env.items() if k != "CLAUDE_REFLECT_AGENTS_MD"}
        if value is not None:
            env["CLAUDE_REFLECT_AGENTS_MD"] = value
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "project_paths.py"), "--agents-md-env"],
            capture_output=True, text=True, env=env, cwd=str(self.project), timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_prints_value_or_off(self):
        # Same as the shell's "${CLAUDE_REFLECT_AGENTS_MD:-off}".
        self.assertEqual(self._env(None), "off\n")
        self.assertEqual(self._env(""), "off\n")
        self.assertEqual(self._env("TRUE"), "TRUE\n")
        self.assertEqual(self._env("1"), "1\n")


class TestProjectPathsAgentsMd(_TempHome):
    def state(self):
        return self.run_script("project_paths.py")["agents_md"]

    def test_missing(self):
        state = self.state()
        self.assertFalse(state["exists"])
        self.assertIsNone(state["lines"])
        self.assertFalse(state["same_file_as_claude_md"])
        self.assertEqual(state["claude_md_include_lines"], [])

    def test_found_with_line_count(self):
        (self.project / "AGENTS.md").write_text("# A\n- one\n- two\n", encoding="utf-8")
        (self.project / "CLAUDE.md").write_text("# C\nsee AGENTS.md for more\n", encoding="utf-8")
        state = self.state()
        self.assertTrue(state["exists"])
        self.assertEqual(state["lines"], 3)
        self.assertFalse(state["same_file_as_claude_md"])
        self.assertEqual(state["claude_md_include_lines"], [])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_is_same_file(self):
        (self.project / "AGENTS.md").write_text("- x\n", encoding="utf-8")
        os.symlink("AGENTS.md", self.project / "CLAUDE.md")
        self.assertTrue(self.state()["same_file_as_claude_md"])

    def test_include_lines(self):
        (self.project / "AGENTS.md").write_text("- x\n", encoding="utf-8")
        (self.project / "CLAUDE.md").write_text(
            "@AGENTS.md\n"              # 1: include
            "mail me@AGENTS.md\n"       # 2: preceded by a letter, not one
            "Read @./AGENTS.md too\n"   # 3: include
            "x.@AGENTS.md\n"            # 4: preceded by a dot, not one
            "(@AGENTS.md)\n",           # 5: include
            encoding="utf-8")
        self.assertEqual(self.state()["claude_md_include_lines"], [1, 3, 5])

    def test_include_ignored_without_agents_md(self):
        (self.project / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        self.assertEqual(self.state()["claude_md_include_lines"], [])


class TestProjectPathsAllProjects(_TempHome):
    def _touch(self, rel, age_days=0):
        path = self.home / ".claude" / "projects" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))

    def test_lists_main_sessions_across_projects(self):
        self._touch("-a/s1.jsonl", age_days=1)
        self._touch("-b/s2.jsonl", age_days=0)
        self._touch("-b/agent-x.jsonl")
        self._touch("-b/s2/subagents/agent-y.jsonl")
        self._touch("-c/old.jsonl", age_days=30)
        out = self.run_script("project_paths.py", "--all-projects", "--days", "14")
        self.assertEqual([Path(p).name for p in out["session_files"]], ["s2.jsonl", "s1.jsonl"])
        self.assertEqual(out["subagent_files"], [])


class TestReadQueueSummary(unittest.TestCase):
    def test_relative_age(self):
        now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        cases = {
            timedelta(seconds=30): "just now",
            timedelta(minutes=1): "1 minute ago",
            timedelta(minutes=5): "5 minutes ago",
            timedelta(hours=2): "2 hours ago",
            timedelta(days=3): "3 days ago",
            timedelta(days=15): "2 weeks ago",
        }
        for delta, expected in cases.items():
            with self.subTest(delta=delta):
                ts = (now - delta).isoformat().replace("+00:00", "Z")
                self.assertEqual(read_queue.relative_age(ts, now), expected)
        self.assertEqual(read_queue.relative_age("not a date", now), "unknown")
        self.assertEqual(read_queue.relative_age(None, now), "unknown")

    def test_summary_keeps_missing_values_null(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        items = [
            {"type": "explicit", "message": "remember: use uv", "timestamp": "2026-01-09T00:00:00Z",
             "confidence": 0.9, "decay_days": 120, "patterns": ["remember"]},
            {"type": "auto", "message": "no, use pnpm", "timestamp": "2026-01-10T00:00:00Z"},
            "garbage",
        ]
        summary = read_queue.summarize(items, now)
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0]["age"], "1 day ago")
        self.assertEqual(summary[0]["confidence"], 0.9)
        self.assertEqual(summary[1]["confidence"], None)
        self.assertEqual(summary[1]["decay_days"], None)
        self.assertEqual(summary[1]["patterns"], [])
        self.assertEqual(summary[1]["message"], "no, use pnpm")

    def test_cli_modes(self):
        items = [{"type": "auto", "message": "m", "timestamp": "2026-01-10T00:00:00Z"}]
        for argv, check in (([], lambda out: json.loads(out) == items),
                            (["--count"], lambda out: out.strip() == "1"),
                            (["--summary"], lambda out: json.loads(out)[0]["message"] == "m")):
            with self.subTest(argv=argv):
                buf = io.StringIO()
                with patch.object(read_queue, "load_queue", return_value=items), redirect_stdout(buf):
                    self.assertEqual(read_queue.main(argv), 0)
                self.assertTrue(check(buf.getvalue()), buf.getvalue())


class TestMemoryTiers(_TempHome):
    def setUp(self):
        super().setUp()
        claude = self.home / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "CLAUDE.md").write_text("# G\n- Use tabs\n", encoding="utf-8")
        (self.project / "CLAUDE.md").write_text("# P\n- Use spaces\n- Run tests\n", encoding="utf-8")

    def test_files_have_line_counts(self):
        files = {f["relative_path"]: f for f in self.run_script("memory_tiers.py")}
        self.assertEqual(files["~/.claude/CLAUDE.md"]["lines"], 2)
        self.assertEqual(files["./CLAUDE.md"]["lines"], 3)

    def test_auto_memory(self):
        out = self.run_script("memory_tiers.py", "--auto-memory")
        self.assertFalse(out["exists"])
        self.assertEqual(out["files"], [])
        memory = Path(out["memory_dir"])
        memory.mkdir(parents=True)
        (memory / "workflow.md").write_text("# Workflow\n- Run tests first\n", encoding="utf-8")
        out = self.run_script("memory_tiers.py", "--auto-memory")
        self.assertTrue(out["exists"])
        self.assertEqual(out["files"][0]["name"], "workflow")

    def test_entries(self):
        texts = {e["text"] for e in self.run_script("memory_tiers.py", "--entries")}
        self.assertEqual(texts, {"Use tabs", "Use spaces", "Run tests"})

    def test_topics(self):
        out = self.run_script("memory_tiers.py", "--topics")
        self.assertIn("model-preferences", out["topics"])
        self.assertEqual(out["fallback"], "general")

    def test_contradictions_collects_both_files(self):
        found = [{"entry1": "Use tabs", "entry2": "Use spaces", "conflict": "indentation"}]
        buf = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch("lib.semantic_detector.detect_contradictions", return_value=found) as detect, \
                patch("os.getcwd", return_value=str(self.project)), \
                patch("pathlib.Path.cwd", return_value=self.project), \
                redirect_stdout(buf):
            self.assertEqual(memory_tiers.main(["--contradictions", "--model", "haiku"]), 0)
        detect.assert_called_once_with(["Use tabs", "Use spaces", "Run tests"], model="haiku")
        out = json.loads(buf.getvalue())
        self.assertEqual(out["contradictions"], found)
        self.assertEqual([(e["file"], e["line"]) for e in out["entries"]],
                         [("~/.claude/CLAUDE.md", 2), ("./CLAUDE.md", 2), ("./CLAUDE.md", 3)])


class TestExtractToolErrorsValidate(unittest.TestCase):
    AGG = [{"error_type": "connection_refused", "count": 3, "confidence": 0.7,
            "suggested_guideline": "Check DATABASE_URL", "sample_errors": ["refused"]}]

    def _main(self, *argv):
        buf = io.StringIO()
        with tempfile.NamedTemporaryFile(suffix=".jsonl") as f, \
                patch.object(extract_tool_errors, "extract_tool_errors", return_value=[{}]), \
                patch.object(extract_tool_errors, "aggregate_tool_errors", return_value=self.AGG), \
                patch("lib.semantic_detector.validate_tool_errors",
                      return_value=[{**self.AGG[0], "refined_guideline": "Load .env first"}]) as validate, \
                patch.object(sys, "argv", ["extract_tool_errors.py", f.name, "--json", *argv]), \
                redirect_stdout(buf):
            self.assertEqual(extract_tool_errors.main(), 0)
        return json.loads(buf.getvalue()), validate

    def test_without_validate_no_claude_call(self):
        out, validate = self._main()
        validate.assert_not_called()
        self.assertNotIn("refined_guideline", out[0])

    def test_validate_adds_refined_guideline(self):
        out, validate = self._main("--validate", "--model", "haiku")
        validate.assert_called_once_with(self.AGG, model="haiku")
        self.assertEqual(out[0]["refined_guideline"], "Load .env first")


if __name__ == "__main__":
    unittest.main()
