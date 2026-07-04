#!/usr/bin/env python3
"""secret-scan.py - detect credential/secret values in text before they leave.

PURE DETECTION. It takes text and returns findings. It never blocks, never does
I/O on behalf of a tool call, and (critically) NEVER emits a secret value: a
finding carries the detector, a label, an offset/length, and a non-reversible
short fingerprint, never the matched bytes. Keeping detection separate from the
enforcement hook makes it unit-testable with no live tool call.

THREE DETECTION LAYERS
  1. PATTERN  - known provider prefixes and shapes (sk-ant-, ghp_, AIza..., AKIA,
     private-key headers, JWTs, and more). Fast, high precision, no config.
  2. INDEXED  - exact-match against YOUR OWN curated secret values without ever
     storing them in the clear. You keep a plaintext list of the exact secrets
     that must never egress (an ignored, local-only file), build a salted-hash
     INDEX from it once, and scan against the index in-process. This catches a
     leaked literal even in a format the pattern layer does not know. The match
     is mathematically identical to a substring search for each value, but the
     index holds only salted SHA-256 hashes, never plaintext.
  3. ENTROPY  - long, dense, mixed-class tokens that match no known prefix. Noisy
     by nature, so it is tagged low/medium confidence and is opt-in.

WHY AN INDEX INSTEAD OF THE RAW LIST AT RUNTIME
  The enforcement hook runs on a hot path and should not read a plaintext secret
  file every call (and you do not want that file loadable by the scanned agent).
  Build the index offline, store it 0600, and the hook matches against hashes
  only. Rebuild it whenever you rotate or add a secret.

CROSS-PLATFORM: this ships no OS secret-store integration. Your secret VALUES
  come from a file you control (populate it from your own vault, environment, or
  secret manager in a build step). See secret-values.example.txt.

CLI
  scan-text   : read --text or stdin, print findings JSON (redacted).
  build-index : read --values FILE, write a salted-hash index to --out.
  selftest    : run built-in planted/benign assertions (no external files).
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import time

MIN_INDEX_VALUE_LEN = 8  # below this a value is too short to match without noise
INDEX_OPS_CAP = 2_000_000  # bound rolling-hash work on a pathological payload


# --- Layer 1: pattern detectors --------------------------------------------
# (name, compiled regex). Anchored on token shapes so we do not false-positive
# on prose. The private-key marker is assembled from a fragment so this source
# file does not itself trip a "detect private key" pre-commit scanner.
PATTERNS = [
    ("anthropic_api_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_project_key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}")),
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[psour]_[A-Za-z0-9]{36,}\b")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("stripe_secret_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?" + "PRIVATE" + " KEY-----")),
    ("bearer_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

_PUBLIC_JWT_ROLES = {"anon", "public", "authenticated"}


def _fingerprint(value):
    """Non-reversible short tag for correlating a finding across a log without
    revealing the secret. SHA-256 truncated; high-entropy inputs make this safe
    to store."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def _jwt_confidence(token):
    """A JWT is only a high-confidence leak if it is a PRIVILEGED token. Many
    public frontend keys are JWTs too (they ship in client code). Decode the
    payload's `role`: an explicit public role downgrades to low confidence;
    anything privileged or undecodable stays high."""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg).decode("utf-8", "replace"))
        if str(claims.get("role", "")).lower() in _PUBLIC_JWT_ROLES:
            return "low"
    except Exception:
        pass
    return "high"


def scan_patterns(text):
    findings = []
    for name, pat in PATTERNS:
        for m in pat.finditer(text):
            val = m.group(0)
            conf = _jwt_confidence(val) if name == "jwt" else "high"
            findings.append({
                "detector": "pattern", "label": name,
                "offset": m.start(), "length": len(val),
                "fingerprint": _fingerprint(val), "confidence": conf,
            })
    return findings


# --- Layer 3: entropy detector ---------------------------------------------
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")


def _shannon_entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def scan_entropy(text, min_entropy=4.0):
    findings = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        classes = sum([any(c.islower() for c in tok),
                       any(c.isupper() for c in tok),
                       any(c.isdigit() for c in tok)])
        if classes < 2:
            continue
        ent = _shannon_entropy(tok)
        if ent < min_entropy:
            continue
        findings.append({
            "detector": "entropy", "label": "high_entropy_token",
            "offset": m.start(), "length": len(tok),
            "fingerprint": _fingerprint(tok),
            "confidence": "medium" if ent >= 4.5 else "low",
            "entropy": round(ent, 2),
        })
    return findings


# --- Layer 2: salted-hash index --------------------------------------------
def _read_values_file(path):
    """One secret per line; blank lines and #-comments ignored. Returns the list
    of values that are long enough to index."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if len(line) >= MIN_INDEX_VALUE_LEN:
                out.append(line)
    return out


def build_index_payload(values, salt_hex=None):
    """Turn a list of secret VALUES into an index dict to persist. The index
    holds ONLY salted SHA-256 hashes, never plaintext. `by_length` maps a value
    length -> the hashes at that length; `charset` is the union of all value
    characters (a stored value can only occur inside a run of these chars, which
    bounds the scan window); `prefixes` is the set of 2-char value prefixes (a
    cheap necessary-condition prune). None of these reveal a full secret."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    by_length, charset, lengths, prefixes = {}, set(), set(), set()
    count = 0
    for val in values:
        h = hashlib.sha256(salt + val.encode("utf-8", "replace")).hexdigest()
        L = len(val)
        by_length.setdefault(str(L), [])
        if h not in by_length[str(L)]:
            by_length[str(L)].append(h)
        charset.update(val)
        lengths.add(L)
        prefixes.add(val[:2])
        count += 1
    return {
        "version": 1,
        "salt": salt.hex(),
        "min_len": min(lengths) if lengths else MIN_INDEX_VALUE_LEN,
        "lengths": sorted(lengths),
        "by_length": by_length,
        "charset": "".join(sorted(charset)),
        "prefixes": sorted(prefixes),
        "count": count,
    }


def load_index(path):
    """Load and sanity-check an index. Returns the dict or None."""
    try:
        with open(path, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return None
    if not isinstance(idx, dict) or idx.get("version") != 1 or not idx.get("count"):
        return None
    return idx


def scan_index(text, idx):
    """Match `text` against the salted-hash index in-process. Equivalent to a
    substring search for every stored value, with no plaintext and no
    subprocess. Returns (findings, degraded); degraded=True if the work cap was
    hit (treat like a budget overrun)."""
    findings = []
    try:
        salt = bytes.fromhex(idx["salt"])
        charset = set(idx["charset"])
        lengths = [int(x) for x in idx["lengths"]]
        by_length = {int(k): set(v) for k, v in idx["by_length"].items()}
        min_len = int(idx["min_len"])
        prefixes = set(idx.get("prefixes") or [])
    except Exception:
        return findings, True
    n = len(text)
    if n < min_len or not lengths:
        return findings, False
    ops = 0
    i = 0
    while i < n:
        if text[i] not in charset:
            i += 1
            continue
        j = i
        while j < n and text[j] in charset:
            j += 1
        if (j - i) >= min_len:
            for start in range(i, j - min_len + 1):
                if prefixes and text[start:start + 2] not in prefixes:
                    continue
                for L in lengths:
                    if start + L > j:
                        break  # sorted asc; longer overruns the run too
                    hset = by_length.get(L)
                    if not hset:
                        continue
                    ops += 1
                    if ops > INDEX_OPS_CAP:
                        return findings, True
                    window = text[start:start + L]
                    h = hashlib.sha256(salt + window.encode("utf-8", "replace")).hexdigest()
                    if h in hset:
                        findings.append({
                            "detector": "index", "label": "known_secret",
                            "offset": start, "length": L,
                            "fingerprint": _fingerprint(window), "confidence": "high",
                        })
        i = j
    return findings, False


# --- Top-level API ----------------------------------------------------------
_DETECTOR_RANK = {"index": 3, "pattern": 2, "entropy": 1}


def _dedup(findings):
    best = {}
    for f in findings:
        key = f["offset"]
        cur = best.get(key)
        if cur is None or _DETECTOR_RANK[f["detector"]] > _DETECTOR_RANK[cur["detector"]]:
            best[key] = f
    return sorted(best.values(), key=lambda f: f["offset"])


def scan(text, index=None, use_entropy=False):
    """Scan text. Returns {findings: [...], index_degraded: bool}. Findings never
    contain the matched secret bytes."""
    if not isinstance(text, str) or not text:
        return {"findings": [], "index_degraded": False}
    findings = scan_patterns(text)
    degraded = False
    if index is not None:
        idx_findings, degraded = scan_index(text, index)
        findings.extend(idx_findings)
    if use_entropy:
        findings.extend(scan_entropy(text))
    return {"findings": _dedup(findings), "index_degraded": degraded}


def has_high_confidence(result):
    """True if any finding is high confidence (the enforce trigger). Entropy-only
    findings inform telemetry but do not trip a block on their own."""
    return any(f.get("confidence") == "high" for f in result.get("findings", []))


# --- CLI --------------------------------------------------------------------
def _cli_scan_text(rest):
    ap = argparse.ArgumentParser(prog="secret-scan.py scan-text")
    ap.add_argument("--text")
    ap.add_argument("--index")
    ap.add_argument("--entropy", action="store_true")
    args = ap.parse_args(rest)
    text = args.text if args.text is not None else sys.stdin.read()
    idx = load_index(args.index) if args.index else None
    result = scan(text, index=idx, use_entropy=args.entropy)
    print(json.dumps(result, indent=2))
    return 0


def _cli_build_index(rest):
    ap = argparse.ArgumentParser(prog="secret-scan.py build-index")
    ap.add_argument("--values", required=True, help="Plaintext secret-values file.")
    ap.add_argument("--out", required=True, help="Where to write the salted-hash index.")
    args = ap.parse_args(rest)
    try:
        values = _read_values_file(args.values)
    except OSError as exc:
        sys.stderr.write("secret-scan: cannot read values: " + str(exc) + "\n")
        return 2
    if not values:
        sys.stderr.write("secret-scan: no index-eligible values found (min length "
                         + str(MIN_INDEX_VALUE_LEN) + ")\n")
        return 2
    payload = build_index_payload(values)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print("wrote index: " + str(payload["count"]) + " value(s), 0600, hashes only")
    return 0


def _cli_selftest(_rest):
    ok = True
    hit = [
        "here is sk-ant-api03-" + "A" * 40,
        "token ghp_" + "b" * 36,
        "key AIza" + "C" * 35,
        "-----BEGIN OPENSSH " + "PRIVATE" + " KEY-----",
        "bot 1234567890:" + "D" * 35,
    ]
    clean = [
        "the quick brown fox jumps over the lazy dog",
        "build the credential egress gate today",
        "doc:abc123def456 art:001122334455 chunk:deadbeef0000",
    ]
    for c in hit:
        if not scan(c)["findings"]:
            sys.stderr.write("FAIL (missed): " + c[:30] + "\n")
            ok = False
    for c in clean:
        if scan(c)["findings"]:
            sys.stderr.write("FAIL (false positive): " + c[:40] + "\n")
            ok = False
    # Index round-trip: a made-up secret is caught by the index but NOT by the
    # pattern layer (proving the index adds unknown-format coverage), and the
    # index stores no plaintext.
    secret = "zZ9-not_a_real_secret_value_00-QQ"
    idx = build_index_payload([secret])
    if secret in json.dumps(idx):
        sys.stderr.write("FAIL: index leaked plaintext\n")
        ok = False
    r_pat = scan(secret)  # pattern-only, no index
    r_idx = scan(secret, index=idx)
    if r_pat["findings"]:
        sys.stderr.write("FAIL: pattern layer should not match the made-up secret\n")
        ok = False
    if not any(f["detector"] == "index" for f in r_idx["findings"]):
        sys.stderr.write("FAIL: index did not catch its own value\n")
        ok = False
    # And a benign payload does not hit the index.
    if scan("just some ordinary words here", index=idx)["findings"]:
        sys.stderr.write("FAIL: index false positive on prose\n")
        ok = False
    print("selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    if not argv:
        sys.stderr.write("usage: secret-scan.py {scan-text|build-index|selftest} [opts]\n")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "scan-text":
        return _cli_scan_text(rest)
    if cmd == "build-index":
        return _cli_build_index(rest)
    if cmd == "selftest":
        return _cli_selftest(rest)
    sys.stderr.write("unknown command: " + cmd + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
