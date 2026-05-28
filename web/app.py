#!/usr/bin/env python3
"""
FastAPI web server for TelegramRAG.

Run from project root:
    python3 web/app.py

Access at http://rpi.local:8080

Optional push notifications (mobile):
    Add NTFY_TOPIC=your-unique-topic to .env, install the ntfy app, subscribe.
"""

import asyncio
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

import anthropic
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from scripts.index import build_bm25, build_qdrant, get_all_embeddings
from scripts.preprocess import process_export
from scripts.query import bm25_search, date_filter, embed_query, rerank, rrf_merge, vector_search

app = FastAPI(title="TelegramRAG")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ac = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

CLAUDE_MODEL = "claude-sonnet-4-6"
HTML = Path(__file__).parent / "templates" / "index.html"
DB_PATH = ROOT / "data" / "history.db"


# ── request/response models ───────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    date_from: str | None = None
    date_to: str | None = None
    chat_id: str | None = None
    session_id: str | None = None


class MetadataUpdate(BaseModel):
    display_name: str


# ── SQLite history ─────────────────────────────────────────────────────────────

def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            title      TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            role       TEXT,
            content    TEXT,
            sources    TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_session(conn: sqlite3.Connection, session_id: str, title: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, title, created_at) VALUES (?, ?, ?)",
        (session_id, title[:80], _now()),
    )
    conn.commit()


def _save_exchange(session_id: str, query: str, answer: str, chunks: list[dict]) -> None:
    sources_json = json.dumps(
        [{"chat_name": c["chat_name"], "date_start": c["date_start"][:10],
          "authors": c["authors"], "text": c["text"]} for c in chunks],
        ensure_ascii=False,
    )
    conn = _db()
    try:
        _ensure_session(conn, session_id, query)
        now = _now()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, sources, created_at) VALUES (?,?,?,?,?)",
            (session_id, "user", query, None, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, sources, created_at) VALUES (?,?,?,?,?)",
            (session_id, "assistant", answer, sources_json, now),
        )
        conn.commit()
    finally:
        conn.close()


_init_db()


# ── indexing background state ─────────────────────────────────────────────────

_idx: dict = {
    "running": False,
    "project": None,
    "step": None,
    "progress": 0,
    "total": 0,
    "message": "",
    "error": None,
    "completed": False,
    "new_chunks": 0,
    "total_chunks": 0,
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_headers() -> dict:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _ntfy(message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=message.encode(),
                headers={"Title": "TelegramRAG", "Priority": "default"},
            ),
            timeout=5,
        )
    except Exception:
        pass


def _slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return re.sub(r"-+", "-", name).strip("-") or "chat"


def _read_metadata(project_dir: Path) -> dict:
    meta_path = project_dir / "metadata.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def scan_projects() -> list[dict]:
    projects = []
    for origin_dir in sorted(ROOT.glob("telegram-*/origin")):
        project_dir = origin_dir.parent
        name = project_dir.name.removeprefix("telegram-")
        processed = project_dir / "processed" / "chunks.json"
        cache = project_dir / "processed" / "embeddings.pkl"
        meta = _read_metadata(project_dir)

        chunk_count = 0
        chat_name = None
        date_min = None
        date_max = None

        if processed.exists():
            try:
                chunks = json.loads(processed.read_text(encoding="utf-8"))
                chunk_count = len(chunks)
                if chunks:
                    chat_name = chunks[0].get("chat_name")
                    dates_start = [c["date_start"][:10] for c in chunks if c.get("date_start")]
                    dates_end   = [c["date_end"][:10]   for c in chunks if c.get("date_end")]
                    if dates_start:
                        date_min = min(dates_start)
                    if dates_end:
                        date_max = max(dates_end)
            except Exception:
                pass

        display_name = meta.get("display_name") or chat_name or name

        projects.append({
            "name": name,
            "dir": project_dir.name,
            "display_name": display_name,
            "chat_name": chat_name,
            "has_export": (origin_dir / "result.json").exists(),
            "preprocessed": processed.exists(),
            "chunk_count": chunk_count,
            "cached": cache.exists(),
            "indexed": processed.exists() and (ROOT / "db" / "qdrant").exists(),
            "date_min": date_min,
            "date_max": date_max,
        })
    return projects


# ── background indexing ───────────────────────────────────────────────────────

async def _run_indexing(project_name: str) -> None:
    def upd(**kw: object) -> None:
        _idx.update(kw)

    upd(running=True, project=project_name, error=None, completed=False,
        step="preprocess", progress=0, total=0, message="Parsing messages…")
    try:
        origin = ROOT / f"telegram-{project_name}" / "origin" / "result.json"
        if not origin.exists():
            raise FileNotFoundError(f"result.json not found in telegram-{project_name}/origin/")

        await asyncio.to_thread(process_export, origin)
        chunks_path = ROOT / f"telegram-{project_name}" / "processed" / "chunks.json"
        new_count = len(json.loads(chunks_path.read_text(encoding="utf-8")))
        upd(message=f"Parsed → {new_count} chunks")

        def _on_embed(done: int, total: int) -> None:
            upd(step="embed", progress=done, total=total,
                message=f"Embedding {done} / {total} chunks…")

        upd(step="embed", message="Checking embedding cache…")
        all_chunks, all_embeddings = await asyncio.to_thread(get_all_embeddings, _on_embed)

        upd(step="qdrant", progress=0, total=0,
            message=f"Building vector DB ({len(all_chunks)} chunks)…")
        await asyncio.to_thread(build_qdrant, all_chunks, all_embeddings, True)

        upd(step="bm25", message="Building keyword index…")
        await asyncio.to_thread(build_bm25, all_chunks)

        upd(running=False, completed=True, step="done",
            message=f"Ready — {len(all_chunks)} chunks indexed.",
            new_chunks=new_count, total_chunks=len(all_chunks))
        _ntfy(f"Indexing complete! {len(all_chunks)} chunks ready.")

    except Exception as exc:
        upd(running=False, step="error", error=str(exc))
        _ntfy(f"Indexing failed: {exc}")


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(HTML)


@app.get("/api/projects")
async def list_projects():
    return scan_projects()


@app.get("/api/status")
async def status():
    projects = scan_projects()
    return {
        "projects": len(projects),
        "indexed": sum(1 for p in projects if p["indexed"]),
        "total_chunks": sum(p["chunk_count"] for p in projects if p["indexed"]),
        "db_exists": (ROOT / "db" / "qdrant").exists(),
        "indexing": _idx["running"],
    }


@app.get("/api/indexing-status")
async def indexing_status():
    return _idx


@app.post("/api/projects/{project_name}/index")
async def index_project(project_name: str):
    if _idx["running"]:
        return JSONResponse(
            {"error": f"Already indexing '{_idx['project']}' — wait for it to finish."},
            status_code=409,
        )
    asyncio.create_task(_run_indexing(project_name))
    return {"started": True, "project": project_name}


@app.patch("/api/projects/{project_name}/metadata")
async def update_metadata(project_name: str, data: MetadataUpdate):
    project_dir = ROOT / f"telegram-{project_name}"
    if not project_dir.exists():
        return JSONResponse({"error": "Project not found"}, status_code=404)
    meta = _read_metadata(project_dir)
    meta["display_name"] = data.display_name
    (project_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


@app.post("/api/projects/upload")
async def upload_project(
    file: UploadFile = File(...),
    display_name: str = Form(""),
):
    filename = file.filename or "upload"
    name_lower = filename.lower()
    if not (name_lower.endswith(".zip") or name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz")):
        return JSONResponse({"error": "Only .zip or .tar.gz archives are supported."}, status_code=400)

    content = await file.read()
    result_data: bytes | None = None

    try:
        if name_lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for entry in zf.namelist():
                    parts = Path(entry).parts
                    if parts and parts[-1] == "result.json" and len(parts) <= 2:
                        result_data = zf.read(entry)
                        break
        else:
            with tarfile.open(fileobj=io.BytesIO(content)) as tf:
                for member in tf.getmembers():
                    parts = Path(member.name).parts
                    if parts and parts[-1] == "result.json" and len(parts) <= 2:
                        f = tf.extractfile(member)
                        if f:
                            result_data = f.read()
                        break
    except Exception as e:
        return JSONResponse({"error": f"Failed to read archive: {e}"}, status_code=400)

    if result_data is None:
        return JSONResponse(
            {"error": "result.json not found in archive root (or one level deep)."},
            status_code=400,
        )

    stem = display_name.strip() or Path(filename).stem.removesuffix(".tar")
    slug = _slugify(stem)
    project_dir = ROOT / f"telegram-{slug}"
    n = 0
    while project_dir.exists():
        n += 1
        project_dir = ROOT / f"telegram-{slug}-{n}"

    origin_dir = project_dir / "origin"
    origin_dir.mkdir(parents=True)
    (origin_dir / "result.json").write_bytes(result_data)

    final_name = display_name.strip() or stem
    meta = {"display_name": final_name, "created_at": _now()[:10]}
    (project_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "project_name": project_dir.name.removeprefix("telegram-"),
        "display_name": final_name,
    }


# ── history routes ─────────────────────────────────────────────────────────────

@app.get("/api/history")
async def list_history():
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT s.id, s.title, s.created_at,
                   COUNT(m.id) AS msg_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT 100
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/history/{session_id}")
async def get_session(session_id: str):
    conn = _db()
    try:
        sess = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess:
            return JSONResponse({"error": "Not found"}, status_code=404)
        rows = conn.execute(
            "SELECT role, content, sources, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        messages = []
        for m in rows:
            msg = dict(m)
            if msg.get("sources"):
                try:
                    msg["sources"] = json.loads(msg["sources"])
                except Exception:
                    msg["sources"] = None
            messages.append(msg)
        return {"session": dict(sess), "messages": messages}
    finally:
        conn.close()


@app.delete("/api/history")
async def clear_history():
    conn = _db()
    try:
        conn.execute("DELETE FROM sessions")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.delete("/api/history/{session_id}")
async def delete_session(session_id: str):
    conn = _db()
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/query")
async def query_endpoint(req: QueryRequest):
    async def generate():
        if not (ROOT / "bm25" / "index.pkl").exists():
            yield _sse({"type": "error",
                        "message": "Index not built yet. Open Projects and click Index."})
            return

        yield _sse({"type": "status", "message": "Searching…"})
        try:
            query_vec = await asyncio.to_thread(embed_query, req.query)
            vec_results = await asyncio.to_thread(vector_search, query_vec, 20, None)
            bm25_results = await asyncio.to_thread(bm25_search, req.query, 20, None)
            merged = rrf_merge([vec_results, bm25_results])
            merged = date_filter(merged, req.date_from, req.date_to)[:20]
            if req.chat_id:
                merged = [c for c in merged if c.get("chat_name") == req.chat_id]
            chunks = await asyncio.to_thread(rerank, req.query, merged, 5)
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return

        if not chunks:
            yield _sse({"type": "error", "message": "Ничего не найдено."})
            return

        yield _sse({"type": "sources", "chunks": [
            {"chat_name": c["chat_name"], "date_start": c["date_start"][:10],
             "authors": c["authors"], "text": c["text"]}
            for c in chunks
        ]})

        context = "\n\n---\n\n".join(
            f"[{c['chat_name']} | {c['date_start'][:10]} | {', '.join(c['authors'])}]\n{c['text']}"
            for c in chunks
        )
        prompt = (
            "You are an assistant answering questions about Telegram chat history. "
            "Use ONLY the provided excerpts. Mention specific people and dates from the excerpts when relevant. "
            "If the answer is not in the excerpts, say so clearly. "
            "Answer in the same language as the question.\n\n"
            f"Chat excerpts:\n{context}\n\n"
            f"Question: {req.query}"
        )
        full_answer = ""
        try:
            with ac.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    full_answer += text
                    yield _sse({"type": "token", "text": text})
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return

        yield _sse({"type": "done"})

        if req.session_id and full_answer:
            await asyncio.to_thread(_save_exchange, req.session_id, req.query, full_answer, chunks)

    return StreamingResponse(generate(), media_type="text/event-stream", headers=_sse_headers())


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
