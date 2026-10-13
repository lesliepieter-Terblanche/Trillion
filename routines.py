"""Trillion routines: one phrase ("morning") runs a set of briefing sections for that login.

Every login has its own routines, its own trigger phrases and its own choices per section.
Everything here is free and needs no keys: met.no / Open-Meteo for weather, Frankfurter for
exchange rates, gold-api.com for metals, Coinbase for crypto, and the publishers' own news feeds.
"""
import re, json, time, html, datetime, ipaddress
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

import core
import watch

UA = {"User-Agent": "Trillion-home-assistant/0.5 (personal, non-commercial use)"}
HOME_CURRENCY = "ZAR"

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, triggers TEXT,
  sections TEXT, enabled INTEGER DEFAULT 1, last_run REAL, last_brief TEXT, created REAL);
CREATE TABLE IF NOT EXISTS routine_cache(k TEXT PRIMARY KEY, v TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS quotes(symbol TEXT, day TEXT, price REAL, PRIMARY KEY(symbol, day));
"""

FEEDS = {
    "citizen": ("The Citizen: South African news", "https://www.citizen.co.za/feed/"),
    "dailymaverick": ("Daily Maverick: South African news", "https://www.dailymaverick.co.za/dmrss/"),
    "businesstech": ("BusinessTech: business and money", "https://businesstech.co.za/news/feed/"),
    "moneyweb": ("Moneyweb: markets and investing", "https://www.moneyweb.co.za/feed/"),
    "mybroadband": ("MyBroadband: technology", "https://mybroadband.co.za/news/feed"),
    "techcentral": ("TechCentral: technology", "https://techcentral.co.za/feed/"),
    "aljazeera": ("Al Jazeera: world news", "https://www.aljazeera.com/xml/rss/all.xml"),
}

SECTIONS = [
    {"type": "weather", "label": "Weather", "hint": "The forecast for your town.",
     "fields": [{"key": "city", "label": "Town or city", "kind": "text"},
                {"key": "day", "label": "Which day", "kind": "select",
                 "options": [["today", "Today"], ["tomorrow", "Tomorrow"]]}],
     "defaults": {"city": "Johannesburg", "day": "today"}},
    {"type": "news", "label": "News", "hint": "The latest headlines from the sources you tick.",
     "fields": [{"key": "feeds", "label": "Sources", "kind": "feeds"},
                {"key": "custom", "label": "Your own news feeds (RSS links, one per line)", "kind": "textarea"},
                {"key": "keywords", "label": "Put stories mentioning these words first (comma separated)", "kind": "text"},
                {"key": "count", "label": "Headlines she reads out", "kind": "select",
                 "options": [["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"], ["6", "6"]]}],
     "defaults": {"feeds": ["citizen", "businesstech"], "custom": "", "keywords": "", "count": "3"}},
    {"type": "markets", "label": "Financials", "hint": "Exchange rates against the rand, gold, silver and crypto.",
     "fields": [{"key": "watch", "label": "What to track (comma separated: USD, EUR, GBP, Gold, Silver, Bitcoin, Ethereum ...)",
                 "kind": "text"}],
     "defaults": {"watch": "USD, EUR, GBP, Gold, Bitcoin"}},
    {"type": "done", "label": "Latest jobs done", "hint": "What she finished for you: jobs, videos and new notes.",
     "fields": [{"key": "period", "label": "Looking back over", "kind": "select",
                 "options": [["yesterday", "Yesterday and today"], ["today", "Today only"], ["week", "The last 7 days"]]}],
     "defaults": {"period": "yesterday"}},
    {"type": "outstanding", "label": "Outstanding", "hint": "Approvals waiting on you, unfinished jobs and jobs that failed.",
     "fields": [], "defaults": {}},
    {"type": "website", "label": "My website", "hint": "Whether your website is up and how fast it answers.",
     "fields": [], "defaults": {}},
    {"type": "pc", "label": "PC health", "hint": "Memory, disk and graphics card of the PC she runs on.",
     "fields": [], "defaults": {}},
    {"type": "phone", "label": "My phone", "hint": "Battery level of the phone linked to this login.",
     "fields": [], "defaults": {}},
    {"type": "verse", "label": "Bible verse", "hint": "A verse read aloud. The morning and the evening each have their own verse for the day.",
     "fields": [{"key": "which", "label": "Which verse", "kind": "select",
                 "options": [["morning", "The morning verse"], ["night", "The evening verse"]]}],
     "defaults": {"which": "morning"}},
    {"type": "note", "label": "My own reminder", "hint": "A line of your own that she reads out every time.",
     "fields": [{"key": "text", "label": "What she should say", "kind": "textarea"}],
     "defaults": {"text": ""}},
]
BY_TYPE = {s["type"]: s for s in SECTIONS}


class Miss(Exception):
    """A section could not be filled, with a message fit to show and say."""


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


# ---------- small helpers ----------
def cache_get(k, max_age=None):
    r = core.q("SELECT v, ts FROM routine_cache WHERE k=?", (k,), one=True)
    if not r or (max_age and time.time() - r["ts"] > max_age):
        return None
    try:
        return json.loads(r["v"])
    except Exception:
        return None


def cache_set(k, v):
    core.x("INSERT INTO routine_cache(k,v,ts) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts",
           (k, json.dumps(v), time.time()))


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())).strip()


def clean(text, limit=160):
    t = html.unescape(html.unescape(str(text or "")))
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit].rstrip()


def num(v, places=0):
    """12345.6 -> '12 346' for the screen."""
    return f"{v:,.{places}f}".replace(",", " ")


def money(v):
    if v >= 1e6:
        return f"R{v / 1e6:.2f} million"
    return "R" + (num(v, 2) if v < 1000 else num(v))


def money_say(v):
    if v >= 1e6:
        return f"{v / 1e6:.2f} million rand"
    if v >= 1000:
        return f"{round(v, -2):,.0f} rand"
    return f"{v:.2f} rand"


def hour_words(h):
    if h == 0:
        return "midnight"
    if h == 12:
        return "midday"
    if h < 12:
        return f"{h} in the morning"
    return f"{h - 12} in the {'afternoon' if h < 18 else 'evening'}"


def join_words(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"


# ---------- storing routines ----------
def clean_sections(sections):
    """Keep only known sections and known settings, in the order given; add any the list is missing."""
    out, seen = [], set()
    for s in sections or []:
        spec = BY_TYPE.get(s.get("type") if isinstance(s, dict) else None)
        if not spec or spec["type"] in seen:
            continue
        seen.add(spec["type"])
        row = {"type": spec["type"], "on": bool(s.get("on"))}
        for f in spec["fields"]:
            v = s.get(f["key"], spec["defaults"].get(f["key"]))
            if f["kind"] == "feeds":
                v = [k for k in (v or []) if k in FEEDS]
            elif f["kind"] == "select":
                v = str(v) if str(v) in [o[0] for o in f["options"]] else spec["defaults"][f["key"]]
            else:
                v = str(v or "")[:600].strip()
            row[f["key"]] = v
        out.append(row)
    for spec in SECTIONS:
        if spec["type"] not in seen:
            out.append(dict({"type": spec["type"], "on": False}, **spec["defaults"]))
    return out


def clean_triggers(triggers):
    out = []
    for t in triggers or []:
        t = norm(t)[:40].strip()
        if t and t not in out:
            out.append(t)
    return out[:8]


def as_dict(r):
    return {"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "last_run": r["last_run"],
            "triggers": json.loads(r["triggers"] or "[]"), "sections": clean_sections(json.loads(r["sections"] or "[]"))}


def _starter(on, **overrides):
    rows = []
    for t in on:
        rows.append({"type": t, "on": True, **BY_TYPE[t]["defaults"], **overrides.get(t, {})})
    return clean_sections(rows)


def ensure(user):
    """Give a login its three starter routines the first time routines are used."""
    s = core.settings(user)
    if s.get("routines_seeded"):
        return
    s["routines_seeded"] = True
    core.save_settings(user["id"], s)
    if core.q("SELECT 1 FROM routines WHERE user_id=? LIMIT 1", (user["id"],)):
        return
    morning = ["weather", "news", "markets", "done", "outstanding", "website"] + (["pc"] if user["is_admin"] else [])
    starters = [
        ("Morning", ["morning", "good morning"], _starter(morning)),
        ("Evening wrap-up", ["evening", "good evening", "good night", "wrap up"],
         _starter(["done", "outstanding", "weather"], done={"period": "today"}, weather={"day": "tomorrow"})),
        ("Status check", ["status", "status report", "how are things"],
         _starter(["outstanding", "website", "pc", "phone"])),
    ]
    for name, trig, sections in starters:
        core.x("INSERT INTO routines(user_id,name,triggers,sections,enabled,created) VALUES(?,?,?,?,1,?)",
               (user["id"], name, json.dumps(trig), json.dumps(sections), time.time()))


def list_for(user):
    ensure(user)
    return [as_dict(r) for r in core.q("SELECT * FROM routines WHERE user_id=? ORDER BY id", (user["id"],))]


def save(user, data, rid=None):
    name = clean(data.get("name"), 40)
    triggers = clean_triggers(data.get("triggers"))
    if not name:
        raise ValueError("Give the routine a name.")
    if not triggers:
        raise ValueError("Add at least one thing you say to start it, for example: morning")
    for other in list_for(user):
        if other["id"] == rid:
            continue
        clash = [t for t in triggers if t in other["triggers"]]
        if clash:
            raise ValueError(f'"{clash[0]}" already starts your {other["name"]} routine. Choose different words.')
    sections = clean_sections(data.get("sections"))
    if not any(s["on"] for s in sections):
        raise ValueError("Switch on at least one section.")
    vals = (name, json.dumps(triggers), json.dumps(sections), 1 if data.get("enabled", True) else 0)
    if rid:
        if not core.q("SELECT 1 FROM routines WHERE id=? AND user_id=?", (rid, user["id"])):
            raise ValueError("No such routine.")
        core.x("UPDATE routines SET name=?, triggers=?, sections=?, enabled=? WHERE id=?", vals + (rid,))
        return rid
    if len(list_for(user)) >= 20:
        raise ValueError("That is the limit of 20 routines. Delete one first.")
    return core.x("INSERT INTO routines(user_id,name,triggers,sections,enabled,created) VALUES(?,?,?,?,?,?)",
                  (user["id"],) + vals + (time.time(),))


def delete(user, rid):
    core.x("DELETE FROM routines WHERE id=? AND user_id=?", (rid, user["id"]))


def last_brief(user):
    r = core.q("SELECT last_brief FROM routines WHERE user_id=? AND last_brief IS NOT NULL ORDER BY last_run DESC LIMIT 1",
               (user["id"],), one=True)
    try:
        return json.loads(r["last_brief"]) if r else None
    except Exception:
        return None


# ---------- hearing the trigger ----------
FILLER = {"hey", "hi", "hello", "ok", "okay", "please", "thanks", "there"}
LEAD = r"(?:(?:run|start|do|play|give me|lets have|i want)\s+)?(?:(?:my|the)\s+)?(?:good\s+)?"
TAIL = r"(?:\s+(?:routine|briefing|brief|report|update|rundown))?"


def match(user, text):
    """The routine this short message starts, or None. Only whole-message matches count, so
    'make a morning video' never sets off the Morning routine."""
    if len(text) > 80:
        return None
    ensure(user)
    skip = FILLER | set(norm(core.settings(user).get("assistant_name", "")).split())
    said = " ".join(w for w in norm(text).split() if w not in skip)
    if not said:
        return None
    for r in core.q("SELECT * FROM routines WHERE user_id=? AND enabled=1 ORDER BY id", (user["id"],)):
        for trig in json.loads(r["triggers"] or "[]") + [norm(r["name"])]:
            if trig and re.fullmatch(LEAD + re.escape(trig) + TAIL, said):
                return r
    return None


def prompt_line(user):
    try:
        rows = [r for r in list_for(user) if r["enabled"]]
    except Exception:
        rows = []
    return "\n".join(f"- {r['name']}: starts when the user says \"{r['triggers'][0]}\"" for r in rows if r["triggers"]) or "(none)"


# ---------- weather ----------
SYMBOLS = [("thunder", "thunderstorms", "storm"), ("heavyrain", "heavy rain", "rain"), ("lightrainshowers", "light showers", "rain"),
           ("rainshowers", "showers", "rain"), ("lightrain", "light rain", "rain"), ("rain", "rain", "rain"),
           ("sleet", "sleet", "rain"), ("snow", "snow", "rain"), ("fog", "fog", "fog"),
           ("partlycloudy", "partly cloudy", "part"), ("cloudy", "cloudy", "cloud"), ("fair", "mostly sunny", "sun"),
           ("clearsky", "clear skies", "sun")]
WMO = {0: ("clear skies", "sun"), 1: ("mostly sunny", "sun"), 2: ("partly cloudy", "part"), 3: ("cloudy", "cloud"),
       45: ("fog", "fog"), 48: ("fog", "fog"), 51: ("light drizzle", "rain"), 53: ("drizzle", "rain"), 55: ("drizzle", "rain"),
       61: ("light rain", "rain"), 63: ("rain", "rain"), 65: ("heavy rain", "rain"), 80: ("light showers", "rain"),
       81: ("showers", "rain"), 82: ("heavy showers", "rain"), 95: ("thunderstorms", "storm"),
       96: ("thunderstorms with hail", "storm"), 99: ("thunderstorms with hail", "storm")}


def symbol_words(code):
    code = str(code or "").split("_")[0]
    for key, words, icon in SYMBOLS:
        if key in code:
            return words, icon
    return (code or "mixed weather"), "part"


def geocode(city):
    city = clean(city, 80)
    if not city:
        raise Miss("No town is set for the weather. Add one on the Routines page.")
    key = "geo:" + city.lower()
    hit = cache_get(key)
    if hit:
        return hit
    name, _, region = [p.strip() for p in city.partition(",")]
    r = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=8, headers=UA,
                  params={"name": name, "count": 10, "language": "en", "format": "json"})
    r.raise_for_status()
    found = r.json().get("results") or []
    if region:
        want = region.lower()
        found = [f for f in found if want in " ".join(str(f.get(k, "")) for k in ("country", "country_code", "admin1")).lower()] or found
    if not found:
        raise Miss(f"I could not find a place called {city}.")
    g = {"name": found[0]["name"], "lat": found[0]["latitude"], "lon": found[0]["longitude"]}
    cache_set(key, g)
    return g


def _metno(g, offset):
    r = httpx.get("https://api.met.no/weatherapi/locationforecast/2.0/compact", timeout=10, headers=UA,
                  params={"lat": round(g["lat"], 4), "lon": round(g["lon"], 4)})
    r.raise_for_status()
    series = r.json()["properties"]["timeseries"]
    target = datetime.date.today() + datetime.timedelta(days=offset)
    rows = []
    for e in series:
        t = datetime.datetime.fromisoformat(e["time"].replace("Z", "+00:00")).astimezone()  # the PC's own time zone
        d = e["data"]
        nxt = d.get("next_1_hours") or d.get("next_6_hours") or {}
        rows.append({"t": t, "temp": d["instant"]["details"].get("air_temperature"),
                     "wind": (d["instant"]["details"].get("wind_speed") or 0) * 3.6,
                     "rain": (d.get("next_1_hours") or {}).get("details", {}).get("precipitation_amount"),
                     "sym": nxt.get("summary", {}).get("symbol_code")})
    day = [x for x in rows if x["t"].date() == target and x["temp"] is not None] or rows[:6]
    light = [x["sym"] for x in day if 6 <= x["t"].hour <= 18 and x["sym"]] or [x["sym"] for x in day if x["sym"]]
    cond, icon = symbol_words(Counter(light).most_common(1)[0][0] if light else "")
    wet = [x for x in day if (x["rain"] or 0) >= 0.3]
    return {"now": rows[0]["temp"], "hi": max(x["temp"] for x in day), "lo": min(x["temp"] for x in day),
            "cond": cond, "icon": icon, "rain_mm": sum(x["rain"] or 0 for x in day),
            "rain_from": wet[0]["t"].hour if wet else None, "wind": max(x["wind"] for x in day)}


def _openmeteo(g, offset):
    r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=10, headers=UA, params={
        "latitude": g["lat"], "longitude": g["lon"], "current": "temperature_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "timezone": "auto", "forecast_days": 2})
    r.raise_for_status()
    j = r.json()
    d = j["daily"]
    cond, icon = WMO.get(int(d["weather_code"][offset]), ("mixed weather", "part"))
    return {"now": j["current"]["temperature_2m"], "hi": d["temperature_2m_max"][offset], "lo": d["temperature_2m_min"][offset],
            "cond": cond, "icon": icon, "rain_mm": d["precipitation_sum"][offset] or 0, "rain_from": None,
            "wind": d["wind_speed_10m_max"][offset] or 0}


def sec_weather(user, cfg, ctx):
    g = geocode(cfg.get("city"))
    offset = 1 if cfg.get("day") == "tomorrow" else 0
    try:
        w = _metno(g, offset)
    except Miss:
        raise
    except Exception:
        w = _openmeteo(g, offset)
    hi, lo, now = round(w["hi"]), round(w["lo"]), round(w["now"])
    rain = w["rain_mm"]
    if rain >= 0.5:
        rain_say = f"About {max(1, round(rain))} millimetres of rain expected" + \
                   (f", from around {hour_words(w['rain_from'])}." if w["rain_from"] is not None else ".")
        rain_line = f"{max(1, round(rain))} mm" + (f" from {w['rain_from']:02d}:00" if w["rain_from"] is not None else "")
    else:
        rain_say, rain_line = "No rain expected.", "None expected"
    windy = f" Windy, up to {round(w['wind'])} kilometres an hour." if w["wind"] >= 30 else ""
    if offset:
        say = f"Tomorrow in {g['name']}: {w['cond']}, between {lo} and {hi} degrees. {rain_say}{windy}"
        big, sub = f"{hi}°", f"Tomorrow · {w['cond']} · low {lo}°"
    else:
        say = f"In {g['name']} it is {now} degrees, {w['cond']}, heading for a top of {hi}. {rain_say}{windy}"
        big, sub = f"{now}°", f"{w['cond'].capitalize()} · top {hi}°"
    return {"title": "Weather · " + g["name"], "big": big, "sub": sub, "icon": w["icon"], "say": say,
            "tone": "red" if w["icon"] == "storm" else "",
            "lines": [{"t": "Rain", "v": rain_line}, {"t": "Wind", "v": f"up to {round(w['wind'])} km/h"}]}


# ---------- news ----------
def safe_url(u):
    p = urlparse(u.strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or host == "localhost" or "." not in host:
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    return u.strip()


SKIP_TITLE = re.compile(r"(podcast|watch|listen|video|gallery|cartoon|quiz|sponsored|partner content|in pictures)\s*[:|]", re.I)


def fetch_feed(url):
    r = httpx.get(url, timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; Trillion/0.5)"})
    r.raise_for_status()
    if len(r.content) > 3_000_000:
        raise Miss("that feed is too large")
    root = ET.fromstring(r.content)
    items = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        title = link = when = ""
        for c in el:
            tag = c.tag.rsplit("}", 1)[-1]
            if tag == "title":
                title = "".join(c.itertext())
            elif tag == "link" and not link:
                link = (c.text or "").strip() or c.get("href", "")
            elif tag in ("pubDate", "published", "updated", "date") and not when:
                when = (c.text or "").strip()
        title = clean(title)
        if title and not SKIP_TITLE.match(title):
            items.append({"title": title, "link": link if link.startswith("http") else "", "ts": _when(when)})
        if len(items) >= 12:
            break
    return items


def _when(text):
    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        try:
            return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0


def sec_news(user, cfg, ctx):
    sources = [(FEEDS[k][0].split(":")[0], FEEDS[k][1]) for k in cfg.get("feeds") or [] if k in FEEDS]
    for line in str(cfg.get("custom") or "").splitlines()[:5]:
        u = safe_url(line)
        if u:
            sources.append((urlparse(u).hostname.replace("www.", ""), u))
    if not sources:
        raise Miss("No news sources are ticked. Choose some on the Routines page.")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [(name, pool.submit(fetch_feed, url)) for name, url in sources]
    per, failed = [], []
    for name, f in futs:
        try:
            per.append([dict(i, src=name) for i in f.result()])
        except Exception:
            failed.append(name)
    if not any(per):
        raise Miss("I could not reach the news just now.")
    mixed, seen = [], set()
    for rank in range(12):  # one from each source in turn, so no single source fills the list
        for items in per:
            if rank < len(items) and items[rank]["title"].lower() not in seen:
                seen.add(items[rank]["title"].lower())
                mixed.append(items[rank])
    words = [w.strip().lower() for w in str(cfg.get("keywords") or "").split(",") if w.strip()]
    if words:
        mixed.sort(key=lambda i: not any(w in i["title"].lower() for w in words))
    count = int(cfg.get("count") or 3)
    lead = ["In the news: ", "Also: ", "And: ", "Then: ", "Next: ", "Lastly: "]
    say = " ".join(lead[n] + i["title"].rstrip(".!?") + "." for n, i in enumerate(mixed[:count]))
    return {"title": "News", "wide": True, "say": say,
            "sub": (f"Could not reach {join_words(failed)}." if failed else ""),
            "lines": [{"t": i["title"], "s": i["src"], "href": i["link"]} for i in mixed[:8]]}


# ---------- financials ----------
FIAT = set("AUD BGN BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR ISK JPY KRW MXN MYR NOK NZD PHP PLN RON SEK SGD "
           "THB TRY USD ZAR".split())
FIAT_NAMES = {"USD": "US dollar", "EUR": "Euro", "GBP": "British pound", "AUD": "Australian dollar", "CNY": "Chinese yuan",
              "JPY": "Japanese yen", "CHF": "Swiss franc", "CAD": "Canadian dollar", "INR": "Indian rupee"}
FIAT_SAY = {"USD": "a dollar", "EUR": "a euro", "GBP": "a pound"}
ALIAS = {"dollar": "USD", "dollars": "USD", "usdollar": "USD", "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP",
         "sterling": "GBP", "yen": "JPY", "yuan": "CNY", "franc": "CHF", "rupee": "INR", "gold": "XAU", "silver": "XAG", "bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH",
         "solana": "SOL", "ripple": "XRP", "litecoin": "LTC", "dogecoin": "DOGE", "cardano": "ADA"}
METALS = {"XAU": "Gold", "XAG": "Silver"}
CRYPTO_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "LTC": "Litecoin", "DOGE": "Dogecoin",
                "ADA": "Cardano"}


def parse_watch(text):
    out = []
    for raw in re.split(r"[,;\n]+", str(text or "")):
        key = re.sub(r"[^a-z0-9]", "", raw.lower())
        if not key:
            continue
        sym = ALIAS.get(key, key.upper())
        if sym != HOME_CURRENCY and re.fullmatch(r"[A-Z0-9]{2,6}", sym) and sym not in out:
            out.append(sym)
    return out[:10]


def _change(symbol, price):
    """Percent change against the last earlier day we saw this price, and remember today's."""
    prev = core.q("SELECT price FROM quotes WHERE symbol=? AND day<? ORDER BY day DESC LIMIT 1", (symbol, core.today()), one=True)
    core.x("INSERT INTO quotes(symbol,day,price) VALUES(?,?,?) ON CONFLICT(symbol,day) DO UPDATE SET price=excluded.price",
           (symbol, core.today(), price))
    return 100 * (price - prev["price"]) / prev["price"] if prev and prev["price"] else None


def _fx(symbols):
    start = (datetime.date.today() - datetime.timedelta(days=8)).isoformat()
    want = sorted({HOME_CURRENCY} | {s for s in symbols if s != "USD"})
    r = httpx.get(f"https://api.frankfurter.dev/v1/{start}..", timeout=8, headers=UA,
                  params={"base": "USD", "symbols": ",".join(want)}, follow_redirects=True)
    r.raise_for_status()
    rates = r.json()["rates"]
    days = sorted(rates)
    return rates[days[-1]], (rates[days[-2]] if len(days) > 1 else None), days[-1]


def _metal(sym):
    r = httpx.get(f"https://api.gold-api.com/price/{sym}", timeout=8, headers=UA)
    r.raise_for_status()
    return float(r.json()["price"])


def _crypto(sym):
    r = httpx.get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot", timeout=8, headers=UA)
    r.raise_for_status()
    return float(r.json()["data"]["amount"])


def sec_markets(user, cfg, ctx):
    watch = parse_watch(cfg.get("watch"))
    if not watch:
        raise Miss("Nothing is on your financials list. Add some on the Routines page.")
    fiat = [s for s in watch if s in FIAT]
    try:
        last, prev, day = _fx(fiat)
    except Exception:
        raise Miss("I could not get the exchange rates just now.")
    per = lambda code, r: r[HOME_CURRENCY] if code == "USD" else r[HOME_CURRENCY] / r[code]
    usd = last[HOME_CURRENCY]
    others = [s for s in watch if s not in FIAT]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {s: pool.submit(_metal if s in METALS else _crypto, s) for s in others}
    lines, said, missed = [], [], []
    for s in watch:
        ch = None
        if s in FIAT:
            if s not in last and s != "USD":
                missed.append(s)
                continue
            price = per(s, last)
            if prev and (s == "USD" or s in prev):
                ch = 100 * (price - per(s, prev)) / per(s, prev)
            name, spoken, sub = FIAT_NAMES.get(s, s), FIAT_SAY.get(s, f"one {s}") + " costs", ""
        else:
            try:
                dollars = futs[s].result()
            except Exception:
                missed.append(s)
                continue
            price, ch = dollars * usd, _change(s, dollars)
            name = METALS.get(s) or CRYPTO_NAMES.get(s, s)
            spoken, sub = f"{name} is at", f"${num(dollars, 2 if dollars < 100 else 0)}" + (" an ounce" if s in METALS else "")
        move = f", {'up' if ch > 0 else 'down'} {abs(ch):.1f} percent" if ch is not None and abs(ch) >= 0.3 else ""
        said.append(f"{spoken} {money_say(price)}{' an ounce' if s in METALS else ''}{move}")
        lines.append({"t": name, "v": money(price), "s": sub, "ch": None if ch is None else round(ch, 2)})
    if not lines:
        raise Miss("I could not get any of the prices on your list just now.")
    say = "On the markets: " + ". ".join(x[0].upper() + x[1:] for x in said) + "."
    if missed:
        say += f" I could not get a price for {join_words(missed)}."
    return {"title": "Financials", "say": say, "lines": lines,
            "sub": f"In rand · exchange rates from {datetime.date.fromisoformat(day).strftime('%d %b')}"
                   + (f" · no price for {', '.join(missed)}" if missed else "")}


# ---------- her own work ----------
PERIODS = {"yesterday": (86400, "Since yesterday morning"), "today": (0, "Today"), "week": (6 * 86400, "Over the last 7 days")}


def sec_done(user, cfg, ctx):
    uid = user["id"]
    back, label = PERIODS.get(cfg.get("period"), PERIODS["yesterday"])
    since = core.day_start() - back
    jobs = core.q("SELECT title FROM tasks WHERE user_id=? AND domain!='ROUTINE' AND status='completed' AND updated>=? "
                  "ORDER BY updated DESC", (uid, since))
    try:
        videos = core.q("SELECT title FROM videos WHERE user_id=? AND created>=? ORDER BY id DESC", (uid, since))
    except Exception:
        videos = []
    notes = core.q("SELECT COUNT(*) c FROM notes WHERE user_id=? AND ts>=?", (uid, since), one=True)["c"]
    if not jobs and not videos and not notes:
        return {"title": "Jobs done", "big": "0", "sub": label, "lines": [],
                "say": f"{label}, nothing new was completed."}
    say = f"{label} I completed {plural(len(jobs), 'job')}."
    if jobs:
        say += " The latest: " + join_words([clean(j["title"], 70).rstrip(".!?") for j in jobs[:3]]) + "."
    extra = join_words([("made " + plural(len(videos), "video")) if videos else "",
                        ("saved " + plural(notes, "new note")) if notes else ""])
    if extra:
        say += f" I also {extra}."
    lines = [{"t": clean(j["title"], 90)} for j in jobs[:6]] + [{"t": "Video: " + clean(v["title"], 80)} for v in videos[:3]]
    if notes:
        lines.append({"t": plural(notes, "new note") + " in memory"})
    return {"title": "Jobs done", "big": str(len(jobs)), "sub": label, "tone": "green", "lines": lines, "say": say}


def sec_outstanding(user, cfg, ctx):
    uid = user["id"]
    approvals = core.q("SELECT summary FROM approvals WHERE user_id=? AND status='pending' ORDER BY id", (uid,))
    open_jobs = core.q("SELECT title FROM tasks WHERE user_id=? AND domain!='ROUTINE' AND status IN ('waiting','in_progress') "
                       "AND id NOT IN (SELECT task_id FROM approvals WHERE status='pending' AND task_id IS NOT NULL) "
                       "ORDER BY id DESC LIMIT 10", (uid,))
    failed = core.q("SELECT title FROM tasks WHERE user_id=? AND domain!='ROUTINE' AND status='failed' AND updated>=? "
                    "ORDER BY id DESC LIMIT 10", (uid, core.day_start() - 86400))
    drafts = []
    if user["is_admin"]:
        try:
            drafts = core.q("SELECT name FROM skills WHERE status='draft' ORDER BY created")
        except Exception:
            drafts = []
    lines = ([{"t": clean(a["summary"], 110), "s": "Needs your approval", "tone": "red"} for a in approvals]
             + [{"t": clean(d["name"], 80), "s": "New playbook to approve on the Skills page", "tone": "red"} for d in drafts]
             + [{"t": clean(t["title"], 90), "s": "Not finished"} for t in open_jobs]
             + [{"t": clean(t["title"], 90), "s": "Failed"} for t in failed])
    waiting = len(approvals) + len(drafts)
    if not lines:
        return {"title": "Outstanding", "big": "0", "sub": "Nothing is waiting on you", "tone": "green", "lines": [],
                "say": "Nothing is waiting on you."}
    parts = []
    if waiting:
        first = approvals[0]["summary"] if approvals else "the playbook " + drafts[0]["name"]
        first = clean(re.sub(r"\s*\(\+?\d{6,}\)", "", first), 80).rstrip(".!?")  # no phone numbers read aloud
        parts.append(f"{plural(waiting, 'thing is', 'things are')} waiting for your approval, starting with: {first}.")
    if open_jobs:
        parts.append(f"{plural(len(open_jobs), 'job is', 'jobs are')} not finished.")
    if failed:
        parts.append(f"{plural(len(failed), 'job')} failed since yesterday, the latest being: {clean(failed[0]['title'], 70).rstrip('.!?')}.")
    return {"title": "Outstanding", "big": str(len(lines)), "tone": "red" if waiting else "",
            "sub": f"{waiting} waiting on you" if waiting else "Nothing needs your approval",
            "lines": lines[:8], "say": " ".join(parts)}


def sec_website(user, cfg, ctx):
    import tools
    s = core.settings(user)
    site = s.get("site")
    if not site:
        raise Miss("No website is set. Add one under Settings.")
    res = tools.site_check(site)
    if ctx.get("sites") is not None:
        ctx["sites"][site] = res
    name = (s.get("brand") or "").strip() or urlparse(res["url"]).hostname
    host = (urlparse(res["url"]).hostname or site).replace("www.", "")
    if res["ok"]:
        secs = res.get("ms", 0) / 1000
        return {"title": "Website", "big": "UP", "tone": "green", "sub": host,
                "lines": [{"t": "Answered in", "v": f"{secs:.1f} s"}],
                "say": f"The {name} website is up, answering in " + ("under a second." if secs < 1 else f"{secs:.1f} seconds.")}
    return {"title": "Website", "big": "DOWN", "tone": "red", "sub": host,
            "lines": [{"t": "Problem", "v": f"code {res['status']}" if res.get("status") else "no answer", "tone": "red"}],
            "say": f"Heads up: the {name} website is not answering."}


def sec_pc(user, cfg, ctx):
    v = core.vitals()
    ram = 100 * v["ram_used"] / v["ram_total"]
    disk = 100 * v["disk"]["used"] / v["disk"]["total"] if v.get("disk") else None
    lines = [{"t": "Memory", "v": f"{ram:.0f}% used", "tone": "red" if ram > 90 else ""}]
    warn = ["memory is almost full"] if ram > 90 else []
    if disk is not None:
        free = v["disk"]["total"] - v["disk"]["used"]
        lines.append({"t": "Disk", "v": f"{free:.0f} GB free", "tone": "red" if disk > 90 else ""})
        if disk > 90:
            warn.append(f"the disk is {disk:.0f} percent full")
    g = v.get("gpu")
    if g:
        lines.append({"t": "Graphics card", "v": f"{g['temp']:.0f}°C", "tone": "red" if g["temp"] > 85 else ""})
        if g["temp"] > 85:
            warn.append(f"the graphics card is running hot at {g['temp']:.0f} degrees")
    if warn:
        return {"title": "PC health", "big": "CHECK", "tone": "red", "lines": lines,
                "say": "On the PC, " + join_words(warn) + "."}
    say = f"The PC is healthy: memory {ram:.0f} percent used" + (f", disk {disk:.0f} percent full." if disk is not None else ".")
    return {"title": "PC health", "big": "OK", "tone": "green", "lines": lines, "say": say}


def sec_phone(user, cfg, ctx):
    import phone
    if not core.settings(user).get("phone_serial"):
        return {"title": "Phone", "sub": "No phone linked. Link one under Settings.", "lines": [], "say": ""}
    try:
        st = phone.status(user)
    except Exception:
        raise Miss("I could not reach your phone.")
    pct = st.get("battery_percent")
    if pct is None:
        raise Miss("Your phone did not report its battery.")
    low = pct < 25 and not st.get("charging")
    say = f"Your phone is at {pct} percent" + (" and charging." if st.get("charging") else (", so put it on charge." if low else "."))
    return {"title": "Phone", "big": f"{pct}%", "tone": "red" if low else "green",
            "sub": (st.get("model") or "Phone") + (" · charging" if st.get("charging") else ""), "lines": [], "say": say}


def sec_note(user, cfg, ctx):
    text = clean(cfg.get("text"), 500)
    if not text:
        raise Miss("Your reminder is empty. Type one on the Routines page.")
    return {"title": "Reminder", "lines": [{"t": text}], "say": text if text[-1] in ".!?" else text + "."}


def sec_verse(user, cfg, ctx):
    import bible
    if not bible.allowed(user):
        raise Miss("Bible study is not switched on for this login.")
    try:
        p = bible.daily(user, "night" if cfg.get("which") == "night" else "morning")
    except bible.BibleError as e:
        raise Miss(str(e))
    lead = "Tonight's verse" if p["when"] == "night" else "This morning's verse"
    return {"title": "Evening verse" if p["when"] == "night" else "Morning verse", "lines": [{"t": p["text"]}, {"t": p["ref"], "v": p["version_name"]}],
            "say": f"{lead} is {bible.say(p)}"}


RUNNERS = {"verse": sec_verse, "weather": sec_weather, "news": sec_news, "markets": sec_markets, "done": sec_done,
           "outstanding": sec_outstanding, "website": sec_website, "pc": sec_pc, "phone": sec_phone, "note": sec_note}

VOICE = {
    "professional": ("{hello}, {name}. It is {date}.", "That is your {routine} briefing."),
    "warm": ("{hello}, {name}! It's {date}, and here is what you need to know.", "That's everything. You've got this!"),
    "witty": ("{hello}, {name}. It's {date}, and somehow we're both still here. Right, the damage.",
              "That's the lot. Now go make some bloody money."),
}


# ---------- running one ----------
def run(user, routine, sites=None):
    """Fill every switched-on section at the same time and return what she says plus the cards to show."""
    r = routine if isinstance(routine, dict) else as_dict(routine)
    s = core.settings(user)
    ctx = {"sites": sites}
    todo = [sec for sec in r["sections"] if sec["on"]]

    def fill(sec):
        label = BY_TYPE[sec["type"]]["label"]
        try:
            card = RUNNERS[sec["type"]](user, sec, ctx)
        except Miss as e:
            card = {"title": label, "error": str(e), "say": str(e)}
        except Exception as e:
            core.log(user["id"], f"Routine: the {label} section failed ({type(e).__name__}: {e})"[:300], "waiting")
            card = {"title": label, "error": "I could not get this just now.", "say": f"I could not get the {label.lower()} just now."}
        card["type"] = sec["type"]
        return card

    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = list(pool.map(fill, todo))
    now = datetime.datetime.now()
    hello = "Good morning" if now.hour < 12 else ("Good afternoon" if now.hour < 17 else "Good evening")
    date = f"{now.strftime('%A')} {now.day} {now.strftime('%B')}"
    opener, closer = VOICE.get(s.get("personality"), VOICE["professional"])
    opener = opener.format(hello=hello, name=user["name"], date=date)
    closer = closer.format(routine=r["name"].lower())
    reply = " ".join([opener] + [c["say"] for c in cards if c.get("say")] + [closer])
    brief = {"id": r["id"], "routine": r["name"], "ts": time.time(), "title": f"{hello}, {user['name']}", "date": date,
             "cards": [{k: v for k, v in c.items() if k != "say"} for c in cards]}
    core.x("UPDATE routines SET last_run=?, last_brief=? WHERE id=?", (brief["ts"], json.dumps(brief), r["id"]))
    watch.briefing(user, r["name"], cards)
    return {"reply": reply, "brief": brief}


# ---------- asking for one thing: "what's the weather", "give me the news", "how's the rand" ----------
ASK = [
    ("weather", re.compile(r"\b(weather|forecast|temperature|is it (?:going to )?rain\w*|will it rain)\b", re.I)),
    ("news", re.compile(r"\b(news|headlines)\b", re.I)),
    ("markets", re.compile(r"\b(financials?|markets?|exchange rates?|the rand|rand dollar|dollar rate|gold price|price of gold|"
                           r"bitcoin|crypto)\b", re.I)),
]
NOT_AN_ASK = re.compile(r"\b(write|create|make|draft|save|note|email|whats ?app|send|open|file|folder|video|document|"
                        r"post|article|website|search|find|remember|routine|add|change|set)\b", re.I)


def ask(user, text, sites=None):
    """One section on its own, using the choices from that login's routines. None if this is not such a request."""
    if len(text) > 90 or NOT_AN_ASK.search(text):
        return None
    kind = next((k for k, rx in ASK if rx.search(text)), None)
    if not kind:
        return None
    ensure(user)
    cfg = None
    for r in list_for(user):                     # reuse what the user already chose for this section
        cfg = next((dict(x) for x in r["sections"] if x["type"] == kind), None)
        if cfg:
            break
    cfg = cfg or {"type": kind, **BY_TYPE[kind]["defaults"]}
    cfg["on"] = True
    if kind == "weather":
        if re.search(r"\btomorrow\b", text, re.I):
            cfg["day"] = "tomorrow"
        elif re.search(r"\b(today|now|outside)\b", text, re.I):
            cfg["day"] = "today"
        m = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{2,40}?)(?:\s+(?:today|tomorrow|now|please))?[?.!]*$", text)
        if m and m.group(1).strip().lower() not in ("today", "tomorrow", "the morning", "the week"):
            cfg["city"] = m.group(1).strip()
    label = BY_TYPE[kind]["label"]
    try:
        card = RUNNERS[kind](user, cfg, {"sites": sites})
    except Miss as e:
        card = {"title": label, "error": str(e), "say": str(e)}
    except Exception as e:
        core.log(user["id"], f"The {label.lower()} could not be fetched ({type(e).__name__}: {e})"[:300], "waiting")
        card = {"title": label, "error": "I could not get this just now.", "say": f"I could not get the {label.lower()} just now."}
    card["type"] = kind
    now = datetime.datetime.now()
    brief = {"id": None, "routine": label, "ts": time.time(), "title": label,
             "date": f"{now.strftime('%A')} {now.day} {now.strftime('%B')}",
             "cards": [{k: v for k, v in card.items() if k != "say"}]}
    return {"reply": card.get("say") or f"Here is the {label.lower()}.", "brief": brief, "name": label}
