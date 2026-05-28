#!/usr/bin/env python3
"""
Embeds all processed chunks with Cohere and stores them in Qdrant + BM25.

Embedding results are cached per project in processed/embeddings.pkl so
re-indexing after adding a new chat only embeds the new chat's chunks.

Usage:
    python3 scripts/index.py                  # embed new + use cache for rest
    python3 scripts/index.py --clear-cache    # force re-embed everything
"""

import argparse
import json
import os
import pickle
import re
import time
from pathlib import Path

import cohere
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from rank_bm25 import BM25Okapi

load_dotenv()

COLLECTION = "telegram_rag"
EMBED_MODEL = "embed-multilingual-v3.0"
EMBED_DIM = 1024
BATCH_SIZE = 96
# Target 80k tokens/min — safely below Cohere trial limit of 100k/min.
# At avg 400 tokens/chunk × 96 chunks = 38,400 tokens/batch → ~29s sleep per batch.
# With cache, this only applies when embedding a project for the first time.
_TOKEN_RATE_TARGET = 1_350   # tokens per second  (= ~81k/min)
RATE_LIMIT_WAIT = 65         # seconds to pause on a 429

co = cohere.Client(os.environ["COHERE_API_KEY"])


# ── low-level embedding ───────────────────────────────────────────────────────

def _embed_texts(texts: list[str], on_progress=None) -> list[list[float]]:
    """Embed with rate-limit-aware pacing. Retries on 429."""
    embeddings: list[list[float]] = []
    i = 0
    while i < len(texts):
        batch = texts[i : i + BATCH_SIZE]
        try:
            resp = co.embed(texts=batch, model=EMBED_MODEL, input_type="search_document")
            embeddings.extend(resp.embeddings)
            i += BATCH_SIZE
            if on_progress:
                on_progress(min(i, len(texts)), len(texts))
            if i < len(texts):
                batch_tokens = sum(len(t) for t in batch) // 4
                time.sleep(max(1.0, batch_tokens / _TOKEN_RATE_TARGET))
        except Exception as e:
            if "429" in str(e) or "rate limit" in str(e).lower():
                print(f"\n  Rate limit — waiting {RATE_LIMIT_WAIT}s...")
                time.sleep(RATE_LIMIT_WAIT)
                # retry same batch (i not advanced)
            else:
                raise
    return embeddings


# ── per-project embedding cache ───────────────────────────────────────────────

def _cache_path(project_dir: Path) -> Path:
    return project_dir / "processed" / "embeddings.pkl"


def _load_cache(project_dir: Path, expected: int) -> list[list[float]] | None:
    path = _cache_path(project_dir)
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = pickle.load(f)
    return data if len(data) == expected else None


def _save_cache(project_dir: Path, embeddings: list[list[float]]) -> None:
    with open(_cache_path(project_dir), "wb") as f:
        pickle.dump(embeddings, f)


# ── public API ────────────────────────────────────────────────────────────────

def load_all_chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(Path(".").glob("telegram-*/processed/chunks.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        chunks.extend(batch)
        print(f"  Loaded {len(batch):>5} chunks from {path}")
    return chunks


def get_all_embeddings(
    on_progress=None,
) -> tuple[list[dict], list[list[float]]]:
    """
    Returns (chunks, embeddings) for all processed projects.
    Uses per-project cache; only calls Cohere for new/changed projects.
    on_progress(done: int, total: int) is called during active embedding.
    """
    # First pass: figure out what actually needs embedding
    projects: list[tuple[Path, list[dict], list[list[float]] | None]] = []
    for path in sorted(Path(".").glob("telegram-*/processed/chunks.json")):
        project_dir = path.parent.parent
        chunks = json.loads(path.read_text(encoding="utf-8"))
        cached = _load_cache(project_dir, len(chunks))
        projects.append((project_dir, chunks, cached))

    total_new = sum(len(c) for _, c, cached in projects if cached is None)
    embed_offset = 0
    all_chunks: list[dict] = []
    all_embeddings: list[list[float]] = []

    for project_dir, chunks, cached in projects:
        if cached is not None:
            print(f"  {project_dir.name}: cache hit ({len(chunks)} chunks)")
            all_chunks.extend(chunks)
            all_embeddings.extend(cached)
        else:
            print(f"  {project_dir.name}: embedding {len(chunks)} chunks...")
            base = embed_offset

            def _prog(done: int, total: int, _b: int = base, _t: int = total_new) -> None:
                if on_progress:
                    on_progress(_b + done, _t)

            texts = [c["text"] for c in chunks]
            embs = _embed_texts(texts, on_progress=_prog)
            _save_cache(project_dir, embs)
            embed_offset += len(chunks)
            all_chunks.extend(chunks)
            all_embeddings.extend(embs)

    return all_chunks, all_embeddings


def build_qdrant(chunks: list[dict], embeddings: list[list[float]], rebuild: bool) -> None:
    Path("db").mkdir(exist_ok=True)
    client = QdrantClient(path="db/qdrant")

    if rebuild and client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )

    points = [
        PointStruct(
            id=i,
            vector=emb,
            payload={k: c[k] for k in ("chunk_id", "chat_id", "chat_name",
                                        "date_start", "date_end", "authors",
                                        "message_ids", "text")},
        )
        for i, (c, emb) in enumerate(zip(chunks, embeddings))
    ]
    for i in range(0, len(points), 200):
        client.upsert(collection_name=COLLECTION, points=points[i : i + 200])
    print(f"  Qdrant: {len(points)} vectors stored")
    client.close()


def build_bm25(chunks: list[dict]) -> None:
    Path("bm25").mkdir(exist_ok=True)

    def tokenize(text: str) -> list[str]:
        return re.findall(r"[а-яёa-z0-9]+", text.lower())

    bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])
    with open("bm25/index.pkl", "wb") as f:
        pickle.dump({"bm25": bm25, "chunks": chunks}, f)
    print(f"  BM25: {len(chunks)} docs indexed")


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete all cached embeddings and re-embed from scratch")
    args = parser.parse_args()

    if args.clear_cache:
        for f in Path(".").glob("telegram-*/processed/embeddings.pkl"):
            f.unlink()
            print(f"Cleared: {f}")

    print("Loading embeddings (cache-aware)...")
    chunks, embeddings = get_all_embeddings()
    if not chunks:
        print("No chunks found. Run preprocess.py first.")
        raise SystemExit(1)
    print(f"\n  Total: {len(chunks)} chunks")

    print("Building Qdrant index...")
    build_qdrant(chunks, embeddings, rebuild=True)

    print("Building BM25 index...")
    build_bm25(chunks)

    print("\nDone.")
