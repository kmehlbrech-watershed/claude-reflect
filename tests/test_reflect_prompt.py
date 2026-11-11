#!/usr/bin/env python3
"""Guards on commands/reflect.md for the opt-in AGENTS.md sync.

The /reflect command is a prompt, so these only check that the rules that
keep AGENTS.md opt-in are still written down. They don't test behaviour.
"""
import re
import unittest
from pathlib import Path

REFLECT_MD = Path(__file__).parent.parent / "commands" / "reflect.md"


class TestReflectPromptAgentsMd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = REFLECT_MD.read_text(encoding="utf-8")

    def test_env_context_line_uses_printf(self):
        self.assertIn(
            "!`printf '%s\\n' \"${CLAUDE_REFLECT_AGENTS_MD:-off}\"`", self.text)
        self.assertNotRegex(self.text, r"echo \"\$\{CLAUDE_REFLECT_AGENTS_MD")

    def test_agents_md_is_never_a_referenced_doc_target(self):
        routing = next(
            line for line in self.text.splitlines()
            if line.startswith("- **Topic-aligned referenced docs**"))
        self.assertIn("Never an AGENTS.md", routing)

    def test_sync_state_is_resolved_in_step_0(self):
        step0 = self.text.index("### Step 0: Check Arguments")
        step05 = self.text.index("### Step 0.5:")
        section = self.text[step0:step05]
        for state in ("`on`", "`off`", "`missing`", "`same-file`", "`included`"):
            self.assertIn(state, section)

    def test_confirmation_offers_skipping_agents_md(self):
        self.assertIn('"Apply, but skip AGENTS.md"', self.text)
        self.assertIn("[W] to AGENTS.md", self.text)

    def test_step_7c_is_gated_on_step_0_state(self):
        start = self.text.index("**7c. Apply to AGENTS.md (opt-in):**")
        end = self.text.index("### Step 8: Clear Queue")
        section = self.text[start:end]
        self.assertIn("state from Step 0 is `on`", section)
        self.assertNotIn("apply the SAME learnings", section)
        # The old global-looking example must not come back.
        self.assertIsNone(re.search(r"gpt-5\.\d", section))


if __name__ == "__main__":
    unittest.main()
