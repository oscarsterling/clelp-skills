#!/usr/bin/env python3
"""Restore a saved session context brief.

v3.2.2: dropped the partial-substring fallback. The previous version used
`if label in f` against every file in SAVE_DIR, which would silently load
the wrong file if the caller passed a truncated label or one that
incidentally matched another file. For !refresh the timestamp label made
collision unlikely but the foot-gun was real. Strict exact-match only now:
either the file exists, or we fail loudly. Use `!contexts` (session-list.py)
to discover available labels.
"""
import os, re, sys

SAVE_DIR = os.path.expanduser("~/claude-telegram-remote/saved-contexts")

# Restrict labels to plain alnum + underscore + hyphen so a caller cannot
# pass `../somewhere` or `.hidden` and reach outside SAVE_DIR.
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def main():
    if len(sys.argv) < 2:
        print("Usage: session-restore.py <label>")
        sys.exit(1)

    label = sys.argv[1].strip().replace(" ", "-").lower()
    if not LABEL_RE.match(label):
        print(f"ERROR: invalid label {label!r}. Use [a-z0-9_-] only, no path separators.")
        sys.exit(2)

    save_path = os.path.join(SAVE_DIR, f"{label}.md")
    if not os.path.exists(save_path):
        print(f"No saved context found for '{label}' (use !contexts to list available labels)")
        sys.exit(1)

    with open(save_path) as f:
        content = f.read()

    # Output the brief for injection
    print(content)


if __name__ == "__main__":
    main()
