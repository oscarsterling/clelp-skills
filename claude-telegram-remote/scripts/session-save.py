#!/usr/bin/env python3
"""Save a context brief of the current Claude Code session.

Designed to be paired with session-restore.py via !refresh. Optimized for
faithful round-trip context, not aggressive compression. Caps are per-section
(not per-message) so individual exchanges restore intact.

v3.2 changes vs the earlier compression-first implementation:
- Removed per-message char caps (was 300/400 on user/assistant). Individual
  messages now restore in full and only get trimmed at section budgets.
- Commit extraction now uses `git log --since=<session-start>` instead of
  regex-parsing bash `git commit -m "..."` invocations, which mis-captured
  heredoc preambles on multi-line messages.
- Output leads with the last full exchange (both sides), then earlier paired
  exchanges, then files/commits/tools. Easier to resume from.
"""
import json, os, sys, glob, subprocess, datetime, re, time

# === CONFIGURE THESE ===
# Auto-detect the Claude projects directory. Override if your layout differs.
PROJECTS_DIR = ""  # Leave empty for auto-detect, or set to your .claude/projects/... path
SAVE_DIR = os.path.expanduser("~/claude-telegram-remote/saved-contexts")
# Retention for auto-named refresh saves. Auto-named files (refresh-*) are
# timestamp-keyed and lose value within hours, so this is generous. Custom
# labels (anything not refresh-*) are never pruned - those are caller-chosen
# and deliberate. Added in v3.2.2 to bound saved-contexts/ growth.
REFRESH_RETENTION_DAYS = 14
# =======================

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")

# Labels must look like `[a-z0-9_-]+`. Anything else is rejected rather than
# silently scrubbed so the caller sees a clear error instead of writing
# somewhere they didn't expect.
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Control chars and channel-tag neutralization. Saved briefs are later
# wrapped in a synthetic <channel> frame and tmux-pasted back into Claude,
# so any literal `<channel` or `</channel>` in captured content could forge
# a frame with a different user_id. Defuse at write time.
_CHANNEL_OPEN_RE = re.compile(r"<channel", re.IGNORECASE)
_CHANNEL_CLOSE_RE = re.compile(r"</channel>", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _neutralize(text):
    """Strip control chars and break any literal channel tags.

    The defuse inserts an underscore between `<` and `channel` so tag-shaped
    tokens are still human-readable in the brief but no longer match as an
    inbound <channel> frame (regexes like `<channel\\s` fail on `<_channel`).
    """
    if not isinstance(text, str):
        return text
    text = _CONTROL_RE.sub("", text)
    text = _CHANNEL_OPEN_RE.sub("<_channel", text)
    text = _CHANNEL_CLOSE_RE.sub("</_channel>", text)
    return text


# Section budgets in chars. Generous so individual messages aren't sliced.
# Aim: total file under ~16KB so the tmux paste in !refresh stays reliable.
MAX_LAST_EXCHANGE_CHARS = 4000   # full user + full assistant at the tail
MAX_RECENT_EXCHANGE_CHARS = 6000 # paired earlier exchanges
MAX_PER_TOPIC_CHARS = 800        # terminal-text excerpts (less important)


def _detect_projects_dir():
    """Auto-detect the Claude Code projects directory for the current working directory."""
    if PROJECTS_DIR:
        return PROJECTS_DIR
    # Claude Code stores session JSONL files under ~/.claude/projects/-<escaped-path>/
    cwd = os.getcwd()
    escaped = cwd.replace("/", "-")
    candidate = os.path.expanduser(f"~/.claude/projects/{escaped}")
    if os.path.isdir(candidate):
        return candidate
    # Fallback: find the most recently modified projects subdirectory
    projects_base = os.path.expanduser("~/.claude/projects")
    if os.path.isdir(projects_base):
        subdirs = [os.path.join(projects_base, d) for d in os.listdir(projects_base)
                    if os.path.isdir(os.path.join(projects_base, d))]
        if subdirs:
            return max(subdirs, key=os.path.getmtime)
    return ""


def find_session_jsonl(projects_dir):
    """Most recently modified JSONL in the project dir wins."""
    files = sorted(glob.glob(os.path.join(projects_dir, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def parse_session(jsonl_path, max_lines=1500):
    """Parse session JSONL for meaningful context data.

    Returns full untruncated TG exchanges + file/tool metadata. Length capping
    happens in build_summary at the section level, not here.
    """
    with open(jsonl_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        # Read up to 6MB tail so long sessions still get full last exchanges
        read_size = min(size, 6 * 1024 * 1024)
        f.seek(size - read_size)
        tail = f.read().decode("utf-8", errors="replace")

    lines = tail.strip().split("\n")
    if len(lines) > max_lines:
        lines = lines[-max_lines:]

    telegram_replies = []     # Assistant's full TG messages, in order
    user_requests = []        # User's full TG messages, in order
    files_modified = []       # Files written/edited
    tools_used = set()        # Unique tool names (excluding read-only)
    topics = []               # Terminal-text excerpts (non-TG assistant output)
    session_start_iso = None  # First timestamp in JSONL, used for git log cutoff

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Capture the earliest timestamp we see for git log cutoff
        ts = entry.get("timestamp")
        if ts and not session_start_iso:
            session_start_iso = ts

        entry_type = entry.get("type")

        if entry_type == "assistant":
            msg = entry.get("message", {})
            content_list = msg.get("content", [])
            if not isinstance(content_list, list):
                continue
            for block in content_list:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    if name == "mcp__plugin_telegram_telegram__reply":
                        text = inp.get("text", "")
                        if text and len(text) > 20:
                            telegram_replies.append(_neutralize(text))  # FULL, but defused
                    elif name in ("Write", "Edit"):
                        fp = inp.get("file_path", "")
                        if fp and fp not in files_modified:
                            files_modified.append(fp)
                    elif name == "Agent":
                        desc = inp.get("description", "")
                        if desc:
                            tools_used.add(f"Agent({desc})")
                    elif name not in ("Read", "Glob", "Grep", "ToolSearch", "Bash"):
                        tools_used.add(name)
                elif block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text and len(text) > 30 and not text.startswith("Waiting"):
                        # Cap terminal-text snippets only (least important)
                        topics.append(text[:MAX_PER_TOPIC_CHARS])

        if entry_type == "user":
            msg = entry.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                # Real Telegram messages arrive wrapped in <channel> tags
                tg_match = re.search(r'<channel[^>]*>(.*?)</channel>', content, re.DOTALL)
                if tg_match:
                    tg_text = tg_match.group(1).strip()
                    # Skip the auto-injected restore payloads (they begin with
                    # "Context restore from") so we don't recursively echo old saves
                    if tg_text and len(tg_text) > 5 and not tg_text.startswith("Context restore from"):
                        user_requests.append(_neutralize(tg_text))  # FULL, but defused
                elif not content.startswith("<") and not content.startswith("[{"):
                    if len(content) > 10:
                        user_requests.append(_neutralize(content))

    return {
        "telegram_replies": telegram_replies,
        "user_requests": user_requests,
        "files_modified": files_modified,
        "tools_used": sorted(tools_used)[:15],
        "topics": topics[-5:],
        "session_start_iso": session_start_iso,
    }


def get_recent_commits(session_start_iso):
    """Pull commits via `git log` since the session began.

    Replaces the old approach of regex-parsing `git commit -m "..."` from bash
    invocations, which broke on heredoc syntax (`-m "$(cat <<'EOF'..."`) by
    capturing the heredoc preamble instead of the actual commit body.

    Runs against whatever git repo the current working directory is in. If
    you're not in a repo, this silently returns [].
    """
    try:
        if session_start_iso:
            since = session_start_iso
        else:
            # Fallback: 6 hours, covers most sessions
            since = "6 hours ago"
        r = subprocess.run(
            ["git", "log", f"--since={since}", "--pretty=format:%h %s"],
            capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return []
        out = r.stdout.strip()
        if not out:
            return []
        return [l for l in out.split("\n") if l.strip()]
    except Exception:
        return []


def pair_exchanges(user_requests, telegram_replies):
    """Zip user messages with assistant replies in order.

    Best-effort pairing: if reply N is missing or extra, we still surface what
    we have. The goal is human readability, not strict request/response logic.
    """
    pairs = []
    n = max(len(user_requests), len(telegram_replies))
    for i in range(n):
        req = user_requests[i] if i < len(user_requests) else None
        rep = telegram_replies[i] if i < len(telegram_replies) else None
        pairs.append((req, rep))
    return pairs


def build_summary(parsed_data, recent_commits):
    sections = []

    pairs = pair_exchanges(parsed_data["user_requests"], parsed_data["telegram_replies"])

    # 1. Last exchange — FULL text, both sides. Most important section.
    sections.append("## Last exchange (full)")
    if pairs:
        last_req, last_rep = pairs[-1]
        if last_req:
            sections.append(f"**User:** {last_req}")
            sections.append("")
        if last_rep:
            sections.append(f"**Assistant:** {last_rep}")
    else:
        sections.append("(no exchanges captured)")

    # 2. Recent exchanges (preceding the last one) — paired, fuller text.
    sections.append("")
    sections.append("## Recent exchanges")
    earlier = pairs[:-1] if len(pairs) > 1 else []
    if earlier:
        budget = MAX_RECENT_EXCHANGE_CHARS
        rendered = []
        for req, rep in reversed(earlier):  # newest of the earlier first
            entry_lines = []
            if req:
                entry_lines.append(f"- **User:** {req}")
            if rep:
                entry_lines.append(f"  **Assistant:** {rep}")
            entry = "\n".join(entry_lines)
            if not entry:
                continue
            if len(entry) > budget:
                break
            rendered.append(entry)
            budget -= len(entry)
        # Restore chronological order (oldest first)
        sections.extend(reversed(rendered))
    else:
        sections.append("(no earlier exchanges)")

    # 3. Files changed in this session
    if parsed_data["files_modified"]:
        sections.append("")
        sections.append("## Files changed")
        home = os.path.expanduser("~")
        for f in parsed_data["files_modified"][:20]:
            sections.append(f"- {f.replace(home, '~')}")

    # 4. Commits — straight from `git log`, not regex'd from bash
    if recent_commits:
        sections.append("")
        sections.append("## Commits")
        for c in recent_commits[:10]:
            sections.append(f"- {c}")

    # 5. Tools/agents that ran (handy for jogging memory of subagent work)
    if parsed_data["tools_used"]:
        sections.append("")
        sections.append("## Tools/agents invoked")
        sections.append(", ".join(parsed_data["tools_used"]))

    return "\n".join(sections)


def prune_old_refresh_saves(retention_days=REFRESH_RETENTION_DAYS):
    """Delete refresh-*.md files older than retention_days. Returns count
    pruned. Non-refresh saves (custom labels) are kept forever.

    Run at the start of each save so the dir is self-maintaining and we
    don't need a separate cron. Failures are non-fatal — pruning never
    blocks a real save."""
    if not os.path.isdir(SAVE_DIR):
        return 0
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    try:
        for fname in os.listdir(SAVE_DIR):
            if not fname.startswith("refresh-") or not fname.endswith(".md"):
                continue
            fpath = os.path.join(SAVE_DIR, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.unlink(fpath)
                    pruned += 1
            except OSError:
                pass
    except OSError:
        pass
    return pruned


def main():
    if len(sys.argv) < 2:
        print("Usage: session-save.py <label>")
        sys.exit(1)

    label = sys.argv[1].strip().replace(" ", "-").lower()
    if not LABEL_RE.match(label):
        print(f"ERROR: invalid label {label!r}. Use [a-z0-9_-] only, no path separators.")
        sys.exit(2)
    os.makedirs(SAVE_DIR, exist_ok=True)
    pruned = prune_old_refresh_saves()

    projects_dir = _detect_projects_dir()
    if not projects_dir:
        print("ERROR: Could not detect Claude Code projects directory.")
        print("Set PROJECTS_DIR at the top of session-save.py.")
        sys.exit(1)

    jsonl_path = find_session_jsonl(projects_dir)
    if not jsonl_path:
        print("ERROR: No session JSONL files found")
        sys.exit(1)
    session_id = os.path.basename(jsonl_path).replace(".jsonl", "")

    parsed = parse_session(jsonl_path)
    commits = get_recent_commits(parsed["session_start_iso"])
    summary = build_summary(parsed, commits)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"# Session Save: {label}\n**Saved:** {now} | **Session:** {session_id[:8]}\n\n"
    content = header + summary + "\n"

    save_path = os.path.join(SAVE_DIR, f"{label}.md")
    with open(save_path, "w") as f:
        f.write(content)

    prune_suffix = f" pruned={pruned}" if pruned else ""
    print(f"Saved '{label}' ({len(content)} chars, "
          f"{len(parsed['telegram_replies'])} TG replies, "
          f"{len(parsed['user_requests'])} user msgs, "
          f"{len(parsed['files_modified'])} files, "
          f"{len(commits)} commits){prune_suffix}")


if __name__ == "__main__":
    main()
