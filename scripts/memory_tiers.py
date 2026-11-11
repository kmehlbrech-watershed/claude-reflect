#!/usr/bin/env python3
"""Report this project's memory tiers as JSON. Helper for /reflect.

/reflect used to run these lib/reflect_utils.py calls as inline
`python3 -c "import sys; sys.path.insert(...)"` snippets. Those can't be
pre-approved narrowly, so every run prompted for each of them (and dontAsk
mode refused them). Run from the project directory:

    memory_tiers.py                    # --files: memory files + line counts
    memory_tiers.py --auto-memory      # auto memory dir and its entries
    memory_tiers.py --entries          # bullets from every tier (dedupe)
    memory_tiers.py --topics           # auto memory topic keywords
    memory_tiers.py --contradictions   # semantic check of CLAUDE.md bullets

Nothing here takes session text as an argument.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import (  # noqa: E402
    _AUTO_MEMORY_TOPICS,
    ensure_utf8_io,
    find_claude_files,
    get_auto_memory_path,
    get_claude_dir,
    read_all_memory_entries,
    read_auto_memory,
)


def _line_count(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def memory_files(root=None):
    """find_claude_files(), each with its line count."""
    files = find_claude_files(root)
    for entry in files:
        entry["lines"] = _line_count(entry["path"])
    return files


def auto_memory(root=None):
    memory_dir = get_auto_memory_path(root)
    return {
        "memory_dir": str(memory_dir),
        "exists": memory_dir.is_dir(),
        "files": read_auto_memory(root),
    }


def claude_md_bullets(root=None):
    """`- ` bullets from ~/.claude/CLAUDE.md and ./CLAUDE.md, with line numbers."""
    root = Path(root) if root else Path.cwd()
    bullets = []
    for label, path in (("~/.claude/CLAUDE.md", get_claude_dir() / "CLAUDE.md"),
                        ("./CLAUDE.md", root / "CLAUDE.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("- "):
                bullets.append({"file": label, "line": n, "text": stripped[2:].strip()})
    return bullets


def contradictions(root=None, model=None):
    from lib.semantic_detector import detect_contradictions
    bullets = claude_md_bullets(root)
    return {
        "entries": bullets,
        "contradictions": detect_contradictions([b["text"] for b in bullets], model=model),
    }


def main(argv=None) -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--files", action="store_true", help="Memory files with line counts (default)")
    mode.add_argument("--auto-memory", action="store_true", help="Auto memory directory and entries")
    mode.add_argument("--entries", action="store_true", help="Bullet entries from every tier")
    mode.add_argument("--topics", action="store_true", help="Auto memory topic keywords")
    mode.add_argument("--contradictions", action="store_true",
                      help="Contradicting bullets in ~/.claude/CLAUDE.md and ./CLAUDE.md")
    parser.add_argument("--model", default=None, help="Model for --contradictions")
    args = parser.parse_args(argv)

    if args.auto_memory:
        result = auto_memory()
    elif args.entries:
        result = read_all_memory_entries()
    elif args.topics:
        result = {"topics": _AUTO_MEMORY_TOPICS, "fallback": "general"}
    elif args.contradictions:
        result = contradictions(model=args.model)
    else:
        result = memory_files()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never let a slash command die on this
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
