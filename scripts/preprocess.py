#!/usr/bin/env python3
"""
Parses a Telegram JSON export and groups messages into conversation chunks.

Usage:
    python3 scripts/preprocess.py telegram-mygroup/origin/result.json
    python3 scripts/preprocess.py   # processes all telegram-*/origin/result.json
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

GAP_MINUTES = 30
MAX_CHUNK_TOKENS = 600
MIN_CHUNK_TOKENS = 80
CHARS_PER_TOKEN = 4  # conservative estimate for mixed Russian/Latin
MIN_TEXT_CHARS = 10


def load_export(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Truncated export — find last complete message object
        last = raw.rfind("\n  }")
        if last == -1:
            raise
        return json.loads(raw[: last + 4] + "\n ]\n}")


def get_text(msg: dict) -> str:
    t = msg.get("text", "")
    if isinstance(t, list):
        return "".join(e["text"] if isinstance(e, dict) else e for e in t)
    return t


def is_useful(msg: dict, text: str) -> bool:
    if msg.get("type") != "message":
        return False
    if len(text.strip()) < MIN_TEXT_CHARS:
        return False
    return True


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def build_chunks(messages: list[dict], chat_name: str, chat_id: str) -> list[dict]:
    by_id = {m["id"]: m for m in messages}

    # Build reply graph
    children: dict[int, list[int]] = defaultdict(list)
    roots: list[int] = []
    for m in messages:
        pid = m.get("reply_to_message_id")
        if pid and pid in by_id:
            children[pid].append(m["id"])
        else:
            roots.append(m["id"])

    # DFS to extract reply threads (each thread = connected component)
    visited: set[int] = set()
    threads: list[list[dict]] = []

    def dfs(mid: int, thread: list[dict]) -> None:
        if mid in visited:
            return
        visited.add(mid)
        thread.append(by_id[mid])
        for child_id in sorted(children[mid], key=lambda c: by_id[c]["date_unixtime"]):
            dfs(child_id, thread)

    for root_id in sorted(roots, key=lambda r: by_id[r]["date_unixtime"]):
        thread: list[dict] = []
        dfs(root_id, thread)
        if thread:
            threads.append(thread)

    chunks: list[dict] = []
    chunk_idx = 0

    for thread in threads:
        thread.sort(key=lambda m: m["date_unixtime"])

        # Split thread at time gaps
        groups: list[list[dict]] = [[thread[0]]]
        for msg in thread[1:]:
            gap = (int(msg["date_unixtime"]) - int(groups[-1][-1]["date_unixtime"])) / 60
            if gap > GAP_MINUTES:
                groups.append([msg])
            else:
                groups[-1].append(msg)

        # Split groups at token limit
        for group in groups:
            current: list[dict] = []
            current_tokens = 0

            for msg in group:
                text = get_text(msg)
                line = f"{msg.get('from') or 'Unknown'}: {text}\n"
                msg_tokens = estimate_tokens(line)

                if current and current_tokens + msg_tokens > MAX_CHUNK_TOKENS:
                    if current_tokens >= MIN_CHUNK_TOKENS:
                        chunks.append(_make_chunk(current, chat_name, chat_id, chunk_idx))
                        chunk_idx += 1
                    current = []
                    current_tokens = 0

                current.append(msg)
                current_tokens += msg_tokens

            if current and current_tokens >= MIN_CHUNK_TOKENS:
                chunks.append(_make_chunk(current, chat_name, chat_id, chunk_idx))
                chunk_idx += 1

    return chunks


def _make_chunk(messages: list[dict], chat_name: str, chat_id: str, idx: int) -> dict:
    # Deduplicate authors while preserving order
    seen: set[str] = set()
    authors: list[str] = []
    for m in messages:
        name = m.get("from") or "Unknown"
        if name not in seen:
            seen.add(name)
            authors.append(name)

    lines = []
    for m in messages:
        text = get_text(m).strip()
        if text:
            lines.append(f"{m.get('from') or 'Unknown'}: {text}")

    header = f"[Чат: {chat_name} | {messages[0]['date'][:10]} | Авторы: {', '.join(authors)}]"
    full_text = header + "\n" + "\n".join(lines)

    return {
        "chunk_id": f"{chat_id}_{idx}",
        "chat_id": chat_id,
        "chat_name": chat_name,
        "date_start": messages[0]["date"],
        "date_end": messages[-1]["date"],
        "authors": authors,
        "message_ids": [m["id"] for m in messages],
        "text": full_text,
    }


def process_export(export_path: Path) -> None:
    print(f"Processing {export_path} ...")
    data = load_export(export_path)

    chat_name = data.get("name", "unknown")
    chat_id = str(data.get("id", export_path.parent.parent.name))

    all_messages = data.get("messages", [])
    messages = [m for m in all_messages if is_useful(m, get_text(m))]
    print(f"  {len(all_messages)} total → {len(messages)} useful messages")

    chunks = build_chunks(messages, chat_name, chat_id)
    print(f"  → {len(chunks)} chunks")

    out_dir = export_path.parent.parent / "processed"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "chunks.json"
    out_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Saved → {out_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_export(Path(sys.argv[1]))
    else:
        exports = sorted(Path(".").glob("telegram-*/origin/result.json"))
        if not exports:
            print("No telegram-*/origin/result.json found. Pass a path explicitly.")
            sys.exit(1)
        for export in exports:
            process_export(export)
