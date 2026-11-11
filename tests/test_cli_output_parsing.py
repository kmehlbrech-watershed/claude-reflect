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


class TestFindingTheAnswerInTheReply(unittest.TestCase):
    """The reply text is free-form; find the right object in it."""

    def test_braces_in_string_values_inside_a_fence(self):
        answer = dict(ANALYSIS, reasoning="unbalanced } and { and }} here",
                      extracted_learning="Wrap env vars as ${VAR}")
        reply = "```json\n" + json.dumps(answer) + "\n```"
        self.assertEqual(sd._parse_cli_output(envelope(reply), "is_learning"), answer)

    def test_prose_with_braces_before_the_json(self):
        reply = "The message mentions {foo} and a {bar: baz} map.\n" + json.dumps(ANALYSIS)
        self.assertEqual(sd._parse_cli_output(envelope(reply), "is_learning"), ANALYSIS)

    def test_example_object_before_the_answer(self):
        reply = ('The format was {"type": "correction", "confidence": 0.9}; also the '
                 'template {"is_learning": true or false}. Answer: ' + json.dumps(ANALYSIS))
        self.assertEqual(sd._parse_cli_output(envelope(reply), "is_learning"), ANALYSIS)

    def test_fenced_answer_preferred_over_earlier_object(self):
        reply = ('Not {"is_learning": "maybe"} -- final:\n```json\n'
                 + json.dumps(ANALYSIS) + "\n```")
        self.assertEqual(sd._parse_cli_output(envelope(reply), "is_learning"), ANALYSIS)

    def test_no_object_with_the_expected_key(self):
        reply = json.dumps({"something": "else"})
        self.assertIsNone(sd._parse_cli_output(envelope(reply), "is_learning"))

    def test_without_expected_key_first_object_wins(self):
        reply = 'a {"x": 1} b {"is_learning": true}'
        self.assertEqual(sd._extract_json_from_text(reply), {"x": 1})


class TestErrorAndUnsupportedOutput(unittest.TestCase):
    def test_envelope_without_result(self):
        out = json.dumps({"type": "result", "subtype": "error_max_turns", "num_turns": 2})
        self.assertIsNone(sd._parse_cli_output(out, "is_learning"))

    def test_error_envelope_without_result(self):
        out = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})
        self.assertIsNone(sd._parse_cli_output(out, "is_learning"))

    def test_is_error_with_result(self):
        out = envelope(json.dumps(ANALYSIS), is_error=True)
        self.assertIsNone(sd._parse_cli_output(out, "is_learning"))

    def test_verbose_array_output(self):
        out = json.dumps([{"type": "system", "subtype": "init"},
                          json.loads(envelope(json.dumps(ANALYSIS)))])
        self.assertIsNone(sd._parse_cli_output(out, "is_learning"))

    def test_stream_json_lines(self):
        out = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                         json.dumps({"type": "assistant", "message": {"content": [
                             {"type": "text", "text": json.dumps(ANALYSIS)}]}}),
                         envelope(json.dumps(ANALYSIS))])
        self.assertIsNone(sd._parse_cli_output(out, "is_learning"))

    @patch("lib.semantic_detector.subprocess.run")
    def test_error_envelope_is_no_answer_not_a_no(self, mock_run):
        mock_run.return_value = _proc(json.dumps(
            {"type": "result", "subtype": "error_max_turns", "is_error": True}))
        self.assertIsNone(sd.semantic_analyze("no, use uv not pip"))
        self.assertIsNone(sd.validate_tool_error("t", "e", 3, "g"))
        self.assertEqual(sd.detect_contradictions(["a", "b"]), [])

    @patch("lib.semantic_detector.subprocess.run")
    def test_tool_error_reply_missing_verdict(self, mock_run):
        reply = {"refined_guideline": "Load .env first", "confidence": 0.8}
        mock_run.return_value = _proc(envelope(json.dumps(reply)))
        self.assertIsNone(sd.validate_tool_error("env_undefined", "x", 3, "Load .env"))


class TestExplicitItemsSurviveValidation(unittest.TestCase):
    NOT_A_LEARNING = {"is_learning": False, "type": None, "confidence": 0.2,
                      "reasoning": "one-off", "extracted_learning": None}

    @patch("lib.semantic_detector.semantic_analyze")
    def test_remember_and_explicit_items_kept_on_a_no(self, mock_analyze):
        mock_analyze.return_value = self.NOT_A_LEARNING
        items = [
            {"type": "auto", "message": "Remember: deploy from main only", "confidence": 0.9},
            {"type": "explicit", "message": "always run make lint", "confidence": 0.9},
            {"type": "auto", "message": "fix this typo", "confidence": 0.6},
        ]
        kept = sd.validate_queue_items(items)
        self.assertEqual([i["message"] for i in kept], [items[0]["message"], items[1]["message"]])
        # The model's view is still attached; the original confidence is not lowered.
        self.assertEqual(kept[0]["semantic_confidence"], 0.2)
        self.assertEqual(kept[0]["semantic_reasoning"], "one-off")
        self.assertEqual(kept[0]["confidence"], 0.9)

    @patch("lib.semantic_detector.subprocess.run")
    def test_remember_item_kept_through_the_real_envelope(self, mock_run):
        reply = dict(self.NOT_A_LEARNING)
        mock_run.return_value = _proc(envelope(json.dumps(reply)))
        items = [{"type": "auto", "message": "remember: use uv", "confidence": 0.9}]
        self.assertEqual(len(sd.validate_queue_items(items)), 1)

    def test_prompt_text_has_no_literal_marker(self):
        self.assertNotIn("remember:", sd.ANALYSIS_PROMPT.lower())
        self.assertIn('"remember" marker', sd.ANALYSIS_PROMPT)


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
