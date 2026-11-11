#!/usr/bin/env python3
"""Tests for secret redaction (scripts/lib/redact.py) and where it is applied."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib.redact import REDACTED, redact_secrets, redaction_enabled  # noqa: E402
from lib.reflect_utils import (  # noqa: E402
    _legacy_encode_project_path,
    create_queue_item,
    extract_tool_errors,
    extract_tool_rejections,
    extract_user_messages,
    get_project_folder_name,
    get_queue_path,
    load_queue_at,
    migrate_global_queue,
    migrate_legacy_project_folder,
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

    def test_more_secret_shapes_are_masked(self):
        cases = {
            '{"password": "hunter2xyz"}': '{"password": "[REDACTED]"}',
            '{"user":"bob","api_key":"abc123def456"}': '{"user":"bob","api_key":"[REDACTED]"}',
            "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY":
                "export AWS_SECRET_ACCESS_KEY=[REDACTED]",
            "JWT_SECRET=s3cr3tvalue": "JWT_SECRET=[REDACTED]",
            "STRIPE_SECRET_KEY=abc123def456": "STRIPE_SECRET_KEY=[REDACTED]",
            "DB_PASS=s3cr3tpw": "DB_PASS=[REDACTED]",
            "PWD=hunter22": "PWD=[REDACTED]",
            "clientSecret: 9f8e7d6c5b": "clientSecret: [REDACTED]",
            "use:password=hunter2x": "use:password=[REDACTED]",
            "password=hunter2.": "password=[REDACTED].",
            "?a=1&token=abc123xyz&b=2": "?a=1&token=[REDACTED]&b=2",
            "redis://:hunter2@cache:6379": "redis://:[REDACTED]@cache:6379",
            "postgres://app:p@ss@db:5432/main": "postgres://app:[REDACTED]@db:5432/main",
            "curl -H 'Authorization: Bearer abc123xyz'": "curl -H 'Authorization: Bearer [REDACTED]'",
            "password = 'correct horse battery'": "password = '[REDACTED]'",
            "the API key is 'sk9-xyz' ok": "the API key is '[REDACTED]' ok",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_secrets(text), expected)

    def test_ordinary_learnings_are_untouched(self):
        """Redacted text becomes CLAUDE.md rules, so false positives cost."""
        for text in [
            # Prose about secrets.
            "the API key is loaded from .env",
            "token is stored in the keychain",
            "password is required",
            "the token is 2 hours old",
            "the secret is base64-encoded",
            "the password is in 1Password",
            "Bearer authentication is required",
            "session token: expires after 30m",
            # Types, flags, numbers, references, placeholders.
            "token: string",
            "password: str",
            "password: Optional[str] = None",
            "secret=True",
            "max_token=4096",
            "max_tokens: 4096",
            "token_limit=100k",
            "token_type: bearer",
            "token_url=https://auth.example.com/oauth2/token",
            "token: ${{ secrets.NPM_TOKEN }}",
            "password = os.environ['DB_PASSWORD']",
            'api_key = os.getenv("API_KEY")',
            "apiKey: process.env.API_KEY",
            "use secret: <your-secret>",
            "PWD=/home/user/project",
            # Identifiers and hashes.
            "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 fixed it",
            "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            "id 123e4567-e89b-12d3-a456-426614174000",
            "use claude-opus-4-5-20251101-with-extended-thinking",
            "test_user_signup_flow_with_retry_and_backoff_v2",
            "see /src/components/AbC123dEf456gHi789jKl012mNo345pQr678/index.ts",
            "open AbC123dEf456gHi789jKl012mNo345pQr678.json",
            "pk_orders_customer_id",
            "pk_orders_customer_id_idx",
            # URLs without credentials.
            "https://medium.com/@user/post",
            "ssh://git@github.com:22/org/repo",
            "https://example.com:8080/a",
        ]:
            with self.subTest(text=text):
                self.assertEqual(redact_secrets(text), text)

    def test_redaction_is_idempotent(self):
        """Readers redact on load, so already-redacted text must not change."""
        for text in [
            "password: hunter2 please",
            "no, use postgres://app:p@ss@db:5432/main",
            '{"password": "hunter2xyz"}',
            "password = 'correct horse battery'",
            f"auth header is Bearer {JWT} ok",
            "token=ghp_abcdefghijklmnop1234",
        ]:
            with self.subTest(text=text):
                once = redact_secrets(text)
                self.assertEqual(redact_secrets(once), once)

    def test_large_inputs_stay_fast(self):
        """No catastrophic backtracking on ~1 MB of adversarial text."""
        for text in [
            "a" * 1_000_000,
            "a-" * 500_000,
            "token" * 200_000,
            "://x:" * 200_000,
            "password=a " * 100_000,
            "sk-" * 330_000,
            "://a:" + "@" * 1_000_000,
            "password" + " " * 1_000_000,
        ]:
            with self.subTest(text=text[:12]):
                start = time.monotonic()
                redact_secrets(text)
                self.assertLess(time.monotonic() - start, 10)

    def test_other_key_shapes(self):
        for secret in [
            "AIzaSyA1234567890abcdefghijklmnopqrs",
            "xoxb-123456789012-abcdefghijkl",
            "glpat-abcdefghij1234567890",
            "Zx9aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2y",  # 40-char random token
            "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc",  # split so secret scanners skip the fixture
            "pk_" + "test_" + "TYooMQauvdEDq54NiTphI7jx",  # split so secret scanners skip the fixture
            "sk-abcdefghijklmnop1234567890",
            "ASIAIOSFODNN7EXAMPLE",
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
        old = [{"type": "explicit", "message": "remember: DB_PASS=hunter22"}, "not-a-dict"]
        save_queue_at(queue, old)
        self.assertNotIn("hunter22", queue.read_text(encoding="utf-8"))
        saved = load_queue_at(queue)
        self.assertEqual(saved[0]["message"], "remember: DB_PASS=[REDACTED]")
        self.assertEqual(saved[1], "not-a-dict")
        # The caller's list is not mutated.
        self.assertEqual(old[0]["message"], "remember: DB_PASS=hunter22")

    def test_load_queue_redacts_a_raw_file(self):
        """A queue written before redaction is scrubbed for every reader."""
        queue = self.tmp / "learnings-queue.json"
        queue.write_text(json.dumps([{"message": "remember: DB_PASS=hunter22"}]), encoding="utf-8")
        self.assertEqual(load_queue_at(queue)[0]["message"], "remember: DB_PASS=[REDACTED]")
        # Reading does not rewrite the file.
        self.assertIn("hunter22", queue.read_text(encoding="utf-8"))

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


RAW_ITEM = {
    "type": "explicit",
    "message": "remember: DB_PASS=hunter22 for staging",
    "timestamp": "2026-01-01T00:00:00Z",
    "confidence": 0.9,
}


class TestQueueReadersSeeRedactedText(unittest.TestCase):
    """Queues already on disk hold raw text; nothing that reads one may leak it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.project = self.home / "work" / "demo"
        self.project.mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        self.env.pop("CLAUDE_REFLECT_REDACT", None)
        with patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)}):
            self.queue = get_queue_path(str(self.project))
        self.queue.parent.mkdir(parents=True)
        self.queue.write_text(json.dumps([RAW_ITEM]), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, script):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / script)], input="{}", capture_output=True,
            text=True, encoding="utf-8", env=self.env, cwd=str(self.project), timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_read_queue_prints_redacted_items(self):
        out = self._run("read_queue.py")
        self.assertNotIn("hunter22", out)
        self.assertEqual(json.loads(out)[0]["message"], "remember: DB_PASS=[REDACTED] for staging")

    def test_session_start_reminder_prints_redacted_items(self):
        out = self._run("session_start_reminder.py")
        self.assertIn("DB_PASS=[REDACTED]", out)
        self.assertNotIn("hunter22", out)

    def test_pre_compact_backup_holds_redacted_items(self):
        self._run("check_learnings.py")
        # One subfolder per project (see project_backup_dir).
        backups = list((self.home / ".claude" / "learnings-backups").glob("*/pre-compact-*.json"))
        self.assertEqual(len(backups), 1)
        text = backups[0].read_text(encoding="utf-8")
        self.assertNotIn("hunter22", text)
        self.assertEqual(json.loads(text)[0]["message"], "remember: DB_PASS=[REDACTED] for staging")


class TestMigrationsRedact(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.claude_dir = self.home / ".claude"
        self.project = self.home / "work" / "my_app"
        self.project.mkdir(parents=True)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_global_queue_migration_writes_redacted(self):
        self.claude_dir.mkdir()
        (self.claude_dir / "learnings-queue.json").write_text(
            json.dumps([{**RAW_ITEM, "project": str(self.project)}]), encoding="utf-8")
        migrate_global_queue()
        text = get_queue_path(str(self.project)).read_text(encoding="utf-8")
        self.assertNotIn("hunter22", text)
        self.assertIn("DB_PASS=[REDACTED]", text)

    def test_legacy_folder_migration_does_not_duplicate_redacted_items(self):
        resolved = str(self.project)
        legacy_name = _legacy_encode_project_path(resolved)
        current_name = get_project_folder_name(resolved)
        if any(c in legacy_name for c in ':*?"<>|') or legacy_name == current_name:
            self.skipTest("no distinct legacy folder on this platform")
        projects = self.claude_dir / "projects"
        # The current queue was saved redacted; the legacy one still holds
        # the raw text of the same item.
        save_queue_at(projects / current_name / "learnings-queue.json", [RAW_ITEM])
        (projects / legacy_name).mkdir(parents=True)
        (projects / legacy_name / "learnings-queue.json").write_text(
            json.dumps([RAW_ITEM]), encoding="utf-8")
        with patch("lib.reflect_utils.get_claude_dir", return_value=self.claude_dir):
            migrate_legacy_project_folder(resolved)
        text = (projects / current_name / "learnings-queue.json").read_text(encoding="utf-8")
        self.assertEqual(len(json.loads(text)), 1)
        self.assertNotIn("hunter22", text)


if __name__ == "__main__":
    unittest.main()
