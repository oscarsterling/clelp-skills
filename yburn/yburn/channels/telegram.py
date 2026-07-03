"""Telegram output channel for yburn.

Sends messages via the Telegram Bot API. Handles message splitting
for the 4096 character limit and markdown formatting.
"""

import json
import logging
import urllib.request
import urllib.error
from urllib.parse import urlsplit, urlunsplit
from typing import List, Optional

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
SPLIT_BUFFER = 100  # Leave room for split indicators


class TelegramChannel:
    """Sends messages via Telegram Bot API."""

    def __init__(self, token: str, chat_id: str):
        """Initialize Telegram channel.

        Args:
            token: Telegram bot token.
            chat_id: Target chat ID.
        """
        if not token:
            raise ValueError("Telegram bot token is required")
        if not chat_id:
            raise ValueError("Telegram chat_id is required")
        self.token = token
        self.chat_id = chat_id
        self.base_url = "https://api.telegram.org"

    def _method_url(self, method: str) -> str:
        """Build a Telegram API URL for a method without storing tokenized URLs."""
        return f"{self.base_url}/bot{self.token}/{method}"

    def _redact_url(self, value: str) -> str:
        """Remove bot token from URLs before logging."""
        if "/bot" not in value:
            return value

        parts = urlsplit(value)
        marker = "/bot"
        idx = parts.path.find(marker)
        if idx == -1:
            return value

        remainder = parts.path[idx + len(marker):]
        slash_idx = remainder.find("/")
        if slash_idx == -1:
            redacted_path = f"{parts.path[:idx]}{marker}<redacted>"
        else:
            redacted_path = (
                f"{parts.path[:idx]}{marker}<redacted>{remainder[slash_idx:]}"
            )
        return urlunsplit(parts._replace(path=redacted_path))

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message, splitting if necessary.

        Args:
            message: The message text to send.
            parse_mode: Telegram parse mode (Markdown or HTML).

        Returns:
            True if all message parts sent successfully.
        """
        if not message.strip():
            logger.warning("Attempted to send empty message")
            return False

        chunks = self._split_message(message)
        all_ok = True

        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"({i+1}/{len(chunks)})\n{chunk}"

            success = self._send_chunk(chunk, parse_mode)
            if not success:
                # Try without parse_mode as fallback
                logger.warning("Retrying chunk %d without parse_mode", i + 1)
                success = self._send_chunk(chunk, parse_mode=None)
            if not success:
                all_ok = False

        return all_ok

    def _send_chunk(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """Send a single message chunk.

        Args:
            text: Message text.
            parse_mode: Optional parse mode.

        Returns:
            True if sent successfully.
        """
        payload = {
            "chat_id": self.chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        data = json.dumps(payload).encode("utf-8")
        url = self._method_url("sendMessage")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                logger.debug("Message sent successfully")
                return True
            else:
                logger.error("Telegram API error: %s", result.get("description"))
                return False
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            logger.error("Telegram HTTP error %d: %s", e.code, body[:200])
            return False
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, str):
                reason = self._redact_url(reason)
            logger.error("Telegram connection error: %s", reason)
            return False
        except Exception as e:
            logger.error("Telegram send error: %s", self._redact_url(str(e)))
            return False

    def _split_message(self, message: str) -> List[str]:
        """Split a message into chunks that fit within Telegram limits.

        Tries to split on newline boundaries for clean breaks.

        Args:
            message: The full message text.

        Returns:
            List of message chunks.
        """
        max_len = MAX_MESSAGE_LENGTH - SPLIT_BUFFER

        if len(message) <= max_len:
            return [message]

        chunks = []
        lines = message.split("\n")
        current_chunk = []
        current_length = 0

        for line in lines:
            line_len = len(line) + 1  # +1 for newline

            if current_length + line_len > max_len:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = [line]
                    current_length = line_len
                else:
                    # Single line exceeds limit, force split
                    while line:
                        chunks.append(line[:max_len])
                        line = line[max_len:]
                    current_length = 0
            else:
                current_chunk.append(line)
                current_length += line_len

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks

    def test_connection(self) -> bool:
        """Test if the bot token and chat_id are valid.

        Returns:
            True if the bot can reach the chat.
        """
        url = self._method_url("getMe")
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                bot_name = result["result"].get("username", "unknown")
                logger.info("Telegram bot connected: @%s", bot_name)
                return True
            return False
        except Exception as e:
            logger.error(
                "Telegram connection test failed: %s",
                self._redact_url(str(e)),
            )
            return False
