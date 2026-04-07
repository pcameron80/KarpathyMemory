# Paperclip integration

Phase 8 of the Karpathy memory system — gives Paperclip's agent stack
(running in `paperclip-src-paperclip-1` on the Unraid NAS) read+write
access to the same shared brain that Philip uses on his Mac.

## What this folder contains

- **`SKILL.md`** — the karpathy-memory skill, written for Paperclip's
  agents. Tells them how to read the project knowledge bases before
  starting work and how to write back to the daily logs after meaningful
  work. Used by both Claude Code agents (auto-discovered) and OpenCode
  agents (referenced from their system prompt).

## Architecture (one-paragraph version)

`livesync-bridge` runs as a Docker container on the NAS, mirroring the
`brain-personal` CouchDB database to a real filesystem at
`/mnt/user/appdata/paperclip/shared/brain/`. Paperclip's container
already mounts that path (it shows up inside as `/paperclip/shared/brain/`),
so agents can `cat`, `grep`, and write to vault files using normal Unix
ops. The bridge picks up file writes and pushes them back through CouchDB
to every Mac via Self-hosted LiveSync.

## Setup steps (already done — for reference / disaster recovery)

### 1. NAS — livesync-bridge container

```bash
ssh root@192.168.2.110

mkdir -p /mnt/user/appdata/livesync-bridge/{dat,data}
mkdir -p /mnt/user/appdata/paperclip/shared/brain
chown -R 1000:1000 /mnt/user/appdata/paperclip/shared/brain /mnt/user/appdata/livesync-bridge

cd /tmp && git clone --recursive https://github.com/vrtmrz/livesync-bridge.git
cd livesync-bridge

# Patch Dockerfile for Deno 2.3 + uid 1000
cat > Dockerfile <<EOF
FROM denoland/deno:2.3.1
WORKDIR /app
VOLUME /app/dat
VOLUME /app/data
COPY . .
RUN deno install --allow-import \\
 && deno cache --allow-import main.ts \\
 && mkdir -p /app/dat /app/data /deno-dir \\
 && chown -R 1000:1000 /app /deno-dir
USER 1000:1000
CMD [ "deno", "task", "run" ]
EOF

docker build -t livesync-bridge:local .

# Write config
cat > /mnt/user/appdata/livesync-bridge/dat/config.json <<'EOF'
{
  "peers": [
    {
      "type": "couchdb",
      "group": "main",
      "name": "brain-personal-couchdb",
      "database": "brain-personal",
      "username": "brain",
      "password": "<from .secrets.local>",
      "url": "https://sync.toh.fyi",
      "passphrase": "<from .secrets.local LIVESYNC_E2E_PASSPHRASE>",
      "obfuscatePassphrase": "<same>",
      "baseDir": "",
      "useRemoteTweaks": true
    },
    {
      "type": "storage",
      "group": "main",
      "name": "brain-mirror-storage",
      "baseDir": "./data/brain/",
      "scanOfflineChanges": true,
      "useChokidar": true
    }
  ]
}
EOF

docker run -d \
  --name livesync-bridge \
  --restart unless-stopped \
  -v /mnt/user/appdata/livesync-bridge/dat:/app/dat \
  -v /mnt/user/appdata/paperclip/shared/brain:/app/data/brain \
  -l com.centurylinklabs.watchtower.enable=false \
  livesync-bridge:local
```

### 2. NAS — clone KarpathyMemory inside the paperclip volume

```bash
cd /mnt/user/appdata/paperclip
git clone https://github.com/pcameron80/KarpathyMemory.git karpathy-memory
chown -R 1000:1000 karpathy-memory

docker exec paperclip-src-paperclip-1 bash -c \
  'cd /paperclip/karpathy-memory && uv sync'
```

### 3. Container — settings.json hooks + registry

```bash
mkdir -p /mnt/user/appdata/paperclip/.claude/karpathy-memory/state
ln -sfn /paperclip/karpathy-memory \
  /mnt/user/appdata/paperclip/.claude/karpathy-memory/repo

cat > /mnt/user/appdata/paperclip/.claude/karpathy-memory/registry.json <<'EOF'
{
  "vaults": {"personal": "/paperclip/shared/brain"},
  "default_vault": "personal",
  "default_slug": "paperclip",
  "projects": [
    {"path": "/paperclip", "vault": "personal", "slug": "paperclip"},
    {"path": "/paperclip/repos/GreatPickDeals", "vault": "personal", "slug": "greatpickdeals"},
    {"path": "/paperclip/repos/paintcolorhq", "vault": "personal", "slug": "paintcolorhq"},
    {"path": "/paperclip/repos/dumpster-directory", "vault": "personal", "slug": "town-bins"},
    {"path": "/paperclip/repos/bloodhound", "vault": "personal", "slug": "bloodhound"}
  ]
}
EOF

cat > /mnt/user/appdata/paperclip/.claude/settings.json <<'EOF'
{
  "hooks": {
    "SessionStart": [{"matcher": "", "hooks": [{"type": "command",
      "command": "/usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory python hooks/session-start.py",
      "timeout": 15}]}],
    "PreCompact": [{"matcher": "", "hooks": [{"type": "command",
      "command": "/usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory python hooks/pre-compact.py",
      "timeout": 10}]}],
    "SessionEnd": [{"matcher": "", "hooks": [{"type": "command",
      "command": "/usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory python hooks/session-end.py",
      "timeout": 10}]}]
  }
}
EOF

chown -R 1000:1000 /mnt/user/appdata/paperclip/.claude/karpathy-memory \
                   /mnt/user/appdata/paperclip/.claude/settings.json
```

### 4. Skill installation

```bash
mkdir -p /mnt/user/appdata/paperclip/skills-custom/karpathy-memory
ln -sfn /paperclip/karpathy-memory/paperclip/SKILL.md \
        /mnt/user/appdata/paperclip/skills-custom/karpathy-memory/SKILL.md
chown -R 1000:1000 /mnt/user/appdata/paperclip/skills-custom/karpathy-memory

# Make the skill discoverable to Claude Code agents in the container
docker exec paperclip-src-paperclip-1 bash -c \
  'ln -sfn /paperclip/skills-custom/karpathy-memory /paperclip/.claude/skills/karpathy-memory'
```

### 5. (Manual) update OpenCode agent system prompts

The 4 Ollama agents (Ted, Sandy, Marshall, Captain) need a snippet of
this skill pasted into their HEARTBEAT.md or SOUL.md instructions in
Paperclip. Use the Paperclip web UI at https://paperclip.toh.fyi/ →
agent edit. Add a "Memory protocol" section that says:

> After meaningful work in a project, log a brief summary by running:
>
> ```bash
> KARPATHY_VAULT=/paperclip/shared/brain \
>   /usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory \
>   python scripts/memory_log.py \
>   --slug <project-slug> --agent <your-name> \
>   --section "Short title" --content "What you did, learned, what's next"
> ```
>
> Read `/paperclip/.claude/skills/karpathy-memory/SKILL.md` for the full
> protocol, including which slug to use and what's worth logging.

## Verification

```bash
# Inside the container, fire a fake session-end and confirm round-trip
ssh root@192.168.2.110 'docker exec paperclip-src-paperclip-1 bash -c "
cat > /tmp/test.jsonl <<JSONL
{\"message\":{\"role\":\"user\",\"content\":\"verifying paperclip→karpathy memory loop\"}}
JSONL
echo '\''{\"session_id\":\"verify-001\",\"source\":\"manual\",\"transcript_path\":\"/tmp/test.jsonl\",\"cwd\":\"/paperclip\"}'\'' | \
  /usr/local/bin/uv run --quiet --directory /paperclip/karpathy-memory python hooks/session-end.py
"'
sleep 30
# On the Mac:
tail -5 ~/Brain-Personal/10-projects/paperclip/daily_logs/$(date +%Y-%m-%d).md
```

If the entry shows up on the Mac, the full pipeline is healthy.
