#!/usr/bin/env python3
"""Nested `claude -p` calls run with no tools, no MCP, and the plugin's hooks muted."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lib.semantic_detector as sd  # noqa: E402
from lib.reflect_utils import in_nested_reflect_call  # noqa: E402

OK_ANALYSIS = {"is_learning": True, "type": "correction", "confidence": 0.8,
               "reasoning": "r", "extracted_learning": "Use uv"}


def _result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return r


def _ok(payload):
    return _result(stdout=json.dumps({"result": payload}))


class NestedCallTestCase(unittest.TestCase):
    def setUp(self):
        sd._rejected_flags.clear()
        # Fallback warnings go to stderr; keep test output clean and inspectable.
        self.stderr = io.StringIO()
        patcher = patch.object(sys, "stderr", self.stderr)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        sd._rejected_flags.clear()


class TestCommand(NestedCallTestCase):
    def test_hardened_command(self):
        cmd = sd._claude_command("haiku")
        self.assertEqual(cmd, ["claude", "-p", "--output-format", "json",
                               "--tools", "", "--strict-mcp-config", "--safe-mode",
                               "--no-session-persistence", "--model", "haiku"])

    def test_tools_gets_its_own_empty_string_argument(self):
        cmd = sd._claude_command(None)
        i = cmd.index("--tools")
        # A separate argv element holding "", not "--tools=" or a dropped arg.
        self.assertEqual(cmd[i + 1], "")
        self.assertNotIn("--tools=", cmd)
        self.assertEqual(sum(1 for a in cmd if a == ""), 1)

    def test_rejected_tools_falls_back_to_disallowed_builtin_tools(self):
        cmd = sd._claude_command(None, {"--tools"})
        self.assertNotIn("--tools", cmd)
        i = cmd.index("--disallowedTools")
        denied = cmd[i + 1].split(",")
        for tool in ("Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch", "Task", "Glob", "Grep"):
            self.assertIn(tool, denied)
        for flag in ("--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
            self.assertIn(flag, cmd)

    def test_everything_rejected_is_the_old_command(self):
        rejected = {f for f, _ in sd.HARDENING_FLAGS} | {"--disallowedTools"}
        self.assertEqual(sd._claude_command(None, rejected), ["claude", "-p", "--output-format", "json"])

    def test_env_var_name_matches_the_hook_guard(self):
        with patch.dict(os.environ, {sd.NESTED_ENV_VAR: "1"}):
            self.assertTrue(in_nested_reflect_call())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(sd.NESTED_ENV_VAR, None)
            self.assertFalse(in_nested_reflect_call())


class TestEveryCallSiteIsHardened(NestedCallTestCase):
    def _assert_hardened(self, mock_run):
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        for flag in ("--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        self.assertEqual(kwargs["env"][sd.NESTED_ENV_VAR], "1")

    @patch("lib.semantic_detector.subprocess.run")
    def test_semantic_analyze(self, mock_run):
        mock_run.return_value = _ok(OK_ANALYSIS)
        self.assertIsNotNone(sd.semantic_analyze("no, use uv not pip"))
        self._assert_hardened(mock_run)

    @patch("lib.semantic_detector.subprocess.run")
    def test_validate_tool_error(self, mock_run):
        mock_run.return_value = _ok({"is_learnable": True, "refined_guideline": "g",
                                     "confidence": 0.8, "reasoning": "r"})
        self.assertIsNotNone(sd.validate_tool_error("env_undefined", "DATABASE_URL not set", 3, "Load .env"))
        self._assert_hardened(mock_run)

    @patch("lib.semantic_detector.subprocess.run")
    def test_detect_contradictions(self, mock_run):
        mock_run.return_value = _ok({"contradictions": []})
        sd.detect_contradictions(["Use npm", "Use pnpm"])
        self._assert_hardened(mock_run)


class TestOlderClaudeFallback(NestedCallTestCase):
    @patch("lib.semantic_detector.subprocess.run")
    def test_rejected_tools_keeps_every_other_flag_and_remembers(self, mock_run):
        mock_run.side_effect = [
            _result(returncode=1, stderr="error: unknown option '--tools'"),
            _ok(OK_ANALYSIS),
            _ok(OK_ANALYSIS),
        ]
        self.assertIsNotNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertEqual(mock_run.call_count, 2)
        retry_cmd = mock_run.call_args_list[1][0][0]
        self.assertNotIn("--tools", retry_cmd)
        self.assertIn("--disallowedTools", retry_cmd)
        for flag in ("--strict-mcp-config", "--safe-mode", "--no-session-persistence"):
            self.assertIn(flag, retry_cmd)
        # Still muted even without the flag.
        self.assertEqual(mock_run.call_args_list[1][1]["env"][sd.NESTED_ENV_VAR], "1")
        # One warning line names what was lost.
        self.assertEqual(len(self.stderr.getvalue().strip().splitlines()), 1)
        self.assertIn("--tools", self.stderr.getvalue())

        # The next call goes straight to the degraded command, without a probe.
        sd.semantic_analyze("don't use npm")
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(mock_run.call_args_list[2][0][0], retry_cmd)

    @patch("lib.semantic_detector.subprocess.run")
    def test_degrades_one_flag_at_a_time(self, mock_run):
        accepted = {"--strict-mcp-config", "--disallowedTools"}

        def old_cli(cmd, **kwargs):
            for arg in cmd:
                if arg.startswith("--") and arg not in accepted | {"--output-format", "--model"}:
                    return _result(returncode=1, stderr=f"error: unknown option '{arg}'")
            return _ok(OK_ANALYSIS)

        mock_run.side_effect = old_cli
        self.assertIsNotNone(sd.semantic_analyze("no, use uv not pip"))
        final = mock_run.call_args_list[-1][0][0]
        self.assertIn("--strict-mcp-config", final)
        self.assertIn("--disallowedTools", final)
        self.assertNotIn("--tools", final)
        self.assertNotIn("--safe-mode", final)
        self.assertNotIn("--no-session-persistence", final)
        # tools, safe-mode, no-session-persistence: one rejected call each.
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(len(self.stderr.getvalue().strip().splitlines()), 3)

    @patch("lib.semantic_detector.subprocess.run")
    def test_cli_rejecting_everything_does_not_loop_forever(self, mock_run):
        mock_run.side_effect = lambda cmd, **kw: _result(
            returncode=1, stderr="error: unknown option '%s'" % next(
                (a for a in cmd if a in {f for f, _ in sd.HARDENING_FLAGS} | {"--disallowedTools"}),
                "--output-format"))
        self.assertIsNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertLessEqual(mock_run.call_count, len(sd.HARDENING_FLAGS) + 2)
        self.assertEqual(mock_run.call_args_list[-1][0][0][:4], ["claude", "-p", "--output-format", "json"])
        self.assertIn("WITH tools", self.stderr.getvalue())

    @patch("lib.semantic_detector.subprocess.run")
    def test_other_error_naming_our_flag_is_handled(self, mock_run):
        # Not "unknown option", but the CLI still blames one of our flags.
        mock_run.side_effect = [
            _result(returncode=1, stderr="error: option '--tools <tools...>' argument missing"),
            _ok(OK_ANALYSIS),
        ]
        self.assertIsNotNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertIn("--tools", sd._rejected_flags)
        self.assertIn("--disallowedTools", mock_run.call_args_list[1][0][0])

    @patch("lib.semantic_detector.subprocess.run")
    def test_unknown_option_not_ours_is_logged_not_retried(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr="error: unknown option '--output-format'")
        self.assertIsNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertEqual(mock_run.call_count, 1)
        self.assertIn("--output-format", self.stderr.getvalue())

    @patch("lib.semantic_detector.subprocess.run")
    def test_other_failures_do_not_retry_unhardened(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr="API error: overloaded")
        self.assertIsNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(sd._rejected_flags, set())
        self.assertEqual(self.stderr.getvalue(), "")


class TestNoOtherWayToRunClaude(unittest.TestCase):
    """Every claude subprocess goes through _run_claude, so none skips the hardening."""

    def test_subprocess_and_claude_only_inside_run_claude(self):
        offenders = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            in_helper = False
            for n, line in enumerate(lines, 1):
                if re.match(r"def \w", line):
                    in_helper = path.name == "semantic_detector.py" and line.startswith(
                        ("def _run_claude(", "def _claude_command("))
                code = line.split("#", 1)[0]
                hits = re.search(r"subprocess\.(run|Popen|call|check_call|check_output)\b"
                                 r"|os\.system|os\.popen|[\"']claude[\"']", code)
                if hits and not in_helper:
                    offenders.append(f"{path.relative_to(SCRIPTS)}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])


class TestHooksStayQuietInNestedCalls(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / ".claude" / "projects" / "-tmp-demo"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, script, stdin="", nested=True, cwd=None):
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("CLAUDE_PROJECT_DIR", None)
        if nested:
            env[sd.NESTED_ENV_VAR] = "1"
        else:
            env.pop(sd.NESTED_ENV_VAR, None)
        return subprocess.run([sys.executable, str(SCRIPTS / script)], input=stdin,
                              capture_output=True, text=True, env=env, timeout=30, cwd=cwd)

    def test_capture_hook_does_not_queue_classifier_prompts(self):
        payload = json.dumps({
            "prompt": "remember: always use uv",
            "transcript_path": str(self.project / "s.jsonl"),
        })
        nested = self._run("capture_learning.py", payload, nested=True)
        self.assertEqual(nested.returncode, 0, nested.stderr)
        self.assertEqual(nested.stdout, "")
        self.assertFalse((self.project / "learnings-queue.json").exists())

        # Control: the same prompt outside a nested call is captured.
        normal = self._run("capture_learning.py", payload, nested=False)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertTrue((self.project / "learnings-queue.json").exists())

    def test_session_start_reminder_is_silent(self):
        result = self._run("session_start_reminder.py", nested=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_post_commit_reminder_is_silent(self):
        payload = json.dumps({"tool_input": {"command": "git commit -m 'x'"}})
        nested = self._run("post_commit_reminder.py", payload, nested=True)
        self.assertEqual(nested.returncode, 0, nested.stderr)
        self.assertEqual(nested.stdout, "")

        # Control: outside a nested call the reminder fires.
        normal = self._run("post_commit_reminder.py", payload, nested=False)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertIn("/reflect", normal.stdout)

    def test_check_learnings_does_not_back_up(self):
        workdir = self.home / "work"
        workdir.mkdir()
        env = {**os.environ, "HOME": str(self.home), "PYTHONPATH": str(SCRIPTS)}
        env.pop("CLAUDE_PROJECT_DIR", None)
        # Ask the library where the hook will look, rather than re-deriving it.
        queue = Path(subprocess.run(
            [sys.executable, "-c", "from lib.reflect_utils import get_queue_path; print(get_queue_path())"],
            capture_output=True, text=True, env=env, cwd=str(workdir), timeout=30, check=True,
        ).stdout.strip())
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(json.dumps([{"message": "use uv", "timestamp": "2026-01-01T00:00:00"}]))
        backups = self.home / ".claude" / "learnings-backups"

        nested = self._run("check_learnings.py", nested=True, cwd=str(workdir))
        self.assertEqual(nested.returncode, 0, nested.stderr)
        self.assertEqual(nested.stdout, "")
        self.assertFalse(backups.exists())

        # Control: outside a nested call the queue is backed up.
        normal = self._run("check_learnings.py", nested=False, cwd=str(workdir))
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertIn("backed up", normal.stdout)
        self.assertTrue(any(backups.iterdir()))


if __name__ == "__main__":
    unittest.main()
