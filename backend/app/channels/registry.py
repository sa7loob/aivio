"""Maps Meta `object` types to parsers and channels to senders.

إضافة قناة جديدة (مثل TikTok) = parser + sender هنا فقط.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.channels import messenger, whatsapp
from app.channels.base import ChannelSender, ParsedWebhook

Parser = Callable[[dict[str, Any]], ParsedWebhook]

PARSERS: dict[str, Parser] = {
    whatsapp.OBJECT_TYPE: whatsapp.parse_webhook,
    messenger.OBJECT_PAGE: messenger.parse_messenger,
    messenger.OBJECT_INSTAGRAM: messenger.parse_instagram,
}


def parser_for(object_type: str | None) -> Parser | None:
    return PARSERS.get(object_type or "")


class SenderRegistry:
    def __init__(self, senders: dict[str, ChannelSender]) -> None:
        self._senders = senders

    def get(self, channel: str) -> ChannelSender | None:
        return self._senders.get(channel)
