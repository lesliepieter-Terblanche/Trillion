"""Score how outdated a business's existing website looks — to find rebuild prospects.

Trillion's lead-finder already surfaces businesses with NO website. This adds the far larger pool: businesses
whose site is clearly dated. Give it one or more URLs and it rates each from plain HTML signals (no browser,
nothing paid): a high score means a strong rebuild pitch. Pair it with the before/after card to open the deal.

Signals adapted from the MIT website-rebuilder by Jonny West (github.com/pantheonmarketing/claude-website-rebuilder).
"""
import concurrent.futures as cf
import re
import ssl
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_BUILDERS = r"lnwshop|readyplanet|wix|weebly|tarad|jimdo|godaddy sites|webs\.com|yola|moonfruit"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"


def _band(score):
    if score >= 5:
        return "strong prospect — clearly dated"
    if score >= 3:
        return "worth a look — showing its age"
    if score >= 1:
        return "minor tells — probably fine"
    return "looks current"


def score(url):
    """Rate one site. Returns {url, final, score, band, flags, has_form}. score<0 means it couldn't be read."""
    url = str(url or "").strip()
    if not url:
        return {"url": url, "score": -1, "band": "no url", "flags": ["empty"], "final": url, "has_form": False}
    if not url.lower().startswith("http"):
        url = "https://" + url
    h, final = "", url
    try:                                    # stronger reader first: gets past anti-bot / Cloudflare walls
        import webread
        got = webread.fetch(url, stealth=True, timeout=25)
        if got.get("ok") and got.get("html"):
            h, final = got["html"][:600000], got.get("url") or url
    except Exception:
        pass
    if not h:                               # fallback: plain stdlib request
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": _UA}),
                                       timeout=20, context=_CTX)
            h = r.read(600000).decode("utf-8", "replace")
            final = r.geturl()
        except Exception as e:
            return {"url": url, "score": -1, "band": "could not open", "flags": [f"error: {str(e)[:60]}"],
                    "final": url, "has_form": False}
    s, flags = 0, []
    if 'name="viewport"' not in h and "name='viewport'" not in h:
        s += 3
        flags.append("no mobile viewport")
    tables = len(re.findall(r"<table", h, re.I))
    if tables > 3:
        s += 2
        flags.append(f"table layout ({tables} tables)")
    yrs = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\D{0,15}((?:19|20)\d\d)", h, re.I)]
    if yrs and max(yrs) < 2022:
        s += 2
        flags.append(f"© {max(yrs)}")
    jq = re.search(r"jquery[-.]?(\d)\.(\d+)", h, re.I)
    if jq and int(jq.group(1)) < 2:
        s += 1
        flags.append("old jQuery")
    if final.startswith("http:"):
        s += 2
        flags.append("no HTTPS")
    if re.search(r"<font|<marquee|<center>|\.swf|bgcolor=", h, re.I):
        s += 2
        flags.append("<font>/marquee/Flash")
    gen = re.search(r'name="generator" content="([^"]+)', h, re.I)
    if gen:
        flags.append("built with " + gen.group(1)[:25])
    bld = re.search(_BUILDERS, h, re.I)
    if bld:
        flags.append("site builder: " + bld.group(0))
    has_form = bool(re.search(r"<form[^>]*>[\s\S]{0,4000}?<textarea", h, re.I))
    return {"url": url, "final": final, "score": s, "band": _band(s), "flags": flags, "has_form": has_form}


def rate(urls, progress=None):
    """Score several sites in parallel, best prospects (highest score) first."""
    say = progress or (lambda t: None)
    urls = [u for u in (urls or []) if str(u).strip()][:30]
    if not urls:
        return {"error": "Give me one or more website URLs to rate."}
    say(f"Rating {len(urls)} site{'s' if len(urls) != 1 else ''}")
    with cf.ThreadPoolExecutor(min(10, len(urls))) as ex:
        results = list(ex.map(score, urls))
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"ok": True, "results": results,
            "note": "Present the sites worth rebuilding (highest score first) with their tells. A high score "
                    "is the opener; offer to build the new site and a before/after card."}
