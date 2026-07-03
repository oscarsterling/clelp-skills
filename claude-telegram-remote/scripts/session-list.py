#!/usr/bin/env python3
"""List all saved session contexts."""
import os, datetime

SAVE_DIR = os.path.expanduser("~/claude-telegram-remote/saved-contexts")


def main():
    if not os.path.exists(SAVE_DIR):
        print("No saved contexts.")
        return

    files = sorted(
        [f for f in os.listdir(SAVE_DIR) if f.endswith(".md")],
        key=lambda f: os.path.getmtime(os.path.join(SAVE_DIR, f)),
        reverse=True
    )

    if not files:
        print("No saved contexts.")
        return

    lines = []
    for f in files:
        path = os.path.join(SAVE_DIR, f)
        label = f.replace(".md", "")
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        size = os.path.getsize(path)

        # Read first content line after header for preview
        preview = ""
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("**"):
                    preview = line[:80]
                    break

        lines.append(f"  {label} | {mtime.strftime('%b %d %H:%M')} | {size}b | {preview}")

    print(f"Saved contexts ({len(files)}):")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
