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
    "aurora":    {"file": "threeui-aurora.html", "photo": False, "dark": True},      # horizon aurora bands
    "bells":     {"file": "threeui-bells.html", "photo": False, "dark": True},       # radial standing-wave rings
    "dotgrid":   {"file": "threeui-dotgrid.html", "photo": False, "dark": True},     # pulsing dot matrix
    "laser":     {"file": "threeui-laser.html", "photo": False, "dark": True},       # sweeping neon beams
    "pixels":    {"file": "threeui-pixels.html", "photo": False, "dark": True},      # retro pixel field
    "glass":     {"file": "threeui-glass.html", "photo": False, "dark": True},       # drifting frosted-glass blobs
    "constellation": {"file": "threeui-constellation.html", "photo": False, "dark": True},  # points network
    "ember":     {"file": "threeui-ember.html", "photo": False, "dark": True},       # rising embers
    "globe":     {"file": "threeui-globe.html", "photo": False, "dark": True},        # rotating dotted globe
    "topo":      {"file": "threeui-topo.html", "photo": False, "dark": True},         # topographic contour lines
    "portal":    {"file": "threeui-portal.html", "photo": False, "dark": True},       # swirling portal vortex
    # premium animated WebGL2 shaders adapted from paper-design/shaders (Apache-2.0), recoloured to the palette
    "mesh":      {"file": "paper-mesh.html", "photo": False, "dark": True},            # soft animated mesh gradient
    "swirl":     {"file": "paper-swirl.html", "photo": False},                          # bold cinematic spiral, light/dark
    "neuro":     {"file": "paper-neuro.html", "photo": False},                          # organic neuro-plasma flow
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


# Premium Google-Fonts pairings (all OFL), rotated per build so sites don't share a typographic identity.
# head = display font for headings/logo, body = text font. Loaded from Google Fonts with a clean system
# fallback, so a blocked/offline load degrades gracefully instead of breaking the layout.
_SANS_FB = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"
_SERIF_FB = "Georgia,'Times New Roman',serif"
FONT_PAIRS = [
    {"head": "Sora", "body": "Inter", "hw": "600;700;800", "serif": False},
    {"head": "Fraunces", "body": "Inter", "hw": "500;600;700", "serif": True},
    {"head": "Space Grotesk", "body": "Inter", "hw": "500;600;700", "serif": False},
    {"head": "Playfair Display", "body": "Source Sans 3", "hw": "600;700;800", "serif": True},
    {"head": "Poppins", "body": "Inter", "hw": "600;700;800", "serif": False},
    {"head": "DM Serif Display", "body": "DM Sans", "hw": "400", "serif": True},
    {"head": "Archivo", "body": "Inter", "hw": "600;700;800", "serif": False},
    {"head": "Bricolage Grotesque", "body": "Inter", "hw": "600;700;800", "serif": False},
    {"head": "Epilogue", "body": "Inter", "hw": "600;700;800", "serif": False},
]


def _font_tokens(font=None):
    """Return (__FONT__ body family, __FONTDEF__ <link>+heading CSS) for a chosen or random pairing."""
    pair = FONT_PAIRS[font % len(FONT_PAIRS)] if isinstance(font, int) else random.choice(FONT_PAIRS)
    head, body, hw = pair["head"], pair["body"], pair["hw"]
    fam = f"{head.replace(' ', '+')}:wght@{hw}&family={body.replace(' ', '+')}:wght@400;500;600;700"
    link = f"https://fonts.googleapis.com/css2?family={fam}&display=swap"
    hfb = _SERIF_FB if pair["serif"] else _SANS_FB
    fontdef = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
               '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
               f'<link rel="stylesheet" href="{link}">'
               f"<style>h1,h2,h3,h4,.brand{{font-family:'{head}',{hfb}}}</style>")
    return f"'{body}',{_SANS_FB}", fontdef


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
      "hotel", "guest", "lodge", "pub", "takeaway"), ["cinematic", "ember", "premium", "orb"]),
    (("salon", "beauty", "spa", "hair", "barber", "nail", "wellness", "yoga", "pilates", "massage", "aesthetic",
      "skin", "therap"), ["cinematic", "orb", "neuro", "premium", "resonance", "bells"]),
    (("gym", "fitness", "crossfit", "boxing", "club", "night", "lounge", "gaming", "esport"),
     ["streams", "premium", "portal", "laser", "dotgrid"]),
    (("music", "sound", "audio", "recording", "dj", "venue", "event", "wedding", "church", "choir", "piano", "guitar"),
     ["resonance", "bells", "swirl", "portal", "streams", "premium"]),
    (("auto", "detailing", "car ", "motor", "tyre", "spares", "mechanic", "panel", "workshop"),
     ["liquid", "object3d", "premium", "streams"]),
    (("jewel", "watch", "luxury", "boutique", "fashion", "interior", "decor", "furniture", "florist"),
     ["cinematic", "liquid", "glass", "premium"]),
    (("property", "estate", "real estate", "letting", "rental", "architect", "construct", "build", "solar",
      "landscap", "garden", "farm", "eco-", "eco ", "green", "travel", "tour", "lodge", "guest", "outdoor", "survey", "mining"),
     ["aurora", "topo", "premium", "editorial", "object3d"]),
    (("tech", "software", "it service", "it support", "digital", "agency", "data", "cloud", "cyber", "network",
      "startup", "security", "print", "signage", "telecom", "fibre", "isp"),
     ["mesh", "dotgrid", "constellation", "neuro", "globe", "ribbons", "object3d"]),
    (("gaming", "arcade", "retro", "creative", "design studio", "media", "vfx", "animation", "nightlife", "festival"),
     ["pixels", "swirl", "portal", "mesh", "dotgrid", "streams", "premium"]),
    (("consult", "finance", "account", "advis", "corporate", "legal", "insurance", "broker"),
     ["glass", "mesh", "editorial", "premium", "constellation"]),
    (("retail", "product", "supplier", "wholesale", "hardware", "electronic", "fabricat", "logistics",
      "courier", "freight", "shipping", "trading", "import", "export", "distribut"),
     ["object3d", "globe", "dotgrid", "premium", "constellation"]),
]
DEFAULT_POOL = ["premium", "mesh", "object3d", "orb", "ribbons", "editorial"]


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


def _svc_icon(it, i):
    """A crisp SVG icon for a service — honour an explicit one, else choose by what the service is about."""
    import webicons
    key = it.get("ic")
    if key and key in webicons._ICON:
        return webicons._svg(key)
    return webicons.svg_for((it.get("t") or "") + " " + (it.get("d") or ""), i)


def _services_cards(items):
    out = []
    for i, it in enumerate(items):
        out.append(f'<div class="card reveal"><div class="ic" style="color:#fff">{_svc_icon(it, i)}</div>'
                   f'<h3>{_esc(it.get("t"))}</h3><p>{_esc(it.get("d"))}</p></div>')
    return '<div class="grid g3">' + "".join(out) + "</div>"


def _services_rows(items):
    out = []
    for i, it in enumerate(items):
        out.append('<div class="reveal" style="display:flex;gap:20px;align-items:flex-start;padding:22px 24px;'
                   'border:1px solid var(--border);border-radius:18px;background:var(--card)">'
                   '<div style="flex:none;width:54px;height:54px;border-radius:15px;display:flex;align-items:center;'
                   'justify-content:center;background:var(--surface);color:var(--accent)">' + _svc_icon(it, i) + '</div>'
                   f'<div><h3 style="margin:0 0 6px;font-size:20px;line-height:1.2">{_esc(it.get("t"))}</h3>'
                   f'<p style="margin:0;color:var(--muted);line-height:1.6">{_esc(it.get("d"))}</p></div></div>')
    return '<div style="display:grid;gap:16px;margin-top:44px;max-width:760px">' + "".join(out) + "</div>"


def _services_numbered(items):
    out = []
    for i, it in enumerate(items):
        out.append('<div class="reveal" style="display:flex;gap:24px;align-items:baseline;padding:26px 0;'
                   'border-top:1px solid var(--border)">'
                   f'<div style="font-size:clamp(26px,5vw,40px);font-weight:800;color:var(--accent);line-height:1;'
                   f'font-variant-numeric:tabular-nums;flex:none">{i + 1:02d}</div>'
                   f'<div style="flex:1"><h3 style="margin:0 0 6px;font-size:21px;line-height:1.2">{_esc(it.get("t"))}</h3>'
                   f'<p style="margin:0;color:var(--muted);line-height:1.6;max-width:62ch">{_esc(it.get("d"))}</p></div>'
                   f'<div style="flex:none;color:var(--accent);opacity:.85">{_svc_icon(it, i)}</div></div>')
    return '<div style="display:grid;margin-top:40px">' + "".join(out) + "</div>"


_SERVICE_LAYOUTS = [_services_cards, _services_cards, _services_rows, _services_numbered]


def _services_html(items, layout=None):
    """Render the services block in one of several layouts so the body doesn't look identical every build.
    `layout` (0-based index) pins one — used so a revise keeps the same body unless asked to change it."""
    fns = _SERVICE_LAYOUTS
    fn = fns[layout % len(fns)] if isinstance(layout, int) else random.choice(fns)
    return fn(items)


# A dependency-free swipe slider (CSS scroll-snap + a tiny inline script for dots & gentle autoplay). No CDN,
# so it always loads; if the script is blocked the track is still a swipeable scrolling row with content visible.
SLIDER_ASSETS = (
    "<style>"
    ".tslider{margin-top:44px}"
    ".ttrack{position:relative;display:flex;gap:20px;overflow-x:auto;scroll-snap-type:x mandatory;"
    "scroll-behavior:smooth;-webkit-overflow-scrolling:touch;padding:4px 2px 8px;scrollbar-width:none}"
    ".ttrack::-webkit-scrollbar{display:none}"
    ".tslide{scroll-snap-align:center;flex:0 0 clamp(270px,78%,380px)}"
    ".tslide.gal{flex-basis:clamp(240px,70%,320px)}"
    ".tslide.gal .tile{aspect-ratio:4/5;border-radius:16px;overflow:hidden;border:1px solid var(--border);height:100%}"
    ".tslide.gal img{width:100%;height:100%;object-fit:cover}"
    ".tdots{display:flex;gap:9px;justify-content:center;margin-top:24px;flex-wrap:wrap}"
    ".tdot{width:9px;height:9px;border-radius:50%;border:0;background:var(--border);cursor:pointer;padding:0;"
    "transition:width .3s,background .3s}.tdot.on{background:var(--accent);width:26px;border-radius:6px}"
    "</style>"
    "<script>(function(){function go(t,s){t.scrollTo({left:s.offsetLeft-(t.clientWidth-s.offsetWidth)/2,behavior:'smooth'});}"
    "function init(sl){if(sl.__i)return;sl.__i=1;var t=sl.querySelector('.ttrack'),dw=sl.querySelector('.tdots');"
    "var S=[].slice.call(t.children);if(S.length<2){if(dw)dw.style.display='none';return;}"
    "var D=S.map(function(_,i){var b=document.createElement('button');b.className='tdot'+(i?'':' on');"
    "b.setAttribute('aria-label','Slide '+(i+1));b.onclick=function(){go(t,S[i]);};dw.appendChild(b);return b;});"
    "function up(){var c=t.scrollLeft+t.clientWidth/2,bi=0,bd=1e18;S.forEach(function(s,i){var m=s.offsetLeft+s.offsetWidth/2,"
    "d=Math.abs(m-c);if(d<bd){bd=d;bi=i;}});D.forEach(function(d,i){d.classList.toggle('on',i===bi);});return bi;}"
    "t.addEventListener('scroll',function(){clearTimeout(t.__t);t.__t=setTimeout(up,90);});"
    "var ap=parseInt(sl.getAttribute('data-autoplay')||'0',10),tm;function nx(){var i=up();go(t,S[(i+1)%S.length]);}"
    "function on(){if(ap)tm=setInterval(nx,ap);}function off(){clearInterval(tm);}"
    "sl.addEventListener('mouseenter',off);sl.addEventListener('mouseleave',on);"
    "t.addEventListener('touchstart',off,{passive:true});on();}"
    "function all(){document.querySelectorAll('.tslider').forEach(init);}"
    "if(document.readyState!='loading')all();else document.addEventListener('DOMContentLoaded',all);})();</script>")


# Cinematic motion layer — subtle, premium polish applied to the content Trillion already builds. All vanilla
# (no CDN), so it also animates in the in-container preview. Adapted from the MIT cinematic-site-components by
# Jay/RoboLabs (github.com/robonuggets/cinematic-site-components): a stat count-up, mouse-follow spotlight on
# cards, and magnetic primary buttons. Respects prefers-reduced-motion and never hides content.
MOTION_ASSETS = (
    "<style>"
    # base: never let a stray wide element (hero planes, a slider) scroll the whole page sideways on a phone.
    # overflow:clip clips horizontally without creating a scroll container, so sticky navs keep working.
    "html{overflow-x:clip}.hero{overflow-x:clip}"
    "@supports not (overflow:clip){body{overflow-x:hidden}}"
    ".mspot{position:absolute;inset:0;border-radius:inherit;pointer-events:none;opacity:0;transition:opacity .35s;"
    "background:radial-gradient(circle 200px at var(--mx,50%) var(--my,50%),color-mix(in srgb,var(--accent) 22%,transparent),transparent 60%);z-index:0}"
    ".card:hover .mspot{opacity:1}.card>*:not(.mspot){position:relative;z-index:1}"
    "@media(prefers-reduced-motion:reduce){.mspot{display:none}}"
    "</style>"
    "<script>(function(){var RM=matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;"
    # stat count-up
    "function countup(el){var raw=el.textContent.trim();var m=raw.match(/-?\\d[\\d\\s,]*\\.?\\d*/);if(!m)return;"
    "var numStr=m[0].replace(/[\\s,]/g,'');var dec=(numStr.split('.')[1]||'').length;var target=parseFloat(numStr);"
    "if(!isFinite(target))return;var pre=raw.slice(0,m.index),post=raw.slice(m.index+m[0].length);"
    "var grp=/,/.test(m[0]);function fmt(v){var s=dec?v.toFixed(dec):Math.round(v).toString();"
    "if(grp)s=s.replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');return pre+s+post;}"
    "if(RM){el.textContent=fmt(target);return;}var st=null,D=1100;"
    "function step(t){if(!st)st=t;var p=Math.min((t-st)/D,1);var e=1-Math.pow(1-p,3);el.textContent=fmt(target*e);"
    "if(p<1)requestAnimationFrame(step);else el.textContent=fmt(target);}requestAnimationFrame(step);}"
    "var seen=new WeakSet();if('IntersectionObserver'in window){var io=new IntersectionObserver(function(es){"
    "es.forEach(function(en){if(en.isIntersecting&&!seen.has(en.target)){seen.add(en.target);countup(en.target);io.unobserve(en.target);}});},{threshold:.4});"
    "function initStats(){document.querySelectorAll('.brstat').forEach(function(el){io.observe(el);});}}"
    "else{function initStats(){document.querySelectorAll('.brstat').forEach(countup);}}"
    # card spotlight
    "function initCards(){if(RM)return;document.querySelectorAll('.card').forEach(function(c){"
    "if(c.__m)return;c.__m=1;if(getComputedStyle(c).position==='static')c.style.position='relative';"
    "var s=document.createElement('div');s.className='mspot';c.insertBefore(s,c.firstChild);"
    "c.addEventListener('mousemove',function(e){var r=c.getBoundingClientRect();"
    "c.style.setProperty('--mx',(e.clientX-r.left)+'px');c.style.setProperty('--my',(e.clientY-r.top)+'px');});});}"
    # magnetic primary buttons
    "function initMag(){if(RM)return;document.querySelectorAll('.btn.primary,.btn.pri,a.btn.dark').forEach(function(b){"
    "if(b.__g)return;b.__g=1;b.style.transition='transform .2s ease-out';var R=60;"
    "b.addEventListener('mousemove',function(e){var r=b.getBoundingClientRect();var x=e.clientX-(r.left+r.width/2),"
    "y=e.clientY-(r.top+r.height/2);b.style.transform='translate('+x*.25+'px,'+y*.35+'px)';});"
    "b.addEventListener('mouseleave',function(){b.style.transform='';});});}"
    "function go(){initStats();initCards();initMag();}"
    "if(document.readyState!='loading')go();else document.addEventListener('DOMContentLoaded',go);})();</script>")


def _reviews_html(items):
    cards = []
    for it in items:
        cards.append('<div class="tslide"><div class="rev reveal"><div class="stars">★★★★★</div>'
                     f'<p>“{_esc(it.get("t"))}”</p><div class="who">{_esc(it.get("n"))} '
                     f'<span>· {_esc(it.get("w") or "Google review")}</span></div></div></div>')
    return ('<div class="tslider" data-autoplay="5500"><div class="ttrack">'
            + "".join(cards) + '</div><div class="tdots"></div></div>')


def _hours_compact(hours):
    """A one-line hours summary for the footer; the full schedule shows in the Visit section."""
    lines = [x.strip() for x in str(hours or "").splitlines() if x.strip()]
    if not lines:
        return "By appointment"
    times = []
    for ln in lines:
        times.append(ln.split(":", 1)[1].strip() if ":" in ln else ln)
    # if every open day is the same time, say it once; otherwise point to the schedule on the page
    uniq = {t for t in times if t.lower() not in ("closed",)}
    if len(uniq) == 1 and len(lines) >= 5:
        return "Open " + next(iter(uniq))
    return "See opening hours on this page"


def _value_html(data, info):
    """The premium 'value' block that makes a generated site read like a considered, high-ticket build:
    a stats band, a why-us row, a how-it-works process, an FAQ and a guarantee strip. Driven by the brain's
    copy with solid fallbacks, built from shared classes + CSS vars so it works under every template."""
    cat = str(info.get("category") or "business").strip()
    loc = str(info.get("location") or info.get("area") or "").strip()
    out = []

    # 1) stats band
    stats = [s for s in (data.get("stats") or []) if isinstance(s, dict) and s.get("n")][:4]
    if len(stats) < 3:
        fb = []
        if info.get("rating"):
            fb.append({"n": f"★ {info['rating']}", "l": "rated on Google"})
        fb += [{"n": "100%", "l": "focused on getting it right"},
               {"n": "Same week", "l": "we get back to you fast"},
               {"n": loc or "Local", "l": "proudly serving the area"}]
        stats = (stats + fb)[:4] if stats else fb[:4] if len(fb) >= 4 else fb[:3]
    cells = "".join(
        '<div class="reveal" style="flex:1 1 150px;text-align:center">'
        f'<div class="brstat" style="font-size:clamp(30px,5vw,46px);font-weight:800;line-height:1;letter-spacing:-.02em;'
        f'background:linear-gradient(100deg,var(--accent),var(--accent2));-webkit-background-clip:text;'
        f'background-clip:text;-webkit-text-fill-color:transparent;font-variant-numeric:tabular-nums">{_esc(s["n"])}</div>'
        f'<div class="muted" style="margin-top:8px;font-size:14px">{_esc(s.get("l") or "")}</div></div>'
        for s in stats)
    out.append('<section class="pad" style="background:var(--bg);padding-top:clamp(40px,7vw,70px);'
               'padding-bottom:clamp(40px,7vw,70px)"><div class="wrap">'
               '<div style="display:flex;flex-wrap:wrap;gap:30px;justify-content:space-between;align-items:flex-start">'
               + cells + '</div></div></section>')

    # 2) why us
    why = [w for w in (data.get("why") or []) if isinstance(w, dict) and w.get("t")][:3]
    if len(why) < 3:
        why = [{"t": "Done properly, first time", "d": f"No shortcuts. The kind of {cat} work that lasts and that you "
                "don't have to think about again."},
               {"t": "Straight with you", "d": "Clear pricing and honest advice up front, so there are no surprises "
                "and no pressure."},
               {"t": "Treated like a neighbour", "d": f"You're not a ticket number. {('People across ' + loc) if loc else 'The people here'} "
                "come back because of how they're looked after."}]
    cards = "".join(
        '<div class="reveal" style="padding:28px;border:1px solid var(--border);border-radius:20px;background:var(--card)">'
        '<div style="width:50px;height:50px;border-radius:14px;display:flex;align-items:center;justify-content:center;'
        f'background:var(--surface);color:var(--accent);margin-bottom:16px">{_svc_icon(w, i)}</div>'
        f'<h3 style="margin:0 0 8px;font-size:20px;line-height:1.25">{_esc(w.get("t"))}</h3>'
        f'<p style="margin:0;color:var(--muted);line-height:1.6">{_esc(w.get("d"))}</p></div>'
        for i, w in enumerate(why))
    out.append('<section class="pad" style="background:var(--surface)"><div class="wrap">'
               f'<div class="sectlabel reveal">{_esc(data.get("why_label") or "Why choose us")}</div>'
               f'<h2 class="reveal">{_esc(data.get("why_head") or "The difference is in the detail")}</h2>'
               '<div class="grid g3" style="margin-top:44px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px">'
               + cards + '</div></div></section>')

    # 3) how it works
    proc = [p for p in (data.get("process") or []) if isinstance(p, dict) and p.get("t")][:4]
    if len(proc) < 3:
        proc = [{"t": "Get in touch", "d": "Tell us what you need. A quick chat or message is all it takes to start."},
                {"t": "We sort the details", "d": "We talk you through the options and give you a clear, fair quote."},
                {"t": "We get it done", "d": "The work happens on time and done right, and we stand behind it."}]
    steps = "".join(
        '<div class="reveal" style="flex:1 1 220px;position:relative">'
        f'<div style="font-size:clamp(26px,4vw,38px);font-weight:800;color:var(--accent);line-height:1;'
        f'font-variant-numeric:tabular-nums">{i + 1:02d}</div>'
        f'<h3 style="margin:14px 0 6px;font-size:19px;line-height:1.25">{_esc(p.get("t"))}</h3>'
        f'<p style="margin:0;color:var(--muted);line-height:1.6">{_esc(p.get("d"))}</p></div>'
        for i, p in enumerate(proc))
    out.append('<section class="pad" style="background:var(--bg)"><div class="wrap">'
               '<div class="sectlabel reveal">How it works</div>'
               f'<h2 class="reveal">{_esc(data.get("process_head") or "Simple from start to finish")}</h2>'
               '<div style="display:flex;flex-wrap:wrap;gap:34px;margin-top:44px">'
               + steps + '</div></div></section>')

    # 4) FAQ
    faqs = [f for f in (data.get("faqs") or []) if isinstance(f, dict) and f.get("q") and f.get("a")][:5]
    if len(faqs) >= 3:
        rows = "".join(
            '<details class="reveal" style="border:1px solid var(--border);border-radius:14px;background:var(--card);'
            'padding:2px 20px;margin-bottom:12px">'
            f'<summary style="cursor:pointer;list-style:none;padding:18px 0;font-weight:650;font-size:17px;'
            f'display:flex;justify-content:space-between;gap:16px;align-items:center">{_esc(f["q"])}'
            '<span style="color:var(--accent);font-size:22px;flex:none">+</span></summary>'
            f'<p style="margin:0 0 18px;color:var(--muted);line-height:1.65">{_esc(f["a"])}</p></details>'
            for f in faqs)
        out.append('<section class="pad" style="background:var(--surface)"><div class="wrap" style="max-width:760px">'
                   '<div class="sectlabel reveal">Good to know</div>'
                   f'<h2 class="reveal">{_esc(data.get("faq_head") or "Questions, answered")}</h2>'
                   '<div style="margin-top:34px">' + rows + '</div></div></section>')

    # 4.5) Visit us — opening hours + address + phone + directions (only when we actually have the details)
    hours_raw = str(info.get("hours") or "").strip()
    addr = str(info.get("address") or info.get("location") or "").strip()
    phone = str(info.get("phone") or "").strip()
    if hours_raw or addr:
        hrows = ""
        for ln in [x.strip() for x in hours_raw.splitlines() if x.strip()][:7]:
            if ":" in ln:
                day, t = ln.split(":", 1)
                hrows += (f'<div style="display:flex;justify-content:space-between;gap:16px;padding:9px 0;'
                          f'border-bottom:1px solid var(--border)"><span style="font-weight:600">{_esc(day.strip())}</span>'
                          f'<span class="muted">{_esc(t.strip())}</span></div>')
            else:
                hrows += f'<div style="padding:9px 0;border-bottom:1px solid var(--border)">{_esc(ln)}</div>'
        hours_col = (f'<div><div style="font-weight:700;font-size:18px;margin-bottom:10px">Opening hours</div>{hrows}</div>'
                     if hrows else "")
        contact_bits = []
        if addr:
            dir_url = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(addr)
            contact_bits.append(f'<div style="margin-bottom:14px"><div style="font-weight:700;font-size:18px">Find us</div>'
                                f'<div class="muted" style="margin-top:4px;line-height:1.5">{_esc(addr)}</div>'
                                f'<a href="{dir_url}" target="_blank" rel="noopener" style="display:inline-block;margin-top:10px;'
                                f'color:var(--accent);font-weight:600;text-decoration:none">Get directions &rarr;</a></div>')
        if phone:
            contact_bits.append(f'<div><div style="font-weight:700;font-size:18px">Call us</div>'
                                f'<a href="tel:{_esc(phone)}" style="color:var(--accent);font-weight:600;text-decoration:none;'
                                f'font-size:17px">{_esc(phone)}</a></div>')
        contact_col = "".join(contact_bits)
        cols = "".join(c for c in (hours_col, contact_col) if c)
        if cols:
            out.append('<section class="pad" id="visit" style="background:var(--surface)"><div class="wrap" style="max-width:860px">'
                       '<div class="sectlabel reveal">Visit us</div>'
                       f'<h2 class="reveal">{_esc(data.get("visit_head") or "Come see us")}</h2>'
                       '<div class="reveal" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));'
                       'gap:34px;margin-top:34px;align-items:start">' + cols + '</div></div></section>')

    # 5) guarantee strip
    guar = _esc(data.get("guarantee") or f"If it's not right, we make it right. That's the promise behind every "
                f"job we do{(' in ' + loc) if loc else ''}.")
    out.append('<section style="padding:0 clamp(20px,5vw,40px)"><div style="max-width:var(--maxw);margin:0 auto">'
               '<div class="reveal" style="background:linear-gradient(120deg,var(--accent),var(--accent2));'
               'border-radius:24px;padding:clamp(32px,5vw,52px);color:#fff;text-align:center">'
               '<div style="font-size:12px;letter-spacing:.14em;text-transform:uppercase;opacity:.85;font-weight:700">Our promise</div>'
               f'<div style="font-size:clamp(21px,3.4vw,30px);font-weight:750;line-height:1.3;margin-top:12px;'
               f'max-width:22ch;margin-left:auto;margin-right:auto">{guar}</div></div></div></section>')

    return "".join(out)


def _gallery_section(photos_rel):
    if not photos_rel:
        return ""
    slides = "".join(f'<div class="tslide gal"><div class="tile"><img src="{p}" alt="" loading="lazy"></div></div>'
                     for p in photos_rel)
    inner = (f'<div class="tslider" data-autoplay="4200"><div class="ttrack">{slides}</div>'
             '<div class="tdots"></div></div>') if len(photos_rel) > 1 else \
            f'<div class="gallery" style="margin-top:44px"><div class="tile"><img src="{photos_rel[0]}" alt="" loading="lazy"></div></div>'
    return ('<section class="pad" style="background:var(--surface)"><div class="wrap">'
            '<div class="sectlabel reveal">Gallery</div><h2 class="reveal">A look inside</h2>'
            f'{inner}</div></section>')


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
"services_label":"1-2 words","services_head":"benefit-led section heading","services_sub":"one persuasive sentence",
"services":[{{"t":"a REAL, specific service this {cat or 'business'} offers","d":"1-2 sentences on the OUTCOME the customer gets, not just what it is"}} x5],
"reviews_head":"short heading","cta_head":"a compelling closing headline that drives action","cta_sub":"one persuasive sentence that makes them get in touch now",
"stats":[{{"n":"a short punchy figure e.g. '12+','4.9★','Same day'","l":"3-5 word label"}} x3],
"why_head":"short heading for a why-choose-us section","why":[{{"t":"a real reason to choose this {cat or 'business'}","d":"one persuasive sentence on the benefit to the customer"}} x3],
"process_head":"short heading for a how-it-works section","process":[{{"t":"step name","d":"one sentence"}} x3],
"faq_head":"short heading","faqs":[{{"q":"a real question a customer of this {cat or 'business'} would ask","a":"one honest, reassuring sentence that removes a buying objection"}} x4],
"guarantee":"one confident sentence promising to stand behind the work"}}
For "services", name 5 actual services a {cat or 'business like this'} provides — infer them from the category and the reviews (e.g. a barber: cuts, fades, beard trims, hot-towel shaves, kids' cuts; an auto shop: servicing, repairs, diagnostics, brakes, tyres). Write every description to SELL: lead with the benefit or outcome for the customer, be specific, and give a reason to choose {name}. Never use vague filler like "quality service" or "we care about customers". Mention {name} by name in the headline or sub. English, confident, persuasive, no hashtags, no emojis in text."""
    if info.get("change_note"):
        ask += f"\nAlso apply this change the owner asked for: {info['change_note']}"
    data = {}
    try:
        text = (llm(user, [{"role": "system", "content": "You are a senior conversion copywriter for premium "
                            "local service businesses. Write specific, benefit-led, persuasive copy that makes "
                            "the reader want to book or buy — every line earns its place and sells the outcome. "
                            "Ground everything in the real business details given. Reply with JSON only."},
                           {"role": "user", "content": ask}]).get("content") or "")
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        data = {}

    def g(k, d=""):
        v = data.get(k)
        return v if v else d
    svc_list = data.get("services") if isinstance(data.get("services"), list) else []
    svc_list = [s for s in svc_list if isinstance(s, dict) and s.get("t")][:6]
    if len(svc_list) < 3:
        typed = [x.strip() for x in re.split(r"[,/;\n]", svc) if x.strip()][:6]
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
        "SERVICES": _services_html(svc_list, info.get("_svc_layout")),
        "REVIEWS_HEAD": _esc(g("reviews_head", "What people say")), "REVIEWS": _reviews_html(rev_list),
        "CTA_HEAD": _esc(g("cta_head", "Ready when you are")),
        "CTA_SUB": _esc(g("cta_sub", "Get in touch today and we'll take it from there.")),
        "PHONE": _esc(info.get("phone") or ""), "ADDR": _esc(info.get("location") or info.get("address") or ""),
        "HOURS": _esc(_hours_compact(info.get("hours"))),
        "VALUE": _value_html(data, info),
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
    out = {"PROMO": "", "CONTACT": "", "FLOAT": "", "PRICELIST": "", "APPS": ""}
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
    # tier-2 interactive add-ons (shop, members, appointments, community) — need a site token for their backend
    apps_html, cart_float = _tier2(info, palette, addons)
    out["APPS"] = apps_html
    if cart_float:
        out["FLOAT"] = cart_float + out["FLOAT"]
    return out


# ---- tier-2 interactive add-ons ------------------------------------------------------------------
_T2_HELPER = (
    "<script>window.__T2=(function(){var B=location.pathname.replace(/[^/]*$/,'');"   # dir of this page
    "function api(a,m,d){return fetch(B+'__api/'+a,{method:m||'POST',headers:{'Content-Type':'application/json'},"
    "body:d?JSON.stringify(d):null}).then(function(r){return r.json();});}"
    "return{api:api,base:B};})();</script>")


def _tier2(info, palette, addons):
    """Build the ticked tier-2 sections. They talk to /site/<token>/__api/<action> via a relative URL, so they
    work on the LAN preview, the shared link and the public Funnel alike. Returns (apps_html, cart_float_html)."""
    want = [a for a in ("shop", "members", "appointments", "community") if a in addons]
    if not want:
        return "", ""
    st = _inp(palette)
    sections = [_T2_HELPER]
    cart_float = ""

    if "shop" in addons:
        items = info.get("pricelist_items")
        if items is None:
            items = _parse_pricelist(info.get("pricelist"))
        cards = []
        for i, it in enumerate(items[:24]):
            pr = _esc(it.get("price") or "")
            desc = f'<div class="muted" style="font-size:13px;margin-top:4px">{_esc(it.get("desc"))}</div>' if it.get("desc") else ""
            cards.append(
                '<div class="reveal" style="border:1px solid var(--border);border-radius:16px;background:var(--card);'
                'padding:20px;display:flex;flex-direction:column;gap:6px">'
                f'<div style="font-weight:650;font-size:17px">{_esc(it.get("name"))}</div>{desc}'
                f'<div style="color:var(--accent);font-weight:700;margin-top:4px">{pr or "Enquire"}</div>'
                f'<button class="btn primary" type="button" style="margin-top:10px" '
                f"onclick=\"T2cart.add({json.dumps(it.get('name') or '')},{json.dumps(it.get('price') or '')})\">Add to cart</button></div>")
        if cards:
            sections.append(
                '<section class="pad" id="shop" style="background:var(--bg)"><div class="wrap">'
                f'<div class="sectlabel reveal">{_esc(info.get("shop_head") or "Shop")}</div>'
                '<h2 class="reveal">Order online</h2>'
                '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:18px;margin-top:40px">'
                + "".join(cards) + '</div></div></section>')
            cart_float = _SHOP_JS(st)

    if "members" in addons:
        note = _esc(info.get("members_note") or "Welcome back. This is your members area — look out for exclusive "
                    "updates and offers here.")
        sections.append(
            '<section class="pad" id="members" style="background:var(--surface)"><div class="wrap" style="max-width:520px">'
            '<div class="sectlabel reveal">Members</div><h2 class="reveal" id="m_head">Member login</h2>'
            '<div class="reveal" id="m_area" style="margin-top:26px">'
            '<div id="m_forms" style="display:grid;gap:12px">'
            f'<input id="m_name" placeholder="Your name (sign up only)" style="{st}">'
            f'<input id="m_email" placeholder="Email" style="{st}">'
            f'<input id="m_pw" type="password" placeholder="Password" style="{st}">'
            '<div style="display:flex;gap:10px">'
            '<button class="btn primary" type="button" style="flex:1" onclick="T2mem.login()">Log in</button>'
            '<button class="btn ghost" type="button" style="flex:1" onclick="T2mem.signup()">Sign up</button></div>'
            '<div id="m_msg" style="font-size:13px;color:var(--muted);min-height:18px"></div></div>'
            f'<div id="m_in" style="display:none"><p style="font-size:17px;line-height:1.6">{note}</p>'
            '<button class="btn ghost" type="button" onclick="T2mem.logout()" style="margin-top:10px">Log out</button></div>'
            '</div></div></section>' + _MEM_JS)

    if "appointments" in addons:
        sections.append(
            '<section class="pad" id="book" style="background:var(--bg)"><div class="wrap" style="max-width:560px">'
            '<div class="sectlabel reveal">Appointments</div>'
            f'<h2 class="reveal">{_esc(info.get("appt_head") or "Book a time")}</h2>'
            '<div class="reveal" style="margin-top:26px;display:grid;gap:14px">'
            f'<input id="b_n" placeholder="Your name" style="{st}">'
            f'<input id="b_c" placeholder="Phone or email" style="{st}">'
            f'<input id="b_d" type="date" style="{st}">'
            f'<select id="b_t" style="{st}"><option value="">Preferred time…</option>'
            + "".join(f'<option>{h}</option>' for h in
                      ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"])
            + '</select>'
            f'<textarea id="b_m" rows="2" placeholder="Anything we should know? (optional)" style="{st}"></textarea>'
            '<button class="btn primary" type="button" onclick="T2book()">Request booking</button>'
            '<div id="b_msg" style="font-size:13px;color:var(--muted);min-height:18px"></div>'
            '</div></div></section>' + _BOOK_JS)

    if "community" in addons:
        sections.append(
            '<section class="pad" id="community" style="background:var(--surface)"><div class="wrap" style="max-width:720px">'
            '<div class="sectlabel reveal">Community</div>'
            f'<h2 class="reveal">{_esc(info.get("forum_head") or "Join the conversation")}</h2>'
            '<div class="reveal" style="margin-top:24px;display:grid;gap:10px;'
            'border:1px solid var(--border);border-radius:16px;background:var(--card);padding:18px">'
            f'<input id="c_name" placeholder="Your name" style="{st}">'
            f'<textarea id="c_body" rows="3" placeholder="Start a discussion…" style="{st}"></textarea>'
            '<button class="btn primary" type="button" onclick="T2forum.post(0)">Post</button></div>'
            '<div id="c_list" style="margin-top:26px;display:grid;gap:16px"></div>'
            '</div></section>' + _FORUM_JS(st))

    return "".join(sections), cart_float


def _SHOP_JS(st):
    return (
        '<div id="cartbtn" onclick="T2cart.open()" style="position:fixed;left:20px;bottom:20px;z-index:60;'
        'background:var(--accent);color:#fff;border-radius:999px;padding:12px 18px;font-weight:700;cursor:pointer;'
        'box-shadow:0 12px 30px rgba(0,0,0,.35);display:none">Cart <span id="cartn">0</span></div>'
        '<div id="cartbg" onclick="T2cart.close()" style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:70;display:none"></div>'
        '<div id="cartdr" style="position:fixed;top:0;right:0;bottom:0;width:min(400px,90vw);background:var(--bg);'
        'z-index:71;transform:translateX(100%);transition:transform .3s;padding:24px;overflow:auto;'
        'box-shadow:-20px 0 50px rgba(0,0,0,.4)">'
        '<div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0">Your cart</h3>'
        '<button onclick="T2cart.close()" style="background:none;border:0;font-size:26px;color:var(--text);cursor:pointer">×</button></div>'
        '<div id="cartitems" style="margin-top:18px;display:grid;gap:10px"></div>'
        '<div id="carttot" style="font-weight:700;margin:16px 0;text-align:right"></div>'
        '<div id="cartco" style="display:grid;gap:10px">'
        f'<input id="o_n" placeholder="Your name" style="{st}">'
        f'<input id="o_c" placeholder="Phone or email" style="{st}">'
        f'<textarea id="o_m" rows="2" placeholder="Note (optional)" style="{st}"></textarea>'
        '<button class="btn primary" type="button" onclick="T2cart.checkout()">Place order</button></div>'
        '<div id="cartmsg" style="margin-top:12px;font-size:14px;line-height:1.5"></div></div>'
        "<script>window.T2cart=(function(){var K='cart_'+location.pathname,C=[];"
        "try{C=JSON.parse(localStorage.getItem(K)||'[]')}catch(e){}"
        "function save(){try{localStorage.setItem(K,JSON.stringify(C))}catch(e){}draw();}"
        "function draw(){var n=C.reduce(function(a,b){return a+b.qty},0);"
        "var b=document.getElementById('cartbtn');if(b){b.style.display=n?'block':'none';document.getElementById('cartn').textContent=n;}"
        "var w=document.getElementById('cartitems');if(!w)return;var tot=0;"
        "w.innerHTML=C.map(function(it,i){var m=(it.price||'').match(/[\\d][\\d\\s.,]*/);if(m)tot+=parseFloat(m[0].replace(/[\\s,]/g,''))*it.qty;"
        "return '<div style=\"display:flex;justify-content:space-between;gap:10px;align-items:center;border-bottom:1px solid var(--border);padding-bottom:8px\">'"
        "+'<div><div style=\"font-weight:600\">'+it.name+'</div><div style=\"color:var(--muted);font-size:13px\">'+(it.price||'')+'</div></div>'"
        "+'<div style=\"display:flex;gap:6px;align-items:center\"><button onclick=\"T2cart.q('+i+',-1)\" style=\"width:26px;height:26px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer\">-</button>'"
        "+'<span>'+it.qty+'</span><button onclick=\"T2cart.q('+i+',1)\" style=\"width:26px;height:26px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer\">+</button></div></div>';}).join('')"
        "||'<div style=\"color:var(--muted)\">Your cart is empty.</div>';"
        "document.getElementById('carttot').textContent=tot?('Total: R'+tot.toFixed(2)):'';}"
        "return{add:function(n,p){var e=C.find(function(x){return x.name===n});if(e)e.qty++;else C.push({name:n,price:p,qty:1});save();this.open();},"
        "q:function(i,d){C[i].qty+=d;if(C[i].qty<1)C.splice(i,1);save();},"
        "open:function(){draw();document.getElementById('cartbg').style.display='block';document.getElementById('cartdr').style.transform='translateX(0)';},"
        "close:function(){document.getElementById('cartbg').style.display='none';document.getElementById('cartdr').style.transform='translateX(100%)';},"
        "checkout:function(){var n=document.getElementById('o_n').value,c=document.getElementById('o_c').value,m=document.getElementById('cartmsg');"
        "if(!C.length){m.textContent='Your cart is empty.';return;}if(!n||!c){m.textContent='Please add your name and contact.';return;}"
        "m.textContent='Sending…';__T2.api('order','POST',{name:n,contact:c,note:document.getElementById('o_m').value,items:C}).then(function(r){"
        "if(r.ok){C=[];save();m.innerHTML=r.message+(r.wa?(' <a href=\"'+r.wa+'\" target=\"_blank\" style=\"color:var(--accent)\">Confirm on WhatsApp</a>'):'');}"
        "else m.textContent=r.error||'Could not place the order.';});}};})();setTimeout(function(){try{T2cart.open&&document.getElementById('cartbtn')&&(function(){var e=JSON.parse(localStorage.getItem('cart_'+location.pathname)||'[]');var n=e.reduce(function(a,b){return a+b.qty},0);var b=document.getElementById('cartbtn');if(n){b.style.display='block';document.getElementById('cartn').textContent=n;}})()}catch(e){}},50);</script>")


_MEM_JS = (
    "<script>window.T2mem=(function(){var K='mem_'+location.pathname;"
    "function show(nm){document.getElementById('m_forms').style.display=nm?'none':'grid';"
    "document.getElementById('m_in').style.display=nm?'block':'none';"
    "document.getElementById('m_head').textContent=nm?('Hi, '+nm):'Member login';}"
    "function tok(){try{return localStorage.getItem(K)||''}catch(e){return''}}"
    "function set(t){try{t?localStorage.setItem(K,t):localStorage.removeItem(K)}catch(e){}}"
    "if(tok())__T2.api('me','POST',{session:tok()}).then(function(r){if(r.ok)show(r.name);else set('');});"
    "function msg(t){document.getElementById('m_msg').textContent=t;}"
    "return{signup:function(){msg('Creating your account…');__T2.api('signup','POST',{name:document.getElementById('m_name').value,"
    "email:document.getElementById('m_email').value,password:document.getElementById('m_pw').value}).then(function(r){"
    "if(r.ok){set(r.session);show(r.name);}else msg(r.error||'Could not sign up.');});},"
    "login:function(){msg('Signing in…');__T2.api('login','POST',{email:document.getElementById('m_email').value,"
    "password:document.getElementById('m_pw').value}).then(function(r){if(r.ok){set(r.session);show(r.name);}else msg(r.error||'Could not log in.');});},"
    "logout:function(){set('');show('');}};})();</script>")


_BOOK_JS = (
    "<script>window.T2book=function(){var n=document.getElementById('b_n').value,c=document.getElementById('b_c').value,"
    "d=document.getElementById('b_d').value,t=document.getElementById('b_t').value,m=document.getElementById('b_msg');"
    "if(!n||!c||!d||!t){m.textContent='Please fill in your name, contact, date and time.';return;}m.textContent='Sending…';"
    "__T2.api('book','POST',{name:n,contact:c,slot:d+' '+t,note:document.getElementById('b_m').value}).then(function(r){"
    "if(r.ok)m.innerHTML=r.message+(r.wa?(' <a href=\"'+r.wa+'\" target=\"_blank\" style=\"color:var(--accent)\">Confirm on WhatsApp</a>'):'');"
    "else m.textContent=r.error||'Could not book.';});};</script>")


def _FORUM_JS(st):
    return (
        "<script>window.T2forum=(function(){function esc(s){return(s||'').replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}"
        "function render(th){var w=document.getElementById('c_list');if(!w)return;"
        "w.innerHTML=th.length?th.map(function(t){return '<div style=\"border:1px solid var(--border);border-radius:14px;background:var(--card);padding:16px\">'"
        "+'<div style=\"font-weight:650\">'+esc(t.name)+'</div><div style=\"margin:4px 0 8px;line-height:1.6\">'+esc(t.body)+'</div>'"
        "+(t.replies||[]).map(function(r){return '<div style=\"border-left:2px solid var(--accent);padding:6px 0 6px 12px;margin:8px 0;color:var(--muted)\"><b>'+esc(r.name)+'</b> '+esc(r.body)+'</div>';}).join('')"
        "+'<details style=\"margin-top:6px\"><summary style=\"cursor:pointer;color:var(--accent);font-size:13px\">Reply</summary>"
        "<input id=\"r_n_'+t.id+'\" placeholder=\"Name\" style=\"" + st.replace('"', "'") + ";margin-top:8px\">"
        "<textarea id=\"r_b_'+t.id+'\" rows=\"2\" placeholder=\"Your reply\" style=\"" + st.replace('"', "'") + ";margin-top:8px\"></textarea>"
        "<button class=\"btn primary\" type=\"button\" style=\"margin-top:8px\" onclick=\"T2forum.post('+t.id+')\">Reply</button></details></div>';}).join('')"
        ":'<div style=\"color:var(--muted)\">No posts yet. Be the first.</div>';}"
        "function load(){__T2.api('forum','GET').then(function(r){if(r.ok)render(r.threads);});}"
        "load();return{post:function(parent){var nid=parent?'r_n_'+parent:'c_name',bid=parent?'r_b_'+parent:'c_body';"
        "var n=document.getElementById(nid).value,b=document.getElementById(bid).value;if(!b)return;"
        "__T2.api('forum','POST',{name:n,body:b,parent:parent}).then(function(r){if(r.ok){if(!parent){document.getElementById('c_body').value='';}load();}});}};})();</script>")


def render(content, palette, photos_rel, template="premium", font=None):
    tpl = TEMPLATES.get(template, TEMPLATES["premium"])
    html = (TPL_DIR / tpl["file"]).read_text(encoding="utf-8")
    content = dict(content)
    body_fam, fontdef = _font_tokens(font)
    content["FONT"] = body_fam
    content["FONTDEF"] = fontdef
    content["SLIDERJS"] = SLIDER_ASSETS + MOTION_ASSETS
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


_DECK_FEATURES = {
    "whatsapp": "One-tap WhatsApp button on every page",
    "form": "Enquiry form that emails you straight away",
    "booking": "Online booking request form",
    "pricelist": "A price list customers read on their phone",
    "promo": "A promotions banner you can change any time",
    "shop": "An online shop with a cart and checkout",
    "members": "A member login and members-only area",
    "appointments": "Appointment booking with date and time",
    "community": "A community forum for your customers",
}


def pitch_deck(folder, info, palette, addons, has_video=False):
    """Write pitch.html into the site folder: a one-page offer deck the prospect reads — what they get, how
    installation works, and a clear note that domain and hosting are not included. Self-contained, palette-matched,
    with relative links (so it works on the LAN, the shared link and the public Funnel), plus accept/decline
    buttons that Trillion tracks. Content only — invents no price."""
    name = str(info.get("business") or info.get("name") or "your business").strip()
    cat = str(info.get("category") or "business").strip()
    p = palette
    feats = [v for k, v in _DECK_FEATURES.items() if k in [str(a).lower() for a in (addons or [])]]
    base = ["A fast, modern website built around " + _esc(name),
            "Looks right on a phone, tablet and computer",
            "Your real services, hours and contact details",
            "Found on Google with your business name"]
    feat_items = base + [_esc(f) for f in feats]
    feat_html = "".join(
        f'<li style="padding:11px 0;border-bottom:1px solid {p["C_BORDER"]};display:flex;gap:12px;align-items:flex-start">'
        f'<span style="color:{p["C_ACCENT"]};font-weight:800">&#10003;</span><span>{f}</span></li>'
        for f in feat_items)
    steps = [("1", "You take a look", "Open the sample site" + (" and the 30-second video" if has_video else "") +
              ". Everything you see is real and ready."),
             ("2", "You say the word", "Tap <b>Yes, let's do it</b> below. Tell me any changes — colours, wording, "
              "photos — and I make them."),
             ("3", "We put it live", "You arrange a domain and hosting (see below); I connect your site to it and "
              "it goes live for your customers.")]
    step_html = "".join(
        f'<div style="display:flex;gap:18px;align-items:flex-start;padding:16px 0">'
        f'<div style="flex:none;width:44px;height:44px;border-radius:12px;background:{p["C_ACCENT"]};color:#fff;'
        f'display:flex;align-items:center;justify-content:center;font-weight:800;font-size:18px">{n}</div>'
        f'<div><div style="font-weight:700;font-size:18px">{t}</div>'
        f'<div style="color:{p["C_MUTED"]};line-height:1.6;margin-top:3px">{d}</div></div></div>'
        for n, t, d in steps)
    video_btn = ('<a href="pitch.mp4" style="display:inline-block;padding:13px 24px;border-radius:40px;border:1px solid '
                 f'{p["C_BORDER"]};color:{p["C_TEXT"]};text-decoration:none;font-weight:600">&#9654; Watch the 30-sec video</a>'
                 ) if has_video else ""
    accent_grad = f"linear-gradient(100deg,{p['C_ACCENT']},{p['C_ACCENT2']})"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Your new website — {_esc(name)}</title>
<style>:root{{color-scheme:{p['mode']}}}*{{box-sizing:border-box}}body{{margin:0;background:{p['C_BG']};color:{p['C_TEXT']};
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;line-height:1.6;overflow-x:clip}}
.wrap{{max-width:760px;margin:0 auto;padding:0 22px}}h1{{font-size:clamp(28px,6vw,44px);line-height:1.1;letter-spacing:-.02em;margin:0 0 10px}}
h2{{font-size:clamp(20px,4vw,26px);margin:38px 0 8px}}.sub{{color:{p['C_MUTED']};font-size:17px}}
.card{{background:{p['C_CARD']};border:1px solid {p['C_BORDER']};border-radius:18px;padding:24px;margin-top:16px}}
.btn{{display:inline-block;padding:15px 30px;border-radius:40px;text-decoration:none;font-weight:700;font-size:16px}}
ul{{list-style:none;padding:0;margin:0}}a{{color:{p['C_ACCENT']}}}</style></head>
<body><div style="height:8px;background:{accent_grad}"></div>
<div class="wrap" style="padding-top:46px;padding-bottom:60px">
<div style="font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:{p['C_ACCENT']};font-weight:700">A new website, made for you</div>
<h1>{_esc(name)}, online — properly</h1>
<p class="sub">I built a complete sample website for {_esc(name)} so you can see exactly what it looks like before deciding anything. No obligation, nothing to pay to look.</p>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:22px">
<a class="btn" href="./" style="background:{accent_grad};color:#fff">View your website</a>{video_btn}</div>

<h2>What you get</h2><div class="card"><ul>{feat_html}</ul></div>

<h2>How it works</h2><div class="card">{step_html}</div>

<h2>Domain &amp; hosting</h2>
<div class="card" style="border-color:{p['C_ACCENT']}">
<p style="margin:0 0 10px"><b>Domain and hosting are not included.</b> Those are the two things you own and pay for directly, so they stay in your name and under your control.</p>
<ul style="color:{p['C_MUTED']}">
<li style="padding:5px 0">&bull; A <b>domain</b> is your web address (for example {_esc(name.lower().replace(' ', '') or 'yourbusiness')}.co.za). About R100&ndash;R250 a year from a registrar.</li>
<li style="padding:5px 0">&bull; <b>Hosting</b> is where the site lives online. Low-cost plans are widely available.</li>
<li style="padding:5px 0">&bull; Once you've sorted those, I connect your site to them and it goes live. I'll guide you through it.</li>
</ul></div>

<h2>Ready?</h2>
<p class="sub">Let me know and we'll make it yours.</p>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px">
<a class="btn" href="reply?a=yes" style="background:{accent_grad};color:#fff">Yes, let's do it</a>
<a class="btn" href="reply?a=no" style="background:transparent;border:1px solid {p['C_BORDER']};color:{p['C_MUTED']}">Not right now</a></div>
<p style="font-size:12px;color:{p['C_MUTED']};margin-top:40px">Simulated preview built for {_esc(name)}. Your reply just lets me know how to follow up.</p>
</div></body></html>"""


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
    svc_layout = info.get("svc_layout")
    if not isinstance(svc_layout, int):
        svc_layout = random.randrange(len(_SERVICE_LAYOUTS))   # vary the services body layout each build
    info["_svc_layout"] = svc_layout
    say("Writing the copy")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}), user, info)
    content.update(_addons(info, palette))      # optional features the owner ticked
    say("Building the website")
    font_idx = info.get("font_idx")
    if not isinstance(font_idx, int):
        font_idx = random.randrange(len(FONT_PAIRS))     # a different typeface pairing each build
    html = render(content, palette, photos_rel, template, font=font_idx)
    (folder / "index.html").write_text(html, encoding="utf-8")
    try:
        (folder / "pitch.html").write_text(
            pitch_deck(folder, info, palette, info.get("addons") or [],
                       has_video=(folder / "pitch.mp4").exists()), encoding="utf-8")
    except Exception:
        pass
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
    detail = {"style": style, "mode": palette["mode"], "template": template, "font": font_idx,
              "svc_layout": svc_layout, "business": name, "category": cat,
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


def start_revise(user, sid, change, opts=None):
    init()
    row = core.q("SELECT title FROM gensites WHERE id=? AND user_id=?", (sid, user["id"]), one=True)
    tid = core.add_task(user["id"], f"Change website: {(row['title'] if row else 'site')}", domain="work") \
        if hasattr(core, "add_task") else None

    def go():
        try:
            res = revise(user, sid, change, progress=(lambda t: core.set_task(tid, detail=t)) if tid else None, opts=opts)
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
                    "template": d.get("template"), "addons": d.get("addons") or [],
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


def revise(user, sid, change, progress=None, opts=None):
    """Regenerate a site with a plain-language change (dark/light, a different look or colours, reworded copy),
    keeping the same folder and preview link so anything already shared stays valid. `opts` carries explicit
    structured choices from the CHANGE panel (hero/colour/mode/layout/font/add-ons) which win over the text."""
    import re as _re
    opts = opts or {}
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
    font_idx = d.get("font") if isinstance(d.get("font"), int) else random.randrange(len(FONT_PAIRS))
    svc_layout = d.get("svc_layout") if isinstance(d.get("svc_layout"), int) else random.randrange(len(_SERVICE_LAYOUTS))
    low = (change or "").lower()
    if _re.search(r"\b(font|fonts|typeface|typography|lettering)\b", low):
        font_idx = (font_idx + 1 + random.randrange(len(FONT_PAIRS) - 1)) % len(FONT_PAIRS)   # reroll to a new one
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
    elif _re.search(r"\b(aurora|horizon|northern lights?)\b", low):
        template = "aurora"
    elif _re.search(r"\b(bells?|resonant rings?)\b", low):
        template = "bells"
    elif _re.search(r"\b(dot ?grid|dot ?matrix|dots)\b", low):
        template = "dotgrid"
    elif _re.search(r"\b(laser|beams?)\b", low):
        template = "laser"
    elif _re.search(r"\b(mercury|liquid metal|chrome blob)\b", low):
        template = "liquid"
    elif _re.search(r"\b(pixels?|retro pixel|8.?bit)\b", low):
        template = "pixels"
    elif _re.search(r"\b(glass|frosted|particles?)\b", low):
        template = "glass"
    elif _re.search(r"\b(constellation|network|nodes?|points?)\b", low):
        template = "constellation"
    elif _re.search(r"\b(globe|planet|world|sphere map|dotted globe)\b", low):
        template = "globe"
    elif _re.search(r"\b(topo|topographic|contours?|terrain|elevation)\b", low):
        template = "topo"
    elif _re.search(r"\b(mesh|mesh gradient|flowing gradient|stripe-like|apple-like)\b", low):
        template = "mesh"
    elif _re.search(r"\b(swirl|spiral|hypnotic|vortex spiral)\b", low):
        template = "swirl"
    elif _re.search(r"\b(neuro|plasma|neural|organic flow|liquid light)\b", low):
        template = "neuro"
    elif _re.search(r"\b(portal|vortex|wormhole)\b", low):
        template = "portal"
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
    # ---- explicit choices from the CHANGE panel win over anything parsed from the text ----
    o_look = str(opts.get("look") or "").strip()
    if o_look in TEMPLATES:
        template = o_look
    elif o_look in ("shuffle", "auto", "surprise"):
        template = pick_template(info.get("category", ""), photos_rel, avoid=template)
    o_col = str(opts.get("colour") or opts.get("color") or "").strip().lower()
    if o_col in PALETTES:
        style = o_col
    o_mode = str(opts.get("mode") or "").strip().lower()
    if o_mode in ("light", "dark") and PALETTES.get(style, PALETTES["violet"])["mode"] != o_mode:
        style = (_TO_LIGHT if o_mode == "light" else _TO_DARK).get(style, "azure" if o_mode == "light" else "violet")
    if opts.get("font") in ("change", "shuffle", "new"):
        font_idx = (font_idx + 1 + random.randrange(len(FONT_PAIRS) - 1)) % len(FONT_PAIRS)
    o_lay = opts.get("layout")
    if o_lay in ("shuffle", "change", "new"):
        pool = [i for i in range(len(_SERVICE_LAYOUTS)) if i != svc_layout] or [svc_layout]
        svc_layout = random.choice(pool)
    elif isinstance(o_lay, int):
        svc_layout = o_lay % len(_SERVICE_LAYOUTS)
    if o_mode == "light" and TEMPLATES[template].get("dark"):
        template = "premium"
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
    if _re.search(r"\bshop\b|\bcart\b|\bcheckout\b|sell online|online store|e-?commerce|add to cart", low):
        addons.add("shop")
    if _re.search(r"member login|members? area|sign ?up|log ?in|account|membership", low):
        addons.add("members")
    if _re.search(r"appointment|book a time|schedul|time slot|calendar booking", low):
        addons.add("appointments")
    if _re.search(r"forum|community|discussion|message board|comments? section", low):
        addons.add("community")
    for w, a in (("whatsapp", "whatsapp"), ("form", "form"), ("booking", "booking"), ("promo", "promo"),
                 ("banner", "promo"), ("advert", "promo"), ("shop", "shop"), ("cart", "shop"),
                 ("member", "members"), ("login", "members"), ("appointment", "appointments"),
                 ("forum", "community"), ("community", "community")):
        if _re.search(r"(remove|no|without|drop)\s[\w\s]*" + w, low):
            addons.discard(a)
    if isinstance(opts.get("addons"), list):        # the panel sends the exact set of ticked add-ons
        addons = set(str(a).lower() for a in opts["addons"])
    info["addons"] = list(addons)
    if opts.get("pricelist"):
        info["pricelist"] = opts["pricelist"]
    info["_svc_layout"] = svc_layout
    say("Rewriting the site")
    content = plan(core.LLM if hasattr(core, "LLM") else (lambda u, m: {"content": ""}),
                   user, dict(info, change_note=note))
    content.update(_addons(info, palette))
    html = render(content, palette, photos_rel, template, font=font_idx)
    folder = Path(row["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    try:
        (folder / "pitch.html").write_text(
            pitch_deck(folder, info, palette, info.get("addons") or [],
                       has_video=(folder / "pitch.mp4").exists()), encoding="utf-8")
    except Exception:
        pass
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
    d.update(style=style, mode=palette["mode"], template=template, font=font_idx,
             svc_layout=svc_layout, addons=info["addons"])
    if opts.get("pricelist"):
        d.setdefault("info", {})["pricelist"] = opts["pricelist"]
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
