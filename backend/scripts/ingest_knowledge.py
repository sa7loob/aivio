"""Load a tenant's knowledge base from YAML (admin task, runs as app_owner).

    python -m scripts.ingest_knowledge --tenant-slug noor-travel --file knowledge/noor.yaml

إعادة التشغيل على نفس الملف تستبدل مقاطعه القديمة (source_ref = اسم الملف).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.agent.knowledge_ingest import chunk_entries, embed_chunks, insert_chunks
from app.core.config import get_settings
from app.llm.openai_client import OpenAIEmbeddingClient, create_openai_sdk_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant-slug", required=True)
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    settings = get_settings()
    owner_url = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not owner_url or settings.openai_api_key is None:
        print("MIGRATIONS_DATABASE_URL and OPENAI_API_KEY are required", file=sys.stderr)
        return 2

    path = pathlib.Path(args.file)
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    chunks = chunk_entries(entries)

    sdk = create_openai_sdk_client(settings.openai_api_key.get_secret_value(),
                                   timeout=settings.llm_timeout_seconds, max_retries=settings.llm_max_retries)
    embedder = OpenAIEmbeddingClient(sdk, model=settings.embedding_model,
                                     dimensions=settings.embedding_dimensions)
    asyncio.run(embed_chunks(chunks, embedder))

    with create_engine(owner_url).begin() as conn:
        tenant_id = conn.execute(text("SELECT id FROM tenants WHERE slug = :s"),
                                 {"s": args.tenant_slug}).scalar_one_or_none()
        if tenant_id is None:
            print(f"tenant {args.tenant_slug} not found", file=sys.stderr)
            return 1
        n = insert_chunks(conn, tenant_id, chunks, source_ref=path.name,
                          embedding_model=settings.embedding_model)
    print(f"{n} chunks from {len(entries)} entries -> {args.tenant_slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
