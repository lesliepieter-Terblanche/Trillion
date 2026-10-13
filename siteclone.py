"""Cloning websites, two ways.

COPY: a full working copy of a site the login owns (or has the owner's permission for): every page, stylesheet,
script, picture and font, saved as ordinary files and folders with the links changed so the copy opens from the PC
and can be uploaded to any host. Pages that only exist after the site's JavaScript runs (sites built with React and
similar, like Emergent) are saved as they look in the browser, so they still open and can be edited.

REBUILD: a brand-new site in the layout and style of another one. Trillion measures the other site's look (colours,
fonts, spacing, the order and shape of its sections) and builds new pages from her own templates, with the login's
own brand name, words written for that business, and the login's own pictures. Nothing of the other site's text,
pictures, logos or code goes into it.

Every result: a folder with the site, a README, a zip ready to upload, and a private preview link.
"""
import datetime
import hashlib
import html as htmlmod
import io
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shutil
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import httpx

import core
import siteread
from siteread import SiteError, normalise_url, host_key, public_host, clean_link, _same, UA

MAX_PAGES = 300
DEFAULT_COPY_PAGES = 60
MAX_FILE = 40 * 1024 ** 2
MAX_TOTAL = 600 * 1024 ** 2
REBUILD_PAGES = 6
TRACKERS = re.compile(r"google-analytics|googletagmanager|doubleclick|facebook\.net|connect\.facebook|hotjar|clarity\.ms|"
                      r"tiktok\.com/i18n/pixel|analytics\.tiktok|snap\.licdn|bat\.bing|segment\.(io|com)|mixpanel|intercomcdn", re.I)
ASSET_TYPES = {"stylesheet", "script", "image", "font", "media", "manifest", "other"}
PAGE_EXT = {".html", ".htm"}
SERVER_EXT = {".php", ".asp", ".aspx", ".jsp", ".cfm", ".cgi", ".shtml"}
STOP, _LOCK, RUNNING = set(), threading.Lock(), {}

core.x("""CREATE TABLE IF NOT EXISTS site_clones(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, domain TEXT, mode TEXT,
          status TEXT, created REAL, finished REAL, pages INTEGER DEFAULT 0, files INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0,
          detail TEXT DEFAULT '', brief TEXT DEFAULT '{}', task_id INTEGER, folder TEXT DEFAULT '', token TEXT,
          result TEXT DEFAULT '{}')""")
core.x("UPDATE site_clones SET status='stopped', detail='Trillion restarted while this was being made.' WHERE status='running'")


# ---------- whose site is it ----------
def own_domains(user):
    """The sites this login may copy without asking: its own, and for the owner every family site."""
    rows = core.q("SELECT * FROM users") if user["is_admin"] else [user]
    out = set()
    for r in rows:
        s = core.settings(r).get("site") or ""
        if s:
            try:
                out.add(host_key(urlparse(normalise_url(s)).hostname))
            except SiteError:
                pass
    return out


def is_own(user, url):
    return host_key(urlparse(normalise_url(url)).hostname) in own_domains(user)


# ---------- where each address goes in the folder ----------
_BAD = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)


def _seg(s):
    s = _BAD.sub("_", s).rstrip(" .")[:80] or "_"
    return s + "_" if _RESERVED.match(s) else s


def local_path(url, key, kind, ctype=""):
    """The file inside the copy for this address (forward slashes, no leading slash)."""
    u = urlparse(url)
    path = unquote(u.path or "/")
    base = "" if host_key(u.hostname) == key else "_ext/" + _seg(u.hostname or "x") + "/"
    parts = [p for p in path.split("/") if p not in ("", ".", "..")]
    trailing = path.endswith("/") or not parts
    stem, ext = posixpath.splitext(parts[-1]) if parts and not trailing else ("", "")
    if kind == "page":
        if trailing:
            parts.append("index.html")
        elif ext.lower() in PAGE_EXT:
            pass
        elif ext.lower() in SERVER_EXT or not ext:
            parts[-1] = stem if ext else parts[-1]
            parts.append("index.html")
        else:
            parts[-1] += ".html"
    else:
        if trailing:
            parts.append("index")
            stem, ext = "index", ""
        if not ext:
            guess = mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ""
            parts[-1] += {".jpe": ".jpg", ".htm": ".html"}.get(guess, guess)
    if u.query:
        h = hashlib.sha1(u.query.encode()).hexdigest()[:8]
        s_, e_ = posixpath.splitext(parts[-1])
        if kind == "page" and parts[-1] == "index.html":
            parts.insert(-1, "_" + h)
        else:
            parts[-1] = f"{s_}_{h}{e_}"
    return base + "/".join(_seg(p) for p in parts)


def rel(target, from_file):
    r_ = posixpath.relpath(target, posixpath.dirname(from_file) or ".")
    return r_


# ---------- rewriting links inside pages and stylesheets ----------
ATTR = re.compile(r"""(\s(?:src|href|poster|data-src|data-lazy-src|data-original|data-bg|data-background|data-image|action)\s*=\s*)(["'])(.*?)\2""",
                  re.I | re.S)
SRCSET = re.compile(r"""(\s(?:srcset|data-srcset|data-lazy-srcset)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
CSSURL = re.compile(r"""url\(\s*(["']?)(.*?)\1\s*\)""", re.I)
CSSIMPORT = re.compile(r"""@import\s+(["'])(.*?)\1""", re.I)
STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
STYLE_ATTR = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
SCRIPT = re.compile(r"<script\b(?![^>]*application/ld\+json)[^>]*>.*?</script>", re.I | re.S)
BASE = re.compile(r"<base\b[^>]*>", re.I)
SRI = re.compile(r"""\s(?:integrity|crossorigin)(?:\s*=\s*(["']).*?\1)?(?=[\s>/])""", re.I | re.S)
NOSCRIPT_GONE = re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.I | re.S)


def _skip(v):
    v = v.strip()
    return not v or v.startswith(("#", "data:", "mailto:", "tel:", "javascript:", "about:", "blob:", "{", "[", "%7B"))


class Mapper:
    def __init__(self, key):
        self.key, self.pages, self.assets = key, {}, {}

    def page(self, url):
        return self.pages.get(_same(url))

    def asset(self, url):
        return self.assets.get(url.split("#")[0])

    def to(self, raw, base, here, kind_hint=""):
        """The new value for one link found in a file at `here` (its own place in the copy)."""
        if _skip(raw):
            return raw
        try:
            absu = urljoin(base, htmlmod.unescape(raw.strip()))
        except ValueError:
            return raw
        frag = ("#" + absu.split("#", 1)[1]) if "#" in absu else ""
        target = None if kind_hint == "asset" else self.page(absu)
        if not target:
            target = self.asset(absu)
        if target:
            return rel(target, here) + frag
        return absu if absu.startswith(("http://", "https://")) else raw


def rewrite_css(css, base, here, m):
    css = CSSIMPORT.sub(lambda x: f'@import "{m.to(x.group(2), base, here, "asset")}"', css)
    return CSSURL.sub(lambda x: f'url("{m.to(x.group(2), base, here, "asset")}")', css)


def rewrite_html(doc, base, here, m, snapshot=False):
    b = BASE.search(doc)
    if b:
        hb = re.search(r"""href\s*=\s*(["'])(.*?)\1""", b.group(0), re.I)
        if hb:
            base = urljoin(base, hb.group(2))
        doc = BASE.sub("", doc)
    if snapshot:
        doc = SCRIPT.sub("", doc)
        doc = NOSCRIPT_GONE.sub("", doc)
    doc = SRI.sub("", doc)
    doc = STYLE_BLOCK.sub(lambda x: x.group(1) + rewrite_css(x.group(2), base, here, m) + x.group(3), doc)
    doc = STYLE_ATTR.sub(lambda x: x.group(1) + x.group(2) + rewrite_css(x.group(3), base, here, m) + x.group(2), doc)

    def one_attr(x):
        name = x.group(1).strip().lower()
        hint = "" if name.startswith(("href", "action")) else "asset"
        return x.group(1) + x.group(2) + m.to(x.group(3), base, here, hint) + x.group(2)

    doc = ATTR.sub(one_attr, doc)

    def one_set(x):
        items = []
        for part in x.group(3).split(","):
            bits = part.strip().split()
            if bits:
                bits[0] = m.to(bits[0], base, here, "asset")
                items.append(" ".join(bits))
        return x.group(1) + x.group(2) + ", ".join(items) + x.group(2)

    return SRCSET.sub(one_set, doc)


def refs_in_html(doc, base):
    out = set()
    for x in ATTR.finditer(doc):
        if x.group(1).strip().lower().startswith(("src", "poster", "data-")):
            if not _skip(x.group(3)):
                out.add(urljoin(base, htmlmod.unescape(x.group(3).strip())))
    for x in SRCSET.finditer(doc):
        for part in x.group(3).split(","):
            bits = part.strip().split()
            if bits and not _skip(bits[0]):
                out.add(urljoin(base, htmlmod.unescape(bits[0])))
    for x in re.finditer(r"""<link\b[^>]*>""", doc, re.I):
        tag = x.group(0)
        if re.search(r"""rel\s*=\s*["'][^"']*(stylesheet|icon|apple-touch|manifest|preload)""", tag, re.I):
            h = re.search(r"""href\s*=\s*(["'])(.*?)\1""", tag, re.I)
            if h and not _skip(h.group(2)):
                out.add(urljoin(base, htmlmod.unescape(h.group(2))))
    for css in STYLE_BLOCK.findall(doc):
        out |= refs_in_css(css[1], base)
    for st in STYLE_ATTR.findall(doc):
        out |= refs_in_css(st[2], base)
    return {u.split("#")[0] for u in out if u.startswith(("http://", "https://"))}


def refs_in_css(css, base):
    out = {urljoin(base, x.group(2)) for x in CSSURL.finditer(css) if not _skip(x.group(2))}
    out |= {urljoin(base, x.group(2)) for x in CSSIMPORT.finditer(css) if not _skip(x.group(2))}
    return {u.split("#")[0] for u in out if u.startswith(("http://", "https://"))}


def _visible_words(doc):
    doc = re.sub(r"<(script|style|noscript|template)\b.*?</\1>", " ", doc, flags=re.I | re.S)
    return len(re.sub(r"<[^>]+>", " ", doc).split())


# ---------- COPY ----------
def copy_site(start, out_dir, max_pages, say, stopped, honour_robots=True):
    """Save a working copy of the site into out_dir. Returns a summary."""
    start = normalise_url(start)
    host = urlparse(start).hostname
    if not public_host(host):
        raise SiteError("That address is on this PC or the home network. I only copy websites out on the internet.")
    key = host_key(host)
    rp, maps = siteread.robots_for(start)
    from_map = [u for u in (clean_link(x, start, key) for x in siteread.sitemap_urls(start, maps, limit=MAX_PAGES)) if u]
    m = Mapper(key)
    pages = []                    # (url, local, html, snapshot)
    store = {}                    # asset url -> (ctype, bytes)
    total = [0]
    queue, queued = [(start, 0, True)], {_same(start)}
    out = Path(out_dir)
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise SiteError("The part that opens web pages is not installed yet. Tell me \"update yourself\", or run \"1 - Setup.bat\" again.")

    def keep(url, ctype, body):
        if url in store or len(body) > MAX_FILE or total[0] + len(body) > MAX_TOTAL or TRACKERS.search(url):
            return
        store[url] = (ctype, body)
        total[0] += len(body)

    with sync_playwright() as pw:
        browser, _ = siteread._launch(pw, False)
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, ignore_https_errors=True)
            page = ctx.new_page()
            while queue and len(pages) < max_pages and not stopped():
                queue.sort(key=lambda t: (not t[2], t[1]))
                url, depth, _ = queue.pop(0)
                if honour_robots and not rp.can_fetch("Trillion-SiteReader", url):
                    continue
                say(f"Copying page {len(pages) + 1} of up to {max_pages}: {urlparse(url).path or '/'}")
                seen_resp = []
                handler = lambda r: seen_resp.append(r)
                page.on("response", handler)
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=siteread.PAGE_TIMEOUT)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    for _ in range(6):
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(200)
                    page.wait_for_timeout(500)
                    raw = resp.text() if resp else ""
                    rendered = page.content()
                    final = page.url
                    links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                except Exception as e:
                    if os.environ.get("TRILLION_DEBUG"):
                        print("copy page failed:", url, e)
                    page.remove_listener("response", handler)
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = ctx.new_page()
                    if not pages and url.startswith("https://") and not any(_same("http://" + url[8:]) == _same(q[0]) for q in queue):
                        queue.insert(0, ("http://" + url[8:], depth, True))
                    continue
                page.remove_listener("response", handler)
                for r in seen_resp:
                    try:
                        if r.request.resource_type in ASSET_TYPES and r.status == 200 and r.url.startswith("http"):
                            keep(r.url.split("#")[0], r.headers.get("content-type", ""), r.body())
                    except Exception:
                        pass
                if host_key(urlparse(final).hostname) != key or (resp and resp.status >= 400 and pages):
                    continue
                if any(_same(final) == _same(p[0]) for p in pages):
                    continue
                snapshot = _visible_words(raw) < 0.3 * _visible_words(rendered) and _visible_words(rendered) > 60
                doc = rendered if snapshot or not raw.strip() else raw
                local = local_path(final, key, "page")
                m.pages[_same(final)] = local
                m.pages.setdefault(_same(url), local)
                pages.append((final, local, doc, snapshot))
                for c in links:
                    c = clean_link(c, final, key)
                    if not c or siteread.SKIP_EXT.search(urlparse(c).path) or _same(c) in queued:
                        continue
                    queued.add(_same(c))
                    queue.append((c, depth + 1, depth == 0))
                if len(pages) == 1:
                    for c in from_map:
                        if _same(c) not in queued and not siteread.SKIP_EXT.search(urlparse(c).path):
                            queued.add(_same(c))
                            queue.append((c, 2, False))
                time.sleep(siteread.PAUSE / 2)
        finally:
            browser.close()
    if not pages:
        raise SiteError(f"I could not open {start}. Check the address, or whether the site is online.")

    # what the pages and stylesheets point at that the browser did not load (other picture sizes, lazy pictures, fonts)
    say("Fetching the remaining pictures, fonts and styles")
    want = set()
    for url, local, doc, snap in pages:
        want |= refs_in_html(doc, url)
    client = httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    try:
        for rnd in range(3):
            for u, (ct, body) in list(store.items()):
                if "css" in ct or u.lower().split("?")[0].endswith(".css"):
                    want |= refs_in_css(body.decode("utf-8", "replace"), u)
            todo = [u for u in want if u not in store and not TRACKERS.search(u) and not m.page(u)
                    and (host_key(urlparse(u).hostname) == key or rnd < 2)]
            if not todo:
                break
            for u in todo[:800]:
                if stopped() or total[0] > MAX_TOTAL:
                    break
                if siteread.ALLOW_LOCAL is False and not public_host(urlparse(u).hostname or ""):
                    continue
                try:
                    r = client.get(u)
                    if r.status_code == 200 and len(r.content) <= MAX_FILE:
                        keep(u, r.headers.get("content-type", ""), r.content)
                except Exception:
                    pass
                want.discard(u)
    finally:
        client.close()

    # place every asset, then write everything with the links changed
    for u, (ct, body) in store.items():
        lp = local_path(u, key, "asset", ct)
        while lp in m.pages.values():
            lp = "_files/" + lp
        m.assets[u] = lp
    say("Writing the files")
    site = out / "site"
    site.mkdir(parents=True, exist_ok=True)
    nfiles = 0
    for u, (ct, body) in store.items():
        lp = m.assets[u]
        dest = site / lp
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "css" in ct or lp.endswith(".css"):
            body = rewrite_css(body.decode("utf-8", "replace"), u, lp, m).encode("utf-8")
        dest.write_bytes(body)
        nfiles += 1
    snaps = 0
    for url, local, doc, snap in pages:
        dest = site / local
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rewrite_html(doc, url, local, m, snapshot=snap), encoding="utf-8")
        nfiles += 1
        snaps += snap
    home = pages[0][1]
    if home != "index.html" and not (site / "index.html").exists():
        (site / "index.html").write_text(f'<!doctype html><meta http-equiv="refresh" content="0; url={home}"><a href="{home}">Open the site</a>',
                                         encoding="utf-8")
    return {"pages": len(pages), "files": nfiles, "bytes": sum(f.stat().st_size for f in site.rglob("*") if f.is_file()),
            "snapshots": snaps, "home": "index.html", "list": [[u, lp] for u, lp, _, _ in pages][:400]}


# ---------- REBUILD: measure the look ----------
DESIGN_JS = r"""
() => {
  const hex = c => { const m = (c || '').match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/); if (!m) return null;
    if (m[4] !== undefined && +m[4] < 0.5) return null; return '#' + [m[1], m[2], m[3]].map(x => (+x | 0).toString(16).padStart(2, '0')).join(''); };
  const cs = e => getComputedStyle(e);
  const vis = e => { const r = e.getBoundingClientRect(); const s = cs(e); return r.width > 30 && r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden'; };
  const count = {}; const bump = (k, w) => { if (k) count[k] = (count[k] || 0) + (w || 1); };
  const btns = [...document.querySelectorAll('button, a, input[type=submit]')].filter(vis).filter(b => {
    const s = cs(b); return hex(s.backgroundColor) && (parseFloat(s.paddingLeft) >= 8) && b.getBoundingClientRect().height < 90; });
  const btnBg = {}; btns.forEach(b => { const k = hex(cs(b).backgroundColor); btnBg[k] = (btnBg[k] || 0) + 1; });
  const pick = o => Object.entries(o).sort((a, b) => b[1] - a[1]).map(x => x[0]);
  const body = document.body, bs = cs(body);
  const h1 = document.querySelector('h1'), h2 = document.querySelector('h2'), p = document.querySelector('main p, p');
  const firstBtn = btns.find(b => hex(cs(b).backgroundColor) === pick(btnBg)[0]);
  const header = document.querySelector('header, nav, [role=banner]');
  const footer = document.querySelector('footer, [role=contentinfo]');
  let bg = hex(bs.backgroundColor) || hex(cs(document.documentElement).backgroundColor) || '#ffffff';
  // the sections: the tall blocks of the page, top to bottom, wherever they sit (inside main or beside it)
  const skip = e => e.matches('header,nav,footer,script,style,noscript,[role=banner],[role=navigation],[role=contentinfo]');
  let blocks = [];
  const walk = (el, depth) => {
    for (const k of [...el.children].filter(vis)) {
      if (skip(k)) continue;
      const tall = k.getBoundingClientRect().height > 120;
      const kids = [...k.children].filter(vis).filter(x => !skip(x));
      const wrapper = (k.matches('main,[role=main]') || (kids.length > 1 && kids.filter(x => x.getBoundingClientRect().height > 120).length > 1 &&
                       k.getBoundingClientRect().width >= document.documentElement.clientWidth * 0.9)) && depth < 3;
      if (wrapper) walk(k, depth + 1);
      else if (tall) blocks.push(k);
      else if (kids.length === 1 && depth < 3) walk(k, depth + 1);
    }
  };
  walk(body, 0);
  if (!blocks.length) blocks = [document.querySelector('main') || body];
  const secs = blocks.slice(0, 14).map(e => {
    const r = e.getBoundingClientRect(), s = cs(e);
    const heads = e.querySelectorAll('h1,h2,h3,h4');
    const imgs = [...e.querySelectorAll('img, picture, video, svg')].filter(vis);
    // repeated items: the largest group of siblings with about the same width
    let cols = 1, items = 0;
    e.querySelectorAll('*').forEach(el => { const kids = [...el.children].filter(vis); if (kids.length < 2) return;
      const ws = kids.map(k => Math.round(k.getBoundingClientRect().width / 20));
      const tops = kids.map(k => Math.round(k.getBoundingClientRect().top / 10));
      const same = ws.filter(w => Math.abs(w - ws[0]) <= 1).length;
      if (same === kids.length && kids[0].getBoundingClientRect().width < r.width * 0.6 && kids.length > items) {
        items = kids.length; cols = tops.filter(t => t === tops[0]).length; } });
    const txt = (e.innerText || '').trim();
    const bgc = hex(s.backgroundColor);
    const bgi = s.backgroundImage && s.backgroundImage !== 'none';
    const firstImg = imgs[0] ? imgs[0].getBoundingClientRect() : null;
    let kind = 'text';
    const lower = txt.toLowerCase();
    if (e.querySelector('form input, form textarea')) kind = 'contact';
    else if (/(r|\$|€|£)\s?\d/.test(txt) && items >= 2 && /\/\s?(mo|month|year)|per (month|year|week)|monthly|annually|\bplans?\b|\bpackages?\b/.test(lower)) kind = 'pricing';
    else if (e.querySelector('blockquote, [class*=testimon], [class*=review]') || /★|testimonial|what (our|people) (say|clients)/.test(lower)) kind = 'testimonials';
    else if (e.querySelector('details, [class*=faq], [class*=accordion]') || /frequently asked|faq/.test(lower)) kind = 'faq';
    else if (imgs.length >= 6 && txt.length < 400) kind = 'gallery';
    else if (items >= 3 && txt.length > 40) kind = (txt.match(/\d+[+%k]?/g) || []).length >= items && txt.length < 300 ? 'stats' : 'features';
    else if (imgs.length >= 1 && firstImg && firstImg.width < r.width * 0.65 && txt.length > 60) kind = 'split';
    else if (txt.length < 260 && e.querySelector('a, button') && (bgc && bgc !== bg)) kind = 'cta';
    return { kind, cols: Math.min(cols || 1, 4), items: Math.min(items, 8), height: Math.round(r.height), bg: bgc, image_bg: !!bgi,
             color: hex(s.color), align: s.textAlign === 'center' || (heads[0] && cs(heads[0]).textAlign === 'center') ? 'center' : 'left',
             image_side: firstImg && firstImg.left > r.left + r.width / 3 ? 'right' : 'left', images: imgs.length, words: txt.split(/\s+/).length };
  });
  if (secs.length) { const s0 = secs[0]; if (s0.kind !== 'contact' && (blocks[0].querySelector('h1') || s0.height > 380)) s0.kind = 'hero'; }
  const radius = firstBtn ? cs(firstBtn).borderRadius : '6px';
  const card = document.querySelector('[class*=card], article');
  const shown = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && cs(e).visibility !== 'hidden'; };
  const navLinks = header ? [...header.querySelectorAll('a')].filter(shown).length : 0;
  return {
    tokens: { bg, text: hex(bs.color) || '#222222', heading_font: h1 ? cs(h1).fontFamily : bs.fontFamily, body_font: bs.fontFamily,
      primary: pick(btnBg)[0] || hex(cs(document.querySelector('a') || body).color) || '#2563eb',
      primary_text: firstBtn ? hex(cs(firstBtn).color) || '#ffffff' : '#ffffff', second: pick(btnBg)[1] || null,
      radius: parseInt(radius) || 0, card_radius: card ? parseInt(cs(card).borderRadius) || 0 : 8,
      h1: h1 ? parseInt(cs(h1).fontSize) : 48, h2: h2 ? parseInt(cs(h2).fontSize) : 32, base: p ? parseInt(cs(p).fontSize) : 16,
      heading_weight: h1 ? cs(h1).fontWeight : '700', heading_case: h1 ? cs(h1).textTransform : 'none',
      max_width: Math.min(1400, Math.max(...[...document.querySelectorAll('main *, body > *')].slice(0, 400).filter(vis)
                  .map(e => parseInt(cs(e).maxWidth) || 0).filter(x => x > 600 && x < 2000), 1140)),
      header_bg: header ? hex(cs(header).backgroundColor) : null, header_text: header ? hex(cs(header.querySelector('a') || header).color) : null,
      footer_bg: footer ? hex(cs(footer).backgroundColor) : null, footer_text: footer ? hex(cs(footer).color) : null,
      header_sticky: header ? ['sticky', 'fixed'].includes(cs(header).position) : false, nav_links: navLinks,
      header_center: header && h1 ? false : false },
    sections: secs,
    menu: header ? [...header.querySelectorAll('a[href]')].filter(shown).map(a => a.href).slice(0, 12) : [],
  };
}
"""


def measure(start, pages_wanted, say, stopped):
    """The look of the home page and a few menu pages. No text or pictures are kept."""
    start = normalise_url(start)
    host = urlparse(start).hostname
    if not public_host(host):
        raise SiteError("That address is on this PC or the home network. I only look at websites out on the internet.")
    key = host_key(host)
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as pw:
        browser, _ = siteread._launch(pw, False)
        try:
            page = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, ignore_https_errors=True).new_page()
            todo, done = [start], set()
            while todo and len(out) < pages_wanted and not stopped():
                url = todo.pop(0)
                if _same(url) in done:
                    continue
                done.add(_same(url))
                say(f"Measuring the layout of page {len(out) + 1}: {urlparse(url).path or '/'}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=siteread.PAGE_TIMEOUT)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    for _ in range(5):
                        page.mouse.wheel(0, 1500)
                        page.wait_for_timeout(150)
                    page.evaluate("window.scrollTo(0,0)")
                    d = page.evaluate(DESIGN_JS)
                except Exception as e:
                    if os.environ.get("TRILLION_DEBUG"):
                        print("measure failed:", url, e)
                    if not out and url.startswith("https://"):
                        try:
                            page.close()
                        except Exception:
                            pass
                        page = browser.contexts[0].new_page()
                        done.discard(_same(url))             # the same page again, now over plain http
                        todo.insert(0, "http://" + url[8:])
                    continue
                d["url"] = page.url
                out.append(d)
                if len(out) == 1:
                    for h in d.get("menu", []):
                        c = clean_link(h, page.url, key)
                        if c and _same(c) not in done and not siteread.SKIP_EXT.search(urlparse(c).path):
                            todo.append(c)
        finally:
            browser.close()
    if not out:
        raise SiteError(f"I could not open {start}. Check the address, or whether the site is online.")
    return out


# ---------- REBUILD: new words ----------
SECTION_HINT = {
    "hero": "a big opening: heading (max 8 words), text (one sentence), button label",
    "features": "a heading and {n} items, each with a short title and one sentence",
    "stats": "a heading and {n} items, each title a number with a unit (realistic for this business, or round like 100%) and text a short label",
    "split": "a heading, two or three sentences of text, and a button label",
    "gallery": "a heading and a one-line text",
    "testimonials": "a heading only (the quotes stay as blanks for the owner to fill in with real customers)",
    "pricing": "a heading and {n} items, each title a package name and text one sentence on what it includes (prices are left for the owner)",
    "faq": "a heading and {n} items, each title a question a customer of this business asks and text a short answer",
    "cta": "a heading (max 8 words), one sentence, and a button label",
    "contact": "a heading and one sentence inviting people to get in touch",
    "text": "a heading and a paragraph of three or four sentences",
}


def _ask_json(user, prompt):
    try:
        msg = core.LLM(user, [{"role": "system", "content": "You write clear, honest website copy. Answer with JSON only."},
                              {"role": "user", "content": prompt}])
        t = (msg or {}).get("content") or ""
        return json.loads(t[t.index("{"): t.rindex("}") + 1])
    except Exception:
        return {}


def page_names(user, brief, n):
    j = _ask_json(user, f"A website for {brief['brand']}: {brief['about']}\nName its {n} pages, the first is the home page. "
                        'Return {"pages": [{"nav": "one or two words for the menu", "title": "the page title"}]}')
    pages = [p for p in (j.get("pages") or []) if isinstance(p, dict) and p.get("nav")][:n]
    defaults = [("Home", brief["brand"]), ("About", "About us"), ("Services", "What we do"), ("Gallery", "Gallery"),
                ("FAQ", "Questions"), ("Contact", "Contact us")]
    while len(pages) < n:
        d = defaults[len(pages)] if len(pages) < len(defaults) else (f"Page {len(pages) + 1}", f"Page {len(pages) + 1}")
        pages.append({"nav": d[0], "title": d[1]})
    pages[0]["nav"] = "Home"
    return [{"nav": str(p["nav"])[:22], "title": str(p.get("title") or p["nav"])[:70]} for p in pages]


def page_words(user, brief, name, sections):
    plan = "\n".join(f"{i + 1}. {s['kind']}: " + SECTION_HINT.get(s["kind"], SECTION_HINT["text"]).format(n=max(2, min(s["items"] or 3, 6)))
                     for i, s in enumerate(sections))
    j = _ask_json(user, (
        f"Business: {brief['brand']}\nWhat it does: {brief['about']}\n"
        + (f"Contact: {brief['contact']}\n" if brief.get("contact") else "")
        + f"Page: {name['title']}\nWrite the words for these sections of this page, in order:\n{plan}\n"
          'Return {"sections": [{"heading": "", "text": "", "button": "", "items": [{"title": "", "text": ""}]}]} with one '
          "entry per section. Only claim what the description supports; no made-up awards, reviews or customer names. "
          "South African English."))
    got = [s for s in (j.get("sections") or []) if isinstance(s, dict)]
    out = []
    for i, s in enumerate(sections):
        g = got[i] if i < len(got) else {}
        n = max(2, min(s["items"] or 3, 6))
        items = [it for it in (g.get("items") or []) if isinstance(it, dict) and it.get("title")][:n]
        out.append({"heading": str(g.get("heading") or fallback_heading(s["kind"], brief))[:90],
                    "text": str(g.get("text") or fallback_text(s["kind"], brief))[:600],
                    "button": str(g.get("button") or "Get in touch")[:30],
                    "items": [{"title": str(it["title"])[:60], "text": str(it.get("text") or "")[:220]} for it in items]
                             or [{"title": f"Item {k + 1}", "text": "Describe this here."} for k in range(n)]})
    return out


def fallback_heading(kind, b):
    return {"hero": b["brand"], "features": "What we offer", "stats": "In numbers", "split": f"About {b['brand']}",
            "gallery": "Gallery", "testimonials": "What our customers say", "pricing": "Packages", "faq": "Questions",
            "cta": "Ready when you are", "contact": "Get in touch"}.get(kind, b["brand"])


def fallback_text(kind, b):
    return b["about"] if kind in ("hero", "split", "text", "cta") else ""


# ---------- REBUILD: the new site's own code ----------
GOOGLE_FONTS = {"inter", "roboto", "open sans", "lato", "montserrat", "poppins", "raleway", "nunito", "playfair display", "merriweather",
                "oswald", "source sans pro", "source sans 3", "work sans", "dm sans", "rubik", "manrope", "barlow", "mulish", "karla",
                "libre baskerville", "lora", "pt sans", "pt serif", "noto sans", "noto serif", "quicksand", "josefin sans",
                "archivo", "space grotesk", "outfit", "plus jakarta sans", "figtree", "cormorant garamond", "bebas neue",
                "anton", "fira sans", "ibm plex sans", "ubuntu", "heebo", "kanit", "jost", "urbanist", "sora", "lexend"}


def font_choice(stack, serif=False):
    names = [x.strip().strip("'\"") for x in (stack or "").split(",")]
    for nme in names:
        if nme.lower() in GOOGLE_FONTS:
            return nme, True
    return ("Lora" if serif or any("serif" == x.lower() for x in names[-1:]) and "sans" not in (stack or "").lower() else "Inter"), True


def lum(h):
    h = (h or "#ffffff").lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return 255
    return 0.299 * r + 0.587 * g + 0.114 * b


def on(bgc):
    return "#111111" if lum(bgc) > 150 else "#ffffff"


def mix(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    try:
        return "#" + "".join(f"{round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t):02x}" for i in (0, 2, 4))
    except ValueError:
        return "#" + a


def placeholder_svg(w, h, c1, c2, label, plain=False):
    if plain:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"><defs>'
                f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/>'
                f'</linearGradient></defs><rect width="{w}" height="{h}" fill="url(#g)"/>'
                f'<circle cx="{w * .82}" cy="{h * .2}" r="{h * .45}" fill="#ffffff" fill-opacity=".06"/>'
                f'<circle cx="{w * .95}" cy="{h * .9}" r="{h * .35}" fill="#ffffff" fill-opacity=".05"/></svg>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/>'
            f'<stop offset="1" stop-color="{c2}"/></linearGradient></defs><rect width="{w}" height="{h}" fill="url(#g)"/>'
            f'<g fill="none" stroke="#ffffff" stroke-opacity=".55" stroke-width="{max(2, w // 300)}">'
            f'<rect x="{w / 2 - w / 12}" y="{h / 2 - h / 9}" width="{w / 6}" height="{h / 4.5}" rx="{w / 80}"/>'
            f'<circle cx="{w / 2 - w / 30}" cy="{h / 2 - h / 30}" r="{w / 60}"/>'
            f'<path d="M{w / 2 - w / 12} {h / 2 + h / 12} l{w / 24} -{h / 18} l{w / 30} {h / 30} l{w / 40} -{h / 40} l{w / 30} {h / 26}"/></g>'
            f'<text x="50%" y="{h / 2 + h / 6}" text-anchor="middle" font-family="Arial" font-size="{max(14, w // 40)}" '
            f'fill="#ffffff" fill-opacity=".85">{htmlmod.escape(label)}</text></svg>')


def css_for(t, fonts):
    hf, bf = fonts
    bg, text, pri = t["bg"], t["text"], t["primary"]
    soft = mix(bg, text, 0.06)
    line = mix(bg, text, 0.14)
    muted = mix(text, bg, 0.35)
    hbg = t.get("header_bg") or bg
    fbg = t.get("footer_bg") or mix(bg, text, 0.9 if lum(bg) > 150 else 0.15)
    upper = "uppercase" if t.get("heading_case") == "uppercase" else "none"
    return f""":root{{--bg:{bg};--text:{text};--muted:{muted};--soft:{soft};--line:{line};--pri:{pri};--on-pri:{t.get('primary_text') or on(pri)};
--hbg:{hbg};--htext:{t.get('header_text') or on(hbg)};--fbg:{fbg};--ftext:{t.get('footer_text') or on(fbg)};--r:{min(t.get('radius', 6), 40)}px;
--cr:{min(t.get('card_radius', 8), 32)}px;--max:{t.get('max_width', 1140)}px;--h1:{max(32, min(t.get('h1', 48), 80))}px;--h2:{max(24, min(t.get('h2', 32), 52))}px;
--base:{max(15, min(t.get('base', 16), 20))}px;--hf:'{hf}',system-ui,sans-serif;--bf:'{bf}',system-ui,sans-serif}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font:var(--base)/1.65 var(--bf)}}
img{{max-width:100%;display:block}}a{{color:inherit}}
h1,h2,h3{{font-family:var(--hf);font-weight:{t.get('heading_weight') or 700};line-height:1.15;margin:0 0 .5em;text-transform:{upper}}}
h1{{font-size:var(--h1)}}h2{{font-size:var(--h2)}}h3{{font-size:1.2em}}p{{margin:0 0 1em}}
.wrap{{max-width:var(--max);margin:0 auto;padding:0 24px}}
.btn{{display:inline-block;background:var(--pri);color:var(--on-pri);padding:.85em 1.6em;border-radius:var(--r);text-decoration:none;font-weight:600;border:0;cursor:pointer;font:600 1em var(--bf)}}
.btn:hover{{filter:brightness(1.08)}}
header.site{{background:var(--hbg);color:var(--htext);{'position:sticky;top:0;z-index:20;' if t.get('header_sticky') else ''}border-bottom:1px solid var(--line)}}
header.site .wrap{{display:flex;align-items:center;justify-content:space-between;min-height:72px;gap:20px}}
.logo{{font:700 1.35em var(--hf);text-decoration:none}}
nav.menu{{display:flex;gap:26px;align-items:center}}nav.menu a{{text-decoration:none;opacity:.85}}nav.menu a:hover,nav.menu a.on{{opacity:1;text-decoration:underline;text-underline-offset:6px}}
.burger{{display:none;background:none;border:0;color:inherit;font-size:28px;cursor:pointer}}
section{{padding:88px 0}}section.alt{{background:var(--soft)}}section.dark{{background:var(--sbg);color:var(--stext)}}
.center{{text-align:center}}.center .lead{{margin-left:auto;margin-right:auto}}
.lead{{font-size:1.15em;color:var(--muted);max-width:640px}}section.dark .lead{{color:inherit;opacity:.85}}
.hero{{padding:120px 0;position:relative;overflow:hidden}}.hero.img{{color:#fff;background:#111 center/cover no-repeat}}
.hero.img::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.65),rgba(0,0,0,.25))}}
.hero .wrap{{position:relative}}.hero.img .lead{{color:#fff;opacity:.9}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:56px;align-items:center}}.split.flip .media{{order:2}}
.media img{{border-radius:var(--cr);width:100%;aspect-ratio:4/3;object-fit:cover}}
.grid{{display:grid;gap:24px;margin-top:36px}}.c2{{grid-template-columns:repeat(2,1fr)}}.c3{{grid-template-columns:repeat(3,1fr)}}.c4{{grid-template-columns:repeat(4,1fr)}}
.card{{background:var(--bg);border:1px solid var(--line);border-radius:var(--cr);padding:28px}}section.alt .card{{background:var(--bg)}}
.card .ic{{width:44px;height:44px;border-radius:calc(var(--r) + 4px);background:var(--pri);opacity:.9;margin-bottom:16px}}
.stat b{{display:block;font:700 2.6em var(--hf);color:var(--pri)}}
.gallery img{{border-radius:var(--cr);aspect-ratio:1;object-fit:cover;width:100%}}
.quote{{font-size:1.05em}}.quote .who{{margin-top:14px;color:var(--muted);font-size:.9em}}
.price{{font:700 2em var(--hf);margin:10px 0}}
details{{border-bottom:1px solid var(--line);padding:18px 0}}summary{{cursor:pointer;font-weight:600;font-size:1.05em}}details p{{margin:12px 0 0;color:var(--muted)}}
.cta{{background:var(--pri);color:var(--on-pri)}}.cta .btn{{background:var(--on-pri);color:var(--pri)}}.cta .lead{{color:inherit;opacity:.9}}
form.contact{{display:grid;gap:14px;max-width:560px}}form.contact input,form.contact textarea{{font:inherit;padding:14px;border:1px solid var(--line);border-radius:var(--r);background:var(--bg);color:var(--text)}}
footer.site{{background:var(--fbg);color:var(--ftext);padding:56px 0 30px}}footer.site .cols{{display:flex;justify-content:space-between;gap:30px;flex-wrap:wrap}}
footer.site a{{text-decoration:none;opacity:.85;display:block;margin:6px 0}}footer.site small{{display:block;margin-top:36px;opacity:.6}}
@media(max-width:860px){{.c3,.c4{{grid-template-columns:repeat(2,1fr)}}.split{{grid-template-columns:1fr}}.split.flip .media{{order:0}}
section{{padding:64px 0}}.hero{{padding:84px 0}}h1{{font-size:calc(var(--h1)*.72)}}h2{{font-size:calc(var(--h2)*.8)}}
.burger{{display:block}}nav.menu{{display:none;position:absolute;left:0;right:0;top:72px;background:var(--hbg);flex-direction:column;padding:20px;gap:16px;border-bottom:1px solid var(--line)}}
nav.menu.open{{display:flex}}header.site .wrap{{position:relative}}}}
@media(max-width:560px){{.c2,.c3,.c4{{grid-template-columns:1fr}}}}
"""


JS = """document.addEventListener('click',e=>{const b=e.target.closest('.burger');if(b){document.querySelector('nav.menu').classList.toggle('open');}});
document.querySelectorAll('form.contact').forEach(f=>f.addEventListener('submit',e=>{if(f.getAttribute('action')==='#'){e.preventDefault();
f.innerHTML='<p><b>Thank you.</b> This form still needs to be connected; see README.txt.</p>';}}));"""


def render_section(s, w, i, t, img, brief, here, cta="#contact"):
    """One section of a new page, in the shape measured on the other site."""
    e = htmlmod.escape
    kind = s["kind"]
    alt = " alt" if (i % 2 == 1 and kind not in ("hero", "cta")) else ""
    dark = ""
    if s.get("bg") and s["bg"] != t["bg"] and kind not in ("hero", "cta") and abs(lum(s["bg"]) - lum(t["bg"])) > 80:
        dark = f' dark" style="--sbg:{s["bg"]};--stext:{on(s["bg"])}'
        alt = ""
    center = " center" if s.get("align") == "center" else ""
    head = f'<h2>{e(w["heading"])}</h2>' + (f'<p class="lead">{e(w["text"])}</p>' if w["text"] else "")
    cols = max(2, min(s.get("cols") or 3, 4))
    items = w["items"]
    if kind == "hero":
        hero_img = img("hero", 1600, 900)
        if s.get("image_bg"):
            return (f'<section class="hero img{center}" style="background-image:url(\'{rel(hero_img, here)}\')"><div class="wrap">'
                    f'<h1>{e(w["heading"])}</h1><p class="lead">{e(w["text"])}</p><a class="btn" href="{cta}">{e(w["button"])}</a></div></section>')
        if s.get("images"):
            flip = " flip" if s.get("image_side") == "left" else ""
            return (f'<section class="hero"><div class="wrap split{flip}"><div><h1>{e(w["heading"])}</h1><p class="lead">{e(w["text"])}</p>'
                    f'<a class="btn" href="{cta}">{e(w["button"])}</a></div><div class="media"><img src="{rel(hero_img, here)}" alt="{e(brief["brand"])}"></div></div></section>')
        return (f'<section class="hero{center}"><div class="wrap"><h1>{e(w["heading"])}</h1><p class="lead">{e(w["text"])}</p>'
                f'<a class="btn" href="{cta}">{e(w["button"])}</a></div></section>')
    if kind in ("features", "stats"):
        cards = "".join((f'<div class="card stat"><b>{e(it["title"])}</b>{e(it["text"])}</div>' if kind == "stats" else
                         f'<div class="card"><div class="ic"></div><h3>{e(it["title"])}</h3><p>{e(it["text"])}</p></div>') for it in items)
        return f'<section class="{alt}{dark}{center}"><div class="wrap">{head}<div class="grid c{min(cols, len(items)) if len(items) > 1 else 2}">{cards}</div></div></section>'
    if kind == "split":
        flip = " flip" if s.get("image_side") == "right" else ""
        pic = img(f"photo{i}", 1200, 900)
        return (f'<section class="{alt}{dark}"><div class="wrap split{flip}"><div class="media"><img src="{rel(pic, here)}" alt=""></div><div>'
                f'<h2>{e(w["heading"])}</h2><p>{e(w["text"])}</p><a class="btn" href="{cta}">{e(w["button"])}</a></div></div></section>')
    if kind == "gallery":
        n = max(4, min(s.get("images") or 6, 8))
        pics = "".join(f'<img src="{rel(img(f"gallery{k + 1}", 900, 900), here)}" alt="">' for k in range(n))
        return f'<section class="{alt}{dark}{center}"><div class="wrap">{head}<div class="grid gallery c{4 if n >= 8 else 3}">{pics}</div></div></section>'
    if kind == "testimonials":
        n = max(2, min(s.get("items") or 3, 3))
        cards = "".join('<div class="card quote"><p>“Add a real quote from one of your customers here.”</p><div class="who">Customer name, town</div></div>'
                        for _ in range(n))
        return f'<section class="{alt}{dark}{center}"><div class="wrap"><h2>{e(w["heading"])}</h2><div class="grid c{n}">{cards}</div></div></section>'
    if kind == "pricing":
        cards = "".join(f'<div class="card"><h3>{e(it["title"])}</h3><div class="price">R ___</div><p>{e(it["text"])}</p>'
                        f'<a class="btn" href="{cta}">{e(w["button"])}</a></div>' for it in items[:4])
        return f'<section class="{alt}{dark}{center}"><div class="wrap">{head}<div class="grid c{min(len(items[:4]), 3) or 2}">{cards}</div></div></section>'
    if kind == "faq":
        qa = "".join(f'<details><summary>{e(it["title"])}</summary><p>{e(it["text"])}</p></details>' for it in items)
        return f'<section class="{alt}{dark}"><div class="wrap" style="max-width:820px">{head}{qa}</div></section>'
    if kind == "cta":
        return (f'<section class="cta center"><div class="wrap"><h2>{e(w["heading"])}</h2><p class="lead">{e(w["text"])}</p>'
                f'<a class="btn" href="{cta}">{e(w["button"])}</a></div></section>')
    if kind == "contact":
        info = "".join(f"<p>{e(x.strip())}</p>" for x in re.split(r"[;\n]", brief.get("contact") or "") if x.strip())
        return (f'<section id="contact" class="{alt}{dark}"><div class="wrap split"><div><h2>{e(w["heading"])}</h2><p class="lead">{e(w["text"])}</p>{info}</div>'
                f'<form class="contact" action="#" method="post"><input name="name" placeholder="Your name" required>'
                f'<input name="email" type="email" placeholder="Email address" required><textarea name="message" rows="5" placeholder="Message"></textarea>'
                f'<button class="btn" type="submit">Send</button></form></div></section>')
    return f'<section class="{alt}{dark}{center}"><div class="wrap" style="max-width:820px"><h2>{e(w["heading"])}</h2><p>{e(w["text"])}</p></div></section>'


def build_site(user, brief, designs, out_dir, say, stopped, use_my_colour=False, ai_pictures=False):
    site = Path(out_dir) / "site"
    if site.exists():
        shutil.rmtree(site)
    (site / "images").mkdir(parents=True)
    (site / "css").mkdir()
    (site / "js").mkdir()
    t = dict(designs[0]["tokens"])
    if use_my_colour:
        acc = str(core.settings(user).get("accent") or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", acc):
            t["primary"], t["primary_text"] = acc, on(acc)
    if abs(lum(t["text"]) - lum(t["bg"])) < 90:                       # unreadable pair measured: fall back to safe ones
        t["text"] = "#151515" if lum(t["bg"]) > 128 else "#f5f5f5"
    if abs(lum(t["primary"]) - lum(t["bg"])) < 25:
        t["primary"] = "#2563eb" if lum(t["bg"]) > 128 else "#ffb454"
    t["primary_text"] = on(t["primary"]) if abs(lum(t.get("primary_text") or on(t["primary"])) - lum(t["primary"])) < 90 else t["primary_text"]
    for bgk, fgk in (("header_bg", "header_text"), ("footer_bg", "footer_text")):
        b_ = t.get(bgk) or (t["bg"] if bgk == "header_bg" else mix(t["bg"], t["text"], 0.9 if lum(t["bg"]) > 150 else 0.15))
        t[bgk] = b_
        if not t.get(fgk) or abs(lum(t[fgk]) - lum(b_)) < 90:
            t[fgk] = on(b_)
    hf, _ = font_choice(t["heading_font"])
    bf, _ = font_choice(t["body_font"])
    n = len(designs)
    names = page_names(user, brief, n)
    has_contact = any(s["kind"] == "contact" for d in designs for s in d["sections"])
    if not has_contact:                         # every site needs a way to reach the business
        designs[-1]["sections"].append({"kind": "contact", "items": 0, "cols": 1, "align": "left"})
    brief["_has_contact"] = False
    paths = ["index.html"] + [f"{re.sub(r'[^a-z0-9]+', '-', nm['nav'].lower()).strip('-') or f'page{k}'}/index.html" for k, nm in enumerate(names[1:], 1)]
    seen_p = set()
    for k, p in enumerate(paths):
        while p in seen_p:
            p = p.replace("/index.html", "-2/index.html")
        paths[k] = p
        seen_p.add(p)
    contact_page = next((p for d, p in zip(designs, paths) if any(x["kind"] == "contact" for x in d["sections"])), paths[-1])
    made = {}
    ai_left = [4 if ai_pictures else 0]

    def img(name, w, h):
        rp = f"images/{name}.svg"
        if name in made:
            return made[name]
        if ai_left[0] > 0 and name in ("hero", "photo1", "photo2", "photo3", "gallery1"):
            import tools
            ai_left[0] -= 1
            r = tools.t_create_image(user, f"Professional website photograph for {brief['brand']}, {brief['about']}. "
                                           f"{'Wide banner, ' if name == 'hero' else ''}natural light, no text, no logos.",
                                     "wide" if name == "hero" else "square")
            if r.get("saved"):
                rp = f"images/{name}.png"
                shutil.copy(r["saved"], site / rp)
                made[name] = rp
                return rp
        (site / rp).write_text(placeholder_svg(w, h, mix(t["primary"], "#000000", .15), mix(t["primary"], t["bg"], .55),
                                               "Your photo", plain=(name == "hero")), encoding="utf-8")
        made[name] = rp
        return rp

    for k, (d, nm, here) in enumerate(zip(designs, names, paths)):
        if stopped():
            break
        say(f"Writing page {k + 1} of {n}: {nm['nav']}")
        secs = [s for s in d["sections"] if s["kind"] != "text" or s["words"] > 15][:9] or [{"kind": "hero", "items": 0, "cols": 1}]
        if k == 0 and secs[0]["kind"] != "hero":
            secs.insert(0, {"kind": "hero", "items": 0, "cols": 1, "align": "center"})
        if k == 0 and len(secs) < 4:                     # a thin home page: fill it out with the usual parts
            kinds = {x["kind"] for x in secs}
            for extra in ({"kind": "features", "items": 3, "cols": 3, "align": "center"},
                          {"kind": "split", "items": 0, "cols": 1, "image_side": "left", "images": 1},
                          {"kind": "cta", "items": 0, "cols": 1, "align": "center"}):
                if len(secs) >= 4:
                    break
                if extra["kind"] not in kinds:
                    secs.append(extra)
        if k > 0 and secs[0]["kind"] == "hero":
            secs[0] = dict(secs[0], image_bg=False, images=0)
        words = page_words(user, brief, nm, secs)
        cta = "#contact" if any(x["kind"] == "contact" for x in secs) else (rel(contact_page, here) + "#contact")
        body = "".join(render_section(s, w, i, t, img, brief, here, cta) for i, (s, w) in enumerate(zip(secs, words)))
        menu = "".join(f'<a href="{rel(p, here)}"{" class=\"on\"" if p == here else ""}>{htmlmod.escape(x["nav"])}</a>' for x, p in zip(names, paths))
        foot_links = "".join(f'<a href="{rel(p, here)}">{htmlmod.escape(x["nav"])}</a>' for x, p in zip(names, paths))
        contact = "".join(f"<div>{htmlmod.escape(x.strip())}</div>" for x in re.split(r"[;\n]", brief.get("contact") or "") if x.strip())
        fonts = "&".join(f"family={f.replace(' ', '+')}:wght@400;600;700" for f in dict.fromkeys([hf, bf]))
        doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{htmlmod.escape(nm['title'] if k else brief['brand'] + ' | ' + (brief['about'][:50]))}</title>
<meta name="description" content="{htmlmod.escape(brief['about'][:155])}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?{fonts}&display=swap">
<link rel="stylesheet" href="{rel('css/style.css', here)}"></head>
<body><header class="site"><div class="wrap"><a class="logo" href="{rel('index.html', here)}">{htmlmod.escape(brief['brand'])}</a>
<button class="burger" aria-label="Menu">&#9776;</button><nav class="menu">{menu}</nav></div></header>
<main>{body}</main>
<footer class="site"><div class="wrap"><div class="cols"><div><div class="logo">{htmlmod.escape(brief['brand'])}</div><p style="max-width:360px;opacity:.8">{htmlmod.escape(brief['about'][:200])}</p></div>
<div>{foot_links}</div><div>{contact}</div></div><small>&copy; {datetime.date.today().year} {htmlmod.escape(brief['brand'])}</small></div></footer>
<script src="{rel('js/site.js', here)}"></script></body></html>"""
        dest = site / here
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(doc, encoding="utf-8")
    (site / "css" / "style.css").write_text(css_for(t, (hf, bf)), encoding="utf-8")
    (site / "js" / "site.js").write_text(JS, encoding="utf-8")
    files = [f for f in site.rglob("*") if f.is_file()]
    return {"pages": len(paths), "files": len(files), "bytes": sum(f.stat().st_size for f in files), "home": "index.html",
            "list": [[nm["nav"], p] for nm, p in zip(names, paths)], "fonts": [hf, bf],
            "colours": {k: t[k] for k in ("bg", "text", "primary")},
            "pictures": sorted(p for p in made.values()), "ai_pictures": sum(1 for p in made.values() if p.endswith(".png"))}


# ---------- jobs ----------
def readme(mode, url, brief, res):
    when = datetime.datetime.now().strftime("%d %B %Y %H:%M")
    if mode == "copy":
        return (f"Copy of {url}\nMade by Trillion on {when}.\n\n"
                f"{res['pages']} pages and {res['files']} files in the folder 'site'.\n\n"
                "TO LOOK AT IT: open site\\index.html in your browser.\n"
                "TO PUT IT ONLINE: upload everything inside the 'site' folder to your host (or upload the zip and unpack it there).\n\n"
                + (f"{res['snapshots']} page(s) were built by the site's JavaScript; they were saved as they looked in the browser, "
                   "with the scripts removed, so they open and can be edited, but buttons that needed the scripts will not work.\n\n" if res.get("snapshots") else "")
                + "Shop checkouts, logins, search and contact forms need the original website's server and will not work in the copy.\n"
                  "Pictures and files from other websites (fonts, libraries) are in the folder _ext.\n")
    pics = "\n".join(f"  {p}" for p in res.get("pictures", []))
    return (f"{brief['brand']} website\nMade by Trillion on {when}, in the layout and style of {url}.\n"
            f"The words were written for {brief['brand']}; none of the other site's text, pictures or code is in it.\n\n"
            "TO LOOK AT IT: open site\\index.html in your browser.\n"
            "TO PUT IT ONLINE: upload everything inside the 'site' folder to your host.\n\n"
            "BEFORE YOU PUBLISH\n"
            "1. Read every page and correct anything that is not exactly right for your business.\n"
            "2. Put your own photos in site\\images, using the same file names (or change the names in the pages):\n" + pics + "\n"
            "3. Fill in the customer quotes and prices marked with blanks, if those sections are there.\n"
            "4. The contact form needs a form service. Free options: Netlify Forms, Formspree or Web3Forms. Put the address\n"
            "   they give you in action=\"#\" in the contact form.\n\n"
            f"Fonts: {', '.join(res.get('fonts', []))} (Google Fonts, free to use).\n"
            "Colours are set at the top of site\\css\\style.css.\n")


def start(user, mode, url, brief=None, max_pages=DEFAULT_COPY_PAGES, permission=False, use_my_colour=False, ai_pictures=False):
    url = normalise_url(url)
    host = urlparse(url).hostname
    if not public_host(host):
        raise SiteError("That address is on this PC or the home network. I only work with websites out on the internet.")
    uid = user["id"]
    if mode not in ("copy", "rebuild"):
        raise SiteError("Choose copy or rebuild.")
    own = is_own(user, url)
    if mode == "copy" and not own and not permission:
        raise SiteError(f"{host_key(host)} is not one of your sites. I make full copies only of your own sites, or of a site whose owner "
                        "gave you permission (tick the box to say so). To build your own site in its style instead, choose Rebuild.")
    brief = dict(brief or {})
    if mode == "rebuild":
        st = core.settings(user)
        brief["brand"] = str(brief.get("brand") or st.get("brand") or "").strip()[:60]
        brief["about"] = str(brief.get("about") or "").strip()[:500]
        brief["contact"] = str(brief.get("contact") or "").strip()[:300]
        if not brief["brand"]:
            raise SiteError("Tell me the name of your business for the new site.")
        if len(brief["about"]) < 10:
            raise SiteError("Tell me in a sentence what your business does, so I can write the words for the new site.")
    if core.q("SELECT 1 FROM site_clones WHERE user_id=? AND status='running'", (uid,), one=True):
        raise SiteError("I am already making a site for you. Let that one finish, or press Stop on the Websites page.")
    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = DEFAULT_COPY_PAGES
    max_pages = max(1, min(max_pages, MAX_PAGES if mode == "copy" else REBUILD_PAGES))
    brief.update(max_pages=max_pages, own=own, permission=bool(permission), use_my_colour=bool(use_my_colour), ai_pictures=bool(ai_pictures))
    title = (f"Copy {host_key(host)}" if mode == "copy" else f"Build {brief['brand']} site in the style of {host_key(host)}")
    tid = core.add_task(uid, title)
    jid = core.x("INSERT INTO site_clones(user_id,url,domain,mode,status,created,brief,task_id,token,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (uid, url, host_key(host), mode, "running", time.time(), json.dumps(brief), tid, secrets.token_urlsafe(18),
                  "Starting the browser"))
    t = threading.Thread(target=_run, args=(dict(user), jid), daemon=True)
    with _LOCK:
        RUNNING[jid] = t
    t.start()
    return job(user, jid)


def _set(jid, **kw):
    core.x("UPDATE site_clones SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", tuple(kw.values()) + (jid,))


def _run(user, jid):
    j = core.q("SELECT * FROM site_clones WHERE id=?", (jid,), one=True)
    tid, brief = j["task_id"], json.loads(j["brief"])
    stopped = lambda: jid in STOP

    def say(text):
        _set(jid, detail=text[:200])
        core.set_task(tid, detail=text[:200])

    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
        name = j["domain"] if j["mode"] == "copy" else re.sub(r'[<>:"/\\|?*]+', "", brief["brand"]).strip() or "New site"
        folder = core.out_dir(user, "Trillion Websites") / ("Copies" if j["mode"] == "copy" else "New sites") / f"{name} {stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        _set(jid, folder=str(folder))
        if j["mode"] == "copy":
            res = copy_site(j["url"], folder, brief["max_pages"], say, stopped, honour_robots=not brief.get("own"))
        else:
            designs = measure(j["url"], brief["max_pages"], say, stopped)
            if stopped():
                raise SiteError("Stopped before the new site was written.")
            res = build_site(user, brief, designs, folder, say, stopped, brief.get("use_my_colour"), brief.get("ai_pictures"))
        (folder / "README.txt").write_text(readme(j["mode"], j["url"], brief, res), encoding="utf-8")
        say("Packing the zip")
        zpath = folder / f"{name} website.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in (folder / "site").rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(folder / "site").as_posix())
            z.write(folder / "README.txt", "README.txt")
        res["zip"] = str(zpath)
        status = "stopped" if stopped() else "done"
        what = (f"Copied {res['pages']} pages of {j['domain']} ({res['files']} files)" if j["mode"] == "copy"
                else f"Built a {res['pages']}-page site for {brief['brand']} in the style of {j['domain']}")
        _set(jid, status=status, finished=time.time(), pages=res["pages"], files=res["files"], bytes=res["bytes"],
             result=json.dumps(res), detail=what + ("; stopped early" if status == "stopped" else ""))
        core.set_task(tid, status="completed", detail=what)
        core.log(user["id"], what, "done")
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant",
               what + ". It is on the Websites page, with a preview and a zip ready to upload.", time.time()))
    except SiteError as e:
        _set(jid, status="failed", finished=time.time(), detail=str(e)[:300])
        core.set_task(tid, status="failed", detail=str(e)[:200])
    except Exception as e:
        _set(jid, status="failed", finished=time.time(), detail=f"Something went wrong: {type(e).__name__}: {e}"[:300])
        core.set_task(tid, status="failed", detail=f"{type(e).__name__}: {e}"[:200])
    finally:
        STOP.discard(jid)
        with _LOCK:
            RUNNING.pop(jid, None)


def stop(user, jid):
    if core.q("SELECT 1 FROM site_clones WHERE id=? AND user_id=? AND status='running'", (jid, user["id"]), one=True):
        STOP.add(jid)
        _set(jid, detail="Stopping")
        return True
    return False


def job(user, jid):
    r = core.q("SELECT * FROM site_clones WHERE id=? AND user_id=?", (jid, user["id"]), one=True)
    if not r:
        return None
    d = dict(r, brief=json.loads(r["brief"] or "{}"), result=json.loads(r["result"] or "{}"))
    d["preview"] = f"/clone/{r['token']}/" if r["status"] in ("done", "stopped") and d["result"] else ""
    return d


def jobs(user, limit=30):
    rows = core.q("SELECT id FROM site_clones WHERE user_id=? ORDER BY id DESC LIMIT ?", (user["id"], limit))
    return [job(user, r["id"]) for r in rows]


def forget(user, jid):
    core.x("DELETE FROM site_clones WHERE id=? AND user_id=? AND status!='running'", (jid, user["id"]))


def preview_file(token, path):
    """The file a preview link asks for, inside that clone's own site folder only."""
    r = core.q("SELECT folder FROM site_clones WHERE token=? AND status IN ('done','stopped')", (token,), one=True)
    if not r or not r["folder"]:
        return None
    root = (Path(r["folder"]) / "site").resolve()
    p = (root / unquote(path or "")).resolve()
    if p != root and root not in p.parents:
        return None
    if p.is_dir():
        p = p / "index.html"
    return p if p.is_file() else None


# ---------- chat ----------
CLONE_VERB = re.compile(r"\b(clone|copy|mirror|duplicate|back ?up|download|rip|replicate|rebuild|recreate)\b", re.I)
STYLE_WORDS = re.compile(r"\b(layout|design|style|look|template|theme|structure|feel|like)\b", re.I)
FOR_BRAND = re.compile(r"\bfor\s+(?:my\s+(?:business|company|shop|brand|site)\s+)?([A-Z][\w&'.-]*(?:\s+[A-Z][\w&'.-]*){0,3})")
PERMISSION = re.compile(r"\b(my (own )?(web)?site|i own|we own|permission|owner (said|agreed|gave))\b", re.I)


LIKE = re.compile(r"\b(make|build|create|design|do)\b[^.?!]{0,25}\b(web ?site|site|page)\b[^.?!]{0,15}\b(like|similar to|in the style of|based on)\b", re.I)


def request(text):
    m = siteread.URL_IN.search(text)
    if not m:
        return None
    verb = CLONE_VERB.search(text)
    like = LIKE.search(text)
    if not verb and not like:
        return None
    strong = re.search(r"\b(clone|mirror|rebuild|recreate|replicate|copy)\b", text, re.I)
    if not like and not strong and not re.search(r"\b(web ?site|site)\b", text, re.I) and not re.search(r"https?://", m.group(1)):
        return None
    rebuild = bool(like) or bool(STYLE_WORDS.search(text)) or bool(re.search(r"\b(rebuild|recreate)\b", text, re.I))
    pm = siteread.PAGES.search(text)
    fb = FOR_BRAND.search(text)
    return {"url": m.group(1), "mode": "rebuild" if rebuild else "copy", "permission": bool(PERMISSION.search(text)),
            "brand": fb.group(1).strip().rstrip(",.") if fb and rebuild else "", "about": text if rebuild else "",
            "max_pages": int(pm.group(1)) if pm else None}


def started_reply(j):
    if j["mode"] == "copy":
        return (f"I am copying {j['domain']} now, up to {j['brief']['max_pages']} pages with all their pictures, styles and fonts. "
                "When it is done you get the site folder, a zip to upload, and a preview on the Websites page.")
    return (f"I am measuring the layout and style of {j['domain']} and building a new site for {j['brief']['brand']} in that style, "
            f"{j['brief']['max_pages']} page{'s' if j['brief']['max_pages'] != 1 else ''}, with words written for your business. "
            "Follow it on the Websites page.")
