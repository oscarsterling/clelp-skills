#!/usr/bin/env python3
"""gauntlet-bounce.py - one adversarial review round across two model labs.

The Gauntlet is a hook/code hardening loop: you hand a packet (the code under
review plus an attack framing) to two models from different labs, let each hunt
for concrete failures, and adjudicate. This script runs ONE round. The loop
around it (fix, re-run, repeat until both models return zero findings) is yours
to drive; see SKILL.md.

Bring your own keys via environment variables:
    OPENAI_API_KEY   used for the OpenAI leg
    GEMINI_API_KEY   used for the Google Gemini leg

Usage:
    OPENAI_API_KEY=... GEMINI_API_KEY=... \\
        python3 gauntlet-bounce.py --file round-1-packet.md

    cat packet.md | python3 gauntlet-bounce.py            # packet on stdin
    python3 gauntlet-bounce.py "inline packet text"       # packet as an argument
    python3 gauntlet-bounce.py --file p.md --out round-1.md
    python3 gauntlet-bounce.py --dry-run --file p.md      # no network, prints plan

Model choice is overridable so the script never pins a lab's roster:
    --openai-model / OPENAI_MODEL   (default: gpt-4o)
    --gemini-model / GEMINI_MODEL   (default: gemini-2.5-pro)

Output: a markdown transcript with each model's full response and a verdict
summary (GO / NO-GO per model, plus whether the round converged: both GO).
Exit status is 0 on a completed round, 2 if a model leg errored.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"

# Cap on tokens we ask each model to produce. Adversarial reviews with concrete
# reproducers run long, so this is generous on purpose.
MAX_OUTPUT_TOKENS = 16000
HTTP_TIMEOUT_S = 300


def call_openai(prompt, api_key, model):
    """Call the OpenAI chat-completions API directly. Newer reasoning and
    gpt-5-class models use max_completion_tokens and reject a custom
    temperature; older models use max_tokens. We branch on the model prefix so
    a caller can point --openai-model at either family."""
    url = "https://api.openai.com/v1/chat/completions"
    newer_family = model.startswith("o") or model.startswith("gpt-5")
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if newer_family:
        payload["max_completion_tokens"] = MAX_OUTPUT_TOKENS
    else:
        payload["max_tokens"] = MAX_OUTPUT_TOKENS
        payload["temperature"] = 0.7
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S)
        result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001 - surface any failure as a leg error
        return f"ERROR: {e}"


def call_gemini(prompt, api_key, model):
    """Call the Google Gemini generateContent API directly. The key rides as a
    query parameter, matching Google's documented REST shape."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.7},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S)
        result = json.loads(resp.read())
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:  # noqa: BLE001 - surface any failure as a leg error
        return f"ERROR: {e}"


def parse_verdict(text):
    """Classify a model response as GO / NO-GO / UNCLEAR. NO-GO is checked
    first because the substring 'GO' also matches inside 'NO-GO'. This is a
    convenience signal for convergence tracking, not a substitute for reading
    the finding: a model can say GO and still be wrong, which is exactly why
    the loop uses two labs."""
    upper = text.upper()
    if "NO-GO" in upper or "NO GO" in upper:
        return "NO-GO"
    if "GO" in upper:
        return "GO"
    return "UNCLEAR"


def read_packet(args):
    if args.file:
        with open(os.path.expanduser(args.file)) as f:
            return f.read().strip()
    if args.packet:
        return args.packet
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def build_report(timestamp, openai_model, gemini_model, legs):
    lines = [
        f"# Gauntlet round - {timestamp}",
        "",
        "## Verdicts",
        "",
    ]
    for name, model, _resp, verdict in legs:
        lines.append(f"- {name} ({model}): {verdict}")
    verdicts = {v for _, _, _, v in legs}
    converged = verdicts == {"GO"}
    lines.append("")
    lines.append(
        "Converged: yes (both models returned GO, zero findings)"
        if converged
        else "Converged: no (at least one model still has findings or was unclear)"
    )
    lines.append("")
    for name, model, resp, _verdict in legs:
        lines.append(f"## {name} ({model})")
        lines.append("")
        lines.append(resp)
        lines.append("")
    lines.append("## Adjudication (for the orchestrator)")
    lines.append("")
    lines.append("Read both responses and decide:")
    lines.append("1. Which findings are concrete (a real input/state reproduces the failure)?")
    lines.append("2. Where the models disagree, which one reasoned correctly, and why?")
    lines.append("3. The fix for the enumerable class, not just the named instance.")
    lines.append("")
    return "\n".join(lines), converged


def main():
    parser = argparse.ArgumentParser(description="One adversarial two-model review round.")
    parser.add_argument("packet", nargs="?", help="Review packet text (or use --file / stdin).")
    parser.add_argument("--file", "-f", help="Read the review packet from a file.")
    parser.add_argument("--out", "-o", help="Write the transcript here (default: stdout).")
    parser.add_argument(
        "--openai-model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        help=f"OpenAI model id (default: {DEFAULT_OPENAI_MODEL}).",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help=f"Gemini model id (default: {DEFAULT_GEMINI_MODEL}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the plan without making any network calls.",
    )
    args = parser.parse_args()

    packet = read_packet(args)
    if not packet:
        print("Error: no review packet provided (use --file, an argument, or stdin).", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")

    if args.dry_run:
        print("Gauntlet dry run (no network calls made).", file=sys.stderr)
        print(f"  packet length : {len(packet)} chars", file=sys.stderr)
        print(f"  openai model  : {args.openai_model}", file=sys.stderr)
        print(f"  gemini model  : {args.gemini_model}", file=sys.stderr)
        print(f"  OPENAI_API_KEY: {'set' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}", file=sys.stderr)
        print(f"  GEMINI_API_KEY: {'set' if os.environ.get('GEMINI_API_KEY') else 'MISSING'}", file=sys.stderr)
        print(f"  output target : {args.out or 'stdout'}", file=sys.stderr)
        print("Dry run OK.", file=sys.stderr)
        return

    openai_key = os.environ.get("OPENAI_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    legs = []
    if openai_key:
        print(f"Calling OpenAI ({args.openai_model})...", file=sys.stderr, flush=True)
        resp = call_openai(packet, openai_key, args.openai_model)
    else:
        resp = "ERROR: OPENAI_API_KEY not set"
    legs.append(("OpenAI", args.openai_model, resp, parse_verdict(resp)))

    if gemini_key:
        print(f"Calling Gemini ({args.gemini_model})...", file=sys.stderr, flush=True)
        resp = call_gemini(packet, gemini_key, args.gemini_model)
    else:
        resp = "ERROR: GEMINI_API_KEY not set"
    legs.append(("Gemini", args.gemini_model, resp, parse_verdict(resp)))

    report, converged = build_report(timestamp, args.openai_model, args.gemini_model, legs)

    if args.out:
        with open(os.path.expanduser(args.out), "w") as f:
            f.write(report)
        print(f"Transcript written to {args.out}", file=sys.stderr)
    else:
        print(report)

    for name, _model, _resp, verdict in legs:
        print(f"  {name}: {verdict}", file=sys.stderr)
    print(f"  converged: {converged}", file=sys.stderr)

    if any(resp.startswith("ERROR:") for _, _, resp, _ in legs):
        sys.exit(2)


if __name__ == "__main__":
    main()
