"""Voice notes: source extraction, format detection, prompt hints, temp-file lifecycle, download, handler."""
import asyncio
import os
import stat
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.ai import queries as aq
from app.ai import transcription as tr
from app.ai.jobs import AIDeps, Job, PermanentJobError
from app.channels.meta_graph import MetaGraphClient, MetaGraphError
from app.core.config import get_settings
from app.core.crypto import encrypt_token
from app.llm.base import Transcript, Usage
from tests.unit.fakes import FakeResult

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
OGG = b"OggS" + b"\x00" * 60


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ source
def test_audio_source_whatsapp_and_messenger():
    wa = tr.audio_source("whatsapp", {"type": "audio", "audio": {"id": "M1", "mime_type": "audio/ogg", "voice": True}})
    assert wa == tr.AudioSource(media_id="M1", mime_type="audio/ogg")
    ms = tr.audio_source("messenger", {"message": {"mid": "m", "attachments": [
        {"type": "image", "payload": {"url": "https://cdn/x.jpg"}},
        {"type": "audio", "payload": {"url": "https://cdn/v.mp4"}}]}})
    assert ms == tr.AudioSource(url="https://cdn/v.mp4")


@pytest.mark.parametrize("channel,payload", [
    ("whatsapp", {"type": "text", "text": {"body": "hi"}}),
    ("whatsapp", {"audio": {}}),
    ("instagram", {"message": {"attachments": [{"type": "audio", "payload": {}}]}}),
    ("messenger", None),
    ("messenger", "not a dict"),
])
def test_audio_source_missing(channel, payload):
    assert tr.audio_source(channel, payload) is None


# ------------------------------------------------------------------ format
@pytest.mark.parametrize("mime,head,expected", [
    ("audio/ogg; codecs=opus", b"OggS\x00\x02", ".ogg"),
    (None, b"ID3\x04\x00", ".mp3"),
    (None, b"\xff\xfb\x90\x00", ".mp3"),                 # MPEG-1 Layer III frame
    (None, b"\x00\x00\x00\x20ftypM4A ", ".mp4"),
    (None, b"RIFF\x24\x00\x00\x00WAVEfmt ", ".wav"),
    (None, b"\x1a\x45\xdf\xa3\x01", ".webm"),
    (None, b"fLaC\x00", ".flac"),
    ("audio/mpeg", b"\x00\x01\x02\x03", ".mp3"),         # البايتات غير معروفة => الـ MIME
    ("audio/aac", b"\xff\xf1\x50\x80", None),            # AAC ADTS: غير مدعوم
    ("audio/amr", b"#!AMR\n", None),
    ("application/octet-stream", b"\x00\x01", None),
])
def test_audio_suffix(mime, head, expected):
    assert tr.audio_suffix(mime, head) == expected


# ------------------------------------------------------------------ prompt / text / wait
def test_prompt_tenant_terms_first_dedup_and_limit():
    p = tr.build_transcription_prompt(["عمرة رمضان - 15 يوم", "  فندق   دار الإيمان ", "طرابلس", "", None, "ع"])
    assert p.startswith(tr.PROMPT_INTRO)
    body = p[len(tr.PROMPT_INTRO):]
    assert body.split("، ")[:3] == ["عمرة رمضان - 15 يوم", "فندق دار الإيمان", "طرابلس"]
    assert body.count("طرابلس") == 1                      # موجودة في القائمة الثابتة أيضاً
    assert "ع،" not in body and len(p) <= tr.PROMPT_MAX_CHARS
    short = tr.build_transcription_prompt([f"برنامج رقم {i}" for i in range(100)], max_chars=120)
    assert len(short) <= 120 and "برنامج رقم 0" in short


def test_clean_transcript():
    assert tr.clean_transcript("  قداش\n\n عمرة   رمضان؟ ") == "قداش عمرة رمضان؟"
    assert tr.clean_transcript(None) == ""
    assert len(tr.clean_transcript("ا " * 5000)) == tr.TRANSCRIPT_MAX_CHARS


def test_transcription_blocks_reply_until_max_wait():
    fresh = {"created_at": NOW - timedelta(seconds=10)}
    stale = {"created_at": NOW - timedelta(seconds=60)}
    assert tr.transcription_blocks_reply([fresh], NOW, 45)
    assert tr.transcription_blocks_reply([stale, fresh], NOW, 45)
    assert not tr.transcription_blocks_reply([stale], NOW, 45)
    assert not tr.transcription_blocks_reply([], NOW, 45)


# ------------------------------------------------------------------ temp files
class RecordingTranscriber:
    model = "fake-transcribe"

    def __init__(self, text="نص", fail=False):
        self.text, self.fail, self.seen = text, fail, {}

    async def transcribe(self, *, audio_path, prompt, language):
        st = os.stat(audio_path)
        self.seen = {"path": audio_path, "exists": os.path.exists(audio_path), "mode": stat.S_IMODE(st.st_mode),
                     "content": open(audio_path, "rb").read(), "prompt": prompt, "language": language}
        if self.fail:
            raise RuntimeError("model down")
        return Transcript(text=self.text, usage=Usage(5, 2), model=self.model)


def test_tempfile_written_private_and_always_deleted(tmp_path):
    t = RecordingTranscriber("السلام عليكم")
    out = run(tr.transcribe_via_tempfile(OGG, ".ogg", transcriber=t, prompt="p", language="ar",
                                         tmp_dir=str(tmp_path / "media")))
    assert out.text == "السلام عليكم"
    assert t.seen["exists"] and t.seen["content"] == OGG and t.seen["path"].endswith(".ogg")
    assert t.seen["mode"] == 0o600 and os.path.basename(t.seen["path"]).startswith(tr.TMP_PREFIX)
    assert not os.path.exists(t.seen["path"])                              # حُذف بعد النجاح
    assert stat.S_IMODE(os.stat(tmp_path / "media").st_mode) == 0o700

    failing = RecordingTranscriber(fail=True)
    with pytest.raises(RuntimeError):
        run(tr.transcribe_via_tempfile(OGG, ".ogg", transcriber=failing, prompt=None, language="ar",
                                       tmp_dir=str(tmp_path / "media")))
    assert not os.path.exists(failing.seen["path"])                        # وحُذف بعد الفشل
    assert os.listdir(tmp_path / "media") == []


def test_sweep_removes_only_stale_voice_files(tmp_path):
    old, new, other = tmp_path / "voice-old.ogg", tmp_path / "voice-new.ogg", tmp_path / "keep.txt"
    for f in (old, new, other):
        f.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    os.utime(other, (1000, 1000))
    assert tr.sweep_tmp_dir(str(tmp_path), max_age_seconds=3600, now=1000 + 7200) == 1
    assert not old.exists() and new.exists() and other.exists()
    assert tr.sweep_tmp_dir(str(tmp_path / "missing")) == 0


# ------------------------------------------------------------------ download
class FakeGraph:
    def __init__(self, media=None, media_error=None, download_error=None):
        self.media = media or {"url": "https://lookaside/m1", "mime_type": "audio/ogg", "file_size": len(OGG)}
        self.media_error, self.download_error = media_error, download_error
        self.calls = []

    async def get_media(self, media_id, token):
        self.calls.append(("get_media", media_id, token))
        if self.media_error:
            raise self.media_error
        return self.media

    async def download(self, url, token, *, max_bytes):
        self.calls.append(("download", url, token, max_bytes))
        if self.download_error:
            raise self.download_error
        return OGG, "application/octet-stream"


def test_fetch_whatsapp_media_with_token():
    g = FakeGraph()
    data, mime = run(tr.fetch_audio(g, tr.AudioSource(media_id="M1"), "TOKEN", 1000))
    assert data == OGG and mime == "audio/ogg"
    assert g.calls == [("get_media", "M1", "TOKEN"), ("download", "https://lookaside/m1", "TOKEN", 1000)]


def test_fetch_messenger_url_without_token():
    g = FakeGraph()
    data, mime = run(tr.fetch_audio(g, tr.AudioSource(url="https://cdn/v.mp4"), None, 1000))
    assert g.calls == [("download", "https://cdn/v.mp4", None, 1000)] and mime == "application/octet-stream"


def test_fetch_declared_too_large_skips_download():
    g = FakeGraph(media={"url": "u", "file_size": 5000})
    with pytest.raises(PermanentJobError, match="too_large"):
        run(tr.fetch_audio(g, tr.AudioSource(media_id="M1"), "T", 1000))
    assert [c[0] for c in g.calls] == ["get_media"]


def test_fetch_errors_permanent_vs_retryable():
    expired = FakeGraph(media_error=MetaGraphError("not found", retryable=False, code=100, http_status=404))
    with pytest.raises(PermanentJobError):
        run(tr.fetch_audio(expired, tr.AudioSource(media_id="M1"), "T", 1000))
    flaky = FakeGraph(download_error=MetaGraphError("503", retryable=True, http_status=503))
    with pytest.raises(MetaGraphError):
        run(tr.fetch_audio(flaky, tr.AudioSource(media_id="M1"), "T", 1000))


def _graph(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MetaGraphClient(http, base_url="https://graph.test", api_version="v25.0")


def test_graph_download_sends_token_and_caps_size():
    seen = {}

    def ok(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, content=OGG, headers={"content-type": "audio/ogg"})

    assert run(_graph(ok).download("https://lookaside/x", "TOK", max_bytes=1000)) == (OGG, "audio/ogg")
    assert seen["auth"] == "Bearer TOK"

    def big(request):
        return httpx.Response(200, content=b"x" * 2000)          # content-length أكبر من الحد

    with pytest.raises(MetaGraphError) as e:
        run(_graph(big).download("https://cdn/x", None, max_bytes=1000))
    assert e.value.code == "too_large" and not e.value.retryable

    async def chunks():
        yield b"x" * 600
        yield b"x" * 600

    def streamed(request):                                         # بدون content-length
        return httpx.Response(200, content=chunks())

    with pytest.raises(MetaGraphError, match="too large"):
        run(_graph(streamed).download("https://cdn/x", None, max_bytes=1000))


@pytest.mark.parametrize("code,retryable", [(404, False), (403, False), (429, True), (500, True)])
def test_graph_download_http_errors(code, retryable):
    with pytest.raises(MetaGraphError) as e:
        run(_graph(lambda r: httpx.Response(code)).download("https://x", "T", max_bytes=10))
    assert e.value.retryable is retryable


# ------------------------------------------------------------------ handler
class HandlerDB:
    """جلسة وهمية: TRANSCRIPTION_SOURCE و TENANT_VOCABULARY و SET_MESSAGE_TRANSCRIPT."""

    def __init__(self, source):
        self.source, self.executed = source, []

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        if stmt is aq.TRANSCRIPTION_SOURCE:
            return FakeResult([self.source] if self.source else [])
        if stmt is aq.TENANT_VOCABULARY:
            return FakeResult([{"term": "عمرة رمضان"}, {"term": "فندق دار الإيمان"}])
        if stmt is aq.SET_MESSAGE_TRANSCRIPT:
            return FakeResult([{"conversation_id": "C1", "created_at": NOW}])
        return FakeResult()

    def calls_to(self, stmt):
        return [p for s, p in self.executed if s is stmt]


def _handler_env(monkeypatch, tmp_path, source, transcriber):
    db = HandlerDB(source)

    @asynccontextmanager
    async def session(tenant_id, user_id=None):
        yield db

    monkeypatch.setattr(tr, "tenant_session", session)
    settings = get_settings().model_copy(update={"media_tmp_dir": str(tmp_path)})
    deps = AIDeps(settings=settings, graph=FakeGraph(), transcriber=transcriber, extractor=None, embedder=None)
    job = Job(id=uuid4(), tenant_id=uuid4(), kind="transcribe", attempts=1, message_id=uuid4())
    return db, deps, job


def _source(**kw):
    base = {"id": "M", "conversation_id": "C1", "channel": "whatsapp", "msg_type": "audio", "text_content": None,
            "payload": {"audio": {"id": "MEDIA-1", "mime_type": "audio/ogg"}}, "created_at": NOW,
            "access_token_enc": encrypt_token("TOKEN")}
    return {**base, **kw}


def test_handler_writes_transcript_preview_and_uses_vocabulary(monkeypatch, tmp_path):
    t = RecordingTranscriber("  قداش   عمرة رمضان؟ ")
    db, deps, job = _handler_env(monkeypatch, tmp_path, _source(), t)
    result = run(tr.TranscribeHandler().run(deps, job))
    assert db.calls_to(aq.SET_MESSAGE_TRANSCRIPT) == [{"message_id": job.message_id, "text": "قداش عمرة رمضان؟"}]
    assert db.calls_to(aq.PREVIEW_TRANSCRIPT)[0]["preview"] == "🎤 قداش عمرة رمضان؟"
    assert "عمرة رمضان، فندق دار الإيمان" in t.seen["prompt"] and t.seen["language"] == "ar"
    assert deps.graph.calls[0] == ("get_media", "MEDIA-1", "TOKEN")        # التوكن المفكوك من القناة
    assert result.model == "fake-transcribe" and result.input_tokens == 5 and result.note is None
    assert os.listdir(tmp_path) == []


def test_handler_idempotent_and_empty(monkeypatch, tmp_path):
    db, deps, job = _handler_env(monkeypatch, tmp_path, _source(text_content="موجود"), RecordingTranscriber())
    assert run(tr.TranscribeHandler().run(deps, job)).note == "already_transcribed"
    assert deps.graph.calls == []

    db, deps, job = _handler_env(monkeypatch, tmp_path, _source(), RecordingTranscriber("   "))
    assert run(tr.TranscribeHandler().run(deps, job)).note == "empty transcript"
    assert db.calls_to(aq.SET_MESSAGE_TRANSCRIPT) == []


@pytest.mark.parametrize("source", [
    _source(payload={"type": "audio"}),                        # بدون media id
    _source(access_token_enc=b"not-a-fernet-token"),           # توكن القناة غير قابل للفك
])
def test_handler_permanent_failures(monkeypatch, tmp_path, source):
    db, deps, job = _handler_env(monkeypatch, tmp_path, source, RecordingTranscriber())
    with pytest.raises(PermanentJobError):
        run(tr.TranscribeHandler().run(deps, job))


def test_handler_unsupported_format(monkeypatch, tmp_path):
    db, deps, job = _handler_env(monkeypatch, tmp_path, _source(), RecordingTranscriber())

    async def aac(url, token, *, max_bytes):
        return b"\xff\xf1\x50\x80" + b"\x00" * 20, "audio/aac"

    deps.graph.download = aac
    with pytest.raises(PermanentJobError, match="unsupported"):
        run(tr.TranscribeHandler().run(deps, job))
