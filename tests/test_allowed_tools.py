#!/usr/bin/env python3
"""Slash commands pre-approve only the bundled scripts they run, nothing broad.

`allowed-tools` only pre-approves; unlisted tools still work after a
permission prompt (and are refused in dontAsk mode). But a `!` context line
that no rule allows aborts the whole command, so every non-read-only part of
every `!` line must match a rule exactly as written (quotes included), and
the read-only parts must stay inside the project or plugin: Claude Code only
auto-allows read-only commands within the working directory.
"""
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
COMMANDS = ROOT / "commands"
SCRIPTS = ROOT / "scripts"

# Claude Code runs these read-only commands without any rule (inside the
# working directory only, hence the path check in readonly_path_problem).
READ_ONLY = {"pwd", "echo", "cat", "ls", "head", "tail", "wc", "test"}
BROAD_BASH = {"Bash", "Bash(*)", "Bash(python3:*)", "Bash(python3 *)"}
# Any form of these writes files: Write, Write(**), Edit(./x), MultiEdit, ...
WRITE_TOOL = re.compile(r"^(Write|Edit|MultiEdit|NotebookEdit)\w*\b")
BUNDLED_RULE = re.compile(r'^Bash\(python3 "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([\w.-]+\.py)" \*\)$')
SCRIPT_CALL = re.compile(r'python3 "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([\w.-]+\.py)"')
PLUGIN_ROOT = "${CLAUDE_PLUGIN_ROOT}"


# --- parsing -----------------------------------------------------------------

def split_frontmatter(text):
    """(frontmatter lines, body). No leading `---` block: ([], text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return lines[1:], ""


def _split_items(value):
    """Split on commas that are not inside (...)."""
    return re.split(r",\s*(?![^()]*\))", value)


def _unquote(item):
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "'\"":
        item = item[1:-1]
    return item.strip()


def frontmatter_tools(text):
    """allowed-tools as a list, whether written as a comma string, a YAML
    flow list (`[Bash, Write]`, possibly over several lines) or a YAML block
    list (`- Bash` lines)."""
    fm, _ = split_frontmatter(text)
    for i, line in enumerate(fm):
        m = re.match(r"""^\s*['"]?allowed-tools['"]?\s*:\s*(.*)$""", line)
        if not m:
            continue
        value = m.group(1).strip()
        if not value:  # block list
            items = []
            for nxt in fm[i + 1:]:
                item = re.match(r"^\s*-\s+(.*)$", nxt)
                if item:
                    items.append(item.group(1))
                elif nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
            return [t for t in (_unquote(x) for x in items) if t]
        if value.startswith("["):  # flow list, maybe multi-line
            j = i
            while "]" not in value and j + 1 < len(fm):
                j += 1
                value += " " + fm[j].strip()
            value = value.strip()[1:].rsplit("]", 1)[0]
        return [t for t in (_unquote(x) for x in _split_items(value)) if t]
    return []


def bang_commands(text):
    """Every `!` context command: from "!`" to the last backtick on its line,
    so a nested backtick shows up inside the command instead of ending it."""
    commands = []
    for line in text.splitlines():
        start = line.find("!`")
        if start == -1:
            continue
        rest = line[start + 2:]
        end = rest.rfind("`")
        commands.append(rest[:end] if end != -1 else rest)
    return commands


def bash_blocks(text):
    """Lines of ```bash fenced blocks, without comments and blank lines."""
    lines = []
    for block in re.findall(r"^\s*```(?:bash|sh|shell)\s*\n(.*?)^\s*```", text, re.M | re.S):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


def command_parts(command):
    for part in re.split(r"\|\||&&|;|\|", command):
        part = re.sub(r"\s*\d?>\s*/dev/null", "", part).strip()
        if part:
            yield part


def _tokens(part):
    try:
        return shlex.split(part)
    except ValueError:
        return part.split()


def is_read_only(part):
    tokens = _tokens(part)
    return bool(tokens) and tokens[0] in READ_ONLY


def readonly_path_problem(part):
    """A read-only command that reaches outside the project or plugin, e.g.
    `cat ~/.claude/settings.json`, is not auto-allowed. Returns the token."""
    for token in _tokens(part)[1:]:
        if token.startswith(PLUGIN_ROOT):
            continue
        if token.startswith(("~", "/")) or "$HOME" in token or "${HOME}" in token:
            return token
        if ".." in Path(token).parts:
            return token
    return None


def shell_problem(command):
    if "$(" in command:
        return "$( command substitution"
    if "`" in command:
        return "backtick command substitution"
    # Verified on Claude Code 2.1.322: `!`echo "${X:-off}"`` (and printf)
    # fails with "A variable in braces in this command can't be checked
    # before it runs". Only ${CLAUDE_PLUGIN_ROOT} is substituted beforehand.
    if re.search(r"\$\{(?!CLAUDE_PLUGIN_ROOT\})", command):
        return "${...} variable other than CLAUDE_PLUGIN_ROOT"
    return None


def is_broad(tool):
    return tool in BROAD_BASH or bool(WRITE_TOOL.match(tool))


def allowed_by(part, tools):
    for tool in tools:
        m = re.fullmatch(r"Bash\((.+?)(?: \*|:\*)?\)", tool)
        if m and part.startswith(m.group(1)):
            return True
    return False


def commands_md():
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(COMMANDS.glob("*.md"))]


# --- the commands --------------------------------------------------------------

class TestAllowedTools(unittest.TestCase):
    def test_every_command_has_allowed_tools(self):
        for name, text in commands_md():
            with self.subTest(command=name):
                self.assertTrue(frontmatter_tools(text), f"{name}: no allowed-tools parsed")

    def test_no_broad_pre_approvals(self):
        for name, text in commands_md():
            with self.subTest(command=name):
                broad = [t for t in frontmatter_tools(text) if is_broad(t)]
                self.assertFalse(broad, f"{name} pre-approves {broad}")

    def test_bash_rules_are_bundled_scripts_only(self):
        for name, text in commands_md():
            for tool in frontmatter_tools(text):
                if tool.startswith("Bash"):
                    with self.subTest(command=name, tool=tool):
                        m = BUNDLED_RULE.match(tool)
                        self.assertTrue(m, f"not a bundled-script rule: {tool}")
                        self.assertTrue((SCRIPTS / m.group(1)).exists(), m.group(1))

    def test_every_script_rule_is_used(self):
        for name, text in commands_md():
            _, body = split_frontmatter(text)
            used = set(SCRIPT_CALL.findall(body))
            for tool in frontmatter_tools(text):
                m = BUNDLED_RULE.match(tool)
                if m:
                    with self.subTest(command=name, script=m.group(1)):
                        self.assertIn(m.group(1), used, f"{name} pre-approves a script it never runs")

    def test_every_script_call_has_a_rule(self):
        for name, text in commands_md():
            _, body = split_frontmatter(text)
            ruled = {BUNDLED_RULE.match(t).group(1) for t in frontmatter_tools(text) if BUNDLED_RULE.match(t)}
            for script in sorted(set(SCRIPT_CALL.findall(body))):
                with self.subTest(command=name, script=script):
                    self.assertIn(script, ruled, f"{name} runs {script} without a rule (prompts every run)")

    def test_every_bang_line_is_covered(self):
        for name, text in commands_md():
            tools = frontmatter_tools(text)
            for command in bang_commands(text):
                with self.subTest(command=name, line=command):
                    self.assertIsNone(shell_problem(command))
                for part in command_parts(command):
                    with self.subTest(command=name, part=part):
                        if is_read_only(part):
                            self.assertIsNone(readonly_path_problem(part),
                                              "read-only command outside the project")
                        else:
                            self.assertTrue(allowed_by(part, tools), f"no allowed-tools rule covers: {part}")

    def test_bash_blocks_only_run_pre_approved_commands(self):
        # Shell the prompt tells Claude to run should go through without a
        # prompt: bundled scripts by rule, or `echo` as a fallback value.
        for name, text in commands_md():
            tools = frontmatter_tools(text)
            for line in bash_blocks(split_frontmatter(text)[1]):
                with self.subTest(command=name, line=line):
                    self.assertIsNone(shell_problem(line))
                    for part in command_parts(line):
                        self.assertTrue(allowed_by(part, tools) or _tokens(part)[:1] == ["echo"],
                                        f"not pre-approved: {part}")

    def test_no_inline_python(self):
        for name, text in commands_md():
            with self.subTest(command=name):
                for command in bang_commands(text) + bash_blocks(text):
                    self.assertNotIn("python3 -c", command)
                self.assertNotIn("sys.path.insert", text)
                self.assertNotRegex(text, r"(?m)^\s*```python")


# --- the checks themselves -----------------------------------------------------

class TestCheckers(unittest.TestCase):
    """The parser and rules above must catch the shapes they claim to."""

    def test_comma_string(self):
        text = "---\ndescription: x\nallowed-tools: Read, Bash(python3 \"a, b\" *), Write\n---\nbody"
        self.assertEqual(frontmatter_tools(text), ["Read", 'Bash(python3 "a, b" *)', "Write"])

    def test_yaml_flow_list_is_caught(self):
        text = "---\nallowed-tools: [Bash, Write]\n---\n"
        self.assertEqual(frontmatter_tools(text), ["Bash", "Write"])
        self.assertTrue(all(is_broad(t) for t in frontmatter_tools(text)))

    def test_multiline_flow_list(self):
        text = "---\nallowed-tools: [Read,\n  'Edit']\n---\n"
        self.assertEqual(frontmatter_tools(text), ["Read", "Edit"])

    def test_yaml_block_list_is_caught(self):
        text = "---\nallowed-tools:\n  - Read\n  - \"Bash\"\n  - Edit(./CLAUDE.md)\ndescription: x\n---\n"
        self.assertEqual(frontmatter_tools(text), ["Read", "Bash", "Edit(./CLAUDE.md)"])

    def test_allowed_tools_outside_frontmatter_is_ignored(self):
        self.assertEqual(frontmatter_tools("---\ndescription: x\n---\nallowed-tools: Bash\n"), [])

    def test_write_and_edit_variants_are_broad(self):
        for tool in ("Write", "Write(**)", "Edit", "Edit(./CLAUDE.md)", "MultiEdit",
                     "NotebookEdit", "Bash", "Bash(*)", "Bash(python3:*)"):
            with self.subTest(tool=tool):
                self.assertTrue(is_broad(tool))
        for tool in ("Read", "Grep", "Glob", 'Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/x.py" *)'):
            with self.subTest(tool=tool):
                self.assertFalse(is_broad(tool))

    def test_command_substitution_in_bang_lines_is_caught(self):
        text = "- a: !`echo $(whoami)`\n- b: !`echo `whoami``\n"
        commands = bang_commands(text)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(shell_problem(c) for c in commands))

    def test_brace_variables_in_bang_lines_are_caught(self):
        for command in ('echo "${CLAUDE_REFLECT_AGENTS_MD:-off}"',
                        "printf '%s\\n' \"${CLAUDE_REFLECT_AGENTS_MD:-off}\""):
            with self.subTest(command=command):
                self.assertIsNotNone(shell_problem(command))
        self.assertIsNone(shell_problem('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/x.py" --count'))

    def test_read_only_outside_project_is_caught(self):
        for part in ("cat ~/.claude/settings.json", "ls /etc", "head $HOME/.netrc",
                     "cat ../secrets", 'test -f "${HOME}/x"'):
            with self.subTest(part=part):
                self.assertTrue(is_read_only(part))
                self.assertIsNotNone(readonly_path_problem(part))
        for part in ("pwd", 'echo "[]"', "wc -l CLAUDE.md", 'cat "${CLAUDE_PLUGIN_ROOT}/README.md"'):
            with self.subTest(part=part):
                self.assertIsNone(readonly_path_problem(part))

    def test_rule_must_match_as_written(self):
        tools = ['Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/read_queue.py" *)']
        self.assertTrue(allowed_by('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/read_queue.py" --count', tools))
        self.assertFalse(allowed_by("python3 ${CLAUDE_PLUGIN_ROOT}/scripts/read_queue.py", tools))


if __name__ == "__main__":
    unittest.main()
