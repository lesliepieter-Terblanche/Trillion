"""Web Studio: turn a business's details into a finished, ultra-premium one-page website - hosted on this PC
with a shareable link and downloadable as a single self-contained HTML file.

Built for the lead-gen play: enter what a Google Maps search gives you (name, category, area, phone, a few
photos, reviews), press Generate, and the local brain writes the copy and picks a palette while a hand-built,
cinematic template (animated WebGL hero, scroll reveals, premium sections) carries the polish. Light or dark
is chosen to suit the trade. The quality comes from the template, not the model guessing at code.
"""
import glob
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

import core

ROOT = core.DATA / "gensites"
TPL_DIR = Path(__file__).resolve().parent / "webtemplates"
# the look library: each entry is a self-contained, hand-built, verified template
TEMPLATES = {
    "premium":   {"file": "premium.html", "photo": False},                 # animated WebGL shader-gradient hero
    "cinematic": {"file": "cinematic.html", "photo": True, "dark": True},   # full-bleed real photo, Ken-Burns + scrim
    "object3d":  {"file": "object3d.html", "photo": False, "dark": True},   # Three.js 3D crystal hero, lit + parallax
}
# categories that suit a 3D-object hero (products on show, modern/tech feel)
OBJECT_WORDS = ("retail", "product", "auto part", "car part", "motor", "tyre", "spares", "tech",
                "software", "electronic", "jewel", "watch", "furniture", "agency", "solar",
                "security", "print", "signage", "fabricat", "hardware", "supplier", "wholesale")
# categories that look best as a cinematic photo hero (when the business has usable photos)
CINEMATIC_WORDS = ("restaurant", "cafe", "coffee", "bar", "bistro", "grill", "diner", "eatery", "bakery",
                   "deli", "salon", "beauty", "spa", "hair", "barber", "nail", "hotel", "guest", "lodge",
                   "venue", "event", "wedding", "photo", "florist", "boutique", "interior", "decor",
                   "gym", "fitness", "yoga", "pilates", "tattoo", "lounge", "club")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def init():
    core.x("""CREATE TABLE IF NOT EXISTS gensites(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, business TEXT, category TEXT,
        style TEXT, token TEXT, folder TEXT, thumb TEXT, detail TEXT, status TEXT, created REAL)""")
    ROOT.mkdir(parents=True, exist_ok=True)


# ---------- styling ----------
# a spread of palettes so sites don't all look the same; each is a complete token set for the template
PALETTES = {
    "violet":  dict(mode="dark", C_BG="#0a0a0f", C_ACCENT="#7c5cff", C_ACCENT2="#d94fd0", C_GLOW="#3b2a8c",
                    C_SURFACE="#0b0b11", C_CARD="#15151f", C_BORDER="rgba(255,255,255,.08)", C_TEXT="#f3f3f8",
                    C_MUTED="#9a9ab0", C_HEROTEXT="#f6f6fb", C_HEROMUTED="#bcbcd0", C_VIGN="rgba(0,0,0,.55)"),
    "ember":   dict(mode="dark", C_BG="#0c0906", C_ACCENT="#ff7a3d", C_ACCENT2="#ffd23d", C_GLOW="#5c2d14",
                    C_SURFACE="#0c0907", C_CARD="#181310", C_BORDER="rgba(255,255,255,.08)", C_TEXT="#f6f2ee",
                    C_MUTED="#b3a79c", C_HEROTEXT="#fbf7f2", C_HEROMUTED="#d2c5b8", C_VIGN="rgba(0,0,0,.55)"),
    "teal":    dict(mode="dark", C_BG="#05100f", C_ACCENT="#2fe6c0", C_ACCENT2="#39b6ff", C_GLOW="#0f4a44",
                    C_SURFACE="#060f0e", C_CARD="#101c1a", C_BORDER="rgba(255,255,255,.08)", C_TEXT="#eef6f4",
                    C_MUTED="#8fa8a2", C_HEROTEXT="#f1faf8", C_HEROMUTED="#aecfc8", C_VIGN="rgba(0,0,0,.55)"),
    "azure":   dict(mode="light", C_BG="#ffffff", C_ACCENT="#2f6bff", C_ACCENT2="#17b9a6", C_GLOW="#cfe0ff",
                    C_SURFACE="#f6f8fc", C_CARD="#ffffff", C_BORDER="rgba(12,22,44,.09)", C_TEXT="#101627",
                    C_MUTED="#586074", C_HEROTEXT="#0f1526", C_HEROMUTED="#424d66", C_VIGN="rgba(255,255,255,.4)"),
    "emerald": dict(mode="light", C_BG="#ffffff", C_ACCENT="#0b9d63", C_ACCENT2="#39c0a6", C_GLOW="#cdeede",
                    C_SURFACE="#f5faf7", C_CARD="#ffffff", C_BORDER="rgba(12,34,24,.09)", C_TEXT="#0e1f18",
                    C_MUTED="#53685e", C_HEROTEXT="#0e1f18", C_HEROMUTED="#3c5349", C_VIGN="rgba(255,255,255,.4)"),
    "slate":   dict(mode="light", C_BG="#ffffff", C_ACCENT="#5b5bf0", C_ACCENT2="#8b5cf6", C_GLOW="#dcdcff",
                    C_SURFACE="#f7f7fb", C_CARD="#ffffff", C_BORDER="rgba(20,20,45,.09)", C_TEXT="#13132a",
                    C_MUTED="#5a5a72", C_HEROTEXT="#13132a", C_HEROMUTED="#44445e", C_VIGN="rgba(255,255,255,.4)"),
}
DARK_WORDS = ("restaurant", "bar", "cafe", "coffee", "bistro", "grill", "diner", "eatery", "pub", "lounge",
              "club", "salon", "barber", "beauty", "spa", "tattoo", "gym", "fitness", "studio", "photography",
              "event", "auto", "detailing", "car ", "night", "music", "interior", "jewel", "fashion", "boutique")
STYLE_BY_WORD = {"restaurant": "ember", "cafe": "ember", "coffee": "ember", "grill": "ember", "bistro": "ember",
                 "bar": "violet", "lounge": "violet", "club": "violet", "salon": "violet", "beauty": "violet",
                 "spa": "teal", "gym": "teal", "fitness": "teal", "auto": "teal", "detailing": "teal",
                 "dentist": "azure", "dental": "azure", "clinic": "azure", "doctor": "azure", "medical": "azure",
                 "physio": "azure", "optometr": "azure", "law": "slate", "attorney": "slate", "legal": "slate",
                 "account": "slate", "financ": "slate", "consult": "slate", "it ": "slate", "software": "slate",
                 "plumb": "azure", "electric": "azure", "build": "azure", "construct": "azure", "clean": "emerald",
                 "landscap": "emerald", "garden": "emerald", "pest": "emerald", "solar": "emerald", "farm": "emerald"}


def pick_style(category, hint=""):
    c = (str(category) + " " + str(hint)).lower()
    for word, pal in STYLE_BY_WORD.items():
        if word in c:
            return pal
    return "violet" if any(w in c for w in DARK_WORDS) else "azure"


def _dark_style(category):
    """A dark palette suited to the trade — cinematic photo heroes need one for legible text over the scrim."""
    c = str(category).lower()
    if any(w in c for w in ("restaurant", "cafe", "coffee", "grill", "bistro", "bakery", "deli", "diner")):
        return "ember"
    if any(w in c for w in ("gym", "fitness", "spa", "yoga", "auto", "detailing")):
        return "teal"
    return "violet"


def pick_template(category, photos, look=""):
    """Choose the look. Explicit look wins; otherwise a cinematic photo hero for the right trades with photos,
    else the animated shader hero. Cinematic needs at least one photo, so fall back if there are none."""
    if look in TEMPLATES:
        chosen = look
    else:
        c = str(category).lower()
        if photos and any(w in c for w in CINEMATIC_WORDS):
            chosen = "cinematic"
        elif any(w in c for w in OBJECT_WORDS):
            chosen = "object3d"
        else:
            chosen = "premium"
    if TEMPLATES[chosen].get("photo") and not photos:   # a photo look with no photos -> fall back
        chosen = "object3d" if any(w in str(category).lower() for w in OBJECT_WORDS) else "premium"
    return chosen


# ---------- template filling ----------
def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _headline(text):
    """One [word or phrase] in the headline becomes the gradient accent; split the rest into animated words."""
    text = _esc(text)
    text = text.replace("[", "§G§").replace("]", "§/§")
    words = " ".join(f'<span class="word">{w}</span>' for w in text.split(" ") if w)
    return words.replace("§G§", '<span class="grad">').replace("§/§", "</span>")


ICONS = ["✨", "⭐", "\U0001f525", "\U0001f3af", "\U0001f6e0️", "\U0001f91d", "\U0001f4c8", "\U0001f3c6"]


def _services_html(items):
    out = []
    for i, it in enumerate(items):
        out.append(f'<div class="card reveal"><div class="ic">{it.get("ic") or ICONS[i % len(ICONS)]}</div>'
                   f'<h3>{_esc(it.get("t"))}</h3><p>{_esc(it.get("d"))}</p></div>')
    return "".join(out)


def _reviews_html(items):
    out = []
    for it in items:
        out.append(f'<div class="rev reveal"><div class="stars">★★★★★</div>'
                   f'<p>“{_esc(it.get("t"))}”</p><div class="who">{_esc(it.get("n"))} '
                   f'<span>· {_esc(it.get("w") or "Google review")}</span></div></div>')
    return "".join(out)


def _gallery_section(photos_rel):
    if not photos_rel:
        return ""
    tiles = "".join(f'<div class="tile"><img src="{p}" alt="" loading="lazy"></div>' for p in photos_rel)
    return ('<section class="pad" style="background:var(--surface)"><div class="wrap">'
            '<div class="sectlabel reveal">Gallery</div><h2 class="reveal">A look inside</h2>'
            f'<div class="gallery" style="margin-top:44px">{tiles}</div></div></section>')


# ---------- photos ----------
def _photos_into(folder, photos):
    """Copy/download up to 6 usable photos into the site folder; return relative filenames. Best-effort."""
    rel = []
    if not photos:
        return rel
    try:
        import httpx
        from PIL import Image, ImageOps
    except Exception:
        return rel
    for i, src in enumerate(list(photos)[:12]):
        if len(rel) >= 6:
            break
        try:
            raw = None
            if isinstance(src, str) and src.lower().startswith("http"):
                r = httpx.get(src, timeout=15, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; Trillion)"})
                if r.status_code == 200 and r.content:
                    raw = r.content
            elif isinstance(src, str) and os.path.exists(src):
                raw = Path(src).read_bytes()
            if not raw:
                continue
            import io
            with Image.open(io.BytesIO(raw)) as im:
                if min(im.size) < 350:
                    continue
                name = f"photo{i}.jpg"
                ImageOps.exif_transpose(im).convert("RGB").save(folder / name, "JPEG", quality=88)
            rel.append(name)
        except Exception:
            continue
    return rel


# ---------- the brain writes the copy ----------
def plan(llm, user, info):
    """Ask the local brain for the site's words as JSON, grounded in the business details. Falls back to a
    clean structure built straight from the details if the model doesn't return usable JSON."""
    name = str(info.get("business") or info.get("name") or "The Business").strip()
    cat = str(info.get("category") or "").strip()
    loc = str(info.get("location") or info.get("area") or "").strip()
    svc = str(info.get("services") or "").strip()
    about = str(info.get("about") or "").strip()
    reviews = info.get("reviews") or []
    ask = f"""Write the words for a premium one-page website for this real business. Reply with JSON only.
Business: {name}
Category: {cat or 'unknown'}
Area: {loc or 'unknown'}
Services/notes: {svc or 'none given'}
About: {about or 'none given'}
Shape exactly:
{{"tagline":"4-6 word tagline","eyebrow":"tiny label e.g. 'Est. 2012 · {loc or 'local'}'",
"headline":"5-8 word headline with ONE standout word in [square brackets] for a colour accent",
"sub":"one warm sentence, max 22 words","cta1":"2-3 word button","cta2":"2-3 word button",
"nav":["3 short nav words"],"navcta":"2-3 word button",
"strip":["4 tiny trust badges e.g. '12 years local','Open 7 days'"],
"services_label":"1-2 words","services_head":"short section heading","services_sub":"one sentence",
"services":[{{"t":"service name","d":"one sentence benefit"}} x3],
"reviews_head":"short heading","cta_head":"short closing headline","cta_sub":"one sentence"}}
Keep it specific to this business and category. English, confident, no hashtags, no emojis in text."""
    if info.get("change_note"):
        ask += f"\nAlso apply this change the owner asked for: {info['change_note']}"
    data = {}
    try:
        text = (llm(user, [{"role": "system", "content": "You are a senior web copywriter. Reply with JSON only."},
                           {"role": "user", "content": ask}]).get("content") or "")
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        data = {}

    def g(k, d=""):
        v = data.get(k)
        return v if v else d
    svc_list = data.get("services") if isinstance(data.get("services"), list) else []
    svc_list = [s for s in svc_list if isinstance(s, dict) and s.get("t")][:3]
    if len(svc_list) < 3:
        base = [x.strip() for x in re.split(r"[,/;\n]", svc) if x.strip()][:3]
        while len(base) < 3:
            base.append(["Quality service", "Trusted local team", "Fair, honest pricing"][len(base)])
        svc_list = [{"t": b[:40], "d": f"{b.capitalize()} you can count on, done properly."} for b in base]
    rev_list = []
    for r in (reviews or [])[:3]:
        if isinstance(r, dict) and r.get("t"):
            rev_list.append({"t": r.get("t"), "n": r.get("n") or "Google review", "w": r.get("w") or "Google"})
        elif isinstance(r, str) and r.strip():
            rev_list.append({"t": r.strip(), "n": "A happy customer", "w": "Google review"})
    while len(rev_list) < 3:
        rev_list.append([{"t": f"Genuinely the best {cat or 'service'} in {loc or 'the area'}. Highly recommend.",
                          "n": "Local customer", "w": "Google review"},
                         {"t": "Professional, friendly and great value. We'll be back.",
                          "n": "Happy client", "w": "Google review"},
                         {"t": "Could not fault them. Exactly what we needed.",
                          "n": "Verified review", "w": "Google review"}][len(rev_list)])
    nav = data.get("nav") if isinstance(data.get("nav"), list) else []
    nav = [str(x)[:16] for x in nav][:3] or ["Services", "About", "Contact"]
    strip = data.get("strip") if isinstance(data.get("strip"), list) else []
    strip = [str(x)[:28] for x in strip][:4] or ([f"★ {info.get('rating')} on Google"] if info.get("rating") else []) \
        + ["Trusted locally", "Free quotes", "Friendly service"]
    return {
        "BRAND": _esc(name.upper()), "TAGLINE": _esc(g("tagline", cat or "Welcome")),
        "EYEBROW": _esc(g("eyebrow", (f"{loc} · " if loc else "") + (cat or "Established"))),
        "HEADLINE_HTML": _headline(g("headline", f"{name}, done [properly]")),
        "SUB": _esc(g("sub", about or f"A trusted {cat or 'local business'}{(' in ' + loc) if loc else ''} — quality work and a warm welcome.")),
        "CTA1": _esc(g("cta1", "Get in touch")), "CTA2": _esc(g("cta2", "Our services")),
        "NAV": "".join(f'<a href="#services">{_esc(w)}</a>' for w in nav), "NAVCTA": _esc(g("navcta", "Contact us")),
        "STRIP": "".join(f"<span>{_esc(s)}</span>" for s in strip),
        "SERVICES_LABEL": _esc(g("services_label", "What we do")), "SERVICES_HEAD": _esc(g("services_head", "Built on doing it right")),
        "SERVICES_SUB": _esc(g("services_sub", f"Everything you'd expect from a top {cat or 'local business'}, and nothing you wouldn't.")),
        "SERVICES": _services_html(svc_list),
        "REVIEWS_HEAD": _esc(g("reviews_head", "What people say")), "REVIEWS": _reviews_html(rev_list),
        "CTA_HEAD": _esc(g("cta_head", "Ready when you are")),
        "CTA_SUB": _esc(g("cta_sub", "Get in touch today and we'll take it from there.")),
        "PHONE": _esc(info.get("phone") or ""), "ADDR": _esc(info.get("location") or info.get("address") or ""),
        "HOURS": _esc(info.get("hours") or "By appointment"),
    }


def render(content, palette, photos_rel, template="premium"):
    tpl = TEMPLATES.get(template, TEMPLATES["premium"])
    html = (TPL_DIR / tpl["file"]).read_text(encoding="utf-8")
    content = dict(content)
    content["GALLERY_SECTION"] = _gallery_section(photos_rel[1:] if tpl.get("photo") else photos_rel)
    if tpl.get("photo"):
        content["HERO_PHOTO"] = photos_rel[0] if photos_rel else ""
    for k, v in palette.items():
        if k != "mode":
            html = html.replace("__" + k + "__", v)
    for k, v in content.items():
        html = html.replace("__" + k + "__", v)
    return html


# ---------- build ----------
def build(user, info, progress=None):
    init()
    say = progress or (lambda t: None)
    uid = user["id"]
    link = str(info.get("link") or "").strip()
    if link:                                    # a Google Maps link -> pull the business details from it
        say("Reading the business from the link")
        try:
            import maps
            ex = maps.extract(user, link)
            merged = {k: v for k, v in ex.items() if not str(k).startswith("_")}
            for k, v in info.items():           # anything typed in by hand wins over the link
                if v not in (None, "", []):
                    merged[k] = v
            info = merged
        except Exception:
            pass
    name = str(info.get("business") or info.get("name") or "Business").strip()
    cat = str(info.get("category") or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "site"
    token = secrets.token_urlsafe(9)
    folder = ROOT / f"{slug}-{int(time.time())}"
    folder.mkdir(parents=True, exist_ok=True)
    say("Pulling in the photos")
    photos_rel = _photos_into(folder, info.get("photos") or [])
    # choose the look (template), then a palette that suits it
    template = pick_template(cat, photos_rel, info.get("look") or "")
    style = info.get("style") or pick_style(cat, name)
    palette = PALETTES.get(style, PALETTES["violet"])
    if TEMPLATES[template].get("dark") and palette.get("mode") != "dark":
        style = _dark_style(cat)
        palette = PALETTES[style]
    say("Writing the copy")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}), user, info)
    say("Building the website")
    html = render(content, palette, photos_rel, template)
    (folder / "index.html").write_text(html, encoding="utf-8")
    thumb = ""
    try:
        say("Taking a preview shot")
        import sitegrab
        if sitegrab.ready():
            tp = folder / "thumb.jpg"
            if sitegrab.screenshot("file://" + str(folder / "index.html"), str(tp)):
                thumb = str(tp)
    except Exception:
        pass
    detail = {"style": style, "mode": palette["mode"], "template": template, "business": name, "category": cat,
              "photos": len(photos_rel), "photos_rel": photos_rel,
              "info": {k: info.get(k) for k in
                       ("business", "category", "location", "address", "phone", "hours", "services",
                        "about", "rating", "reviews", "link")}}
    sid = core.x("""INSERT INTO gensites(user_id,title,business,category,style,token,folder,thumb,detail,status,created)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                 (uid, name, name, cat, style, token, str(folder), thumb, json.dumps(detail), "done", time.time()))
    return {"site_id": sid, "title": name, "token": token, "folder": str(folder), "style": style,
            "mode": palette["mode"], "photos": len(photos_rel),
            "notes": [f"Premium {palette['mode']} site for {name}" + (f" ({photos_rel and str(len(photos_rel)) + ' real photos' or 'no photos yet'})")]}


# ---------- background job + serving ----------
def start(user, info):
    init()
    label = info.get("business") or ("the linked business" if info.get("link") else "a business")
    tid = core.add_task(user["id"], f"Website for {label}", domain="work") if hasattr(core, "add_task") else None

    def go():
        try:
            core.BUSY_WEB = True
            build(user, info, progress=(lambda t: core.set_task(tid, detail=t)) if tid else None)
            if tid:
                core.set_task(tid, status="completed", detail="Website ready")
        except Exception as e:
            if tid:
                core.set_task(tid, status="failed", detail=f"Could not build the site: {type(e).__name__}")
            core.log(user["id"], f"Website build failed: {type(e).__name__}: {e}"[:200], "error")
    threading.Thread(target=go, daemon=True).start()
    return {"task_id": tid, "started": True}


def start_revise(user, sid, change):
    init()
    row = core.q("SELECT title FROM gensites WHERE id=? AND user_id=?", (sid, user["id"]), one=True)
    tid = core.add_task(user["id"], f"Change website: {(row['title'] if row else 'site')}", domain="work") \
        if hasattr(core, "add_task") else None

    def go():
        try:
            res = revise(user, sid, change, progress=(lambda t: core.set_task(tid, detail=t)) if tid else None)
            if tid:
                core.set_task(tid, status="failed" if res.get("error") else "completed",
                              detail=res.get("error") or "Website updated")
        except Exception as e:
            if tid:
                core.set_task(tid, status="failed", detail=f"Could not change the site: {type(e).__name__}")
    threading.Thread(target=go, daemon=True).start()
    return {"task_id": tid, "started": True}


def _row(user, sid):
    return core.q("SELECT * FROM gensites WHERE id=? AND user_id=?", (sid, user["id"]), one=True)


def by_token(token):
    return core.q("SELECT * FROM gensites WHERE token=?", (token,), one=True)


def preview_file(token, path=""):
    row = by_token(token)
    if not row:
        return None
    base = Path(row["folder"])
    p = (base / (path or "index.html")).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return str(p) if p.exists() else None


def html_file(user, sid):
    row = _row(user, sid)
    if not row:
        return None
    f = Path(row["folder"]) / "index.html"
    return str(f) if f.exists() else None


def listing(user):
    init()
    out = []
    for r in core.q("SELECT * FROM gensites WHERE user_id=? ORDER BY id DESC", (user["id"],)):
        d = json.loads(r["detail"] or "{}")
        out.append({"id": r["id"], "title": r["title"], "business": r["business"], "category": r["category"],
                    "style": r["style"], "mode": d.get("mode"), "token": r["token"], "photos": d.get("photos", 0),
                    "has_thumb": bool(r["thumb"] and os.path.exists(r["thumb"])), "created": r["created"],
                    "exists": os.path.exists(os.path.join(r["folder"], "index.html"))})
    return out


_COLOUR = {"violet": "violet", "purple": "violet", "blue": "azure", "azure": "azure", "teal": "teal",
           "cyan": "teal", "green": "emerald", "emerald": "emerald", "warm": "ember", "orange": "ember",
           "red": "ember", "amber": "ember", "ember": "ember", "slate": "slate", "indigo": "slate",
           "grey": "slate", "gray": "slate"}
_TO_LIGHT = {"violet": "slate", "ember": "azure", "teal": "emerald"}
_TO_DARK = {"azure": "violet", "emerald": "teal", "slate": "violet"}


def revise(user, sid, change, progress=None):
    """Regenerate a site with a plain-language change (dark/light, a different look or colours, reworded copy),
    keeping the same folder and preview link so anything already shared stays valid."""
    import re as _re
    say = progress or (lambda t: None)
    row = _row(user, sid)
    if not row:
        return {"error": f"I couldn't find that website (#{sid}) to change."}
    d = json.loads(row["detail"] or "{}")
    info = dict(d.get("info") or {})
    style = d.get("style") or "violet"
    template = d.get("template") or "premium"
    photos_rel = d.get("photos_rel") or []
    low = (change or "").lower()
    # look
    if _re.search(r"\b(3d|object|crystal)\b", low):
        template = "object3d"
    elif _re.search(r"cinematic|photo hero|photo", low):
        template = "cinematic" if photos_rel else "premium"
    elif _re.search(r"gradient|shader|animated", low):
        template = "premium"
    # colour
    for w, s in _COLOUR.items():
        if _re.search(r"\b" + w + r"\b", low):
            style = s
            break
    # light / dark
    want = "light" if _re.search(r"\blight\b|brighter|bright", low) else ("dark" if _re.search(r"\bdark\b|moody|black", low) else None)
    if want and PALETTES[style]["mode"] != want:
        style = (_TO_LIGHT if want == "light" else _TO_DARK).get(style, "azure" if want == "light" else "violet")
    if want == "light" and TEMPLATES[template].get("dark"):
        template = "premium"                      # the photo/3D looks are dark-only
    palette = PALETTES.get(style, PALETTES["violet"])
    if TEMPLATES[template].get("dark") and palette["mode"] != "dark":
        style = _dark_style(info.get("category", ""))
        palette = PALETTES[style]
    # is there a wording change beyond the design words?
    leftover = _re.sub(r"\b(light|dark|bright|brighter|moody|black|3d|object|crystal|cinematic|photo|hero|gradient|"
                       r"shader|animated|colour|color|make|it|the|to|change|look|style|" + "|".join(_COLOUR) + r")\b",
                       " ", low)
    note = change if len(_re.sub(r"[^a-z0-9]+", "", leftover)) >= 4 else ""
    say("Rewriting the site")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}),
                   user, dict(info, change_note=note))
    html = render(content, palette, photos_rel, template)
    folder = Path(row["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    thumb = row["thumb"]
    try:
        say("Taking a preview shot")
        import sitegrab
        if sitegrab.ready():
            tp = folder / "thumb.jpg"
            if sitegrab.screenshot("file://" + str(folder / "index.html"), str(tp)):
                thumb = str(tp)
    except Exception:
        pass
    d.update(style=style, mode=palette["mode"], template=template)
    core.x("UPDATE gensites SET style=?, detail=?, thumb=? WHERE id=?", (style, json.dumps(d), thumb, sid))
    return {"site_id": sid, "title": row["title"], "token": row["token"], "style": style,
            "mode": palette["mode"], "template": template, "notes": [f"Updated: now the {palette['mode']} {template} look."]}


def forget(user, sid):
    row = _row(user, sid)
    if row:
        try:
            shutil.rmtree(row["folder"], ignore_errors=True)
        except Exception:
            pass
        core.x("DELETE FROM gensites WHERE id=?", (sid,))
    return {"ok": True}
