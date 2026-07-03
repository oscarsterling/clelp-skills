"""Tests for the yburn Telegram output channel."""

import json
from unittest.mock import patch, MagicMock

import pytest

from yburn.channels.telegram import TelegramChannel, MAX_MESSAGE_LENGTH


class TestInit:
    def test_requires_token(self):
        with pytest.raises(ValueError, match="token"):
            TelegramChannel("", "12345")

    def test_requires_chat_id(self):
        with pytest.raises(ValueError, match="chat_id"):
            TelegramChannel("fake-token", "")

    def test_valid_init(self):
        ch = TelegramChannel("fake-token", "12345")
        assert ch.token == "fake-token"
        assert ch.chat_id == "12345"


class TestMessageSplitting:
    def test_short_message_no_split(self):
        ch = TelegramChannel("token", "123")
        chunks = ch._split_message("Hello world")
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_message_splits(self):
        ch = TelegramChannel("token", "123")
        # Create a message that exceeds the limit
        long_msg = "\n".join([f"Line {i}: " + "x" * 80 for i in range(100)])
        chunks = ch._split_message(long_msg)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= MAX_MESSAGE_LENGTH

    def test_splits_on_newlines(self):
        ch = TelegramChannel("token", "123")
        lines = [f"Line {i}" for i in range(500)]
        msg = "\n".join(lines)
        chunks = ch._split_message(msg)
        assert len(chunks) > 1
        # Each chunk should end cleanly (no mid-line splits)
        for chunk in chunks:
            assert not chunk.endswith(" ")  # Not mid-word

    def test_empty_message(self):
        ch = TelegramChannel("token", "123")
        chunks = ch._split_message("")
        assert len(chunks) == 1

    def test_exact_limit_no_split(self):
        ch = TelegramChannel("token", "123")
        msg = "x" * (MAX_MESSAGE_LENGTH - 200)
        chunks = ch._split_message(msg)
        assert len(chunks) == 1


class TestSend:
    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_send_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_urlopen.return_value = mock_resp

        ch = TelegramChannel("fake-token", "12345")
        result = ch.send("Hello world")
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_send_empty_message(self, mock_urlopen):
        ch = TelegramChannel("fake-token", "12345")
        result = ch.send("")
        assert result is False
        mock_urlopen.assert_not_called()

    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_send_includes_parse_mode(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_urlopen.return_value = mock_resp

        ch = TelegramChannel("fake-token", "12345")
        ch.send("Hello", parse_mode="HTML")

        # Check the request payload
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        payload = json.loads(req.data.decode())
        assert payload["parse_mode"] == "HTML"
        assert payload["chat_id"] == "12345"
        assert payload["text"] == "Hello"

    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_send_api_error_retries_without_parse_mode(self, mock_urlopen):
        # First call fails, second succeeds
        mock_resp_fail = MagicMock()
        mock_resp_fail.read.return_value = json.dumps({"ok": False, "description": "parse error"}).encode()
        mock_resp_ok = MagicMock()
        mock_resp_ok.read.return_value = json.dumps({"ok": True}).encode()
        mock_urlopen.side_effect = [mock_resp_fail, mock_resp_ok]

        ch = TelegramChannel("fake-token", "12345")
        result = ch.send("Hello *world")
        assert result is True
        assert mock_urlopen.call_count == 2


class TestConnection:
    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_connection_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "ok": True,
            "result": {"username": "yburn_bot"}
        }).encode()
        mock_urlopen.return_value = mock_resp

        ch = TelegramChannel("fake-token", "12345")
        assert ch.test_connection() is True

    @patch("yburn.channels.telegram.urllib.request.urlopen")
    def test_connection_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        ch = TelegramChannel("fake-token", "12345")
        assert ch.test_connection() is False
