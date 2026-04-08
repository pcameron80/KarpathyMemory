"""
Hermes — QA gate for the Karpathy compiler.

Hermes runs AFTER compile.py and validates each newly created or modified
article in the knowledge base. Articles that fail validation are moved to
`knowledge/_pending/` (quarantine) and a report is appended to
`knowledge/log.md`.

Why "after compile" instead of "before"?  Adding a pre-compile gate would
require modifying compile.py's input. Post-compile is purely additive — if
we delete hermes.py tomorrow, the knowledge base still works exactly like
the unmodified upstream reference.

Hermes uses a SEPARATE model family (Gemini via the local `gemini` CLI)
and a stricter, adversarial prompt. The compiler (Claude) writes; Hermes
(Gemini) challenges. Cross-model review is stronger than same-family
review because the two models' hallucination patterns and blind spots
differ — the same trick that makes code review work, but between species.

Usage (typically called by daily_flush_all.py after compile.py):

    KARPATHY_PROJECT_DIR=~/Brain-Personal/10-projects/town-bins \
    KARPATHY_STATE_DIR=~/.claude/karpathy-memory/state/town-bins \
    uv run python scripts/hermes.py

Direct invocation:

    uv run python scripts/hermes.py                    # validate all changed articles
    uv run python scripts/hermes.py --all              # re-validate every article
    uv run python scripts/hermes.py --file knowledge/concepts/foo.md
    uv run python scripts/hermes.py --dry-run          # show plan, no writes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

# Recursion guard (harmless even though Hermes no longer invokes Claude)
os.environ.setdefault("CLAUDE_INVOKED_BY", "hermes")

GEMINI_BIN = os.environ.get("GEMINI_BIN", "/usr/local/bin/gemini")
# Default is flash, not pro: flash is ~3-5x faster, has much higher daily
# free-tier request limits, and is plenty strong for adversarial QA review
# of Codex-written articles. Override via HERMES_MODEL env var for pro.
DEFAULT_MODEL = os.environ.get("HERMES_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT_SEC = int(os.environ.get("HERMES_TIMEOUT", "180"))

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# config.py is env-var driven; importing it after setting env vars resolves paths
from config import (  # noqa: E402
    AGENTS_FILE,
    CONCEPTS_DIR,
    CONNECTIONS_DIR,
    DAILY_DIR,
    INDEX_FILE,
    KNOWLEDGE_DIR,
    LOG_FILE,
    PROJECT_ROOT,
    QA_DIR,
    STATE_DIR,
    now_iso,
)

PENDING_DIR = KNOWLEDGE_DIR / "_pending"
HERMES_STATE_FILE = STATE_DIR / "hermes-state.json"
HERMES_LOG_FILE = STATE_DIR / "hermes.log"

# Verdict markers Hermes is allowed to return.
VERDICT_PASS = "PASS"
VERDICT_QUARANTINE = "QUARANTINE"
VERDICT_ERROR = "HERMES_ERROR"


def configure_logging() -> None:
    HERMES_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(HERMES_LOG_FILE))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def load_state() -> dict:
    if HERMES_STATE_FILE.exists():
        try:
            return json.loads(HERMES_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"validated": {}, "quarantined": {}, "last_run": None}


def save_state(state: dict) -> None:
    HERMES_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HERMES_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()[:16]


def list_articles() -> list[Path]:
    """All article files Hermes is responsible for validating."""
    articles = []
    for subdir in (CONCEPTS_DIR, CONNECTIONS_DIR, QA_DIR):
        if subdir.exists():
            articles.extend(sorted(subdir.glob("*.md")))
    return articles


def changed_articles(state: dict, all_mode: bool = False) -> list[Path]:
    """Articles that have changed since last validation, or all if all_mode."""
    if all_mode:
        return list_articles()

    out = []
    validated = state.get("validated", {})
    for a in list_articles():
        rel = str(a.relative_to(KNOWLEDGE_DIR))
        cur_hash = file_hash(a)
        prev = validated.get(rel, {})
        if prev.get("hash") != cur_hash:
            out.append(a)
    return out


def gather_source_logs(article: Path) -> str:
    """Read the daily logs an article was compiled from (per its YAML frontmatter)."""
    content = article.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return "(no YAML frontmatter — article has no traceable source)"

    end = content.find("---", 3)
    if end == -1:
        return "(malformed frontmatter)"

    fm = content[3:end]
    sources = []
    in_sources = False
    for line in fm.splitlines():
        if line.strip().startswith("sources:"):
            in_sources = True
            continue
        if in_sources:
            stripped = line.strip()
            if stripped.startswith("- "):
                sources.append(stripped[2:].strip().strip('"').strip("'"))
            elif stripped and not stripped.startswith("-"):
                in_sources = False

    if not sources:
        return "(no sources listed in frontmatter)"

    parts = []
    for s in sources:
        # source paths are relative to project root, e.g. "daily_logs/2026-04-07.md"
        log_path = PROJECT_ROOT / s
        if not log_path.exists():
            # Old format — try DAILY_DIR
            log_path = DAILY_DIR / Path(s).name
        if log_path.exists():
            parts.append(f"### {s}\n\n```markdown\n{log_path.read_text(encoding='utf-8')}\n```")
        else:
            parts.append(f"### {s}\n\n(SOURCE LOG NOT FOUND — article may reference a deleted log)")

    return "\n\n".join(parts)


def gather_sibling_articles(article: Path, max_chars: int = 12_000) -> str:
    """Read other articles in the same KB so Hermes can spot contradictions/dupes."""
    others = [a for a in list_articles() if a != article]
    parts = []
    total = 0
    for o in others:
        rel = str(o.relative_to(KNOWLEDGE_DIR))
        body = o.read_text(encoding="utf-8")
        section = f"### {rel}\n\n```markdown\n{body}\n```"
        if total + len(section) > max_chars:
            parts.append(f"### ...({len(others) - len(parts)} more articles truncated)")
            break
        parts.append(section)
        total += len(section)
    return "\n\n".join(parts) if parts else "(no other articles yet)"


def validate_one(article: Path, model: str = DEFAULT_MODEL) -> tuple[str, str]:
    """Run Hermes on a single article. Returns (verdict, reasoning_text)."""
    article_content = article.read_text(encoding="utf-8")
    rel_path = article.relative_to(KNOWLEDGE_DIR)
    sources = gather_source_logs(article)
    siblings = gather_sibling_articles(article)
    schema = AGENTS_FILE.read_text(encoding="utf-8") if AGENTS_FILE.exists() else "(no AGENTS.md)"

    prompt = f"""You are HERMES, the QA reviewer for a personal knowledge base. Your job
is NOT to write or improve articles — your job is to challenge them.

You receive ONE article that was just compiled from raw daily conversation
logs by another agent. You judge whether the article is safe to keep in
the live knowledge base, or whether it should be QUARANTINED for human
review.

Be skeptical. The compiling agent is hardworking but can hallucinate,
duplicate existing knowledge, or write claims that don't appear in the
source. Your job is to catch those.

## Schema (AGENTS.md)

{schema}

## Article under review

**Path:** `{rel_path}`

```markdown
{article_content}
```

## Source daily logs (the ONLY ground truth)

{sources}

## Sibling articles (the rest of the knowledge base)

{siblings}

## Checks (run all of them)

1. **Hallucination.** Every factual claim in the article must be traceable
   to the source daily logs. If the article asserts X and the logs don't
   say X, that is a hallucination — quarantine.

2. **Contradiction.** If the article contradicts a claim in any sibling
   article without acknowledging the conflict, quarantine.

3. **Duplication.** If a sibling article already covers the same ground
   with the same level of detail, quarantine — the compiler should have
   updated the existing article instead of creating a near-duplicate.

4. **Frontmatter.** Article must have YAML frontmatter with at least
   `title`, `sources`, `created`, `updated`. Missing → quarantine.

5. **Sources resolvable.** The `sources:` list must point to real files
   that the article's content can plausibly have come from. If
   "(SOURCE LOG NOT FOUND)" appears above, quarantine.

6. **Self-evident style.** Encyclopedia tone, factual, third-person
   where appropriate. Marketing copy, opinions presented as facts, or
   conversational asides → quarantine.

7. **Sparseness.** Below ~150 words AND no clear "Key Points" or
   "Details" → quarantine (incomplete).

## Your response

Respond with EXACTLY one of two formats. No preamble, no code fences, no
markdown headers around the verdict word.

**If the article passes all checks**, your entire response must be:

    PASS
    One short sentence summarizing why this article is good.

**If the article should be quarantined**, your entire response must be:

    QUARANTINE
    1. **<check name>.** <specific evidence — quote exact phrases from the article and the source logs>
    2. **<check name>.** <specific evidence>
    ...

Rules for the response:
- Start with the literal word PASS or QUARANTINE on its own line (no wrapping characters, no markdown)
- Do NOT echo the placeholder text (no `<check name>`, no angle-bracket templates)
- Do NOT use any tools, propose edits, or rewrite the article
- Your only job is the verdict itself
"""

    # Shell out to Gemini CLI in headless, read-only mode.
    # --approval-mode plan = read-only (no tool execution), our equivalent
    # of Claude SDK's allowed_tools=[]. -o text keeps stdout clean for parsing.
    try:
        result = subprocess.run(
            [
                GEMINI_BIN,
                "-p", prompt,
                "-m", model,
                "--approval-mode", "plan",
                "-o", "text",
            ],
            capture_output=True,
            text=True,
            timeout=GEMINI_TIMEOUT_SEC,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        logging.error("Hermes timeout on %s after %ds", rel_path, GEMINI_TIMEOUT_SEC)
        return VERDICT_ERROR, f"gemini timeout after {GEMINI_TIMEOUT_SEC}s"
    except FileNotFoundError:
        logging.error("Hermes: gemini binary not found at %s", GEMINI_BIN)
        return VERDICT_ERROR, f"gemini binary not found at {GEMINI_BIN}"
    except Exception as e:
        import traceback
        logging.error("Hermes subprocess error on %s: %s\n%s", rel_path, e, traceback.format_exc())
        return VERDICT_ERROR, f"{type(e).__name__}: {e}"

    if result.returncode != 0:
        stderr_tail = (result.stderr or "")[-500:]
        logging.error("Hermes gemini rc=%d on %s: %s", result.returncode, rel_path, stderr_tail)
        return VERDICT_ERROR, f"gemini rc={result.returncode}: {stderr_tail}"

    response = result.stdout or ""

    # Strip leading/trailing whitespace and any markdown code fences
    response = response.strip()
    if response.startswith("```"):
        # Drop the first line (opening fence, possibly with language tag)
        response = response.split("\n", 1)[1] if "\n" in response else ""
        # Drop the trailing fence
        if response.rstrip().endswith("```"):
            response = response.rstrip()[:-3].rstrip()
    response = response.strip()

    if response.startswith(VERDICT_PASS):
        return VERDICT_PASS, response[len(VERDICT_PASS):].strip()
    if response.startswith(VERDICT_QUARANTINE):
        return VERDICT_QUARANTINE, response[len(VERDICT_QUARANTINE):].strip()
    # Unparseable response — treat as error so we don't silently lose articles
    return VERDICT_ERROR, f"unparseable verdict: {response[:200]}"


def quarantine_article(article: Path, reason: str) -> Path:
    """Move an article to knowledge/_pending/ with a sidecar reason file."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    rel = article.relative_to(KNOWLEDGE_DIR)
    target = PENDING_DIR / rel.name
    # If a file with that name is already in pending, suffix with timestamp
    if target.exists():
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        target = PENDING_DIR / f"{rel.stem}.{ts}{rel.suffix}"

    shutil.move(str(article), str(target))
    sidecar = target.with_suffix(target.suffix + ".hermes-reason.md")
    sidecar.write_text(
        f"# Hermes verdict for `{rel}`\n\n"
        f"Quarantined at {now_iso()}\n\n"
        f"## Reason\n\n{reason}\n",
        encoding="utf-8",
    )
    return target


def append_log_entry(verdict: str, rel_path: str, reason: str) -> None:
    """Append a Hermes verdict to knowledge/log.md."""
    if not LOG_FILE.parent.exists():
        return
    if not LOG_FILE.exists():
        LOG_FILE.write_text(
            f"# Build Log\n\nSeeded {datetime.now(timezone.utc).astimezone().date().isoformat()}\n\n",
            encoding="utf-8",
        )
    short = (reason[:200] + "...") if len(reason) > 200 else reason
    entry = (
        f"\n## [{now_iso()}] hermes | {verdict} | {rel_path}\n"
        f"- {short}\n"
    )
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


def run(args: argparse.Namespace) -> int:
    state = load_state()
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = (PROJECT_ROOT / target).resolve()
        if not target.exists():
            print(f"file not found: {target}", file=sys.stderr)
            return 1
        articles = [target]
    else:
        articles = changed_articles(state, all_mode=args.all)

    if not articles:
        print("Hermes: nothing to validate")
        return 0

    print(f"Hermes: validating {len(articles)} article(s)")
    logging.info("validating %d articles (project=%s)", len(articles), PROJECT_ROOT.name)

    quarantined = 0
    passed = 0
    errored = 0

    for article in articles:
        rel = str(article.relative_to(KNOWLEDGE_DIR))
        if args.dry_run:
            print(f"  · would validate {rel}")
            continue

        verdict, reason = validate_one(article, model=args.model)
        cur_hash = file_hash(article) if article.exists() else "deleted"

        if verdict == VERDICT_PASS:
            print(f"  ✓ pass    {rel}")
            state["validated"][rel] = {
                "hash": cur_hash,
                "verdict": verdict,
                "at": now_iso(),
            }
            passed += 1
        elif verdict == VERDICT_QUARANTINE:
            target = quarantine_article(article, reason)
            print(f"  ⚠ quarantine  {rel} → {target.relative_to(KNOWLEDGE_DIR)}")
            state["quarantined"][rel] = {
                "moved_to": str(target.relative_to(KNOWLEDGE_DIR)),
                "verdict": verdict,
                "reason": reason,
                "at": now_iso(),
            }
            # Drop any prior PASS validation for this rel since the file moved
            state["validated"].pop(rel, None)
            append_log_entry(verdict, rel, reason)
            quarantined += 1
        else:
            print(f"  ! error   {rel}: {reason[:80]}")
            errored += 1
            append_log_entry(verdict, rel, reason)

        logging.info("%-12s %s", verdict, rel)

    state["last_run"] = now_iso()
    if not args.dry_run:
        save_state(state)

    print(f"Hermes: pass={passed} quarantine={quarantined} error={errored}")
    return 0 if errored == 0 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes — QA gate for the Karpathy compiler.")
    parser.add_argument("--all", action="store_true", help="re-validate every article")
    parser.add_argument("--file", help="validate one specific file")
    parser.add_argument("--dry-run", action="store_true", help="show plan, no writes")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"gemini model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    configure_logging()
    rc = run(args)
    sys.exit(rc)


if __name__ == "__main__":
    main()
