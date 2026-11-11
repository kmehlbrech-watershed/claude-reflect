#!/usr/bin/env python3
"""Print the current project's learnings queue as JSON. Helper for slash commands.

    read_queue.py            # the queue, as stored
    read_queue.py --count    # just the number of items
    read_queue.py --summary  # one compact object per item, with a relative age

--count and --summary exist so commands don't need an inline `python3 -c` or
a `| jq` pipeline (neither can be pre-approved narrowly) to count or list the
items.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import load_queue  # noqa: E402


def relative_age(iso_timestamp, now=None):
    """'just now', '5 minutes ago', '2 days ago', '3 weeks ago' or 'unknown'."""
    try:
        dt = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = ((now or datetime.now(timezone.utc)) - dt).total_seconds()
    except (TypeError, ValueError):
        return "unknown"
    for limit, size, unit in ((3600, 60, "minute"), (86400, 3600, "hour"),
                              (604800, 86400, "day"), (None, 604800, "week")):
        if seconds < 60:
            return "just now"
        if limit is None or seconds < limit:
            n = int(seconds / size)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "unknown"


def summarize(items, now=None):
    """Fields the queue views show. Missing values stay null, so each command
    applies its own default (e.g. confidence 0.5 in --review, 0.60 in
    /view-queue)."""
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        out.append({
            "index": i,
            "timestamp": item.get("timestamp"),
            "age": relative_age(item.get("timestamp"), now),
            "type": item.get("type"),
            "confidence": item.get("confidence"),
            "decay_days": item.get("decay_days"),
            "patterns": item.get("patterns") or [],
            "message": item.get("message", ""),
        })
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--count", action="store_true", help="Print only the number of items")
    mode.add_argument("--summary", action="store_true",
                      help="Print a compact JSON list with each item's relative age")
    args = parser.parse_args(argv)

    items = load_queue()
    if args.count:
        print(len(items))
    elif args.summary:
        print(json.dumps(summarize(items), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(items, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
