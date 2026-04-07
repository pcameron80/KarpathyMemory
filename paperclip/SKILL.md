---
name: karpathy-memory
description: >
  Cross-agent shared knowledge base built on Karpathy's LLM knowledge-base
  pattern. Use to leave a record of meaningful work (decisions, lessons,
  discoveries) so other agents — and Philip on his Mac — can pick up where
  you left off. Use to read the project's compiled knowledge (concepts,
  connections, prior Q&A) before starting work, so you don't repeat
  research that's already been done.
---

# Karpathy Memory Skill

You share a long-term memory with every other agent in this company AND
with Philip on his Mac. The memory is a per-project knowledge base. You
write to it after meaningful work; you read from it before starting new
work in a project you haven't touched recently.

The memory lives at `/paperclip/shared/brain/` inside this container (it's
a real-time mirror of Philip's Obsidian vault on his Mac, synced via
self-hosted CouchDB). Edits in either direction propagate within seconds.

## Layout

```
/paperclip/shared/brain/
├── 00-global/                  Cross-project context: user profile, preferences
├── 10-projects/
│   ├── paperclip/              Your own work as a Paperclip team
│   ├── greatpickdeals/         GreatPickDeals project
│   ├── paintcolorhq/           PaintColorHQ project
│   ├── town-bins/              Town Bins / dumpster-directory project
│   └── bloodhound/             Bloodhound project
└── 20-references/              Long-lived infra docs (Unraid, HA, etc.)
```

Each project folder has the same structure:

```
10-projects/<slug>/
├── AGENTS.md          Schema + project-specific compiler instructions
├── index.md           Master catalog of compiled knowledge — READ THIS FIRST
├── daily_logs/        Raw session summaries (immutable, append-only)
│   └── YYYY-MM-DD.md
└── knowledge/
    ├── concepts/      Atomic compiled articles
    ├── connections/   Cross-cutting insights linking 2+ concepts
    ├── qa/            Filed answers to past questions
    └── log.md         Build log
```

## How to choose your project slug

Match the work you're doing, not your agent identity:

- Working on the Paperclip platform itself, internal coordination, agent
  org structure → `paperclip`
- Writing/editing GreatPickDeals content → `greatpickdeals`
- PaintColorHQ work → `paintcolorhq`
- Town Bins / dumpster-directory work → `town-bins`
- Bloodhound work → `bloodhound`

If you're a manager (Ted, Sandy, Marshall, Captain) doing cross-cutting
strategic work that doesn't belong to a single project, use `paperclip`.

## Reading the memory before you work

At the start of any heartbeat where you're about to do meaningful work in
a project you haven't touched today:

1. Read `/paperclip/shared/brain/10-projects/<slug>/index.md`
2. If a relevant article exists, read it: `/paperclip/shared/brain/10-projects/<slug>/knowledge/concepts/<article>.md`
3. Read today's daily log if it exists: `/paperclip/shared/brain/10-projects/<slug>/daily_logs/$(date +%Y-%m-%d).md`
   — this tells you what other agents (and Philip) have already done today

You're a regular Unix user — just `cat` / `grep` / `find` these files. They're
real markdown on a real filesystem.

## Writing to the memory

There are two write paths depending on which model you run on.

### Path A — Claude Code agents (James, Barney, Lily, Robin, Ranjit)

You don't need to do anything explicit. The Karpathy memory hooks are
installed in `/paperclip/.claude/settings.json` and fire automatically:

- `SessionStart` → injects the project's index + AGENTS.md + recent daily
  log into your context (so what's in this skill happens automatically)
- `PreCompact` / `SessionEnd` → spawns a background extractor that reads
  your last ~30 conversation turns, calls Claude SDK to pull out
  decisions, lessons, gotchas, and action items, and appends them to the
  right project's `daily_logs/YYYY-MM-DD.md`

You can still write *explicitly* if you want a guaranteed entry (the
auto-extractor sometimes returns FLUSH_OK if it judges the conversation
too low-signal). Use Path B for that.

### Path B — Ollama agents (Ted, Sandy, Marshall, Captain) and explicit writes

Run this CLI command before exiting your heartbeat, when you've completed
something worth recording:

```bash
KARPATHY_VAULT=/paperclip/shared/brain \
  /usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory \
  python scripts/memory_log.py \
  --slug <project-slug> \
  --agent <your-name> \
  --section "Short title" \
  --content "What you did, what you learned, what's next"
```

Or pipe content via stdin:

```bash
echo "Drafted 3 GPD product roundups. Hit a snag with the new affiliate
disclosure rule — Sandy needs to weigh in." | \
  KARPATHY_VAULT=/paperclip/shared/brain \
  /usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory \
  python scripts/memory_log.py \
  --slug greatpickdeals --agent ranjit --section "Product roundups WIP"
```

## When to write — and when NOT to

**Write** when you've:
- Made a meaningful decision (chose tool X over Y, pivoted strategy, paused a project)
- Hit a non-obvious gotcha or bug worth other agents knowing about
- Completed a milestone (shipped a feature, published content, ran a key analysis)
- Discovered a fact that contradicts existing knowledge in the index
- Need to leave context for the next agent picking up this project

**Skip** when:
- The work was routine (running normal heartbeat checks)
- Nothing changed in the world
- You're just acknowledging an assignment without doing any work
- You're going to be back in <30 minutes anyway

A good heuristic: would Philip want to know this tomorrow morning? If yes,
log it. If no, skip.

## Format for your entry

The CLI handles formatting, but the `--content` should be written like a
mini status update:

```
Drafted 3 product roundups for GreatPickDeals (kitchen gadgets, kids'
educational toys, summer outdoor). All in /paperclip/repos/GreatPickDeals
under content/drafts/2026-04-07/. Board still needs to add product images.

Gotcha: the new affiliate disclosure rule (effective April 1) requires the
disclosure block to appear ABOVE the first affiliate link, not just at
top of post. Updated my drafts. Other content agents should do the same.

Next: handing off to QA for fact-checking before publish.
```

Direct, dense, future-self-readable. Markdown is fine. Don't write a novel.

## Compilation (you usually don't trigger this)

After 18:00 local time, the memory system automatically promotes that
day's daily log into structured `knowledge/concepts/` articles. You don't
trigger this; it happens via cron and Claude SDK extraction. Your job is
just to leave good raw material in `daily_logs/`.

## Errors

If `memory_log.py` fails because the slug doesn't exist, it will say so
and exit non-zero. Don't retry blindly — pick a real slug from the list
above. If the brain mirror is unreachable (`/paperclip/shared/brain/`
empty or missing), notify the CEO via Paperclip comment and continue
your work. Don't block on memory writes.

## Reference

- Source: `/paperclip/karpathy-memory/` (cloned from
  `https://github.com/pcameron80/KarpathyMemory`)
- CLI script: `scripts/memory_log.py`
- Schema: `AGENTS.md` in the karpathy-memory repo, plus per-project AGENTS.md
- Inspired by Karpathy's tweet: https://x.com/karpathy/status/2039805659525644595
