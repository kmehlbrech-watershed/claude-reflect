#!/usr/bin/env python3
"""Print the resolved claude-reflect paths for a project, as JSON.

Slash commands call this instead of re-deriving the project folder in shell.
Three separate copies of that shell logic had drifted out of sync with
lib/reflect_utils.py, each in a way that failed silently:

  * /reflect read and cleared ~/.claude/learnings-queue.json, the pre-3.1
    global queue, which migrate_global_queue() deletes on first access -- so
    it processed nothing and cleared nothing.
  * /reflect-skills encoded the folder with `sed 's|/|-|g'`, which leaves
    "_", "." and spaces alone and so pointed at a folder Claude Code never
    writes to.
  * /reflect --scan-history grepped ~/.claude/projects by basename and
    guessed at underscores with `tr '_' '-'`.

There is one encoder. This exposes it.

It also reports the facts /reflect used to gather with shell one-liners
(`test -f`, `grep`, `python3 -c "...samefile..."`, `touch`), so the command
can pre-approve this one script instead of prompting for each of them:

  * "initialized": whether <session_dir>/.reflect-initialized exists
    (--mark-initialized creates it);
  * "agents_md": whether ./AGENTS.md exists, its line count, whether it is
    the same file as ./CLAUDE.md, and the ./CLAUDE.md lines that @-include it;
  * --all-projects lists main session files across every project folder;
  * --agents-md-env prints CLAUDE_REFLECT_AGENTS_MD (or "off" if unset or
    empty) and nothing else. /reflect's context line uses it because a `!`
    line with `${VAR:-off}` fails Claude Code's permission check ("A variable
    in braces ... can't be checked before it runs") and aborts the command.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import (  # noqa: E402
    ensure_utf8_io,
    get_auto_memory_path,
    get_claude_dir,
    get_project_folder_name,
    get_queue_path,
    make_private_dir,
)

INIT_MARKER = ".reflect-initialized"
AGENTS_MD_ENV = "CLAUDE_REFLECT_AGENTS_MD"

# ./CLAUDE.md lines that @-include AGENTS.md: `@AGENTS.md` or `@./AGENTS.md`,
# not preceded by a word character or a dot (so `foo@AGENTS.md` is not one).
AGENTS_INCLUDE = re.compile(r"(?:^|[^\w.])@(?:\./)?AGENTS\.md")


def _line_count(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def agents_md_state(project):
    """Facts /reflect needs to pick an AGENTS.md sync state."""
    agents = os.path.join(project, "AGENTS.md")
    claude = os.path.join(project, "CLAUDE.md")
    exists = os.path.isfile(agents)
    same_file = False
    include_lines = []
    if exists and os.path.isfile(claude):
        try:
            same_file = os.path.samefile(claude, agents)
        except OSError:
            same_file = False
        try:
            with open(claude, encoding="utf-8", errors="replace") as f:
                include_lines = [n for n, line in enumerate(f, start=1)
                                 if AGENTS_INCLUDE.search(line)]
        except OSError:
            include_lines = []
    return {
        "path": agents,
        "exists": exists,
        "lines": _line_count(agents) if exists else None,
        "same_file_as_claude_md": same_file,
        "claude_md_include_lines": include_lines,
    }


def all_project_sessions(projects_dir, cutoff):
    """Main session files one level down in every project folder, newest
    first. Never deeper (that would reach <session>/subagents/), and never
    agent-*.jsonl, whose user turns Claude wrote."""
    paths = []
    if projects_dir.is_dir():
        for path in projects_dir.glob("*/*.jsonl"):
            if path.name.startswith("agent-") or not path.is_file():
                continue
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
            paths.append(str(path))
    return sorted(paths, key=os.path.getmtime, reverse=True)


def main() -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None,
                        help="Project directory (default: cwd)")
    parser.add_argument("--days", type=float, default=None,
                        help="Only list session files modified in the last N days")
    parser.add_argument("--all-projects", action="store_true",
                        help="List main session files of every project in session_files")
    parser.add_argument("--mark-initialized", action="store_true",
                        help="Create <session_dir>/.reflect-initialized, then print the paths")
    parser.add_argument("--agents-md-env", action="store_true",
                        help=f"Print only ${AGENTS_MD_ENV} (\"off\" if unset or empty)")
    args = parser.parse_args()

    if args.agents_md_env:
        print(os.environ.get(AGENTS_MD_ENV) or "off")
        return 0

    project = args.project or os.getcwd()
    folder = get_project_folder_name(project)
    session_dir = get_claude_dir() / "projects" / folder

    if args.mark_initialized:
        make_private_dir(session_dir)
        (session_dir / INIT_MARKER).touch(mode=0o600, exist_ok=True)

    cutoff = time.time() - args.days * 86400 if args.days is not None else None
    sessions = []
    subagents = []
    if args.all_projects:
        sessions = all_project_sessions(get_claude_dir() / "projects", cutoff)
    elif session_dir.is_dir():

        def newest_first(paths):
            paths = [str(p) for p in paths]
            if cutoff is not None:
                paths = [p for p in paths if os.path.getmtime(p) >= cutoff]
            return sorted(paths, key=os.path.getmtime, reverse=True)

        # agent-*.jsonl are subagent conversations: their "user" turns are
        # prompts Claude wrote, not the user. Listed apart so callers mine
        # them only for tool rejections, which do come from the user.
        # Current Claude Code writes them to <session-uuid>/subagents/;
        # older versions put them next to the session files.
        for path in newest_first(session_dir.glob("*.jsonl")):
            (subagents if os.path.basename(path).startswith("agent-") else sessions).append(path)
        subagents = newest_first(
            subagents + [str(p) for p in session_dir.glob("*/subagents/agent-*.jsonl")]
        )

    print(json.dumps({
        "project_dir": str(project),
        "folder_name": folder,
        "session_dir": str(session_dir),
        "session_dir_exists": session_dir.is_dir(),
        "session_files": sessions,
        "subagent_files": subagents,
        "queue_path": str(get_queue_path(project)),
        "memory_dir": str(get_auto_memory_path(project)),
        "initialized": (session_dir / INIT_MARKER).is_file(),
        "agents_md": agents_md_state(str(project)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never let a slash command die on this
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
