#!/usr/bin/env python3
"""
Tests for scripts/hooks/check-tg-reply-completeness.py.

Why this exists (2026-05-13, Layer 1 of two): one-hour Stop-hook rewake death
loop. The hook was exit-2'ing with empty stderr, the model misread the empty
rewake as "ack the user," wrote terminal text, and the cycle repeated ~100
times. This Layer 1 suite locks in the silent-exit-2 ban across the 4 known
block conditions (TG-no-reply, trailing text in same message, trailing text
in later message, suspected-but-unidentified). Layer 2 (rewake counter) adds
loop-break tests on top.

Test mode: invoke the hook script as a subprocess with synthesized stdin and
a temporary transcript file. Assert on exit code, stderr content, and the
debug-log side effect for the suspected branch.

Run: python3 scripts/hooks/test-check-tg-reply-completeness.py
Exit 0 = all pass. Exit 1 = at least one failure.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "check-tg-reply-completeness.py"

# Debug log dir used by the test suite. Hook is told about it via the
# TG_HOOK_DEBUG_LOG_DIR env var in run_hook(), so this constant only
# needs to match what the env var sets.
DEBUG_LOG_DIR = Path(tempfile.gettempdir()) / "tg-hook-test-debug"
DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)


def write_transcript(path, messages):
    """Write a list of message dicts to a JSONL transcript file."""
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


def run_hook(stdin_payload, env_overrides=None, counter_path=None, known_issues_path=None):
    """Invoke the hook script. Returns (returncode, stdout, stderr).

    Always isolates Layer 2 state (rewake counter + loop-event log) from
    real state via env vars. Tests that need to share state across
    multiple run_hook calls (Layer 2 loop-break scenarios) pass explicit
    paths. Single-call tests get fresh per-call tmpfiles by default.

    The `known_issues_path` parameter name is kept for backwards-compat
    with the test signature; in this public version it maps to the
    generic loop-event JSONL log path.
    """
    env = os.environ.copy()
    if counter_path is None:
        counter_fd, counter_path = tempfile.mkstemp(suffix="-counter.json")
        os.close(counter_fd)
        os.remove(counter_path)  # Hook creates it; passing a fresh non-existent path matches prod.
    if known_issues_path is None:
        ki_fd, known_issues_path = tempfile.mkstemp(suffix="-loop-events.jsonl")
        os.close(ki_fd)
    env["TG_HOOK_REWAKE_COUNTER_PATH"] = str(counter_path)
    env["TG_HOOK_LOOP_EVENT_LOG_PATH"] = str(known_issues_path)
    env["TG_HOOK_DEBUG_LOG_DIR"] = str(DEBUG_LOG_DIR)
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


# Transcript fixture builders ------------------------------------------------


def user_text(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_blocks(blocks):
    return {"role": "assistant", "content": blocks}


def text_block(text):
    return {"type": "text", "text": text}


def reply_tool_block(text="fake", tool_use_id=None):
    block = {
        "type": "tool_use",
        "name": "mcp__plugin_telegram_telegram__reply",
        "input": {"chat_id": "12345", "text": text},
    }
    if tool_use_id is not None:
        block["id"] = tool_use_id
    return block


def edit_tool_block(text="fake", tool_use_id=None):
    block = {
        "type": "tool_use",
        "name": "mcp__plugin_telegram_telegram__edit_message",
        "input": {"chat_id": "12345", "message_id": "1", "text": text},
    }
    if tool_use_id is not None:
        block["id"] = tool_use_id
    return block


def tool_result_block(content="ok", tool_use_id=None, is_error=False):
    block = {"type": "tool_result", "content": content}
    if tool_use_id is not None:
        block["tool_use_id"] = tool_use_id
    if is_error:
        block["is_error"] = True
    return block


def tg_inbound_user():
    return user_text(
        '<channel source="plugin:telegram:telegram" chat_id="12345" '
        'message_id="1" user="jason" ts="now">hello</channel>'
    )


# -----------------------------------------------------------------------------


class HookTests(unittest.TestCase):
    """Layer 1 assertions: silent-exit-2 ban + 4 distinct actionable directives."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="hook-test-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.transcript = self.tmpdir / f"{self.id().replace('.', '_')}.jsonl"

    def stdin(self, **overrides):
        payload = {"transcript_path": str(self.transcript)}
        payload.update(overrides)
        return payload

    # ----- 4 block conditions each get distinct actionable stderr ------------

    def test_block_condition_1_tg_inbound_no_reply(self):
        write_transcript(
            self.transcript,
            [tg_inbound_user(), assistant_blocks([text_block("ok done")])],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2, "TG inbound without reply must exit 2")
        self.assertTrue(err.strip(), "stderr must not be empty")
        self.assertIn("no mcp__plugin_telegram_telegram__reply", err)
        self.assertIn("Next turn", err)

    def test_block_condition_2_trailing_text_same_message(self):
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        reply_tool_block(),
                        text_block("Hook passed."),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        self.assertIn("trailing terminal text after TG reply", err)
        self.assertIn("Hook passed.", err)
        self.assertIn("End turn cleanly", err)

    def test_block_condition_3_trailing_text_later_message(self):
        # Reply in msg A, tool_result in user msg B, terminal text in msg C.
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block()]),
                {"role": "user", "content": [tool_result_block()]},
                assistant_blocks([text_block("Done.")]),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        self.assertIn("trailing terminal text after TG reply", err)
        self.assertIn("Done.", err)

    def test_block_condition_4_suspected_unidentified_via_inflight(self):
        # Stdin last_assistant_message has text not present in the JSONL =
        # the in-flight trailing-text branch. Distinct from the suspected
        # fallback below.
        write_transcript(
            self.transcript,
            [tg_inbound_user(), assistant_blocks([reply_tool_block()])],
        )
        code, _, err = run_hook(
            self.stdin(last_assistant_message="Hook passed - in flight")
        )
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        self.assertIn("in-flight", err)
        self.assertIn("Hook passed - in flight", err)

    def test_block_condition_4b_suspected_writes_debug_log(self):
        # Force the suspected-but-unidentified branch: reply present, no
        # trailing text anywhere, but the caller flagged force_suspected.
        # The hook must still exit 2 with actionable stderr AND write a
        # debug log so a human can investigate.
        write_transcript(
            self.transcript,
            [tg_inbound_user(), assistant_blocks([reply_tool_block()])],
        )
        code, _, err = run_hook(
            self.stdin(
                force_suspected_trailing_text=True, turn_id="suspected-test"
            )
        )
        self.assertEqual(code, 2)
        self.assertTrue(err.strip())
        self.assertIn("trailing-text-suspected", err)
        self.assertIn("hook-debug-", err)
        debug_log = DEBUG_LOG_DIR / "hook-debug-suspected-test.log"
        self.assertTrue(
            debug_log.exists(), f"debug log should exist at {debug_log}"
        )
        debug_log.unlink()

    # ----- PASS cases write empty stderr -------------------------------------

    def test_pass_no_tg_no_text(self):
        write_transcript(
            self.transcript,
            [
                user_text("hi"),
                assistant_blocks([text_block("hi back")]),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 0)
        self.assertEqual(
            err.strip(), "", f"PASS should write empty stderr, got {err!r}"
        )

    def test_pass_tg_with_clean_reply(self):
        write_transcript(
            self.transcript,
            [tg_inbound_user(), assistant_blocks([reply_tool_block()])],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), "")

    def test_pass_no_transcript(self):
        # Missing transcript file = PASS, empty stderr (defensive default).
        code, _, err = run_hook({"transcript_path": "/nonexistent/path.jsonl"})
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), "")

    # ----- All 4 block conditions produce DISTINCT directives ----------------

    def test_four_block_conditions_have_distinct_stderr(self):
        # Each block condition must communicate a different actionable
        # directive so the model can tell them apart in the rewake reminder.
        outputs = []

        # 1: TG no reply
        t1 = self.tmpdir / "distinct-1.jsonl"
        write_transcript(
            t1, [tg_inbound_user(), assistant_blocks([text_block("ok")])]
        )
        _, _, e1 = run_hook({"transcript_path": str(t1)})
        outputs.append(e1.strip())

        # 2: trailing text same message
        t2 = self.tmpdir / "distinct-2.jsonl"
        write_transcript(
            t2,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block(), text_block("Hook passed.")]),
            ],
        )
        _, _, e2 = run_hook({"transcript_path": str(t2)})
        outputs.append(e2.strip())

        # 3: trailing text later message
        t3 = self.tmpdir / "distinct-3.jsonl"
        write_transcript(
            t3,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block()]),
                {"role": "user", "content": [tool_result_block()]},
                assistant_blocks([text_block("All good.")]),
            ],
        )
        _, _, e3 = run_hook({"transcript_path": str(t3)})
        outputs.append(e3.strip())

        # 4: suspected unidentified
        t4 = self.tmpdir / "distinct-4.jsonl"
        write_transcript(
            t4, [tg_inbound_user(), assistant_blocks([reply_tool_block()])]
        )
        _, _, e4 = run_hook(
            {
                "transcript_path": str(t4),
                "force_suspected_trailing_text": True,
                "turn_id": "distinct-4",
            }
        )
        outputs.append(e4.strip())
        debug_log = DEBUG_LOG_DIR / "hook-debug-distinct-4.log"
        if debug_log.exists():
            debug_log.unlink()

        for i, out in enumerate(outputs, 1):
            self.assertTrue(out, f"block condition {i} produced empty stderr")
        self.assertEqual(
            len(set(outputs)),
            4,
            "all 4 block conditions must produce DISTINCT stderr",
        )

    def test_silent_exit_2_ban_under_every_block_path(self):
        # Aggregate guard: there must be NO exit-2 path that writes empty
        # stderr. Re-runs all 4 condition fixtures and asserts stderr length.
        scenarios = [
            ("c1", [tg_inbound_user(), assistant_blocks([text_block("oops")])], {}),
            (
                "c2",
                [
                    tg_inbound_user(),
                    assistant_blocks(
                        [reply_tool_block(), text_block("Hook passed.")]
                    ),
                ],
                {},
            ),
            (
                "c3",
                [
                    tg_inbound_user(),
                    assistant_blocks([reply_tool_block()]),
                    {"role": "user", "content": [tool_result_block()]},
                    assistant_blocks([text_block("Done.")]),
                ],
                {},
            ),
            (
                "c4",
                [tg_inbound_user(), assistant_blocks([reply_tool_block()])],
                {
                    "force_suspected_trailing_text": True,
                    "turn_id": "ban-c4",
                },
            ),
        ]
        for label, msgs, extras in scenarios:
            t = self.tmpdir / f"ban-{label}.jsonl"
            write_transcript(t, msgs)
            payload = {"transcript_path": str(t)}
            payload.update(extras)
            code, _, err = run_hook(payload)
            self.assertEqual(code, 2, f"{label}: expected exit 2")
            self.assertTrue(
                err.strip(),
                f"silent exit-2 violation in scenario {label}",
            )
        # Cleanup any debug logs we wrote.
        dbg = DEBUG_LOG_DIR / "hook-debug-ban-c4.log"
        if dbg.exists():
            dbg.unlink()


class PreReplyLeakDetectionTests(unittest.TestCase):
    """2026-05-15 hardening: catch pre-reply and between-reply terminal text.

    The original hook only caught text AFTER the last reply tool call. These
    tests lock in the new substring-of-tg-payload rule. Design constraints
    locked by the GPT-5.5 bounce at
    2026-05-15 GPT-5.5 design bounce: block (not auto-relay),
    suppress duplication, normalize only line endings and outer whitespace.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="hook-test-leak-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.transcript = self.tmpdir / f"{self.id().replace('.', '_')}.jsonl"

    def stdin(self, **overrides):
        payload = {"transcript_path": str(self.transcript)}
        payload.update(overrides)
        return payload

    def test_pre_reply_leaked_text_blocks(self):
        """Test 1: text emitted BEFORE the first reply tool call must block."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        text_block("On it..."),
                        reply_tool_block(text="Here is the answer"),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2, "pre-reply leaked text must exit 2")
        self.assertIn("invisible terminal text", err)
        self.assertIn("On it", err)

    def test_between_reply_leaked_text_blocks(self):
        """Test 2: text between two reply tool calls must block."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        reply_tool_block(text="First part"),
                        text_block("now finishing up"),
                        reply_tool_block(text="Second part"),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2, "between-reply leaked text must exit 2")
        self.assertIn("invisible terminal text", err)
        self.assertIn("now finishing up", err)

    def test_text_substring_of_reply_payload_passes(self):
        """Test 3: text block that is a normalized substring of the reply
        payload is treated as ALREADY DELIVERED and passes."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        text_block("Here is the answer"),
                        reply_tool_block(
                            text="Hi the user, Here is the answer to your question."
                        ),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 0, f"substring of reply payload must pass, got err={err!r}")
        self.assertEqual(err.strip(), "")

    def test_text_substring_of_edit_message_payload_passes(self):
        """Test 4: text block that is a substring of an edit_message payload
        also passes (edit_message text IS visible to the user)."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        edit_tool_block(text="Working on it... step 2 of 3"),
                        text_block("step 2 of 3"),
                        reply_tool_block(text="Done."),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(
            code, 0, f"substring of edit_message payload must pass, got err={err!r}"
        )
        self.assertEqual(err.strip(), "")

    def test_edit_only_turn_still_blocks_missing_reply(self):
        """Test 5: a turn with only edit_message and no reply call still BLOCKS
        on the existing TG-inbound-requires-reply rule. edit_message satisfies
        the leak check but NOT the inbound-reply rule."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks([edit_tool_block(text="working")]),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 2, "edit-only turn must still trip no-reply rule")
        self.assertIn("no mcp__plugin_telegram_telegram__reply", err)

    def test_crlf_line_endings_normalize(self):
        """Test 6: CRLF in the text block, LF in the reply payload, substring
        match should still succeed after normalization."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        text_block("line one\r\nline two"),
                        reply_tool_block(text="prefix\nline one\nline two\nsuffix"),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(
            code, 0, f"CRLF should normalize to LF for substring match, got err={err!r}"
        )

    def test_malformed_transcript_passes_defensively(self):
        """Test 7: malformed JSONL lines are skipped; empty transcript = PASS.

        Defensive default preserved: we never block on garbage input or we
        risk breaking the agent loop on disk corruption.
        """
        # Write invalid JSON lines only - load_transcript returns [].
        self.transcript.write_text("not json\n{also not\n", encoding="utf-8")
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 0, "malformed transcript must default to PASS")
        self.assertEqual(err.strip(), "")

    def test_whitespace_only_text_block_does_not_trigger(self):
        """Defensive: a text block that is only whitespace is noise, not a leak.
        Per the brief: ignore empty/whitespace-only text blocks entirely."""
        write_transcript(
            self.transcript,
            [
                tg_inbound_user(),
                assistant_blocks(
                    [
                        text_block("   \n  \n"),
                        reply_tool_block(text="answer"),
                    ]
                ),
            ],
        )
        code, _, err = run_hook(self.stdin())
        self.assertEqual(code, 0, f"whitespace-only text must pass, got err={err!r}")

    def test_leak_check_respects_rewake_counter(self):
        """Test 8: the rewake counter still force-releases on the 4th block
        within 60s, even when the trigger is the new leak-check path."""
        # Build a fixture that trips the leak check (pre-reply text).
        leak_msgs = [
            tg_inbound_user(),
            assistant_blocks(
                [text_block("On it..."), reply_tool_block(text="answer")]
            ),
        ]
        leak_transcript = self.tmpdir / f"{self.id().replace('.', '_')}-leak.jsonl"
        write_transcript(leak_transcript, leak_msgs)
        counter = self.tmpdir / f"{self.id().replace('.', '_')}-counter.json"
        known_issues = self.tmpdir / f"{self.id().replace('.', '_')}-ki.md"

        payload = {"transcript_path": str(leak_transcript), "session_id": "leak-s1"}

        # First 3 leak-trip turns each exit 2.
        for i in range(1, 4):
            code, _, err = run_hook(
                payload, counter_path=counter, known_issues_path=known_issues
            )
            self.assertEqual(code, 2, f"leak block {i} should exit 2")
            self.assertTrue(err.strip())
            self.assertIn("invisible terminal text", err)

        # 4th turn force-releases (exit 0) even on the new leak path.
        code, _, err = run_hook(
            payload, counter_path=counter, known_issues_path=known_issues
        )
        self.assertEqual(
            code, 0, "4th consecutive leak-check block must be force-released"
        )
        self.assertTrue(
            known_issues.exists(),
            "rewake force-release must still append a known-issues entry",
        )


class ReplyDeliverySuccessTests(unittest.TestCase):
    """2026-05-15 hardening: tool_use existence is not the same as successful
    delivery. If the reply tool returns is_error=true or an "Error:" string,
    nothing actually shipped to the user and we must block."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="hook-test-delivery-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _stdin_for(self, transcript):
        return {"transcript_path": str(transcript)}

    def test_reply_errored_blocks(self):
        # Reply tool was called AND tool_result carries is_error=true ->
        # BLOCK with the new errored-reply directive.
        transcript = self.tmpdir / "errored.jsonl"
        write_transcript(
            transcript,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block(tool_use_id="tu_err")]),
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            tool_result_block(
                                content="Error: chat not found",
                                tool_use_id="tu_err",
                                is_error=True,
                            )
                        ],
                    },
                },
            ],
        )
        code, _, err = run_hook(self._stdin_for(transcript))
        self.assertEqual(code, 2)
        self.assertIn("returned an error", err)

    def test_reply_success_passes(self):
        # tool_result present without is_error and content not starting with
        # "Error:" -> PASS (this also exercises the previously-uncovered
        # find_tool_result code path with a real tool_use_id set).
        transcript = self.tmpdir / "success.jsonl"
        write_transcript(
            transcript,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block(tool_use_id="tu_ok")]),
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            tool_result_block(content="ok", tool_use_id="tu_ok")
                        ],
                    },
                },
            ],
        )
        code, _, err = run_hook(self._stdin_for(transcript))
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), "")

    def test_missing_tool_result_passes(self):
        # Reply called but no tool_result block in transcript yet (could be
        # the case at hook-fire time before result flushes) -> PASS
        # defensively. Failure-to-deliver gets caught on the next turn.
        transcript = self.tmpdir / "missing-result.jsonl"
        write_transcript(
            transcript,
            [
                tg_inbound_user(),
                assistant_blocks([reply_tool_block(tool_use_id="tu_pending")]),
            ],
        )
        code, _, err = run_hook(self._stdin_for(transcript))
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), "")


class Layer2RewakeCounterTests(unittest.TestCase):
    """Layer 2: rewake-counter loop break.

    Counter resets on: (a) clean pass, (b) new session_id, (c) time gap > 60s.
    Loop breaks on the 4th consecutive block within the window (force exit 0
    instead of exit 2, append known-issues entry).
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="hook-test-layer2-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def setUp(self):
        self.transcript = self.tmpdir / f"{self.id().replace('.', '_')}.jsonl"
        self.counter = self.tmpdir / f"{self.id().replace('.', '_')}-counter.json"
        self.known_issues = (
            self.tmpdir / f"{self.id().replace('.', '_')}-known-issues.md"
        )

    def _trailing_text_msgs(self):
        return [
            tg_inbound_user(),
            assistant_blocks([reply_tool_block(), text_block("Hook passed.")]),
        ]

    def _clean_reply_msgs(self):
        return [
            tg_inbound_user(),
            assistant_blocks([reply_tool_block()]),
            {"role": "user", "content": [tool_result_block()]},
        ]

    def _run(self, payload):
        return run_hook(
            payload,
            counter_path=self.counter,
            known_issues_path=self.known_issues,
        )

    def test_layer2_four_consecutive_blocks_in_window_releases(self):
        """3 blocks exit 2 normally; 4th block forces exit 0 + known-issues entry."""
        write_transcript(self.transcript, self._trailing_text_msgs())
        payload = {"transcript_path": str(self.transcript), "session_id": "s1"}

        for i in range(1, 4):
            code, _, err = self._run(payload)
            self.assertEqual(code, 2, f"block {i} should exit 2")
            self.assertTrue(err.strip(), f"block {i} stderr must be non-empty")

        code, _, err = self._run(payload)
        self.assertEqual(code, 0, "4th consecutive block should be released (exit 0)")

        self.assertTrue(
            self.known_issues.exists(),
            "loop-break must append a loop-event log entry",
        )
        ki_text = self.known_issues.read_text(encoding="utf-8")
        self.assertIn(
            "stop_hook_rewake_loop_break",
            ki_text,
            "loop-event log entry must name the loop incident kind",
        )
        self.assertIn("force_released_to_exit_0", ki_text)

        # State after release: counter reset to 0
        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(state["consecutive_blocks"], 0)
        self.assertIsNone(state["first_block_ts"])
        self.assertIsNone(state["last_block_ts"])

    def test_layer2_time_gap_resets_counter(self):
        """3 blocks within window, then simulate > 60s gap; next block exits 2 normally."""
        write_transcript(self.transcript, self._trailing_text_msgs())
        payload = {"transcript_path": str(self.transcript), "session_id": "s1"}

        for i in range(1, 4):
            code, _, _ = self._run(payload)
            self.assertEqual(code, 2, f"warm-up block {i} should exit 2")

        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(state["consecutive_blocks"], 3)

        # Simulate a > 60s gap by backdating last_block_ts and first_block_ts.
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        if old_ts.endswith("+00:00"):
            old_ts = old_ts.replace("+00:00", "Z")
        state["first_block_ts"] = old_ts
        state["last_block_ts"] = old_ts
        self.counter.write_text(json.dumps(state), encoding="utf-8")

        # Next block should exit 2 normally (counter reset by time-gap trigger).
        code, _, err = self._run(payload)
        self.assertEqual(
            code, 2, "block after > 60s gap should exit 2 normally, not be released"
        )
        self.assertTrue(err.strip())

        # State: time-gap reset cleared counter, this block re-armed it at 1.
        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(
            state["consecutive_blocks"],
            1,
            "counter must reset on time gap, then increment from 1 for this block",
        )

    def test_layer2_pass_resets_counter(self):
        """block, then pass, then block: counter resets on the pass."""
        block_transcript = self.tmpdir / f"{self.id().replace('.', '_')}-block.jsonl"
        pass_transcript = self.tmpdir / f"{self.id().replace('.', '_')}-pass.jsonl"
        write_transcript(block_transcript, self._trailing_text_msgs())
        write_transcript(pass_transcript, self._clean_reply_msgs())

        session = {"session_id": "s1"}

        # Block (counter=1)
        code, _, _ = self._run({"transcript_path": str(block_transcript), **session})
        self.assertEqual(code, 2)
        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(state["consecutive_blocks"], 1)

        # Pass (counter resets to 0)
        code, _, _ = self._run({"transcript_path": str(pass_transcript), **session})
        self.assertEqual(code, 0)
        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(state["consecutive_blocks"], 0)
        self.assertIsNone(state["first_block_ts"])
        self.assertIsNone(state["last_block_ts"])

        # Block again (counter back to 1, NOT 2)
        code, _, _ = self._run({"transcript_path": str(block_transcript), **session})
        self.assertEqual(code, 2)
        state = json.loads(self.counter.read_text(encoding="utf-8"))
        self.assertEqual(
            state["consecutive_blocks"],
            1,
            "counter must reset on pass; the new block is a fresh 1, not 2",
        )


if __name__ == "__main__":
    # Verbose so the human-visible run shows pass/fail per case.
    unittest.main(verbosity=2)
