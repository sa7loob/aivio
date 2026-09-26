"""Knowledge base ingestion: YAML entries -> chunks -> embeddings -> knowledge_chunks.

صيغة الملف (يكتبه صاحب الوكالة أو نحن أثناء الإعداد):
    - title: سياسة الإلغاء والاسترجاع
      source_type: policy            # faq | policy | package | document | manual
      package_code: UMR-MAWLID       # اختياري: يربط المعلومة ببرنامج
      content: |
        نص حر بالعربي...
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.llm.base import EmbeddingClient

MAX_CHUNK_CHARS = 900
EMBED_BATCH = 64


@dataclass
class Chunk:
    title: str
    content: str
    source_type: str
    package_code: str | None
    embedding: list[float] | None = None

    @property
    def embed_input(self) -> str:
        return f"{self.title}\n{self.content}"   # العنوان يحسّن التطابق الدلالي


def chunk_entries(entries: list[dict[str, Any]]) -> list[Chunk]:
    """تقسيم حسب الفقرات مع دمج الفقرات الصغيرة حتى MAX_CHUNK_CHARS."""
    chunks: list[Chunk] = []
    for e in entries:
        title = str(e["title"]).strip()
        source_type = e.get("source_type", "faq")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", str(e["content"])) if p.strip()]
        buf = ""
        for p in paragraphs:
            if buf and len(buf) + len(p) + 1 > MAX_CHUNK_CHARS:
                chunks.append(Chunk(title, buf, source_type, e.get("package_code")))
                buf = ""
            buf = f"{buf}\n{p}".strip()
            while len(buf) > MAX_CHUNK_CHARS:     # فقرة طويلة جداً
                cut = buf.rfind(" ", 0, MAX_CHUNK_CHARS)
                cut = cut if cut > 0 else MAX_CHUNK_CHARS
                chunks.append(Chunk(title, buf[:cut].strip(), source_type, e.get("package_code")))
                buf = buf[cut:].strip()
        if buf:
            chunks.append(Chunk(title, buf, source_type, e.get("package_code")))
    return chunks


async def embed_chunks(chunks: list[Chunk], embedder: EmbeddingClient) -> None:
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        vectors = await embedder.embed([c.embed_input for c in batch])
        for c, v in zip(batch, vectors):
            c.embedding = v


def insert_chunks(conn: Any, tenant_id: Any, chunks: list[Chunk], *, source_ref: str,
                  embedding_model: str, replace: bool = True) -> int:
    """يُنفّذ بدور app_owner (سكربت إداري) لذلك tenant_id صريح."""
    if replace:
        conn.execute(text("DELETE FROM knowledge_chunks WHERE tenant_id = :t AND source_ref = :r"),
                     {"t": tenant_id, "r": source_ref})
    for c in chunks:
        conn.execute(text("""
            INSERT INTO knowledge_chunks (tenant_id, source_type, source_ref, package_id, title,
                                          content, embedding, embedding_model)
            VALUES (:t, :source_type, :ref,
                    (SELECT id FROM packages WHERE tenant_id = :t AND code = :code),
                    :title, :content, CAST(:emb AS vector), :model)
        """), {
            "t": tenant_id, "source_type": c.source_type, "ref": source_ref, "code": c.package_code,
            "title": c.title, "content": c.content, "model": embedding_model,
            "emb": "[" + ",".join(f"{x:.7f}" for x in c.embedding) + "]" if c.embedding else None,
        })
    return len(chunks)
