"""
memory_log.py — append a session/action entry to a project's daily log.

Designed for non-Claude-Code agents (Ollama-backed Paperclip agents like Sandy,
Ted, Marshall, Captain) that need to leave a record in the Karpathy memory
without going through the SessionEnd → flush.py → Claude SDK pipeline.

This is the "synchronous, agent-managed" path. The agent writes its own
summary; there's no LLM extraction. Cheaper, simpler, fully under the agent's
control.

Usage:
    uv run python scripts/memory_log.py \\
        --slug paperclip \\
        --agent sandy \\
        --section "Content draft for GPD" \\
        --content "Drafted 3 product roundups for greatpickdeals.com..."

    # Or via stdin:
    echo "summary text..." | uv run python scripts/memory_log.py \\
        --slug paperclip --agent ted --section "Daily standup"

    # From inside the paperclip container:
    KARPATHY_VAULT=/paperclip/shared/brain \\
        uv run python /paperclip/karpathy-memory/scripts/memory_log.py \\
        --slug paperclip --agent ted --section "..." --content "..."
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def append_entry(vault_path: Path, slug: str, agent: str, section: str, content: str) -> Path:
    project_dir = vault_path / "10-projects" / slug
    daily_dir = project_dir / "daily_logs"
    daily_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).astimezone()
    log_path = daily_dir / f"{today.strftime('%Y-%m-%d')}.md"

    if not log_path.exists():
        log_path.write_text(
            f"# Daily Log: {today.strftime('%Y-%m-%d')} — {slug}\n\n## Sessions\n\n## Memory Maintenance\n\n",
            encoding="utf-8",
        )

    time_str = today.strftime("%H:%M")
    entry = f"### {section} ({time_str}) — agent: {agent}\n\n{content.strip()}\n\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)

    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Append an entry to a project's daily log.")
    parser.add_argument("--slug", required=True, help="project slug (e.g. paperclip)")
    parser.add_argument("--agent", required=True, help="agent name (e.g. sandy, ted)")
    parser.add_argument("--section", required=True, help="short section title")
    parser.add_argument("--content", help="entry body. If omitted, reads from stdin.")
    parser.add_argument(
        "--vault",
        default=os.environ.get("KARPATHY_VAULT", str(Path.home() / "Brain-Personal")),
        help="vault path (env: KARPATHY_VAULT, default: ~/Brain-Personal)",
    )
    args = parser.parse_args()

    if args.content is None:
        if sys.stdin.isatty():
            print("Pass --content or pipe text via stdin", file=sys.stderr)
            sys.exit(1)
        content = sys.stdin.read()
    else:
        content = args.content

    if not content.strip():
        print("Empty content, nothing to log", file=sys.stderr)
        sys.exit(1)

    vault = Path(args.vault).expanduser()
    if not vault.exists():
        print(f"Vault does not exist: {vault}", file=sys.stderr)
        sys.exit(1)

    log_path = append_entry(vault, args.slug, args.agent, args.section, content)
    print(f"appended to {log_path}")


if __name__ == "__main__":
    main()
