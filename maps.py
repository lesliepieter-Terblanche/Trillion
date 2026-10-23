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


def _fetch(url):
    """Follow the short link to the full maps URL and return (final_url, page_html)."""
    try:
        r = httpx.get(url, timeout=18, follow_redirects=True, headers=UA)
        return str(r.url), (r.text or "")
    except Exception:
        return url, ""


def resolve(url):
    """Follow the short link to the full maps URL."""
    return _fetch(url)[0]


def _name_from_html(html):
    """When the name isn't in the URL path, read it from the resolved Maps page (og:title / <title>)."""
    html = html or ""
    m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I))
    if m:
        t = m.group(1).strip()
    else:
        mt = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        t = mt.group(1).strip() if mt else ""
    t = re.sub(r"\s*[-–·|]\s*Google\s*Maps\s*$", "", t, flags=re.I).strip()
    if not t or t.lower() in ("google maps", "google"):
        return ""
    return t


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


def _hours(weekday):
    wk = weekday or []
    if not wk:
        return ""
    return "; ".join(s.split(": ", 1)[-1] if ": " in s else s for s in wk[:7])[:120]


def _photos_new(key, photos, n=6):
    out = []
    for p in (photos or [])[:n]:
        nm = p.get("name")                       # e.g. "places/XXX/photos/YYY"
        if nm:
            out.append(f"https://places.googleapis.com/v1/{nm}/media?maxWidthPx=1600&key={quote(key)}")
    return out


def _area_from(address, fallback=""):
    parts = [p.strip() for p in str(address or "").split(",")]
    towns = [p for p in parts[1:] if p and not re.fullmatch(r"\d{3,5}", p) and p.lower() != "south africa"]
    return towns[0] if towns else fallback


# Places API (New) - the current API (the classic one is retired for new projects)
NEW_FIELDS = ("places.id,places.displayName,places.types,places.formattedAddress,places.nationalPhoneNumber,"
              "places.internationalPhoneNumber,places.rating,places.userRatingCount,places.regularOpeningHours,"
              "places.reviews,places.photos,places.websiteUri,places.editorialSummary")


def _search_new(key, query, limit=20):
    """Text search on Places API (New). Returns (places, status)."""
    try:
        r = httpx.post("https://places.googleapis.com/v1/places:searchText", timeout=25,
                       headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": NEW_FIELDS,
                                "Content-Type": "application/json"},
                       json={"textQuery": query, "maxResultCount": min(max(int(limit), 1), 20)})
        if r.status_code == 200:
            return (r.json().get("places") or []), "OK"
        emsg = ""
        try:
            e = r.json().get("error", {})
            emsg = e.get("status") or e.get("message") or ""
        except Exception:
            pass
        if r.status_code in (401, 403):
            return [], "REQUEST_DENIED"
        if r.status_code == 429:
            return [], "OVER_QUERY_LIMIT"
        return [], f"HTTP{r.status_code}" + ((":" + emsg) if emsg else "")
    except Exception as e:
        return [], "error:" + type(e).__name__


_DETAILS_FIELDS = ("id,displayName,types,formattedAddress,nationalPhoneNumber,internationalPhoneNumber,"
                   "rating,userRatingCount,regularOpeningHours,reviews,photos,websiteUri,editorialSummary")


def _details_new(key, place_id):
    """Full Place Details on Places API (New) - phone, hours, reviews, photos. Returns (place, status)."""
    try:
        r = httpx.get(f"https://places.googleapis.com/v1/places/{place_id}", timeout=20,
                      headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": _DETAILS_FIELDS})
        if r.status_code == 200:
            return r.json() or {}, "OK"
        if r.status_code in (401, 403):
            return {}, "REQUEST_DENIED"
        return {}, f"HTTP{r.status_code}"
    except Exception as e:
        return {}, "error:" + type(e).__name__


def _info_from_new(p, key, link=""):
    dn = (p.get("displayName") or {}).get("text") or "Business"
    addr = p.get("formattedAddress") or ""
    reviews = [(rv.get("text") or {}).get("text") for rv in (p.get("reviews") or [])[:3]
               if (rv.get("text") or {}).get("text")]
    return {
        "business": dn,
        "category": _category(p.get("types")),
        "address": addr,
        "location": _area_from(addr),
        "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
        "rating": p.get("rating"),
        "reviews": reviews,
        "hours": _hours((p.get("regularOpeningHours") or {}).get("weekdayDescriptions")),
        "about": (p.get("editorialSummary") or {}).get("text") or "",
        "photos": _photos_new(key, p.get("photos")),
        "has_website": bool(p.get("websiteUri")),
        "website": p.get("websiteUri") or "",
        "source_link": link,
    }


def search(user, query, limit=20, only_no_website=True):
    """Find businesses for a 'type in area' query, keep the ones with no website (your leads)."""
    key = key_for(user)
    if not key:
        return {"error": "Add a free Google Places key first (on the Web Studio page)."}
    places, st = _search_new(key, query, min(limit * 2, 20))
    if st != "OK" and not places:
        return {"error": f"Google Places error: {st}."}
    leads, with_site = [], 0
    for p in places:
        if len(leads) >= limit:
            break
        if p.get("websiteUri"):
            with_site += 1
            if only_no_website:
                continue
        pid = p.get("id")
        det, _ = _details_new(key, pid) if pid else ({}, "")
        leads.append(_info_from_new(det or p, key))
    return {"ok": True, "leads": leads, "scanned": len(places), "with_website": with_site}


def extract(user, link):
    """Return a business-info dict ready for web.build(), from a Google Maps link."""
    final, html = _fetch(link.strip())
    base = parse_url(final)
    name = base["name"] or _name_from_html(html)        # fall back to the page title when the URL has no name
    info = {"business": name, "category": "", "location": base["area"] or base["address"],
            "address": base["address"], "phone": "", "source_link": link, "has_website": None}
    key = key_for(user)
    info["_api"] = "no_key" if not key else ("" if name else "no_name_in_link")
    if key and name:
        q = f'{name} {base["address"]}'.strip() if base["address"] else name
        places, st = _search_new(key, q, 1)
        if not places and st == "OK" and base["area"]:          # retry with a looser query
            places, st = _search_new(key, f'{name} {base["area"]}'.strip(), 1)
        if not places and st == "OK":                           # last resort: name only
            places, st = _search_new(key, name, 1)
        info["_api"] = st if st != "OK" else ("OK" if places else "ZERO_RESULTS")
        if places:
            pid = places[0].get("id")
            det, _ = _details_new(key, pid) if pid else ({}, "")
            got = _info_from_new(det or places[0], key, link)
            got["location"] = base["area"] or got.get("location") or base["address"]
            info.update(got)
    info["_resolved"] = final
    info["_have_key"] = bool(key)
    return info
