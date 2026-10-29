"""Ask my stuff: a private, local document brain.

Point Trillion at your work folders and it reads the files (PDF, Word, text, markdown, CSV, HTML), splits
them into passages, embeds each with the local Ollama embedder (nomic-embed-text) and stores them in SQLite.
Then "ask my stuff …" searches across everything — your documents AND your saved notes — and the brain
answers grounded in the passages, each with its source. Fully local and token-free; nothing leaves the PC.
"""
import json
import math
import os
import re
import threading
import time
from pathlib import Path

import core

EXTS = (".txt", ".md", ".markdown", ".csv", ".log", ".rtf", ".html", ".htm", ".pdf", ".docx")
CHUNK, OVERLAP, MAX_CHUNKS = 1200, 150, 600


def init():
    core.x("""CREATE TABLE IF NOT EXISTS corpus(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, source TEXT, title TEXT, part INTEGER,
        chunk TEXT, emb TEXT, mtime REAL, created REAL)""")


# ---------- text extraction ----------
def _strip_html(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", s)).strip()


def _extract(path):
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in (".txt", ".md", ".markdown", ".csv", ".log", ".rtf"):
            return p.read_text(encoding="utf-8", errors="replace")
        if ext in (".html", ".htm"):
            return _strip_html(p.read_text(encoding="utf-8", errors="replace"))
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except Exception:
                from PyPDF2 import PdfReader
            out = []
            for pg in PdfReader(str(p)).pages:
                out.append(pg.extract_text() or "")
            return "\n".join(out)
        if ext == ".docx":
            from docx import Document
            d = Document(str(p))
            return "\n".join(par.text for par in d.paragraphs)
    except Exception:
        return ""
    return ""


def _chunks(text):
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text) and len(out) < MAX_CHUNKS:
        out.append(text[i:i + CHUNK])
        i += CHUNK - OVERLAP
    return out


# ---------- ingest ----------
def _supported(p):
    return p.suffix.lower() in EXTS and p.is_file()


def ingest_file(user, abspath, progress=None):
    init()
    uid = user["id"]
    p = Path(abspath)
    if not _supported(p):
        return {"skipped": str(p), "reason": "unsupported type"}
    src = str(p)
    mtime = p.stat().st_mtime
    row = core.q("SELECT mtime FROM corpus WHERE user_id=? AND source=? LIMIT 1", (uid, src), one=True)
    if row and abs((row["mtime"] or 0) - mtime) < 1:
        return {"skipped": src, "reason": "already indexed"}
    text = _extract(src)
    chunks = _chunks(text)
    if not chunks:
        return {"skipped": src, "reason": "no readable text"}
    core.x("DELETE FROM corpus WHERE user_id=? AND source=?", (uid, src))     # re-index clean
    title = p.name
    n = 0
    for i, ch in enumerate(chunks):
        try:
            emb = core.embed(ch)
        except Exception:
            emb = None
        core.x("INSERT INTO corpus(user_id,source,title,part,chunk,emb,mtime,created) VALUES(?,?,?,?,?,?,?,?)",
               (uid, src, title, i, ch, json.dumps(emb) if emb else None, mtime, time.time()))
        n += 1
        if progress and i % 20 == 0:
            progress(f"{title}: {i + 1}/{len(chunks)} passages")
    return {"indexed": src, "title": title, "passages": n}


def ingest_path(user, abspath, progress=None):
    """A file, or a folder (walked for supported files)."""
    init()
    say = progress or (lambda t: None)
    p = Path(abspath)
    if p.is_file():
        return {"files": 1, "results": [ingest_file(user, p, say)]}
    files = [f for f in p.rglob("*") if _supported(f)]
    say(f"Found {len(files)} readable file(s). Reading them in…")
    results, done = [], 0
    for f in files:
        try:
            r = ingest_file(user, f, say)
            results.append(r)
            if r.get("indexed"):
                done += 1
                say(f"Indexed {done}: {f.name}")
        except Exception:
            continue
    return {"files": len(files), "indexed": done, "results": results[-50:]}


def start_ingest(user, abspath):
    init()
    tid = core.add_task(user["id"], f"Index documents: {Path(abspath).name}", domain="work") \
        if hasattr(core, "add_task") else None

    def go():
        try:
            r = ingest_path(user, abspath, progress=(lambda t: core.set_task(tid, detail=t)) if tid else None)
            msg = f"Indexed {r.get('indexed', 0)} of {r.get('files', 0)} file(s)."
            if tid:
                core.set_task(tid, status="completed", detail=msg)
        except Exception as e:
            if tid:
                core.set_task(tid, status="failed", detail=f"Indexing failed: {type(e).__name__}")
    threading.Thread(target=go, daemon=True).start()
    return {"task_id": tid, "started": True}


# ---------- search / ask ----------
def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(user, query, k=6):
    init()
    uid = user["id"]
    qe = core.embed(query) if query else None
    rows = core.q("SELECT source,title,chunk,emb FROM corpus WHERE user_id=? LIMIT 8000", (uid,))
    words = [w for w in (query or "").lower().split() if len(w) > 2]
    scored = []
    for r in rows:
        cos = 0.0
        if qe and r["emb"]:
            try:
                cos = _cos(qe, json.loads(r["emb"]))
            except Exception:
                cos = 0.0
        low = (r["chunk"] or "").lower()
        hits = sum(1 for w in words if w in low)
        if hits or cos > 0.5:
            scored.append((cos + 0.12 * hits, r))
    scored.sort(key=lambda s: -s[0])
    return [{"source": r["source"], "title": r["title"], "text": r["chunk"][:900]} for _, r in scored[:k]]


def ask(user, query, k=6):
    """Unified retrieval across your documents AND your saved notes, for the brain to answer from."""
    passages = search(user, query, k=k)
    notes = []
    try:
        import tools
        for n in tools.search_notes(user["id"], query, k=3):
            notes.append({"source": "note", "title": n.get("title", "note"), "text": (n.get("body") or "")[:600]})
    except Exception:
        pass
    hits = passages + notes
    if not hits:
        return {"hits": [], "note": "Nothing in your documents or notes matches that. Index a folder first with "
                "index_documents, or try other words."}
    return {"hits": hits, "note": "Answer the user's question from these passages and name the source file(s). "
            "If they don't contain the answer, say so plainly."}


def listing(user):
    init()
    out = []
    for r in core.q("SELECT source, title, COUNT(*) c, MAX(created) t FROM corpus WHERE user_id=? GROUP BY source "
                    "ORDER BY t DESC", (user["id"],)):
        out.append({"source": r["source"], "title": r["title"], "passages": r["c"]})
    return out


def forget(user, source):
    init()
    like = f"%{source}%"
    core.x("DELETE FROM corpus WHERE user_id=? AND (source=? OR source LIKE ? OR title LIKE ?)",
           (user["id"], source, like, like))
    return {"ok": True}
