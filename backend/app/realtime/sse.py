"""مولّد بث SSE (بدون اعتماد على FastAPI => قابل للاختبار مباشرة)."""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

from app.realtime.hub import EventHub, sse_format

HEARTBEAT_SECONDS = 20
MAX_STREAM_SECONDS = 30 * 60


class _Request(Protocol):
    async def is_disconnected(self) -> bool: ...


class _Ctx(Protocol):
    tenant_id: UUID


async def stream(request: _Request, hub: EventHub, ctx: _Ctx | Any,
                 heartbeat: float = HEARTBEAT_SECONDS, max_seconds: float = MAX_STREAM_SECONDS
                 ) -> AsyncIterator[bytes]:
    sub = hub.subscribe(ctx.tenant_id)
    deadline = time.monotonic() + max_seconds
    try:
        yield b"retry: 3000\n\n"
        yield sse_format({"type": "ready"})
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                break
            event = await sub.get(timeout=heartbeat)
            yield sse_format(event) if event is not None else b": ping\n\n"
    finally:
        sub.close()
