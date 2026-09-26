"""ai_jobs mechanics, staff-answer knowledge, and the OpenAI adapters used by phase 7a."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ai import jobs
from app.ai import knowledge as kn
from app.ai import queries as aq
from app.ai.jobs import AIDeps, Job, JobResult, PermanentJobError
from app.core.config import get_settings
from app.llm.base import LLMError
from app.llm.openai_client import OpenAIDocumentExtractor, OpenAITranscriptionClient
from tests.unit.fakes import FakeResult

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ knowledge suggestion
def _msgs(*spec):
    """spec: (direction, sender, text) الأقدم أولاً."""
    return [{"id": uuid4(), "direction": d, "sender_type": s, "msg_type": "text", "text_content": t,
             "created_at": T0 + timedelta(minutes=i)} for i, (d, s, t) in enumerate(spec)]


def test_suggest_question_block_before_staff_answer():
    m = _msgs(("inbound", "customer", "السلام عليكم"), ("outbound", "bot", "هلا بيك"),
              ("inbound", "customer", "هل تقبلوا"), ("inbound", "customer", "الدفع بالتقسيط؟"),
              ("outbound", "bot", "نحولك لموظف"),                       # رسالة التحويل تُتخطى
              ("outbound", "staff", "نعم على دفعتين"), ("outbound", "staff", "النصف عند الحجز"),
              ("inbound", "customer", "تمام"))
    s = kn.suggest_knowledge(m)
    assert s.question == "هل تقبلوا الدفع بالتقسيط؟"
    assert s.answer == "نعم على دفعتين\nالنصف عند الحجز" and s.message_id == m[5]["id"]
    # اختيار الرسالة الثانية من نفس الكتلة => نفس الاقتراح ونفس المعرّف (لا تُحفظ مرتين)
    assert kn.suggest_knowledge(m, m[6]["id"]) == s


def test_suggest_specific_reply_and_edge_cases():
    m = _msgs(("inbound", "customer", "سؤال أول؟"), ("outbound", "staff", "جواب أول"),
              ("inbound", "customer", "سؤال ثاني؟"), ("outbound", "staff", "جواب ثاني"))
    assert kn.suggest_knowledge(m).question == "سؤال ثاني؟"                       # آخر رد افتراضياً
    first = kn.suggest_knowledge(m, m[1]["id"])
    assert (first.question, first.answer) == ("سؤال أول؟", "جواب أول")
    assert kn.suggest_knowledge(m, uuid4()) is None                             # ليس رد موظف في المحادثة
    assert kn.suggest_knowledge(_msgs(("inbound", "customer", "سؤال"), ("outbound", "bot", "رد"))) is None
    assert kn.suggest_knowledge(_msgs(("outbound", "staff", "رد بدون سؤال"))) is None
    voice = _msgs(("inbound", "customer", "  قداش   العمرة؟ "), ("outbound", "staff", "4500"))
    voice[0]["msg_type"] = "audio"                                              # نص التفريغ الصوتي
    assert kn.suggest_knowledge(voice).question == "قداش العمرة؟"


def test_source_ref_and_title():
    c, m = uuid4(), uuid4()
    assert kn.knowledge_source_ref(c, m) == f"conversation:{c}:message:{m}"
    assert kn.knowledge_source_ref(c, None) == f"conversation:{c}"
    assert kn.chunk_title("سؤال\n\n طويل " + "x" * 300) == ("سؤال طويل " + "x" * 300)[:200]


# ------------------------------------------------------------------ job mechanics
class JobDB:
    def __init__(self, job_row=None):
        self.job_row, self.executed = job_row, []

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        if stmt is aq.LOAD_AI_JOB:
            return FakeResult([self.job_row] if self.job_row else [])
        if stmt is aq.KNOWLEDGE_FOR_EMBEDDING:
            return FakeResult([{"id": "K", "title": "سؤال", "content": "جواب", "is_active": True}])
        return FakeResult()

    def calls_to(self, stmt):
        return [p for s, p in self.executed if s is stmt]


def _patch_sessions(monkeypatch, db, *modules):
    @asynccontextmanager
    async def session(tenant_id, user_id=None):
        yield db

    for mod in modules:
        monkeypatch.setattr(mod, "tenant_session", session)


class ScriptedHandler:
    kind = "transcribe"
    max_attempts = 3
    retry_base_seconds = 2.0

    def __init__(self, outcome):
        self.outcome, self.failed_with = outcome, None

    async def run(self, deps, job):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def on_final_failure(self, deps, job, error):
        self.failed_with = error


DEPS = AIDeps(settings=get_settings(), graph=None, transcriber=None, extractor=None, embedder=None)


def _job_row(attempts):
    return {"id": uuid4(), "kind": "transcribe", "attempts": attempts, "message_id": uuid4(),
            "catalog_import_id": None, "knowledge_chunk_id": None, "created_at": T0}


@pytest.mark.parametrize("outcome,attempts,expect", [
    (JobResult(model="m", input_tokens=5, note="empty transcript"), 1, "done"),
    (PermanentJobError("unsupported"), 1, "failed"),
    (RuntimeError("network"), 1, "retry"),
    (RuntimeError("network"), 3, "failed"),              # آخر محاولة
])
def test_execute_outcomes(monkeypatch, outcome, attempts, expect):
    db = JobDB(_job_row(attempts))
    _patch_sessions(monkeypatch, db, jobs)
    h = ScriptedHandler(outcome)
    run(jobs.run_one(DEPS, {"transcribe": h}, uuid4(), uuid4()))
    finish, retry = db.calls_to(aq.FINISH_AI_JOB), db.calls_to(aq.RETRY_AI_JOB)
    if expect == "done":
        assert finish[0]["status"] == "done" and finish[0]["error"] == "empty transcript"
        assert finish[0]["model"] == "m" and finish[0]["input_tokens"] == 5 and retry == [] and h.failed_with is None
    elif expect == "retry":
        assert retry and 1.5 <= retry[0]["delay_seconds"] <= 2.5 and finish == [] and h.failed_with is None
    else:
        assert finish[0]["status"] == "failed" and h.failed_with is not None and retry == []
        assert finish[0]["duration_ms"] >= 0


def test_run_one_missing_job_is_noop(monkeypatch):
    db = JobDB(None)
    _patch_sessions(monkeypatch, db, jobs)
    h = ScriptedHandler(JobResult())
    run(jobs.run_one(DEPS, {"transcribe": h}, uuid4(), uuid4()))
    assert db.calls_to(aq.FINISH_AI_JOB) == []


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self):
        self.inputs = []

    async def embed(self, texts):
        self.inputs.extend(texts)
        return [[0.25, -0.5]]


def test_embed_handler_writes_vector(monkeypatch):
    db = JobDB()
    _patch_sessions(monkeypatch, db, kn)
    emb = FakeEmbedder()
    deps = AIDeps(settings=get_settings(), graph=None, transcriber=None, extractor=None, embedder=emb)
    job = Job(id=uuid4(), tenant_id=uuid4(), kind="embed_knowledge", attempts=1, knowledge_chunk_id=uuid4())
    result = run(kn.EmbedKnowledgeHandler().run(deps, job))
    assert emb.inputs == ["سؤال\nجواب"] and result.model == "fake-embed"
    assert db.calls_to(aq.SET_KNOWLEDGE_EMBEDDING)[0]["embedding"] == "[0.2500000,-0.5000000]"


# ------------------------------------------------------------------ OpenAI adapters
class FakeTranscriptions:
    def __init__(self, fail=False):
        self.fail, self.kwargs = fail, None

    async def create(self, **kwargs):
        self.kwargs = {**kwargs, "file_name": kwargs["file"].name}
        if self.fail:
            raise RuntimeError("rate limited")
        return SimpleNamespace(text=" قداش العمرة؟ ",
                               usage=SimpleNamespace(type="tokens", input_tokens=40, output_tokens=6))


def test_transcription_adapter(tmp_path):
    audio = tmp_path / "voice-1.ogg"
    audio.write_bytes(b"OggS")
    tx = FakeTranscriptions()
    client = OpenAITranscriptionClient(SimpleNamespace(audio=SimpleNamespace(transcriptions=tx)),
                                       model="gpt-4o-transcribe")
    out = run(client.transcribe(audio_path=str(audio), prompt="كلمات", language="ar"))
    assert out.text == "قداش العمرة؟" and out.usage.input_tokens == 40 and out.model == "gpt-4o-transcribe"
    assert tx.kwargs["model"] == "gpt-4o-transcribe" and tx.kwargs["language"] == "ar"
    assert tx.kwargs["prompt"] == "كلمات" and tx.kwargs["file_name"].endswith(".ogg")
    failing = OpenAITranscriptionClient(SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions(True))),
                                        model="m")
    with pytest.raises(LLMError):
        run(failing.transcribe(audio_path=str(audio), prompt=None, language=None))


def _completion(content, finish="stop", refusal=None):
    msg = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], model="gpt-4o-2026",
                           usage=SimpleNamespace(prompt_tokens=1500, completion_tokens=700))


class FakeCompletions:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _extractor(response):
    comp = FakeCompletions(response)
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return OpenAIDocumentExtractor(sdk, model="gpt-4o", max_output_tokens=8000), comp


def test_document_extractor_image_and_pdf_parts():
    ex, comp = _extractor(_completion(json.dumps({"packages": [], "notes": None})))
    out = run(ex.extract(system="S", instruction="I", document=b"\x89PNG", mime_type="image/png",
                         schema_name="travel_catalog", schema={"type": "object"}))
    assert out.data == {"packages": [], "notes": None} and out.usage.input_tokens == 1500 and out.model == "gpt-4o-2026"
    k = comp.kwargs
    assert k["response_format"]["json_schema"]["strict"] is True and k["temperature"] == 0
    part = k["messages"][1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    assert part["image_url"]["detail"] == "high"
    pdf = OpenAIDocumentExtractor.document_part(b"%PDF-1.7", "application/pdf")
    assert pdf["type"] == "file" and pdf["file"]["file_data"].startswith("data:application/pdf;base64,")


@pytest.mark.parametrize("response", [
    _completion("not json"), _completion("[1, 2]"), _completion("{}", finish="length"),
    _completion(None, refusal="cannot help"),
])
def test_document_extractor_errors(response):
    ex, _ = _extractor(response)
    with pytest.raises(LLMError):
        run(ex.extract(system="S", instruction="I", document=b"x", mime_type="image/jpeg",
                       schema_name="n", schema={}))
