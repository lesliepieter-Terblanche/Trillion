"""Designed ebooks (PDF) written by Trillion's brain and laid out by ebook-creator (github.com/victornns/ebook-creator, MIT).

ebook-creator is a separate program, installed next to Trillion with "7 - Install Ebook Creator.bat". Trillion writes the
book as that program's own content file, lets it lay the pages out (cover, contents page, chapter pages), and saves the
result as a PDF with the hidden browser she already uses. Everything happens on this PC.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

import core

HOME = Path(os.environ.get("TRILLION_EBOOK_DIR", core.BASE / "EbookCreator"))
PORT = int(os.environ.get("TRILLION_EBOOK_PORT", 3217))
BASE = f"http://127.0.0.1:{PORT}"
SCHEMA = """
CREATE TABLE IF NOT EXISTS ebooks(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, path TEXT, pages INTEGER, detail TEXT, created REAL);
"""
NOT_INSTALLED = ("The ebook maker is not installed yet. Double-click \"7 - Install Ebook Creator.bat\" in the Trillion folder, "
                 "wait for it to finish, then ask me again.")
SERVER = {"proc": None}
MM = {"A4": (210, 297), "A5": (148, 210), "Letter": (215.9, 279.4)}
# the kinds of content the layout program understands, and the one field each needs
TEXT_BLOCKS = {"rich-paragraph", "highlight", "note", "warning", "action-box", "quote"}
LIST_BLOCKS = {"list", "steps", "item-list"}


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def status():
    node = shutil.which("node")
    here = (HOME / "package.json").exists()
    ready = here and (HOME / "node_modules" / "next").exists()
    return {"installed": bool(ready and node), "node": bool(node), "folder": str(HOME),
            "note": "Ready." if ready and node else NOT_INSTALLED}


# ---------- 1. the words ----------
def _json(text):
    text = text or ""
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        return {}


def _clean(s, n=1200):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def outline(llm, user, prompt, chapters):
    s = core.settings(user)
    ask = f"""Plan a short, practical ebook for this request: "{prompt}"
Brand: {s.get('brand') or 'none'}.
Reply with JSON only, in exactly this shape:
{{"title": "a strong title, at most 8 words", "subtitle": "one line that says who it is for and what they get",
"intro": "a welcoming introduction of about 120 words",
"chapters": [{{"title": "chapter title", "description": "one sentence on what this chapter covers", "points": ["3 to 5 things it must explain"]}}]}}
Give exactly {chapters} chapters. English only."""
    d = _json((llm(user, [{"role": "system", "content": "You are an experienced non-fiction editor. Reply with JSON only."},
                          {"role": "user", "content": ask}]).get("content")))
    chs = [c for c in d.get("chapters", []) if isinstance(c, dict) and c.get("title")][:chapters]
    topic = re.sub(r"^\s*(please\s+)?(create|make|write|generate|produce)\b[^:]{0,40}?(e-?book|guide|book|lead magnet)s?\b\s*(about|on|for|:)?\s*", "",
                   prompt, flags=re.I)
    topic = re.sub(r"\b(with|in|of)?\s*\d{1,2}[\s-]*chapters?\b", " ", topic, flags=re.I)
    topic = re.sub(r"\b(and|with)\s+(ai[- ]made\s+)?(pictures|images|photos|illustrations)\b", " ", topic, flags=re.I)
    topic = re.sub(r"\s+", " ", topic).strip(" :.,-") or prompt.strip()
    if not chs:
        return None                                       # the brain gave nothing usable: say so, do not print an empty book
    return {"title": _clean(d.get("title") or topic.capitalize(), 90), "subtitle": _clean(d.get("subtitle"), 180),
            "intro": _clean(d.get("intro"), 1500), "topic": topic,
            "chapters": [{"title": _clean(c["title"], 90), "description": _clean(c.get("description"), 220),
                          "points": [_clean(p, 160) for p in (c.get("points") or []) if p][:6]} for c in chs]}


def chapter_blocks(llm, user, book, ch, n):
    ask = f"""Write chapter {n} of the ebook "{book['title']}" ({book['subtitle']}).
Chapter title: {ch['title']}
It must cover: {'; '.join(ch['points']) or ch['description'] or ch['title']}
Reply with JSON only: {{"blocks": [ ... ]}} where each block is one of:
{{"type": "heading", "content": "a sub-heading"}}
{{"type": "rich-paragraph", "content": "a paragraph of 60 to 110 words; **bold** is allowed"}}
{{"type": "list", "items": ["short point", "short point"]}}
{{"type": "steps", "items": ["first do this", "then this"]}}
{{"type": "highlight", "content": "one key takeaway sentence"}}
{{"type": "note", "content": "a practical tip"}}
{{"type": "action-box", "content": "one thing the reader should do today"}}
Write 8 to 12 blocks: mostly paragraphs, two or three sub-headings, one list or steps, one highlight, and end with an action-box.
Be specific and practical. No filler, no invented statistics. English only."""
    d = _json((llm(user, [{"role": "system", "content": "You are an expert non-fiction writer. Reply with JSON only."},
                          {"role": "user", "content": ask}]).get("content")))
    out = []
    for b in d.get("blocks", []) if isinstance(d.get("blocks"), list) else []:
        if not isinstance(b, dict):
            continue
        t = str(b.get("type", "")).strip().lower()
        if t == "paragraph":
            t = "rich-paragraph"
        if t == "heading" and _clean(b.get("content")):
            out.append({"type": "heading", "level": 2, "content": _clean(b["content"], 120)})
        elif t in TEXT_BLOCKS and _clean(b.get("content")):
            out.append({"type": t, "content": _clean(b["content"], 1400)})
        elif t in LIST_BLOCKS and isinstance(b.get("items"), list):
            items = [_clean(i, 300) for i in b["items"] if _clean(i)][:10]
            if items:
                out.append({"type": t, "items": items})
    return out[:16]


def _words(blocks):
    return sum(len(str(b.get("content", "")).split()) + sum(len(i.split()) for i in b.get("items", [])) for b in blocks)


# ---------- 1b. a book made from the person's own document ----------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def read_document(path):
    """A document as its pieces in order: headings, paragraphs, list points, tables and pictures.
    Returns {"title": str, "items": [("h", level, text) | ("p", text) | ("li", text, numbered) | ("table", rows) | ("img", bytes)]}."""
    path = Path(path)
    title, items = "", []
    if path.suffix.lower() == ".docx":
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        d = docx.Document(str(path))
        plain = []                                        # (index in items, paragraph) for the no-styles fallback below
        for child in d.element.body.iterchildren():
            if child.tag == W + "p":
                para = Paragraph(child, d)
                for blip in child.iter(BLIP):
                    part = d.part.related_parts.get(blip.get(EMBED))
                    if part is not None and getattr(part, "blob", None):
                        items.append(("img", part.blob))
                text = re.sub(r"\s+", " ", para.text).strip()
                if not text:
                    continue
                style = ""
                try:
                    style = (para.style.name or "").lower()
                except Exception:
                    pass
                if style == "title":
                    title = title or text
                elif style == "subtitle":
                    items.append(("p", text))
                elif style.startswith("heading"):
                    m = re.search(r"\d", style)
                    items.append(("h", int(m.group()) if m else 1, text))
                elif "list" in style or child.find(f".//{W}numPr") is not None:
                    items.append(("li", text, "number" in style))
                else:
                    plain.append((len(items), para))
                    items.append(("p", text))
            elif child.tag == W + "tbl":
                rows = [[re.sub(r"\s+", " ", c.text).strip() for c in r.cells] for r in Table(child, d).rows]
                rows = [r for r in rows if any(r)]
                if rows:
                    items.append(("table", rows))
        if not any(i[0] == "h" for i in items):
            # written without heading styles: a short line that is all bold, or starts "Chapter 3", is a heading
            for idx, para in plain:
                text = items[idx][1]
                runs = [r for r in para.runs if r.text.strip()]
                bold = bool(runs) and all(r.bold for r in runs)
                if len(text) < 90 and not text.endswith((".", ",", ";", ":")) and (bold or re.match(r"(chapter|part|section)\s+\w+", text, re.I)):
                    items[idx] = ("h", 1, text)
    else:
        import attach
        words = attach.read_words(path) or ""
        buf = []

        def flush():
            if buf:
                items.append(("p", " ".join(buf)))
                buf.clear()
        for line in words.splitlines():
            s_ = line.strip()
            m = re.match(r"(#{1,4})\s+(.+)", s_)
            if not s_:
                flush()
            elif m:
                flush()
                items.append(("h", len(m.group(1)), m.group(2).strip(" #")))
            elif re.match(r"(chapter|part)\s+\w+\b.{0,70}$", s_, re.I) and len(s_) < 90:
                flush()
                items.append(("h", 1, s_))
            elif re.match(r"([-*\u2022]|\d{1,2}[.)])\s+\S", s_):
                flush()
                items.append(("li", re.sub(r"^([-*\u2022]|\d{1,2}[.)])\s+", "", s_), bool(re.match(r"\d", s_))))
            else:
                buf.append(s_)
        flush()
    return {"title": title, "items": items}


def split_chapters(items, want):
    """Group the pieces into chapters: by the document's own main headings if it has them, else into even parts."""
    levels = [i[1] for i in items if i[0] == "h"]
    top = next((lv for lv in sorted(set(levels)) if levels.count(lv) >= 2), None)
    front, chapters = [], []
    if top is not None:
        cur = None
        for it in items:
            if it[0] == "h" and it[1] == top:
                cur = {"title": _clean(it[2], 90), "items": []}
                chapters.append(cur)
            elif it[0] == "h" and it[1] < top:
                continue                                  # a single line above the chapters: the book's own title
            elif cur is None:
                front.append(it)
            else:
                cur["items"].append(it)
        return front, [c for c in chapters if c["items"]] or chapters
    size = sum(len(str(i[1])) for i in items if i[0] in ("p", "li")) or 1
    want = max(2, min(12, want or round(size / 6000) or 3))
    per, cur, used = size / want, {"title": "", "items": []}, 0
    chapters.append(cur)
    for it in items:
        cur["items"].append(it)
        if it[0] in ("p", "li"):
            used += len(str(it[1]))
            if used >= per and len(chapters) < want:
                cur, used = {"title": "", "items": []}, 0
                chapters.append(cur)
    return front, [c for c in chapters if c["items"]]


def _split_long(text, limit=1300):
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + len(sentence) > limit:
            out.append(cur.strip())
            cur = ""
        cur += sentence + " "
    return [x for x in out + [cur.strip()] if x]


def blocks_from(items, save_image, alt):
    """The person's own words as layout blocks, in their order. Nothing is rewritten."""
    out, run = [], []

    def flush():
        if run:
            out.append({"type": "steps" if all(r[1] for r in run) and len(run) > 1 else "list", "items": [_clean(r[0], 400) for r in run]})
            run.clear()
    for it in items:
        if it[0] == "li":
            run.append((it[1], it[2]))
            continue
        flush()
        if it[0] == "p":
            out += [{"type": "rich-paragraph", "content": part} for part in _split_long(re.sub(r"\s+", " ", it[1]).strip())]
        elif it[0] == "h":
            out.append({"type": "heading", "level": 2 if it[1] <= 2 else 3, "content": _clean(it[2], 140)})
        elif it[0] == "table":
            rows = [[_clean(c, 200) for c in r[:8]] for r in it[1][:40]]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            if len(rows) > 1:
                out.append({"type": "table", "headers": rows[0], "rows": rows[1:]})
        elif it[0] == "img":
            name = save_image(it[1])
            if name:
                out.append({"type": "image-with-caption", "src": "@@IMG:" + name + "@@", "alt": alt})
    flush()
    return out


def describe(llm, user, book_title, ch, text):
    """One sentence under the chapter's title, a takeaway, and a title if the document gave none. Her only additions."""
    ask = f"""This is one chapter of the ebook "{book_title}".{' Its title is: ' + ch['title'] if ch['title'] else ''}
SOURCE:
{text[:3200]}

Reply with JSON only: {{"title": "a chapter title of at most 7 words", "description": "one sentence saying what this chapter covers",
"highlight": "the single most useful point in this chapter, in one sentence, using only what the source says"}}"""
    try:
        d = _json(llm(user, [{"role": "system", "content": "You are a careful editor. Use only the source. Reply with JSON only."},
                             {"role": "user", "content": ask}]).get("content"))
    except Exception:
        d = {}
    return _clean(d.get("title"), 90), _clean(d.get("description"), 220), _clean(d.get("highlight"), 320)


def name_book(llm, user, prompt, doc_title, file_stem, text):
    ask = f"""Give this document a title and a subtitle for an ebook.
Request from the owner: "{prompt[:300]}"
The document starts:
{text[:2500]}

Reply with JSON only: {{"title": "at most 8 words", "subtitle": "one line saying who it is for and what they get"}}"""
    try:
        d = _json(llm(user, [{"role": "system", "content": "You are an editor. Reply with JSON only."}, {"role": "user", "content": ask}]).get("content"))
    except Exception:
        d = {}
    nice = re.sub(r"[_-]+", " ", file_stem).strip().title()
    return _clean(doc_title or d.get("title") or nice, 90), _clean(d.get("subtitle"), 180)


class Assets:
    """The pictures that go into one book: saved in the book's own folder, each with a name the layout program imports."""

    def __init__(self, bid):
        self.folder = HOME / "ebooks" / bid / "assets"
        self.files = {}                                   # name -> file name

    def add(self, data=None, path=None):
        from PIL import Image, ImageOps
        import io
        try:
            im = Image.open(io.BytesIO(data) if data is not None else path)
            im = ImageOps.exif_transpose(im)
            if min(im.size) < 80:                         # an icon or a bullet, not a picture
                return None
            im = im.convert("RGB")
            im.thumbnail((1600, 1600))
            self.folder.mkdir(parents=True, exist_ok=True)
            name = f"img{len(self.files) + 1}"
            im.save(self.folder / f"{name}.jpg", "JPEG", quality=88)
            self.files[name] = f"{name}.jpg"
            return name
        except Exception:
            return None


def picture_for(visual, seed):
    """A picture made for this book by the free picture service her videos already use. Only the description is sent."""
    try:
        import video
        video.init()
        return video.ai_image(visual, 768, 1024, seed)
    except Exception:
        return None


# ---------- 2. the layout program ----------
def _npm(args):
    return (["cmd", "/c", "npm"] if os.name == "nt" else ["npm"]) + args


def _up():
    try:
        return httpx.get(BASE + "/", timeout=4).status_code < 500
    except Exception:
        return False


def start_server(say):
    if _up():
        return
    p = SERVER.get("proc")
    if not p or p.poll() is not None:
        say("Starting the ebook maker")
        log = open(core.DATA / "ebook_server.log", "ab")
        SERVER["proc"] = subprocess.Popen(_npm(["run", "dev", "--", "-p", str(PORT), "-H", "127.0.0.1"]), cwd=str(HOME), stdout=log, stderr=log,
                                          stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(90):
        time.sleep(2)
        if _up():
            return
        if SERVER["proc"].poll() is not None:
            break
    raise RuntimeError("The ebook maker did not start. Run \"7 - Install Ebook Creator.bat\" again; if it still fails, send Claude the file "
                       f"{core.DATA / 'ebook_server.log'}.")


def write_book(bid, book, sections, user, assets=None, cover=None):
    s = core.settings(user)
    accent = s.get("accent") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(s.get("accent") or "")) else "#c8862a"
    folder = HOME / "ebooks" / bid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"id": bid, "title": book["title"], "subtitle": book["subtitle"], "author": s.get("brand") or user["name"],
            "cover": ({"layout": "centered", "textColor": "#ffffff", "background": {"variant": "corner-wave"},
                       "image": {"src": "@@IMG:" + cover + "@@", "alt": book["title"]}} if cover else
                      {"layout": "right", "textColor": "#ffffff", "background": {"variant": "wave-cover"}}), "sections": sections}
    files = assets.files if assets else {}
    body = re.sub(r'"@@IMG:(\w+)@@"', lambda m: m.group(1) if m.group(1) in files else '""', json.dumps(data, ensure_ascii=False, indent=2))
    (folder / "ebook.ts").write_text('import type { Ebook } from "@/ebook-engine/types/ebook";\n'
                                     + "".join(f'import {n} from "./assets/{f}";\n' for n, f in files.items())
                                     + "\nexport const ebook: Ebook = " + body + ";\n", encoding="utf-8")
    theme = {"colors": {"primary": "#14213d", "secondary": "#24365e", "text": "#1c2230", "accent": accent, "muted": "#7b8494"},
             "fonts": {"heading": '"Playfair Display", Georgia, serif', "body": 'Georgia, "Times New Roman", serif',
                       "mono": '"Courier New", Courier, monospace', "googleFonts": ["Playfair Display"]},
             "spacing": {"sectionGap": "4rem", "blockGap": "1.5rem", "pagePadding": "3rem"}, "pageSize": "A4"}
    (folder / "theme.ts").write_text('import type { EbookTheme } from "@/ebook-engine/types/theme";\n\nexport const theme: EbookTheme = '
                                     + json.dumps(theme, indent=2) + ";\n", encoding="utf-8")
    # the program's list of books: hers only (its own demo books stay on disk, untouched)
    mine = sorted(d.name for d in (HOME / "ebooks").iterdir() if d.is_dir() and d.name.startswith("t-") and (d / "ebook.ts").exists())[-40:]
    lines = ['import type { Ebook } from "@/ebook-engine/types/ebook";', 'import type { EbookTheme } from "@/ebook-engine/types/theme";']
    for i, name in enumerate(mine):
        lines += [f'import {{ ebook as e{i} }} from "./{name}/ebook";', f'import {{ theme as t{i} }} from "./{name}/theme";']
    lines += ["", "export interface EbookEntry {", "  ebook: Ebook;", "  theme: EbookTheme;", "}", "",
              "export const ebookRegistry: Record<string, EbookEntry> = {"]
    lines += [f'  "{name}": {{ ebook: e{i}, theme: t{i} }},' for i, name in enumerate(mine)] + ["};", ""]
    (HOME / "ebooks" / "index.ts").write_text("\n".join(lines), encoding="utf-8")


def print_pdf(bid, out):
    """Open the laid-out book in the hidden browser and save it as a PDF, one sheet per page, edge to edge."""
    from playwright.sync_api import sync_playwright
    w, h = MM["A4"]
    with sync_playwright() as pw:
        browser, last = None, ""
        for channel in ("msedge", "chrome", None):
            try:
                browser = pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()
                break
            except Exception as e:
                last = str(e).splitlines()[0][:160]
        if not browser:
            raise RuntimeError("I could not start a browser on the PC to save the PDF (" + last + ").")
        try:
            page = browser.new_context(device_scale_factor=1, viewport={"width": int(w * 3.7795) + 1, "height": int(h * 3.7795) + 1}).new_page()
            page.goto(f"{BASE}/preview/{bid}", wait_until="networkidle", timeout=180000)
            page.wait_for_selector("[data-pagination-ready='true']", timeout=120000)
            page.pdf(path=str(out), width=f"{w}mm", height=f"{h}mm", print_background=True,
                     margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
        finally:
            browser.close()


def make_epub(bid, out):
    """The same book as an EPUB (for Kindle, Kobo and phone reading apps), made by the layout program's own exporter."""
    try:
        r = subprocess.run(_npm(["run", "export:epub", "--", bid, BASE]), cwd=str(HOME), capture_output=True, timeout=180,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        made = HOME / "public" / "output" / f"{bid}.epub"
        if r.returncode == 0 and made.exists() and made.stat().st_size > 1000:
            shutil.move(str(made), str(out))
            return True
    except Exception:
        pass
    return False


def page_count(path):
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:
        return 0


# ---------- 3. the whole job ----------
COVERS = ("layered", "diagonal", "organic", "split-wave", "corner-wave")


def _chapter(sections, n, title, description, blocks, image=None):
    """A chapter is two parts in the layout program: its full-page opener, then its pages. The second part is named after
    the chapter's first sub-heading so the contents page does not list the same title twice."""
    sub = next((b["content"] for b in blocks if b["type"] == "heading"), "") or "In this chapter"
    cover = {"chapterNumber": n, "description": description, "layout": "minimal", "background": {"variant": COVERS[(n - 1) % 5]}}
    if image:
        cover.update(layout="horizontal", image={"src": "@@IMG:" + image + "@@", "alt": title})
    sections.append({"id": f"chapter-{n}", "title": title, "blocks": [], "chapterCover": cover})
    sections.append({"id": f"chapter-{n}-text", "title": sub, "blocks": [{"type": "heading", "level": 1, "content": title}] + blocks})


def create(llm, user, prompt, chapters=5, progress=None, sources=None, images=None, pictures=False):
    """sources: the person's own documents to make the book from (their words are kept, not rewritten).
    images: their own pictures (the first is the cover, the rest open the chapters). pictures: make pictures where there are none."""
    init()
    say = progress or (lambda t: None)
    st = status()
    if not st["installed"]:
        return {"error": st["note"]}
    asked = chapters
    try:
        chapters = max(2, min(10, int(chapters or 5)))
    except (TypeError, ValueError):
        chapters = 5
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bid = f"t-{stamp}-{user['id']}"
    assets = Assets(bid)
    own = [n for n in (assets.add(path=p) for p in (images or [])[:20]) if n]
    cover_img = own.pop(0) if own else None
    sections, notes, words, thin, titles = [], [], 0, [], []
    sources = [s for s in (sources or []) if os.path.exists(s)]

    if sources:
        say("Reading your document")
        items, doc_title = [], ""
        for src in sources[:5]:
            try:
                d = read_document(src)
            except Exception:
                notes.append(f"I could not read {os.path.basename(src)}.")
                continue
            doc_title = doc_title or d["title"]
            items += d["items"]
        text_all = " ".join(str(i[1]) if i[0] in ("p", "li") else (i[2] if i[0] == "h" else "") for i in items)
        if len(text_all.split()) < 60:
            return {"error": "I could not find enough words in that document to make a book. If it is a scan, the pages are pictures and I cannot read them."}
        front, parts = split_chapters(items, asked if isinstance(asked, int) and asked != 5 else 0)
        say("Naming the book")
        title, subtitle = name_book(llm, user, prompt, doc_title, Path(sources[0]).stem, text_all)
        book = {"title": title, "subtitle": subtitle}
        in_doc = sum(1 for i in items if i[0] == "img")
        if front:
            blocks = blocks_from(front, lambda b: assets.add(data=b), title)
            if _words(blocks) > 25:
                sections.append({"id": "introduction", "title": "Introduction",
                                 "blocks": [{"type": "heading", "level": 1, "content": "Introduction"}] + blocks})
                words += _words(blocks)
        for n, ch in enumerate(parts, 1):
            say(f"Laying out chapter {n} of {len(parts)}" + (f": {ch['title']}" if ch["title"] else ""))
            blocks = blocks_from(ch["items"], lambda b: assets.add(data=b), ch["title"] or title)
            src_text = " ".join(str(b.get("content", "")) + " " + " ".join(b.get("items", [])) for b in blocks)
            new_title, desc, key = describe(llm, user, title, ch, src_text)
            ch_title = ch["title"] or new_title or f"Part {n}"
            if key and len(src_text.split()) > 150:
                blocks.append({"type": "highlight", "content": key})
            words += _words(blocks)
            titles.append(ch_title)
            pic = own.pop(0) if own else None
            if not pic and pictures:
                say(f"Making the picture for chapter {n}")
                made = picture_for(f"{ch_title}. {desc or title}", n)
                pic = assets.add(path=made) if made else None
            _chapter(sections, n, ch_title, desc, blocks, pic)
        notes.append("The words are your own from the document; I added only the chapter lines and one key-point box per chapter.")
        if in_doc:
            kept = sum(1 for s_ in sections for b in s_["blocks"] if b["type"] == "image-with-caption")
            notes.append(f"{kept} of the {in_doc} pictures in your document are in the book." if kept != in_doc else f"All {in_doc} pictures from your document are in the book.")
    else:
        say("Planning the book")
        book = outline(llm, user, prompt, chapters) or outline(llm, user, prompt, chapters)    # one more go if the plan came back unusable
        if not book:
            return {"error": "My brain did not come back with a usable plan for the book. Ask me again; if it keeps happening, a shorter request helps."}
        sections.append({"id": "introduction", "title": "Introduction", "blocks": [{"type": "heading", "level": 1, "content": "Introduction"}] + (
            [{"type": "rich-paragraph", "content": book["intro"]}] if book["intro"] else []) + [
            {"type": "list", "items": [c["title"] for c in book["chapters"]]}]})
        words = len(book["intro"].split())
        for n, ch in enumerate(book["chapters"], 1):
            say(f"Writing chapter {n} of {len(book['chapters'])}: {ch['title']}")
            blocks = chapter_blocks(llm, user, book, ch, n)
            if _words(blocks) < 120:                      # too thin to be a chapter: have one more go
                again = chapter_blocks(llm, user, book, ch, n)
                blocks = again if _words(again) > _words(blocks) else blocks
            if _words(blocks) < 120:
                thin.append(ch["title"])
                blocks = blocks or [{"type": "rich-paragraph", "content": ch["description"] or ch["title"]}]
            words += _words(blocks)
            titles.append(ch["title"])
            pic = own.pop(0) if own else None
            if not pic and pictures:
                say(f"Making the picture for chapter {n}")
                made = picture_for(f"{ch['title']}. {ch['description'] or book['title']}", n)
                pic = assets.add(path=made) if made else None
            _chapter(sections, n, ch["title"], ch["description"], blocks, pic)
        if len(thin) * 2 > len(book["chapters"]):
            return {"error": "My brain could not write enough for most of the chapters, so I did not make a thin ebook. Ask me again, or try fewer chapters."}
        if thin:
            notes.append(f"These chapters came out thin and need your attention: {', '.join(thin)}.")

    if pictures and not cover_img:
        say("Making the cover picture")
        made = picture_for(f"{book['title']}. {book['subtitle']}", 0)
        cover_img = assets.add(path=made) if made else None
    if pictures:
        made_n = sum(1 for s_ in sections if s_.get("chapterCover", {}).get("image")) + (1 if cover_img else 0)
        notes.append(f"{made_n} pictures were added (cover and chapter pages), made by the free Pollinations service; only the chapter titles are sent to it."
                     if made_n else "I could not add pictures: the picture service did not answer. The book is laid out without them.")

    slug = re.sub(r"[^a-z0-9]+", "-", book["title"].lower()).strip("-")[:40] or "ebook"
    folder = core.out_dir(user, "Trillion Ebooks")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-{stamp}.pdf"
    try:
        write_book(bid, book, sections, user, assets, cover_img)
        start_server(say)
        say("Laying out the pages")
        print_pdf(bid, out)
    except Exception as e:
        return {"error": str(e) if isinstance(e, RuntimeError) else f"The pages could not be laid out ({type(e).__name__}: {str(e).splitlines()[0][:160]})."}
    if not out.exists() or out.stat().st_size < 5000:
        return {"error": "The ebook maker did not hand back a PDF. Try again."}
    pages = page_count(out)
    say("Making the EPUB copy")
    epub = out.with_suffix(".epub")
    has_epub = make_epub(bid, epub)
    eid = core.x("INSERT INTO ebooks(user_id,title,path,pages,detail,created) VALUES(?,?,?,?,?,?)",
                 (user["id"], book["title"], str(out), pages,
                  json.dumps({"subtitle": book["subtitle"], "chapters": titles, "words": words, "pictures": len(assets.files),
                              "epub": str(epub) if has_epub else "", "from": [os.path.basename(s_) for s_ in sources]}), time.time()))
    return {"ebook_id": eid, "title": book["title"], "saved": str(out), "pages": pages, "chapters": len(titles), "words": words, "epub": has_epub,
            "pictures": len(assets.files), "from_document": bool(sources), "notes": notes}
