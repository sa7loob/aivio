import asyncio
import json
from uuid import uuid4

from app.realtime.hub import RESYNC, EventHub, parse_notification, sse_format
from app.realtime.sse import stream

T1, T2 = uuid4(), uuid4()


def _payload(t, typ="message", **kw):
    return json.dumps({"t": str(t), "type": typ, "id": str(uuid4()), "conversation_id": str(uuid4()), **kw})


def test_parse_notification_strips_tenant_and_rejects_garbage():
    parsed = parse_notification(_payload(T1))
    assert parsed is not None
    tenant, event = parsed
    assert tenant == T1 and "t" not in event and event["type"] == "message"
    assert parse_notification("not json") is None
    assert parse_notification(json.dumps({"t": "x", "type": "message"})) is None
    assert parse_notification(json.dumps({"t": str(T1), "type": "drop_table"})) is None
    assert parse_notification(json.dumps({"type": "message"})) is None


def test_dispatch_only_to_same_tenant():
    async def run():
        hub = EventHub(connect=None)
        a, b = hub.subscribe(T1), hub.subscribe(T2)
        assert hub.dispatch(_payload(T1)) == 1
        ea = await a.get(0.1)
        eb = await b.get(0.05)
        assert ea["type"] == "message" and eb is None
        a.close()
        b.close()
        assert hub.subscriber_count() == 0
        assert hub.dispatch(_payload(T1)) == 0
    asyncio.run(run())


def test_slow_consumer_gets_single_resync():
    async def run():
        hub = EventHub(connect=None, queue_size=3)
        s = hub.subscribe(T1)
        for _ in range(10):
            hub.dispatch(_payload(T1))
        assert await s.get(0.1) is RESYNC
        assert await s.get(0.05) is None          # الطابور فُرّغ ولا تراكم
        hub.dispatch(_payload(T1, typ="lead"))
        assert (await s.get(0.1))["type"] == "lead"  # بعد الـ resync تعود الأحداث
    asyncio.run(run())


class FakeConn:
    def __init__(self, fail_execute=False):
        self.listeners, self.closed, self.fail_execute = {}, False, fail_execute

    async def add_listener(self, channel, cb):
        self.listeners[channel] = cb

    async def execute(self, sql):
        if self.fail_execute:
            raise ConnectionError("gone")

    async def close(self):
        self.closed = True


def test_listener_reconnects_and_broadcasts_resync():
    async def run():
        conns = [FakeConn(fail_execute=True), FakeConn()]
        made = []

        async def connect():
            c = conns[len(made)] if len(made) < len(conns) else FakeConn()
            made.append(c)
            return c

        import app.realtime.hub as hub_mod
        real_sleep = asyncio.sleep

        async def fast_sleep(d):
            await real_sleep(0.001)
        hub_mod.asyncio.sleep = fast_sleep
        try:
            hub = EventHub(connect, backoff=(0,))
            sub = hub.subscribe(T1)
            await hub.start()
            for _ in range(200):
                if len(made) >= 2 and hub.connected.is_set():
                    break
                await real_sleep(0.001)
            assert made[0].closed, "failed connection closed"
            assert "tenant_events" in made[1].listeners
            assert await sub.get(0.1) is RESYNC      # قد تكون أحداث ضاعت أثناء الانقطاع
            made[1].listeners["tenant_events"](made[1], 1, "tenant_events", _payload(T1, typ="conversation"))
            assert (await sub.get(0.1))["type"] == "conversation"
            await hub.stop()
        finally:
            hub_mod.asyncio.sleep = real_sleep
    asyncio.run(run())


class Req:
    def __init__(self, disconnect_after):
        self.n, self.limit = 0, disconnect_after

    async def is_disconnected(self):
        self.n += 1
        return self.n > self.limit


class Ctx:
    tenant_id = T1


def test_sse_stream_events_heartbeat_and_cleanup():
    async def run():
        hub = EventHub(connect=None)
        gen = stream(Req(disconnect_after=2), hub, Ctx(), heartbeat=0.02, max_seconds=5)
        out = [await gen.__anext__(), await gen.__anext__()]
        assert out[0].startswith(b"retry:") and b"event: ready" in out[1]
        assert hub.subscriber_count() == 1
        hub.dispatch(_payload(T1))
        hub.dispatch(_payload(T2))
        chunk = await gen.__anext__()
        assert chunk.startswith(b"event: message\n") and str(T1).encode() not in chunk
        assert await gen.__anext__() == b": ping\n\n"
        rest = [c async for c in gen]
        assert rest == [] and hub.subscriber_count() == 0
    asyncio.run(run())


def test_sse_format():
    assert sse_format({"type": "lead", "id": "x"}) == b'event: lead\ndata: {"type": "lead", "id": "x"}\n\n'
