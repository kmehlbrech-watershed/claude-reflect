#!/usr/bin/env python3
"""Run semantic analysis on messages read from a file, stdin, or the queue.

Slash commands call this instead of `python3 -c "...semantic_analyze('<message>')"`.
Messages come from session transcripts, which can hold text Claude read from
web pages and tool output; pasted into a shell command line, a quote or
`$(...)` in them would run as shell. Here they only ever travel as data:

    semantic_check.py PATH        # JSON file written with the Write tool
    semantic_check.py -           # JSON on stdin
    semantic_check.py --queue     # this project's learnings queue

Input JSON is a list of strings or of objects with a "message" key (a single
string or object is accepted too). Output is a JSON list, in input order:

    [{"index": 0, "message": "...", "analysis": {...} | null}, ...]

"analysis" is semantic_analyze()'s result, or null when the Claude CLI was
unavailable or failed; callers then fall back to heuristics.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import ensure_utf8_io, load_queue  # noqa: E402
from lib.semantic_detector import DEFAULT_TIMEOUT, semantic_analyze  # noqa: E402


def _messages(data):
    if isinstance(data, (str, dict)):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of strings or {\"message\": ...} objects")
    out = []
    for item in data:
        if isinstance(item, dict):
            item = item.get("message", "")
        if not isinstance(item, str):
            raise ValueError("each item must be a string or have a string \"message\"")
        out.append(item)
    return out


def main(argv=None) -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", default=None,
                        help="JSON file of messages, or - for stdin")
    parser.add_argument("--queue", action="store_true",
                        help="Analyze this project's learnings queue")
    parser.add_argument("--model", default=None, help="Model for semantic analysis")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Timeout per message, in seconds")
    args = parser.parse_args(argv)

    if args.queue == (args.input is not None):
        parser.error("give exactly one of: a JSON file path, - for stdin, or --queue")

    try:
        if args.queue:
            data = load_queue()
        elif args.input == "-":
            data = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as f:
                data = json.load(f)
        messages = _messages(data)
    except (OSError, ValueError) as e:  # JSONDecodeError is a ValueError
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2

    results = [
        {
            "index": i,
            "message": message,
            "analysis": semantic_analyze(message, timeout=args.timeout, model=args.model),
        }
        for i, message in enumerate(messages)
    ]
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
