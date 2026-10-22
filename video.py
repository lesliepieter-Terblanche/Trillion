"""Trillion video maker: turns a plain request into a finished MP4, with no media supplied.

Steps: the brain writes a short script and scene list; real footage for each scene is fetched from
Pixabay (free stock video; needs a free key in Settings); ElevenLabs reads the voice-over (if that
login has a key); then everything is graded, titled, captioned, mixed with music and encoded by
FFmpeg. Without a Pixabay key the scenes use AI-made or abstract light backdrops instead of footage.
"""
import base64, json, re, time, wave

import httpx

import core

import glob, math, os, random, shutil, subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

SIZES = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}
FPS = 30
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    for pat in (r"Microsoft\WinGet\Links\ffmpeg.exe", r"Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"):
        hits = glob.glob(os.path.join(local, pat), recursive=True)
        if hits:
            return hits[0]
    raise RuntimeError("FFmpeg was not found. Run '1 - Setup.bat' again, then restart Trillion.")


def _font(size, bold=True):
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = (["bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"])
    cands = [os.path.join(win, n) for n in names] + [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in cands:
        if os.path.exists(c):
            f = ImageFont.truetype(c, size)
            if "bahnschrift" in c.lower():
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            return f
    return ImageFont.load_default()


def _hex(c, default=(255, 180, 84)):
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in str(text).split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:5]


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _abstract_bg(w, h, accent, seed):
    """A moody light-leak backdrop for scenes without a photo."""
    rnd = random.Random(seed)
    W, H = int(w * 1.3), int(h * 1.3)
    small = Image.new("RGB", (W // 8, H // 8), (9, 8, 13))
    d = ImageDraw.Draw(small)
    teal = (40, 190, 200)
    for i in range(7):
        col = accent if i % 2 == 0 else teal
        col = tuple(int(c * rnd.uniform(0.35, 0.8)) for c in col)
        r = rnd.randint(small.width // 6, small.width // 2)
        x, y = rnd.randint(0, small.width), rnd.randint(0, small.height)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    small = small.filter(ImageFilter.GaussianBlur(small.width // 7))
    return small.resize((W, H), Image.BICUBIC)


def _cover(img, w, h, scale=1.3):
    """Resize a photo so it covers the frame with room to move the camera."""
    img = img.convert("RGB")
    k = max(w * scale / img.width, h * scale / img.height)
    return img.resize((max(w, int(img.width * k)), max(h, int(img.height * k))), Image.LANCZOS)


def _camera(base, w, h, t, move):
    """Slow push-in or pull-out with a drift (the Ken Burns move)."""
    z0, z1, dx, dy = move
    z = z0 + (z1 - z0) * t
    cw, ch = int(w * z), int(h * z)
    cw, ch = min(cw, base.width), min(ch, base.height)
    cx = (base.width - cw) / 2 + dx * (base.width - cw) / 2 * (t * 2 - 1)
    cy = (base.height - ch) / 2 + dy * (base.height - ch) / 2 * (t * 2 - 1)
    box = (int(cx), int(cy), int(cx) + cw, int(cy) + ch)
    return base.resize((w, h), Image.BILINEAR, box=box)


def _overlays(w, h):
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = y / h
        grad.putpixel((0, y), int(255 * (0.20 + 0.62 * max(0.0, (t - 0.35) / 0.65) ** 1.4)))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade.putalpha(grad.resize((w, h)))
    vig = Image.new("L", (w // 8, h // 8), 0)
    ImageDraw.Draw(vig).ellipse([-w // 40, -h // 40, w // 8 + w // 40, h // 8 + h // 40], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w // 28)).resize((w, h), Image.BICUBIC)
    vig = Image.eval(vig, lambda v: 110 + int(v * 145 / 255))
    vig = Image.merge("RGB", (vig, vig, vig))
    grains = []
    for i in range(5):
        n = Image.effect_noise((w // 2, h // 2), 26).resize((w, h), Image.NEAREST)
        g = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        g.putalpha(Image.eval(n, lambda v: max(0, min(255, int(abs(v - 128) * 0.40)))))
        grains.append(g)
    return shade, vig, grains



CACHE = core.DATA / "video_cache"
MUSIC = core.DATA / "music"
END_LEN = 3.0


def init():
    CACHE.mkdir(parents=True, exist_ok=True)
    MUSIC.mkdir(parents=True, exist_ok=True)
    c = core._conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, path TEXT, "
                  "thumb TEXT, seconds REAL, detail TEXT, created REAL)")
        c.commit()
    finally:
        c.close()


def find_ffprobe():
    ff = find_ffmpeg()
    cand = os.path.join(os.path.dirname(ff), "ffprobe" + (".exe" if ff.lower().endswith(".exe") else ""))
    return cand if os.path.exists(cand) else (shutil.which("ffprobe") or "ffprobe")


def media_seconds(path):
    try:
        out = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


# ---------- 1. the script ----------
_URL_IN = re.compile(r"https?://[^\s]+|\bwww\.[^\s]+|\b[a-z0-9][a-z0-9-]*\.(?:co\.za|com|net|org|io|shop|store|app|co)\b[^\s]*", re.I)


# ---------- whose video is this? ----------
def _domain(url):
    return re.sub(r"^(https?://)?(www\.)?", "", str(url or "").strip().lower()).split("/")[0].rstrip(".,)")


def _family():
    try:
        return json.loads((core.BASE / "family.json").read_text(encoding="utf-8"))
    except Exception:
        return []


def _too_dark(accent):
    r, g, b = _hex(accent or "", default=(0, 0, 0))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 70


def brand_for(user, prompt):
    """The brand, website and colour a video belongs to. The website NAMED IN THE REQUEST wins over whatever
    the login's settings say - so a video 'for truehome.co.za' is titled TrueHome with TrueHome's colour even
    if the settings were blank or set for another site. A brand mentioned by name (no URL) also counts."""
    s = core.settings(user)
    brand, site, accent = s.get("brand") or "", s.get("site") or "", s.get("accent") or ""
    fam = [f for f in _family() if isinstance(f, dict) and f.get("site")]
    m = _URL_IN.search(prompt or "")
    hit = None
    if m:
        dom = _domain(m.group(0))
        hit = next((f for f in fam if _domain(f["site"]) == dom), None)
        if not hit and dom and dom != _domain(site):          # a site nobody in the family owns: name it after its domain
            brand, site = dom.split(".")[0].replace("-", " ").title(), "https://www." + dom
    else:
        low = re.sub(r"\s+", "", (prompt or "").lower())
        hit = next((f for f in fam if f.get("brand") and re.sub(r"\s+", "", f["brand"].lower()) in low), None)
    if hit:
        brand, site, accent = hit.get("brand") or brand, hit["site"], hit.get("accent") or accent
    if _too_dark(accent):                                     # a black/blank accent made the titles' bar invisible
        accent = "#ffb454"
    return {"brand": brand, "site": site, "accent": accent}


def with_brand_site(prompt):
    """A request that names a family brand but no address ('a video for TrueHome') gets that brand's website
    added, so it is read and kept on-brand exactly like a request that gave the URL."""
    if _URL_IN.search(prompt or ""):
        return prompt
    low = re.sub(r"\s+", "", (prompt or "").lower())
    for f in _family():
        if isinstance(f, dict) and f.get("brand") and f.get("site") and re.sub(r"\s+", "", f["brand"].lower()) in low:
            return f"{prompt} ({f['site']})"
    return prompt


# ---------- keeping stock footage on the subject ----------
# Stock libraries answer place names with safari and scenery: "South African homes" on Pixabay returns
# elephants. So place names never go into a footage search, and every clip's own tags are checked.
_PLACES = re.compile(r"\b(south(ern)?[\s-]*africa[n]?s?|africa[n]?s?|mzansi|cape[\s-]*town|johannesburg|joburg|jozi|durban|pretoria|"
                     r"tshwane|sandton|soweto|centurion|midrand|roodepoort|gauteng|western[\s-]*cape|eastern[\s-]*cape|"
                     r"kwazulu[\s-]*natal|kzn|gqeberha|port[\s-]*elizabeth|bloemfontein|stellenbosch|sadc|namibia[n]?|"
                     r"botswana|zimbabwe(an)?|zambia[n]?|mozambique|lesotho|eswatini|sa|rsa|local|nationwide)\b", re.I)
_ANIMALS = {"animal", "wildlife", "elephant", "bird", "safari", "chicken", "hen", "rooster", "poultry", "chick", "lion", "lioness",
            "giraffe", "zebra", "rhino", "rhinoceros", "buffalo", "hippo", "hippopotamus", "monkey", "baboon", "antelope", "impala",
            "deer", "cattle", "cow", "bull", "goat", "sheep", "horse", "dog", "puppy", "cat", "kitten", "insect", "butterfly",
            "bee", "fish", "fishing", "turtle", "duck", "goose", "parrot", "eagle", "owl", "leopard", "cheetah", "wolf", "bear",
            "fox", "squirrel", "rabbit", "pig", "turaco", "ostrich", "flamingo", "crocodile", "snake", "lizard", "frog"}
_STOP = {"the", "and", "for", "with", "your", "you", "our", "its", "from", "into", "that", "this", "their", "real", "new",
         "best", "smart", "services", "service", "people", "person", "video", "shot", "scene", "footage", "stock", "cinematic"}
_PROPERTY = re.compile(r"\b(propert(y|ies)|real[\s-]*estate|estate[\s-]*agent|realtor|home[s]?|house[s]?|housing|rental[s]?|rent(ing)?|"
                       r"landlord|tenant|apartment|flat[s]?|bond|mortgage|listing[s]?|buyer[s]?|seller[s]?|suburb|townhouse)\b", re.I)
PROPERTY_SHOTS = ["modern house exterior", "luxury home interior", "living room interior", "modern kitchen interior",
                  "house keys", "real estate agent", "family new home", "apartment building", "villa swimming pool",
                  "bedroom interior", "aerial suburb houses", "couple house viewing"]


def _norm(word):
    word = re.sub(r"[^a-z]", "", word.lower())
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def clean_search(q):
    """Footage search words with place names and filler taken out ('South African family home' -> 'family home')."""
    q = _PLACES.sub(" ", str(q or ""))
    q = re.sub(r"[^\w\s-]", " ", q)
    return re.sub(r"\s+", " ", q).strip()[:60]


def topic_shots(text):
    """A safe list of on-subject searches to fall back on, for subjects stock libraries are known to get wrong."""
    hits = {m.group(0).lower() for m in _PROPERTY.finditer(text or "")}
    return PROPERTY_SHOTS if len(hits) >= 2 else []          # two different property words, so 'home of anglers' isn't property


def clip_matches(tags, query):
    """True only when a stock clip's own tags show it is about what was searched: it must share a real word with
    the search, and it must not be an animal clip unless animals were asked for."""
    tag_words = {_norm(w) for w in re.split(r"[\s,]+", str(tags or "")) if w}
    tag_words.discard("")
    want = {_norm(w) for w in str(query or "").split() if len(w) >= 3} - {_norm(w) for w in _STOP}
    want.discard("")
    animals = {_norm(a) for a in _ANIMALS}
    if (tag_words & animals) and not (want & animals):
        return False
    return bool(tag_words & want)


def site_subject(llm, user, prompt):
    """If the request points at a website, read it and ground the script in what the site actually offers -
    so a video 'for vapeessence.co.za' is about vapes, not an invented theme. No URL -> prompt unchanged."""
    m = _URL_IN.search(prompt or "")
    if not m:
        return prompt
    url = m.group(0).rstrip(".,)")
    if not url.lower().startswith("http"):
        url = "https://" + url
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; Trillion)"})
        html = r.text[:150000]
    except Exception:
        return prompt
    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, ""])[1].strip()
    md = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
    desc = md.group(1).strip() if md else ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:3000]
    try:
        summary = (llm(user, [{"role": "system", "content": "In one plain sentence say what this business/website offers and who it is for. No preamble, no markdown."},
                              {"role": "user", "content": f"Title: {title}\nDescription: {desc}\nPage: {text}"}]).get("content") or "").strip()[:300]
    except Exception:
        summary = desc or title
    brand = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return (f'{prompt}\n\nThis video is for {brand}. What they actually offer: {summary} '
            "Keep the whole video specifically about this real business and what it sells - do not invent an unrelated theme or mood.")


_SKIP_IMG = ("logo", "icon", "sprite", "favicon", "placeholder", "loader", "spinner",
             "avatar", "badge", "payment", "visa", "mastercard", "paypal", "flag", "pixel", "tracking")


def site_media(url, w, h, uid, limit=8, progress=None):
    """Pull the real product photos straight off the given website, so the footage IS the brand - no stock
    library, no uploads. Reads the page source first; if the site is a JavaScript app (source has no real
    images), opens it in a headless browser when that engine is set up. Biggest/best first; [] if nothing."""
    got = _site_media_static(url, w, h, uid, limit, progress)
    if got:
        return got
    try:                                    # JavaScript store: the photos only exist once the page runs
        import sitegrab
        if sitegrab.ready():
            return sitegrab.grab(url, w, h, uid, limit, progress)
    except Exception:
        pass
    return got


def _site_media_static(url, w, h, uid, limit=8, progress=None):
    """Read product photos straight from the page source (og:image, <img>, srcset, JSON-LD). Fast, no browser.
    Returns [] for JavaScript sites whose source carries no real images."""
    from urllib.parse import urljoin
    say = progress or (lambda t: None)
    if not url:
        return []
    if not url.lower().startswith("http"):
        url = "https://" + url
    hdr = {"User-Agent": "Mozilla/5.0 (compatible; Trillion)"}
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True, headers=hdr)
        html, base = r.text, str(r.url)
    except Exception:
        return []
    cands = []
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)[^"\']*["\'][^>]*>', html, re.I):
        mm = re.search(r'content=["\']([^"\']+)', m.group(0))
        if mm:
            cands.append(mm.group(1))
    for m in re.finditer(r'<img[^>]+>', html, re.I):
        tag = m.group(0)
        mm = re.search(r'\bsrcset=["\']([^"\']+)', tag, re.I)          # srcset: last entry is the largest
        if mm:
            parts = [p.strip().split(" ")[0] for p in mm.group(1).split(",") if p.strip()]
            if parts:
                cands.append(parts[-1])
        for attr in ("data-src", "data-original", "data-lazy-src", "data-image", "src"):
            mm = re.search(rf'\b{attr}=["\']([^"\']+)', tag, re.I)
            if mm:
                cands.append(mm.group(1))
                break
    for m in re.finditer(r'<source[^>]+srcset=["\']([^"\']+)', html, re.I):
        parts = [p.strip().split(" ")[0] for p in m.group(1).split(",") if p.strip()]
        if parts:
            cands.append(parts[-1])
    for m in re.finditer(r'"image"\s*:\s*"([^"]+)"', html):            # JSON-LD / product data
        cands.append(m.group(1).replace("\\/", "/"))
    seen, urls = set(), []
    for u in cands:
        u = (u or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        u = urljoin(base, u)
        lu = u.lower().split("?")[0]
        if not lu.startswith("http") or lu.endswith(".svg") or u.startswith("data:"):
            continue
        if lu in seen or any(b in lu for b in _SKIP_IMG):
            continue
        seen.add(lu)
        urls.append(u)
        if len(urls) >= 40:
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    scored = []
    for i, u in enumerate(urls):
        if len(scored) >= limit * 2:
            break
        try:
            rr = httpx.get(u, timeout=12, follow_redirects=True, headers=hdr)
            if rr.status_code != 200 or not rr.content:
                continue
            raw = CACHE / f"site-{uid}-{i}.bin"
            raw.write_bytes(rr.content)
            with Image.open(raw) as im:
                iw, ih = im.size
                ar = iw / max(1, ih)
                if iw < 500 or ih < 500 or ar > 4 or ar < 0.25:       # skip thumbnails, icons, banners
                    continue
                fixed = CACHE / f"site-{uid}-{i}.jpg"
                ImageOps.exif_transpose(im).convert("RGB").save(fixed, "JPEG", quality=92)
            scored.append((iw * ih, str(fixed)))
            say(f"Pulled {len(scored)} photo{'s' if len(scored) != 1 else ''} from the site")
        except Exception:
            continue
    scored.sort(reverse=True)                                          # biggest images are usually the real product shots
    return [p for _, p in scored[:limit]]


def plan(llm, user, prompt, seconds, n_scenes, with_voice, brand=None, site=None):
    s = core.settings(user)
    brand = s.get("brand") if brand is None else brand
    site = s.get("site") if site is None else site
    words = int((seconds - END_LEN) * 2.3)
    ask = f"""Write a {seconds}-second cinematic social media video for this request: "{prompt}"
Brand: {brand or 'none'}. Website: {site or 'none'}.
Reply with JSON only, in exactly this shape:
{{"title": "short title", "scenes": [{{"search": "2 to 4 plain CONCRETE words for stock footage that literally shows the real product, someone using it, or its true setting - e.g. the actual item, a person using it, the place it belongs. Use the real subject/product, NEVER abstract moods, paint, ink, liquid swirls, bokeh, or unrelated scenery. No brand names",
"headline": "at most 5 punchy words", "caption": "one short supporting sentence",
"visual": "one vivid sentence describing a cinematic photograph for this moment: the subject, the setting, the light and the mood. No words, signs or logos in the picture"}}],
"narration": "a voice-over of about {words} words for the whole video, warm and confident, no hashtags"}}
Every scene's "search" must clearly relate to the real subject/product above - a viewer should recognise what it is selling.
NEVER put a country, city or region in "search" (stock libraries answer "South Africa" with elephants and safari). For a property / real-estate business search for homes, interiors, keys, agents and buildings - never animals, wildlife or wild landscapes. Give exactly {n_scenes} scenes. English only."""
    data = {}
    try:
        text = (llm(user, [{"role": "system", "content": "You are a film director who writes tight social video scripts. Reply with JSON only."},
                           {"role": "user", "content": ask}]).get("content") or "")
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        data = {}
    scenes = [x for x in data.get("scenes", []) if isinstance(x, dict) and x.get("headline")][:n_scenes]
    if not scenes:  # the brain did not give usable JSON: fall back to a plain structure
        topic = re.sub(r"\b(create|make|generate|produce|me|a|an|the|video|cinematic|premium|social|media|second|seconds|for|\d+)\b",
                       " ", prompt, flags=re.I)
        topic = re.sub(r"\s+", " ", topic).strip() or (s.get("brand") or "Your story")
        scenes = [{"search": topic, "headline": topic[:40], "caption": ""}] * n_scenes
        data = {"title": topic[:50], "narration": ""}
    for sc in scenes:
        sc["search"] = clean_search(sc.get("search") or sc["headline"]) or clean_search(sc["headline"])
        sc["headline"], sc["caption"] = str(sc["headline"])[:60], str(sc.get("caption") or "")[:140]
        sc["visual"] = re.sub(r"\s+", " ", str(sc.get("visual") or sc["search"] or sc["headline"]))[:300]
    narration = re.sub(r"\s+", " ", str(data.get("narration") or "")).strip() if with_voice else ""
    return {"title": str(data.get("title") or prompt)[:70], "scenes": scenes, "narration": narration}


# ---------- 2. footage ----------
def pixabay_clip(key, query, w, h, need_seconds, used):
    """Find and download one stock clip from Pixabay (free key, no orientation filter: clips are cropped to fit)."""
    query = clean_search(query)
    if not query:
        return None, None
    try:
        r = httpx.get("https://pixabay.com/api/videos/", timeout=25,
                      params={"key": key, "q": query[:100], "per_page": 20, "video_type": "film", "safesearch": "true"})
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception:
        return None, None
    hits = [v for v in hits if clip_matches(v.get("tags"), query)]     # off-subject clips (elephants for 'home') never get in
    hits = [v for v in hits if v.get("id") not in used and v.get("duration", 0) >= need_seconds] or \
           [v for v in hits if v.get("id") not in used]
    for v in hits:
        sizes = [f for f in (v.get("videos") or {}).values() if f.get("url") and f.get("height")]
        if not sizes:
            continue
        # tall output crops the middle of a wide clip, so it needs more source height
        want = h if w >= h else min(1080, h)
        sizes.sort(key=lambda f: (f["height"] > 1440, abs(f["height"] - want)))
        f = sizes[0]
        path = CACHE / f"pixabay-{v['id']}-{f['height']}.mp4"
        if not path.exists():
            try:
                with httpx.stream("GET", f["url"], timeout=180, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as out:
                        for chunk in resp.iter_bytes(1 << 16):
                            out.write(chunk)
            except Exception:
                path.unlink(missing_ok=True)
                continue
        used.add(v["id"])
        return str(path), v.get("user") or "Pixabay"
    return None, None


AI_IMAGE_URL = os.environ.get("TRILLION_AI_IMAGE_URL", "https://image.pollinations.ai/prompt/")
AI_STYLE = ", cinematic photograph, dramatic natural light, shallow depth of field, rich colour, 35mm film still, highly detailed, no text"


def ai_image(visual, w, h, seed):
    """A picture made for this scene by a free image service, when there is no stock footage for it.
    Only the scene description is sent. Returns a local file path, or None if the service did not deliver."""
    from urllib.parse import quote
    gw, gh = (768, 1344) if h > w else ((1024, 1024) if h == w else (1344, 768))
    path = CACHE / f"ai-{abs(hash((visual, gw, gh, seed))) % 10 ** 12}.jpg"
    if path.exists():
        return str(path)
    try:
        r = httpx.get(AI_IMAGE_URL + quote((visual + AI_STYLE)[:700], safe=""), timeout=120, follow_redirects=True,
                      params={"width": gw, "height": gh, "seed": seed, "nologo": "true", "model": "flux"})
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 8000:
            return None
        path.write_bytes(r.content)
        with Image.open(path) as im:
            im.verify()
            if min(im.size) < 256:
                raise ValueError("too small")
        return str(path)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def clip_frames(path, dur, w, h):
    """Decode a stock clip to graded frames at the output size."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},"
          "eq=contrast=1.08:saturation=1.12:brightness=-0.02,"
          "colorbalance=rs=-0.05:bs=0.07:rh=0.06:bh=-0.07,vignette=PI/5")
    p = subprocess.Popen([find_ffmpeg(), "-v", "error", "-stream_loop", "-1", "-i", path, "-t", f"{dur + 0.3:.2f}", "-vf", vf,
                          "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            yield Image.frombytes("RGB", (w, h), b)
    finally:
        p.kill()


# ---------- 3. voice and music ----------
def voice_over(key, voice_id, text, out_path):
    """ElevenLabs speech with per-character timings (for captions). Returns [(start, end, words)]."""
    body = {"text": text, "model_id": "eleven_multilingual_v2"}
    try:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps", headers={"xi-api-key": key},
                       json=body, timeout=120)
        r.raise_for_status()
        j = r.json()
        open(out_path, "wb").write(base64.b64decode(j["audio_base64"]))
        al = j.get("alignment") or {}
        return caption_chunks(al.get("characters") or [], al.get("character_start_times_seconds") or [],
                              al.get("character_end_times_seconds") or [])
    except Exception:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", headers={"xi-api-key": key},
                       json=body, timeout=120)
        r.raise_for_status()
        open(out_path, "wb").write(r.content)
        return None


def caption_chunks(chars, starts, ends):
    words, cur, ws = [], "", None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((ws, b, cur))
                cur, ws = "", None
        else:
            if not cur:
                ws = a
            cur += ch
            last = b
    if cur:
        words.append((ws, last, cur))
    chunks, group = [], []
    for w in words:
        group.append(w)
        text = " ".join(x[2] for x in group)
        if len(group) >= 5 or len(text) >= 26 or w[2][-1:] in ".!?":
            chunks.append((group[0][0], group[-1][1], text))
            group = []
    if group:
        chunks.append((group[0][0], group[-1][1], " ".join(x[2] for x in group)))
    return chunks


def even_chunks(text, duration):
    """Fallback caption timing when the voice service gives no timings."""
    words = text.split()
    groups = [" ".join(words[i:i + 4]) for i in range(0, len(words), 4)]
    total = sum(len(g) for g in groups) or 1
    out, t = [], 0.0
    for g in groups:
        d = duration * len(g) / total
        out.append((t, t + d, g))
        t += d
    return out


def ambient_track():
    """A simple original ambient bed, generated once, so videos always have music that is safe to post."""
    path = MUSIC / "trillion-ambient.wav"
    if path.exists():
        return str(path)
    import numpy as np
    sr, bar = 44100, 8.0
    chords = [(110.0, 164.81, 220.0, 246.94, 329.63), (87.31, 130.81, 174.61, 261.63, 349.23),
              (130.81, 196.0, 261.63, 329.63, 392.0), (98.0, 146.83, 196.0, 293.66, 392.0)]
    n = int(sr * bar)
    t = np.arange(n) / sr
    env = np.clip(np.minimum(t / 2.0, (bar - t) / 2.0), 0, 1) ** 1.5
    left, right = np.zeros(n * len(chords) * 2), np.zeros(n * len(chords) * 2)
    for rep in range(2):
        for i, ch in enumerate(chords):
            seg_l, seg_r = np.zeros(n), np.zeros(n)
            for k, f in enumerate(ch):
                for harm, amp in ((1, 1.0), (2, 0.28), (3, 0.10)):
                    lfo = 1 + 0.15 * np.sin(2 * np.pi * (0.07 + 0.03 * k) * t + k)
                    seg_l += amp * lfo * np.sin(2 * np.pi * f * harm * 0.998 * t + k)
                    seg_r += amp * lfo * np.sin(2 * np.pi * f * harm * 1.002 * t + k * 1.7)
            pulse = np.sin(2 * np.pi * ch[0] / 2 * t) * (0.5 + 0.5 * np.cos(2 * np.pi * 0.5 * t)) ** 6 * 1.6
            at = (rep * len(chords) + i) * n
            left[at:at + n] += (seg_l + pulse) * env
            right[at:at + n] += (seg_r + pulse) * env
    mix = np.stack([left, right], axis=1)
    hop = int(sr * 0.31)  # one soft echo for space
    mix[hop:] += 0.35 * mix[:-hop]
    mix = (mix / np.abs(mix).max() * 0.7 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(mix.tobytes())
    return str(path)


def pick_music():
    own = [f for f in MUSIC.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg") and f.name != "trillion-ambient.wav"]
    if own:
        return str(random.choice(own)), own[0].name
    return ambient_track(), "built-in ambient"


# ---------- 4. the render ----------
def render(out_path, brand, scenes, website="", accent="#ffb454", fmt="vertical", seconds=20,
           vo_path=None, captions=None, music=None, progress=None):
    w, h = SIZES.get(fmt, SIZES["vertical"])
    accent = _hex(accent)
    body = max(seconds - END_LEN, 4.0)
    per = body / len(scenes)
    seconds = body + END_LEN
    total = int(seconds * FPS)
    margin = int(w * 0.08)
    wide = fmt == "landscape"
    f_head = _font(int(w * (0.062 if wide else 0.100)))
    f_cap = _font(int(w * (0.024 if wide else 0.040)), bold=False)
    f_sub = _font(int(w * (0.028 if wide else 0.046)))
    f_tag = _font(int(w * (0.018 if wide else 0.030)))
    f_brand = _font(int(w * (0.09 if wide else 0.15)))
    shade, vig, grains = _overlays(w, h)
    bar = int(h * (0.045 if fmt == "vertical" else 0.09))
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for i, sc in enumerate(scenes):
        sc["head"] = _wrap(probe, str(sc.get("headline", "")).upper(), f_head, w - 2 * margin)[:3]
        sc["cap"] = [] if captions else _wrap(probe, sc.get("caption", ""), f_cap, w - 2 * margin)[:3]
        if not sc.get("clip"):
            sc["base"] = None
            if sc.get("image"):
                try:
                    sc["base"] = _cover(Image.open(sc["image"]), w, h, scale=1.32)
                except Exception:
                    sc["base"] = None
            if sc["base"] is None:
                sc["base"] = _abstract_bg(w, h, accent, seed=i + 1)
            # push in, pull out, drift left, drift right: a different move each scene so pictures never feel like a slideshow
            sc["move"] = [(1.26, 1.0, 0.5, 0.25), (1.0, 1.26, -0.5, 0.3), (1.18, 1.06, 0.9, -0.2), (1.06, 1.2, -0.9, 0.2)][i % 4]
    end_bg = _abstract_bg(w, h, accent, seed=99)
    head_bottom = int(h * (0.66 if captions else 0.80)) if not wide else int(h * (0.70 if captions else 0.84))

    def text_layer(sc, lt, idx):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lh, ch = int(f_head.size * 1.06), int(f_cap.size * 1.35)
        block = len(sc["head"]) * lh + (int(h * 0.02) + len(sc["cap"]) * ch if sc["cap"] else 0)
        y = head_bottom - block
        a = _ease(lt / 0.5)
        d.rectangle([margin, y - int(h * 0.03), margin + int(w * 0.16 * _ease(lt / 0.7)), y - int(h * 0.03) + max(4, w // 180)],
                    fill=accent + (255,))
        for j, line in enumerate(sc["head"]):
            la = _ease((lt - 0.12 * j) / 0.55)
            d.text((margin + 3, y + j * lh + int((1 - la) * h * 0.03) + 3), line, font=f_head, fill=(0, 0, 0, int(150 * la)))
            d.text((margin, y + j * lh + int((1 - la) * h * 0.03)), line, font=f_head, fill=(255, 255, 255, int(255 * la)))
        cy = y + len(sc["head"]) * lh + int(h * 0.02)
        ca = _ease((lt - 0.55) / 0.6)
        for j, line in enumerate(sc["cap"]):
            d.text((margin, cy + j * ch), line, font=f_cap, fill=(240, 234, 224, int(255 * ca)))
        if brand:
            d.text((margin, bar + int(h * 0.025)), "  ".join(brand.upper()), font=f_tag, fill=accent + (int(255 * a),))
        return layer

    sub_cache = {}

    def subtitle(t):
        for k, (a, b, text) in enumerate(captions or []):
            if a - 0.05 <= t <= b + 0.25:
                if k not in sub_cache:
                    lines = _wrap(probe, text, f_sub, w - 2 * margin - 40)[:2]
                    lw = max(int(probe.textlength(ln, font=f_sub)) for ln in lines) + 56
                    lhh = int(f_sub.size * 1.3)
                    img = Image.new("RGBA", (lw, lhh * len(lines) + 28), (0, 0, 0, 0))
                    dd = ImageDraw.Draw(img)
                    dd.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=18, fill=(0, 0, 0, 175))
                    for j, ln in enumerate(lines):
                        dd.text(((lw - dd.textlength(ln, font=f_sub)) / 2, 12 + j * lhh), ln, font=f_sub, fill=(255, 255, 255, 255))
                    sub_cache[k] = img
                return sub_cache[k]
        return None

    video_only = str(out_path) + ".video.mp4"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen([find_ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                            "-i", "-",
                            # a gentle cinematic grade: a touch more contrast and colour, slightly deeper, lightly sharpened
                            "-vf", "eq=contrast=1.07:saturation=1.12:gamma=0.97,unsharp=5:5:0.5:5:5:0.0",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", video_only],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    cache = {}

    def emit(frame, t, fade):
        nonlocal n
        g = grains[n % len(grains)]
        frame.paste(g, (0, 0), g)
        sub = subtitle(t - 0.4)
        if sub is not None:
            frame.paste(sub, ((w - sub.width) // 2, int(h * (0.78 if not wide else 0.80))), sub)
        if fade < 1.0:
            frame = Image.eval(frame, lambda v, f=max(0.0, fade): int(v * f))
        bh = int(bar * _ease(t / 0.8))
        if bh:
            d = ImageDraw.Draw(frame)
            d.rectangle([0, 0, w, bh], fill=(0, 0, 0))
            d.rectangle([0, h - bh, w, h], fill=(0, 0, 0))
        enc.stdin.write(frame.tobytes())
        n += 1
        if progress and n % 45 == 0:
            progress(int(100 * n / total))

    try:
        XF = max(2, int(FPS * 0.35))          # cross-dissolve length between scenes, for smoother flow
        prev_last = None
        for idx, sc in enumerate(scenes):
            frames_needed = int(round((idx + 1) * per * FPS)) - int(round(idx * per * FPS))
            two = bool(sc.get("clip") and sc.get("clip2"))
            src = clip_frames(sc["clip"], per / 2 if two else per, w, h) if sc.get("clip") else None
            last = None
            for k in range(frames_needed):
                lt = k / FPS
                if two and k == frames_needed // 2:      # cut to the second shot
                    src.close()
                    src, last = clip_frames(sc["clip2"], per / 2, w, h), None
                frame = None
                if src is not None:
                    frame = next(src, None)
                    if frame is None:
                        frame = last.copy() if last is not None else None
                    else:
                        last = frame.copy()
                if frame is None:
                    if "base" not in sc:
                        sc["base"] = _abstract_bg(w, h, accent, seed=idx + 1)
                        sc["move"] = (1.22, 1.0, 0.6, 0.3)
                    frame = ImageChops.multiply(_camera(sc["base"], w, h, lt / per, sc["move"]), vig)
                frame.paste(shade, (0, 0), shade)
                layer = (cache.get(idx) or cache.setdefault(idx, text_layer(sc, 9.0, idx))) if lt > 1.4 else text_layer(sc, lt, idx)
                frame.paste(layer, (0, 0), layer)
                if idx > 0 and prev_last is not None and k < XF:     # dissolve into the new scene instead of cutting to black
                    frame = Image.blend(prev_last, frame, _ease((k + 1) / XF))
                if k >= frames_needed - 1:
                    prev_last = frame.copy()
                emit(frame, idx * per + lt, min(1.0, lt / 0.4) if idx == 0 else 1.0)
            if src is not None:
                src.close()
        fb = f_brand
        while probe.textlength(brand.upper(), font=fb) > w - 2 * margin and fb.size > 30:
            fb = _font(int(fb.size * 0.9))
        ei = 0
        while n < total:
            lt = n / FPS - body
            frame = ImageChops.multiply(_camera(end_bg, w, h, max(0, lt) / END_LEN, (1.25, 1.05, 0.3, 0.2)), vig)
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            a = _ease(lt / 0.6)
            bw = d.textlength(brand.upper(), font=fb)
            by = int(h * 0.44) - fb.size // 2
            d.text(((w - bw) / 2, by + int((1 - a) * h * 0.02)), brand.upper(), font=fb, fill=(255, 255, 255, int(255 * a)))
            lw = int(w * 0.22 * _ease((lt - 0.3) / 0.6))
            ly = by + int(fb.size * 1.25)
            d.rectangle([(w - lw) / 2, ly, (w + lw) / 2, ly + max(4, w // 180)], fill=accent + (255,))
            if website:
                site = website.replace("https://", "").replace("http://", "").strip("/")
                sa = _ease((lt - 0.6) / 0.6)
                d.text(((w - d.textlength(site, font=f_cap)) / 2, ly + int(h * 0.03)), site, font=f_cap,
                       fill=(240, 234, 224, int(255 * sa)))
            if prev_last is not None and ei < XF:               # dissolve from the last scene into the end card
                frame = Image.blend(prev_last, frame, _ease((ei + 1) / XF))
            ei += 1
            frame.paste(layer, (0, 0), layer)
            emit(frame, n / FPS, min(1.0, (END_LEN - lt) / 0.6))
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        if enc.wait() != 0:
            raise RuntimeError("FFmpeg could not write the video: " + err[-300:])
    except BrokenPipeError:
        raise RuntimeError("FFmpeg stopped early: " + enc.stderr.read().decode(errors="replace")[-300:])
    finally:
        if enc.poll() is None:
            enc.kill()

    # sound: voice on top, music underneath
    cmd = [find_ffmpeg(), "-y", "-v", "error", "-i", video_only]
    filt, last_a = [], None
    if vo_path:
        cmd += ["-i", str(vo_path)]
        filt.append("[1:a]adelay=400|400,volume=1.0[vo]")
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        mi = 2 if vo_path else 1
        filt.append(f"[{mi}:a]volume={0.16 if vo_path else 0.55},afade=t=in:d=1.5,afade=t=out:st={seconds - 2.5:.2f}:d=2.5[bg]")
    if vo_path and music:
        filt.append("[bg][vo]amix=inputs=2:duration=first:normalize=0[a]")
        last_a = "[a]"
    elif vo_path:
        last_a = "[vo]"
    elif music:
        last_a = "[bg]"
    if last_a:
        cmd += ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", last_a, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-t", f"{seconds:.2f}", "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        os.unlink(video_only)
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError("FFmpeg could not add the sound: " + (r.stderr or "")[-300:])
    return seconds


# ---------- 5. the whole job ----------
def create(llm, user, prompt, seconds=20, fmt="vertical", voiceover=True, progress=None, media=None):
    init()
    s = core.settings(user)
    uid = user["id"]
    seconds = max(8, min(60, int(seconds or 20)))
    fmt = fmt if fmt in SIZES else "vertical"
    w, h = SIZES[fmt]
    say = progress or (lambda text: None)
    orig_request = str(prompt)              # the user's own words, kept so the video can be revised later
    n_scenes = max(2, min(8, round((seconds - END_LEN) / 4.2)))
    # the person's own photos and clips, handed over in the conversation: one scene each, in the order given
    notes = []
    own = [m for m in (media or []) if os.path.exists(m) and m.lower().endswith(IMAGE_EXT + (".gif", ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"))][:8]
    # no photos attached, but the request names a website -> use the site's OWN product photos as the footage
    from_site = False
    if not own and _URL_IN.search(prompt or "") and s.get("video_site_media", True):
        say("Pulling photos from the site")
        got = site_media(_URL_IN.search(prompt).group(0).rstrip(".,)"), w, h, uid, progress=say)
        if got:
            own, from_site = got, True
            notes.append(f"Footage is {len(got)} real photo{'s' if len(got) != 1 else ''} pulled straight from the site.")
    n_scenes = max(2, min(8, len(own))) if from_site else max(n_scenes, len(own))
    el_key = core.get_secret(uid, "elevenlabs") if voiceover else None
    pb_key = core.get_secret(uid, "pixabay")
    find_clip, stock_key, stock_name = pixabay_clip, pb_key, "Pixabay"

    who = brand_for(user, orig_request)        # the site named in the request decides the brand, site and colour
    if _URL_IN.search(prompt or ""):
        say("Reading the website")
        prompt = site_subject(llm, user, prompt)
    say("Writing the script")
    p = plan(llm, user, prompt, seconds, n_scenes, bool(el_key), brand=who["brand"], site=who["site"])
    fallback_shots = [q for q in topic_shots(prompt)]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-")[:40] or "video"
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-{stamp}.mp4"

    vo_path, caps = None, None
    if voiceover and not el_key:
        notes.append("No voice-over: add an ElevenLabs key in Settings to get one.")
    if el_key and p["narration"]:
        say("Recording the voice-over")
        vo_path = CACHE / f"vo-{uid}-{stamp}.mp3"
        try:
            caps = voice_over(el_key, s.get("eleven_voice_id") or "21m00Tcm4TlvDq8ikWAM", p["narration"], vo_path)
            core.bump_usage(uid, "elevenlabs")
            dur = media_seconds(vo_path)
            if not caps:
                caps = even_chunks(p["narration"], dur or (seconds - END_LEN - 1))
            if dur and dur + 0.9 > seconds - END_LEN:
                seconds = min(75, int(dur + 0.9 + END_LEN + 0.999))
        except Exception as e:
            vo_path, caps = None, None
            notes.append(f"The voice-over failed ({type(e).__name__}), so this one has music and titles only.")

    credits, used = [], set()
    per = (seconds - END_LEN) / len(p["scenes"])
    mine = 0
    for sc, m in zip(p["scenes"], own):
        if m.lower().endswith(IMAGE_EXT + (".gif",)):
            try:
                with Image.open(m) as im:               # phone photos are often stored sideways with a note saying which way is up
                    fixed = CACHE / f"own-{uid}-{abs(hash(m)) % 10 ** 10}.jpg"
                    ImageOps.exif_transpose(im).convert("RGB").save(fixed, "JPEG", quality=92)
                sc["image"] = str(fixed)
                mine += 1
            except Exception:
                notes.append(f"I could not open {os.path.basename(m)}, so that scene uses other footage.")
        elif media_seconds(m):
            sc["clip"] = m
            mine += 1
        else:
            notes.append(f"I could not play {os.path.basename(m)}, so that scene uses other footage.")
    if mine:
        notes.append(f"{mine} scene{'s' if mine != 1 else ''} use{'s' if mine == 1 else ''} your own photo{'s' if mine != 1 else ''} or clip{'s' if mine != 1 else ''}.")
    if stock_key:
        for i, sc in enumerate(p["scenes"]):
            if sc.get("clip") or sc.get("image"):
                continue
            say(f"Finding footage {i + 1} of {len(p['scenes'])}")
            clip, by = find_clip(stock_key, sc["search"], w, h, per, used)
            # nothing on-subject for this scene's words: try the safe searches for this kind of business
            while not clip and fallback_shots:
                q = fallback_shots.pop(i % len(fallback_shots))
                clip, by = find_clip(stock_key, q, w, h, per, used)
                if clip:
                    sc["search"] = q
            if clip:
                sc["clip"] = clip
                credits.append(by)
                if per >= 3.6:                       # long enough for two shots: quicker cuts feel more like a film
                    clip2, by2 = find_clip(stock_key, sc["search"], w, h, per / 2, used)
                    if clip2:
                        sc["clip2"] = clip2
                        credits.append(by2)
    bare = [sc for sc in p["scenes"] if not sc.get("clip") and not sc.get("image")]
    made = 0
    if bare and s.get("video_ai_images", True):
        for i, sc in enumerate(bare):
            say(f"Making picture {i + 1} of {len(bare)}")
            img = ai_image(sc["visual"], w, h, seed=int(time.time()) % 100000 + i)
            if img:
                sc["image"] = img
                made += 1
            elif i == 0 and len(bare) > 1:
                break                                # the picture service is not answering: do not wait for every scene
        if made:
            notes.append(f"{made} scene{'s' if made != 1 else ''} use{'s' if made == 1 else ''} AI-made pictures from the free Pollinations service "
                         "(only the scene description is sent to it).")
    left = len(bare) - made
    if left:
        saved = core.get_secret(uid, "pixabay", only_enabled=False)
        why = ("the picture service did not answer" if s.get("video_ai_images", True) else "AI pictures are switched off")
        notes.append(f"{left} scene{'s' if left != 1 else ''} use{'s' if left == 1 else ''} a plain backdrop because no footage was found and {why}."
                     + ("" if stock_key else (" Your footage key is saved but switched off." if saved else
                                              " Add a free Pixabay key under Settings, Keys for real moving footage.")))
    elif not stock_key and made:
        notes.append("For real moving footage instead of pictures, add a free Pixabay key under Settings, Keys.")

    music, music_name = pick_music()
    say("Rendering")
    seconds = render(out, who["brand"], p["scenes"], website=who["site"], accent=who["accent"],
                     fmt=fmt, seconds=seconds, vo_path=vo_path, captions=caps, music=music,
                     progress=lambda pct: say(f"Rendering {pct}%"))
    thumb = str(out)[:-4] + ".jpg"
    subprocess.run([find_ffmpeg(), "-y", "-v", "error", "-ss", "1.6", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2", thumb],
                   capture_output=True)
    if vo_path:
        try:
            os.unlink(vo_path)
        except OSError:
            pass
    for sc in p["scenes"]:
        sc.pop("base", None)
    detail = {"ai_pictures": made, "scenes": [sc["headline"] for sc in p["scenes"]], "narration": p["narration"] if vo_path else "",
              "footage": sorted(set(credits)), "footage_source": stock_name if credits else "", "music": music_name, "format": fmt, "notes": notes,
              "request": orig_request, "voiceover": bool(voiceover), "media": own}   # kept so the video can be changed and remade
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (uid, p["title"], str(out), thumb if os.path.exists(thumb) else "", seconds, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": p["title"], "saved": str(out), "seconds": round(seconds, 1),
            "footage_clips": len(credits), "ai_pictures": made, "voice_over": bool(vo_path), "notes": notes}
