"""Realtime: Postgres LISTEN tenant_events -> مشتركي SSE لنفس الوكالة فقط.

- اتصال asyncpg واحد لكل عملية API (وليس لكل متصفح) يستمع لقناة tenant_events.
- الإشعار يحمل معرّفات فقط؛ يُوزَّع على طوابير مشتركي نفس tenant_id، والواجهة تعيد الجلب
  من الـ API (RLS). لا يصل أي حقل من وكالة لأخرى، ولا يُرسل tenant_id للمتصفح.
- طابور كل مشترك محدود: إذا امتلأ (متصفح بطيء) تُحذف الأحداث ويُرسل له 'resync' مرة واحدة.
- انقطاع الاتصال بقاعدة البيانات => إعادة اتصال مع backoff ثم 'resync' لكل المشتركين
  (قد تكون أحداث ضاعت أثناء الانقطاع؛ الواجهة تعيد تحميل القائمة).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

log = logging.getLogger(__name__)

CHANNEL = "tenant_events"
EVENT_TYPES = {"message", "conversation", "lead", "message_status"}
RESYNC = {"type": "resync"}


def parse_notification(payload: str) -> tuple[UUID, dict[str, Any]] | None:
    """payload من الـ trigger => (tenant_id, حدث للمتصفح بدون tenant_id). أي شيء غير متوقع => None."""
    try:
        raw = json.loads(payload)
        tenant_id = UUID(str(raw["t"]))
        etype = raw["type"]
    except (ValueError, KeyError, TypeError):
        return None
    if etype not in EVENT_TYPES:
        return None
    event = {"type": etype, "id": raw.get("id")}
    if raw.get("conversation_id"):
        event["conversation_id"] = raw["conversation_id"]
    return tenant_id, event


class Subscription:
    def __init__(self, hub: EventHub, tenant_id: UUID, maxsize: int) -> None:
        self.hub, self.tenant_id = hub, tenant_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False

    def push(self, event: dict[str, Any]) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # المتصفح لا يلحق: نفرغ الطابور ونطلب إعادة تحميل كاملة بدل تراكم الذاكرة
            self.overflowed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(RESYNC)

    async def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            event = await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if event is RESYNC:
            self.overflowed = False
        return event

    def close(self) -> None:
        self.hub._remove(self)


Connector = Callable[[], Awaitable[Any]]


class EventHub:
    def __init__(self, connect: Connector, *, queue_size: int = 200,
                 backoff: tuple[float, ...] = (1, 2, 5, 10, 30)) -> None:
        self._connect = connect
        self._queue_size = queue_size
        self._backoff = backoff
        self._subs: dict[UUID, set[Subscription]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.connected = asyncio.Event()

    # -------------------------------------------------------------- subscribers
    def subscribe(self, tenant_id: UUID) -> Subscription:
        sub = Subscription(self, tenant_id, self._queue_size)
        self._subs.setdefault(tenant_id, set()).add(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.tenant_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.tenant_id]

    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subs.values())

    def dispatch(self, payload: str) -> int:
        parsed = parse_notification(payload)
        if parsed is None:
            log.warning("realtime: ignored malformed notification")
            return 0
        tenant_id, event = parsed
        subs = self._subs.get(tenant_id, ())
        for sub in list(subs):
            sub.push(event)
        return len(subs)

    def broadcast_resync(self) -> None:
        for subs in self._subs.values():
            for sub in list(subs):
                sub.push(RESYNC)

    # -------------------------------------------------------------- listener
    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self.dispatch(payload)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="realtime-hub")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        attempt = 0
        first = True
        while not self._stopping.is_set():
            conn = None
            try:
                conn = await self._connect()
                await conn.add_listener(CHANNEL, self._on_notify)
                self.connected.set()
                if not first:
                    self.broadcast_resync()
                first, attempt = False, 0
                log.info("realtime: listening on %s", CHANNEL)
                while not self._stopping.is_set():
                    await asyncio.sleep(15)
                    await conn.execute("SELECT 1")      # كشف انقطاع الاتصال الصامت
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - أي خطأ اتصال => إعادة المحاولة
                self.connected.clear()
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                attempt += 1
                first = False
                log.warning("realtime: listener error (%s); reconnecting in %ss", type(e).__name__, delay)
                await asyncio.sleep(delay)
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        await conn.close()


def asyncpg_connector(database_url: str) -> Connector:
    """database_url بصيغة SQLAlchemy (postgresql+asyncpg://) => DSN لـ asyncpg. نفس دور app_user."""
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def connect() -> Any:
        import asyncpg  # noqa: PLC0415 - مستورد عند التشغيل فقط

        return await asyncpg.connect(dsn, server_settings={"application_name": "api-realtime"})

    return connect


def sse_format(event: dict[str, Any]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
