"""
Seed project folders in the Brain-Personal vault.

For each project in registry.json (or one named via --slug), create:
  10-projects/<slug>/AGENTS.md      (per-project compiler instructions)
  10-projects/<slug>/index.md       (knowledge base index, loaded by SessionStart)
  10-projects/<slug>/knowledge/log.md  (build log)
  10-projects/<slug>/{daily_logs, knowledge/concepts, knowledge/connections, knowledge/qa}

Existing files are NEVER overwritten — re-running this is safe.

Usage:
    uv run python scripts/bootstrap_project.py            # seed every project + _scratch
    uv run python scripts/bootstrap_project.py --slug paintcolorhq
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from router import load_registry, resolve  # noqa: E402


AGENTS_TEMPLATE = """# AGENTS.md — {slug}

This project follows the Karpathy memory schema. The full spec lives at
`~/.claude/karpathy-memory/repo/AGENTS.md` (or in the [KarpathyMemory repo]
(https://github.com/pcameron80/KarpathyMemory)).

## Compiler instructions

When compiling daily logs into the knowledge base for this project:

- Encyclopedia style: factual, concise, third-person where appropriate
- Use Obsidian-style `[[wikilinks]]` (no `.md` extension)
- Every concept article needs YAML frontmatter with `title`, `sources`, `created`, `updated`
- Prefer updating existing articles over creating near-duplicates
- A single daily log may touch 3-10 knowledge articles

## Project-specific notes

(Add anything Claude should always know about this project — tech stack,
conventions, gotchas, pinned context.)
"""

INDEX_TEMPLATE = """# {slug} — Knowledge Base Index

This file is the master catalog for the `{slug}` project. Claude Code's
SessionStart hook reads this on every session.

## Articles

| Article | Summary | Compiled From | Updated |
|---------|---------|---------------|---------|

(empty — no articles compiled yet)

## Quick links

- [AGENTS.md](AGENTS.md) — compiler instructions for this project
- [daily_logs/](daily_logs/) — raw session summaries
- [knowledge/concepts/](knowledge/concepts/) — atomic articles
- [knowledge/connections/](knowledge/connections/) — cross-cutting insights
- [knowledge/qa/](knowledge/qa/) — filed query answers
"""

LOG_TEMPLATE = """# Build Log — {slug}

Append-only chronological record of compile/query/lint operations for this project.

## [{today}T00:00:00] seed
- Project folder seeded by bootstrap_project.py
"""


def seed(slug: str) -> None:
    reg = load_registry()
    if slug == "_scratch":
        proj_path = "~"  # _scratch is the fallback; resolve via that
    else:
        proj = next((p for p in reg["projects"] if p["slug"] == slug), None)
        if not proj:
            print(f"  SKIP {slug}: not in registry")
            return
        proj_path = proj["path"]

    rp = resolve(proj_path)
    rp.ensure_dirs()

    created = []

    if not rp.agents_file.exists():
        rp.agents_file.write_text(AGENTS_TEMPLATE.format(slug=rp.slug), encoding="utf-8")
        created.append("AGENTS.md")
    if not rp.index_file.exists():
        rp.index_file.write_text(INDEX_TEMPLATE.format(slug=rp.slug), encoding="utf-8")
        created.append("index.md")
    if not rp.log_file.exists():
        rp.log_file.write_text(
            LOG_TEMPLATE.format(slug=rp.slug, today=date.today().isoformat()),
            encoding="utf-8",
        )
        created.append("knowledge/log.md")

    if created:
        print(f"  ✓ {rp.slug:18} created: {', '.join(created)}")
    else:
        print(f"  · {rp.slug:18} already seeded")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="seed only this project (default: all)")
    args = parser.parse_args()

    reg = load_registry()
    slugs = [args.slug] if args.slug else [p["slug"] for p in reg["projects"]] + ["_scratch"]

    print(f"Seeding {len(slugs)} project(s) in vault: {reg['vaults'][reg['default_vault']]}")
    for s in slugs:
        seed(s)
    print("Done.")


if __name__ == "__main__":
    main()
