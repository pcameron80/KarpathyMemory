"""
Router — resolves cwd to a project's vault folder + state folder.

The hooks in `hooks/` and the scripts in `scripts/` use this to figure out
which project (and which vault) the current Claude Code session belongs to.

Registry lives at ~/.claude/karpathy-memory/registry.json:

    {
      "vaults": {
        "personal": "~/Brain-Personal"
      },
      "default_vault": "personal",
      "default_slug": "_scratch",
      "projects": [
        {"path": "~/Documents/GitHub/paintcolorhq", "vault": "personal", "slug": "paintcolorhq"},
        ...
      ]
    }

Returns:
    ResolvedProject(vault_dir, project_dir, state_dir, slug)

- vault_dir   = ~/Brain-Personal
- project_dir = ~/Brain-Personal/10-projects/<slug>      (in the synced vault)
- state_dir   = ~/.claude/karpathy-memory/state/<slug>   (NOT synced — local state)
- slug        = the project slug
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

REGISTRY_PATH = Path.home() / ".claude" / "karpathy-memory" / "registry.json"
STATE_ROOT = Path.home() / ".claude" / "karpathy-memory" / "state"


@dataclass
class ResolvedProject:
    vault_dir: Path
    project_dir: Path
    state_dir: Path
    slug: str
    knowledge_dir: Path
    daily_dir: Path
    index_file: Path
    agents_file: Path
    log_file: Path

    def ensure_dirs(self) -> None:
        for d in (
            self.project_dir,
            self.daily_dir,
            self.knowledge_dir,
            self.knowledge_dir / "concepts",
            self.knowledge_dir / "connections",
            self.knowledge_dir / "qa",
            self.state_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {
            "vaults": {"personal": str(Path.home() / "Brain-Personal")},
            "default_vault": "personal",
            "default_slug": "_scratch",
            "projects": [],
        }
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def resolve(cwd: Path | str | None = None) -> ResolvedProject:
    """Resolve a working directory to its project. Falls back to default _scratch."""
    if cwd is None:
        cwd_path = Path.cwd()
    else:
        # Expand ~ before resolving so tilde-prefixed paths from registry/CLI work
        cwd_path = Path(os.path.expanduser(str(cwd)))
    try:
        cwd_path = cwd_path.resolve()
    except (FileNotFoundError, OSError):
        cwd_path = Path.home()

    reg = load_registry()
    projects = reg.get("projects", [])

    # Longest-prefix match wins (so a sub-repo within a parent project picks the sub-repo)
    matches = []
    for p in projects:
        try:
            ppath = _expand(p["path"])
        except Exception:
            continue
        if cwd_path == ppath or ppath in cwd_path.parents:
            matches.append((len(str(ppath)), p))

    matches.sort(key=lambda t: t[0], reverse=True)

    if matches:
        proj = matches[0][1]
        vault_name = proj["vault"]
        slug = proj["slug"]
    else:
        vault_name = reg.get("default_vault", "personal")
        slug = reg.get("default_slug", "_scratch")

    vault_dir = _expand(reg["vaults"][vault_name])
    project_dir = vault_dir / "10-projects" / slug
    state_dir = STATE_ROOT / slug
    knowledge_dir = project_dir / "knowledge"
    daily_dir = project_dir / "daily_logs"
    index_file = project_dir / "index.md"
    agents_file = project_dir / "AGENTS.md"
    log_file = knowledge_dir / "log.md"

    return ResolvedProject(
        vault_dir=vault_dir,
        project_dir=project_dir,
        state_dir=state_dir,
        slug=slug,
        knowledge_dir=knowledge_dir,
        daily_dir=daily_dir,
        index_file=index_file,
        agents_file=agents_file,
        log_file=log_file,
    )


def resolve_from_env_or_arg(arg_value: str | None = None) -> ResolvedProject:
    """Used by hooks: prefer arg → CLAUDE_CWD env → os.getcwd()."""
    cwd = arg_value or os.environ.get("CLAUDE_CWD") or os.getcwd()
    return resolve(cwd)


if __name__ == "__main__":
    import sys
    rp = resolve_from_env_or_arg(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"slug:        {rp.slug}")
    print(f"vault_dir:   {rp.vault_dir}")
    print(f"project_dir: {rp.project_dir}")
    print(f"state_dir:   {rp.state_dir}")
    print(f"daily_dir:   {rp.daily_dir}")
    print(f"index_file:  {rp.index_file}")
