"""Stronger web reading for Trillion, built on Scrapling (BSD-3): stealthy fetching that gets past the
anti-bot walls (Cloudflare, bot checks) that plain requests hit, plus clean page-to-markdown for the brain.

It's the preferred way to pull a page's HTML/text when a quick, robust read beats spinning up the full
site crawler — used by the prospect scorer, the "read this page" tool, and anywhere a stealth fetch helps.
Everything falls back to a plain httpx request when Scrapling isn't set up, so nothing breaks without it.

Scrapling installs once on the PC (pip + its browsers), like the other heavy helpers. The fast HTTP fetcher
needs no browser; the stealth fetcher uses one, set up by `scrapling install`.
"""
import html as _htmlmod
import re
import subprocess
import sys
import threading

import core

JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/126.0.0.0 Safari/537.36")


def ready():
    """Scrapling is importable (the parser + fetchers are installed)."""
    try:
        import scrapling  # noqa: F401
        return True
    except Exception:
        return False


# ---------- one-time setup ----------
def install():
    log = []
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--break-system-packages",
                            "scrapling[fetchers]"], capture_output=True, text=True, timeout=1800)
        log.append((r.stdout or "")[-1500:])
        if r.returncode != 0:
            # some Pythons reject --break-system-packages; retry without it
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "scrapling[fetchers]"],
                               capture_output=True, text=True, timeout=1800)
            log.append((r.stdout or "")[-1500:])
        if r.returncode != 0:
            log.append((r.stderr or "")[-1500:])
            raise RuntimeError("Could not install Scrapling. " + (r.stderr or "")[-200:])
        # download the stealth browser(s) Scrapling drives (best-effort: the fast HTTP fetcher works without it)
        for cmd in ([sys.executable, "-m", "scrapling", "install"], ["scrapling", "install"]):
            try:
                b = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                log.append((b.stdout or "")[-800:])
                if b.returncode == 0:
                    break
            except Exception:
                continue
        import importlib
        importlib.invalidate_caches()
        if not ready():
            raise RuntimeError("Scrapling installed but could not be imported.")
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up the stronger web reader (Scrapling)", error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")), error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Stronger web reader ready" if res.get("ok") else "Web reader setup failed",
                         "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


def state():
    return {"ready": ready(), "job": dict(JOB)}


# ---------- fetching ----------
def _scrapling_get(url, stealth, timeout):
    """Fetch with Scrapling, trying the stealth browser fetcher or the fast HTTP one. Returns (html, status,
    final_url) or raises. Written to tolerate Scrapling API differences across versions."""
    from scrapling.fetchers import Fetcher, StealthyFetcher  # type: ignore

    def _unpack(page):
        html = (getattr(page, "html_content", None) or getattr(page, "body", None) or
                (page.get("html_content") if isinstance(page, dict) else None) or "")
        if not isinstance(html, str):
            try:
                html = html.decode("utf-8", "replace")
            except Exception:
                html = str(html)
        status = getattr(page, "status", None) or getattr(page, "status_code", None) or 200
        final = getattr(page, "url", None) or url
        return html, int(status), str(final)

    if stealth:
        last = None
        for call in (
            lambda: StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000),
            lambda: StealthyFetcher.fetch(url, headless=True),
            lambda: StealthyFetcher().fetch(url, headless=True, network_idle=True),
        ):
            try:
                return _unpack(call())
            except Exception as e:
                last = e
        raise last or RuntimeError("stealth fetch failed")
    last = None
    for call in (
        lambda: Fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=timeout),
        lambda: Fetcher.get(url, follow_redirects=True, timeout=timeout),
        lambda: Fetcher.get(url),
    ):
        try:
            return _unpack(call())
        except Exception as e:
            last = e
    raise last or RuntimeError("fetch failed")


def _httpx_get(url, timeout):
    import httpx
    r = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=True)
    return r.text, r.status_code, str(r.url)


def fetch(url, stealth=False, timeout=25):
    """Get a page's HTML. Prefers Scrapling (stealth optional); falls back to a plain request. Never raises."""
    url = str(url or "").strip()
    if url and not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    if not url:
        return {"ok": False, "error": "no url", "html": "", "status": 0, "url": url}
    if ready():
        try:
            html, status, final = _scrapling_get(url, stealth, timeout)
            if html:
                return {"ok": True, "html": html, "status": status, "url": final, "engine": "scrapling"}
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            err = "empty page"
    else:
        err = "scrapling not set up"
    try:
        html, status, final = _httpx_get(url, timeout)
        return {"ok": True, "html": html, "status": status, "url": final, "engine": "httpx", "note": err}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}", "html": "", "status": 0, "url": url,
                "tried": err}


# ---------- html -> text / markdown ----------
_HEAD = re.compile(r"<head\b[^>]*>.*?</head>", re.I | re.S)
_SCRIPT = re.compile(r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\f\v]+")
_NL = re.compile(r"\n{3,}")


def _title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return _htmlmod.unescape(_TAG.sub("", m.group(1)).strip())[:200] if m else ""


def to_markdown(html):
    """A compact, dependency-free HTML->markdown: headings, links, list items and paragraphs. Good enough for
    the brain to read a page's content without the page's chrome."""
    h = _SCRIPT.sub(" ", _HEAD.sub(" ", html or ""))
    h = re.sub(r"(?is)<(header|nav|footer|aside)\b[^>]*>.*?</\1>", " ", h)   # drop obvious chrome
    h = re.sub(r"(?is)<br\s*/?>", "\n", h)
    h = re.sub(r"(?is)</(p|div|section|article|li|tr|h[1-6])>", "\n", h)
    for i in range(1, 7):
        h = re.sub(r"(?is)<h%d[^>]*>\s*" % i, "\n" + "#" * i + " ", h)
    h = re.sub(r"(?is)<li[^>]*>\s*", "\n- ", h)
    h = re.sub(r'(?is)<a\b[^>]*href=["\']?([^"\'>\s]+)["\']?[^>]*>(.*?)</a>',
               lambda m: f"[{_TAG.sub('', m.group(2)).strip()}]({m.group(1)})" if _TAG.sub('', m.group(2)).strip() else "", h)
    h = _TAG.sub("", h)
    h = _htmlmod.unescape(h)
    h = _WS.sub(" ", h)
    h = "\n".join(ln.strip() for ln in h.splitlines())
    h = _NL.sub("\n\n", h).strip()
    return h


def to_text(html):
    h = _SCRIPT.sub(" ", _HEAD.sub(" ", html or ""))
    h = _TAG.sub(" ", h)
    h = _htmlmod.unescape(h)
    return _NL.sub("\n\n", _WS.sub(" ", h)).strip()


def read(url, stealth=False, markdown=True, max_chars=16000, timeout=25):
    """Fetch a page and return clean content for the brain: title + markdown (or text), with the final URL.
    Uses the stealth fetcher when asked, so anti-bot / Cloudflare pages still come through."""
    r = fetch(url, stealth=stealth, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error") or "could not read the page", "url": r.get("url", url)}
    html = r["html"]
    body = to_markdown(html) if markdown else to_text(html)
    truncated = len(body) > max_chars
    return {"ok": True, "url": r["url"], "status": r["status"], "title": _title(html),
            "engine": r.get("engine"), "truncated": truncated,
            "markdown" if markdown else "text": body[:max_chars]}
