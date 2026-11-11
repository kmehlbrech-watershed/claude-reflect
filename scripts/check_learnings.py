#!/usr/bin/env python3
"""Backup queue before context compaction. PreCompact hook.

Cross-platform compatible (Windows, macOS, Linux).
This script is called by Claude Code's PreCompact hook to back up
the learnings queue before context is compacted.
"""
import sys
import os
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.reflect_utils import (
    in_nested_reflect_call,
    backup_file_name,
    ensure_utf8_io,
    get_max_backups,
    get_queue_path,
    load_queue,
    load_queue_at,
    make_private_dir,
    project_backup_dir,
    project_dir_from_transcript,
    prune_backups,
    queue_path_for_folder,
    save_queue_at,
)


def _read_payload() -> dict:
    """The hook's JSON payload, or {} when there is none (hand-run hook)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    """Main entry point."""
    ensure_utf8_io()
    # Our own nested `claude -p` (semantic_detector): not the user's session.
    if in_nested_reflect_call():
        return 0

    # Like capture_learning.py: the payload's transcript_path sits in the
    # folder Claude Code uses for this project, which is authoritative.
    # Deriving it from cwd can name a different project's queue. Fall back
    # to the cwd encoder only when the field is absent.
    project_folder = project_dir_from_transcript(_read_payload().get("transcript_path"))
    if project_folder:
        queue_path = queue_path_for_folder(project_folder)
        items = load_queue_at(queue_path)
    else:
        items = load_queue()
        queue_path = get_queue_path()
    if not items:
        return 0

    # One backup folder per project, so pruning here never touches another
    # project's backups. It holds prompts, so owner-only.
    backup_dir = project_backup_dir(queue_path)
    make_private_dir(backup_dir.parent)
    make_private_dir(backup_dir)

    # save_queue_at writes atomically and owner-only, the same way the
    # queue itself is written.
    backup_file = backup_dir / backup_file_name()
    save_queue_at(backup_file, items)

    # Backups used to accumulate forever; keep the newest few.
    prune_backups(backup_dir, get_max_backups(), protect=backup_file)

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
