"""
SessionStart hook — injects per-project knowledge base context into every session.

Resolves cwd → project via the router, then reads:
  1. The project's index.md (the core retrieval mechanism)
  2. The project's AGENTS.md (per-project schema/instructions, if present)
  3. The most recent daily log

The result is injected as `additionalContext` in the SessionStart response,
so Claude always "remembers" what it has learned in this project.

Pure local I/O — no API calls, runs in <1 second.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Recursion guard (in case the agent SDK fires this hook for child sessions)
if os.environ.get("CLAUDE_INVOKED_BY"):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}}))
    sys.exit(0)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from router import resolve  # noqa: E402

STATE_ROOT = Path.home() / ".claude" / "karpathy-memory" / "state"
STATE_ROOT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(STATE_ROOT / "hooks.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [session-start] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_CONTEXT_CHARS = 20_000
MAX_LOG_LINES = 40


def get_recent_log(daily_dir: Path) -> str:
    today = datetime.now(timezone.utc).astimezone()
    for offset in range(3):
        date = today - timedelta(days=offset)
        log_path = daily_dir / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return f"_(from {log_path.name})_\n\n" + "\n".join(recent)
    return "(no recent daily log)"


def build_context(hook_input: dict) -> str:
    cwd = hook_input.get("cwd") or os.environ.get("CLAUDE_CWD") or os.getcwd()
    rp = resolve(cwd)
    rp.ensure_dirs()

    parts = []
    today = datetime.now(timezone.utc).astimezone()
    parts.append(
        f"## Karpathy Memory\n"
        f"**Date:** {today.strftime('%A, %B %d, %Y')}\n"
        f"**Project:** `{rp.slug}` (vault: `{rp.vault_dir.name}`)\n"
        f"**Project root:** `{rp.project_dir}`"
    )

    if rp.agents_file.exists():
        agents = rp.agents_file.read_text(encoding="utf-8")
        parts.append(f"## Project AGENTS.md\n\n{agents}")

    if rp.index_file.exists():
        idx = rp.index_file.read_text(encoding="utf-8")
        parts.append(f"## Knowledge Base Index — {rp.slug}\n\n{idx}")
    else:
        parts.append(f"## Knowledge Base Index — {rp.slug}\n\n(empty — no articles compiled yet)")

    parts.append(f"## Recent Daily Log\n\n{get_recent_log(rp.daily_dir)}")

    context = "\n\n---\n\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n...(truncated)"
    return context


def main() -> None:
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_input = {}

    try:
        context = build_context(hook_input)
        cwd = hook_input.get("cwd") or os.environ.get("CLAUDE_CWD") or os.getcwd()
        from router import resolve as _resolve
        rp = _resolve(cwd)
        logging.info("SessionStart fired slug=%s cwd=%s ctx_len=%d", rp.slug, cwd, len(context))
    except Exception as e:
        logging.error("SessionStart failed: %s", e)
        context = f"## Karpathy Memory\n\n(hook error: {e})"

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    main()
