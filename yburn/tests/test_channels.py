"""Tests for Discord and Slack channel helpers."""

from yburn.channels.discord import send_discord
from yburn.channels.slack import send_slack


class TestWebhookChannelHelpers:
    def test_discord_send_function_exists(self):
        assert callable(send_discord)

    def test_slack_send_function_exists(self):
        assert callable(send_slack)
