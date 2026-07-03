# claude-telegram-remote

**v3.5.0** (May 15, 2026). Control Claude Code from your phone via Telegram. 23 commands, interactive checkpoint rollback, session save/restore/refresh, a typing indicator, a hardened deterministic Stop-hook, and inline button pickers.

![Hero](assets/hero.png)

## What This Is

A set of scripts and configurations that give you full remote control of Claude Code through Telegram. Built by someone who manages BPO operations for a living, not an engineer.

**[Read the full story on Clelp.ai](https://clelp.ai/blog/claude-telegram-remote-control)**

## Version History

### v3.5

- **Stop hook hardened against pre-reply and between-reply terminal leaks.** The deterministic Stop hook from v2 caught two failure modes (no reply at all, trailing text after the last reply) but it never noticed when the agent wrote a status sentence BEFORE the first reply tool call or BETWEEN two reply calls. That "On it, let me read the file..." narration is invisible to you on Telegram, and as long as the agent eventually called the reply tool, the hook passed. v3.5 closes that gap: every text content block in a Telegram-triggered turn must be a normalized substring of some reply or edit_message payload, or the hook blocks with the leaked snippet quoted back to the model. Normalization only touches line endings and outer whitespace so real mismatches still get caught.
- **Errored-delivery detection.** Same hook: previously it counted a reply tool CALL, not a successful tool_result. If the reply tool returned an error (`is_error: true`, or content starting with "Error:"), the hook used to pass even though nothing reached your phone. v3.5 checks the matching tool_result and blocks on error with the failure excerpt. Conservative heuristic by design (false negatives tolerable, false-positive blocks on a successful reply that happens to mention "error" are not).
- **Layer 2 rewake-counter safety net** carried forward from the May 13 hot patch. If the hook keeps blocking without convergence, force-release at 4 consecutive blocks within 60 seconds and append a JSONL event to the loop-event log. Prevents loop-death scenarios.
- **Portable state paths.** State (rewake counter, loop event log, debug logs) lives under `~/claude-telegram-remote/state/` by default. Override individually via `TG_HOOK_REWAKE_COUNTER_PATH`, `TG_HOOK_LOOP_EVENT_LOG_PATH`, `TG_HOOK_DEBUG_LOG_DIR`.
- **25 tests** in `hooks/test-check-tg-reply-completeness.py` including the four block-condition stderr-distinctness invariants, pre-reply leak detection, edit_message-payload leak coverage, CRLF normalization, errored-delivery detection, and the Layer 2 force-release at N=4.
- Design locked via GPT-5.5 model bounce: block over auto-relay, suppress pre-tool announcements rather than duplicate, normalize only line endings + outer whitespace.

### v3.4

- **Optional refuse-launch patch for the Telegram MCP plugin.** Under `advanced/refuse-launch-patch/`, an idempotent script that patches `telegram@claude-plugins-official`'s `server.ts` to refuse launching when its parent chain belongs to Claude Desktop. Mitigates a thrash bug where Desktop's agent-mode sub-claudes (Computer Use, Skills, ccd_session, Claude in Chrome, etc.) inherit the global plugin cache, spawn their own `bun server.ts`, and SIGTERM the bun owned by your legitimate Code session. Symptoms without this patch: Telegram bot suddenly stops replying mid-session, multiple bun processes cycling every few minutes, extended Telegram silence followed by self-recovery once Desktop sub-claudes shut down. The Customize panel toggle is not a durable lever (Desktop has been observed re-enabling it across restarts even when `~/.claude/settings.json` correctly reads `enabledPlugins: false`). Ships with `--check` and `--dry-run` modes; self-contained so it stacks cleanly with `reply-context-patch` or runs on its own. Root cause documented at [anthropics/claude-code#43645](https://github.com/anthropics/claude-code/issues/43645).

### v3.3

- **`!context` no longer mis-parses when the pane holds a prior model-name match.** Previously, any earlier mention of `Opus N.N` / `Sonnet N.N` / `Haiku N.N` above CC's live status block (an earlier script echo, a prose mention in the conversation, a previous `!context` reply still in scrollback) would win the parser's first-match scan, and the parser would then read the line directly below it as the percentage row. The auto-mode subline produced the message `context line not parseable: ...`. The parser now scans bottom-up so the LAST match wins; the live status line is always last.
- **`inject_slash_command` pre-clears the CC input box before sending.** Adds an Escape, Escape, Enter sequence (with 0.4s and 0.5s gaps) in front of every slash command to drop CC out of any open picker or popup, including the Rewind dialog that opens when the input is empty and Escape Escape lands there. Slash text and Enter are now sent as two separate `send-keys` calls with a 0.5s gap, using `tmux send-keys -l` for literal-text mode so tmux cannot parse tokens in the payload as key names. Callers can opt out via `pre_clear=False` (used in `cmd_refresh`'s post-`/reset` restore inject).

### v3.2

- **Full-fidelity session save/restore.** `session-save.py` no longer slices individual messages at character caps, uses `git log --since=<session-start>` instead of heredoc-breaking regex for commit extraction, and leads the output with the last full exchange so fresh sessions pick up exactly where the previous one left off. Restore payloads from prior saves are filtered out of the parse to stop recursive echo across save/restore cycles.
- **Optional reply-context patch for the Telegram MCP plugin.** Under `advanced/reply-context-patch/`, an idempotent script that patches `telegram@claude-plugins-official`'s `server.ts` to surface Telegram's "reply to this message" gesture to Claude. Without the patch, the swipe-reply is invisible. Ships with `--check` and `--dry-run` modes for safe re-runs after plugin auto-upgrades.

### v3.1

- **`!refresh` command.** Save, reset, restore in one shot. Captures context, resets the session, and injects the brief into the fresh session automatically. If any step fails, tells you exactly where it stopped and how to recover manually.
- **Channel-tag injection for restore/refresh.** `!restore` and `!refresh` now wrap the injected brief in Telegram channel tags so Claude treats it as a real Telegram message and responds in Telegram, not the terminal.

### v3.0

- **Interactive /rewind with Telegram buttons.** Opens the Claude Code checkpoint picker, parses it from tmux, and sends tappable buttons. Pick a checkpoint or cancel, all from your phone. Cooldown guard prevents duplicate execution.
- **Session save/restore.** `!save` captures a compressed brief of what you were working on (exchanges, files changed, commits, where you left off). `!restore` injects a saved brief into a fresh session. `!contexts` lists everything you have saved.
- **Seven new commands.** `!rewind`, `!save`, `!restore`, `!contexts`, `!fast`, `!resume`, `!init`.
- **Removed terminal-only commands.** `!review`, `!doctor`, and `!memory` produced output that only made sense in a terminal, not in Telegram.
- **Fixed dict-slice crash.** The logging line now handles dict responses without raising TypeError (this was causing a launchd respawn loop).

### v2.0

- **Typing indicator pinger.** Telegram now shows "Claude is typing..." the entire time he is working, just like a real chat. Spawns on inbound, dies on reply, hard 10-min ceiling.
- **Deterministic Stop hook.** Replaces the old LLM-judge with a Python script that walks the actual transcript. Catches both "missing TG reply" AND "trailing terminal text after the reply" (the silent killer).
- **Five new commands.** `!ping`, `!reset`, `!effort`, `!health`, `!cost`.
- **Inline button picker.** `!effort` with no argument pops up a Max/High/Medium/Auto button picker via callback queries.
- **Optional health check hook.** Wire your own health-check script into `!health`.

## The Six Pieces

| # | Piece | What It Does |
|---|-------|-------------|
| 1 | **Conversation Layer** | Anthropic's Telegram MCP plugin. Claude receives and sends messages via Telegram. |
| 2 | **Message Cache** | Hooks that log all messages per chat. Gives Claude thread context across sessions. |
| 3 | **Command Daemon** | Background service that watches for `!commands` and injects them into Claude Code's tmux session. Handles inline-button callbacks for effort and rewind pickers. |
| 4 | **Stop Hook** | Deterministic Python check. Blocks if Claude got a TG message and didn't reply, OR if he wrote terminal text after the final reply. |
| 5 | **Typing Pinger** | Spawns a `sendChatAction(typing)` loop on inbound, killed on reply. Single-instance per chat. |
| 6 | **Proactive Messaging** | Shell scripts for cron notifications and interactive inline keyboard buttons. |

## Commands

| Command | What It Does |
|---------|-------------|
| `!ping` | Health check, replies "Pong" |
| `!status` | What Claude is working on right now (PID + uptime) |
| `!stop` | Send SIGINT to interrupt the current task |
| `!plan` | Switch to plan mode before acting |
| `!restart` / `!reset` | Restart the Claude Code session (requires `RESTART_SCRIPT` config, supports wake-ping, see Advanced) |
| `!mode` | Cycle permission modes (Shift+Tab) and report the current one |
| `!opus` | Switch to Opus (1M context) |
| `!sonnet` | Switch to Sonnet (faster) |
| `!model [name]` | Show current model, or switch to a specific one |
| `!clear` | Clear conversation context |
| `!compact` | Compact the conversation |
| `!cost` | Show current session cost |
| `!effort [max\|high\|medium\|auto]` | Set thinking effort level (no arg = button picker) |
| `!health` | Run your custom health check script (requires `HEALTH_SCRIPT` config) |
| `!context` | Show model + context % used (no Claude turn burned) |
| `!rewind` | Roll back to a prior checkpoint (interactive button picker) |
| `!fast` | Toggle fast output mode (same model, faster output) |
| `!resume [query]` | Resume a previous conversation |
| `!init` | Initialize CLAUDE.md for current project |
| `!refresh` | Save context, reset session, restore context in one shot |
| `!save [label]` | Save a compressed context brief of the current session |
| `!restore <label>` | Restore a saved session context into Claude Code |
| `!contexts` | List all saved session context briefs |

Both `!command` and `/command` syntax work, since some Telegram clients auto-complete `/`.

## Prerequisites

- macOS (launchd for the daemon; Linux users swap for systemd)
- Claude Code installed and running in a tmux session
- Two Telegram bots (one for conversation, one for commands)
- tmux
- Python 3.10+

## Setup

### Step 1: Create Two Telegram Bots

You need two bots because the command daemon polls for messages, and you don't want that polling noise in your main conversation.

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Create bot #1: your conversation bot (e.g., "MyClaudeBot")
3. Create bot #2: your command bot (e.g., "MyClaudeCommander")
4. Save both tokens

### Step 2: Store Tokens

On macOS, store the command bot token in Keychain:

```bash
security add-generic-password -a claude-code -s telegram-commander-bot-token -w "YOUR_COMMAND_BOT_TOKEN"
```

Store the conversation bot token where the typing pinger can read it:

```bash
mkdir -p ~/.claude/channels/telegram
echo 'TELEGRAM_BOT_TOKEN=YOUR_CONVERSATION_BOT_TOKEN' > ~/.claude/channels/telegram/.env
chmod 600 ~/.claude/channels/telegram/.env
```

The conversation bot token is also configured in Claude Code's Telegram MCP plugin settings.

### Step 3: Get Your Telegram User ID

Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram. It will reply with your user ID. You'll need this to restrict commands to only you.

### Step 4: Install the Telegram MCP Plugin

In Claude Code, enable the Telegram plugin:

```json
{
  "enabledPlugins": {
    "telegram@claude-plugins-official": true
  }
}
```

### Step 5: Configure the Command Daemon

Edit `scripts/telegram-commander.py` and set the values in the `=== CONFIGURE THESE ===` block:

- `YOUR_USER_ID` - your Telegram user ID
- `TMUX_SESSION` - your tmux session name (default: `claude`)
- `TMUX_PATH` - output of `which tmux`
- `RESTART_SCRIPT` - optional, absolute path to your restart script (or leave `""` to disable `!restart`)
- `HEALTH_SCRIPT` - optional, absolute path to your health check script (or leave `""` to disable `!health`)
- `REPO_DIR` - where you cloned this repo (default: `~/claude-telegram-remote`)

### Step 6: Install the Hooks

Copy this repo to `~/claude-telegram-remote/` (the hook scripts assume that path):

```bash
git clone https://github.com/oscarsterling/claude-telegram-remote ~/claude-telegram-remote
chmod +x ~/claude-telegram-remote/hooks/*.sh
chmod +x ~/claude-telegram-remote/hooks/*.py
```

Then add the hook configuration to your Claude Code `settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/claude-telegram-remote/hooks/cache-telegram-inbound.sh",
            "timeout": 10,
            "async": true
          },
          {
            "type": "command",
            "command": "bash ~/claude-telegram-remote/hooks/start-typing-pinger.sh",
            "timeout": 5,
            "async": true
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__plugin_telegram_telegram__reply",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/claude-telegram-remote/hooks/cache-telegram-outbound.sh",
            "timeout": 10,
            "async": true
          },
          {
            "type": "command",
            "command": "bash ~/claude-telegram-remote/hooks/stop-typing-pinger.sh",
            "timeout": 5,
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/claude-telegram-remote/hooks/check-tg-reply-completeness.py",
            "timeout": 10
          },
          {
            "type": "command",
            "command": "bash ~/claude-telegram-remote/hooks/stop-typing-pinger.sh",
            "timeout": 5,
            "async": true
          }
        ]
      }
    ]
  }
}
```

### Step 7: Start the Daemon

```bash
# Test it first
python3 ~/claude-telegram-remote/scripts/telegram-commander.py

# Run as a launchd service (macOS)
cp ~/claude-telegram-remote/services/com.claude.telegram-commander.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.claude.telegram-commander.plist
```

### Step 8: Start Claude Code in tmux

```bash
tmux new-session -d -s claude
tmux send-keys -t claude 'claude' Enter
```

Send `!ping` from your command bot. If you get `Pong`, you're live.

## File Structure

```
claude-telegram-remote/
  scripts/
    telegram-commander.py            # Command daemon (23 commands + button callbacks)
    session-save.py                  # Save session context briefs
    session-restore.py               # Restore saved context briefs
    session-list.py                  # List saved context briefs
    tg-send.sh                       # Proactive plain text messaging
    tg-buttons.sh                    # Proactive inline keyboard buttons
  hooks/
    cache-telegram-inbound.sh        # Message cache (inbound)
    cache-telegram-outbound.sh       # Message cache (outbound)
    start-typing-pinger.sh           # Spawns the typing pinger on inbound TG
    stop-typing-pinger.sh            # Kills any running typing pingers
    typing-indicator-pinger.py       # Loops sendChatAction(typing) until killed
    check-tg-reply-completeness.py   # Deterministic Stop hook
  services/
    com.claude.telegram-commander.plist  # macOS launchd config
  saved-contexts/                    # Session context briefs (created by !save)
  advanced/
    reply-context-patch/             # Optional patch for the Telegram MCP plugin
      apply.py                       # Idempotent patch applier with --check / --dry-run
      README.md                      # What it does, how to install, rot guard
  assets/
    hero.png                         # Project hero image
```

## How It Works

**Command daemon.** A Python process long-polls the Telegram Bot API for messages from your command bot. When it sees a message starting with `!` or `/`, it maps it to an action:
- Slash commands (`!plan`, `!clear`, `!compact`, `!cost`): injected as keystrokes into tmux via `tmux send-keys`
- Raw keys (`!mode`): sent as special key names (e.g., `BTab` for Shift+Tab)
- Process control (`!stop`, `!restart`): direct signal/subprocess calls
- Inline pickers (`!effort` with no arg, `!rewind`): sends inline keyboard buttons; the daemon handles the callback_query taps
- Session management (`!save`, `!restore`, `!contexts`): runs helper scripts that parse and store session JSONL data

**Message cache hooks.** Fire on every prompt submission and every Telegram reply, logging both sides of the conversation to JSONL files organized by chat ID.

**Typing pinger.** When a Telegram message arrives, `start-typing-pinger.sh` extracts the chat_id from the inbound channel tag and spawns `typing-indicator-pinger.py` as a detached process. The pinger loops `sendChatAction(typing)` every 4 seconds (Telegram clears typing after ~5s, so this keeps it lit). It dies in three ways: PostToolUse on the reply tool, the Stop hook backstop, or the hard 10-minute ceiling.

**Stop hook.** A deterministic Python script that walks the JSONL transcript backwards to the most recent real user prompt, then checks four conditions:
1. If a Telegram channel tag was in the prompt and no `mcp__plugin_telegram_telegram__reply` tool was called, BLOCK with "missing TG reply"
2. If a reply was called AND there is text either after it in the transcript OR in the in-flight `last_assistant_message` payload, BLOCK with "trailing terminal text after TG reply"
3. (v3.5) If any text content block in the turn is NOT a normalized substring of some reply or edit_message payload, BLOCK with "invisible terminal text" and the leaked snippet quoted in stderr. Catches the pre-reply and between-reply leaks v2 missed
4. (v3.5) If the reply tool was called but its `tool_result` came back as an error (`is_error: true`, or content starting with "Error:"), BLOCK with the failure excerpt. Tool-use existence is not the same as successful delivery

The trailing-text check uses both the persisted transcript AND the stdin payload because the Stop hook fires before the final assistant text is flushed to JSONL. Without that cross-check, trailing text after the final reply slips through invisible.

**Session save/restore/refresh.** `!save` runs `session-save.py`, which reads the tail of the active session JSONL, extracts Telegram replies, user requests, file modifications, and git commits, then compresses them into a structured markdown brief. `!restore` reads that brief back and injects it into the tmux session wrapped in Telegram channel tags, so Claude treats it as a real message and responds in Telegram. `!refresh` chains save, `/reset`, and restore into a single command for mid-session resets without losing context.

## Customization

- **Add commands**: Edit the `COMMANDS` dict in `telegram-commander.py`
- **Change tmux session name**: Edit `TMUX_SESSION` in the config block
- **Linux**: Replace the launchd plist with a systemd service file
- **Multiple users**: Add user IDs to an allowlist in the commander
- **Custom button pickers**: Add new `callback_data` prefixes in the `callback_query` handler
- **Session save location**: Edit `SAVE_DIR` in `session-save.py` and `session-restore.py`

## Advanced: Wake-Ping After `!restart`

When you `!restart` from your phone, tmux comes back fast but Claude takes a beat to fully reset. The daemon tells you "restarting, give it 30 seconds" but you have no proof the new session is actually awake before you send your next message.

Fix: have the new Claude announce itself in Telegram once `/reset` has processed.

Pattern:

1. `cmd_restart()` writes two files: the restart trigger **and** a "manual flag" file (e.g. `restart-manual-flag`).
2. Your restart script runs its normal steps, verifies the new session is ready, then checks for the manual flag.
3. If the flag exists, sleep ~10 seconds (buffer so `/reset` finishes), then `tmux send-keys` a short wake prompt. Example:
   ```bash
   WAKE_PROMPT='You just rebooted via manual /reset. Reply in DM: "Awake. Session ready." with current time. Nothing else.'
   tmux send-keys -t "$TMUX_SESSION" "$WAKE_PROMPT" Enter
   ```
4. Delete the flag.

**Customize the ping.** The wake prompt is just a string in your restart script, so you can make it anything: have Claude report system health, read the last few inbox items, sanity-check a cron, or just say hi with a specific tone. Keep it short (one to two sentences) so the first turn doesn't eat context.

Nightly cron restarts never create the flag, so scheduled resets stay silent. Only `!restart` (manual) triggers the ping.

See `cmd_restart()` in `scripts/telegram-commander.py` for the skeleton.

## Security Model

Read this once before you point a Telegram account at a machine running Claude Code in bypass mode.

**Trust boundary.** The allowlisted Telegram user ID is the ONLY authentication between "someone sending a message" and "Claude Code executing code on your machine." There is no second factor. A compromised Telegram account, a stolen session token, or an attacker who sits down at an unlocked phone has the same authority as you do.

**Blast radius when the allowlist is breached.** The command daemon passes `!command` arguments as keystrokes into the tmux session. If Claude Code is running with `--dangerously-skip-permissions`, those keystrokes become unrestricted code execution: file reads, writes, network calls, commits, secret exfiltration. `!restart` can spawn arbitrary processes via the restart script. `!resume` can attach to any prior conversation. `!restore` can inject content wrapped in a `<channel>` frame that Claude treats as a real Telegram message.

**What the project does to reduce that risk.**
- Allowlist is exact-match integer compare (`user_id == YOUR_USER_ID`), not a pattern.
- `!save` and `!restore` labels are validated as `[a-z0-9_-]+` before they reach the filesystem.
- Restored briefs are sanitized before re-injection: control chars stripped, literal `<channel` tokens neutralized, so a hostile inbound message cannot plant a payload that forges a different-user frame on the next `!refresh`.
- Keychain holds both bot tokens (not environment vars or config files).
- `saved-contexts/` is in `.gitignore` so a careless `git add .` on a live install cannot push session briefs.

**What you can do to harden further.**
- Use a dedicated Telegram account for the command bot instead of your personal one.
- Enable Telegram's two-step verification and lock your phone screen.
- Set `ALLOWED_USERS` (if you fork to multi-user) as a tiny list, not a wildcard.
- Audit `saved-contexts/` before sharing your machine; briefs contain conversation content and file paths.
- Consider running Claude Code without `--dangerously-skip-permissions` if your workflow permits; the daemon still works, it just makes each tool call prompt you in the tmux pane.

**What the project does NOT protect against.**
- Prompt injection through normal Telegram messages is a product-level concern of Claude Code itself; the daemon just relays text.
- A physically present attacker with access to the tmux session has the same authority as `!commands`.
- Malicious plugins installed in `~/.claude/plugins/` are not sandboxed by this project.

## Credits

Built by [Oscar Sterling](https://github.com/oscarsterling) (AI Chief of Staff) for [Jason Haugh](https://x.com/jason_haugh).

Story: [How I Control Claude Code From My Phone](https://clelp.ai/blog/claude-telegram-remote-control)

## License

MIT
