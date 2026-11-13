"""Workshop and repair manuals a login can ask questions about, page by page.

A manual is read once, every page of it, into a search index (manuals.db in Trillion's data folder). Each page keeps
the section it belongs to (Engine, Cooling, Brakes, Wiring ...) and the page number printed on it, so an answer can say
"section Cooling, page 7-12" and link straight to that page. The manual can also be split into one PDF per section,
saved next to the original in a folder of its own.

Nothing leaves this PC: the PDF is read with pypdf and searched with SQLite's own full-text search.
"""
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

import core

DB = core.DATA / "manuals.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS manuals(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, path TEXT, pages INTEGER,
  done INTEGER DEFAULT 0, status TEXT, sections TEXT, split_dir TEXT, size INTEGER, mtime REAL, created REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS manual_pages USING fts5(manual_id UNINDEXED, page UNINDEXED, section, ref UNINDEXED,
  text, tokenize='porter unicode61');
"""
_LOCK = threading.Lock()
JOBS = {}                          # manual id -> thread, while a manual is being read

# the legal notice printed on every page of Mahindra's manuals (and similar ones): it says nothing about the car
BOILER = re.compile(
    r"All copyrights reserved by|The repair methods give.{0,700}?(?:consent of|written co|unauthori[sz]ed)[^\n]*|"
    r"©\s*\d{4}[^\n]*|Mahindra\s*&\s*Mahindra\s*Ltd\.?[^\n]*|www\.teknetmahindra\.com", re.I | re.S)
STOP = set("the a an and or of to in is are was were be been that this it for with as on at by from not his her their our my "
           "your i you he she we they them him me us will shall do does did have has had what who how why when where which "
           "about say says said tell me there can could would should please find show give get look up manual workshop repair "
           "page pages located location locate situated xuv xuv500 mahindra car vehicle bakkie mean means meaning "
           "know need want way best".split())
# words people say and words manuals print: searching for one also finds the other
SAME = {
    "water": ["coolant", "radiator"], "pipe": ["hose", "pipe", "line", "tube"], "pipes": ["hose", "pipe", "line", "tube"],
    "hose": ["hose", "pipe"], "bonnet": ["bonnet", "hood"], "hood": ["hood", "bonnet"], "boot": ["tailgate", "boot", "trunk"],
    "trunk": ["tailgate", "boot", "trunk"], "tyre": ["tyre", "tire", "wheel"], "tire": ["tire", "tyre", "wheel"],
    "aircon": ["air conditioning", "ac", "hvac", "compressor"], "ac": ["air conditioning", "hvac"],
    "fuse": ["fuse", "relay"], "globe": ["bulb", "lamp"], "bulb": ["bulb", "lamp"], "light": ["lamp", "light"],
    "gearbox": ["transmission", "gearbox"], "diff": ["differential"], "petrol": ["fuel"], "diesel": ["fuel", "diesel"],
    "battery": ["battery"], "windscreen": ["windshield", "windscreen"], "windshield": ["windshield", "windscreen"],
    "radiator": ["radiator", "coolant"], "temp": ["temperature"], "aircond": ["air conditioning", "hvac"],
    "oil": ["oil", "lubrication"], "sensor": ["sensor"], "torque": ["torque", "tightening", "nm"],
    "wire": ["wiring", "harness", "wire"], "wires": ["wiring", "harness"], "plug": ["connector", "plug"],
}
WHERE = re.compile(r"\b(where|locat\w*|find the|position of|sits?|situated)\b", re.I)
LAYOUT = re.compile(r"component locations?|location index|description and operation|schematic|layout|\blocation\b", re.I)
ASKS_MANUAL = re.compile(r"\b(manual|workshop|xuv\w*|mahindra|my car|the car|vehicle|bakkie|engine bay|bonnet|"
                         r"torque|fuses?|wiring|part number|error code|fault code|dtc|warning light|[PBCU][0-9A-F]{4})\b", re.I)

# Things that are clearly about Trillion / this PC / the home network, NOT the car.
# Section names like "network" (a vehicle's CAN network) used to pull the car manual into
# messages like "check my network security" - this stops that when there's no real car signal.
NOT_MANUAL = re.compile(r"\b(my pc|this pc|my computer|this computer|my laptop|my (?:wi-?fi|network|router|firewall)|"
                        r"network security|cyber\w*|hack\w*|intrud\w*|malware|virus|antivirus|defender|"
                        r"firewall|ports?\b|trillion|desktop|windows|security (?:scan|check|watch)|"
                        r"scan (?:my|this|the) (?:pc|computer|network))\b", re.I)


class ManualError(Exception):
    pass


def _conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()
    for m in _q("SELECT id FROM manuals WHERE done=0"):       # a read that was cut short by a restart: carry on
        _start(m["id"])


def _q(sql, a=(), one=False):
    c = _conn()
    try:
        rows = c.execute(sql, a).fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        c.close()


def _x(sql, a=()):
    c = _conn()
    try:
        cur = c.execute(sql, a)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def listing(user):
    return [_public(m) for m in _q("SELECT * FROM manuals WHERE user_id=? ORDER BY id", (user["id"],))]


def get(user, mid):
    m = _q("SELECT * FROM manuals WHERE id=? AND user_id=?", (int(mid), user["id"]), one=True)
    return dict(m) if m else None


def _public(m):
    import json
    secs = json.loads(m["sections"] or "[]")
    return {"id": m["id"], "name": m["name"], "path": m["path"], "pages": m["pages"], "ready": bool(m["done"]),
            "status": m["status"], "split_folder": m["split_dir"],
            "sections": [f"{s['name']} (pages {s['first']}-{s['last']})" for s in secs]}


# ---------- reading a manual in ----------
def add(user, path, name=None):
    """Start reading a PDF manual into the index. Reading a big manual takes some minutes; it runs by itself."""
    p = Path(str(path).strip().strip('"'))
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise ManualError(f"There is no PDF at {p}.")
    st = p.stat()
    old = _q("SELECT * FROM manuals WHERE user_id=? AND path=?", (user["id"], str(p)), one=True)
    if old and old["size"] == st.st_size and old["mtime"] == st.st_mtime:
        if not old["done"] and old["id"] not in JOBS:
            _start(old["id"])
        return _public(_q("SELECT * FROM manuals WHERE id=?", (old["id"],), one=True))
    if old:
        _forget(old["id"])
    mid = _x("INSERT INTO manuals(user_id,name,path,pages,done,status,size,mtime,created) VALUES(?,?,?,?,0,?,?,?,?)",
             (user["id"], (name or p.stem)[:120], str(p), 0, "waiting to be read", st.st_size, st.st_mtime, time.time()))
    _start(mid)
    return _public(_q("SELECT * FROM manuals WHERE id=?", (mid,), one=True))


def _forget(mid):
    _x("DELETE FROM manual_pages WHERE manual_id=?", (mid,))
    _x("DELETE FROM manuals WHERE id=?", (mid,))


def remove(user, mid):
    if not get(user, mid):
        raise ManualError("That manual is not yours.")
    _forget(int(mid))


def _start(mid):
    with _LOCK:
        if mid in JOBS and JOBS[mid].is_alive():
            return
        t = threading.Thread(target=_read, args=(mid,), daemon=True, name=f"manual-{mid}")
        JOBS[mid] = t
        t.start()


def clean(text):
    text = BOILER.sub(" ", text or "")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def page_ref(text):
    """The page number printed on a repair manual page, like 2-25 (chapter 2, page 25), or None."""
    head = (text or "")[:300]
    if not re.search(r"Repair\s*Manual", head, re.I):
        return None
    m = re.search(r"(?<![\d.\-/A-Za-z])(\d{1,2})\s?-\s?(\d{1,3})(?![\d.\-/A-Za-z])", head)
    return f"{int(m.group(1))}-{int(m.group(2))}" if m else None


SHORT = {"EMS": "engine management", "MBFM": "body function module", "IC": "instrument cluster", "HVAC": "air conditioning",
         "RPAS": "reverse park assist", "TPMS": "tyre pressure monitoring", "SRS": "airbags", "CAN": "vehicle network",
         "ESP/ABS": "stability control and anti-lock brakes", "AWD": "all wheel drive"}


def _title(s):
    """'EMS DIAGNOSTIC' -> 'EMS Diagnostic (engine management)': short codes stay in capitals and are spelt out."""
    s = re.sub(r"\s+", " ", s).strip(" -,.")
    words = s.split(" ")
    if s.isupper():
        words = [w if len(w) <= 4 or "/" in w else w.capitalize() for w in words]
        words = [w.lower() if w in ("AND", "OF") and i else w for i, w in enumerate(words)]
    out = " ".join(words)
    code = words[0].upper()
    return out + f" ({SHORT[code]})" if code in SHORT else out


def starts_section(text):
    """The name of the section this page opens, or None if it carries on the one before.

    Workshop PDFs are often several books bound together: a repair manual with a title page for every chapter
    ("Repair Manual / Engine Cooling", with no page number), then wiring and diagnostic manuals that each start with a
    cover of their own, then loose bulletins and fitting instructions."""
    t = re.sub(r"\s+", " ", text or "").strip()
    head = t[:220]
    m = re.match(r"(?:MAN\s*-?\s*\d+\s*)?Repair Manual (?:MAN\s*-?\s*\d+\s*)?(.{3,50}?) All copyrights", head)
    if m and not page_ref(text) and "table of contents" not in m.group(1).lower():
        return _title(m.group(1))
    m = re.match(r"(.{2,40}?)\s*MANUAL\s*(MAN\s*-?\s*\d+)?\s*(?:www\.\S+\s*)?ALL INFORMATION CONTAINED", head)
    if m:                                                     # the cover of a book: "EMS DIAGNOSTIC MANUAL ..."
        name = _title(m.group(1))
        if name.lower() == "repair":
            return "Repair Manual - Cover and Contents"
        return re.sub(r"^(\S+ Diagnostic)", r"\1 Manual", name) if "Diagnostic" in name else name + " Manual"
    m = re.match(r"WIRING MANUAL (MAN\s*-?\s*\d+ )?(ABOUT THE WIRING MANUAL|NEW AGE (.{3,40}?) MAN)", head)
    if m:
        return "Wiring Manual" + (f" ({m.group(3).strip()})" if m.group(3) else "")
    m = re.match(r"(.{3,60}?Installation Instructions)", head)
    if m:
        return _title(m.group(1))
    if head.startswith("Technical Service Bulletin"):
        return "Technical Service Bulletins"
    return None


def sections_of(raw):
    """Group the pages into sections. raw is the page texts. Returns ([section name per page], [section ranges])."""
    labels, cur = [], "Front pages"
    for t in raw:
        cur = starts_section(t) or cur
        labels.append(cur)
    ranges = []
    for i, lab in enumerate(labels, 1):
        if ranges and ranges[-1]["name"] == lab:
            ranges[-1]["last"] = i
        else:
            ranges.append({"name": lab, "first": i, "last": i})
    # a stray title page (one or two pages of another name between two runs of the same section) is not a section
    k = 1
    while k < len(ranges) - 1:
        a, b, c = ranges[k - 1], ranges[k], ranges[k + 1]
        if a["name"] == c["name"] and b["last"] - b["first"] < 2:
            a["last"] = c["last"]
            del ranges[k:k + 2]
            for i in range(b["first"], c["last"] + 1):
                labels[i - 1] = a["name"]
        else:
            k += 1
    # a book's cover is followed by pages that repeat the book's name: keep one name, and number repeats apart
    seen = Counter()
    for r in ranges:
        seen[r["name"]] += 1
        if seen[r["name"]] > 1:
            new = f"{r['name']} ({seen[r['name']]})"
            for i in range(r["first"], r["last"] + 1):
                labels[i - 1] = new
            r["name"] = new
    return labels, ranges


def page_texts(path, progress=None):
    """The words on every page. pdfium reads a big manual in about a minute and a half; pypdf takes half an hour."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(path))
        out = []
        try:
            for i in range(len(doc)):
                page = doc[i]
                tp = page.get_textpage()
                out.append(tp.get_text_bounded() or "")
                tp.close()
                page.close()
                if progress and i % 50 == 0:
                    progress(i, len(doc))
        finally:
            doc.close()
        return out
    except ImportError:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        out = []
        for i, page in enumerate(reader.pages):
            try:
                out.append(page.extract_text() or "")
            except Exception:
                out.append("")
            if progress and i % 50 == 0:
                progress(i, len(reader.pages))
        return out


def _store(mid, raw):
    """Put the page texts of a manual into the search index, each with its section. Returns the sections."""
    import json
    labels, ranges = sections_of(raw)
    c = _conn()
    try:
        c.execute("DELETE FROM manual_pages WHERE manual_id=?", (mid,))
        c.executemany("INSERT INTO manual_pages(manual_id,page,section,ref,text) VALUES(?,?,?,?,?)",
                      [(mid, i + 1, labels[i], page_ref(t) or "", clean(t)) for i, t in enumerate(raw)])
        c.execute("UPDATE manuals SET done=1, pages=?, status=?, sections=? WHERE id=?",
                  (len(raw), f"ready: {len(raw)} pages in {len(ranges)} sections", json.dumps(ranges), mid))
        c.commit()
    finally:
        c.close()
    return ranges


def _read(mid):
    import json
    m = _q("SELECT * FROM manuals WHERE id=?", (mid,), one=True)
    if not m:
        return
    try:
        raw = page_texts(m["path"], lambda i, n: _x("UPDATE manuals SET pages=?, status=? WHERE id=?",
                                                     (n, f"reading {i} of {n} pages", mid)))
        ranges = _store(mid, raw)
        core.log(m["user_id"], f"Read the manual {m['name']}: {len(raw)} pages in {len(ranges)} sections", "done")
    except Exception as e:
        _x("UPDATE manuals SET status=? WHERE id=?", (f"could not be read ({type(e).__name__}: {e})"[:300], mid))
    finally:
        JOBS.pop(mid, None)


# ---------- splitting into one PDF per section ----------
def split(user, mid):
    """Save each section as its own PDF in a folder next to the manual. Returns the folder and the files."""
    import json
    from pypdf import PdfReader, PdfWriter
    m = get(user, mid)
    if not m:
        raise ManualError("That manual is not yours.")
    if not m["done"]:
        raise ManualError(f"{m['name']} is still being read ({m['status']}). Try again when it is ready.")
    src = Path(m["path"])
    out = src.parent / f"{src.stem} - sections"
    out.mkdir(exist_ok=True)
    reader = PdfReader(str(src))
    files = []
    for n, s in enumerate(json.loads(m["sections"] or "[]"), 1):
        w = PdfWriter()
        for i in range(s["first"] - 1, s["last"]):
            w.add_page(reader.pages[i])
        name = re.sub(r'[<>:"/\\|?*]', "-", f"{n:02d} {s['name']} (pages {s['first']}-{s['last']}).pdf")
        with open(out / name, "wb") as fh:
            w.write(fh)
        files.append(name)
    _x("UPDATE manuals SET split_dir=? WHERE id=?", (str(out), int(mid)))
    return {"folder": str(out), "files": files}


def page_pdf(user, mid, page):
    """One page of a manual as a small PDF of its own, to open in the browser or on the phone."""
    import io
    from pypdf import PdfReader, PdfWriter
    m = get(user, mid)
    if not m:
        raise ManualError("That manual is not yours.")
    reader = PdfReader(m["path"])
    if not 1 <= int(page) <= len(reader.pages):
        raise ManualError(f"{m['name']} has no page {page}.")
    w = PdfWriter()
    w.add_page(reader.pages[int(page) - 1])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue(), f"{m['name']} - page {page}.pdf"


def page_png(user, mid, page, scale=2.5):
    """A sharp picture of one page (PNG bytes), kept so the next look is instant."""
    m = get(user, mid)
    if not m:
        raise ManualError("That manual is not yours.")
    page = int(page)
    if not 1 <= page <= (m["pages"] or 0):
        raise ManualError(f"{m['name']} has no page {page}.")
    cache = core.DATA / "manual_pictures" / str(m["id"])
    f = cache / f"{page}.png"
    if f.exists() and f.stat().st_mtime >= (m["mtime"] or 0):
        return f.read_bytes()
    import io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(m["path"])
    try:
        img = doc[page - 1].render(scale=scale).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    cache.mkdir(parents=True, exist_ok=True)
    f.write_bytes(buf.getvalue())
    return buf.getvalue()


def page_text(user, mid, page):
    """Every word on one page, for the brain to read in full."""
    m = get(user, mid)
    if not m:
        raise ManualError("That manual is not yours.")
    r = _q("SELECT section, ref, text FROM manual_pages WHERE manual_id=? AND page=?", (int(mid), int(page)), one=True)
    if not r:
        raise ManualError(f"{m['name']} has no page {page}.")
    return {"manual": m["name"], "page": int(page), "section": r["section"], "ref": r["ref"], "text": r["text"][:8000],
            "link": f"/api/manuals/{m['id']}/page/{int(page)}"}


def find(user, manual):
    """A manual by its number or a part of its name; the only one if there is just one."""
    ms = _q("SELECT * FROM manuals WHERE user_id=? ORDER BY id", (user["id"],))
    if str(manual or "").strip().isdigit():
        ms = [m for m in ms if m["id"] == int(manual)] or ms
    elif manual:
        ms = [m for m in ms if str(manual).lower() in m["name"].lower()] or ms
    if not ms:
        raise ManualError("No manuals have been read in yet. Give me the PDF's path and I will read it.")
    return dict(ms[0])


# ---------- searching ----------
def _terms(question):
    words = [w for w in re.findall(r"[a-z0-9]+", str(question or "").lower()) if w not in STOP and len(w) > 1]
    out = []
    for w in dict.fromkeys(words):
        group = [w] + [s for s in SAME.get(w, []) if s != w]
        out.append(group)
    return out[:10]


def _fts(group):
    parts = []
    for t in group:
        t = re.sub(r"[^a-z0-9 ]", "", t)
        if not t:
            continue
        parts.append('"' + t + '"' if " " in t else t + ("*" if len(t) >= 4 else ""))
    return "(" + " OR ".join(parts) + ")" if parts else ""


def search(user, question, manual=None, limit=6):
    """The pages that best answer the question, best first: [{manual, page, ref, section, extract, link}]."""
    ms = [m for m in _q("SELECT * FROM manuals WHERE user_id=? AND done=1", (user["id"],))
          if not manual or str(manual).lower() in m["name"].lower() or str(manual) == str(m["id"])]
    if not ms:
        return []
    terms = _terms(question)
    groups = [g for g in (_fts(g) for g in terms) if g]
    if not groups:
        return []
    names = {m["id"]: m["name"] for m in ms}
    ids = ",".join(str(i) for i in names)
    # what each of the question's words looks like on a page (any of its other names, any ending)
    pats = [re.compile("|".join(r"\b" + re.escape(t[:max(4, len(t) - 2)] if " " not in t else t) for t in g), re.I)
            for g in terms]
    c = _conn()
    try:
        sql = (f"SELECT manual_id, page, ref, section, text, snippet(manual_pages, 4, '[', ']', ' ... ', 40) AS ex, "
               f"bm25(manual_pages, 0, 0, 4.0, 0, 1.0) AS score FROM manual_pages "
               f"WHERE manual_pages MATCH ? AND manual_id IN ({ids}) ORDER BY score LIMIT 250")
        try:
            rows = c.execute(sql, (" OR ".join(groups),)).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        c.close()
    # "where is ...": the manual's component location and layout pages answer that best
    if WHERE.search(str(question)):
        try:
            c = _conn()
            more = c.execute(
                f"SELECT manual_id, page, ref, section, text, snippet(manual_pages, 4, '[', ']', ' ... ', 40) AS ex, "
                f"bm25(manual_pages, 0, 0, 4.0, 0, 1.0) AS score FROM manual_pages WHERE manual_pages MATCH ? "
                f"AND manual_id IN ({ids}) ORDER BY score LIMIT 60",
                ('("component location" OR "location index" OR "component locations" OR "description and operation") AND ('
                 + " OR ".join(groups) + ")",)).fetchall()
            c.close()
        except sqlite3.OperationalError:
            more = []
        have = {(r["manual_id"], r["page"]) for r in rows}
        rows = list(rows) + [r for r in more if (r["manual_id"], r["page"]) not in have]
    layout = lambda r: 1 if WHERE.search(str(question)) and LAYOUT.search(r["text"][:600]) else 0
    contents = lambda r: 2 if r["text"].count("....") > 5 else 0       # a contents page only points elsewhere
    # the pages that cover most of what was asked come first; among those, the best search score
    covered = lambda r: sum(1 for p in pats if p.search(r["section"] + " " + r["text"])) + layout(r) - contents(r)
    rows = sorted(rows, key=lambda r: (-covered(r), r["score"]))[:limit]
    return [{"manual": names[r["manual_id"]], "manual_id": r["manual_id"], "page": r["page"], "ref": r["ref"],
             "section": r["section"], "extract": re.sub(r"\s+", " ", r["ex"]).strip(),
             "link": f"/api/manuals/{r['manual_id']}/page/{r['page']}"} for r in rows]


def has_any(user):
    return bool(_q("SELECT 1 FROM manuals WHERE user_id=? AND done=1 LIMIT 1", (user["id"],), one=True))


def about(user, text):
    """True when the message looks like a question for one of this login's manuals."""
    if not text or not has_any(user):
        return False
    if ASKS_MANUAL.search(text):
        return True
    if NOT_MANUAL.search(text):     # a PC / network / security command for Trillion, not a car question
        return False
    import json
    secs = {w.lower() for m in _q("SELECT sections FROM manuals WHERE user_id=? AND done=1", (user["id"],))
            for s in json.loads(m["sections"] or "[]") for w in re.findall(r"[A-Za-z]{4,}", s["name"])}
    secs -= {"chapter", "front", "pages", "general", "information", "system", "manual", "cover", "contents"}
    words = [w for g in _terms(text) for t in g for w in t.split()]
    return any(w in secs for w in words if len(w) >= 4)


def prompt_line(user):
    """What the brain is told about this login's manuals in every conversation."""
    import json
    out = []
    for m in _q("SELECT * FROM manuals WHERE user_id=? ORDER BY id", (user["id"],)):
        secs = ", ".join(s["name"] for s in json.loads(m["sections"] or "[]"))
        out.append(f"- {m['name']} (number {m['id']}): {m['path']}, {m['pages']} pages, {m['status']}."
                   + (f" Sections: {secs}." if secs else "")
                   + (f" Split into one PDF per section in {m['split_dir']}." if m["split_dir"] else ""))
    return "\n".join(out) or "(none yet)"


def for_brain(hits, user=None, full=3, room=1800):
    """The found pages, written out for the brain to answer from: the best few in full, the rest as an extract."""
    if not hits:
        return ""
    parts = []
    for n, h in enumerate(hits):
        head = f"[{h['manual']} - section {h['section']} - PDF page {h['page']}" + (f", printed page {h['ref']}" if h["ref"] else "") + "]"
        body = h["extract"]
        if user is not None and n < full:
            try:
                body = page_text(user, h["manual_id"], h["page"])["text"][:room]
            except ManualError:
                pass
        parts.append(head + "\n" + body)
    return ("You looked the user's question up in their workshop manual yourself, and these are the pages you found "
            "(the user did not send them). The manual uses workshop words: coolant for water, hose or pipe for pipes, "
            "and so on.\n\n" + "\n\n".join(parts) +
            "\n\nIf the question is about the vehicle, answer from these pages: say plainly where the part is or what "
            "the problem means, then give the manual's own advice or steps to fix it (the order of work, torque values, "
            "cautions), and say which section and page it is on. Do not add steps, numbers or locations the pages do not "
            "give. The pages have labelled pictures (A, B ...); say the picture on that page shows it, and the links to "
            "the pages are added under your answer by themselves, so do not write links. If the pages do not answer it, "
            "say so and suggest other words to ask with. If the question is not about the vehicle at all, ignore them.")
