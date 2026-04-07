"""
Daily flush — walks the registry, runs compile.py for every project that
has uncompiled or stale daily logs, then runs lint.py --structural-only.

Designed to be invoked once a day by launchd (see Phase 7 plist), or manually:

    uv run python scripts/daily_flush_all.py
    uv run python scripts/daily_flush_all.py --slug paintcolorhq    # one project
    uv run python scripts/daily_flush_all.py --dry-run              # show what would run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from router import load_registry, resolve  # noqa: E402

UV_BIN = os.environ.get("UV_BIN", "/Users/philipcameron/.local/bin/uv")


def needs_compile(project_dir: Path, state_dir: Path) -> tuple[bool, str]:
    """Return (needs_compile, reason)."""
    daily_dir = project_dir / "daily_logs"
    if not daily_dir.exists():
        return False, "no daily_logs/ dir"

    logs = sorted(daily_dir.glob("*.md"))
    if not logs:
        return False, "no daily logs"

    state_file = state_dir / "state.json"
    if not state_file.exists():
        return True, f"no state.json yet ({len(logs)} log(s) to compile)"

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "state.json unreadable, recompiling all"

    ingested = state.get("ingested", {})
    pending = []
    for log in logs:
        name = log.name
        cur_hash = sha256(log.read_bytes()).hexdigest()[:16]
        if name not in ingested:
            pending.append(f"{name} (new)")
        elif ingested[name].get("hash") != cur_hash:
            pending.append(f"{name} (changed)")

    if pending:
        return True, ", ".join(pending[:3]) + (f" +{len(pending)-3} more" if len(pending) > 3 else "")
    return False, "all logs already compiled"


def run_compile(slug: str, project_dir: Path, state_dir: Path, dry_run: bool) -> int:
    if dry_run:
        return 0
    cmd = [UV_BIN, "run", "--quiet", "--directory", str(REPO_ROOT), "python", "scripts/compile.py"]
    env = os.environ.copy()
    env["KARPATHY_PROJECT_DIR"] = str(project_dir)
    env["KARPATHY_STATE_DIR"] = str(state_dir)
    env["CLAUDE_INVOKED_BY"] = "daily_flush_all"  # block hook recursion
    log_path = state_dir / "compile.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n=== {datetime.now(timezone.utc).astimezone().isoformat()} compile slug={slug} ===\n")
        result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdout=logf, stderr=subprocess.STDOUT)
    return result.returncode


def run_hermes(slug: str, project_dir: Path, state_dir: Path, dry_run: bool) -> int:
    if dry_run:
        return 0
    cmd = [UV_BIN, "run", "--quiet", "--directory", str(REPO_ROOT),
           "python", "scripts/hermes.py"]
    env = os.environ.copy()
    env["KARPATHY_PROJECT_DIR"] = str(project_dir)
    env["KARPATHY_STATE_DIR"] = str(state_dir)
    env["CLAUDE_INVOKED_BY"] = "daily_flush_all"
    log_path = state_dir / "hermes.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n=== {datetime.now(timezone.utc).astimezone().isoformat()} hermes slug={slug} ===\n")
        result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdout=logf, stderr=subprocess.STDOUT)
    return result.returncode


def run_lint(slug: str, project_dir: Path, state_dir: Path, dry_run: bool) -> int:
    if dry_run:
        return 0
    cmd = [UV_BIN, "run", "--quiet", "--directory", str(REPO_ROOT),
           "python", "scripts/lint.py", "--structural-only"]
    env = os.environ.copy()
    env["KARPATHY_PROJECT_DIR"] = str(project_dir)
    env["KARPATHY_STATE_DIR"] = str(state_dir)
    env["CLAUDE_INVOKED_BY"] = "daily_flush_all"
    log_path = state_dir / "lint.log"
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n=== {datetime.now(timezone.utc).astimezone().isoformat()} lint slug={slug} ===\n")
        result = subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdout=logf, stderr=subprocess.STDOUT)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="run only this project")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-lint", action="store_true")
    parser.add_argument("--skip-hermes", action="store_true",
                        help="skip the Hermes QA gate (default: run after compile)")
    args = parser.parse_args()

    reg = load_registry()
    slugs_to_check = [args.slug] if args.slug else [p["slug"] for p in reg["projects"]] + [reg.get("default_slug", "_scratch")]

    print(f"=== daily_flush_all started at {datetime.now(timezone.utc).astimezone().isoformat()} ===")
    print(f"Vault: {reg['vaults'][reg['default_vault']]}")
    print(f"Projects: {len(slugs_to_check)}")
    print()

    for slug in slugs_to_check:
        # Find the project's path to use for resolving
        if slug == reg.get("default_slug", "_scratch"):
            proj_path = "~"  # _scratch fallback
        else:
            proj = next((p for p in reg["projects"] if p["slug"] == slug), None)
            if not proj:
                continue
            proj_path = proj["path"]

        rp = resolve(proj_path)
        rp.ensure_dirs()

        needs, reason = needs_compile(rp.project_dir, rp.state_dir)
        marker = "→ COMPILE" if needs else "  skip   "
        print(f"  {marker}  {rp.slug:18}  {reason}")

        if needs:
            rc = run_compile(rp.slug, rp.project_dir, rp.state_dir, args.dry_run)
            print(f"             compile rc={rc}")

            # Hermes runs after compile only when compile actually ran
            if not args.skip_hermes:
                rc = run_hermes(rp.slug, rp.project_dir, rp.state_dir, args.dry_run)
                print(f"             hermes  rc={rc}")

        if not args.skip_lint:
            rc = run_lint(rp.slug, rp.project_dir, rp.state_dir, args.dry_run)
            print(f"             lint rc={rc}")

    print()
    print(f"=== daily_flush_all finished at {datetime.now(timezone.utc).astimezone().isoformat()} ===")


if __name__ == "__main__":
    main()
