"""Optional Chroma vector store for the second brain.

Trillion's second brain works on its own (cosine over embeddings kept in SQLite). When the
'chromadb' package is installed, this module gives it a real vector database so recall stays
fast and accurate as the number of saved notes grows. Everything here is best-effort: if Chroma
is not installed, or anything goes wrong, the caller falls back to the SQLite search and nothing
breaks. We always pass our own Ollama (nomic-embed-text) embeddings, so Chroma never downloads an
embedder of its own - it stays fully local and token-free.
"""
import threading

import core

_client = None
_failed = False
_lock = threading.Lock()


def _get():
    global _client, _failed
    if _client is not None or _failed:
        return _client
    with _lock:
        if _client is not None or _failed:
            return _client
        try:
            import chromadb
            _client = chromadb.PersistentClient(path=str(core.DATA / "chroma"))
        except Exception:
            _failed = True
            _client = None
        return _client


def available():
    return _get() is not None


def _coll(uid):
    c = _get()
    if not c:
        return None
    try:
        return c.get_or_create_collection(f"notes_u{uid}")
    except Exception:
        return None


def add(uid, note_id, text, emb=None, meta=None):
    """Store or update one note's vector. Returns True on success."""
    col = _coll(uid)
    if not col:
        return False
    try:
        kw = {"ids": [str(note_id)], "documents": [(text or "")[:6000]], "metadatas": [meta or {"d": ""}]}
        if emb:
            kw["embeddings"] = [emb]
        col.upsert(**kw)
        return True
    except Exception:
        return False


def search(uid, query, emb=None, k=20):
    """Return the note ids most similar to the query, best first. Empty list if unavailable."""
    col = _coll(uid)
    if not col:
        return []
    try:
        kw = {"n_results": k}
        if emb:
            kw["query_embeddings"] = [emb]
        else:
            kw["query_texts"] = [query]
        res = col.query(**kw)
        ids = (res.get("ids") or [[]])[0]
        return [int(i) for i in ids if str(i).isdigit()]
    except Exception:
        return []


def remove(uid, note_id):
    col = _coll(uid)
    if not col:
        return
    try:
        col.delete(ids=[str(note_id)])
    except Exception:
        pass
