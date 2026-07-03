#!/usr/bin/env python3
"""Telegram Commander - Remote control Claude Code via ! commands from Telegram."""
import inspect
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Sanitizer for restored briefs. Saved briefs from recent session-save.py
# versions already neutralize channel tags at write time, but older on-disk
# briefs may not, and defense in depth is cheap. Strip control chars and
# break any literal <channel> tokens so a restored brief cannot forge a
# new inbound frame with a different user_id once it's wrapped and tmux
# pasted back into the fresh Claude session.
_BRIEF_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BRIEF_CHANNEL_OPEN_RE = re.compile(r"<channel", re.IGNORECASE)
_BRIEF_CHANNEL_CLOSE_RE = re.compile(r"</channel>", re.IGNORECASE)


def _sanitize_brief(text):
    if not isinstance(text, str):
        return text
    text = _BRIEF_CONTROL_RE.sub("", text)
    text = _BRIEF_CHANNEL_OPEN_RE.sub("<_channel", text)
    text = _BRIEF_CHANNEL_CLOSE_RE.sub("</_channel>", text)
    return text

# === CONFIGURE THESE ===
YOUR_USER_ID = 0  # Your Telegram user ID (get it from @userinfobot)
TMUX_SESSION = "claude"  # Your tmux session name where Claude Code runs
TMUX_PATH = "/opt/homebrew/bin/tmux"  # `which tmux` to find yours
RESTART_SCRIPT = ""  # Optional: absolute path to your restart script (leave "" to disable !restart)
HEALTH_SCRIPT = ""  # Optional: absolute path to a health-check script (leave "" to disable !health)
REPO_DIR = os.path.expanduser("~/claude-telegram-remote")  # Where this repo lives
# =======================

PID_FILE = os.path.join(REPO_DIR, "commander.pid")
LOG_FILE = os.path.join(REPO_DIR, "commander.log")
POLL_TIMEOUT = 30
RETRY_DELAY = 30
running = True


def get_bot_token():
    """Read bot token from macOS Keychain."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", "clawdbot",
             "-s", "telegram-commander-bot-token", "-w"],
            capture_output=True, text=True, timeout=10)
        token = r.stdout.strip()
        if not token:
            logging.error("Bot token empty from Keychain")
            sys.exit(1)
        return token
    except Exception as e:
        logging.error("Failed to get bot token: %s", e)
        sys.exit(1)


def telegram_api(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        data = json.dumps(params).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        logging.warning("Telegram API error (%s): %s", method, e)
        return None


def reply(token, chat_id, text):
    telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_buttons(token, chat_id, text, buttons_json):
    """Send a message with inline keyboard buttons via the Commander bot itself."""
    telegram_api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": buttons_json}
    })


def find_claude_pid():
    r = subprocess.run(["pgrep", "-f", "claude.*--channels"], capture_output=True, text=True)
    pids = r.stdout.strip().split("\n") if r.stdout.strip() else []
    return pids[0] if pids and pids[0] else None


def cmd_ping():
    return "Pong"


def cmd_status():
    pid = find_claude_pid()
    if not pid:
        return "Claude is NOT running."
    lines = [f"Claude is running (PID {pid})"]
    ps = subprocess.run(["ps", "-o", "etime=", "-p", pid], capture_output=True, text=True)
    if ps.stdout.strip():
        lines.append(f"Uptime: {ps.stdout.strip()}")
    return "\n".join(lines)


def cmd_stop():
    pid = find_claude_pid()
    if not pid:
        return "No Claude process found."
    try:
        os.kill(int(pid), signal.SIGINT)
        return f"Sent SIGINT to PID {pid}. Claude is waiting for input."
    except OSError as e:
        return f"Failed to stop PID {pid}: {e}"


def cmd_restart():
    """Restart Claude Code session via RESTART_SCRIPT.

    Advanced: for a wake-ping after manual !restart, have this function also
    write a "manual flag" file, and have your restart script check for it
    after verifying the new session is ready. If present, sleep ~10s, inject
    a wake-prompt into tmux, delete the flag. Nightly/scheduled restarts
    skip the flag write, so they stay silent. See README "Advanced: Wake-Ping".
    """
    if not RESTART_SCRIPT:
        return "No RESTART_SCRIPT configured. Set RESTART_SCRIPT in telegram-commander.py."
    try:
        # Optional: mark this as a manual restart so the script can wake-ping.
        # manual_flag = os.path.join(REPO_DIR, "restart-manual-flag")
        # with open(manual_flag, "w") as f: f.write(str(time.time()))
        subprocess.Popen(["bash", RESTART_SCRIPT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "Session restarting. Give it 30 seconds."
    except Exception as e:
        return f"Restart failed: {e}"


def inject_slash_command(slash_cmd, pre_clear=True):
    """Inject a slash command into the running Claude Code tmux session.

    pre_clear: send Escape, 0.4s, Escape, 0.4s, Enter, 0.5s before the slash
    text to drop out of any open picker or popup and clear stale text in CC's
    input box. Default True for the common case where the input may not be
    empty. Set False when the caller knows the input is already clean (e.g.
    cmd_refresh's post-/reset restore inject, where /reset just spawned a
    fresh session with an empty input by definition).

    The Escape Escape pair drops CC out of any open picker. The 0.4s gap
    between the two escapes matters: CC's Ink-based input has a render cycle,
    and sending two Escapes faster than that landed on stale frames in
    testing.

    The Enter between the second Escape and the slash text covers an edge
    case where the input box is EMPTY: in that state, Escape Escape opens
    CC's Rewind dialog instead of being a no-op. The subsequent slash text
    would type into that dialog and a stray Enter could fire an actual
    rewind. With "(current)" highlighted by default, Enter exits the dialog
    without rewinding anything. If no Rewind dialog is open, Enter on an
    empty input is a no-op (CC ignores empty submits), so this is safe in
    the text-was-in-box case too.
    """
    check = subprocess.run([TMUX_PATH, "has-session", "-t", TMUX_SESSION],
                           capture_output=True, text=True)
    if check.returncode != 0:
        return "no_session"
    if pre_clear:
        subprocess.run(
            [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Escape"],
            capture_output=True, text=True, timeout=5)
        time.sleep(0.4)
        subprocess.run(
            [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Escape"],
            capture_output=True, text=True, timeout=5)
        time.sleep(0.4)
        subprocess.run(
            [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Enter"],
            capture_output=True, text=True, timeout=5)
        time.sleep(0.5)
    # Send text and Enter as two separate send-keys calls. The -l flag forces
    # literal-text mode on the text send, so tmux does not try to parse any
    # token in slash_cmd as a key name (matters for !refresh and !restore,
    # where the payload is a multi-line <channel> block). The 0.5s gap gives
    # CC time to process large pastes before Enter arrives.
    r = subprocess.run(
        [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "-l", slash_cmd],
        capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return f"error: {r.stderr.strip()}"
    time.sleep(0.5)
    r2 = subprocess.run(
        [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Enter"],
        capture_output=True, text=True, timeout=10)
    if r2.returncode == 0:
        return "sent"
    return f"error: {r2.stderr.strip()}"


def inject_key(key_name):
    """Send a raw key (not text) into the tmux session."""
    check = subprocess.run([TMUX_PATH, "has-session", "-t", TMUX_SESSION],
                           capture_output=True, text=True)
    if check.returncode != 0:
        return "no_session"
    r = subprocess.run(
        [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, key_name],
        capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        return "sent"
    return f"error: {r.stderr.strip()}"


def read_current_mode():
    """Read current permission mode from tmux pane status line."""
    try:
        r = subprocess.run(
            [TMUX_PATH, "capture-pane", "-t", TMUX_SESSION, "-p"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for line in reversed(r.stdout.strip().split("\n")):
                low = line.lower()
                if "shift+tab to cycle" in low or "permissions" in low:
                    if "bypass" in low:
                        return "bypass permissions"
                    if "plan" in low:
                        return "plan mode"
                    if "auto" in low:
                        return "auto mode"
                    if "accept" in low:
                        return "accept edits"
            return "default mode"
        return "unknown"
    except Exception:
        return "unknown"


def _no_session_msg():
    return f"No tmux session '{TMUX_SESSION}' found. Is Claude running in tmux?"


def cmd_plan():
    result = inject_slash_command("/plan")
    if result == "sent":
        return "Sent /plan."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_mode():
    result = inject_key("BTab")
    if result == "sent":
        time.sleep(0.5)
        mode = read_current_mode()
        return f"Cycled mode (Shift+Tab). Current: {mode}"
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_compact():
    result = inject_slash_command("/compact")
    if result == "sent":
        return "Sent /compact."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_clear():
    result = inject_slash_command("/clear")
    if result == "sent":
        return "Sent /clear. Fresh conversation."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_model(args=""):
    model = args.strip() if args else ""
    if not model:
        try:
            r = subprocess.run(
                [TMUX_PATH, "capture-pane", "-t", TMUX_SESSION, "-p"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                for line in reversed(r.stdout.strip().split("\n")):
                    low = line.lower()
                    if "opus" in low or "sonnet" in low or "haiku" in low:
                        return f"Current model: {line.strip()}"
        except Exception:
            pass
        return "Could not read current model."
    result = inject_slash_command(f"/model {model}")
    if result == "sent":
        return f"Switched to {model}."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_opus():
    result = inject_slash_command("/model default")
    if result == "sent":
        return "Switched to Opus (1M context)."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_sonnet():
    result = inject_slash_command("/model sonnet")
    if result == "sent":
        return "Switched to Sonnet."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_cost():
    result = inject_slash_command("/cost")
    if result == "sent":
        return "Sent /cost."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_context():
    """Scrape Claude Code status line from tmux pane. No Claude turn burned."""
    import re
    try:
        r = subprocess.run(
            [TMUX_PATH, "capture-pane", "-t", TMUX_SESSION, "-p"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return _no_session_msg()
        lines = r.stdout.split("\n")
        model_re = re.compile(r"(Opus|Sonnet|Haiku)\s+[\d.]+", re.IGNORECASE)
        pct_re = re.compile(r"(\d+)%\s*(\d+[KM])")
        model_line = ctx_line = ""
        # Scan from the bottom. CC's status line is always at the very bottom
        # of the pane, but conversation echoes (e.g. an earlier script that
        # printed "On Opus 4.7, ..." or any prose mention of "Sonnet N.N")
        # can match the model regex too. Forward iteration with `break` would
        # pick stale echoes; reverse iteration locks onto the live render.
        # Bug fired when an above-status echo collided with the auto-mode
        # subline directly below it to produce "context line not parseable:
        # ... auto mode classifier" responses to !context.
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if model_re.search(line):
                model_line = line.strip()
                if idx + 1 < len(lines):
                    ctx_line = lines[idx + 1].strip()
                break
        if not model_line:
            return "Status line not found in pane. Is Claude CLI running?"
        model_m = model_re.search(model_line)
        model = model_line[model_m.start():].split("|")[0].strip()
        model = re.sub(r"[^\w\s().]", "", model).strip()
        pct_m = pct_re.search(ctx_line)
        if pct_m:
            pct, window = pct_m.group(1), pct_m.group(2)
            return f"{model} | {pct}% of {window} context used"
        window_m = re.search(r"\((\d+[KM])\s*context\)", model)
        window = window_m.group(1) if window_m else "?"
        return f"{model} | 0% of {window} context used"
    except subprocess.TimeoutExpired:
        return "tmux capture-pane timed out."
    except Exception as e:
        return f"Context check failed: {e}"


def cmd_effort(args=""):
    level = args.strip().lower() if args else ""
    valid_levels = {"max", "high", "medium", "auto"}
    if level in valid_levels:
        result = inject_slash_command(f"/effort {level}")
        if result == "sent":
            return f"Set effort to {level}."
        if result == "no_session":
            return _no_session_msg()
        return f"Failed: {result}"
    return "picker"


def cmd_health():
    if not HEALTH_SCRIPT:
        return "No HEALTH_SCRIPT configured. Set HEALTH_SCRIPT in telegram-commander.py."
    try:
        r = subprocess.run(
            ["bash", HEALTH_SCRIPT, "check", "--quiet"],
            capture_output=True, text=True, timeout=30)
        output = r.stdout.strip()
        if not output:
            return "All systems healthy."
        return output
    except subprocess.TimeoutExpired:
        return "Health check timed out."
    except Exception as e:
        return f"Health check failed: {e}"


def cmd_fast():
    """Switch to fast output mode."""
    result = inject_slash_command("/fast")
    if result == "sent":
        return "Switched to fast mode (same model, faster output)."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_resume(args=""):
    """Resume a previous conversation."""
    result = inject_slash_command(f"/resume {args}".strip())
    if result == "sent":
        return "Sent /resume."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


def cmd_init():
    """Initialize CLAUDE.md for current project."""
    result = inject_slash_command("/init")
    if result == "sent":
        return "Sent /init."
    if result == "no_session":
        return _no_session_msg()
    return f"Failed: {result}"


_rewind_last_run = 0  # cooldown guard


def cmd_rewind(args=""):
    """Open /rewind picker, read checkpoints from tmux, send as Telegram buttons."""
    global _rewind_last_run
    now = time.time()
    # Cooldown: ignore if fired within last 20 seconds
    if now - _rewind_last_run < 20:
        return None  # silently skip duplicate
    _rewind_last_run = now

    check = subprocess.run([TMUX_PATH, "has-session", "-t", TMUX_SESSION],
                           capture_output=True, text=True)
    if check.returncode != 0:
        return _no_session_msg()

    # Inject /rewind into CC to open the picker
    result = inject_slash_command("/rewind")
    if result != "sent":
        return f"Failed to send /rewind: {result}"
    time.sleep(2.5)

    # Read the tmux pane to parse checkpoints
    r = subprocess.run([TMUX_PATH, "capture-pane", "-t", TMUX_SESSION, "-p"],
                       capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        return "Could not read tmux pane."
    lines = r.stdout.strip().split("\n")

    # Parse checkpoint entries from Claude Code's rewind picker.
    # Format:
    #   /clear
    #   27 files changed +1274 -264
    #   /reset/reset
    #   2 files changed +63 -8
    # > (current)
    checkpoints = []
    in_rewind = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "Rewind" in stripped and not in_rewind:
            in_rewind = True
            continue
        if not in_rewind:
            continue
        if "(current)" in stripped:
            break
        if stripped.startswith("/") or stripped.startswith("\u2190"):
            # This is a checkpoint label
            detail = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if "file" in next_line and "changed" in next_line:
                    detail = f" ({next_line})"
                elif next_line == "No code changes":
                    detail = " (no code changes)"
            checkpoints.append({"label": stripped + detail, "index": len(checkpoints)})

    if not checkpoints:
        # Close the picker cleanly
        subprocess.run([TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Escape"],
                       capture_output=True, text=True, timeout=5)
        return "No checkpoints found in this session."

    # Leave the picker OPEN - user will choose via buttons
    # Build buttons: up to 5 most recent + Cancel
    buttons = []
    for cp in checkpoints[:5]:
        label = cp["label"][:40]  # Telegram button text limit
        buttons.append([{"text": label, "callback_data": f"rewind:{cp['index']}"}])
    buttons.append([{"text": "Cancel", "callback_data": "rewind:cancel"}])

    return {"type": "rewind_picker", "buttons": buttons, "count": len(checkpoints)}


def cmd_save(args=""):
    """Save a compressed context brief of the current session."""
    import datetime as _dt
    label = args.strip() if args.strip() else _dt.datetime.now().strftime("auto-%Y%m%d-%H%M")
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "session-save.py"), label],
            capture_output=True, text=True, timeout=60,
            cwd=REPO_DIR)
        output = r.stdout.strip()
        if r.returncode != 0:
            return f"Save failed: {r.stderr.strip() or output}"
        return output or f"Saved context as '{label}'"
    except subprocess.TimeoutExpired:
        return "Save timed out (60s). Session might be too large."
    except Exception as e:
        return f"Save failed: {e}"


def cmd_restore(args=""):
    """Restore a saved context brief into the CC session."""
    label = args.strip()
    if not label:
        return "Usage: !restore <label>. Use !contexts to see saved contexts."
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "session-restore.py"), label],
            capture_output=True, text=True, timeout=10,
            cwd=REPO_DIR)
        if r.returncode != 0:
            return r.stdout.strip() or r.stderr.strip() or "Restore failed."
        brief = _sanitize_brief(r.stdout.strip())
        if not brief:
            return "Restore returned empty content."
        # Inject wrapped in channel tags so Claude treats it as a Telegram message.
        # brief is pre-sanitized above so nested <channel> tokens cannot
        # forge a new inbound frame with a different user_id.
        inject_msg = (
            '<channel source="plugin:telegram:telegram" chat_id="'
            + str(YOUR_USER_ID)
            + '" message_id="0" user="user" user_id="'
            + str(YOUR_USER_ID)
            + '" ts="">'
            + f"Context restore from saved session '{label}': {brief[:2800]}"
            + '</channel>'
        )
        inject_result = inject_slash_command(inject_msg)
        if inject_result == "sent":
            return f"Restored '{label}' into CC session."
        return f"Restored brief but failed to inject: {inject_result}"
    except Exception as e:
        return f"Restore failed: {e}"


SAVED_CONTEXTS_DIR = os.path.join(REPO_DIR, "saved-contexts")


def _inject_refresh_failure_notice(label, step, save_path, save_result, restore_result, extra=""):
    """Push a diagnostic <channel> block into the post-/reset session so the
    fresh session boots aware that the refresh round-trip failed.

    Added in v3.2.2 after the launchd-PATH-shim ghost: the daemon logged
    "Refresh complete" but no save file ever landed on disk, and the new
    session got the failure stdout pasted back as if it were valid restored
    content. Without this notice, the new session sees nothing actionable.
    """
    def _trim(s, n=300):
        if not s:
            return ""
        s = s.strip()
        return (s[:n] + "...") if len(s) > n else s

    file_exists = os.path.exists(save_path) if save_path else False
    save_rc = save_result.returncode if save_result is not None else None
    save_out = _trim(save_result.stdout) if save_result is not None else ""
    save_err = _trim(save_result.stderr) if save_result is not None else ""
    restore_rc = restore_result.returncode if restore_result is not None else None
    restore_out = _trim(restore_result.stdout) if restore_result is not None else ""
    restore_err = _trim(restore_result.stderr) if restore_result is not None else ""

    body_lines = [
        f"REFRESH FAILED at step '{step}' for label '{label}'.",
        f"Expected save file: {save_path} (exists={file_exists}).",
        f"save: rc={save_rc} stdout={save_out!r} stderr={save_err!r}",
        f"restore: rc={restore_rc} stdout={restore_out!r} stderr={restore_err!r}",
    ]
    if extra:
        body_lines.append(f"extra: {extra}")
    body_lines.extend([
        "Places to look:",
        "  scripts/session-save.py (writes save file)",
        "  scripts/session-restore.py (reads save file)",
        "  scripts/telegram-commander.py cmd_refresh",
        "  commander.log around the failure timestamp",
        "Recommended first step: re-run save manually with this label and inspect filesystem:",
        f"  python3 scripts/session-save.py {label}",
        f"  ls -la {save_path}",
    ])
    msg_body = " ".join(body_lines)
    inject_msg = (
        '<channel source="plugin:telegram:telegram" chat_id="'
        + str(YOUR_USER_ID)
        + '" message_id="0" user="user" user_id="'
        + str(YOUR_USER_ID)
        + '" ts="">'
        + msg_body
        + '</channel>'
    )
    return inject_slash_command(inject_msg)


def cmd_refresh(args=""):
    """Save context, reset CC, restore context. Full refresh in one command.

    v3.2.2 hardening pass after the launchd-PATH-shim ghost:
      - Verify save FILE EXISTS after save subprocess returns 0 (catches the
        case where the script lies about success).
      - Log full stdout/stderr for save and restore at INFO/ERROR.
      - On any post-/reset failure, inject a <channel> diagnostic into the
        new session so the fresh session knows the round-trip failed and
        where to look.
      - Defense-in-depth: if restore stdout matches a known failure string
        ("No saved context found") despite rc=0, treat as failure.
    """
    import datetime as _dt
    label = "refresh-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(SAVED_CONTEXTS_DIR, f"{label}.md")
    save_result = None

    # Step 1: Save
    try:
        save_result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "session-save.py"), label],
            capture_output=True, text=True, timeout=60,
            cwd=REPO_DIR)
        if save_result.returncode != 0:
            logging.error(
                "session-save FAILED label=%s rc=%d stdout=%r stderr=%r",
                label, save_result.returncode,
                save_result.stdout.strip()[:400], save_result.stderr.strip()[:400])
            return f"Refresh aborted: save failed (rc={save_result.returncode}). stderr={save_result.stderr.strip()[:200]}"
        logging.info(
            "session-save OK label=%s stdout=%s",
            label, save_result.stdout.strip()[:200])
    except Exception as e:
        logging.exception("session-save subprocess crashed label=%s", label)
        return f"Refresh aborted: save subprocess crashed. {e}"

    # Step 1b: VERIFY the save file actually landed on disk. Without this
    # check, /reset fires anyway and the new session gets the restore failure
    # stdout pasted in as if it were valid content (the v3.2.2 ghost).
    if not os.path.exists(save_path):
        logging.error(
            "session-save lied: rc=0 but file missing label=%s path=%s stdout=%r stderr=%r",
            label, save_path,
            save_result.stdout.strip()[:400],
            save_result.stderr.strip()[:400])
        return (
            f"Refresh aborted: save returned rc=0 but file is missing at "
            f"{save_path}. Not running /reset - your session is preserved. "
            f"stderr={save_result.stderr.strip()[:400]!r} "
            f"Investigate session-save.py."
        )

    # Step 2: Reset (only after save is verified on disk)
    time.sleep(1)
    result = inject_slash_command("/reset")
    if result != "sent":
        return f"Refresh aborted: reset failed. {result}"

    # Step 3: Wait for fresh prompt
    time.sleep(3)

    # Step 4: Restore
    restore_result = None
    try:
        restore_result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "session-restore.py"), label],
            capture_output=True, text=True, timeout=10,
            cwd=REPO_DIR)
        if restore_result.returncode != 0:
            logging.error(
                "session-restore FAILED label=%s rc=%d stdout=%r stderr=%r",
                label, restore_result.returncode,
                restore_result.stdout.strip()[:400], restore_result.stderr.strip()[:400])
            _inject_refresh_failure_notice(label, "restore", save_path, save_result, restore_result)
            return f"Refresh partial: saved + reset done, but restore failed (rc={restore_result.returncode}). Failure notice injected into new session. Use !restore {label} manually."
        brief = _sanitize_brief(restore_result.stdout.strip())
        if not brief:
            logging.error("session-restore returned empty stdout label=%s", label)
            _inject_refresh_failure_notice(
                label, "restore-empty", save_path, save_result, restore_result,
                extra="restore returned rc=0 but empty stdout")
            return f"Refresh partial: saved + reset done, but restore empty. Failure notice injected. Use !restore {label} manually."
        # Defense-in-depth: if restore stdout looks like a failure string
        # despite rc=0, treat as failure. Prevents the v3.2.2 ghost from
        # re-occurring if returncode propagation ever breaks again.
        # v3.2.3: anchor to startswith, NOT substring. session-restore.py
        # prints these strings as the entire stdout before sys.exit(1), so
        # they always appear at offset 0 on real failure. A successful
        # restore prints the whole save file, which may legitimately quote
        # the failure string in historical commentary - the v3.2.2 substring
        # check fired against a healthy save that mentioned the bug it had
        # just fixed, injecting a false-positive failure notice.
        if brief.startswith("No saved context found") or brief.startswith("Multiple matches:"):
            logging.error(
                "session-restore stdout looks like failure text despite rc=%d label=%s stdout=%r",
                restore_result.returncode, label, brief[:400])
            _inject_refresh_failure_notice(
                label, "restore-mismatch", save_path, save_result, restore_result,
                extra="rc=0 but stdout matches a failure pattern")
            return f"Refresh partial: restore stdout looks like a failure message. Failure notice injected. Use !restore {label} manually."
        inject_msg = (
            '<channel source="plugin:telegram:telegram" chat_id="'
            + str(YOUR_USER_ID)
            + '" message_id="0" user="user" user_id="'
            + str(YOUR_USER_ID)
            + '" ts="">'
            + f"Context restore from refresh '{label}': {brief[:2800]}"
            + '</channel>'
        )
        # pre_clear=False: /reset above already gave us a fresh session with
        # empty input. Skipping the Escape Escape Enter dance avoids a visible
        # blip at the end of the refresh flow and removes the Rewind-dialog
        # edge case (since Escape Escape on an empty input opens it).
        inject_result = inject_slash_command(inject_msg, pre_clear=False)
        if inject_result != "sent":
            logging.error("inject_slash_command failed for refresh restore label=%s result=%s", label, inject_result)
            _inject_refresh_failure_notice(
                label, "inject", save_path, save_result, restore_result,
                extra=f"inject_slash_command returned {inject_result!r}")
            return f"Refresh partial: saved + reset done, inject failed ({inject_result}). Use !restore {label} manually."
    except Exception as e:
        logging.exception("restore subprocess crashed label=%s", label)
        _inject_refresh_failure_notice(
            label, "restore-exception", save_path, save_result, restore_result,
            extra=f"exception={e!r}")
        return f"Refresh partial: saved + reset done, restore error: {e}. Failure notice injected. Use !restore {label} manually."

    return f"Refresh complete ({label}). Saved, reset, restored."


def cmd_contexts(args=""):
    """List all saved session contexts."""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "session-list.py")],
            capture_output=True, text=True, timeout=10,
            cwd=REPO_DIR)
        return r.stdout.strip() or "No saved contexts."
    except Exception as e:
        return f"Failed: {e}"


COMMANDS = {
    "!ping": cmd_ping, "!status": cmd_status, "!stop": cmd_stop,
    "!restart": cmd_restart, "!reset": cmd_restart,
    "!plan": cmd_plan, "!mode": cmd_mode, "!compact": cmd_compact,
    "!clear": cmd_clear, "!model": cmd_model,
    "!opus": cmd_opus, "!sonnet": cmd_sonnet,
    "!effort": cmd_effort, "!health": cmd_health, "!cost": cmd_cost,
    "!context": cmd_context, "!rewind": cmd_rewind,
    "!fast": cmd_fast,
    "!resume": cmd_resume, "!init": cmd_init,
    "!save": cmd_save, "!restore": cmd_restore, "!contexts": cmd_contexts,
    "!refresh": cmd_refresh,
}


# Descriptions for Telegram's in-chat command menu (the "/" picker).
# Telegram requires a leading "/" not "!", so we register the ! aliases
# by stripping the prefix. Stored via setMyCommands.
COMMAND_DESCRIPTIONS = [
    ("ping", "Liveness check"),
    ("context", "Show model + context % used (no turn burned)"),
    ("restart", "Restart Claude Code session"),
    ("health", "System health check"),
    ("cost", "Show session cost"),
    ("rewind", "Roll back to a prior checkpoint"),
    ("fast", "Toggle fast output mode"),
    ("mode", "Cycle permission mode"),
    ("effort", "Pick reasoning effort"),
    ("model", "Switch model"),
    ("opus", "Switch to Opus"),
    ("sonnet", "Switch to Sonnet"),
    ("plan", "Enter plan mode"),
    ("compact", "Compact the conversation"),
    ("clear", "Clear the conversation"),
    ("resume", "Resume a previous conversation"),
    ("init", "Initialize CLAUDE.md"),
    ("refresh", "Save, reset, restore in one shot"),
    ("save", "Save session context with a label"),
    ("restore", "Restore a saved session context"),
    ("contexts", "List saved session contexts"),
    ("status", "Daemon status"),
    ("stop", "Stop the daemon"),
]


def register_bot_commands(token):
    """Register slash-commands in Telegram's UI picker via setMyCommands."""
    try:
        commands = [{"command": c, "description": d} for c, d in COMMAND_DESCRIPTIONS]
        result = telegram_api(token, "setMyCommands", {"commands": commands})
        if result and result.get("ok"):
            logging.info("Registered %d slash-commands in Telegram UI", len(commands))
        else:
            logging.warning("setMyCommands failed: %s", result)
    except Exception as e:
        logging.warning("setMyCommands exception: %s", e)


def handle_signal(signum, frame):
    global running
    logging.info("Received signal %d, shutting down", signum)
    running = False


def main():
    if YOUR_USER_ID == 0:
        sys.stderr.write("ERROR: Set YOUR_USER_ID at the top of telegram-commander.py.\n")
        sys.exit(1)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    logging.info("Telegram Commander starting")
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    # Startup diagnostic: log the Python interpreter and what 'python3' on the
    # daemon's PATH would resolve to. Added in v3.2.2 after the launchd PATH
    # shim ghost: launchd hands daemons a minimal PATH (/usr/bin:/bin:...) so
    # subprocess.run(["python3", ...]) was hitting Apple's Command Line Tools
    # shim, which exits 0 silently in non-interactive context. All subprocess
    # Python calls now use sys.executable explicitly; this log line confirms
    # the daemon's interpreter and surfaces any future PATH drift.
    import shutil as _shutil
    logging.info(
        "Interpreter: sys.executable=%s sys.version=%s shutil.which('python3')=%s PATH=%s",
        sys.executable, sys.version.split()[0],
        _shutil.which("python3"), os.environ.get("PATH", "<unset>"))

    token = get_bot_token()
    register_bot_commands(token)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logging.info("Daemon started, PID %d", os.getpid())
    last_update_id = 0

    try:
        while running:
            result = telegram_api(token, "getUpdates", {
                "offset": last_update_id + 1,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"]
            })
            if result is None or not result.get("ok"):
                logging.warning("API issue, retrying in %ds: %s", RETRY_DELAY, result)
                time.sleep(RETRY_DELAY)
                continue

            for update in result.get("result", []):
                update_id = update["update_id"]
                last_update_id = max(last_update_id, update_id)

                # Handle callback queries (inline button taps)
                cb = update.get("callback_query")
                if cb:
                    cb_id = cb["id"]
                    data = cb.get("data", "")
                    cb_chat_id = cb.get("message", {}).get("chat", {}).get("id", 0)
                    cb_msg_id = cb.get("message", {}).get("message_id", 0)
                    cb_user_id = cb.get("from", {}).get("id", 0)
                    if cb_user_id == YOUR_USER_ID and data.startswith("effort:"):
                        level = data.split(":", 1)[1]
                        tmux_result = inject_slash_command(f"/effort {level}")
                        if tmux_result == "sent":
                            telegram_api(token, "answerCallbackQuery", {
                                "callback_query_id": cb_id,
                                "text": f"Effort set to {level}"
                            })
                            telegram_api(token, "editMessageText", {
                                "chat_id": cb_chat_id,
                                "message_id": cb_msg_id,
                                "text": f"Effort: {level}"
                            })
                        else:
                            telegram_api(token, "answerCallbackQuery", {
                                "callback_query_id": cb_id,
                                "text": f"Failed: {tmux_result}"
                            })
                        logging.info("Callback effort:%s -> %s", level, tmux_result)
                    elif cb_user_id == YOUR_USER_ID and data.startswith("rewind:"):
                        choice = data.split(":", 1)[1]
                        if choice == "cancel":
                            subprocess.run(
                                [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Escape"],
                                capture_output=True, text=True, timeout=5)
                            telegram_api(token, "answerCallbackQuery", {
                                "callback_query_id": cb_id,
                                "text": "Rewind cancelled"
                            })
                            telegram_api(token, "editMessageText", {
                                "chat_id": cb_chat_id,
                                "message_id": cb_msg_id,
                                "text": "Rewind: cancelled"
                            })
                        else:
                            # Picker is already open - navigate to checkpoint
                            try:
                                idx = int(choice)
                            except (ValueError, TypeError):
                                telegram_api(token, "answerCallbackQuery", {
                                    "callback_query_id": cb_id,
                                    "text": "Invalid checkpoint"
                                })
                                logging.warning("Invalid rewind choice: %s", choice)
                                continue
                            for _ in range(idx):
                                subprocess.run(
                                    [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Up"],
                                    capture_output=True, text=True, timeout=5)
                                time.sleep(0.3)
                            subprocess.run(
                                [TMUX_PATH, "send-keys", "-t", TMUX_SESSION, "Enter"],
                                capture_output=True, text=True, timeout=5)
                            telegram_api(token, "answerCallbackQuery", {
                                "callback_query_id": cb_id,
                                "text": f"Rewinding to checkpoint {idx}"
                            })
                            telegram_api(token, "editMessageText", {
                                "chat_id": cb_chat_id,
                                "message_id": cb_msg_id,
                                "text": f"Rewind: restoring checkpoint {idx}."
                            })
                        logging.info("Callback rewind:%s", choice)
                    else:
                        telegram_api(token, "answerCallbackQuery", {"callback_query_id": cb_id})
                    continue

                msg = update.get("message")
                if not msg:
                    continue
                user_id = msg.get("from", {}).get("id")
                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip().lower()
                if user_id != YOUR_USER_ID or (not text.startswith("!") and not text.startswith("/")):
                    continue

                cmd_key = text.split()[0]
                if cmd_key.startswith("/"):
                    cmd_key = "!" + cmd_key[1:].split("@")[0]
                logging.info("Command: %s", cmd_key)
                handler = COMMANDS.get(cmd_key)
                if handler:
                    try:
                        args = text[len(cmd_key):].strip()
                        if inspect.signature(handler).parameters:
                            response = handler(args)
                        else:
                            response = handler()
                    except Exception as e:
                        response = f"Command failed: {e}"
                        logging.error("Command %s failed: %s", cmd_key, e)
                else:
                    available = ", ".join(sorted(COMMANDS.keys()))
                    response = f"Unknown command: {cmd_key}\nAvailable: {available}"

                # None means silently skip (e.g. cooldown guard)
                if response is None:
                    continue
                # Special case: "picker" means send effort inline buttons
                elif response == "picker":
                    try:
                        send_buttons(token, chat_id, "Set effort level:", [
                            [{"text": "Max", "callback_data": "effort:max"},
                             {"text": "High", "callback_data": "effort:high"}],
                            [{"text": "Medium", "callback_data": "effort:medium"},
                             {"text": "Auto", "callback_data": "effort:auto"}]
                        ])
                    except Exception as e:
                        reply(token, chat_id, f"Button send failed: {e}")
                elif isinstance(response, dict) and response.get("type") == "rewind_picker":
                    try:
                        send_buttons(token, chat_id,
                                     f"Rewind to checkpoint ({response['count']} available):",
                                     response["buttons"])
                    except Exception as e:
                        reply(token, chat_id, f"Rewind picker failed: {e}")
                else:
                    reply(token, chat_id, str(response))
                logging.info("Replied to %s: %s", cmd_key, str(response)[:80])
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        logging.info("Telegram Commander stopped")


if __name__ == "__main__":
    main()
