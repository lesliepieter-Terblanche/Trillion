"""Pull a business's details from a Google Maps link, so a whole website can be generated from just a shared
link - no typing. Two ways, as chosen: the Google Places API when a key is set (full data - category, phone,
rating, reviews, photos), otherwise read what the link's own address carries and (best effort) the rendered
Maps page in the browser engine.

The Places key is stored encrypted as the secret 'google_places'. Nothing here is paid unless a key is set,
and the local brain is untouched.
"""
import re
import json
from urllib.parse import unquote, quote

import httpx

import core

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"}
MAPS_LINK = re.compile(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps)\S*", re.I)

# google place "types" -> a human category
TYPE_NAMES = {
    "barber_shop": "barber shop", "hair_care": "hair salon", "beauty_salon": "beauty salon",
    "restaurant": "restaurant", "cafe": "cafe", "bakery": "bakery", "bar": "bar", "meal_takeaway": "takeaway",
    "gym": "gym", "spa": "spa", "dentist": "dental practice", "doctor": "doctor", "physiotherapist": "physio",
    "lawyer": "law firm", "accounting": "accountants", "plumber": "plumber", "electrician": "electrician",
    "car_repair": "auto workshop", "car_dealer": "car dealer", "car_wash": "car wash", "store": "shop",
    "clothing_store": "boutique", "jewelry_store": "jeweller", "furniture_store": "furniture store",
    "florist": "florist", "real_estate_agency": "estate agency", "lodging": "guest house", "school": "school",
    "veterinary_care": "vet", "pharmacy": "pharmacy", "hardware_store": "hardware store",
}


def key_for(user):
    try:
        return core.get_secret(user["id"], "google_places", only_enabled=False) or ""
    except Exception:
        return ""


def is_maps_link(text):
    return bool(MAPS_LINK.search(text or ""))


def resolve(url):
    """Follow the short link to the full maps URL."""
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers=UA)
        return str(r.url)
    except Exception:
        return url


def parse_url(final):
    """Read the business name and address straight from the /maps/place/<name>,+<address>/ path."""
    name = address = area = ""
    m = re.search(r"/maps/place/([^/@]+)", final)
    if m:
        seg = unquote(m.group(1)).replace("+", " ").strip()
        parts = [p.strip() for p in seg.split(",") if p.strip()]
        unit = ""
        if parts and re.match(r"(?i)^(shop|unit|no\.?|suite|office|stand|shd|floor)\s*\w?\d", parts[0]):
            unit, parts = parts[0], parts[1:]          # a leading "shop 17" is a unit, not the name
        if parts:
            name = parts[0]
            rest = parts[1:]
            address = (unit + ", " if unit else "") + ", ".join(rest)
            # area: a suburb/town - skip the postcode and country at the end
            towns = [p for p in rest if not re.fullmatch(r"\d{3,5}", p) and p.lower() not in ("south africa", "südafrika")]
            area = towns[-2] if len(towns) >= 2 else (towns[-1] if towns else "")
    ftid = None
    mf = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", final)
    if mf:
        ftid = mf.group(1)
    return {"name": name, "address": address, "area": area, "ftid": ftid}


def _category(types):
    for t in types or []:
        if t in TYPE_NAMES:
            return TYPE_NAMES[t]
    for t in types or []:
        if t not in ("point_of_interest", "establishment", "service", "health", "food", "store"):
            return t.replace("_", " ")
    return ""


def _hours(oh):
    if not oh:
        return ""
    wk = oh.get("weekday_text") or []
    if not wk:
        return ""
    # compact: "Mon-Fri 8:30am-6pm, Sat 8am-4pm" is nice but fragile; keep it readable
    return "; ".join(s.split(": ", 1)[-1] if ": " in s else s for s in wk[:7])[:120]


def _photo_urls(key, photos, n=6):
    out = []
    for p in (photos or [])[:n]:
        ref = p.get("photo_reference")
        if ref:
            out.append(f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=1600&photo_reference={quote(ref)}&key={quote(key)}")
    return out


def places_details(key, name, address, ftid=None):
    """Find the place by name+address, then fetch the full detail record."""
    try:
        q = f"{name} {address}".strip()
        fp = httpx.get("https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                       params={"input": q, "inputtype": "textquery", "fields": "place_id", "key": key},
                       timeout=15, headers=UA).json()
        cands = fp.get("candidates") or []
        if not cands:
            return {}
        pid = cands[0].get("place_id")
        det = httpx.get("https://maps.googleapis.com/maps/api/place/details/json",
                        params={"place_id": pid, "key": key,
                                "fields": ("name,types,formatted_address,formatted_phone_number,"
                                           "international_phone_number,rating,user_ratings_total,"
                                           "opening_hours,reviews,photos,website")},
                        timeout=15, headers=UA).json()
        return det.get("result") or {}
    except Exception:
        return {}


def extract(user, link):
    """Return a business-info dict ready for web.build(), from a Google Maps link."""
    final = resolve(link.strip())
    base = parse_url(final)
    info = {"business": base["name"], "category": "", "location": base["area"] or base["address"],
            "address": base["address"], "phone": "", "source_link": link, "has_website": None}
    key = key_for(user)
    if key and base["name"]:
        res = places_details(key, base["name"], base["address"], base.get("ftid"))
        if res:
            info.update(
                business=res.get("name") or info["business"],
                category=_category(res.get("types")),
                address=res.get("formatted_address") or info["address"],
                location=base["area"] or (res.get("formatted_address") or "").split(",")[1:2] and
                         (res.get("formatted_address") or "").split(",")[1].strip() or base["area"],
                phone=res.get("formatted_phone_number") or res.get("international_phone_number") or "",
                rating=res.get("rating"),
                reviews=[r.get("text") for r in (res.get("reviews") or [])[:3] if r.get("text")],
                hours=_hours(res.get("opening_hours")),
                photos=_photo_urls(key, res.get("photos")),
                has_website=bool(res.get("website")),
            )
    info["_resolved"] = final
    info["_have_key"] = bool(key)
    return info
