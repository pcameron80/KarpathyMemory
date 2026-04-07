"""
Seed a project's daily_logs/ from its git history.

Each commit becomes a bullet under a "## Git history (seeded)" section in
`daily_logs/<author-date>.md`. Idempotent — re-running replaces the seeded
section in place without touching any real conversation logs that happen to
share the same day.

After seeding, run the normal pipeline (compile → hermes → lint) and the
seeded history will be turned into concept articles in knowledge/.

Usage:

    # Seed one project (uses the repo path from registry.json)
    uv run python scripts/seed_from_git.py --slug town-bins

    # Seed every project in the registry
    uv run python scripts/seed_from_git.py --all

    # Point at a specific repo (overrides registry)
    uv run python scripts/seed_from_git.py --slug paperclip --repo ~/Documents/GitHub/Paperclip

    # Preview only
    uv run python scripts/seed_from_git.py --slug paintcolorhq --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from router import load_registry, resolve  # noqa: E402

# Authors whose commits should be ignored entirely.
BOT_AUTHORS = {
    "dependabot[bot]",
    "dependabot-preview[bot]",
    "renovate[bot]",
    "github-actions[bot]",
    "copilot[bot]",
    "snyk-bot",
    "pre-commit-ci[bot]",
}

SEED_HEADER = "## Git history (seeded)"
SEED_END_MARKER = "<!-- /seed_from_git -->"

# Markers we pass to git log to split commits unambiguously. Using control
# characters + explicit strings avoids any collision with commit message
# contents AND keeps us robust to --name-only putting the file list *after*
# the formatted body (which breaks a simple "record separator at end" scheme).
US = "\x1f"
START = "\x01__KARPATHY_COMMIT_START__\x01"
END = "\x01__KARPATHY_COMMIT_END__\x01"


@dataclass
class Commit:
    sha: str
    author: str
    date: str  # YYYY-MM-DD (author date)
    subject: str
    body: str
    files: list[str]


def git_log_commits(repo: Path) -> list[Commit]:
    """Walk git log --no-merges in chronological order and yield Commits.

    Output format per commit:

        <START><sha><US><author><US><date><US><subject><US><body><END>
        file_a
        file_b
        ...

    We wrap every commit in START/END markers so the file list (which git
    emits *after* the pretty format when --name-only is used) can be clearly
    attributed to its commit.
    """
    fmt = START + US.join(["%h", "%an", "%ad", "%s", "%b"]) + END
    cmd = [
        "git", "-C", str(repo), "log",
        "--no-merges",
        "--reverse",
        "--date=short",
        f"--pretty=format:{fmt}",
        "--name-only",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"  git log failed in {repo}: {e.stderr[:200]}", file=sys.stderr)
        return []

    commits: list[Commit] = []
    for raw in out.split(START):
        if not raw.strip():
            continue
        # Each chunk is: <fields><END>\n<files...>
        head, _, tail = raw.partition(END)
        parts = head.split(US)
        if len(parts) < 5:
            continue
        sha, author, date, subject = parts[0], parts[1], parts[2], parts[3]
        body = US.join(parts[4:]).strip()  # body may itself contain US (unlikely but safe)

        # Files: one per line in tail, skip blank lines introduced by git
        files = [line for line in tail.splitlines() if line.strip()]

        if author in BOT_AUTHORS:
            continue

        commits.append(Commit(
            sha=sha,
            author=author,
            date=date,
            subject=subject,
            body=body,
            files=files,
        ))
    return commits


def group_by_day(commits: list[Commit]) -> dict[str, list[Commit]]:
    by_day: dict[str, list[Commit]] = defaultdict(list)
    for c in commits:
        by_day[c.date].append(c)
    return by_day


def render_commit(c: Commit, max_files: int = 8) -> str:
    lines = [f"- `{c.sha}` **{c.subject}** — _{c.author}_"]
    if c.body:
        for bl in c.body.splitlines():
            if bl.strip():
                lines.append(f"  > {bl.rstrip()}")
    if c.files:
        shown = c.files[:max_files]
        extra = len(c.files) - len(shown)
        file_str = ", ".join(f"`{f}`" for f in shown)
        if extra > 0:
            file_str += f" _(+{extra} more)_"
        lines.append(f"  - files: {file_str}")
    return "\n".join(lines)


def build_seed_section(date: str, commits: list[Commit], repo_name: str) -> str:
    lines = [
        SEED_HEADER,
        "",
        f"_Seeded from `{repo_name}` git history — {len(commits)} commit(s) on {date}._",
        "",
    ]
    for c in commits:
        lines.append(render_commit(c))
        lines.append("")
    lines.append(SEED_END_MARKER)
    return "\n".join(lines).rstrip() + "\n"


def merge_into_daily_log(path: Path, seed_section: str, date: str) -> str:
    """Return the new file contents after inserting/replacing the seed section."""
    if not path.exists():
        return (
            f"# {date}\n\n"
            f"_(This file was seeded from git history. Real conversation logs "
            f"will be appended by normal hooks.)_\n\n"
            f"{seed_section}"
        )

    existing = path.read_text(encoding="utf-8")
    if SEED_HEADER in existing and SEED_END_MARKER in existing:
        # Replace existing seed section in place (idempotent re-run)
        start = existing.find(SEED_HEADER)
        end = existing.find(SEED_END_MARKER) + len(SEED_END_MARKER)
        # Consume trailing newline after the marker if present
        if end < len(existing) and existing[end] == "\n":
            end += 1
        return existing[:start] + seed_section + existing[end:]
    # Append to the end of the existing daily log
    sep = "" if existing.endswith("\n") else "\n"
    return existing + sep + "\n" + seed_section


def seed_project(slug: str, repo_path: Path, dry_run: bool) -> tuple[int, int]:
    """Returns (commit_count, day_count)."""
    rp = resolve(repo_path)
    # Resolution above will use the registry mapping to find the slug.
    # But if the caller passed an explicit slug that differs, trust that.
    if rp.slug != slug:
        # Fall back to a manual ResolvedProject by re-resolving through the
        # registry using the slug's registered path.
        reg = load_registry()
        proj = next((p for p in reg.get("projects", []) if p["slug"] == slug), None)
        if not proj:
            print(f"  slug {slug!r} not in registry", file=sys.stderr)
            return 0, 0
        rp = resolve(os.path.expanduser(proj["path"]))
    rp.ensure_dirs()

    if not (repo_path / ".git").exists():
        print(f"  not a git repo: {repo_path}", file=sys.stderr)
        return 0, 0

    commits = git_log_commits(repo_path)
    if not commits:
        print(f"  {slug}: no commits found")
        return 0, 0

    by_day = group_by_day(commits)
    repo_name = repo_path.name

    for date in sorted(by_day.keys()):
        day_commits = by_day[date]
        seed_section = build_seed_section(date, day_commits, repo_name)
        daily_file = rp.daily_dir / f"{date}.md"
        new_contents = merge_into_daily_log(daily_file, seed_section, date)

        if dry_run:
            print(f"  · would write {daily_file.relative_to(rp.vault_dir)} "
                  f"({len(day_commits)} commit(s))")
            continue

        daily_file.write_text(new_contents, encoding="utf-8")

    if not dry_run:
        print(f"  {slug}: seeded {len(commits)} commit(s) across {len(by_day)} day(s) "
              f"from {repo_name}")
    return len(commits), len(by_day)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed daily logs from git history.")
    parser.add_argument("--slug", help="project slug to seed (one of the registry slugs)")
    parser.add_argument("--repo", help="repo path (overrides registry mapping)")
    parser.add_argument("--all", action="store_true",
                        help="seed every project in the registry")
    parser.add_argument("--dry-run", action="store_true",
                        help="show plan, write nothing")
    args = parser.parse_args()

    if not args.slug and not args.all:
        parser.error("pass --slug <slug> or --all")
    if args.all and (args.slug or args.repo):
        parser.error("--all is mutually exclusive with --slug / --repo")

    reg = load_registry()
    targets: list[tuple[str, Path]] = []

    if args.all:
        for p in reg.get("projects", []):
            path = Path(os.path.expanduser(p["path"])).resolve()
            if not path.exists():
                print(f"skip {p['slug']}: path {path} does not exist")
                continue
            targets.append((p["slug"], path))
    else:
        slug = args.slug
        if args.repo:
            path = Path(os.path.expanduser(args.repo)).resolve()
        else:
            proj = next((p for p in reg.get("projects", []) if p["slug"] == slug), None)
            if not proj:
                parser.error(f"slug {slug!r} not found in registry; pass --repo too")
            path = Path(os.path.expanduser(proj["path"])).resolve()
        if not path.exists():
            parser.error(f"repo path does not exist: {path}")
        targets.append((slug, path))

    print(f"Seeding {len(targets)} project(s) from git history "
          f"{'(dry run)' if args.dry_run else ''}")
    total_commits = 0
    total_days = 0
    for slug, path in targets:
        print(f"→ {slug}  ({path})")
        c, d = seed_project(slug, path, args.dry_run)
        total_commits += c
        total_days += d

    print()
    print(f"Done. {total_commits} commit(s) across {total_days} day(s) seeded.")
    if not args.dry_run:
        print("Next: run `uv run python scripts/daily_flush_all.py` to compile + review.")


if __name__ == "__main__":
    main()
