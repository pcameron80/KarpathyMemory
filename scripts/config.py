"""Path constants and configuration for the personal knowledge base.

Paths are resolved at import time from environment variables set by the
caller (hooks, daily_flush_all.py, or a manual `KARPATHY_PROJECT_DIR=... uv run`).

If the env vars aren't set, falls back to the legacy single-project layout
inside the repo (./daily, ./knowledge) — useful for stand-alone testing only.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

# ── Repo root (where this file lives) ─────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Project + state dirs (env-driven) ─────────────────────────────────
_env_project = os.environ.get("KARPATHY_PROJECT_DIR")
_env_state = os.environ.get("KARPATHY_STATE_DIR")

if _env_project:
    PROJECT_ROOT = Path(_env_project).expanduser()
else:
    # Legacy / standalone fallback
    PROJECT_ROOT = REPO_ROOT

if _env_state:
    STATE_DIR = Path(_env_state).expanduser()
else:
    STATE_DIR = REPO_ROOT / "scripts"

# ── Vault-side paths (synced via Obsidian LiveSync) ───────────────────
DAILY_DIR = PROJECT_ROOT / "daily_logs"
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
INDEX_FILE = PROJECT_ROOT / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
AGENTS_FILE = PROJECT_ROOT / "AGENTS.md"
REPORTS_DIR = PROJECT_ROOT / "reports"

# ── Local-only state (NOT synced) ─────────────────────────────────────
STATE_FILE = STATE_DIR / "state.json"

# ── Misc ──────────────────────────────────────────────────────────────
SCRIPTS_DIR = REPO_ROOT / "scripts"
HOOKS_DIR = REPO_ROOT / "hooks"
TIMEZONE = "America/Chicago"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def ensure_dirs() -> None:
    for d in (DAILY_DIR, CONCEPTS_DIR, CONNECTIONS_DIR, QA_DIR, REPORTS_DIR, STATE_DIR):
        d.mkdir(parents=True, exist_ok=True)
