# Reply-Context Patch for the Telegram MCP Plugin

A local patch to `telegram@claude-plugins-official` that surfaces Telegram's
"reply to this message" gesture into Claude's inbound channel payload. Without
this patch, the swipe-reply gesture is invisible to Claude: every inbound
looks like a fresh standalone message.

**Optional.** The base `claude-telegram-remote` setup works fine without it.
Add this only if you use Telegram's reply-to-message feature and want Claude
to see what you're replying to.

## What this patch does

Telegram's Bot API delivers `ctx.message.reply_to_message` on every inbound
that was sent via a right-click / long-press + Reply gesture. Upstream
`server.ts` ignores that field.

This patch surfaces it in two ways:

1. Adds `reply_to_message_id` as a meta attribute on the inbound `<channel>`
   tag. Claude can read it directly from the channel frame.
2. Prepends a quoted excerpt (max 500 chars) of the replied-to body to the
   content text. So even across session resets, Claude sees *what you were
   replying to*, not just that you replied.

## Installation

```bash
python3 apply.py              # apply to the highest installed plugin version
python3 apply.py --check      # report only; exit 0 if patched, 2 if missing
python3 apply.py --dry-run    # print the unified diff without writing
```

After applying, run `/reset` inside Claude Code. The bun MCP child does not
hot-reload `server.ts`, so the patch only takes effect once the plugin
process respawns. **Do not kill the bun process mid-session**; that
permanently disconnects the Telegram MCP channel until the next session
start. `/reset` is the safe way.

## The known rot problem

The Telegram plugin auto-upgrades silently. Any edits to files under
`~/.claude/plugins/cache/claude-plugins-official/telegram/<version>/` are
wiped on the next version bump. Since the bun process keeps old code in
memory until the next restart, a wiped patch can go undetected for days.

This script is idempotent and anchored on a structural line (`const imagePath
= downloadImage ? ...`). Re-run it after each plugin upgrade. If the plugin's
source shape changes meaningfully, the script refuses (missing anchor) rather
than producing a silent partial patch.

Recommended cron, weekly:

```bash
# every Monday 9am, alert if the patch got wiped
0 9 * * 1 python3 ~/claude-telegram-remote/advanced/reply-context-patch/apply.py --check \
    || /path/to/your-alert-script "reply-context patch missing"
```

## Verified versions

The script has been tested against plugin version `0.0.6`. The anchor points
(`const imagePath`, `content: text,`, `image_path: imagePath` spread) are
stable across the 0.0.x line as of this writing.

If a future plugin version rearranges those anchors, the script will fail
loudly. Re-derive the patch by hand against the new `server.ts`, update the
anchors in `apply.py`, and re-run.

## Upstream

The plugin lives at
`~/.claude/plugins/cache/claude-plugins-official/telegram/` and is published
under Apache-2.0 by Anthropic. This patch is a local modification only; no
plugin source is redistributed here.
