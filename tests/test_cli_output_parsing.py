#!/usr/bin/env python3
"""`claude -p --output-format json` puts the model's reply in a string `result`."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import lib.semantic_detector as sd  # noqa: E402

ANALYSIS = {"is_learning": True, "type": "correction", "confidence": 0.85,
            "reasoning": "tool preference", "extracted_learning": "Use uv, not pip"}


def envelope(reply_text, is_error=False):
    """What the real CLI prints: metadata plus the reply as a string."""
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                       "num_turns": 1, "result": reply_text})


def _proc(stdout):
    r = MagicMock()
    r.returncode, r.stdout, r.stderr = 0, stdout, ""
    return r


class TestParseCliOutput(unittest.TestCase):
    def test_reply_is_a_json_string(self):
        self.assertEqual(sd._parse_cli_output(envelope(json.dumps(ANALYSIS))), ANALYSIS)

    def test_reply_in_a_code_fence(self):
        reply = "```json\n" + json.dumps(ANALYSIS) + "\n```"
        self.assertEqual(sd._parse_cli_output(envelope(reply)), ANALYSIS)

    def test_reply_with_prose_around_it(self):
        reply = "Here is the analysis:\n" + json.dumps(ANALYSIS) + "\nHope that helps."
        self.assertEqual(sd._parse_cli_output(envelope(reply)), ANALYSIS)

    def test_reply_without_json(self):
        self.assertIsNone(sd._parse_cli_output(envelope("I can't help with that.")))

    def test_cli_error_envelope(self):
        self.assertIsNone(sd._parse_cli_output(envelope(json.dumps(ANALYSIS), is_error=True)))

    def test_bare_json_still_accepted(self):
        self.assertEqual(sd._parse_cli_output(json.dumps(ANALYSIS)), ANALYSIS)

    def test_non_json_stdout_with_embedded_object(self):
        self.assertEqual(sd._parse_cli_output("noise " + json.dumps(ANALYSIS)), ANALYSIS)


class TestCallSitesUseTheRealEnvelope(unittest.TestCase):
    """These all returned None/[] for real CLI output before the fix."""

    @patch("lib.semantic_detector.subprocess.run")
    def test_semantic_analyze(self, mock_run):
        mock_run.return_value = _proc(envelope(json.dumps(ANALYSIS)))
        result = sd.semantic_analyze("no, use uv not pip")
        self.assertIsNotNone(result)
        self.assertTrue(result["is_learning"])
        self.assertEqual(result["extracted_learning"], "Use uv, not pip")

    @patch("lib.semantic_detector.subprocess.run")
    def test_validate_tool_error(self, mock_run):
        reply = {"is_learnable": True, "refined_guideline": "Load .env first", "confidence": 0.8, "reasoning": "r"}
        mock_run.return_value = _proc(envelope("```json\n" + json.dumps(reply) + "\n```"))
        result = sd.validate_tool_error("env_undefined", "DATABASE_URL not set", 3, "Load .env")
        self.assertEqual(result["refined_guideline"], "Load .env first")

    @patch("lib.semantic_detector.subprocess.run")
    def test_detect_contradictions(self, mock_run):
        reply = {"contradictions": [{"entry1": "Use npm", "entry2": "Use pnpm", "conflict": "package manager"}]}
        mock_run.return_value = _proc(envelope(json.dumps(reply)))
        self.assertEqual(len(sd.detect_contradictions(["Use npm", "Use pnpm"])), 1)


if __name__ == "__main__":
    unittest.main()
