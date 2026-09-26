"""Voice notes -> text (المرحلة 7a).

المسار: رسالة audio (ingest ينشئ مهمة transcribe) -> تنزيل الصوت من Meta -> ملف مؤقت 0600
-> نموذج التفريغ مع كلمات الوكالة كتلميحات -> حذف الملف دائماً -> النص في messages.text_content.

- الصوت نفسه لا يُخزَّن أبداً؛ النص فقط.
- لا transaction مفتوحة أثناء التنزيل أو التفريغ.
- رد البوت ينتظر التفريغ (transcription_blocks_reply) حتى حد أقصى، ثم يرد بما لديه.
- النص لا يُكتب في السجلات (بيانات زبائن).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.ai import queries as aq
from app.ai.jobs import AIDeps, Job, JobResult, PermanentJobError
from app.channels.meta_graph import MetaGraphClient, MetaGraphError
from app.core.config import Settings
from app.core.crypto import TokenDecryptionError, decrypt_token
from app.db.tenant import tenant_session
from app.llm.base import Transcript, TranscriptionClient

log = logging.getLogger(__name__)

TMP_PREFIX = "voice-"
POLL_SECONDS = 2.0                 # كل كم يعيد الـ worker فحص محادثة تنتظر التفريغ
TRANSCRIPT_MAX_CHARS = 4000
PROMPT_MAX_CHARS = 700
PROMPT_INTRO = "رسالة صوتية باللهجة الليبية من زبون لمكتب سفر وسياحة. كلمات قد ترد: "

# بعد كلمات الوكالة: مدن ليبية ومصطلحات الحج والعمرة الشائعة في الرسائل
LIBYAN_TERMS = (
    "طرابلس", "بنغازي", "مصراتة", "الزاوية", "زليتن", "الخمس", "سبها", "البيضاء", "درنة", "طبرق",
    "سرت", "غريان", "صبراتة", "ترهونة", "اجدابيا", "المرج", "الكفرة", "زوارة",
    "عمرة", "حج", "عمرة رمضان", "العشر الأواخر", "المولد", "مكة", "المدينة", "جدة", "الحرم",
    "رباعي", "ثلاثي", "ثنائي", "مفرد", "تأشيرة", "جواز", "عربون", "دينار", "قداش", "نبي نحجز",
)


# ------------------------------------------------------------------ source
@dataclass(frozen=True)
class AudioSource:
    media_id: str | None = None      # واتساب: يُطلب رابطه من Graph ثم يُنزّل بالتوكن
    url: str | None = None           # ماسنجر / إنستغرام: رابط المرفق مباشرة
    mime_type: str | None = None


def audio_source(channel: str, payload: Mapping[str, Any] | None) -> AudioSource | None:
    """payload = الرسالة الخام كما حفظها ingest (واتساب: كائن الرسالة، ماسنجر: حدث messaging)."""
    if not isinstance(payload, Mapping):
        return None
    if channel == "whatsapp":
        media = payload.get("audio") or payload.get("voice") or {}
        media_id = media.get("id") if isinstance(media, Mapping) else None
        return AudioSource(media_id=str(media_id), mime_type=media.get("mime_type")) if media_id else None
    message = payload.get("message") or {}
    for a in message.get("attachments") or []:
        if isinstance(a, Mapping) and a.get("type") == "audio":
            url = (a.get("payload") or {}).get("url")
            if url:
                return AudioSource(url=str(url))
    return None


# ------------------------------------------------------------------ format
_SUFFIX_BY_MIME = {
    "audio/ogg": ".ogg", "audio/opus": ".ogg", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/mp4": ".mp4", "video/mp4": ".mp4", "audio/m4a": ".m4a", "audio/x-m4a": ".m4a",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/webm": ".webm",
    "audio/flac": ".flac",
}


def audio_suffix(mime: str | None, head: bytes) -> str | None:
    """امتداد الملف المؤقت (يحدد الصيغة عند نموذج التفريغ). None = صيغة لا يدعمها (AAC خام، AMR).
    البايتات الأولى أولاً، ثم نوع الـ MIME."""
    if head.startswith(b"#!AMR"):
        return None
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
        return None                                  # AAC ADTS (رسائل صوتية من بعض الأجهزة)
    if head.startswith(b"OggS"):
        return ".ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return ".mp3"
    if head[4:8] == b"ftyp":
        return ".mp4"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return ".wav"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if head.startswith(b"fLaC"):
        return ".flac"
    base = (mime or "").split(";", 1)[0].strip().lower()
    return _SUFFIX_BY_MIME.get(base)


# ------------------------------------------------------------------ prompt / text
def build_transcription_prompt(tenant_terms: Iterable[str | None], max_chars: int = PROMPT_MAX_CHARS) -> str:
    """كلمات الوكالة أولاً (أسماء برامجها وفنادقها)، ثم القائمة الثابتة، بدون تكرار وضمن حد الطول."""
    seen: set[str] = set()
    parts: list[str] = []
    length = len(PROMPT_INTRO)
    for raw in [*tenant_terms, *LIBYAN_TERMS]:
        term = " ".join(str(raw or "").split())[:80]
        if len(term) < 2 or term in seen:
            continue
        extra = len(term) + (2 if parts else 0)
        if length + extra > max_chars:
            continue                                  # كلمة طويلة لا تتسع؛ قد تتسع أقصر منها
        seen.add(term)
        parts.append(term)
        length += extra
    return PROMPT_INTRO + "، ".join(parts)


def clean_transcript(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:TRANSCRIPT_MAX_CHARS]


def transcription_blocks_reply(pending: Iterable[Mapping[str, Any]], now: datetime,
                               max_wait_seconds: float) -> bool:
    """pending: مهام تفريغ لم تنتهِ لرسائل الزبون المعلّقة. ينتظر البوت ما دامت إحداها أحدث من الحد."""
    limit = timedelta(seconds=max_wait_seconds)
    return any(now - p["created_at"] < limit for p in pending)


# ------------------------------------------------------------------ temp files
def media_tmp_dir(settings: Settings) -> str:
    return settings.media_tmp_dir or os.path.join(tempfile.gettempdir(), "agent-media")


async def transcribe_via_tempfile(audio: bytes, suffix: str, *, transcriber: TranscriptionClient,
                                  prompt: str | None, language: str | None, tmp_dir: str) -> Transcript:
    """يكتب الصوت في ملف مؤقت (0600) ويمرره للنموذج، ثم يحذفه دائماً (نجاحاً أو فشلاً)."""
    os.makedirs(tmp_dir, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=suffix, dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        return await transcriber.transcribe(audio_path=path, prompt=prompt, language=language)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def sweep_tmp_dir(tmp_dir: str, max_age_seconds: float = 3600, now: float | None = None) -> int:
    """بقايا توقف مفاجئ بين الكتابة والحذف: تُحذف عند بدء الـ worker."""
    if not os.path.isdir(tmp_dir):
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed = 0
    for name in os.listdir(tmp_dir):
        path = os.path.join(tmp_dir, name)
        with contextlib.suppress(FileNotFoundError):
            if name.startswith(TMP_PREFIX) and os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
    return removed


# ------------------------------------------------------------------ download
async def fetch_audio(graph: MetaGraphClient, source: AudioSource, token: str | None,
                      max_bytes: int) -> tuple[bytes, str | None]:
    """MetaGraphError(retryable=False) => PermanentJobError. الأخطاء المؤقتة تمر لإعادة المحاولة."""
    try:
        if source.media_id:
            meta = await graph.get_media(source.media_id, token or "")
            url = meta.get("url")
            if not url:
                raise PermanentJobError("media url missing")
            if int(meta.get("file_size") or 0) > max_bytes:
                raise PermanentJobError(f"too_large: {meta.get('file_size')} bytes")
            data, ctype = await graph.download(str(url), token, max_bytes=max_bytes)
            return data, meta.get("mime_type") or source.mime_type or ctype
        data, ctype = await graph.download(str(source.url), None, max_bytes=max_bytes)
        return data, ctype or source.mime_type
    except MetaGraphError as exc:
        if exc.retryable:
            raise
        raise PermanentJobError(f"media: {exc}") from exc


# ------------------------------------------------------------------ job handler
class TranscribeHandler:
    kind = "transcribe"
    max_attempts = 3
    retry_base_seconds = 2.0         # سريعة: الزبون ينتظر الرد

    async def run(self, deps: AIDeps, job: Job) -> JobResult:
        settings = deps.settings
        async with tenant_session(job.tenant_id) as s:
            m = (await s.execute(aq.TRANSCRIPTION_SOURCE, {"message_id": job.message_id})).mappings().first()
            if m is None:
                return JobResult(note="message_deleted")
            if m["text_content"]:
                return JobResult(note="already_transcribed")
            terms = [r["term"] for r in (await s.execute(aq.TENANT_VOCABULARY)).mappings().all()]

        payload = m["payload"] if isinstance(m["payload"], dict) else json.loads(m["payload"] or "{}")
        source = audio_source(m["channel"], payload)
        if source is None:
            raise PermanentJobError("no audio in message")
        if deps.graph is None or deps.transcriber is None:
            raise PermanentJobError("transcription not configured")
        token = None
        if source.media_id:
            try:
                token = decrypt_token(m["access_token_enc"])
            except TokenDecryptionError as exc:
                raise PermanentJobError("channel token unavailable") from exc

        # ---- بدون transaction: تنزيل + تفريغ
        audio, mime = await fetch_audio(deps.graph, source, token, settings.voice_max_bytes)
        suffix = audio_suffix(mime, audio[:16])
        if suffix is None:
            raise PermanentJobError(f"unsupported audio format: {mime}")
        transcript = await transcribe_via_tempfile(
            audio, suffix, transcriber=deps.transcriber, prompt=build_transcription_prompt(terms),
            language=settings.transcription_language, tmp_dir=media_tmp_dir(settings))
        text = clean_transcript(transcript.text)

        if text:
            async with tenant_session(job.tenant_id) as s:
                row = (await s.execute(aq.SET_MESSAGE_TRANSCRIPT,
                                       {"message_id": job.message_id, "text": text})).mappings().first()
                if row is not None:
                    await s.execute(aq.PREVIEW_TRANSCRIPT, {
                        "conversation_id": row["conversation_id"], "created_at": row["created_at"],
                        "preview": f"🎤 {text}"})
        log.info("voice: message %s transcribed (%d chars, %d bytes %s)",
                 job.message_id, len(text), len(audio), suffix)
        return JobResult(model=transcript.model, input_tokens=transcript.usage.input_tokens,
                         output_tokens=transcript.usage.output_tokens,
                         note=None if text else "empty transcript")

    async def on_final_failure(self, deps: AIDeps, job: Job, error: str) -> None:
        # لا شيء: الرد ينتظر المهام غير المنتهية فقط، فيرد البوت الآن ويرى [رسالة صوتية]
        return None
