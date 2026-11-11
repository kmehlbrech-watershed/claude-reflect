---
description: View the learnings queue without processing
allowed-tools: Read, Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/read_queue.py" *)
---

## Context
- Queue file: per-project at `~/.claude/projects/<encoded-cwd>/learnings-queue.json`
- Legacy global queue (`~/.claude/learnings-queue.json`) is auto-migrated on first access

## Your Task

Display the current learnings queue in a readable format with confidence scores, patterns, and relative timestamps.

**Output format:**
```
════════════════════════════════════════════════════════════
LEARNINGS QUEUE: [N] items
════════════════════════════════════════════════════════════

[0.85] "use gpt-5.1 not gpt-5" (use-X-not-Y) - 2 days ago
[0.70] "perfect, that's exactly right" (positive) - 5 days ago
[0.90] "remember: always run tests" (explicit) - just now

════════════════════════════════════════════════════════════
Commands:
  /reflect        - Process and save learnings
  /skip-reflect   - Discard all learnings
════════════════════════════════════════════════════════════
```

If queue is empty:
```
════════════════════════════════════════════════════════════
LEARNINGS QUEUE: Empty
════════════════════════════════════════════════════════════
No learnings queued. Use "remember: <learning>" to add items,
or corrections will be auto-detected. Run /reflect to process.
════════════════════════════════════════════════════════════
```

## Implementation

**Step 1: Read the project-scoped queue:**
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/read_queue.py" --summary 2>/dev/null || echo "[]"
```

It prints one object per item: `timestamp`, `age` (already a relative time:
"just now", "X minutes ago", "X hours ago", "X days ago" or "X weeks ago"),
`type`, `confidence`, `decay_days`, `patterns` and `message`. Missing values
are `null` (or `[]` for `patterns`).

**Step 2: Format each item with:**
- **Confidence score**: `[0.XX]` format, from `confidence` (0.60 if null)
- **Message preview**: First 50 chars with "..." if longer, from `message`
- **Pattern name**: In parentheses, from `patterns` (show first pattern if multiple)
- **Relative timestamp**: `age`, as printed

**Formatting rules:**
- Confidence: Always show 2 decimal places `[0.85]`
- Message: Truncate at 50 chars, add "..." if longer
- Patterns: Show in parentheses `(pattern-name)`
- If patterns is empty, show `(auto)` for auto-detected or `(explicit)` for remember: (`type` is `explicit`)
- Time: Right-aligned relative timestamp

**Example output line:**
```
[0.85] "use gpt-5.1 not gpt-5" (use-X-not-Y) - 2 days ago
```
