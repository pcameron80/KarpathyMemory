# ULTRAPLAN — Cross-Machine Claude Code Memory + Obsidian Brain

> Phased build plan for a unified, cross-machine "second brain + Claude Code memory" system.
> Inspired by [Karpathy's LLM knowledge-base tweet](https://x.com/karpathy/status/2039805659525644595)
> and Cole Medin's reference repo: https://github.com/coleam00/claude-memory-compiler.
>
> Reference repo cloned to `/tmp/claude-memory-compiler` for code reuse.

---

## TL;DR

You will end up with:

1. **Two Obsidian vaults** (`Brain-Personal`, `Brain-Ministry`) that live on every Mac you use.
2. **CouchDB on Unraid** acting as the durable backbone, exposed at `sync.toh.fyi` via your existing Cloudflare Tunnel + NPM.
3. **Self-hosted LiveSync plugin** in Obsidian replicating each vault to its own database in real time.
4. **Global Claude Code hooks** that, on every session, load the right project's knowledge into Claude's context, capture the conversation when the session ends, and compile it into the matching vault automatically.
5. **A project registry** that maps repo paths → `(vault, project slug)` so the hooks know where to read/write.
6. **Existing `~/.claude` memory migrated** into the vaults with symlinks back, so nothing breaks.

The Karpathy "compiler" loop (raw daily logs → compiled wiki articles → injected on session start) runs **per project, per vault**.

---

## High-level architecture

```
+-------------------------+         +--------------------------+
|   Mac #1 (this one)     |         |   Mac #2 (work laptop)   |
|                         |         |                          |
|  ~/Brain-Personal/  <---+----+----+--->  ~/Brain-Personal/   |
|  ~/Brain-Ministry/  <---+--+ |    | +-->  ~/Brain-Ministry/  |
|                         |  | |    | |                        |
|  ~/.claude/hooks/  -----+  | |    | |  ~/.claude/hooks/      |
|  registry.json -----------+  |    |  +-- registry.json       |
+-------------------------+    |    |    +--------------------+
                               |    |
                               v    v
                      +-------------------------+
                      |    Unraid NAS           |
                      |    192.168.2.110        |
                      |                         |
                      |  couchdb container      |
                      |   - brain-personal db   |
                      |   - brain-ministry db   |
                      |                         |
                      |  /mnt/user/appdata/     |
                      |    couchdb/             |
                      +-------------------------+
                               ^
                               |
                       sync.toh.fyi
                       (NPM -> CouchDB:5984)
                               ^
                               |
                       Cloudflare Tunnel "unraid"
```

The vaults live **locally** on every Mac (so Claude reads them at native filesystem speed); LiveSync replicates changes through CouchDB whenever you're online.

---

## Vault layout (mirrored for both vaults)

```
Brain-Personal/                 (or Brain-Ministry/)
├── 00-global/                  # cross-project memory: profile, feedback, references
│   ├── MEMORY.md               # index file (Karpathy-style, humans + LLM read this first)
│   ├── user_philip.md
│   ├── feedback_*.md
│   └── reference_*.md
├── 10-projects/
│   └── <project-slug>/
│       ├── AGENTS.md           # per-project schema/instructions for the compiler
│       ├── index.md            # project-scoped knowledge base index (loaded on session start)
│       ├── daily_logs/
│       │   └── 2026-04-07.md   # raw session summaries (immutable, append-only)
│       ├── knowledge/
│       │   ├── concepts/       # atomic compiled articles
│       │   ├── connections/    # cross-cutting insights
│       │   └── qa/             # filed query answers
│       └── reports/            # lint reports (gitignored from vault publishing if any)
├── 20-references/              # infra docs that span projects (Unraid, HA, Paperclip arch)
└── .obsidian/                  # vault config; LiveSync plugin lives here
```

**Why per-project subfolders inside one vault** instead of one vault per project? Obsidian doesn't love many small vaults, LiveSync doesn't either, and you want graph-view connections across projects in the same context (personal vs. ministry). Folders give isolation without fragmentation.

---

## The compiler loop (per project)

This is the Karpathy / Cole Medin loop, scoped to a single project folder:

```
Conversation in Claude Code (cwd = some repo)
    │
    ▼
SessionStart hook
    └─ resolve cwd → (vault, project) via registry
    └─ read 10-projects/<slug>/index.md  + 10-projects/<slug>/AGENTS.md
    └─ read most recent daily log
    └─ inject into Claude's context
    │
    ▼
... user works ...
    │
    ▼
PreCompact hook  (and SessionEnd hook)
    └─ resolve cwd → (vault, project)
    └─ extract last ~30 turns from transcript
    └─ write temp context file
    └─ spawn flush.py as detached background process
            │
            ▼
        flush.py (Claude Agent SDK, max_turns=2, allowed_tools=[])
            └─ reads context file
            └─ extracts decisions, lessons, gotchas, action items
            └─ appends to 10-projects/<slug>/daily_logs/YYYY-MM-DD.md
            └─ if past 18:00 local AND log changed → spawn compile.py
                    │
                    ▼
                compile.py (Claude Agent SDK, max_turns=30, Read/Write/Edit/Glob/Grep)
                    └─ reads AGENTS.md + project index + all existing concept articles
                    └─ promotes daily log entries into:
                        - new/updated knowledge/concepts/*.md
                        - new knowledge/connections/*.md when 2+ concepts link
                    └─ updates index.md
                    └─ appends to log.md
```

LiveSync syncs the resulting markdown files to CouchDB in near-real-time.

---

## Decisions still open (with my recommendation)

| # | Decision | My recommendation | Why |
|---|---|---|---|
| 1 | One global Obsidian vault per Mac vs. each repo gets its own vault | One per "context" (personal + ministry), projects are subfolders | Fewer vaults to sync; cross-project graph view; simpler hook routing |
| 2 | Where Brain-* vaults live on disk | `~/Brain-Personal/` and `~/Brain-Ministry/` | Top-level home, easy to symlink, easy to back up, never inside a repo |
| 3 | How `~/.claude` connects to vaults | Replace `~/.claude/projects/-Users-philipcameron/memory/` with a **symlink** to `Brain-Personal/00-global/` | Existing auto-memory keeps working; deletes/edits flow into the synced vault automatically; only one source of truth |
| 4 | Where the compiler scripts/hooks live | In this repo (`KarpathyMemory`), cloned to `~/.claude/karpathy-memory/` on every Mac, hooks reference that absolute path | Versioned, updateable across machines via `git pull`, doesn't bloat each project's `.claude/` |
| 5 | How hooks know which vault | Project registry JSON: `~/.claude/karpathy-memory/registry.json`, mapping repo path prefix → `{vault, slug}`; default fallback for "scratch" sessions | Explicit, debuggable, easy to extend, no magic auto-detection that fails silently |
| 6 | What to do when cwd doesn't match any project | Write to `Brain-Personal/10-projects/_scratch/daily_logs/` and warn in hook log | Never lose a session, never block the user |
| 7 | Token budget for flush + compile | Use Claude Agent SDK on personal Anthropic subscription (no API key); cap with `max_turns=2` (flush) and `max_turns=30` (compile); compile only runs once per day after 18:00 | Cost predictable, no surprise bills, matches reference repo |
| 8 | Conflict resolution between Macs | Trust LiveSync's vector-clock merge for non-overlapping edits; for overlapping edits, accept that the "later writer wins" with `.conflict-N` files surfaced in Obsidian | Markdown is hand-mergeable; conflicts will be rare since hooks write to `daily_logs/YYYY-MM-DD.md` (date-keyed) and compile only runs on one Mac at a time after 18:00 |
| 9 | When to migrate existing `~/.claude/memory` | Phase 3, after vaults sync end-to-end (NAS → Mac #1 round trip verified) | If sync is broken, migration would orphan memories in CouchDB |
| 10 | Public vs. private repo for KarpathyMemory | Keep private — `registry.json` and any vault-path leaks could reveal directory structure | Already private, leave it |

---

## Phase 0 — NAS: CouchDB container, NPM, tunnel, auth

**Goal:** A CouchDB instance running on Unraid, reachable on the LAN at `http://tower.local:5984` and over the internet at `https://sync.toh.fyi`, with admin credentials and two empty databases ready.

### 0.1 Prep on Unraid

```bash
# SSH to NAS
ssh root@192.168.2.110

# Create persistent dirs
mkdir -p /mnt/user/appdata/couchdb/data
mkdir -p /mnt/user/appdata/couchdb/etc/local.d

# Generate strong admin password — save this in 1Password / your password manager
openssl rand -base64 32
# Also generate a "second brain user" password for clients to use
openssl rand -base64 32
```

Save both passwords now. You'll need them in 0.2, 0.3, and Phase 2.

### 0.2 CouchDB local.ini (tuning for LiveSync)

LiveSync needs CORS, larger HTTP request size, and HTTP timeouts bumped. Write this to `/mnt/user/appdata/couchdb/etc/local.d/live-sync.ini`:

```ini
[couchdb]
single_node=true
max_document_size = 50000000

[chttpd]
require_valid_user = true
max_http_request_size = 4294967296
enable_cors = true

[chttpd_auth]
require_valid_user = true
authentication_redirect = /_utils/session.html

[httpd]
WWW-Authenticate = Basic realm="couchdb"
enable_cors = true

[cors]
origins = app://obsidian.md,capacitor://localhost,http://localhost
credentials = true
headers = accept, authorization, content-type, origin, referer
methods = GET, PUT, POST, HEAD, DELETE
max_age = 3600
```

### 0.3 Container

Add via Unraid Docker UI **or** by creating `/boot/config/plugins/dockerMan/templates-user/my-couchdb.xml` and starting from CLI. Easiest path is the UI:

- **Repository:** `couchdb:3.4` (pin major+minor — do NOT use `latest`; LiveSync compatibility is important)
- **Network:** `bridge`
- **Port:** host `5984` → container `5984`
- **Volume 1:** host `/mnt/user/appdata/couchdb/data` → container `/opt/couchdb/data`
- **Volume 2:** host `/mnt/user/appdata/couchdb/etc/local.d` → container `/opt/couchdb/etc/local.d`
- **Env COUCHDB_USER:** `admin`
- **Env COUCHDB_PASSWORD:** *(the admin password from 0.1)*
- **Watchtower label:** `com.centurylinklabs.watchtower.enable=false` (do NOT auto-update CouchDB; LiveSync compatibility breaks on majors)
- **Restart policy:** `unless-stopped`

Start the container.

### 0.4 Smoke test on the NAS LAN

```bash
# From your Mac, on LAN
curl http://tower.local:5984/
# Expect: {"couchdb":"Welcome", "version":"3.4.x", ...}

# Auth check
curl -u admin:$ADMIN_PW http://tower.local:5984/_all_dbs
# Expect: ["_replicator","_users"]
```

### 0.5 Create the two databases + a non-admin sync user

```bash
# Create the two databases (admin only)
curl -u admin:$ADMIN_PW -X PUT http://tower.local:5984/brain-personal
curl -u admin:$ADMIN_PW -X PUT http://tower.local:5984/brain-ministry

# Create a non-admin user that LiveSync will use day-to-day
curl -u admin:$ADMIN_PW -X PUT http://tower.local:5984/_users/org.couchdb.user:brain \
  -H "Content-Type: application/json" \
  -d '{"name":"brain","password":"'"$BRAIN_PW"'","roles":[],"type":"user"}'

# Grant the brain user access to both DBs
for db in brain-personal brain-ministry; do
  curl -u admin:$ADMIN_PW -X PUT http://tower.local:5984/$db/_security \
    -H "Content-Type: application/json" \
    -d '{"admins":{"names":[],"roles":[]},"members":{"names":["brain"],"roles":[]}}'
done
```

### 0.6 NPM proxy host

In NPM (`npm.toh.fyi`), add a new Proxy Host:

- **Domain:** `sync.toh.fyi`
- **Forward Hostname/IP:** `tower.local` (or `192.168.2.110`)
- **Forward Port:** `5984`
- **Block common exploits:** ON
- **Websockets support:** ON  ← required for LiveSync continuous replication
- **SSL tab:** wildcard `*.toh.fyi` cert (already on file)
- **Force SSL:** ON
- **HTTP/2 Support:** ON
- **HSTS Enabled:** ON
- **Advanced tab → Custom Nginx Configuration:**
  ```
  client_max_body_size 4G;
  proxy_request_buffering off;
  proxy_read_timeout 600s;
  proxy_send_timeout 600s;
  ```

### 0.7 Cloudflare Tunnel route

If `*.toh.fyi` is already a wildcard CNAME pointing at the tunnel, you may already be done — try the smoke test in 0.8 first.

If not: in Cloudflare Zero Trust → Networks → Tunnels → `unraid` → Public Hostnames, add:

- **Subdomain:** `sync`
- **Domain:** `toh.fyi`
- **Service:** `https://npm.toh.fyi` (or whatever your NPM upstream points to)

### 0.8 End-to-end smoke test

```bash
# From any device, including off-LAN (e.g., phone hotspot)
curl https://sync.toh.fyi/
# Expect: {"couchdb":"Welcome", ...}

curl -u brain:$BRAIN_PW https://sync.toh.fyi/brain-personal
# Expect: {"db_name":"brain-personal", ...}
```

### 0.9 Verify

- [ ] `https://sync.toh.fyi/` returns CouchDB welcome JSON
- [ ] `brain` user can read both databases over HTTPS
- [ ] `admin` user is **NOT** used by clients (only for setup/maintenance)
- [ ] CouchDB container is excluded from Watchtower

### 0.10 Rollback

```bash
# Stop and remove the container (data preserved on disk)
docker stop couchdb && docker rm couchdb
# Remove NPM proxy host via UI
# Remove Cloudflare hostname via UI
# Data still in /mnt/user/appdata/couchdb/ — delete only if you want to fully revert:
rm -rf /mnt/user/appdata/couchdb
```

---

## Phase 1 — Vault scaffolding on this Mac

**Goal:** Two empty, structured Obsidian vaults on disk, ready for Obsidian and LiveSync to attach to.

### 1.1 Create directories

```bash
mkdir -p ~/Brain-Personal/{00-global,10-projects,20-references}
mkdir -p ~/Brain-Ministry/{00-global,10-projects,20-references}

# Obsidian vault marker (empty .obsidian dir is enough; Obsidian fills it on first open)
mkdir -p ~/Brain-Personal/.obsidian
mkdir -p ~/Brain-Ministry/.obsidian
```

### 1.2 Seed both vaults with a top-level README and an empty MEMORY index

For **each** vault, write `00-global/MEMORY.md`:

```markdown
# Memory Index

This file is the master catalog for the {{vault name}} brain.
Claude Code's SessionStart hook reads this on every session, plus the project-scoped
index in 10-projects/<project>/index.md when working inside a known project.

## Global notes
(empty — populated during Phase 3 migration)

## Projects
(empty — populated during Phase 5 rollout)
```

And a top-level `README.md` so the vault is self-explanatory in Obsidian.

### 1.3 Verify

```bash
tree -L 2 ~/Brain-Personal ~/Brain-Ministry
```

Expect both to have `00-global/`, `10-projects/`, `20-references/`, `.obsidian/`, `README.md`.

### 1.4 Rollback

```bash
rm -rf ~/Brain-Personal ~/Brain-Ministry
```

---

## Phase 2 — Obsidian + LiveSync first sync

**Goal:** Both vaults open in Obsidian, both replicating to CouchDB, both reachable from this Mac off-LAN.

### 2.1 Install Obsidian (skip if already installed)

```bash
brew install --cask obsidian
```

### 2.2 Open `Brain-Personal` as a vault

- Obsidian → "Open folder as vault" → `~/Brain-Personal`
- Settings → Community plugins → Turn on community plugins
- Browse → search **"Self-hosted LiveSync"** by vrtmrz → Install → Enable

### 2.3 Configure LiveSync

In LiveSync's setup wizard, choose **"Setup wizard"** → **"I have CouchDB instance"**:

- **URI:** `https://sync.toh.fyi`
- **Username:** `brain`
- **Password:** *(brain user password from 0.1)*
- **Database name:** `brain-personal`
- **End-to-End encryption:** **ON** ← important; use a strong passphrase, save it in 1Password
- **Use the same passphrase for path obfuscation:** ON
- **Sync mode:** **LiveSync (real-time)**
- **Periodic sync:** OFF (LiveSync is real-time; periodic adds CPU)
- **Use index.html backup:** OFF
- **Batch size:** default

Click "Test database connection" → "Check database configuration" → fix anything red → "Apply".

### 2.4 Repeat for Brain-Ministry

Same wizard, but database name `brain-ministry` and a **different** E2E passphrase (so a leak of one doesn't compromise both).

### 2.5 Round-trip sync test

In `Brain-Personal/00-global/MEMORY.md`, add a line `## Sync test 2026-04-07`. Wait 5 seconds. Then:

```bash
curl -u brain:$BRAIN_PW https://sync.toh.fyi/brain-personal/_all_docs?limit=20 | head -40
```

You should see documents whose IDs are obfuscated (because of E2E). Good.

Then close the vault, delete the local file:

```bash
rm ~/Brain-Personal/00-global/MEMORY.md
```

Reopen the vault — LiveSync should pull `MEMORY.md` back from CouchDB within seconds. **This is the canary.** If this works, you have full bi-directional sync.

### 2.6 Verify

- [ ] LiveSync status bar shows "🟢" (synced)
- [ ] CouchDB shows obfuscated docs in both databases
- [ ] Deleting a file locally and reopening the vault restores it
- [ ] E2E passphrases for both vaults are saved in 1Password

### 2.7 Rollback

- LiveSync → Settings → "Discard local database and reset" — clears local replica only
- To wipe server side: `curl -u admin:$ADMIN_PW -X DELETE https://sync.toh.fyi/brain-personal && curl -u admin:$ADMIN_PW -X PUT https://sync.toh.fyi/brain-personal`

---

## Phase 3 — Migrate existing `~/.claude` memory into the vaults

**Goal:** Everything currently at `~/.claude/projects/-Users-philipcameron/memory/` lives in the right vault, and `~/.claude` still resolves to it via symlink so existing auto-memory behavior keeps working.

### 3.1 Inventory current memory

```bash
ls -la ~/.claude/projects/-Users-philipcameron/memory/
```

Categorize each file (the contents from `MEMORY.md` already give the breakdown). Recommended split:

| File | Personal | Ministry | Notes |
|---|---|---|---|
| `MEMORY.md` | ✅ master | ✅ master | Each vault gets its own; split entries by relevance |
| `user_philip.md` | ✅ | ✅ | Same identity in both; keep two copies in sync manually for now |
| `feedback_self_hosted_apps.md` | ✅ | ✅ | Applies to both contexts |
| `reference_unraid_server.md` | ✅ | ✅ | Same NAS serves both |
| `project_home_assistant.md` | ✅ | ❌ | Personal infra |
| `project_book_im_getting_up.md` | ❌ | ✅ | Ministry/personal-faith — your call; recommend Ministry |
| `project_openclaw_stack.md` | ❌ | ✅ | OpenClaw is the ministry prototype per current notes |
| `project_orphans_hands_agents.md` | ❌ | ✅ | Ministry |
| `project_paintcolorhq.md` | ✅ | ❌ | Personal |
| `project_paperclip.md` | ✅ | ✅ | Two teams — split into `project_paperclip_personal.md` and `project_paperclip_ministry.md` during migration |
| `project_paperclip_gpd_content_system.md` | ✅ | ❌ | GreatPickDeals = personal |
| `project_captain_codex_switch.md` | ✅ | ❌ | Captain is personal/finance |

### 3.2 Backup first

```bash
cp -R ~/.claude/projects/-Users-philipcameron/memory ~/claude-memory-backup-$(date +%Y%m%d)
```

### 3.3 Copy files to the right vaults

```bash
# Personal
cp ~/.claude/projects/-Users-philipcameron/memory/user_philip.md \
   ~/Brain-Personal/00-global/
cp ~/.claude/projects/-Users-philipcameron/memory/feedback_*.md \
   ~/Brain-Personal/00-global/
cp ~/.claude/projects/-Users-philipcameron/memory/reference_unraid_server.md \
   ~/Brain-Personal/20-references/
# ...etc per the table above

# Ministry — same pattern
```

For project files, drop them into `10-projects/<slug>/` rather than `00-global/` so they live next to the daily_logs/knowledge that hooks will create.

### 3.4 Build a fresh `MEMORY.md` in each vault

Each vault gets its own master index linking to the files now in `00-global/` and `20-references/`. Mirror the existing `MEMORY.md` style: one line per entry, under 150 chars.

### 3.5 Sync test

Wait for LiveSync to push everything (status bar). Then on **a second machine if available** confirm files appear (or just verify in CouchDB Fauxton at `https://sync.toh.fyi/_utils`).

### 3.6 Cut over `~/.claude` via symlink

**Only after** Phase 2 + 3.5 verifications pass:

```bash
# Move the original out of the way (don't delete — keep as second backup)
mv ~/.claude/projects/-Users-philipcameron/memory \
   ~/.claude/projects/-Users-philipcameron/memory.preMigration

# Symlink to the personal vault's global folder
# (Personal is the default — anything that needs to be in both vaults gets duplicated by hook in Phase 4)
ln -s ~/Brain-Personal/00-global \
   ~/.claude/projects/-Users-philipcameron/memory
```

Open a new Claude Code session and verify the auto-memory load still works.

### 3.7 Verify

- [ ] All memory files appear in both vaults at the right locations
- [ ] CouchDB has them (visible in Fauxton `_all_docs`)
- [ ] `~/.claude/projects/-Users-philipcameron/memory` resolves through the symlink
- [ ] A new Claude Code session in `~/` still sees the global memory
- [ ] `memory.preMigration/` backup is intact

### 3.8 Rollback

```bash
rm ~/.claude/projects/-Users-philipcameron/memory  # remove symlink
mv ~/.claude/projects/-Users-philipcameron/memory.preMigration \
   ~/.claude/projects/-Users-philipcameron/memory
```

---

## Phase 4 — Build the hooks + project registry + global settings.json

**Goal:** Working `SessionStart`, `PreCompact`, `SessionEnd` hooks installed globally in `~/.claude/settings.json`, all routing through the Karpathy compiler scripts in this repo, with a registry that tells them which vault/project to use.

This is the heaviest phase. We'll fork Cole Medin's reference repo into this one, then add the multi-vault routing layer he doesn't have.

### 4.1 Clone the compiler scripts into KarpathyMemory

```bash
cd ~/Documents/GitHub/KarpathyMemory

# Pull in the working code from the reference repo
cp -R /tmp/claude-memory-compiler/hooks ./hooks
cp -R /tmp/claude-memory-compiler/scripts ./scripts
cp /tmp/claude-memory-compiler/pyproject.toml ./pyproject.toml
cp /tmp/claude-memory-compiler/uv.lock ./uv.lock
cp /tmp/claude-memory-compiler/AGENTS.md ./AGENTS.md

# Install deps with uv
brew install uv  # if not already installed
uv sync
```

### 4.2 Add the routing layer (`scripts/router.py`)

Create a new module that replaces the reference repo's hardcoded `ROOT = Path(__file__).resolve().parent.parent` with cwd-based vault/project resolution.

```python
# scripts/router.py
"""Resolve cwd → (vault_path, project_slug) using ~/.claude/karpathy-memory/registry.json."""
from __future__ import annotations
import json, os
from pathlib import Path

REGISTRY_PATH = Path.home() / ".claude" / "karpathy-memory" / "registry.json"

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"vaults": {}, "projects": [], "default": None}
    return json.loads(REGISTRY_PATH.read_text())

def resolve(cwd: Path) -> tuple[Path, str]:
    """
    Returns (project_dir, slug) where project_dir is the absolute path to
    Brain-{Vault}/10-projects/{slug}/. Falls back to scratch if no match.
    """
    reg = load_registry()
    cwd = cwd.resolve()
    # Longest-prefix match wins
    matches = sorted(
        (p for p in reg["projects"] if cwd == Path(p["path"]).resolve() or
                                       Path(p["path"]).resolve() in cwd.parents),
        key=lambda p: len(p["path"]), reverse=True,
    )
    if matches:
        m = matches[0]
        vault_path = Path(reg["vaults"][m["vault"]]).expanduser()
        return vault_path / "10-projects" / m["slug"], m["slug"]
    # Fallback
    default_vault = reg.get("default", "personal")
    vault_path = Path(reg["vaults"][default_vault]).expanduser()
    return vault_path / "10-projects" / "_scratch", "_scratch"

def ensure_project_dirs(project_dir: Path) -> None:
    for sub in ("daily_logs", "knowledge/concepts", "knowledge/connections", "knowledge/qa", "reports"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
```

### 4.3 Patch the hooks to use the router

Edit `hooks/session-start.py`, `hooks/session-end.py`, `hooks/pre-compact.py`. Replace the `ROOT = Path(__file__).resolve().parent.parent` block with:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from router import resolve, ensure_project_dirs

CWD = Path(os.environ.get("CLAUDE_CWD") or os.getcwd())
PROJECT_DIR, SLUG = resolve(CWD)
ensure_project_dirs(PROJECT_DIR)

KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"
DAILY_DIR = PROJECT_DIR / "daily_logs"
INDEX_FILE = KNOWLEDGE_DIR / "index.md" if KNOWLEDGE_DIR.exists() else PROJECT_DIR / "index.md"
```

(You'll also need to do the same patch in `scripts/flush.py` and `scripts/compile.py` so they write to the right project folder. The hook already passes `cwd` via stdin in Claude Code's JSON payload — use that instead of `os.getcwd()` once you confirm the field name.)

### 4.4 Create the registry

```bash
mkdir -p ~/.claude/karpathy-memory
cat > ~/.claude/karpathy-memory/registry.json <<'JSON'
{
  "vaults": {
    "personal": "~/Brain-Personal",
    "ministry": "~/Brain-Ministry"
  },
  "default": "personal",
  "projects": [
    {"path": "~/Documents/GitHub/paintcolorhq",          "vault": "personal", "slug": "paintcolorhq"},
    {"path": "~/dumpster-directory",                      "vault": "personal", "slug": "town-bins"},
    {"path": "~/Documents/GitHub/GreatPickDeals",         "vault": "personal", "slug": "greatpickdeals"},
    {"path": "~/Documents/GitHub/Bloodhound",             "vault": "personal", "slug": "bloodhound"},
    {"path": "~/Documents/GitHub/paperclip-personal",     "vault": "personal", "slug": "paperclip-personal"},

    {"path": "~/Documents/GitHub/receipts",               "vault": "ministry", "slug": "receipts"},
    {"path": "~/Documents/GitHub/landing-pages",          "vault": "ministry", "slug": "landing-pages"},
    {"path": "~/Documents/GitHub/paperclip-ministry",     "vault": "ministry", "slug": "paperclip-ministry"},
    {"path": "~/Documents/GitHub/orphans-hands",          "vault": "ministry", "slug": "orphans-hands"},

    {"path": "~/Documents/GitHub/KarpathyMemory",         "vault": "personal", "slug": "karpathy-memory"}
  ]
}
JSON
```

> ⚠️ **Verify the actual paths** before saving — some of these are placeholders. We'll do that in the morning together; only `paintcolorhq` and `dumpster-directory` are confirmed from your existing memory file.

Symlink the cloned repo into a stable location so hooks have a fixed path:

```bash
ln -s ~/Documents/GitHub/KarpathyMemory ~/.claude/karpathy-memory/repo
```

### 4.5 Wire up `~/.claude/settings.json` (global hooks)

Hooks at the **user-global** level apply to every Claude Code session, regardless of cwd. Backup first:

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.preMemory
```

Then merge in:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory ~/.claude/karpathy-memory/repo python hooks/session-start.py",
            "timeout": 15
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory ~/.claude/karpathy-memory/repo python hooks/pre-compact.py",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --directory ~/.claude/karpathy-memory/repo python hooks/session-end.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

If your existing `settings.json` already has a `"hooks"` block, **merge by key** rather than overwriting. The Vercel plugin and other plugins also register hooks — don't drop them.

### 4.6 Smoke test

Open a new Claude Code session in **this** repo:

```bash
cd ~/Documents/GitHub/KarpathyMemory
claude
```

Inside Claude:
- Ask: "What's in your context right now?" — you should see the SessionStart hook injection mentioning the karpathy-memory project's index.
- Have a tiny conversation (3-4 exchanges).
- `/exit`
- Wait ~30 seconds.
- Check `~/Brain-Personal/10-projects/karpathy-memory/daily_logs/2026-04-07.md` — the SessionEnd hook should have appended a summary.
- Check `~/.claude/karpathy-memory/repo/scripts/flush.log` for the spawn log.

### 4.7 Verify

- [ ] SessionStart hook injects context (Claude can describe today's date + project name)
- [ ] SessionEnd spawns `flush.py` (visible in `flush.log`)
- [ ] `daily_logs/2026-04-07.md` exists in the right vault for the right project
- [ ] No regression: existing `~/.claude` memory still loads
- [ ] No infinite recursion (the `CLAUDE_INVOKED_BY` guard works — flush.py running shouldn't fire its own hooks)

### 4.8 Rollback

```bash
mv ~/.claude/settings.json.preMemory ~/.claude/settings.json
```

The vaults stay; just no auto-capture.

---

## Phase 5 — Per-project rollout

**Goal:** Every project in the registry has a seeded `10-projects/<slug>/` directory with `AGENTS.md`, `index.md`, empty `daily_logs/`, and empty `knowledge/` subfolders so the first session can start writing immediately.

### 5.1 Bootstrap script

Add `scripts/bootstrap_project.py` to the repo:

```python
"""Seed a project folder in the right vault."""
import sys, json
from pathlib import Path
from datetime import date

REGISTRY = json.loads((Path.home() / ".claude/karpathy-memory/registry.json").read_text())

def seed(slug: str):
    proj = next(p for p in REGISTRY["projects"] if p["slug"] == slug)
    vault = Path(REGISTRY["vaults"][proj["vault"]]).expanduser()
    project_dir = vault / "10-projects" / slug
    for sub in ("daily_logs", "knowledge/concepts", "knowledge/connections", "knowledge/qa", "reports"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    # AGENTS.md (per-project compiler instructions)
    agents_md = project_dir / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text(f"""# AGENTS.md — {slug}

This project follows the Karpathy memory schema. See the global AGENTS.md
in ~/.claude/karpathy-memory/repo/AGENTS.md for the full spec.

## Project-specific notes
(add anything Claude should always know about this project)
""")
    # index.md (loaded by SessionStart hook)
    idx = project_dir / "index.md"
    if not idx.exists():
        idx.write_text(f"""# {slug} — Knowledge Base Index

| Article | Summary | Compiled From | Updated |
|---------|---------|---------------|---------|
""")
    # log.md (build log)
    log = project_dir / "knowledge" / "log.md"
    if not log.exists():
        log.write_text(f"# Build Log — {slug}\n\nSeeded {date.today().isoformat()}\n")
    print(f"Seeded {project_dir}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        seed(sys.argv[1])
    else:
        for p in REGISTRY["projects"]:
            seed(p["slug"])
```

### 5.2 Run it for everything

```bash
cd ~/Documents/GitHub/KarpathyMemory
uv run python scripts/bootstrap_project.py
```

### 5.3 Pre-seed `_scratch` projects in both vaults

```bash
mkdir -p ~/Brain-Personal/10-projects/_scratch/{daily_logs,knowledge/concepts,knowledge/connections,knowledge/qa}
mkdir -p ~/Brain-Ministry/10-projects/_scratch/{daily_logs,knowledge/concepts,knowledge/connections,knowledge/qa}
```

### 5.4 Verify

- [ ] Every slug from the registry has a folder under `10-projects/`
- [ ] Each has `AGENTS.md`, `index.md`, `knowledge/log.md`, empty `daily_logs/`
- [ ] LiveSync pushes them all to CouchDB

### 5.5 Rollback

```bash
# Per project
rm -rf ~/Brain-Personal/10-projects/<slug>
# Or all
rm -rf ~/Brain-Personal/10-projects/* ~/Brain-Ministry/10-projects/*
```

---

## Phase 6 — Onboarding the second Mac

**Goal:** A second Mac sees the same brains and the same hooks within an hour.

### 6.1 Install prerequisites

```bash
brew install --cask obsidian
brew install uv
```

### 6.2 Pull the repo

```bash
mkdir -p ~/Documents/GitHub
cd ~/Documents/GitHub
gh auth login              # log in as pcameron80
gh repo clone pcameron80/KarpathyMemory
cd KarpathyMemory
uv sync
```

### 6.3 Set up vaults via LiveSync

- Create `~/Brain-Personal` and `~/Brain-Ministry` empty
- Open each in Obsidian
- Install Self-hosted LiveSync plugin
- Use the **exact same** URI / username / password / E2E passphrase as the first Mac
- Choose **"Sync from server"** when asked — LiveSync will pull the entire history from CouchDB
- Wait until status bar shows ✅

### 6.4 Wire hooks

```bash
mkdir -p ~/.claude/karpathy-memory
ln -s ~/Documents/GitHub/KarpathyMemory ~/.claude/karpathy-memory/repo

# Copy registry from Mac #1 (or commit it to the repo and pull — see 6.6)
scp mac1.local:~/.claude/karpathy-memory/registry.json \
    ~/.claude/karpathy-memory/registry.json
```

Then merge the hook entries from Phase 4.5 into `~/.claude/settings.json` on this Mac.

### 6.5 Cut over `~/.claude` memory

Same as Phase 3.6, only this Mac:

```bash
mv ~/.claude/projects/-Users-philipcameron/memory \
   ~/.claude/projects/-Users-philipcameron/memory.preMigration  # if it exists
ln -s ~/Brain-Personal/00-global \
   ~/.claude/projects/-Users-philipcameron/memory
```

### 6.6 (Optional) Commit the registry to the repo

To avoid `scp`-ing it across machines forever, commit `registry.json` to KarpathyMemory and have each Mac symlink:

```bash
ln -sf ~/Documents/GitHub/KarpathyMemory/registry.json \
       ~/.claude/karpathy-memory/registry.json
```

Caveat: if a project path differs across Macs (e.g., one Mac has it under `~/code/` instead of `~/Documents/GitHub/`), the registry needs per-Mac overrides. Add a `local-overrides.json` next to it.

### 6.7 Verify

- [ ] `~/Brain-Personal/00-global/MEMORY.md` matches Mac #1 byte-for-byte (after sync settles)
- [ ] A test session in any project on Mac #2 produces a daily_log entry that Mac #1 sees within seconds
- [ ] `~/.claude` symlink works

### 6.8 Rollback

Reverse the symlink and remove hooks from `settings.json` (Phase 4.8). Vault data on disk is harmless.

---

## Phase 7 — Daily flush job + health checks

**Goal:** The wiki stays compiled and clean even on days you don't manually trigger anything.

### 7.1 launchd plist for the daily flush

The reference repo's `flush.py` already auto-triggers `compile.py` after 18:00 if today's log changed — so technically this is belt-and-suspenders. But on days you don't open Claude Code at all, nothing fires. A nightly launchd job covers that.

`~/Library/LaunchAgents/com.karpathy.memory.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.karpathy.memory.daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/philipcameron/.local/bin/uv</string>
    <string>run</string>
    <string>--directory</string>
    <string>/Users/philipcameron/Documents/GitHub/KarpathyMemory</string>
    <string>python</string>
    <string>scripts/daily_flush_all.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>23</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/Users/philipcameron/Library/Logs/karpathy-memory.log</string>
  <key>StandardErrorPath</key><string>/Users/philipcameron/Library/Logs/karpathy-memory.err</string>
</dict>
</plist>
```

Where `scripts/daily_flush_all.py` walks the registry and runs `compile.py --project <slug>` for each project that has uncompiled daily logs, then runs `lint.py --structural-only` per project.

```bash
launchctl load ~/Library/LaunchAgents/com.karpathy.memory.daily.plist
launchctl start com.karpathy.memory.daily
```

### 7.2 Health check report

Every Sunday at 23:30, run a full lint (structural + LLM contradiction check) and write the report to `~/Brain-Personal/20-references/lint-reports/YYYY-MM-DD.md`. Same launchd pattern, weekly schedule.

### 7.3 Verify

- [ ] `launchctl list | grep karpathy` shows the agent loaded
- [ ] After it runs, `~/Library/Logs/karpathy-memory.log` has fresh output
- [ ] Lint report appears in the references folder

### 7.4 Rollback

```bash
launchctl unload ~/Library/LaunchAgents/com.karpathy.memory.daily.plist
rm ~/Library/LaunchAgents/com.karpathy.memory.daily.plist
```

---

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| **CouchDB exposed to the public internet via tunnel** | E2E encryption ON in LiveSync (server only sees encrypted blobs); `require_valid_user = true`; non-admin `brain` user with no Cloudflare-side allow list (consider adding Cloudflare Access policy `email == philip@…` for an extra layer); admin password never used by clients |
| **Token cost from Claude Agent SDK on every session end** | `flush.py` uses `max_turns=2`, `allowed_tools=[]` → ~$0.02-0.05 per session per Cole's numbers. Compile is daily, ~$0.50 per project per day, only after 18:00, only if log changed. Cap by Anthropic subscription, no API key, no surprise bills |
| **Two Macs editing the same `MEMORY.md` simultaneously** | LiveSync's CouchDB merge handles non-overlapping edits via vector clocks. Overlapping edits create `.conflict-N` files visible in Obsidian — you resolve manually. Risk is low because hooks write to date-keyed daily logs (not a shared file) and `compile.py` only runs once a day |
| **Hook fires recursively when flush.py spawns Claude Agent SDK** | `CLAUDE_INVOKED_BY` env var guard at the top of every hook (already in reference repo) |
| **`registry.json` drifts when you add a new project** | Add a script `scripts/register_project.py <vault> <slug> <path>` that appends and validates. Include it in your "new project setup" checklist. Long term, build a `claude /register-project` slash command |
| **CouchDB version upgrade breaks LiveSync** | Watchtower disabled on this container; upgrade manually after checking LiveSync changelog |
| **Existing `~/.claude` memory breaks during migration** | Keep `memory.preMigration` backup until you've used the new system for a week |
| **One vault's contents accidentally synced to the wrong vault's database** | Different E2E passphrases per vault; LiveSync prompts for the passphrase on first connect; wrong passphrase = no sync |
| **Loss of internet → can't sync** | LiveSync queues changes locally and pushes when reconnected. Vault works fully offline |
| **NAS dies entirely** | CouchDB data is on `/mnt/user/appdata/couchdb/` — included in your Unraid backup strategy. Vault clones on each Mac are full copies — any one of them can re-seed CouchDB by being "first" in a fresh setup |
| **LiveSync schema migration on plugin update** | Pin the plugin version in Obsidian → community plugins → click the gear → "do not auto-update" |
| **`~/.claude` symlink → vault path means losing the brain if vault folder is renamed** | Rename via Obsidian (it updates `.obsidian/workspace.json`); never `mv` from the shell |

---

## What I need from you in the morning before we start

1. **Confirm vault names + paths.** I assumed `~/Brain-Personal` and `~/Brain-Ministry`. Want different naming (e.g., `~/Vaults/Personal/`)?
2. **Confirm the project list and verify the actual repo paths.** I marked the registry entries that are placeholders — we need to walk through `~/Documents/GitHub/` and `~/dumpster-directory` etc. and resolve real paths.
3. **CouchDB admin password generation** — I'll prompt you for this when we hit Phase 0.1; it goes in 1Password.
4. **The "ministry vs personal" split for the existing memory files** — review the table in Phase 3.1 and override anything I got wrong (especially `project_book_im_getting_up.md` and the OpenClaw/Orphans-Hands assignment).
5. **Decision on `local-overrides.json` for the registry** — only matters if you plan to use the registry on more than one machine where paths differ.

---

## Estimated session count

- **Phase 0** (NAS) — one focused session, ~30-45 min
- **Phase 1 + 2** (vaults + LiveSync) — one session, ~20 min
- **Phase 3** (migration) — one session with you walking through the file split, ~30 min
- **Phase 4** (hooks) — the deep one, ~60-90 min, lots of testing
- **Phase 5** (rollout) — ~15 min
- **Phase 6** (second Mac) — when you're physically at the other Mac, ~30 min
- **Phase 7** (daily jobs) — ~15 min

Total: a long morning + a follow-up at the second Mac.

---

## Open questions for you

1. Do you want compile to run **per project** (one daily_log per project per day → one compile per project per day) or **once globally** (one batch at 18:00 across every project)? I planned per-project; reference repo is single-project.
2. Do you want a slash command (`/recall <topic>`) registered globally that runs `query.py` against the matching project's KB? Easy to add in Phase 4.
3. Do you want the `KarpathyMemory` repo itself to be registered as a project (so this build gets its own memory)? I included it in the example registry — confirm.
4. **Should `~/Brain-Personal/00-global/` and `~/Brain-Ministry/00-global/` share their `MEMORY.md` and `user_philip.md`** via a third "shared" location, or just maintain two copies? Two copies is simpler but drifts. Sharing via a `~/Brain-Shared/` vault is cleaner but adds a third CouchDB database.

---

End of plan. Wake me up and we'll start with Phase 0.
