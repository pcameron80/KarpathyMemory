"""
Compile daily conversation logs into structured knowledge articles.

This is the "LLM compiler" - it reads daily logs (source code) and produces
organized knowledge articles (the executable).

Uses OpenAI's Codex CLI (gpt-5.4 by default) as the writing agent. The
reviewer in the pipeline is Gemini (see hermes.py), so compile + hermes
give you true cross-family authorship + review: anything one model family
blindly asserts gets caught by the other.

Usage:
    uv run python compile.py                    # compile new/changed logs only
    uv run python compile.py --all              # force recompile everything
    uv run python compile.py --file daily/2026-04-01.md  # compile a specific log
    uv run python compile.py --dry-run          # show what would be compiled
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from config import (
    AGENTS_FILE,
    CONCEPTS_DIR,
    CONNECTIONS_DIR,
    DAILY_DIR,
    KNOWLEDGE_DIR,
    PROJECT_ROOT,
    now_iso,
)
from utils import (
    file_hash,
    list_raw_files,
    list_wiki_articles,
    load_state,
    read_wiki_index,
    save_state,
)

# ── Codex CLI configuration ───────────────────────────────────────────
CODEX_BIN = os.environ.get("CODEX_BIN", "/usr/local/bin/codex")
COMPILE_MODEL = os.environ.get("COMPILE_MODEL", "gpt-5.4")
COMPILE_TIMEOUT_SEC = int(os.environ.get("COMPILE_TIMEOUT", "1800"))  # 30 min per log

# Repo root is still useful for a few legacy imports but no longer passed
# to the agent as its working directory — the agent runs inside the vault
# project dir so workspace-write sandboxing permits writes to knowledge/.
ROOT_DIR = Path(__file__).resolve().parent.parent


def compile_daily_log(log_path: Path, state: dict, model: str = COMPILE_MODEL) -> float:
    """Compile a single daily log into knowledge articles using Codex CLI.

    Returns a cost placeholder (always 0.0 — the Codex CLI doesn't expose
    per-invocation cost via `exec`, and you're on a subscription anyway).
    """
    log_content = log_path.read_text(encoding="utf-8")
    schema = AGENTS_FILE.read_text(encoding="utf-8")
    wiki_index = read_wiki_index()

    # Read existing articles for context
    existing_articles_context = ""
    existing = {}
    for article_path in list_wiki_articles():
        rel = article_path.relative_to(KNOWLEDGE_DIR)
        existing[str(rel)] = article_path.read_text(encoding="utf-8")

    if existing:
        parts = []
        for rel_path, content in existing.items():
            parts.append(f"### {rel_path}\n```markdown\n{content}\n```")
        existing_articles_context = "\n\n".join(parts)

    timestamp = now_iso()

    prompt = f"""You are a knowledge compiler. Your job is to read a daily conversation log
and extract knowledge into structured wiki articles.

## Schema (AGENTS.md)

{schema}

## Current Wiki Index

{wiki_index}

## Existing Wiki Articles

{existing_articles_context if existing_articles_context else "(No existing articles yet)"}

## Daily Log to Compile

**File:** {log_path.name}

{log_content}

## Your Task

Read the daily log above and compile it into wiki articles following the schema exactly.

### Rules:

1. **Extract key concepts** - Identify 3-7 distinct concepts worth their own article
2. **Create concept articles** in `knowledge/concepts/` - One .md file per concept
   - Use the exact article format from AGENTS.md (YAML frontmatter + sections)
   - Include `sources:` in frontmatter pointing to the daily log file
   - Use `[[concepts/slug]]` wikilinks to link to related concepts
   - Write in encyclopedia style - neutral, comprehensive
3. **Create connection articles** in `knowledge/connections/` if this log reveals non-obvious
   relationships between 2+ existing concepts
4. **Update existing articles** if this log adds new information to concepts already in the wiki
   - Read the existing article, add the new information, add the source to frontmatter
5. **Update knowledge/index.md** - Add new entries to the table
   - Each entry: `| [[path/slug]] | One-line summary | source-file | {timestamp[:10]} |`
6. **Append to knowledge/log.md** - Add a timestamped entry:
   ```
   ## [{timestamp}] compile | {log_path.name}
   - Source: daily/{log_path.name}
   - Articles created: [[concepts/x]], [[concepts/y]]
   - Articles updated: [[concepts/z]] (if any)
   ```

### File paths:
- Write concept articles to: {CONCEPTS_DIR}
- Write connection articles to: {CONNECTIONS_DIR}
- Update index at: {KNOWLEDGE_DIR / 'index.md'}
- Append log at: {KNOWLEDGE_DIR / 'log.md'}

### Quality standards:
- Every article must have complete YAML frontmatter
- Every article must link to at least 2 other articles via [[wikilinks]]
- Key Points section should have 3-5 bullet points
- Details section should have 2+ paragraphs
- Related Concepts section should have 2+ entries
- Sources section should cite the daily log with specific claims extracted
"""

    cost = 0.0

    # Shell out to Codex CLI in non-interactive mode.
    #   --cd              run inside the vault project dir so workspace-write
    #                     permits writes to daily_logs/ and knowledge/
    #   --sandbox workspace-write
    #                     let the agent write/edit files, but not run arbitrary
    #                     shell commands outside the workspace
    #   --skip-git-repo-check
    #                     the vault is not a git repo
    #   --dangerously-bypass-approvals-and-sandbox
    #                     fully headless, no human approval prompts; acceptable
    #                     because we already constrain writes via --cd + --sandbox
    #                     and the compiler's prompt is hard-coded to only touch
    #                     knowledge/*
    cmd = [
        CODEX_BIN, "exec",
        "--cd", str(PROJECT_ROOT),
        "--sandbox", "workspace-write",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m", model,
        prompt,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        print(f"  Error: codex timeout after {COMPILE_TIMEOUT_SEC}s")
        return 0.0
    except FileNotFoundError:
        print(f"  Error: codex binary not found at {CODEX_BIN}")
        return 0.0
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        return 0.0

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        print(f"  Error: codex rc={result.returncode}: {stderr_tail}")
        return 0.0

    # Codex `exec` streams progress to stdout; keep the last ~40 lines for
    # debugging if something goes sideways, but don't flood the console.
    if result.stdout:
        tail = "\n".join(result.stdout.splitlines()[-3:])
        if tail.strip():
            print(f"  {tail}")

    # Update state
    rel_path = log_path.name
    state.setdefault("ingested", {})[rel_path] = {
        "hash": file_hash(log_path),
        "compiled_at": now_iso(),
        "cost_usd": cost,
    }
    state["total_cost"] = state.get("total_cost", 0.0) + cost
    save_state(state)

    return cost


def main():
    parser = argparse.ArgumentParser(description="Compile daily logs into knowledge articles")
    parser.add_argument("--all", action="store_true", help="Force recompile all logs")
    parser.add_argument("--file", type=str, help="Compile a specific daily log file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be compiled")
    args = parser.parse_args()

    state = load_state()

    # Determine which files to compile
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = DAILY_DIR / target.name
        if not target.exists():
            # Try resolving relative to project root
            target = ROOT_DIR / args.file
        if not target.exists():
            print(f"Error: {args.file} not found")
            sys.exit(1)
        to_compile = [target]
    else:
        all_logs = list_raw_files()
        if args.all:
            to_compile = all_logs
        else:
            to_compile = []
            for log_path in all_logs:
                rel = log_path.name
                prev = state.get("ingested", {}).get(rel, {})
                if not prev or prev.get("hash") != file_hash(log_path):
                    to_compile.append(log_path)

    if not to_compile:
        print("Nothing to compile - all daily logs are up to date.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Files to compile ({len(to_compile)}):")
    for f in to_compile:
        print(f"  - {f.name}")

    if args.dry_run:
        return

    # Compile each file sequentially
    for i, log_path in enumerate(to_compile, 1):
        print(f"\n[{i}/{len(to_compile)}] Compiling {log_path.name}...")
        compile_daily_log(log_path, state)
        print(f"  Done.")

    articles = list_wiki_articles()
    print(f"\nCompilation complete.")
    print(f"Knowledge base: {len(articles)} articles")


if __name__ == "__main__":
    main()
