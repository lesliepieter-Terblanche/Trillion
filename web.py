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
import random
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus

import core

ROOT = core.DATA / "gensites"
TPL_DIR = Path(__file__).resolve().parent / "webtemplates"
# the look library: each entry is a self-contained, hand-built, verified template
TEMPLATES = {
    "premium":   {"file": "premium.html", "photo": False},                 # animated WebGL shader-gradient hero
    "cinematic": {"file": "cinematic.html", "photo": True, "dark": True},   # full-bleed real photo, Ken-Burns + scrim
    "object3d":  {"file": "object3d.html", "photo": False, "dark": True},   # Three.js 3D crystal hero, lit + parallax
    "editorial": {"file": "editorial.html", "photo": False},               # clean split layout, light & professional
    # hero visuals adapted from ThreeUI Community (github.com/MengTo/threeui, MIT), recoloured to the site's palette
    "ribbons":   {"file": "threeui-ribbons.html", "photo": False, "dark": True},    # dotted light ribbons, text left
    "orb":       {"file": "threeui-orb.html", "photo": False, "dark": True},        # glowing smoke orb, text left
    "liquid":    {"file": "threeui-liquid.html", "photo": False, "dark": True},     # raymarched chrome blob, text left
    "resonance": {"file": "threeui-resonance.html", "photo": False, "dark": True},  # cymatic rings, centred text
    "streams":   {"file": "threeui-streams.html", "photo": False, "dark": True},    # neon wave lines, centred text
}
# trades that suit each ThreeUI look (only used when the look is left on Auto)
THREEUI_WORDS = {
    "liquid":    ("auto", "detailing", "car ", "jewel", "watch", "luxury", "chrome", "metal", "engineer", "motor"),
    "orb":       ("spa", "beauty", "wellness", "yoga", "pilates", "massage", "holistic", "therap", "aesthetic", "skin"),
    "resonance": ("music", "sound", "audio", "recording", "dj", "venue", "event", "church", "choir", "piano", "guitar"),
    "streams":   ("gym", "fitness", "club", "night", "lounge", "bar", "gaming", "esport", "crossfit", "boxing", "tattoo"),
    "ribbons":   ("tech", "software", "it service", "it support", "digital", "agency", "data", "cloud", "cyber", "network", "startup"),
}
# professional / clean trades suit the editorial split look
EDITORIAL_WORDS = ("law", "attorney", "legal", "advocate", "account", "financ", "consult", "estate", "property",
                   "agency", "architect", "dentist", "dental", "clinic", "doctor", "medical", "optom", "physio",
                   "notary", "insurance", "broker", "recruit", "marketing", "studio")
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


# When the look is left on Auto, pick at RANDOM from a category-appropriate set so no two sites share a
# layout — but every option in the set still suits that kind of business, so it stays on-brand.
LAYOUT_POOLS = [
    (EDITORIAL_WORDS, ["editorial", "premium", "ribbons", "object3d"]),
    (("restaurant", "cafe", "coffee", "bar", "bistro", "grill", "diner", "eatery", "bakery", "deli", "restaur",
      "hotel", "guest", "lodge", "pub", "takeaway"), ["cinematic", "premium", "orb", "object3d"]),
    (("salon", "beauty", "spa", "hair", "barber", "nail", "wellness", "yoga", "pilates", "massage", "aesthetic",
      "skin", "therap"), ["cinematic", "orb", "premium", "resonance"]),
    (("gym", "fitness", "crossfit", "boxing", "club", "night", "lounge", "gaming", "esport"),
     ["streams", "premium", "object3d", "liquid"]),
    (("music", "sound", "audio", "recording", "dj", "venue", "event", "wedding", "church", "choir", "piano", "guitar"),
     ["resonance", "premium", "streams", "orb"]),
    (("auto", "detailing", "car ", "motor", "tyre", "spares", "mechanic", "panel", "workshop"),
     ["liquid", "object3d", "premium", "streams"]),
    (("jewel", "watch", "luxury", "boutique", "fashion", "interior", "decor", "furniture", "florist"),
     ["cinematic", "liquid", "premium", "object3d"]),
    (("tech", "software", "it service", "it support", "digital", "agency", "data", "cloud", "cyber", "network",
      "startup", "security", "print", "signage"), ["ribbons", "object3d", "premium", "streams"]),
    (("retail", "product", "supplier", "wholesale", "hardware", "electronic", "solar", "fabricat"),
     ["object3d", "liquid", "premium", "ribbons"]),
]
DEFAULT_POOL = ["premium", "object3d", "orb", "ribbons", "editorial"]


def _layout_pool(category, photos):
    c = str(category).lower()
    pool = next((looks for words, looks in LAYOUT_POOLS if any(w in c for w in words)), None) or DEFAULT_POOL
    out = [t for t in pool if t in TEMPLATES and not (TEMPLATES[t].get("photo") and not photos)]
    return out or ["premium"]


def pick_template(category, photos, look="", avoid=""):
    """Choose the look. An explicit look wins; otherwise pick at random from a category-appropriate pool so
    sites vary every time. `avoid` lets a revise request land on a different layout than the current one.
    Photo looks need at least one photo, so they're dropped from the pool when there are none."""
    if look in TEMPLATES:
        chosen = look
    else:
        pool = _layout_pool(category, photos)
        if avoid in pool and len(pool) > 1:
            pool = [t for t in pool if t != avoid]
        chosen = random.choice(pool)
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
_CAT_SERVICES = {
    "barber": [("Cuts & fades", "Clean, modern cuts and fades done exactly how you like them."),
               ("Beard trims", "Sharp lines and a tidy finish, every visit."),
               ("Kids' cuts", "Patient, friendly cuts the little ones actually enjoy.")],
    "hair": [("Cuts & styling", "Expert cuts and blow-outs tailored to you."),
             ("Colour", "Balayage, highlights and full colour by a careful hand."),
             ("Treatments", "Keratin, gloss and care treatments for healthy hair.")],
    "beauty": [("Facials & skin", "Relaxing, results-driven skincare treatments."),
               ("Nails", "Manicures, gels and pedicures, beautifully finished."),
               ("Waxing & brows", "Clean, precise waxing and brow shaping.")],
    "auto": [("Servicing", "Full and minor services to keep your car running right."),
             ("Repairs", "Honest diagnostics and repairs, done properly first time."),
             ("Tyres & brakes", "Fitting, balancing and brake work you can trust.")],
    "restaurant": [("Our kitchen", "Fresh, seasonal plates cooked simply and well."),
                   ("Drinks & wine", "A tight list chosen to match the menu."),
                   ("Bookings & events", "From a quiet dinner to a private celebration.")],
    "plumb": [("Leaks & repairs", "Found fast and fixed properly, no mess left behind."),
              ("Geysers", "Supply, install and repair of every make."),
              ("Blocked drains", "Cleared quickly with the right tools, not guesswork.")],
    "electric": [("Installations", "Safe, neat wiring and new installations."),
                 ("Repairs & faults", "Fault-finding and repairs, done to standard."),
                 ("Compliance", "Certificates of compliance, sorted properly.")],
    "dent": [("Check-ups & cleaning", "Gentle, thorough care with no guesswork."),
             ("Cosmetic", "Whitening, veneers and alignment for a smile you love."),
             ("Family care", "Calm, patient care for the whole family.")],
    "clean": [("Regular cleaning", "Reliable, thorough cleans on your schedule."),
              ("Deep cleans", "Top-to-bottom cleans that actually show."),
              ("Move in/out", "Spotless hand-overs for landlords and tenants.")],
    "gym": [("Memberships", "Flexible plans with everything you need to train."),
            ("Classes", "Energising group classes for every level."),
            ("Personal training", "One-on-one coaching to reach your goals.")],
}


def _fallback_services(category):
    c = str(category).lower()
    for key, items in _CAT_SERVICES.items():
        if key in c:
            return [{"t": t, "d": d} for t, d in items]
    return [{"t": "What we do", "d": "Reliable, professional work tailored to what you need."},
            {"t": "Trusted locally", "d": "A team your neighbours already recommend."},
            {"t": "Fair pricing", "d": "Clear, honest quotes with no surprises."}]


def plan(llm, user, info):
    """Ask the local brain for the site's words as JSON, grounded in the business details. Falls back to a
    clean structure built straight from the details if the model doesn't return usable JSON."""
    name = str(info.get("business") or info.get("name") or "The Business").strip()
    cat = str(info.get("category") or "").strip()
    loc = str(info.get("location") or info.get("area") or "").strip()
    svc = str(info.get("services") or "").strip()
    about = str(info.get("about") or "").strip()
    reviews = info.get("reviews") or []
    rev_text = " / ".join(str(r)[:180] for r in reviews[:3] if r) or "none"
    ask = f"""Write the words for a premium one-page website for this real business. Reply with JSON only.
Business name: {name}
Category: {cat or 'unknown'}
Area: {loc or 'unknown'}
Services/notes: {svc or 'none given'}
About: {about or 'none given'}
What customers say in reviews: {rev_text}
Shape exactly:
{{"tagline":"4-6 word tagline","eyebrow":"tiny label e.g. 'Est. 2012 · {loc or 'local'}'",
"headline":"5-8 word headline with ONE standout word in [square brackets] for a colour accent",
"sub":"one warm sentence, max 22 words","cta1":"2-3 word button","cta2":"2-3 word button",
"nav":["3 short nav words"],"navcta":"2-3 word button",
"strip":["4 tiny trust badges e.g. '12 years local','Open 7 days'"],
"services_label":"1-2 words","services_head":"short section heading","services_sub":"one sentence",
"services":[{{"t":"a REAL, specific service this {cat or 'business'} offers","d":"one sentence benefit"}} x3],
"reviews_head":"short heading","cta_head":"short closing headline","cta_sub":"one sentence"}}
For "services", name the 3 actual services a {cat or 'business like this'} provides — infer them from the category and the reviews (e.g. a barber: cuts, fades, beard trims; an auto shop: servicing, repairs, diagnostics). Never use vague filler like "quality service". Mention {name} by name in the headline or sub. English, confident, no hashtags, no emojis in text."""
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
        typed = [x.strip() for x in re.split(r"[,/;\n]", svc) if x.strip()][:3]
        if typed:
            while len(typed) < 3:
                typed.append(["Quality work", "Trusted local team", "Fair pricing"][len(typed)])
            svc_list = [{"t": b[:40], "d": f"{b.capitalize()} you can count on, done properly."} for b in typed]
        else:
            svc_list = _fallback_services(cat)
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


def _wa_number(phone):
    d = re.sub(r"\D", "", str(phone or ""))
    if not d:
        return ""
    if d.startswith("0"):
        d = "27" + d[1:]          # South Africa default
    elif len(d) == 9:
        d = "27" + d
    return d


_WA_SVG = ('<svg viewBox="0 0 32 32" width="28" height="28" fill="#fff"><path d="M16 3C9.4 3 4 8.3 4 '
           '14.9c0 2.3.7 4.5 1.9 6.4L4 29l7-1.8c1.8 1 3.9 1.5 6 1.5 6.6 0 12-5.3 12-11.9S22.6 3 16 3zm0 '
           '21.7c-1.9 0-3.7-.5-5.3-1.5l-.4-.2-4.1 1.1 1.1-4-.2-.4a9.6 9.6 0 01-1.5-5.1c0-5.4 4.4-9.7 '
           '9.9-9.7s9.9 4.3 9.9 9.7-4.4 9.8-9.9 9.8zm5.5-7.3c-.3-.1-1.8-.9-2-1-.3-.1-.5-.1-.7.1-.2.3-.8'
           '.9-.9 1.1-.2.2-.3.2-.6.1-1.8-.9-3-1.6-4.2-3.6-.3-.5.3-.5.8-1.6.1-.2 0-.4 0-.5 0-.1-.7-1.6-.9'
           '-2.2-.2-.6-.5-.5-.7-.5h-.6c-.2 0-.5.1-.8.4-.3.3-1 1-1 2.5s1.1 2.9 1.2 3.1c.2.2 2.1 3.3 5.2 '
           '4.6 2.9 1.2 2.9.8 3.5.8.5 0 1.8-.7 2-1.4.2-.7.2-1.3.2-1.4-.1-.2-.3-.2-.6-.4z"/></svg>')


def _inp(palette):
    return ("width:100%;padding:13px 16px;border-radius:12px;border:1px solid var(--border);"
            "background:var(--card);color:var(--text);font-size:15px;font-family:inherit")


def _parse_pricelist(raw):
    """Turn typed lines into {name, price, desc}. Accepts 'Name | Price | note' or 'Name - Price'."""
    items = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            name, price = parts[0], (parts[1] if len(parts) > 1 else "")
            desc = parts[2] if len(parts) > 2 else ""
        else:
            m = re.match(r"^(.*?)\s*[-–—:]\s*(R?\s?[\d][\d\s.,]*)\s*$", line)
            if m:
                name, price, desc = m.group(1).strip(), m.group(2).strip(), ""
            else:
                name, price, desc = line, "", ""
        if name:
            items.append({"name": name, "price": price, "desc": desc})
    return items[:40]


def _pricelist_html(info):
    items = info.get("pricelist_items")
    if items is None:
        items = _parse_pricelist(info.get("pricelist"))
    if not items:
        return ""
    rows = []
    for it in items:
        desc = f'<div class="muted" style="font-size:13px;margin-top:2px">{_esc(it["desc"])}</div>' if it.get("desc") else ""
        price = f'<div style="font-weight:700;color:var(--accent);white-space:nowrap;font-size:16px">{_esc(it["price"])}</div>' if it.get("price") else ""
        rows.append('<div style="display:flex;justify-content:space-between;gap:20px;align-items:baseline;'
                    'padding:16px 0;border-bottom:1px solid var(--border)">'
                    f'<div><div style="font-weight:600;font-size:16.5px">{_esc(it["name"])}</div>{desc}</div>{price}</div>')
    head = _esc(info.get("pricelist_head") or "Our prices")
    return ('<section class="pad" id="prices" style="background:var(--surface)"><div class="wrap" style="max-width:680px">'
            f'<div class="sectlabel reveal">Price list</div><h2 class="reveal">{head}</h2>'
            f'<div class="reveal" style="margin-top:30px">{"".join(rows)}</div></div></section>')


def _addons(info, palette):
    """Build the optional feature blocks the owner ticked. Returns the injection tokens (each '' if off)."""
    addons = [str(a).lower() for a in (info.get("addons") or [])]
    phone = info.get("phone") or ""
    num = _wa_number(phone)
    email = info.get("email") or ""
    booking = "booking" in addons
    out = {"PROMO": "", "CONTACT": "", "FLOAT": "", "PRICELIST": ""}
    if "pricelist" in addons:
        out["PRICELIST"] = _pricelist_html(info)
    # floating WhatsApp button
    if "whatsapp" in addons and num:
        msg = quote_plus(f"Hi, I found your website and would like to get in touch.")
        out["FLOAT"] = (f'<a href="https://wa.me/{num}?text={msg}" target="_blank" rel="noopener" aria-label="WhatsApp" '
                        'style="position:fixed;right:20px;bottom:20px;z-index:60;width:58px;height:58px;border-radius:50%;'
                        'background:#25D366;display:flex;align-items:center;justify-content:center;'
                        'box-shadow:0 12px 30px rgba(0,0,0,.35)">' + _WA_SVG + '</a>')
    # promo / advertising banner
    if "promo" in addons:
        head = _esc(info.get("promo_head") or "New here? Let’s make it worth your while")
        sub = _esc(info.get("promo_sub") or "Mention this page when you get in touch for a special welcome offer.")
        href = f"https://wa.me/{num}" if num else "#contact"
        out["PROMO"] = ('<section style="padding:0 clamp(20px,5vw,40px)"><div style="max-width:var(--maxw);margin:36px auto;'
                        'background:linear-gradient(100deg,var(--accent),var(--accent2));border-radius:22px;padding:28px 32px;'
                        'display:flex;flex-wrap:wrap;gap:18px;align-items:center;justify-content:space-between;color:#fff">'
                        f'<div><div style="font-weight:750;font-size:21px;letter-spacing:-.01em">{head}</div>'
                        f'<div style="opacity:.9;font-size:14.5px;margin-top:4px">{sub}</div></div>'
                        f'<a class="btn" href="{href}" style="background:#fff;color:#111">Claim it</a></div></section>')
    # enquiry / booking form (sends to WhatsApp, or email as a fallback)
    if "form" in addons or booking:
        head = "Book with us" if booking else "Send us a message"
        label = "Send booking request" if booking else "Send message"
        if num:
            action = (f"var t=encodeURIComponent('Hi, my name is '+n+'. '+m+' (contact: '+c+')');"
                      f"window.open('https://wa.me/{num}?text='+t,'_blank');")
        elif email:
            action = (f"window.location.href='mailto:{email}?subject=Website%20enquiry&body='+"
                      "encodeURIComponent('Name: '+n+'\\nContact: '+c+'\\n\\n'+m);")
        else:
            action = "alert('Thanks! We will be in touch.');"
        st = _inp(palette)
        out["CONTACT"] = (
            '<section class="pad" id="enquire" style="background:var(--surface)"><div class="wrap" style="max-width:640px">'
            f'<div class="sectlabel reveal">{"Booking" if booking else "Contact"}</div><h2 class="reveal">{_esc(head)}</h2>'
            '<div class="reveal" style="margin-top:26px;display:grid;gap:14px">'
            f'<input id="f_n" placeholder="Your name" style="{st}">'
            f'<input id="f_c" placeholder="Your phone or email" style="{st}">'
            f'<textarea id="f_m" rows="3" placeholder="How can we help?" style="{st}"></textarea>'
            f'<button class="btn primary" type="button" onclick="var n=document.getElementById(\'f_n\').value,'
            f'c=document.getElementById(\'f_c\').value,m=document.getElementById(\'f_m\').value;{action}">{label}</button>'
            '</div></div></section>')
    return out


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
    # a varied scroll animation each build (AOS), so no two sites move the same way
    html = html.replace("__AOS__", random.choice(["fade-up", "fade-up", "zoom-in", "fade-right",
                                                   "fade-left", "fade-up", "zoom-in-up"]))
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
    if not info.get("style"):                   # design variety: vary the palette within the right mode each time
        same = [s for s, p in PALETTES.items() if p["mode"] == palette["mode"]]
        style = random.choice(same)
        palette = PALETTES[style]
    say("Writing the copy")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}), user, info)
    content.update(_addons(info, palette))      # optional features the owner ticked
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
              "photos": len(photos_rel), "photos_rel": photos_rel, "addons": info.get("addons") or [],
              "info": {k: info.get(k) for k in
                       ("business", "category", "location", "address", "phone", "email", "hours", "services",
                        "about", "rating", "reviews", "link", "pricelist")}}
    sid = core.x("""INSERT INTO gensites(user_id,title,business,category,style,token,folder,thumb,detail,status,created)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                 (uid, name, name, cat, style, token, str(folder), thumb, json.dumps(detail), "done", time.time()))
    try:                                        # open a CRM lead for this business so it shows on the dashboard
        import leads
        leads.on_site_created(user, sid, token, info)
    except Exception:
        pass
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
                    "exists": os.path.exists(os.path.join(r["folder"], "index.html")),
                    "has_video": os.path.exists(os.path.join(r["folder"], "pitch.mp4"))})
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
    info["addons"] = d.get("addons") or []
    style = d.get("style") or "violet"
    template = d.get("template") or "premium"
    photos_rel = d.get("photos_rel") or []
    low = (change or "").lower()
    # look
    if _re.search(r"\bribbons?\b", low):
        template = "ribbons"
    elif _re.search(r"\b(orb|sphere|smoke)\b", low):
        template = "orb"
    elif _re.search(r"\b(liquid|chrome|metal|metallic|blob)\b", low):
        template = "liquid"
    elif _re.search(r"\b(resonance|rings?|ripples?|cymatic)\b", low):
        template = "resonance"
    elif _re.search(r"\b(streams?|neon|waves?)\b", low):
        template = "streams"
    elif _re.search(r"\b(3d|object|crystal)\b", low):
        template = "object3d"
    elif _re.search(r"cinematic|photo hero|photo", low):
        template = "cinematic" if photos_rel else "premium"
    elif _re.search(r"gradient|shader|animated", low):
        template = "premium"
    elif _re.search(r"different|another|new|change the|other|vary|fresh|redesign|re-?design", low) and \
            _re.search(r"layout|design|look|template|style", low):
        template = pick_template(info.get("category", ""), photos_rel, avoid=template)   # reroll to a new layout
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
                       r"shader|animated|colour|color|make|it|the|to|change|look|style|ribbons?|orb|sphere|smoke|liquid|chrome|"
                       r"metal|metallic|blob|resonance|rings?|ripples?|cymatic|streams?|neon|waves?|" + "|".join(_COLOUR) + r")\b",
                       " ", low)
    note = change if len(_re.sub(r"[^a-z0-9]+", "", leftover)) >= 4 else ""
    # add or remove optional features
    addons = set(info.get("addons") or [])
    if _re.search(r"whats\s?app", low):
        addons.add("whatsapp")
    if _re.search(r"enquiry|contact form|message form|\bform\b|get in touch", low):
        addons.add("form")
    if _re.search(r"booking|book online|appointment|reservation", low):
        addons.add("booking")
    if _re.search(r"promo|offer|advert|banner|special", low):
        addons.add("promo")
    if _re.search(r"price list|pricelist|\bprices\b|\bmenu\b", low):
        addons.add("pricelist")
    for w, a in (("whatsapp", "whatsapp"), ("form", "form"), ("booking", "booking"), ("promo", "promo"),
                 ("banner", "promo"), ("advert", "promo")):
        if _re.search(r"(remove|no|without|drop)\s[\w\s]*" + w, low):
            addons.discard(a)
    info["addons"] = list(addons)
    say("Rewriting the site")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}),
                   user, dict(info, change_note=note))
    content.update(_addons(info, palette))
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
    d.update(style=style, mode=palette["mode"], template=template, addons=info["addons"])
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
