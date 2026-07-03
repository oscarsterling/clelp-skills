#!/usr/bin/env python3
"""Apply the reply-context patch to the Telegram MCP plugin's server.ts.

Why this patch exists
---------------------
Telegram's Bot API delivers ``ctx.message.reply_to_message`` whenever a user
long-presses or right-clicks a prior message and hits Reply. Upstream
``server.ts`` (the ``telegram@claude-plugins-official`` plugin) ignores that
field — which means the swipe-reply gesture looks to Claude like a fresh
standalone message with no context.

This patch surfaces the reply target in two ways:

1. Adds ``reply_to_message_id`` as a meta attribute on the inbound
   ``<channel>`` tag.
2. Prepends a quoted excerpt (max 500 chars) of the replied-to body to the
   content text so Claude sees what's being responded to even across session
   resets.

Plugin auto-upgrades will wipe this patch silently. Re-run this script after
each upgrade — it's idempotent (sentinel check skips already-patched files).

Usage
-----
    python3 apply.py             # apply to the highest installed plugin version
    python3 apply.py --check     # report only, do not modify
    python3 apply.py --dry-run   # print the diff, do not write

After applying you MUST ``/reset`` Claude Code so the bun MCP child reloads
``server.ts``. Killing bun mid-session permanently disconnects the MCP
channel (no hot-reload support).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path.home() / ".claude/plugins/cache/claude-plugins-official/telegram"
SENTINEL = "LOCAL PATCH (not upstream): reply-context injection"

# Anchor: the line right before where the patch block gets inserted. Stable
# across plugin versions because it's a natural boundary between upstream
# image handling and the notification emission.
INSERT_ANCHOR = "const imagePath = downloadImage ? await downloadImage() : undefined"

PATCH_BLOCK = '''\

  // LOCAL PATCH (not upstream): reply-context injection. When the user
  // right-clicks a prior message and replies, Telegram's Bot API gives us
  // the full replied-to message in ctx.message.reply_to_message. Without
  // this, that "swipe-reply" gesture is invisible to Claude — the inbound
  // looks like a fresh standalone message. The patch surfaces the reply
  // target's id as a meta attribute AND prepends a short quote of the
  // replied-to body to content so context survives session resets.
  const replyTo = ctx.message?.reply_to_message
  const replyToText = replyTo?.text ?? replyTo?.caption ?? ''
  const replyToId = replyTo?.message_id
  const replyToUser = safeName(replyTo?.from?.username) ?? (replyTo?.from?.id != null ? String(replyTo.from.id) : '?')
  const finalText = replyTo && replyToText
    ? `[Replying to message ${replyToId} from @${replyToUser}: ${replyToText.slice(0, 500)}${replyToText.length > 500 ? '…' : ''}]\\n\\n${text}`
    : text
'''

# Replace ``content: text,`` with ``content: finalText,`` inside the
# ``mcp.notification`` call. There is only one occurrence in server.ts; we
# still anchor on ``params: {`` on a nearby preceding line to be safe.
CONTENT_LINE_OLD = "      content: text,"
CONTENT_LINE_NEW = "      content: finalText,"

# Insert the reply_to_message_id spread after the image_path spread in meta.
META_ANCHOR = "        ...(imagePath ? { image_path: imagePath } : {}),"
META_INSERT = "        ...(replyToId != null ? { reply_to_message_id: String(replyToId) } : {}),"


def resolve_latest_plugin_dir() -> Path:
    """Highest installed plugin version directory under PLUGIN_ROOT.

    ``sort -V`` equivalent via parsing dotted version strings. Falls back to
    lexicographic sort when parsing fails (early 0.0.x versions behave the
    same under both).
    """
    if not PLUGIN_ROOT.is_dir():
        sys.exit(f"ERROR: plugin not installed at {PLUGIN_ROOT}")
    versions = [d for d in PLUGIN_ROOT.iterdir() if d.is_dir()]
    if not versions:
        sys.exit(f"ERROR: no plugin versions under {PLUGIN_ROOT}")

    def version_key(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)

    return max(versions, key=version_key)


def apply_patch(src: str) -> str:
    if SENTINEL in src:
        return src  # already patched, no-op

    if INSERT_ANCHOR not in src:
        sys.exit(
            f"ERROR: insert anchor not found: {INSERT_ANCHOR!r}\n"
            "Plugin source shape has changed upstream. Re-derive the patch "
            "manually and update this script."
        )

    # Insert the patch block on the line AFTER the anchor line.
    lines = src.splitlines(keepends=True)
    out: list[str] = []
    inserted_block = replaced_content = inserted_meta = False
    for line in lines:
        out.append(line)
        if not inserted_block and line.rstrip("\n").endswith(INSERT_ANCHOR):
            out.append(PATCH_BLOCK)
            inserted_block = True

    patched = "".join(out)

    if not inserted_block:
        sys.exit("ERROR: failed to insert patch block (anchor matched but insert skipped).")

    # Swap ``content: text,`` -> ``content: finalText,``. There should be
    # exactly one match; refuse if there are multiple (ambiguous).
    occurrences = patched.count(CONTENT_LINE_OLD)
    if occurrences == 0:
        sys.exit(f"ERROR: content line not found: {CONTENT_LINE_OLD!r}")
    if occurrences > 1:
        sys.exit(f"ERROR: content line appears {occurrences} times; ambiguous replacement.")
    patched = patched.replace(CONTENT_LINE_OLD, CONTENT_LINE_NEW)
    replaced_content = True

    # Insert reply_to_message_id meta line after image_path spread.
    if META_ANCHOR not in patched:
        sys.exit(f"ERROR: meta anchor not found: {META_ANCHOR!r}")
    patched = patched.replace(META_ANCHOR, META_ANCHOR + "\n" + META_INSERT, 1)
    inserted_meta = True

    if not (inserted_block and replaced_content and inserted_meta):
        sys.exit("ERROR: patch produced incomplete output; aborting.")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reply-context patch.")
    parser.add_argument("--check", action="store_true", help="report only; do not modify")
    parser.add_argument("--dry-run", action="store_true", help="print diff without writing")
    args = parser.parse_args()

    plugin_dir = resolve_latest_plugin_dir()
    target = plugin_dir / "server.ts"
    if not target.is_file():
        sys.exit(f"ERROR: server.ts missing at {target}")

    original = target.read_text()

    if SENTINEL in original:
        print(f"OK: reply-context patch already present in {plugin_dir.name}")
        return 0

    if args.check:
        print(f"MISSING: reply-context patch absent in {plugin_dir.name}")
        return 2

    patched = apply_patch(original)

    if args.dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"{target} (upstream)",
            tofile=f"{target} (patched)",
        )
        sys.stdout.writelines(diff)
        print(f"\n[dry-run] would patch {target}")
        return 0

    target.write_text(patched)
    print(f"APPLIED: reply-context patch written to {target}")
    print("NEXT STEP: run /reset in Claude Code so bun reloads server.ts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
