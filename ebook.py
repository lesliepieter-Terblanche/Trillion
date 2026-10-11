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


def write_book(bid, book, sections, user):
    s = core.settings(user)
    accent = s.get("accent") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(s.get("accent") or "")) else "#c8862a"
    folder = HOME / "ebooks" / bid
    folder.mkdir(parents=True, exist_ok=True)
    data = {"id": bid, "title": book["title"], "subtitle": book["subtitle"], "author": s.get("brand") or user["name"],
            "cover": {"layout": "right", "textColor": "#ffffff", "background": {"variant": "wave-cover"}}, "sections": sections}
    (folder / "ebook.ts").write_text('import type { Ebook } from "@/ebook-engine/types/ebook";\n\nexport const ebook: Ebook = '
                                     + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")
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
def create(llm, user, prompt, chapters=5, progress=None):
    init()
    say = progress or (lambda t: None)
    st = status()
    if not st["installed"]:
        return {"error": st["note"]}
    try:
        chapters = max(2, min(10, int(chapters or 5)))
    except (TypeError, ValueError):
        chapters = 5
    say("Planning the book")
    book = outline(llm, user, prompt, chapters) or outline(llm, user, prompt, chapters)        # one more go if the plan came back unusable
    if not book:
        return {"error": "My brain did not come back with a usable plan for the book. Ask me again; if it keeps happening, a shorter request helps."}
    sections = [{"id": "introduction", "title": "Introduction", "blocks": [{"type": "heading", "level": 1, "content": "Introduction"}] + (
        [{"type": "rich-paragraph", "content": book["intro"]}] if book["intro"] else []) + [
        {"type": "list", "items": [c["title"] for c in book["chapters"]]}]}]
    words, thin = len(book["intro"].split()), []
    for n, ch in enumerate(book["chapters"], 1):
        say(f"Writing chapter {n} of {len(book['chapters'])}: {ch['title']}")
        blocks = chapter_blocks(llm, user, book, ch, n)
        if _words(blocks) < 120:                          # too thin to be a chapter: have one more go
            again = chapter_blocks(llm, user, book, ch, n)
            blocks = again if _words(again) > _words(blocks) else blocks
        if _words(blocks) < 120:
            thin.append(ch["title"])
            blocks = blocks or [{"type": "rich-paragraph", "content": ch["description"] or ch["title"]}]
        words += _words(blocks)
        # a chapter is two parts in the layout program: its full-page opener, then its pages. The second part is named after
        # the chapter's first sub-heading so the contents page does not list the same title twice.
        sub = next((b["content"] for b in blocks if b["type"] == "heading"), "") or "In this chapter"
        sections.append({"id": f"chapter-{n}", "title": ch["title"], "blocks": [],
                         "chapterCover": {"chapterNumber": n, "description": ch["description"], "layout": "minimal",
                                          "background": {"variant": ("layered", "diagonal", "organic", "split-wave", "corner-wave")[(n - 1) % 5]}}})
        sections.append({"id": f"chapter-{n}-text", "title": sub, "blocks": [{"type": "heading", "level": 1, "content": ch["title"]}] + blocks})
    if len(thin) * 2 > len(book["chapters"]):
        return {"error": "My brain could not write enough for most of the chapters, so I did not make a thin ebook. Ask me again, or try fewer chapters."}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", book["title"].lower()).strip("-")[:40] or "ebook"
    bid = f"t-{slug}-{stamp}"
    folder = core.user_home(user) / "Trillion Ebooks"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-{stamp}.pdf"
    try:
        write_book(bid, book, sections, user)
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
                  json.dumps({"subtitle": book["subtitle"], "chapters": [c["title"] for c in book["chapters"]], "words": words,
                              "epub": str(epub) if has_epub else ""}), time.time()))
    return {"ebook_id": eid, "title": book["title"], "saved": str(out), "pages": pages, "chapters": len(book["chapters"]), "words": words, "epub": has_epub,
            "notes": [f"These chapters came out thin and need your attention: {', '.join(thin)}."] if thin else []}
