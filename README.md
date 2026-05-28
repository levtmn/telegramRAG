# TelegramRAG

*Read this in other languages: [Русский](README.ru.md)*

Local **Retrieval-Augmented Generation** over your Telegram group-chat exports. Ask questions in
natural language and get answers grounded in the actual messages, with sources. Everything runs on
your own machine — light enough for a **Raspberry Pi 5 (4 GB RAM)** — and uses only hosted APIs for
the heavy ML (no local GPU, no `torch`, no multi-gigabyte models).

Multiple chats can be indexed at once. Queries run **hybrid search** (dense vectors + BM25 keywords),
fused with Reciprocal Rank Fusion, reranked, and answered by Claude — streamed token-by-token to a
small web UI.

---

## Why hybrid + hosted APIs

| Layer | Choice | Reason |
|---|---|---|
| Vector DB | **Qdrant** (embedded, no server) | Cosine search + metadata filters; ARM64 wheel, runs in-process |
| Embeddings | **Cohere `embed-multilingual-v3.0`** | Purpose-built multilingual retrieval; handles informal/noisy Russian, slang, mixed scripts |
| Keyword | **`rank_bm25`** (pure Python) | Catches exact names/dates/IDs that dense vectors miss |
| Fusion | **Reciprocal Rank Fusion** | Parameter-free, robust merge of dense + sparse results |
| Reranker | **Cohere Rerank** | Best-in-class multilingual reranking; no ~1 GB local model |
| Answer | **Claude (`claude-sonnet-4-6`)** | Grounded answer generation from retrieved excerpts |

Offloading embeddings/rerank/LLM to APIs is what keeps this runnable on a Pi.

---

## How it works

Three stages — the first two are offline (batch indexing), the third is the live request path.

### 1. Ingest & chunk — `scripts/preprocess.py`
- Parses a Telegram `result.json` export, tolerant of **truncated** files (re-closes the JSON at the
  last complete message).
- Groups messages into **conversation threads**, not fixed windows: a reply-graph built from
  `reply_to_message_id` forms connected components, with a 30-minute time-gap fallback for unreplied
  messages. Target ~80–600 tokens per chunk.
- Each chunk carries a searchable header (`[Chat … | Date … | Authors …]`) plus metadata
  (`chat_name`, `date_start/end`, `authors`, `message_ids`). Output → `telegram-*/processed/chunks.json`.

### 2. Embed & index — `scripts/index.py`
- Embeds chunks with Cohere (1024-dim), then builds both a **Qdrant** collection (cosine) and a
  **BM25** index.
- Embeddings are **cached per chat** (`processed/embeddings.pkl`). Re-indexing after adding a new
  chat only embeds that chat — everything else loads instantly from cache.

### 3. Query & answer — `scripts/query.py` / `web/app.py`
```
query
 ├─ Cohere embed → Qdrant vector top-20 ─┐
 └─ BM25 keyword top-20 ─────────────────┴─ RRF merge → date filter
                                                 │
                                       Cohere Rerank → top-5
                                                 │
                                  Claude streaming answer (+ sources)
```

---

## Quick start

```bash
git clone https://github.com/levtmn/telegramRAG.git
cd telegramRAG

pip install -r requirements.txt --break-system-packages   # drop the flag in a venv

cp .env.example .env        # then add your COHERE_API_KEY and ANTHROPIC_API_KEY
```

Add a chat export (Telegram Desktop → ⋮ → *Export chat history* → **JSON**):

```bash
mkdir -p telegram-mygroup/origin
cp /path/to/result.json telegram-mygroup/origin/result.json
```

Index and query:

```bash
python3 scripts/preprocess.py            # parse all telegram-*/origin/result.json → chunks
python3 scripts/index.py                 # embed + build Qdrant + BM25 indexes
python3 scripts/query.py "what did Vasya say about the car?"
python3 scripts/query.py "F5 deadlines" --date-from 2025-03-01 --no-llm
```

> **First index is slow** (~50–60 min for ~11k chunks) because of the Cohere trial rate limit
> (100k tokens/min). The indexer paces itself and retries on 429. Re-indexes are instant thanks to
> the per-chat embedding cache.

Always run from the project root — the scripts use relative paths for `db/`, `bm25/`, and `telegram-*/`.

---

## Web interface

```bash
python3 web/app.py        # FastAPI + uvicorn on http://localhost:8080
```

- **Projects modal** — auto-detects every `telegram-*/origin/` directory, shows status
  (detected → preprocessed → indexed), one-click indexing with a live progress banner, archive
  upload (`.zip` / `.tar.gz`), editable display names, and a per-chat date range.
- **Query panel** — filter by chat and date range; Claude's answer streams in, with retrieved
  sources shown as collapsible chips.
- **Chat history** — conversations are saved to SQLite (`data/history.db`); resume past sessions
  from a sidebar, start fresh, clear all, or print/export to PDF.

The repository ships **with no chat data** — add your own. `db/`, `bm25/`, `data/`, and every
`telegram-*/` directory are gitignored.

---

## Deploy as a service

`deploy/` contains a systemd unit and an nginx config to run the app on boot and serve a static
status/landing page on port 80. See [`deploy/README.md`](deploy/README.md).

Two things to get right when self-hosting:
- **Don't serve static files from a `0700` home directory** — the web-server user can't traverse it.
  Put the landing page under `/var/www` (the deploy steps do this).
- **Serve over HTTPS / treat `localhost` carefully.** Some browser APIs (e.g. `crypto.randomUUID`)
  only exist in a *secure context* (HTTPS or `localhost`). Over plain HTTP on a LAN hostname they're
  undefined — the UI already includes a fallback, but TLS is recommended for any public deployment.

---

## Requirements

- Python 3.11+ (developed on 3.13)
- A Cohere API key (embeddings + rerank) and an Anthropic API key (Claude)
- ~2 GB RAM recommended (the indexing step is the memory peak); ARM64 / x86-64 both fine
- Dependencies: `fastapi`, `uvicorn`, `qdrant-client`, `rank-bm25`, `cohere`, `anthropic`,
  `python-dotenv`, `python-multipart` — all in `requirements.txt`, no compilation needed

---

## License

MIT
