"""Bible study for a login: a verse for the morning and one for the evening, a reader with search, study notes, and a
helper that answers from the passages themselves.

The King James Version comes with Trillion (it is in the public domain). Any other translation the owner has the right
to use can be added by putting a file in the "bibles" folder inside Trillion's data folder; it then shows up in the
translation list. The file is the same shape as the built-in one: {"code", "name", "lang", "books": [[[verse, ...], ...], ...]}
with the 66 books in the usual order.
"""
import datetime
import difflib
import gzip
import json
import re
import threading
import time
from pathlib import Path

import core

HERE = Path(__file__).parent / "bibles"
SCHEMA = """
CREATE TABLE IF NOT EXISTS bible_notes(id INTEGER PRIMARY KEY, user_id INTEGER, book INTEGER, chapter INTEGER, verse INTEGER,
  verse_to INTEGER, text TEXT, created REAL, updated REAL);
"""

# English name, Afrikaans name, other spellings people type
BOOKS = [
    ("Genesis", "Genesis", "gen ge gn"), ("Exodus", "Eksodus", "ex exo exod eks"), ("Leviticus", "Levitikus", "lev le lv"),
    ("Numbers", "Numeri", "num nu nm nb"), ("Deuteronomy", "Deuteronomium", "deut deu dt"), ("Joshua", "Josua", "josh jos jsh"),
    ("Judges", "Rigters", "judg jdg jg rig"), ("Ruth", "Rut", "ru rth"), ("1 Samuel", "1 Samuel", "1sam 1sa 1s"),
    ("2 Samuel", "2 Samuel", "2sam 2sa 2s"), ("1 Kings", "1 Konings", "1kgs 1ki 1k 1kon"), ("2 Kings", "2 Konings", "2kgs 2ki 2k 2kon"),
    ("1 Chronicles", "1 Kronieke", "1chr 1ch 1chron 1kron"), ("2 Chronicles", "2 Kronieke", "2chr 2ch 2chron 2kron"),
    ("Ezra", "Esra", "ezr esr"), ("Nehemiah", "Nehemia", "neh ne"), ("Esther", "Ester", "est esth es"), ("Job", "Job", "jb"),
    ("Psalms", "Psalms", "ps psa psalm pss psm"), ("Proverbs", "Spreuke", "prov pro prv pr spr"),
    ("Ecclesiastes", "Prediker", "eccl ecc ec qoh pred"), ("Song of Solomon", "Hooglied", "song sos songofsongs canticles hoogl"),
    ("Isaiah", "Jesaja", "isa is jes"), ("Jeremiah", "Jeremia", "jer je jr"), ("Lamentations", "Klaagliedere", "lam la klaagl"),
    ("Ezekiel", "Esegiel", "ezek eze ezk eseg"), ("Daniel", "Daniel", "dan da dn"), ("Hosea", "Hosea", "hos ho"), ("Joel", "Joel", "jl"),
    ("Amos", "Amos", "am"), ("Obadiah", "Obadja", "obad ob"), ("Jonah", "Jona", "jon jnh"), ("Micah", "Miga", "mic mc mig"),
    ("Nahum", "Nahum", "nah na"), ("Habakkuk", "Habakuk", "hab hb"), ("Zephaniah", "Sefanja", "zeph zep zp sef"),
    ("Haggai", "Haggai", "hag hg"), ("Zechariah", "Sagaria", "zech zec zc sag"), ("Malachi", "Maleagi", "mal ml"),
    ("Matthew", "Matteus", "matt mat mt"), ("Mark", "Markus", "mrk mk mr"), ("Luke", "Lukas", "luk lk"), ("John", "Johannes", "joh jn jhn"),
    ("Acts", "Handelinge", "act ac hand"), ("Romans", "Romeine", "rom ro rm"), ("1 Corinthians", "1 Korintiers", "1cor 1co 1kor 1korinthiers"),
    ("2 Corinthians", "2 Korintiers", "2cor 2co 2kor 2korinthiers"), ("Galatians", "Galasiers", "gal ga"),
    ("Ephesians", "Efesiers", "eph ephes ef"), ("Philippians", "Filippense", "phil php pp fil"), ("Colossians", "Kolossense", "col kol"),
    ("1 Thessalonians", "1 Tessalonisense", "1thess 1th 1thes 1tess"), ("2 Thessalonians", "2 Tessalonisense", "2thess 2th 2thes 2tess"),
    ("1 Timothy", "1 Timoteus", "1tim 1ti 1tm"), ("2 Timothy", "2 Timoteus", "2tim 2ti 2tm"), ("Titus", "Titus", "tit ti"),
    ("Philemon", "Filemon", "philem phm pm filem"), ("Hebrews", "Hebreers", "heb"), ("James", "Jakobus", "jas jm jak"),
    ("1 Peter", "1 Petrus", "1pet 1pe 1pt 1p"), ("2 Peter", "2 Petrus", "2pet 2pe 2pt 2p"), ("1 John", "1 Johannes", "1jn 1jo 1joh 1j"),
    ("2 John", "2 Johannes", "2jn 2jo 2joh 2j"), ("3 John", "3 Johannes", "3jn 3jo 3joh 3j"), ("Jude", "Judas", "jud jd"),
    ("Revelation", "Openbaring", "rev re rv revelations apocalypse openb op"),
]
_key = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace("ë", "e").replace("ê", "e").replace("é", "e").replace("ï", "i"))
ALIAS = {}
for _i, (_en, _af, _more) in enumerate(BOOKS):
    for _n in [_en, _af] + _more.split():
        ALIAS.setdefault(_key(_n), _i)
for _w in "is am re na da es ne la ho le ex ac ro ga ti op ru mr so".split():      # everyday words: not safe as book names
    ALIAS.pop(_w, None)
for _roman, _digit in (("iii", "3"), ("ii", "2"), ("i", "1"), ("first", "1"), ("second", "2"), ("third", "3"), ("1st", "1"), ("2nd", "2"), ("3rd", "3")):
    for _k, _v in list(ALIAS.items()):
        if _k[0] == _digit:
            ALIAS.setdefault(_roman + _k[1:], _v)

# a verse for each morning and a different one for each evening; the day of the year picks which
MORNING = """Ps 118:24|Lam 3:22-23|Ps 5:3|Prov 3:5-6|Phil 4:13|Josh 1:9|Isa 40:31|Ps 143:8|Matt 6:33|Rom 8:28|Ps 37:4-5|2 Tim 1:7|
Isa 41:10|Ps 90:14|Col 3:23|Jer 29:11|Ps 119:105|Matt 5:16|Rom 12:2|Ps 46:1|Gal 6:9|Eph 2:10|Ps 27:1|Heb 12:1-2|Mic 6:8|Ps 19:14|
1 Cor 16:13-14|Deut 31:6|Ps 34:8|Jas 1:5|Phil 1:6|Ps 100:4-5|John 15:5|2 Cor 5:17|Ps 16:8|Prov 16:3|Isa 26:3|Matt 7:7-8|
Rom 15:13|Ps 121:1-2|Eph 6:10|1 Pet 5:7|Ps 28:7|John 8:12|Col 3:17|Ps 59:16|Zeph 3:17|Matt 11:28-30|Gal 2:20|Ps 139:14|
Prov 4:23|2 Cor 12:9|Ps 56:3-4|John 14:6|Rom 5:8|Isa 43:2|Ps 103:1-2|Eph 3:20|Phil 4:6-7|Ps 32:8|Heb 11:1|Jas 1:22|
Ps 63:1|Matt 28:19-20|1 John 4:19|Ps 84:11|Rom 8:38-39|Prov 3:9-10|Isa 55:8-9|John 10:10|Ps 145:18|Col 3:12|1 Thess 5:16-18|
Ps 18:2|Mark 12:30-31|2 Cor 9:8|Ps 40:1-3|Josh 24:15|Rom 10:9|Ps 95:1-2|Heb 4:16|Gal 5:22-23|Ps 33:20-22|Matt 5:14|
Eph 4:32|Ps 73:26|John 16:33|1 Cor 10:13|Ps 92:1-2|Isa 12:2|Rom 12:12|Ps 31:24|Phil 2:3-4|Ps 86:5|Jas 4:8|Num 6:24-26|
Ps 20:7|John 13:34-35|1 Pet 2:9|Ps 9:1-2|Prov 18:10|Rom 1:16|Ps 96:1-3|Heb 13:8|2 Chr 7:14|Ps 138:8|Matt 6:34|Eph 5:15-16|
Ps 119:11|Isa 40:28-29|1 John 5:14|Ps 62:5-6|Col 1:16-17|Deut 6:5|Ps 107:1|John 1:12|Rom 6:23|Ps 147:3|Prov 15:1|Phil 3:13-14"""
NIGHT = """Ps 4:8|Ps 23:1-4|Matt 11:28|Ps 91:1-2|Phil 4:8|Ps 121:3-4|Isa 26:3-4|John 14:27|Ps 3:5|Prov 3:24|Ps 46:10|1 Pet 5:6-7|
Ps 127:2|Lam 3:25-26|Ps 62:1-2|Rom 8:31|Ps 42:8|Deut 33:27|Ps 139:11-12|Heb 13:5-6|Ps 16:7-9|Isa 30:15|Ps 63:6-8|John 14:1-3|
Ps 34:4|2 Cor 4:16-18|Ps 116:7|Num 6:24-26|Ps 55:22|Matt 6:25-27|Ps 131:2|Rom 8:26|Ps 141:2|1 John 1:9|Ps 51:10|Ps 103:11-12|
Isa 41:13|Ps 119:147-148|Phil 4:19|Ps 30:5|John 10:27-28|Ps 17:8|Hab 3:17-18|Ps 73:23-24|2 Thess 3:16|Ps 61:2-3|Isa 54:10|
Ps 27:13-14|Rom 8:18|Ps 94:19|Matt 6:9-13|Ps 145:8-9|Heb 4:9-10|Ps 37:7|John 6:35|Ps 130:5-6|1 Cor 13:4-7|Ps 36:5-7|
Isa 40:11|Ps 138:7|Rev 21:4|Ps 57:1|Col 3:15|Ps 119:165|Jer 31:3|Ps 13:5-6|Eph 3:17-19|Ps 25:4-5|John 11:25-26|Ps 86:11|
Zeph 3:17|Ps 40:11|1 Thess 5:23-24|Ps 68:19|Isa 43:1|Ps 71:5|Rom 15:4|Ps 126:5-6|Jas 5:16|Ps 112:7|2 Cor 1:3-4|Ps 18:28|
Job 19:25|Ps 31:5|Mic 7:7-8|Ps 143:10|1 John 3:1|Ps 77:11-12|Isa 46:4|Ps 33:18|Heb 10:23|Ps 65:8|Luke 12:32|Ps 89:1|
John 15:9-11|Ps 115:1|Rev 3:20|Ps 119:49-50|Isa 58:11|Ps 125:1-2|Rom 14:8|Ps 84:1-2|Jude 1:24-25|Ps 16:11|Eph 1:17-18|
Ps 146:5-6|Matt 5:3-6|Ps 91:11|1 Pet 1:3|Ps 104:33-34|Isa 25:8-9|Ps 121:7-8|Phil 4:11-13|Ps 136:1|Rev 22:20-21|Ps 150:6"""

_CACHE, _LOCK = {}, threading.Lock()


class BibleError(Exception):
    pass


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def allowed(user):
    """Bible study is on for Lewellyn's login; the owner can switch it on for others on the Family page."""
    st = core.settings(user)
    return bool(st["bible_on"]) if "bible_on" in st else user["name"].lower() in ("lewellyn", "llewellyn")


# ---------- the translations ----------
def _files():
    out = {}
    for folder in (HERE, core.DATA / "bibles"):
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.name.endswith((".json", ".json.gz")):
                    out[f.name.split(".")[0].lower()] = f
    return out


def _load(code):
    with _LOCK:
        if code in _CACHE:
            return _CACHE[code]
        f = _files().get(code)
        if not f:
            raise BibleError("That translation is not installed.")
        try:
            with (gzip.open(f, "rt", encoding="utf-8") if f.name.endswith(".gz") else open(f, encoding="utf-8-sig")) as fh:
                d = json.load(fh)
            books = d["books"] if isinstance(d, dict) else d
            if books and isinstance(books[0], dict):                 # a common other layout: [{"chapters": [[...]]}, ...]
                books = [b["chapters"] for b in books]
            if len(books) != 66:
                raise ValueError("it does not have 66 books")
            d = {"code": code, "name": (d.get("name") if isinstance(d, dict) else None) or code.upper(),
                 "lang": (d.get("lang") if isinstance(d, dict) else None) or "en", "books": books}
        except BibleError:
            raise
        except Exception as e:
            raise BibleError(f"The translation file {f.name} could not be read ({e}).")
        _CACHE[code] = d
        return d


def versions():
    out = []
    for code in _files():
        try:
            d = _load(code)
            out.append({"code": code, "name": d["name"], "lang": d["lang"]})
        except BibleError:
            pass
    return sorted(out, key=lambda v: (v["code"] != "kjv", v["name"]))


def version_of(user):
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    v = str(core.settings(row or user).get("bible_version") or "kjv").lower()
    return v if v in _files() else "kjv"


def book_name(i, lang="en"):
    return BOOKS[i][1 if lang == "af" else 0]


def books(code="kjv"):
    d = _load(code)
    return [{"i": i, "name": book_name(i, d["lang"]), "en": BOOKS[i][0], "af": BOOKS[i][1], "chapters": len(b)} for i, b in enumerate(d["books"])]


# ---------- references ----------
_BOOKPAT = r"(?:[1-3]|i{1,3}|first|second|third|1st|2nd|3rd)?\s*[A-Za-zëêéï]{2,}(?:\s+of\s+[A-Za-z]+)?"
REF = re.compile(r"\b(" + _BOOKPAT + r")\.?\s*(\d{1,3})(?:\s*(?::|\bverse\b|\bvers\b|\bv\.?|\.)\s*(\d{1,3})(?:\s*(?:-|–|to|tot)\s*(\d{1,3}))?)?", re.I)


def find_book(name):
    k = _key(name)
    if k in ALIAS:
        return ALIAS[k]
    hits = sorted({i for a, i in ALIAS.items() if len(k) >= 3 and a.startswith(k)})
    return hits[0] if len(hits) == 1 else None


def refs_in(text, limit=5):
    """Every Bible reference in the text, in order, as (book, chapter, verse, verse_to). Only passages that exist count,
    so "I am 20" or "the top 5" are not mistaken for one."""
    out, taken = [], -1
    shape = _load("kjv")["books"]
    for start in re.finditer(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9])", text or ""):
        if start.start() < taken:
            continue
        m = REF.match(text, start.start())
        if not m:
            continue
        b = find_book(m.group(1))
        if b is None:
            continue
        ch, v, v2 = int(m.group(2)), (int(m.group(3)) if m.group(3) else None), (int(m.group(4)) if m.group(4) else None)
        if len(shape[b]) == 1 and v is None and ch > 1:          # "Jude 24": a book with one chapter, so that is the verse
            ch, v = 1, ch
        if not 1 <= ch <= len(shape[b]) or (v and not 1 <= v <= len(shape[b][ch - 1])):
            continue
        out.append((b, ch, v, (v2 if v2 and v and v2 >= v else v)))
        taken = m.end()
        if len(out) >= limit:
            break
    return out


def parse(text):
    """The first Bible reference in the text as (book, chapter, verse, verse_to), or None. verse is None for a whole chapter."""
    r = refs_in(text, 1)
    return r[0] if r else None


def label(b, ch, v=None, v2=None, lang="en"):
    s = f"{book_name(b, lang)} {ch}"
    if v:
        s += f":{v}" + (f"-{v2}" if v2 and v2 != v else "")
    return s


def passage(code, b, ch, v=None, v2=None):
    """The verses of a reference as [{"n": verse number, "t": text}], with its label."""
    d = _load(code)
    if not 0 <= b < 66 or not 1 <= ch <= len(d["books"][b]):
        raise BibleError(f"{BOOKS[b][0] if 0 <= b < 66 else 'That book'} has no chapter {ch}.")
    verses = d["books"][b][ch - 1]
    lo, hi = (v or 1), (v2 or v or len(verses))
    if lo > len(verses):
        raise BibleError(f"{label(b, ch, lang=d['lang'])} has only {len(verses)} verses.")
    hi = min(hi, len(verses))
    return {"ref": label(b, ch, v, hi if v else None, d["lang"]), "book": b, "chapter": ch, "version": code,
            "verses": [{"n": n, "t": verses[n - 1]} for n in range(lo, hi + 1)]}


def chapter(code, b, ch):
    d = _load(code)
    p = passage(code, b, ch)
    p.update(chapters=len(d["books"][b]), name=book_name(b, d["lang"]), version_name=d["name"])
    return p


# ---------- the verse of the day ----------
def daily(user, when="morning", day=None):
    """The verse for this morning or this evening. The same for everyone on a given day."""
    code = version_of(user)
    refs = [r.strip() for r in (NIGHT if when == "night" else MORNING).replace("\n", "").split("|") if r.strip()]
    day = day or datetime.date.today()
    b, ch, v, v2 = parse(refs[day.timetuple().tm_yday % len(refs)])
    p = passage(code, b, ch, v, v2)
    p.update(when=when, text=" ".join(x["t"] for x in p["verses"]), version_name=_load(code)["name"])
    return p


def say(p):
    """A passage as she reads it aloud: the reference in words, then the text."""
    d = _load(p["version"])
    ref = f"{book_name(p['book'], d['lang'])} chapter {p['chapter']}"
    ns = [x["n"] for x in p["verses"]]
    if ns and len(ns) < len(d["books"][p["book"]][p["chapter"] - 1]):
        ref += f", verse {ns[0]}" if len(ns) == 1 else f", verses {ns[0]} to {ns[-1]}"
    return ref + ". " + " ".join(x["t"] for x in p["verses"])


# ---------- search ----------
STOP = set("the a an and or of to in is are was were be been that this it for with as on at by from not his her their our my your i you he she "
           "we they them him me us will shall do does did have has had what who how why when where which about say says said bible verse "
           "verses scripture does mean explain tell me there".split())


def search(code, text, limit=60):
    d = _load(code)
    text = str(text or "").strip()
    phrase = re.findall(r'"([^"]{2,80})"', text)
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ']{2,}", re.sub(r'"[^"]*"', " ", text).lower())]
    needles = [p.lower() for p in phrase] + words
    if not needles:
        return {"query": text, "total": 0, "hits": []}
    pats = [re.compile(r"\b" + re.escape(n) + (r"\b" if n in phrase or len(n) < 4 else ""), re.I) for n in needles]
    hits, total = [], 0
    for b, bk in enumerate(d["books"]):
        for c, ch in enumerate(bk):
            for v, t in enumerate(ch):
                if all(p.search(t) for p in pats):
                    total += 1
                    if len(hits) < limit:
                        hits.append({"ref": label(b, c + 1, v + 1, lang=d["lang"]), "book": b, "chapter": c + 1, "verse": v + 1, "t": t})
    return {"query": text, "total": total, "hits": hits}


def _related(code, question, limit=10):
    """Verses that share the most of the question's meaningful words: something for the helper to answer from."""
    d = _load(code)
    words = [w for w in dict.fromkeys(re.findall(r"[a-zà-ÿ']{3,}", question.lower())) if w not in STOP][:8]
    if not words:
        return []
    pats = [re.compile(r"\b" + re.escape(w[:max(4, len(w) - 2)]), re.I) for w in words]
    scored = []
    for b, bk in enumerate(d["books"]):
        for c, ch in enumerate(bk):
            for v, t in enumerate(ch):
                n = sum(1 for p in pats if p.search(t))
                if n:
                    scored.append((-n, len(t), b, c, v))
    scored.sort()
    need = max(1, min(2, len(words)))
    return [passage(code, b, c + 1, v + 1) for n, _, b, c, v in scored[:limit] if -n >= need or len(scored) < 4]


# ---------- the study helper ----------
RULES = ("You are a careful Bible study helper. Answer the question using ONLY the passages given below. Quote or point to the "
         "reference for every point you make. Never quote a verse that is not in the passages and never invent a reference. "
         "If the passages do not answer the question, say so plainly and suggest what to look up. Where Christians read a "
         "passage differently, say that they differ instead of choosing a side. Explain in plain language, in at most 220 words. "
         "Do not preach and do not give medical, legal or financial advice.")


def helper(llm, user, question):
    """Answer a study question from the text itself. Returns {"answer", "passages", "found"}."""
    question = str(question or "").strip()[:600]
    if len(question) < 3:
        raise BibleError("Type a question, or a reference such as Romans 8:28.")
    code = version_of(user)
    d = _load(code)
    found, seen = [], set()
    for r in refs_in(question, 3):
        if r[:3] in seen:
            continue
        seen.add(r[:3])
        b, ch, v, v2 = r
        try:
            if v and (v2 or v) - v < 3:                 # a verse or two: give the helper the lines around it as well
                n = len(d["books"][b][ch - 1])
                p = passage(code, b, ch, max(1, v - 2), min(n, (v2 or v) + 2))
                p["focus"] = label(b, ch, v, v2, d["lang"])
            else:
                p = passage(code, b, ch, v, v2)
                p["verses"] = p["verses"][:40]
            found.append(p)
        except BibleError:
            pass
        if len(found) >= 3:
            break
    how = "reference"
    if not found:
        found, how = _related(code, question), "words"
    if not found:
        return {"answer": "I could not find a passage to answer that from. Try naming a book and chapter, or use other words.",
                "passages": [], "found": "nothing"}
    text = "\n\n".join(f"{p['ref']} ({d['name']}):\n" + "\n".join(f"{x['n']}. {x['t']}" for x in p["verses"]) for p in found)
    focus = "; ".join(p["focus"] for p in found if p.get("focus"))
    ask = f"Passages:\n{text}\n\nQuestion: {question}" + (f"\n(The question is about {focus}; the other lines are its context.)" if focus else "")
    try:
        answer = (llm(user, [{"role": "system", "content": RULES}, {"role": "user", "content": ask}]).get("content") or "").strip()
    except Exception as e:
        answer = ""
        core.log(user["id"], f"Bible helper: the brain did not answer ({type(e).__name__})", "waiting")
    if not answer:
        answer = "The brain is not answering just now, so here are the passages themselves."
    return {"answer": answer, "passages": found, "found": how}


# ---------- study notes ----------
def notes(user, book=None, chapter_=None, q=None):
    sql, a = "SELECT * FROM bible_notes WHERE user_id=?", [user["id"]]
    if book is not None:
        sql += " AND book=?"
        a.append(int(book))
    if chapter_ is not None:
        sql += " AND chapter=?"
        a.append(int(chapter_))
    if q:
        sql += " AND text LIKE ?"
        a.append("%" + str(q)[:80] + "%")
    lang = _load(version_of(user))["lang"]
    return [dict(r, ref=label(r["book"], r["chapter"], r["verse"], r["verse_to"], lang)) for r in core.q(sql + " ORDER BY book, chapter, verse, id", tuple(a))]


def save_note(user, text, ref=None, book=None, chapter_=None, verse=None, verse_to=None, nid=None):
    text = str(text or "").strip()[:6000]
    if not text:
        raise BibleError("The note is empty.")
    if ref:
        r = parse(str(ref))
        if not r:
            raise BibleError("I could not read that reference. Write it like John 3:16 or Psalms 23.")
        book, chapter_, verse, verse_to = r
    if book is None or chapter_ is None:
        raise BibleError("Say which passage the note is about.")
    passage(version_of(user), int(book), int(chapter_), verse, verse_to)          # refuses a passage that does not exist
    now = time.time()
    if nid:
        if not core.q("SELECT 1 FROM bible_notes WHERE id=? AND user_id=?", (int(nid), user["id"]), one=True):
            raise BibleError("That note is not yours.")
        core.x("UPDATE bible_notes SET text=?, updated=? WHERE id=? AND user_id=?", (text, now, int(nid), user["id"]))
        return int(nid)
    return core.x("INSERT INTO bible_notes(user_id,book,chapter,verse,verse_to,text,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                  (user["id"], int(book), int(chapter_), verse, verse_to or verse, text, now, now))


def delete_note(user, nid):
    core.x("DELETE FROM bible_notes WHERE id=? AND user_id=?", (int(nid), user["id"]))
