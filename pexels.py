"""Category-matched cinematic media for generated sites, from Pexels (free stock photos + videos).

So a gym gets a real gym-walkthrough video in the hero, a restaurant gets food, a salon gets a salon — the
hero and the imagery actually fit the business. Uses a free Pexels API key the owner saves once (like the
Google Places key); nothing paid, and if no key is set the site just falls back to the shader heroes.

Pexels API: https://www.pexels.com/api/ — free, attribution appreciated. We download a compact clip and a few
images into the site folder so the finished site is self-contained and works on the shared link and offline.
"""
import hashlib
import os
import re
from pathlib import Path

import core

PHOTOS = "https://api.pexels.com/v1/search"
VIDEOS = "https://api.pexels.com/videos/search"

# business category -> several industry-specific stock searches that each SHOW that trade at work.
# Several variants per trade (not one) so two same-industry businesses pull different footage: the
# business name seeds which variant is used, and which clip is taken from the results. Each variant is
# written to actually depict the work (a barber cutting hair, a fly-through of a home) rather than a
# generic "shopfront", which is what makes the hero feel specific to the business.
_KEYWORDS = [
    (("gym", "fitness", "crossfit", "boxing", "pilates", "yoga", "personal train"),
     ["gym workout training", "weightlifting fitness class", "crossfit athlete exercise", "personal trainer coaching"]),
    (("restaurant", "cafe", "coffee", "bistro", "diner", "eatery", "takeaway", "grill", "kitchen"),
     ["chef plating gourmet food", "restaurant kitchen cooking", "barista pouring coffee", "fine dining dishes close up"]),
    (("bakery", "deli", "patisserie", "pastry"),
     ["baker kneading dough", "fresh bread bakery oven", "decorating pastry cake", "croissant pastry close up"]),
    (("barber", "barbershop"),
     ["barber cutting hair clippers", "barber shaving beard", "barbershop haircut close up", "mens grooming haircut"]),
    (("bar", "pub", "lounge", "nightclub", "cocktail", "tavern"),
     ["bartender making cocktail", "cocktail bar nightlife", "pouring drinks bar", "whisky glass pour close up"]),
    (("salon", "hairdress", "hair ", "hair"),
     ["hairdresser cutting hair", "hair stylist blow dry", "hair coloring salon", "scissors cutting hair close up"]),
    (("nail", "manicure"),
     ["manicure nail painting", "nail salon close up", "applying nail polish", "nail art hands"]),
    (("beauty", "aesthetic", "lash", "brow", "makeup"),
     ["makeup artist applying", "beauty treatment face", "eyelash lashes close up", "skincare facial beauty"]),
    (("spa", "wellness", "massage", "therap"),
     ["spa massage therapy", "wellness relaxation spa", "hot stone massage", "aromatherapy spa candles"]),
    (("auto", "car ", "mechanic", "motor", "tyre", "detailing", "panel", "workshop", "garage"),
     ["mechanic repairing car engine", "car detailing polishing", "auto workshop garage", "changing tyre close up"]),
    (("dental", "dentist", "orthodon"),
     ["dentist dental treatment", "dental clinic chair", "dentist examining teeth", "bright dental smile"]),
    (("clinic", "doctor", "medical", "physio", "optom", "health", "chiro"),
     ["doctor patient consultation", "modern medical clinic", "physiotherapy treatment", "healthcare professional care"]),
    (("plumb",),
     ["plumber fixing pipes", "plumbing repair tools", "fitting bathroom plumbing", "wrench pipe close up"]),
    (("electric",),
     ["electrician wiring", "electrical installation work", "electrician panel tools", "lighting installation close up"]),
    (("handyman", "builder", "construct", "renovat", "contractor", "roofing", "paint"),
     ["construction worker building", "home renovation work", "contractor tools site", "painting renovating room"]),
    (("hotel", "guest", "lodge", "accommodation", "hospitality", "bnb", "b&b"),
     ["boutique hotel interior", "luxury hotel room", "hotel lobby hospitality", "resort pool view"]),
    (("property", "estate", "real estate", "letting", "realtor", "apartment"),
     ["modern luxury home interior walkthrough", "real estate house tour", "architecture modern house exterior", "living room interior pan"]),
    (("landscap", "garden", "nursery", "lawn"),
     ["landscaping garden design", "gardener planting green", "manicured lawn garden", "landscape garden fountain"]),
    (("farm", "agri", "harvest"),
     ["farm field agriculture", "tractor harvest crops", "farmer working field", "green farmland aerial"]),
    (("solar", "renewable", "energy"),
     ["solar panels installation", "solar farm renewable energy", "engineer solar roof", "wind solar clean energy"]),
    (("florist", "flower", "floral"),
     ["florist arranging bouquet", "fresh flowers florist shop", "wedding floral arrangement", "flower bouquet close up"]),
    (("jewel", "watch "),
     ["jeweller crafting jewellery", "luxury jewellery close up", "diamond ring display", "watchmaker luxury watch"]),
    (("boutique", "fashion", "clothing", "apparel", "tailor"),
     ["fashion boutique interior", "clothing rail boutique", "tailor sewing garment", "fashion model runway"]),
    (("photo", "videograph", "film"),
     ["photographer studio shoot", "camera photography close up", "film production set", "photographer on location"]),
    (("studio", "creative", "design", "media", "agency", "market", "brand"),
     ["creative studio team working", "designer workspace creative", "marketing agency office", "brainstorm design team"]),
    (("tech", "software", "it ", "digital", "cyber", "data", "cloud", "network", "app ", "saas", "dev"),
     ["software developers team office", "coding screen technology", "data server technology", "tech startup collaboration"]),
    (("logistics", "courier", "freight", "transport", "trucking", "ship", "warehouse", "delivery"),
     ["logistics warehouse operations", "delivery trucks fleet", "forklift warehouse shipping", "courier parcel delivery"]),
    (("retail", "shop", "store", "supplier", "grocery", "market"),
     ["retail store interior", "shopping shop display", "customer retail checkout", "boutique store shelves"]),
    (("legal", "attorney", "law", "advocate", "solicitor"),
     ["lawyer law office", "legal professional desk", "signing legal contract", "courthouse law books"]),
    (("account", "finance", "consult", "corporate", "advis", "insur", "bank", "invest"),
     ["business meeting professionals", "finance office team", "handshake corporate deal", "analyst charts office"]),
    (("pet", "vet", "groom", "animal", "kennel"),
     ["veterinarian examining dog", "pet grooming dog", "happy pets care", "vet clinic animal"]),
    (("clean", "laundr", "janitor", "maid"),
     ["cleaning service professional", "cleaner wiping surfaces", "housekeeping cleaning home", "window cleaning close up"]),
    (("event", "wedding", "catering", "party"),
     ["elegant event setup", "wedding venue decor", "catering buffet event", "party celebration lights"]),
    (("school", "tutor", "educat", "academy", "learn", "coach", "train"),
     ["classroom teaching students", "tutor helping student", "online learning laptop", "workshop training session"]),
    (("travel", "tour", "safari", "adventure"),
     ["travel scenic landscape", "tourist adventure tour", "safari wildlife nature", "aerial coastline travel"]),
    (("vape", "tobacco", "smoke"),
     ["vape shop products", "vaping lifestyle smoke", "modern vape store interior", "e-cigarette close up"]),
]


def key_for(user):
    try:
        return core.get_secret(user["id"], "pexels", only_enabled=False) or ""
    except Exception:
        return ""


def _seed(business=""):
    """A stable integer from the business name, so the same business always gets the same look,
    but two different businesses (even in the same trade) get different footage."""
    s = str(business or "").strip().lower()
    if not s:
        return 0
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:8], 16)


def _variants(category, business=""):
    """All industry-specific query variants that fit this business (first matching trade)."""
    c = (str(category or "") + " " + str(business or "")).lower()
    for words, variants in _KEYWORDS:
        if any(w in c for w in words):
            return list(variants)
    base = (str(category).strip() + " business professional").strip() or "small business shopfront"
    return [base]


def media_query(category, business=""):
    """Pick one industry-specific query, seeded by the business name so two same-trade
    businesses search for different footage (barber A -> 'barber cutting hair clippers',
    barber B -> 'barbershop haircut close up')."""
    variants = _variants(category, business)
    return variants[_seed(business) % len(variants)]


def _get(url, key, params):
    import httpx
    try:
        r = httpx.get(url, headers={"Authorization": key}, params=params, timeout=25)
        if r.status_code == 200:
            return r.json() or {}
    except Exception:
        pass
    return {}


def search_photos(key, query, n=6, orientation="landscape", seed=0):
    d = _get(PHOTOS, key, {"query": query, "per_page": 15, "orientation": orientation, "size": "large"})
    out = []
    for p in (d.get("photos") or []):
        src = p.get("src") or {}
        u = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original")
        if u:
            out.append(u)
    # rotate the result window by the seed so two same-trade businesses don't get the same photos
    if out and seed:
        off = seed % len(out)
        out = out[off:] + out[:off]
    return out[:n]


def search_video(key, query, seed=0):
    """Return (video_url, poster_url) for a landscape clip sized for a web hero (~960-1920 wide).
    Collects the good candidates and picks one seeded by the business, so two same-trade sites
    get different clips instead of both landing on the single 'best' match."""
    d = _get(VIDEOS, key, {"query": query, "per_page": 12, "orientation": "landscape", "size": "medium"})
    cands = []   # (quality_score, link, poster)
    for v in (d.get("videos") or []):
        if (v.get("width") or 0) < (v.get("height") or 1):   # skip portrait
            continue
        poster = v.get("image")
        pick = None
        for f in (v.get("video_files") or []):
            if (f.get("file_type") or "") != "video/mp4":
                continue
            w = f.get("width") or 0
            if 960 <= w <= 1920 and f.get("link"):
                score = abs(w - 1280)
                if pick is None or score < pick[0]:
                    pick = (score, f["link"], poster)
        if pick:
            cands.append(pick)
    if cands:
        cands.sort(key=lambda c: c[0])           # best web-sized first
        top = cands[:6]                          # keep the good ones
        chosen = top[seed % len(top)]            # seeded pick among them
        return chosen[1], chosen[2]
    # fallback: any mp4 at all
    vids = d.get("videos") or []
    if vids and seed:
        vids = vids[seed % len(vids):] + vids[:seed % len(vids)]
    for v in vids:
        for f in (v.get("video_files") or []):
            if f.get("link") and (f.get("file_type") or "") == "video/mp4":
                return f["link"], v.get("image")
    return None, None


def _download(url, dest, cap_mb=14):
    import httpx
    try:
        with httpx.stream("GET", url, timeout=60, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; Trillion)"}) as r:
            if r.status_code != 200:
                return False
            total = 0
            cap = cap_mb * 1024 * 1024
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(65536):
                    fh.write(chunk)
                    total += len(chunk)
                    if total > cap:
                        break
            return total > 2048
    except Exception:
        try:
            os.path.exists(dest) and os.remove(dest)
        except Exception:
            pass
        return False


def fetch_for_site(user, folder, category, business="", want_video=True, want_images=True, progress=None):
    """Download a category-matched hero video (+poster) and a few images into the site folder.
    Returns {video, poster, images}. Best-effort: anything that fails just comes back empty."""
    say = progress or (lambda t: None)
    key = key_for(user)
    out = {"video": None, "poster": None, "images": []}
    if not key:
        return out
    folder = Path(folder)
    seed = _seed(business)
    variants = _variants(category, business)
    # the business's own query first, then the trade's other variants as fallbacks
    order = variants[seed % len(variants):] + variants[:seed % len(variants)]
    primary = order[0]
    if want_video:
        say("Finding a matching video for the hero")
        for q in order:                      # try this trade's variants until one yields a clip
            vurl, poster = search_video(key, q, seed=seed)
            if vurl and _download(vurl, folder / "hero.mp4"):
                out["video"] = "hero.mp4"
                if poster and _download(poster, folder / "hero-poster.jpg", cap_mb=4):
                    out["poster"] = "hero-poster.jpg"
                break
    if want_images:
        say("Finding photos that fit the business")
        imgs = search_photos(key, primary, n=6, seed=seed)
        if not imgs and len(order) > 1:
            imgs = search_photos(key, order[1], n=6, seed=seed)
        for i, u in enumerate(imgs):
            name = f"stock{i}.jpg"
            if _download(u, folder / name, cap_mb=4):
                out["images"].append(name)
    return out


def test_key(key):
    """Quick check that a saved Pexels key works."""
    d = _get(PHOTOS, key, {"query": "office", "per_page": 1})
    return {"ok": bool(d.get("photos") is not None), "got": len(d.get("photos") or [])}
