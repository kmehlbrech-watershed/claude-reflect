# claude-reflect

[![GitHub stars](https://img.shields.io/github/stars/BayramAnnakov/claude-reflect?style=flat-square)](https://github.com/BayramAnnakov/claude-reflect/stargazers)
[![Version](https://img.shields.io/badge/version-3.3.1-blue?style=flat-square)](https://github.com/BayramAnnakov/claude-reflect/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-332%20passing-brightgreen?style=flat-square)](https://github.com/BayramAnnakov/claude-reflect/actions)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square)](https://github.com/BayramAnnakov/claude-reflect#platform-support)

A self-learning system for Claude Code that captures corrections and discovers workflow patterns — turning them into permanent memory and reusable skills.

**Two ways to run it:**

| | **Plugin** (Python) | **Mod** (`reflect-mod`, new in 3.3) |
|---|---|---|
| You correct Claude | a hook queues it | a model check sees it at once, in any language |
| You review | later, with `/reflect` | right away, in a band above the prompt |
| Runs on | Claude Code, plus Codex/Cursor via opt-in AGENTS.md sync | Claude Code 2.1.286+ with mods |

![reflect-mod: a correction becomes a rule you can save with one press](assets/reflect-mod-band.png)

Pick one. [Install the plugin](#installation) or [the mod](#new-claude-reflect-as-a-mod-early-access).

## What it does

### 1. Learn from Corrections

When you correct Claude ("no, use gpt-5.1 not gpt-5"), it remembers forever.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  You correct    │ ──► │  Hook captures  │ ──► │  /reflect adds  │
│  Claude Code    │     │  to queue       │     │  to CLAUDE.md   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
      (automatic)            (automatic)            (manual review)
```

### 2. Discover Workflow Patterns (NEW in v2)

Analyzes your session history to find repeating tasks that could become reusable commands.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Your past      │ ──► │ /reflect-skills │ ──► │   Generates     │
│  sessions       │     │ finds patterns  │     │   /commands     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
    (68 sessions)         (AI-powered)            (you approve)
```

Example: You've asked "review my productivity" 12 times → suggests creating `/daily-review`

## Key Features

| Feature | What it does |
|---------|--------------|
| **Permanent Memory** | Corrections sync to CLAUDE.md — Claude remembers across sessions |
| **Skill Discovery** | Finds repeating patterns in your history → generates commands |
| **Multi-language** | AI understands corrections in any language |
| **Skill Improvement** | Corrections during `/deploy` improve the deploy skill itself |

## Installation

```bash
# Add the marketplace
claude plugin marketplace add bayramannakov/claude-reflect

# Install the plugin
claude plugin install claude-reflect@claude-reflect-marketplace

# IMPORTANT: Restart Claude Code to activate the plugin
```

After installation, **restart Claude Code** (exit and reopen). Then hooks auto-configure and commands are ready.

> **First run?** When you run `/reflect` for the first time, you'll be prompted to scan your past sessions for learnings.

### Prerequisites

- [Claude Code](https://claude.ai/code) CLI installed
- Python 3.6+ (included on most systems)

### Platform Support

- **macOS**: Fully supported
- **Linux**: Fully supported
- **Windows**: Fully supported (native Python, no WSL required)

### New: claude-reflect as a mod (early access)

Claude Code [mods](https://claude.dev/blog/getting-started-with-claude-code-mods/) run inside the session, so the
same idea works without a queue you have to remember to review:

- **Every short prompt** (500 characters or less, not "ok"/"continue", not a slash command) goes to a one-shot
  model check (`haiku` by default) that decides whether it holds a reusable rule. It reads intent, not keywords,
  so corrections in any language count ("нет, используй pnpm" works).
- **A band above the prompt** shows each rule it finds, with **Save**, **Global/This repo instead**, **Edit** and
  **Skip**. You decide in the moment; repeats merge into one row with a count (`×3`).
- **Save** adds a bullet inside a marked section of `~/.claude/CLAUDE.md` or `./CLAUDE.md`, and the rule applies
  in the current session at once. Nothing is written without a press.
- `remember: <rule>` skips the model. `/reflect-queue` lists what it found; `/reflect-pause` turns it off.

```bash
claude plugin marketplace add bayramannakov/claude-reflect
claude plugin install reflect-mod@claude-reflect-marketplace
```

Needs Claude Code **2.1.286 or newer** with mods enabled. Each checked prompt is one small model call on your
own Claude account. Install **either** the mod **or** the Python plugin above, not both: with both, every
correction is captured twice. The Python plugin stays the choice for `/reflect --scan-history`, `--dedupe`,
`--organize`, `/reflect-skills`, and for Codex/Cursor users. Details: [`mod/README.md`](mod/README.md).

## Commands

| Command | Description |
|---------|-------------|
| `/reflect` | Process queued learnings with human review |
| `/reflect --scan-history` | Scan ALL past sessions for missed learnings |
| `/reflect --dry-run` | Preview changes without applying |
| `/reflect --targets` | Show detected config files (CLAUDE.md, AGENTS.md) |
| `/reflect --agents-md` | Also sync project learnings into an existing `AGENTS.md` (off by default; set `CLAUDE_REFLECT_AGENTS_MD=true` in your shell profile or the `"env"` block of `settings.json` to always enable) |
| `/reflect --review` | Show queue with confidence scores and decay status |
| `/reflect --dedupe` | Find and consolidate similar entries in CLAUDE.md |
| `/reflect --include-tool-errors` | Include tool execution errors in scan |
| `/reflect-skills` | Discover skill candidates from repeating patterns |
| `/reflect-skills --days N` | Analyze last N days (default: 14) |
| `/reflect-skills --project <path>` | Analyze specific project |
| `/reflect-skills --all-projects` | Scan all projects for cross-project patterns |
| `/reflect-skills --dry-run` | Preview patterns without generating skill files |
| `/skip-reflect` | Discard all queued learnings |
| `/view-queue` | View pending learnings without processing |

## How It Works

![claude-reflect in action](assets/reflect-demo.jpg)

### Two-Stage Process

**Stage 1: Capture (Automatic)**

Hooks run automatically to detect and queue corrections:

| Hook | Trigger | Purpose |
|------|---------|---------|
| `session_start_reminder.py` | Session start | Shows pending learnings reminder |
| `capture_learning.py` | Every prompt | Detects correction patterns and queues them |
| `check_learnings.py` | Before compaction | Backs up queue and informs user |
| `post_commit_reminder.py` | After git commit | Reminds to run /reflect after completing work |

The queue (`~/.claude/projects/<project>/learnings-queue.json`) and its backups (`~/.claude/learnings-backups/<project>/`) hold your prompts verbatim, so on macOS and Linux they are created owner-only (`0600`, directories `0700`). Windows has no equivalent of these modes, so there the files get your profile folder's usual permissions. Each project keeps only its newest 20 backups; set `CLAUDE_REFLECT_MAX_BACKUPS` to change that (`0` keeps all). Backups written by earlier versions sit directly in `~/.claude/learnings-backups/` and are never pruned or changed; delete them yourself once you no longer need them.

**Stage 2: Process (Manual)**

Run `/reflect` to review and apply queued learnings to CLAUDE.md.

### Detection Methods

Claude-reflect uses a **hybrid detection approach**:

**1. Regex patterns (real-time capture)**

Fast pattern matching during sessions detects:

- **Corrections**: `"no, use X"` / `"don't use Y"` / `"actually..."` / `"that's wrong"`
- **Positive feedback**: `"Perfect!"` / `"Exactly right"` / `"Great approach"`
- **Explicit markers**: `"remember:"` — highest confidence

**2. Semantic AI validation (during /reflect)**

When you run `/reflect`, an AI-powered semantic filter:
- **Multi-language support** — understands corrections in any language
- **Better accuracy** — filters out false positives from regex
- **Cleaner learnings** — extracts concise, actionable statements

Example: A Spanish correction like `"no, usa Python"` is correctly detected even though it doesn't match English patterns.

The check runs `claude -p` with no tools, no MCP servers, none of your customizations (CLAUDE.md, plugins, hooks) and no saved session (`--tools "" --strict-mcp-config --safe-mode --no-session-persistence`), since it only classifies text already in the prompt and that text comes from transcripts. It uses your normal Claude Code login. claude-reflect's own hooks stay quiet inside it (`CLAUDE_REFLECT_NESTED=1`), so classifier prompts are never captured as learnings. On an older Claude Code that rejects one of those flags, it drops only that flag (replacing `--tools ""` with `--disallowedTools` and the built-in tool names), keeps the rest, and prints a one-line warning; update Claude Code to get all of them.

Each captured learning has a **confidence score** (0.60-0.95). The final score is the higher of regex and semantic confidence.

### Human Review

When you run `/reflect`, Claude presents a summary table with options:
- **Apply** - Accept the learning and add to CLAUDE.md
- **Edit before applying** - Modify the learning text first
- **Skip** - Don't apply this learning

### Multi-Target Sync

Approved learnings are synced to:
- `~/.claude/CLAUDE.md` (global - applies to all projects)
- `./CLAUDE.md` (project-specific)
- `./**/CLAUDE.md` (subdirectories - auto-discovered)
- `./.claude/commands/*.md` (skill files - when correction relates to a skill)
- `AGENTS.md` (opt-in with `--agents-md`, if it exists - works with Codex, Cursor, Aider, Jules, Zed, Factory). Only learnings routed to a shared project target are copied there, never global or personal ones, and existing entries in its claude-reflect section are kept.
- Docs reachable from the above via `@filename` includes or markdown links (bounded depth, cycles handled, code blocks/external URLs skipped) — useful when CLAUDE.md delegates guidance to `standards.md`, `architecture.md`, etc.

Run `/reflect --targets` to see which files will be updated.

### Skill Discovery

Run `/reflect-skills` to discover repeating patterns in your sessions that could become reusable skills:

```
/reflect-skills                 # Analyze current project (last 14 days)
/reflect-skills --days 30       # Analyze last 30 days
/reflect-skills --all-projects  # Analyze all projects (slower)
/reflect-skills --dry-run       # Preview patterns without generating files
```

**Features:**
- **AI-powered detection** — uses reasoning, not regex, to find patterns
- **Semantic similarity** — detects same intent across different phrasings
- **Project-aware** — groups patterns by project, suggests correct location
- **Smart assignment** — asks where each skill should go (project vs global)
- **Generates skill files** — creates draft skills in `.claude/commands/`

**How it works:**

The skill discovers patterns by analyzing your session history semantically. Different phrasings of the same intent are recognized:

```
Session 1: "review my productivity for today"
Session 2: "how was my focus this afternoon?"
Session 3: "check my ActivityWatch data"
Session 4: "evaluate my work hours"
```

Claude reasons: *"These 4 requests have the same intent - reviewing productivity data. The workflow is: fetch time tracking data → categorize activities → calculate focus score. This is a strong candidate for /daily-review."*

**Example output:**
```
════════════════════════════════════════════════════════════
SKILL CANDIDATES DISCOVERED
════════════════════════════════════════════════════════════

Found 2 potential skills from analyzing 68 sessions:

1. /daily-review (High) — from my-productivity-tools
   → Review productivity using time tracking data
   Evidence: 15 similar requests
   Corrections learned: "use local timezone", "chat apps can be work"

2. /deploy-app (High) — from my-webapp
   → Deploy application with pre-flight checks
   Evidence: 10 similar requests
   Corrections learned: "always run tests first"

════════════════════════════════════════════════════════════

Which skills should I generate?
> [1] /daily-review, [2] /deploy-app

Where should each skill be created?
┌──────────────────────┬─────────────────────────┐
│ /daily-review        │ my-productivity-tools   │
│ /deploy-app          │ my-webapp               │
└──────────────────────┴─────────────────────────┘

Skills created:
  ~/projects/my-productivity-tools/.claude/commands/daily-review.md
  ~/projects/my-webapp/.claude/commands/deploy-app.md
```

**Generated skill file example:**

```markdown
---
description: Deploy application with pre-flight checks
allowed-tools: Bash, Read, Write
---

## Context
Deployment scripts in ./scripts/deploy/

## Your Task
Deploy the application to the specified environment.

### Steps
1. Run test suite
2. Build production assets
3. Deploy to target environment
4. Verify deployment health

### Guardrails
- Always run tests before deploying
- Never deploy to production on Fridays
- Check for pending migrations

---
*Generated by /reflect-skills from 10 session patterns*
```

### Skill Improvement Routing

When you correct Claude while using a skill (e.g., `/deploy`), the correction can be routed back to the skill file itself:

```
User: /deploy
Claude: [deploys without running tests]
User: "no, always run tests before deploying"

→ /reflect detects this relates to /deploy
→ Offers to add learning to .claude/commands/deploy.md
→ Skill file updated with new step
```

This makes skills smarter over time, not just CLAUDE.md.

## Upgrading

**AGENTS.md sync is now opt-in.** `/reflect` no longer writes `AGENTS.md` just because it exists. To keep syncing project learnings into it, pass `--agents-md`, or set `CLAUDE_REFLECT_AGENTS_MD=true` in your shell profile or in the `"env"` block of `~/.claude/settings.json`.

### From v2.0.x or earlier

If you see errors like "Duplicate hooks file detected" or "No such file or directory" after updating, you need to clear the plugin cache. This is due to known Claude Code caching issues:
- [#14061](https://github.com/anthropics/claude-code/issues/14061) - `/plugin update` doesn't invalidate cache
- [#15369](https://github.com/anthropics/claude-code/issues/15369) - Uninstall doesn't clear cached files

```bash
# 1. Uninstall the plugin
claude plugin uninstall claude-reflect@claude-reflect-marketplace

# 2. Clear both caches (required!)
rm -rf ~/.claude/plugins/marketplaces/claude-reflect-marketplace
rm -rf ~/.claude/plugins/cache/claude-reflect-marketplace

# 3. Exit Claude Code completely (restart terminal or close app)

# 4. Reinstall
claude plugin install claude-reflect@claude-reflect-marketplace
```

### Standard Update

For normal updates (when no cache issues):

```bash
# Use the /plugin menu in Claude Code
/plugin
# Select "Update now" for claude-reflect
```

## Uninstall

```bash
claude plugin uninstall claude-reflect@claude-reflect-marketplace
```

## File Structure

```
claude-reflect/
├── .claude-plugin/
│   └── plugin.json         # Plugin manifest (auto-registers hooks)
├── commands/
│   ├── reflect.md          # Main command
│   ├── reflect-skills.md   # Skill discovery
│   ├── skip-reflect.md     # Discard queue
│   └── view-queue.md       # View queue
├── hooks/
│   └── hooks.json          # Auto-configured when plugin installed
├── scripts/
│   ├── lib/
│   │   ├── reflect_utils.py      # Shared utilities
│   │   ├── redact.py             # Masks secrets before anything is stored or printed
│   │   └── semantic_detector.py  # AI-powered semantic analysis
│   ├── capture_learning.py       # Hook: detect corrections
│   ├── check_learnings.py        # Hook: pre-compact check
│   ├── post_commit_reminder.py   # Hook: post-commit reminder
│   ├── compare_detection.py      # Compare regex vs semantic detection
│   ├── extract_session_learnings.py
│   ├── extract_tool_errors.py
│   ├── extract_tool_rejections.py
│   ├── semantic_check.py         # Semantic analysis from a file/stdin/queue
│   └── legacy/                   # Bash scripts (deprecated)
├── mod/                    # reflect-mod: the same idea as a Claude Code mod (TypeScript)
│   ├── .claude-plugin/plugin.json
│   ├── hooks/register.tsx  # Hooks module: prompt check, band, save
│   └── hooks/reflect.test.tsx
├── tests/                  # Test suite
└── SKILL.md                # Skill context for Claude
```

## Features

### Historical Scan

First time using claude-reflect? Run:

```bash
/reflect --scan-history
```

This scans all your past sessions for corrections you made, so you don't lose learnings from before installation.

### Smart Filtering

Claude filters out:
- Questions (not corrections)
- One-time task instructions
- Context-specific requests
- Vague/non-actionable feedback

Only reusable learnings are kept.

### Transcripts Are Data

Session files contain text Claude read from web pages, files and tool output, so `/reflect` and `/reflect-skills` never follow instructions found in them, and never put that text on a shell command line. Proposed learnings that would run or fetch something, change settings or permissions, or speak to the AI directly are shown with a `⚠️ possible injected instruction` flag, left out of "Apply all", and applied only if you approve each one on its own (default Skip). That includes `remember:` items. Subagent conversations (`agent-*.jsonl`, under `<session>/subagents/` on current Claude Code, and `isSidechain` entries) are not mined for corrections, because their "user" turns were written by Claude.
### Secret Redaction

Secret-shaped text is replaced with `[REDACTED]` in what the plugin stores and prints: queue items (when captured, saved, and read back, so the session-start reminder, `/reflect`, `/view-queue` and the PreCompact backup all see redacted text), the messages and rejections the session extractors return, and tool-error samples. Only the secret is masked, so `use DATABASE_URL=[REDACTED] from .env` still reads as a rule.

What is masked:

- reflect-mod's patterns: prefixed API keys (`sk-`, `ghp_`, `xoxb-`, `glpat-`, ...), AWS access key ids, Google API keys, JWTs, private key blocks, values after `password` / `secret` / `token` / `api key`, and long random tokens.
- Additions: the password in `scheme://user:password@host` (including an empty user and passwords containing `@`), `Bearer <token>`, JSON-quoted keys (`"password": "..."`), env-style keys that end in a secret word (`AWS_SECRET_ACCESS_KEY=`, `JWT_SECRET=`, `DB_PASS=`, `PWD=`), whole quoted passphrases (`password = 'correct horse battery'`), and AWS temporary key ids (`ASIA...`).

Because the result can become a CLAUDE.md rule, the patterns are tuned to avoid mangling ordinary learnings, and miss some secrets as a result. A value after a secret key is masked only when it looks like a secret: it has a digit or symbol, or is a quoted password or passphrase (and for `the password is X`, it is quoted or has a digit). Words, types, booleans, numbers, `$VAR`/`${{ secrets.X }}` references, paths and URLs are left alone, so `the API key is loaded from .env`, `token: string`, `secret=True` and `max_tokens=4096` survive. Long tokens must mix letters and digits within one run; git SHAs and other hex, UUIDs, slugs such as model ids, path segments and file names are not masked. A password that is a plain word with no digit or symbol (`password: hunter`) is not caught.

Set `CLAUDE_REFLECT_REDACT=false` to turn redaction off.

Limits:

- Files already on disk are not rewritten by reading them. A queue is scrubbed the next time it is saved, and backups in `~/.claude/learnings-backups/` written before this version keep whatever they captured; delete them if they may hold secrets.
- During `/reflect`, Claude can still read session files directly, so review proposed entries before they are written.
- The deprecated bash scripts in `scripts/legacy/` do not redact.

### Duplicate Detection

Before adding a learning, existing CLAUDE.md content is checked. If similar content exists, you can:
- Merge with existing entry
- Replace the old entry
- Skip the duplicate

### Semantic Deduplication

Over time, CLAUDE.md can accumulate similar entries. Run `/reflect --dedupe` to:
- Find semantically similar entries (even with different wording)
- Propose consolidated versions
- Clean up redundant learnings

Example:
```
Before:
  - Use gpt-5.1 for complex tasks
  - Prefer gpt-5.1 for reasoning
  - gpt-5.1 is better for hard problems

After:
  - Use gpt-5.1 for complex reasoning tasks
```

## Tips

1. **Use explicit markers** for important learnings:
   ```
   remember: always use venv for Python projects
   ```

2. **Run /reflect after git commits** - The hook reminds you, but make it a habit

3. **Historical scan on new machines** - When setting up a new dev environment:
   ```
   /reflect --scan-history --days 90
   ```

4. **Project vs Global** - Model names and general patterns go global; project-specific conventions stay in project CLAUDE.md

5. **Discover skills monthly** - Run `/reflect-skills --days 30` monthly to find automation opportunities you might have missed

6. **Skills get smarter** - When you correct Claude during a skill, that correction can be routed back to the skill file itself via `/reflect`

7. **Extend session retention** - Claude Code deletes local sessions after 30 days by default. Since claude-reflect relies on session history for `/reflect --scan-history` and `/reflect-skills`, you can extend this in `~/.claude/settings.json`:
   ```json
   { "cleanupPeriodDays": 90 }
   ```
   Pick the shortest window that covers how far back you want to reflect. Transcripts hold everything you and Claude typed and read, including secrets and customer data, so check your organization's data-retention policy before keeping them longer.

## Contributing

Pull requests welcome! Please read the contributing guidelines first.

## License

MIT
