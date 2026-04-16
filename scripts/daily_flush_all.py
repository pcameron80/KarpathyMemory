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

# Lockfile to prevent concurrent runs. Without this, a manually-started
# run can race against the scheduled launchd daily job and corrupt state
# (concurrent compiles against the same state.json, overlapping writes
# to knowledge/, overlapping Codex sessions burning credits twice).
LOCK_FILE = Path.home() / ".claude" / "karpathy-memory" / "state" / "daily_flush.lock"


def acquire_lock() -> bool:
    """Acquire an exclusive lock. Returns False if another run is active.

    If the lock file exists but its PID is dead (stale lock from a crash),
    we reclaim it. Otherwise we refuse to run.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = None
        if old_pid is not None:
            try:
                os.kill(old_pid, 0)  # signal 0 = "does the process exist?"
                # Process is alive — another run is in progress
                print(
                    f"daily_flush_all: another run is already in progress "
                    f"(pid {old_pid}). Lockfile: {LOCK_FILE}",
                    file=sys.stderr,
                )
                return False
            except ProcessLookupError:
                # Stale lock from a crashed run — take it over
                print(
                    f"daily_flush_all: reclaiming stale lock from dead pid {old_pid}",
                    file=sys.stderr,
                )
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


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


def commit_vault_changes(vault_path: Path, compiled_slugs: list[str], skipped_slugs: list[str], dry_run: bool) -> None:
    """Stage and commit any vault changes produced by the nightly run.

    Non-fatal: logs failures to stdout and returns. Skips if the vault is not
    a git repo or has no changes. Uses the ambient git identity.
    """
    if dry_run:
        return
    if not vault_path.exists():
        print(f"  [commit] vault path does not exist: {vault_path}")
        return

    try:
        git_dir = subprocess.run(
            ["git", "-C", str(vault_path), "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        print(f"  [commit] git unavailable: {exc}")
        return
    if git_dir.returncode != 0:
        print(f"  [commit] vault is not a git repo — skipping commit")
        return

    status = subprocess.run(
        ["git", "-C", str(vault_path), "status", "--porcelain"],
        capture_output=True, text=True, timeout=10,
    )
    if status.returncode != 0 or not status.stdout.strip():
        print(f"  [commit] no vault changes to commit")
        return

    changed_count = len(status.stdout.strip().splitlines())
    add = subprocess.run(
        ["git", "-C", str(vault_path), "add", "-A"],
        capture_output=True, text=True, timeout=30,
    )
    if add.returncode != 0:
        print(f"  [commit] git add failed: {add.stderr.strip()}")
        return

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    subject = f"Nightly compile {now[:10]} — {len(compiled_slugs)} compiled, {changed_count} file(s) changed"
    body_lines = []
    if compiled_slugs:
        body_lines.append("Compiled: " + ", ".join(compiled_slugs))
    if skipped_slugs:
        body_lines.append("Skipped:  " + ", ".join(skipped_slugs))
    body_lines.append(f"Timestamp: {now}")
    message = subject + "\n\n" + "\n".join(body_lines) + "\n"

    commit = subprocess.run(
        ["git", "-C", str(vault_path), "commit", "-m", message],
        capture_output=True, text=True, timeout=30,
    )
    if commit.returncode != 0:
        print(f"  [commit] git commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
        return
    print(f"  [commit] {subject}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="run only this project")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-lint", action="store_true")
    parser.add_argument("--skip-hermes", action="store_true",
                        help="skip the Hermes QA gate (default: run after compile)")
    parser.add_argument("--force", action="store_true",
                        help="ignore the lockfile and run anyway (dangerous)")
    args = parser.parse_args()

    if not args.force and not acquire_lock():
        sys.exit(3)

    try:
        _run(args)
    finally:
        if not args.force:
            release_lock()


def _run(args: argparse.Namespace) -> None:
    reg = load_registry()
    slugs_to_check = [args.slug] if args.slug else [p["slug"] for p in reg["projects"]] + [reg.get("default_slug", "_scratch")]

    print(f"=== daily_flush_all started at {datetime.now(timezone.utc).astimezone().isoformat()} ===")
    vault_path_raw = reg["vaults"][reg["default_vault"]]
    vault_path = Path(vault_path_raw).expanduser().resolve()
    print(f"Vault: {vault_path}")
    print(f"Projects: {len(slugs_to_check)}")
    print()

    compiled_slugs: list[str] = []
    skipped_slugs: list[str] = []

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

            compiled_slugs.append(rp.slug)
        else:
            skipped_slugs.append(rp.slug)

        if not args.skip_lint:
            rc = run_lint(rp.slug, rp.project_dir, rp.state_dir, args.dry_run)
            print(f"             lint rc={rc}")

    print()
    commit_vault_changes(vault_path, compiled_slugs, skipped_slugs, args.dry_run)
    print()
    print(f"=== daily_flush_all finished at {datetime.now(timezone.utc).astimezone().isoformat()} ===")


if __name__ == "__main__":
    main()
