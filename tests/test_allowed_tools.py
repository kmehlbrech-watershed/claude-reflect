#!/usr/bin/env python3
"""Slash commands pre-approve only the bundled scripts they run, nothing broad.

`allowed-tools` only pre-approves; unlisted tools still work after a
permission prompt. But a `!` context line that no rule allows aborts the
whole command, so every non-read-only part of every `!` line must match a
rule exactly as written (quotes included).
"""
import re
import unittest
from pathlib import Path

COMMANDS = Path(__file__).parent.parent / "commands"

# Claude Code runs these read-only commands without any rule.
READ_ONLY = ("pwd", "echo ", "cat ", "ls ", "head ", "tail ", "wc ", "test ")
BROAD = {"Bash", "Write", "Edit", "Bash(*)", "Bash(python3:*)", "Bash(python3 *)"}


def frontmatter_tools(text):
    match = re.search(r"^allowed-tools:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return []
    return [t.strip() for t in re.split(r",\s*(?![^()]*\))", match.group(1)) if t.strip()]


def bang_commands(text):
    return re.findall(r"!`([^`]+)`", text)


def command_parts(command):
    for part in re.split(r"\|\||&&|;|\|", command):
        part = re.sub(r"\s*\d?>\s*/dev/null", "", part).strip()
        if part:
            yield part


def allowed_by(part, tools):
    for tool in tools:
        m = re.fullmatch(r"Bash\((.+?)(?: \*|:\*)?\)", tool)
        if m and part.startswith(m.group(1)):
            return True
    return False


class TestAllowedTools(unittest.TestCase):
    def test_no_broad_pre_approvals(self):
        for path in sorted(COMMANDS.glob("*.md")):
            with self.subTest(command=path.name):
                broad = BROAD.intersection(frontmatter_tools(path.read_text(encoding="utf-8")))
                self.assertFalse(broad, f"{path.name} pre-approves {sorted(broad)}")

    def test_bash_rules_are_bundled_scripts_only(self):
        for path in sorted(COMMANDS.glob("*.md")):
            for tool in frontmatter_tools(path.read_text(encoding="utf-8")):
                if tool.startswith("Bash("):
                    with self.subTest(command=path.name, tool=tool):
                        self.assertRegex(tool, r'^Bash\(python3 "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/[\w.-]+\.py" \*\)$')
                        script = re.search(r"scripts/([\w.-]+\.py)", tool).group(1)
                        self.assertTrue((COMMANDS.parent / "scripts" / script).exists(), script)

    def test_every_bang_line_is_covered(self):
        for path in sorted(COMMANDS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            tools = frontmatter_tools(text)
            for command in bang_commands(text):
                for part in command_parts(command):
                    if part.startswith(READ_ONLY):
                        continue
                    with self.subTest(command=path.name, part=part):
                        self.assertTrue(allowed_by(part, tools), f"no allowed-tools rule covers: {part}")

    def test_no_inline_python_in_bang_lines(self):
        for path in sorted(COMMANDS.glob("*.md")):
            for command in bang_commands(path.read_text(encoding="utf-8")):
                with self.subTest(command=path.name):
                    self.assertNotIn("python3 -c", command)


if __name__ == "__main__":
    unittest.main()
