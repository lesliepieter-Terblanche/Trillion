"""Live business stats pulled from a site's own read-only stats endpoint (e.g. TrueHome on Emergent).

The site exposes GET <url> that returns JSON (registrations, listings, payments, visits) and checks a
secret key sent in the "X-Stats-Key" header. Trillion stores the url as a setting (truehome_stats_url)
and the key as an encrypted secret (truehome_stats). Nothing is sent anywhere except that one request to
the user's own site. Results are cached briefly so the dashboard can poll without hammering the site.
"""
import time

import httpx

import core

_CACHE = {}          # uid -> (fetched_at, data)
TTL = 45             # seconds; the dashboard polls, so don't refetch every tick


def configured(user):
    return bool((core.settings(user).get("truehome_stats_url") or "").strip()
                and core.get_secret(user["id"], "truehome_stats"))


def _num(x, d=0):
    try:
        return x if isinstance(x, (int, float)) else d
    except Exception:
        return d


def _normalise(d):
    """Keep the dashboard safe from a missing or half-filled section."""
    d = d if isinstance(d, dict) else {}
    reg = d.get("registrations") or {}
    lst = d.get("listings") or {}
    pay = d.get("payments") or {}
    vis = d.get("visits") or {}
    return {
        "generated_at": d.get("generated_at") or "",
        "registrations": {"total": _num(reg.get("total")), "today": _num(reg.get("today")),
                          "week": _num(reg.get("week")), "month": _num(reg.get("month")),
                          "recent": reg.get("recent") or []},
        "listings": {"total": _num(lst.get("total")), "active": _num(lst.get("active")),
                     "today": _num(lst.get("today")), "week": _num(lst.get("week")),
                     "recent": lst.get("recent") or []},
        "payments": {"count_month": _num(pay.get("count_month")), "revenue_month": _num(pay.get("revenue_month")),
                     "revenue_total": _num(pay.get("revenue_total")), "currency": pay.get("currency") or "ZAR",
                     "recent": pay.get("recent") or []},
        "visits": {"today": _num(vis.get("today")), "week": _num(vis.get("week")),
                   "month": _num(vis.get("month")), "top_pages": vis.get("top_pages") or []},
    }


def fetch(user, force=False):
    """Return {ok:True, stats:{...}} or {ok:False, error:...}. Cached for TTL seconds."""
    uid = user["id"]
    url = (core.settings(user).get("truehome_stats_url") or "").strip()
    key = core.get_secret(uid, "truehome_stats")
    if not url or not key:
        return {"ok": False, "error": "TrueHome tracking is not set up yet. Add the stats URL in Settings and the "
                                      "key under Settings, Keys (name it truehome_stats)."}
    if not force:
        hit = _CACHE.get(uid)
        if hit and time.time() - hit[0] < TTL:
            return {"ok": True, "stats": hit[1], "cached": True}
    try:
        r = httpx.get(url, headers={"X-Stats-Key": key.strip()}, timeout=20)
    except Exception as e:
        return {"ok": False, "error": f"Could not reach TrueHome ({type(e).__name__}). The site may still be "
                                      "publishing to live, or the stats URL is wrong."}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "TrueHome rejected the stats key (401). Check the key under Settings, Keys "
                                      "matches the STATS_KEY on the site."}
    if r.status_code == 404:
        return {"ok": False, "error": "The TrueHome stats endpoint was not found (404). It works on the preview URL "
                                      "but goes live on the real domain once the Emergent deploy finishes - until then, "
                                      "set the stats URL in Settings to the preview address."}
    if r.status_code != 200:
        return {"ok": False, "error": f"TrueHome returned {r.status_code}."}
    try:
        data = _normalise(r.json())
    except Exception:
        return {"ok": False, "error": "TrueHome replied but not with the expected stats JSON."}
    _CACHE[uid] = (time.time(), data)
    return {"ok": True, "stats": data}


def spoken(user):
    """One-line summary for chat and the morning briefing. '' if not configured."""
    if not configured(user):
        return ""
    res = fetch(user)
    if not res.get("ok"):
        return "TrueHome: " + res.get("error", "stats unavailable right now.")
    s = res["stats"]
    reg, lst, pay, vis = s["registrations"], s["listings"], s["payments"], s["visits"]
    cur = pay.get("currency", "ZAR")
    rev = pay.get("revenue_month", 0)
    return (f"TrueHome: {reg['total']} members ({reg['week']} new this week), {lst['active']} active listings, "
            f"{pay['count_month']} payments this month"
            + (f" worth {cur} {rev:,.0f}" if rev else "")
            + f", {vis['week']} site visits this week.")
