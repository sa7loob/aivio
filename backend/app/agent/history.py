"""Turn stored messages into LLM chat history."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.llm.base import ChatMessage

_NON_TEXT = {"image": "صورة", "audio": "رسالة صوتية", "video": "فيديو", "document": "ملف",
             "location": "موقع", "reaction": "تفاعل", "interactive": "اختيار", "other": "رسالة"}


def _render(msg_type: str, text: str | None) -> str | None:
    if msg_type == "text":
        return text
    label = _NON_TEXT.get(msg_type, "رسالة")
    return f"[{label}: {text}]" if text else f"[{label}]"


def build_history(rows_newest_first: Iterable[Any]) -> list[ChatMessage]:
    """الأقدم أولاً. رسائل الموظف تظهر كـ assistant (نفس صوت الوكالة).
    تُحذف التفاعلات (reactions) والبداية بردود قديمة بدون سؤال."""
    history: list[ChatMessage] = []
    for r in reversed(list(rows_newest_first)):
        if r["msg_type"] == "reaction":
            continue
        content = _render(r["msg_type"], r["text_content"])
        if not content:
            continue
        role = "user" if r["direction"] == "inbound" else "assistant"
        history.append(ChatMessage(role, content))
    while history and history[0].role == "assistant":
        history.pop(0)
    return history
