"""Files and media a person hands to Trillion in the conversation (from the PC or from the phone over Tailscale).

Each file is saved in that login's own "Trillion Uploads" folder and stays on this PC. What she can do with it depends on
the kind: documents are read, sound is written out as text, pictures are described if a local model that can see is
installed, and pictures and video clips can be used as the footage of a video she makes.
"""
import base64
import os
import re
import time
from pathlib import Path

import httpx

import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, path TEXT, kind TEXT, size INTEGER, created REAL);
"""
MAX_BYTES = int(os.environ.get("TRILLION_UPLOAD_MAX", 2 * 1024 ** 3))      # 2 GB a file
IMAGE = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
VIDEO = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp")
AUDIO = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac", ".amr")
TEXT = (".txt", ".md", ".csv", ".tsv", ".json", ".log", ".html", ".htm", ".xml", ".yaml", ".yml", ".ini", ".py", ".js", ".css", ".rtf", ".srt")
OFFICE = (".pdf", ".docx", ".xlsx", ".pptx")
MARK = re.compile(r"\n?\[\[file:(\d+):([^\]\n]*?):(image|video|audio|document|file)\]\]")
PER_FILE, PER_TURN = 6000, 9000                 # characters of a file's words handed to the brain
# local models that can look at a picture, by the names Ollama gives them
SEEING = re.compile(r"llava|vision|minicpm-v|moondream|qwen[\d.]*-?vl|gemma3|pixtral|granite3\.2-vision", re.I)


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def kind_of(name):
    ext = os.path.splitext(name)[1].lower()
    return ("image" if ext in IMAGE else "video" if ext in VIDEO else "audio" if ext in AUDIO
            else "document" if ext in TEXT or ext in OFFICE else "file")


def folder(user):
    p = core.user_home(user) / "Trillion Uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(name):
    name = os.path.basename(str(name or "").replace("\\", "/")).strip().strip(".")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:120]
    return name or "file"


def new_path(user, name):
    name = safe_name(name)
    stem, ext = os.path.splitext(name)
    p = folder(user) / name
    n = 2
    while p.exists():
        p = folder(user) / f"{stem} ({n}){ext}"
        n += 1
    return p


def record(user, path):
    path = Path(path)
    uid = core.x("INSERT INTO uploads(user_id,name,path,kind,size,created) VALUES(?,?,?,?,?,?)",
                 (user["id"], path.name, str(path), kind_of(path.name), path.stat().st_size, time.time()))
    return get(user, uid)


def get(user, fid):
    r = core.q("SELECT * FROM uploads WHERE id=? AND user_id=?", (int(fid), user["id"]), one=True)
    return dict(r) if r else None


def marker(f):
    return f"\n[[file:{f['id']}:{f['name'].replace(']', ')').replace(':', '-')}:{f['kind']}]]"


def for_brain(text):
    """What an older message looked like to the brain: the words, plus a plain note for each file."""
    return MARK.sub(lambda m: f"\n(attached {m.group(3)}: {m.group(2)})", text or "")


# ---------- reading what is inside ----------
def read_words(path):
    """The words in a document, or None if it has none she can read."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in TEXT:
            return path.read_text(encoding="utf-8", errors="replace")
        if ext == ".pdf":
            from pypdf import PdfReader
            out = []
            for page in PdfReader(str(path)).pages[:60]:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) > 40000:
                    break
            return "\n".join(out)
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            rows = [p.text for p in d.paragraphs]
            for t in d.tables:
                rows += [" | ".join(c.text.strip() for c in r.cells) for r in t.rows]
            return "\n".join(rows)
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:6]:
                out.append(f"Sheet: {ws.title}")
                for i, row in enumerate(ws.iter_rows(values_only=True)):
                    if i >= 200:
                        out.append("(more rows not shown)")
                        break
                    if any(v is not None for v in row):
                        out.append(" | ".join("" if v is None else str(v) for v in row))
            return "\n".join(out)
        if ext == ".pptx":
            from pptx import Presentation
            out = []
            for i, slide in enumerate(Presentation(str(path)).slides, 1):
                out.append(f"Slide {i}:")
                out += [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            return "\n".join(out)
    except Exception:
        return None
    return None


def seeing_model():
    try:
        for m in httpx.get(core.OLLAMA + "/api/tags", timeout=2).json().get("models", []):
            if SEEING.search(m.get("name", "")):
                return m["name"]
    except Exception:
        pass
    return None


def look_at(path, model):
    """A local model that can see describes the picture. The picture never leaves this PC."""
    try:
        from PIL import Image, ImageOps
        import io
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1280, 1280))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
        r = httpx.post(core.OLLAMA + "/api/chat", timeout=180, json={
            "model": model, "stream": False, "options": {"temperature": 0.1},
            "messages": [{"role": "user", "images": [base64.b64encode(buf.getvalue()).decode()],
                          "content": "Describe this picture in detail for someone who cannot see it. Copy out any text in it exactly."}]})
        return (r.json().get("message") or {}).get("content", "").strip() or None
    except Exception:
        return None


def digest(files, hear=None, say=None):
    """What the brain is told about the attached files for this one turn. Returns (text, notes for the person)."""
    say = say or (lambda t: None)
    parts, notes, left = [], [], PER_TURN
    model = None
    for f in files:
        head = f"FILE: {f['name']} ({f['kind']}, saved on this PC at {f['path']})"
        body = ""
        if f["kind"] == "document":
            say(f"Reading {f['name']}")
            words = read_words(f["path"])
            if words is None:
                body = "(I could not read the words in this file.)"
                notes.append(f"I could not read {f['name']}.")
            else:
                words = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", words)).strip()
                room = max(500, min(PER_FILE, left))
                body = words[:room] + (f"\n(only the first {room} of {len(words)} characters are shown)" if len(words) > room else "")
                if not words:
                    body = "(This file has no readable words: it may be a scan.)"
                    notes.append(f"{f['name']} has no readable words; it may be a scanned picture.")
        elif f["kind"] == "audio" and hear:
            say(f"Listening to {f['name']}")
            try:
                if f["size"] > 60 * 1024 * 1024:
                    raise ValueError("too long")
                words = hear(Path(f["path"]).read_bytes())
                body = "What is said in it: " + (words[:max(500, min(PER_FILE, left))] if words else "(no speech heard)")
            except Exception:
                body = "(I could not listen to this recording.)"
                notes.append(f"I could not listen to {f['name']}.")
        elif f["kind"] == "image":
            model = model or seeing_model() or ""
            if model:
                say(f"Looking at {f['name']}")
                seen = look_at(f["path"], model)
                body = "What it shows: " + seen[:1500] if seen else "(I could not look at this picture.)"
            else:
                body = "(You cannot see this picture: no local model that can see is installed. You can still use it in a video or move it.)"
                if not any("cannot see pictures" in n for n in notes):
                    notes.append("I cannot see pictures yet. I saved it and can use it in a video. To let me look at pictures, "
                                 "ask Claude to add a local seeing model.")
        elif f["kind"] == "video":
            body = "(A video clip. You cannot watch it, but it can be used as footage in a video you make.)"
        else:
            body = "(Saved. You cannot read this kind of file.)"
        left -= len(body)
        parts.append(head + ("\n" + body if body else ""))
    return "The user attached these files to this message:\n\n" + "\n\n".join(parts), notes
