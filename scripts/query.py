#!/usr/bin/env python3
"""
Hybrid search: Qdrant vector + BM25, then Cohere rerank, then Claude answer.

Usage:
    python3 scripts/query.py "что писал Вася про машину"
    python3 scripts/query.py "ф5 сроки" --author "Таня" --date-from 2025-03-01
    python3 scripts/query.py "виза" --no-llm   # show raw chunks, skip Claude
"""

import argparse
import os
import pickle
import re
import sys
from pathlib import Path

import anthropic
import cohere
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny

load_dotenv()

COLLECTION = "telegram_rag"
EMBED_MODEL = "embed-multilingual-v3.0"
RERANK_MODEL = "rerank-multilingual-v3.0"
CLAUDE_MODEL = "claude-sonnet-4-6"

co = cohere.Client(os.environ["COHERE_API_KEY"])
ac = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── helpers ──────────────────────────────────────────────────────────────────

def embed_query(text: str) -> list[float]:
    return co.embed(texts=[text], model=EMBED_MODEL, input_type="search_query").embeddings[0]


def tokenize(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", text.lower())


def _check_index_exists() -> None:
    if not Path("db/qdrant").exists():
        print("Index not found. Run: python3 scripts/index.py")
        sys.exit(1)
    if not Path("bm25/index.pkl").exists():
        print("BM25 index not found. Run: python3 scripts/index.py")
        sys.exit(1)


# ── search stages ─────────────────────────────────────────────────────────────

def vector_search(query_vec: list[float], top_k: int, author: str | None) -> list[dict]:
    qdrant_filter = None
    if author:
        qdrant_filter = Filter(
            must=[FieldCondition(key="authors", match=MatchAny(any=[author]))]
        )

    client = QdrantClient(path="db/qdrant")
    results = client.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=top_k,
        query_filter=qdrant_filter,
        with_payload=True,
    )
    client.close()
    return [r.payload for r in results.points]


def bm25_search(query: str, top_k: int, author: str | None) -> list[dict]:
    with open("bm25/index.pkl", "rb") as f:
        data = pickle.load(f)

    scores = data["bm25"].get_scores(tokenize(query))
    chunks = data["chunks"]

    ranked = sorted(
        (i for i in range(len(scores)) if scores[i] > 0),
        key=lambda i: scores[i],
        reverse=True,
    )

    results = []
    for i in ranked:
        c = chunks[i]
        if author and author not in c["authors"]:
            continue
        results.append(c)
        if len(results) >= top_k:
            break

    return results


def rrf_merge(lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}

    for ranked in lists:
        for rank, chunk in enumerate(ranked):
            cid = chunk["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            by_id[cid] = chunk

    return [by_id[cid] for cid in sorted(scores, key=lambda c: scores[c], reverse=True)]


def date_filter(chunks: list[dict], date_from: str | None, date_to: str | None) -> list[dict]:
    if not date_from and not date_to:
        return chunks
    filtered = []
    for c in chunks:
        start = c["date_start"][:10]
        if date_from and start < date_from:
            continue
        if date_to and start > date_to:
            continue
        filtered.append(c)
    return filtered


def rerank(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    if not chunks:
        return []
    resp = co.rerank(
        query=query,
        documents=[c["text"] for c in chunks],
        model=RERANK_MODEL,
        top_n=min(top_n, len(chunks)),
    )
    return [chunks[r.index] for r in resp.results]


def answer_with_claude(query: str, chunks: list[dict]) -> str:
    context_blocks = []
    for c in chunks:
        block = (
            f"[{c['chat_name']} | {c['date_start'][:10]} | {', '.join(c['authors'])}]\n"
            f"{c['text']}"
        )
        context_blocks.append(block)

    context = "\n\n---\n\n".join(context_blocks)

    prompt = (
        "You are an assistant that answers questions based on Telegram chat history. "
        "Use ONLY the provided excerpts. If the answer is not there, say so clearly. "
        "Answer in the same language as the question.\n\n"
        f"Chat excerpts:\n{context}\n\n"
        f"Question: {query}"
    )

    msg = ac.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


# ── main ──────────────────────────────────────────────────────────────────────

def search(
    query: str,
    author: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 20,
    top_n: int = 5,
) -> list[dict]:
    query_vec = embed_query(query)

    vec_results = vector_search(query_vec, top_k=top_k, author=author)
    bm25_results = bm25_search(query, top_k=top_k, author=author)

    merged = rrf_merge([vec_results, bm25_results])
    merged = date_filter(merged, date_from, date_to)[:top_k]

    return rerank(query, merged, top_n=top_n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--author", help="Filter by exact author display name")
    parser.add_argument("--date-from", dest="date_from", help="YYYY-MM-DD")
    parser.add_argument("--date-to", dest="date_to", help="YYYY-MM-DD")
    parser.add_argument("--no-llm", dest="no_llm", action="store_true",
                        help="Print raw chunks only, skip Claude")
    args = parser.parse_args()

    _check_index_exists()

    chunks = search(
        args.query,
        author=args.author,
        date_from=args.date_from,
        date_to=args.date_to,
    )

    if not chunks:
        print("No results found.")
        sys.exit(0)

    if args.no_llm:
        for i, c in enumerate(chunks, 1):
            print(f"\n{'─'*60}")
            print(f"Result {i}  [{c['chat_name']} | {c['date_start'][:10]}]")
            print(f"Authors: {', '.join(c['authors'])}")
            print()
            print(c["text"])
    else:
        print(answer_with_claude(args.query, chunks))
