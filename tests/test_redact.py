#!/usr/bin/env python3
"""Tests for secret redaction (scripts/lib/redact.py) and where it is applied."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.redact import REDACTED, redact_secrets, redaction_enabled  # noqa: E402
from lib.reflect_utils import (  # noqa: E402
    create_queue_item,
    extract_tool_errors,
    extract_tool_rejections,
    extract_user_messages,
    load_queue_at,
    save_queue_at,
)

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def _write_jsonl(path: Path, entries) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


class TestRedactSecrets(unittest.TestCase):
    """Same positives and negatives as reflect-mod's looksSecret test, plus more."""

    def test_reflect_mod_positives_are_masked(self):
        for text in [
            "key sk-ant-abcdefghijklmnop",
            "AKIAIOSFODNN7EXAMPLE",
            "пароль: hunter2",
            "password is hunter2",
            "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0",
        ]:
            with self.subTest(text=text):
                out = redact_secrets(text)
                self.assertIn(REDACTED, out)
                self.assertNotIn("hunter2", out)

    def test_reflect_mod_negatives_are_untouched(self):
        for text in [
            "Use pnpm, not npm",
            "the password flow is broken",
            "see /Users/bayramannakov/GH/claude-mods/reflect/hooks",
            "no, use uv instead of pip",
        ]:
            with self.subTest(text=text):
                self.assertEqual(redact_secrets(text), text)

    def test_only_the_secret_is_replaced(self):
        cases = {
            "password: hunter2 please": "password: [REDACTED] please",
            "set API_KEY=abcd1234 in .env": "set API_KEY=[REDACTED] in .env",
            "no, use postgres://app:hunter2@db:5432/main not sqlite":
                "no, use postgres://app:[REDACTED]@db:5432/main not sqlite",
            f"auth header is Bearer {JWT} ok": "auth header is Bearer [REDACTED] ok",
            "use ghp_abcdefghijklmnopqrstuvwxyz0123456789 for CI":
                "use [REDACTED] for CI",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_secrets(text), expected)

    def test_other_key_shapes(self):
        for secret in [
            "AIzaSyA1234567890abcdefghijklmnopqrs",
            "xoxb-123456789012-abcdefghijkl",
            "glpat-abcdefghij1234567890",
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",  # 36-char hex-ish token
        ]:
            with self.subTest(secret=secret):
                out = redact_secrets(f"remember: token for staging {secret} expires monthly")
                self.assertNotIn(secret, out)
                self.assertTrue(out.startswith("remember: "))

    def test_private_key_block_is_removed_whole(self):
        text = (
            "remember: deploy key\n-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAA\nQyNTUxOQAAACD\n"
            "-----END OPENSSH PRIVATE KEY-----\nthanks"
        )
        out = redact_secrets(text)
        self.assertNotIn("b3BlbnNzaC1rZXktdjEAAAAA", out)
        self.assertNotIn("PRIVATE KEY", out)
        self.assertTrue(out.endswith("thanks"))

    def test_unterminated_private_key_is_removed_to_the_end(self):
        out = redact_secrets("key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow")
        self.assertNotIn("MIIEow", out)

    def test_empty_and_non_string_pass_through(self):
        self.assertEqual(redact_secrets(""), "")
        self.assertIsNone(redact_secrets(None))

    def test_opt_out(self):
        for value in ["false", "0", "no", "OFF"]:
            with self.subTest(value=value), patch.dict(os.environ, {"CLAUDE_REFLECT_REDACT": value}):
                self.assertFalse(redaction_enabled())
                self.assertEqual(redact_secrets("password: hunter2"), "password: hunter2")
        with patch.dict(os.environ, {"CLAUDE_REFLECT_REDACT": "true"}):
            self.assertTrue(redaction_enabled())


class TestRedactionIsApplied(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_queue_item_redacts_message(self):
        item = create_queue_item(
            message="no, use postgres://app:hunter2@db/main",
            item_type="auto",
            patterns="no, use",
            confidence=0.8,
            sentiment="correction",
            decay_days=90,
        )
        self.assertEqual(item["message"], "no, use postgres://app:[REDACTED]@db/main")

    def test_save_queue_scrubs_items_written_before_redaction(self):
        queue = self.tmp / "learnings-queue.json"
        old = [{"type": "explicit", "message": "remember: password is hunter2"}, "not-a-dict"]
        save_queue_at(queue, old)
        saved = load_queue_at(queue)
        self.assertEqual(saved[0]["message"], "remember: password is [REDACTED]")
        self.assertEqual(saved[1], "not-a-dict")
        self.assertNotIn("hunter2", queue.read_text(encoding="utf-8"))
        # The caller's list is not mutated.
        self.assertEqual(old[0]["message"], "remember: password is hunter2")

    def test_extract_user_messages_redacts(self):
        session = self.tmp / "s.jsonl"
        _write_jsonl(session, [
            {"type": "user", "message": {"content": "no, use token=abcd1234efgh not the old one"}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "remember: AKIAIOSFODNN7EXAMPLE is staging"}]}},
        ])
        messages = extract_user_messages(session)
        self.assertEqual(len(messages), 2)
        self.assertNotIn("abcd1234efgh", messages[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", messages[1])

    def test_extract_tool_rejections_redacts(self):
        session = self.tmp / "s.jsonl"
        _write_jsonl(session, [{
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "is_error": True,
                "content": "The user doesn't want to proceed with this tool use. "
                           "the user said:\nuse password: hunter2 for the test db",
            }]},
        }])
        rejections = extract_tool_rejections(session)
        self.assertEqual(rejections, ["use password: [REDACTED] for the test db"])

    def test_extract_tool_errors_redacts_before_truncating(self):
        token = "Zx9" + "a1" * 20  # 43 chars
        content = "x" * 480 + " " + token + " trailing"
        session = self.tmp / "s.jsonl"
        _write_jsonl(session, [{
            "type": "user",
            "message": {"content": [{"type": "tool_result", "is_error": True, "content": content}]},
        }])
        errors = extract_tool_errors(session, project_specific_only=False)
        self.assertEqual(len(errors), 1)
        # Truncating first would leave a 19-char fragment of the token behind.
        self.assertNotIn(token[:19], errors[0]["content"])

    def test_capture_hook_stores_only_redacted_text(self):
        project = self.tmp / ".claude" / "projects" / "-tmp-demo"
        project.mkdir(parents=True)
        payload = {
            "prompt": "no, use postgres://app:hunter2@db/main not sqlite",
            "transcript_path": str(project / "session.jsonl"),
        }
        env = {**os.environ, "HOME": str(self.tmp)}
        env.pop("CLAUDE_REFLECT_REDACT", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "capture_learning.py")],
            input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = project / "learnings-queue.json"
        self.assertTrue(queue.exists(), result.stdout + result.stderr)
        stored = queue.read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", stored)
        self.assertNotIn("hunter2", stored)
        self.assertNotIn("hunter2", result.stdout)


if __name__ == "__main__":
    unittest.main()
