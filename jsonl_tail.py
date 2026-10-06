# -*- coding: utf-8 -*-
"""高效读取 JSONL/文本尾部，避免监控循环反复全量读大文件。"""
from __future__ import annotations

import json
from pathlib import Path


def tail_lines(path, n, chunk_size=64 * 1024):
    """Return the last *n* non-empty lines without reading the whole file."""
    p = Path(path)
    if n <= 0 or not p.exists():
        return []
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            data = b""
            newlines = 0
            while pos > 0 and newlines < n:
                step = min(chunk_size, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step)
                data = chunk + data
                newlines += chunk.count(b"\n")
        lines = data.splitlines()[-n:]
        return [line.decode("utf-8", "replace").strip()
                for line in lines if line.strip()]
    except Exception:
        return []


def tail_jsonl(path, n, chunk_size=64 * 1024):
    """Parse JSON objects from the tail; malformed lines are skipped."""
    out = []
    for line in tail_lines(path, n, chunk_size=chunk_size):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[-n:]


def tail_text(path, max_bytes=2 * 1024 * 1024):
    """Return a UTF-8 text tail, dropping a partial first line when truncated."""
    p = Path(path)
    if not p.exists():
        return ""
    try:
        size = p.stat().st_size
        start = max(0, size - max(1, int(max_bytes)))
        with p.open("rb") as f:
            f.seek(start)
            data = f.read()
        if start > 0:
            parts = data.split(b"\n", 1)
            data = parts[1] if len(parts) > 1 else b""
        return data.decode("utf-8", "replace")
    except Exception:
        return ""