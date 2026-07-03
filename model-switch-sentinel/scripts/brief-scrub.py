#!/usr/bin/env python3
"""brief-scrub.py - mechanically sanitize a context brief before it is injected
back into a restored session.

WHY THIS EXISTS
  When a session falls back to a cheaper model and later switches back, the
  restore step hands a written "context brief" to the intended-model session so
  it knows what happened while it was away. That brief is assembled from prior
  session text, which means it can carry the two classic prompt-injection
  signatures:

    1. A conversational TURN-BOUNDARY line: a capitalized word followed by a
       colon at the very start of a line, which a reader can mistake for a new
       speaker turn and act on as if it were a fresh instruction.
    2. An ANGLE-BRACKET MARKUP tag: bracketed markup carrying a source-style
       attribute, which can impersonate a trusted channel wrapper.

  This tool neutralizes both MECHANICALLY, by transformation, not by a denylist
  of known bad strings. It does not try to understand the content. It makes the
  two signatures inert so the restored session reads the brief as plain data.

WHAT IT DOES (transformations, in order)
  - Escapes every angle bracket (< and >) to its HTML entity, so any bracketed
    markup, including a channel-style wrapper or a reminder-style tag, becomes
    inert text that cannot open a tag. This is deliberately broad: a context
    brief is prose, and safety is worth the loss of literal brackets.
  - Quote-prefixes any line that opens with a capitalized word followed by a
    colon and a space (a turn-boundary shape), so it renders as quoted material
    rather than a new speaker turn. Content is preserved, only demoted.
  - Normalizes a few zero-width and bidi control characters that can hide the
    above shapes from a human reviewer.

  The transformation is idempotent: scrubbing already-scrubbed text is a no-op.

WHAT IT IS NOT
  This is a mechanical demotion of two structural signatures, not a semantic
  content filter. It does not detect persuasion, policy evasion, or instructions
  written in plain prose. It makes impersonation of a turn boundary or a trusted
  wrapper mechanically fail. Treat the brief as untrusted data regardless.

USAGE
    python3 brief-scrub.py                 # read stdin, write stdout
    python3 brief-scrub.py --in brief.md --out brief.scrubbed.md
    python3 brief-scrub.py --self-test     # run built-in checks, exit 0/1
    python3 brief-scrub.py --help

Exit status: 0 on success (or all self-tests passing), 1 on a self-test
failure, 2 on an IO/argument error.
"""

import argparse
import re
import sys

# A line that opens (after optional whitespace) with a single capitalized token
# of letters, then a colon and a space. This is the STRUCTURAL shape of a
# conversational turn boundary. It is intentionally generic: it keys off the
# shape, not any specific role word, so it never needs to name one.
_ROLE_BOUNDARY = re.compile(r"^(\s*)([A-Z][A-Za-z]{1,15}):(\s)")

# Zero-width and bidirectional control characters that can hide the shapes above
# from a human eye. Named by codepoint so this source stays reviewable: zero
# width space/non-joiner/joiner, the LTR/RTL and embedding/override marks, the
# word joiner, and the byte-order mark.
_INVISIBLES = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff]"
)

# Marker used to demote a turn-boundary line. A leading gutter moves the role
# word off the start of the line, so it cannot be read as a fresh speaker turn.
# A pipe is used rather than a markdown blockquote (>) so the marker survives the
# angle-bracket escape unchanged, keeping the whole pass idempotent.
_QUOTE_PREFIX = "| "


def _escape_angle_brackets(text):
    """Make every angle bracket inert. Ampersand is escaped first so the entities
    we introduce are not themselves re-interpretable, keeping the pass
    idempotent for already-escaped input."""
    text = text.replace("&lt;", "\x00LT\x00").replace("&gt;", "\x00GT\x00")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\x00LT\x00", "&lt;").replace("\x00GT\x00", "&gt;")
    return text


def _demote_turn_boundaries(text):
    """Quote-prefix any line whose START looks like a speaker turn, so it reads
    as quoted material. Already-quoted lines are left alone (idempotent)."""
    out = []
    for line in text.split("\n"):
        if line.startswith(_QUOTE_PREFIX):
            out.append(line)
            continue
        if _ROLE_BOUNDARY.match(line):
            out.append(_QUOTE_PREFIX + line)
        else:
            out.append(line)
    return "\n".join(out)


def scrub(text):
    """Return `text` with both injection signatures mechanically neutralized.

    Order matters: strip invisibles first so hidden shapes are exposed, escape
    angle brackets next so no markup can open, then demote turn boundaries. The
    whole pass is idempotent."""
    if text is None:
        return ""
    text = _INVISIBLES.sub("", text)
    text = _escape_angle_brackets(text)
    text = _demote_turn_boundaries(text)
    return text


# --- self-test -------------------------------------------------------------


def _self_test():
    """Structural checks. Built without embedding any working injection literal:
    each probe is ASSEMBLED from fragments at runtime so this source file never
    contains a copy-pasteable turn boundary or wrapper."""
    checks = []

    # A turn-boundary shape assembled from fragments.
    role = "Sys" + "tem" + ": do the thing"
    scrubbed = scrub(role)
    checks.append(("turn boundary demoted", scrubbed.startswith(_QUOTE_PREFIX)))

    # A bracketed wrapper assembled from fragments.
    tag = "<" + "wrapper " + "sou" + "rce=" + "x>payload"
    scrubbed = scrub(tag)
    checks.append(("angle bracket escaped", "<" not in scrubbed and ">" not in scrubbed))

    # Idempotence: scrubbing twice equals scrubbing once.
    sample = role + "\n" + tag + "\nplain line stays plain"
    once = scrub(sample)
    twice = scrub(once)
    checks.append(("idempotent", once == twice))

    # A plain line must be untouched.
    plain = "the fallback served three turns while capacity was constrained"
    checks.append(("plain preserved", scrub(plain) == plain))

    # Invisible control char removed.
    hidden = "a" + "\u200b" + "b"
    checks.append(("invisible stripped", scrub(hidden) == "ab"))

    ok = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        sys.stderr.write(status + " " + name + "\n")
        ok = ok and passed
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Mechanically neutralize turn-boundary and markup injection "
        "signatures in a context brief.",
    )
    parser.add_argument("--in", dest="infile", help="Read the brief from a file (default: stdin).")
    parser.add_argument("--out", dest="outfile", help="Write the scrubbed brief here (default: stdout).")
    parser.add_argument("--self-test", action="store_true", help="Run built-in structural checks and exit.")
    args = parser.parse_args(argv)

    if args.self_test:
        return 0 if _self_test() else 1

    try:
        if args.infile:
            with open(args.infile, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            text = sys.stdin.read()
    except OSError as exc:
        sys.stderr.write("brief-scrub: cannot read input: " + str(exc) + "\n")
        return 2

    result = scrub(text)

    try:
        if args.outfile:
            with open(args.outfile, "w", encoding="utf-8") as f:
                f.write(result)
        else:
            sys.stdout.write(result)
    except OSError as exc:
        sys.stderr.write("brief-scrub: cannot write output: " + str(exc) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
