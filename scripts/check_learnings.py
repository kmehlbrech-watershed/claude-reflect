#!/usr/bin/env python3
"""Backup queue before context compaction. PreCompact hook.

Cross-platform compatible (Windows, macOS, Linux).
This script is called by Claude Code's PreCompact hook to back up
the learnings queue before context is compacted.
"""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import (
    in_nested_reflect_call,
    get_backup_dir,
    get_max_backups,
    load_queue,
    backup_timestamp,
    ensure_utf8_io,
    make_private,
    prune_backups,
    save_queue_at,
)


def main() -> int:
    """Main entry point."""
    ensure_utf8_io()
    # Our own nested `claude -p` (semantic_detector): not the user's session.
    if in_nested_reflect_call():
        return 0
    items = load_queue()
    if not items:
        return 0

    # Create backup directory if needed; it holds prompts, so owner-only.
    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    make_private(backup_dir, 0o700)

    # Save backup with timestamp. save_queue_at writes atomically and
    # owner-only, the same way the queue itself is written.
    backup_file = backup_dir / f"pre-compact-{backup_timestamp()}.json"
    save_queue_at(backup_file, items)

    # Backups used to accumulate forever; keep the newest few.
    prune_backups(backup_dir, get_max_backups())

    # Output informational message (non-blocking)
    print()
    print(f"Note: {len(items)} learning(s) backed up to {backup_file}")
    print("Run /reflect in new session to process.")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Never block on errors - just log and exit 0
        print(f"Warning: check_learnings.py error: {e}", file=sys.stderr)
        sys.exit(0)
