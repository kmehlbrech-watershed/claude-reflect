#!/usr/bin/env python3
"""Nested `claude -p` calls run with no tools, no MCP, and the plugin's hooks muted."""
import json
import os
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
        sd._hardening_supported = True

    def tearDown(self):
        sd._hardening_supported = True


class TestCommand(NestedCallTestCase):
    def test_hardened_command(self):
        cmd = sd._claude_command("haiku", hardened=True)
        self.assertEqual(cmd[:4], ["claude", "-p", "--output-format", "json"])
        i = cmd.index("--tools")
        self.assertEqual(cmd[i + 1], "")
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[-2:], ["--model", "haiku"])

    def test_unhardened_command_is_the_old_one(self):
        self.assertEqual(sd._claude_command(None, hardened=False), ["claude", "-p", "--output-format", "json"])

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
        self.assertIn("--strict-mcp-config", cmd)
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
    def test_unknown_option_retries_once_without_flags_and_remembers(self, mock_run):
        mock_run.side_effect = [
            _result(returncode=1, stderr="error: unknown option '--tools'"),
            _ok(OK_ANALYSIS),
            _ok(OK_ANALYSIS),
        ]
        self.assertIsNotNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertEqual(mock_run.call_count, 2)
        retry_cmd = mock_run.call_args_list[1][0][0]
        self.assertNotIn("--tools", retry_cmd)
        # Still muted even without the flags.
        self.assertEqual(mock_run.call_args_list[1][1]["env"][sd.NESTED_ENV_VAR], "1")

        # The next call goes straight to the plain command.
        sd.semantic_analyze("don't use npm")
        self.assertEqual(mock_run.call_count, 3)
        self.assertNotIn("--tools", mock_run.call_args_list[2][0][0])

    @patch("lib.semantic_detector.subprocess.run")
    def test_other_failures_do_not_retry_unhardened(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr="API error: overloaded")
        self.assertIsNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertEqual(mock_run.call_count, 1)
        self.assertTrue(sd._hardening_supported)


class TestHooksStayQuietInNestedCalls(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / ".claude" / "projects" / "-tmp-demo"
        self.project.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, script, stdin="", nested=True):
        env = {**os.environ, "HOME": str(self.home)}
        if nested:
            env[sd.NESTED_ENV_VAR] = "1"
        else:
            env.pop(sd.NESTED_ENV_VAR, None)
        return subprocess.run([sys.executable, str(SCRIPTS / script)], input=stdin,
                              capture_output=True, text=True, env=env, timeout=30)

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


if __name__ == "__main__":
    unittest.main()
