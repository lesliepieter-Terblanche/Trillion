"""Category-matched cinematic media for generated sites, from Pexels (free stock photos + videos).

So a gym gets a real gym-walkthrough video in the hero, a restaurant gets food, a salon gets a salon — the
hero and the imagery actually fit the business. Uses a free Pexels API key the owner saves once (like the
Google Places key); nothing paid, and if no key is set the site just falls back to the shader heroes.

Pexels API: https://www.pexels.com/api/ — free, attribution appreciated. We download a compact clip and a few
images into the site folder so the finished site is self-contained and works on the shared link and offline.
"""
import os
import re
from pathlib import Path

import core

PHOTOS = "https://api.pexels.com/v1/search"
VIDEOS = "https://api.pexels.com/videos/search"

# business category -> the stock search that best shows that kind of place at work
_KEYWORDS = [
    (("gym", "fitness", "crossfit", "boxing", "pilates", "yoga"), "modern gym workout fitness training"),
    (("restaurant", "cafe", "coffee", "bistro", "diner", "eatery", "takeaway", "grill"), "restaurant food cooking chef"),
    (("bakery", "deli", "patisserie"), "bakery fresh bread pastry"),
    (("bar", "pub", "lounge", "night", "club"), "cocktail bar nightlife drinks"),
    (("salon", "beauty", "hair", "barber", "nail", "aesthetic"), "hair salon beauty styling"),
    (("spa", "wellness", "massage", "therap"), "spa wellness relaxation massage"),
    (("auto", "car ", "mechanic", "motor", "tyre", "detailing", "panel", "workshop"), "car repair auto mechanic garage"),
    (("dental", "dentist"), "dental clinic dentist"),
    (("clinic", "doctor", "medical", "physio", "optom"), "medical clinic healthcare"),
    (("plumb", "electric", "handyman", "builder", "construct", "renovat"), "construction trade contractor working"),
    (("hotel", "guest", "lodge", "accommodation"), "boutique hotel interior hospitality"),
    (("property", "estate", "real estate", "letting"), "modern house property real estate"),
    (("landscap", "garden", "farm", "nursery"), "landscaping garden green"),
    (("solar", "energy"), "solar panels renewable energy"),
    (("florist", "flower"), "florist flowers bouquet"),
    (("jewel", "watch", "boutique", "fashion"), "luxury boutique fashion jewellery"),
    (("photo", "studio", "creative", "design", "media", "agency"), "creative studio design workspace"),
    (("tech", "software", "it ", "digital", "cyber", "data", "cloud", "network"), "technology office software team"),
    (("logistics", "courier", "freight", "transport", "trucking"), "logistics delivery trucks warehouse"),
    (("retail", "shop", "store", "supplier"), "retail shop interior shopping"),
    (("legal", "attorney", "law", "advocate"), "law office professional"),
    (("account", "finance", "consult", "corporate", "advis"), "modern office business meeting"),
    (("pet", "vet", "groom"), "pet grooming dog care"),
    (("clean", "laundr"), "cleaning service professional"),
]


def key_for(user):
    try:
        return core.get_secret(user["id"], "pexels", only_enabled=False) or ""
    except Exception:
        return ""


def media_query(category, business=""):
    c = (str(category or "") + " " + str(business or "")).lower()
    for words, q in _KEYWORDS:
        if any(w in c for w in words):
            return q
    return (str(category).strip() + " business professional").strip() or "small business shopfront"


def _get(url, key, params):
    import httpx
    try:
        r = httpx.get(url, headers={"Authorization": key}, params=params, timeout=25)
        if r.status_code == 200:
            return r.json() or {}
    except Exception:
        pass
    return {}


def search_photos(key, query, n=6, orientation="landscape"):
    d = _get(PHOTOS, key, {"query": query, "per_page": min(n, 15), "orientation": orientation, "size": "large"})
    out = []
    for p in (d.get("photos") or []):
        src = p.get("src") or {}
        u = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original")
        if u:
            out.append(u)
    return out[:n]


def search_video(key, query):
    """Return (video_url, poster_url) for the best landscape clip, sized for a web hero (~1280-1920 wide)."""
    d = _get(VIDEOS, key, {"query": query, "per_page": 8, "orientation": "landscape", "size": "medium"})
    best = None
    for v in (d.get("videos") or []):
        if (v.get("width") or 0) < (v.get("height") or 1):   # skip portrait
            continue
        poster = v.get("image")
        for f in (v.get("video_files") or []):
            if (f.get("file_type") or "") != "video/mp4":
                continue
            w = f.get("width") or 0
            if 960 <= w <= 1920 and f.get("link"):
                score = abs(w - 1280)
                if best is None or score < best[0]:
                    best = (score, f["link"], poster)
    if best:
        return best[1], best[2]
    # fallback: any mp4
    for v in (d.get("videos") or []):
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
    q = media_query(category, business)
    if want_video:
        say("Finding a matching video for the hero")
        vurl, poster = search_video(key, q)
        if vurl and _download(vurl, folder / "hero.mp4"):
            out["video"] = "hero.mp4"
            if poster and _download(poster, folder / "hero-poster.jpg", cap_mb=4):
                out["poster"] = "hero-poster.jpg"
    if want_images:
        say("Finding photos that fit the business")
        imgs = search_photos(key, q, n=6)
        for i, u in enumerate(imgs):
            name = f"stock{i}.jpg"
            if _download(u, folder / name, cap_mb=4):
                out["images"].append(name)
    return out


def test_key(key):
    """Quick check that a saved Pexels key works."""
    d = _get(PHOTOS, key, {"query": "office", "per_page": 1})
    return {"ok": bool(d.get("photos") is not None), "got": len(d.get("photos") or [])}
