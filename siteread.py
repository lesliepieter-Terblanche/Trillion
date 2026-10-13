"""Read a whole website and make something from it.

Trillion opens a browser on this PC (Microsoft Edge, which every Windows PC has), goes to the site, follows its own
links page by page, reads each one, checks it, and then the brain writes an analysis. From that she makes a
presentation, a report and a spreadsheet in the login's own folder (sitedocs.py does the layout).

Polite on purpose: one page at a time with a short pause, the site's robots.txt is respected, and only pages of that
one site are opened. Pages behind a login, carts, checkouts and downloads are not opened. Addresses on this PC or the
home network are refused, so a login cannot use this to look inside the PC.
"""
import datetime
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import httpx

import core

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Trillion-SiteReader"
PAGE_CHOICES = (10, 25, 40, 80, 150)
DEFAULT_PAGES = 40
PAUSE = 0.6                       # seconds between pages, so a small site is never hammered
PAGE_TIMEOUT = 30000
TEXT_KEEP = 20000                 # characters of each page's words kept
NOTE_PAGES = 24                   # pages the brain reads one by one before the overall analysis
OUTPUTS = ("pptx", "docx", "xlsx")
SKIP_EXT = re.compile(r"\.(pdf|zip|rar|7z|exe|msi|dmg|apk|jpe?g|png|gif|webp|svg|ico|mp4|mov|avi|mp3|wav|docx?|xlsx?|pptx?|csv|xml|json|txt|css|js)$", re.I)
DOC_EXT = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)$", re.I)
SKIP_PATH = re.compile(r"/(wp-admin|wp-login|wp-json|login|log-in|signin|sign-in|logout|log-out|register|signup|sign-up|my-?account|account|"
                       r"cart|basket|checkout|order-received|feed|xmlrpc|cdn-cgi|admin)\b|[?&](add-to-cart|add_to_cart|replytocom|share|"
                       r"action|session|token|orderby|order|sort|sortby|filter_|filter|view|display|currency|lang)=", re.I)
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|_ga|ref$|ref_)", re.I)
ALLOW_LOCAL = False               # tests only: lets the reader open a site served on this machine
STOP = set()                      # job ids asked to stop
RUNNING = {}                      # job id -> thread
_LOCK = threading.Lock()

core.x("""CREATE TABLE IF NOT EXISTS site_reads(id INTEGER PRIMARY KEY, user_id INTEGER, url TEXT, domain TEXT, status TEXT,
          created REAL, finished REAL, pages INTEGER DEFAULT 0, detail TEXT DEFAULT '', focus TEXT DEFAULT '',
          outputs TEXT DEFAULT '[]', max_pages INTEGER DEFAULT 40, show INTEGER DEFAULT 0, task_id INTEGER,
          result TEXT DEFAULT '{}', folder TEXT DEFAULT '')""")
# a job that was running when Trillion last stopped did not finish
core.x("UPDATE site_reads SET status='stopped', detail='Trillion restarted while this was being read.' WHERE status='running'")


class SiteError(Exception):
    pass


# ---------- addresses ----------
def normalise_url(raw):
    raw = str(raw or "").strip().strip("<>\"'").rstrip(".,;)")
    if not raw:
        raise SiteError("Give me the website's address, for example tackletrail.co.za.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.hostname or "." not in u.hostname:
        raise SiteError(f"{raw} does not look like a website address.")
    return urlunparse((u.scheme, u.netloc.lower(), u.path or "/", "", u.query, ""))


def host_key(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def public_host(host):
    """True when every address the name points to is out on the internet, not this PC or the home network."""
    if ALLOW_LOCAL:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise SiteError(f"I could not find {host}. Check the spelling, or whether the site is online.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified \
                or ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    return True


def clean_link(href, base, key):
    """The address worth opening for this link, or None. Same site only, no fragments or tracking codes."""
    try:
        u = urlparse(urljoin(base, href))
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or host_key(u.hostname) != key:
        return None
    path = re.sub(r"/{2,}", "/", u.path or "/")
    q = urlencode(sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k)))
    out = urlunparse((u.scheme, u.netloc.lower(), path, "", q, ""))
    if SKIP_PATH.search(path + ("?" + q if q else "")):
        return None
    return out


def _same(a):
    """Two addresses for the same page: with or without www, http or https, a slash at the end."""
    u = urlparse(a)
    return host_key(u.hostname) + (u.path.rstrip("/") or "/") + ("?" + u.query if u.query else "")


# ---------- robots.txt and sitemap ----------
def robots_for(start):
    u = urlparse(start)
    rp = urllib.robotparser.RobotFileParser()
    sitemaps = []
    try:
        r = httpx.get(f"{u.scheme}://{u.netloc}/robots.txt", headers={"User-Agent": UA}, timeout=12, follow_redirects=True)
        if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
            lines = r.text.splitlines()
            rp.parse(lines)
            sitemaps = [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")]
        else:
            rp.parse([])
    except Exception:
        rp.parse([])
    return rp, sitemaps


def sitemap_urls(start, listed, limit=400):
    """Page addresses from the site's sitemap, if it has one. Sitemaps of sitemaps are followed one level."""
    u = urlparse(start)
    todo = list(dict.fromkeys(listed + [f"{u.scheme}://{u.netloc}/sitemap.xml", f"{u.scheme}://{u.netloc}/sitemap_index.xml"]))[:6]
    found, seen = [], set()
    while todo and len(found) < limit:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = httpx.get(sm, headers={"User-Agent": UA}, timeout=12, follow_redirects=True)
            if r.status_code != 200 or "<loc" not in r.text:
                continue
        except Exception:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        if "<sitemapindex" in r.text:
            todo += [x for x in locs if x not in seen][:8]
        else:
            found += locs
    return found[:limit]


# ---------- reading one page ----------
EXTRACT_JS = r"""
() => {
  const q = s => [...document.querySelectorAll(s)];
  const meta = n => (document.querySelector(`meta[name="${n}"],meta[property="${n}"]`) || {}).content || '';
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const links = q('a[href]').map(a => ({href: a.href, text: clean(a.innerText).slice(0, 80),
                 nav: !!a.closest('nav,header,[role=navigation]')}));
  const imgs = q('img');
  const root = document.querySelector('main,[role=main],article') || document.body;
  const copy = root.cloneNode(true);
  copy.querySelectorAll('script,style,noscript,svg,iframe,template' + (root === document.body ? ',nav,header,footer' : '')).forEach(e => e.remove());
  let text = (copy.innerText || copy.textContent || '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  const ld = q('script[type="application/ld+json"]').map(s => { try { const j = JSON.parse(s.textContent);
      const all = Array.isArray(j) ? j : (j['@graph'] || [j]); return all.map(x => x['@type']).flat(); } catch (e) { return []; } }).flat()
      .filter(Boolean).map(String);
  let load = 0;
  try { const n = performance.getEntriesByType('navigation')[0]; load = Math.round(n.loadEventEnd || n.domContentLoadedEventEnd || 0); } catch (e) {}
  return {
    title: clean(document.title), description: meta('description'), og_title: meta('og:title'), og_image: meta('og:image'),
    canonical: (document.querySelector('link[rel=canonical]') || {}).href || '', lang: document.documentElement.lang || '',
    viewport: !!document.querySelector('meta[name=viewport]'), robots: meta('robots'),
    h1: q('h1').map(e => clean(e.innerText)).filter(Boolean).slice(0, 5),
    headings: q('h2,h3').map(e => clean(e.innerText)).filter(Boolean).slice(0, 40),
    text, links,
    images: imgs.length, no_alt: imgs.filter(i => !(i.getAttribute('alt') || '').trim()).length,
    image_list: imgs.slice(0, 12).map(i => ({src: i.currentSrc || i.src, alt: clean(i.alt)})),
    forms: q('form').length, buttons: q('button,[role=button],input[type=submit]').map(e => clean(e.innerText || e.value)).filter(Boolean).slice(0, 15),
    structured: [...new Set(ld)].slice(0, 12), load_ms: load,
    socials: [...new Set(links.map(l => l.href).filter(h => /facebook\.com|instagram\.com|tiktok\.com|linkedin\.com|twitter\.com|x\.com\/|youtube\.com|wa\.me|pinterest\./i.test(h)))].slice(0, 12),
  };
}
"""
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+27|0)[\s-]?\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{4}|\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3}[\s-]?\d{3,4}")
PRICE = re.compile(r"(?:R|ZAR|\$|USD|€|£)\s?\d{1,3}(?:[ ,]\d{3})*(?:[.,]\d{2})?(?!\d)")


def _launch(pw, show):
    last = ""
    for channel in ("msedge", "chrome", None):
        for headless in ((False, True) if show else (True,)):
            try:
                kw = {"headless": headless}
                if channel:
                    kw["channel"] = channel
                b = pw.chromium.launch(**kw)
                return b, headless
            except Exception as e:
                last = str(e).splitlines()[0][:160]
    raise SiteError("I could not start a browser on the PC (" + last + "). Tell me \"update yourself\" or run Setup again.")


def crawl(start, max_pages=DEFAULT_PAGES, show=False, shots_dir=None, say=None, stopped=lambda: False):
    """Open the site and read up to max_pages of its pages. Returns (pages, info)."""
    say = say or (lambda t: None)
    start = normalise_url(start)
    host = urlparse(start).hostname
    if not public_host(host):
        raise SiteError("That address is on this PC or the home network. I only read websites out on the internet.")
    key = host_key(host)
    rp, maps = robots_for(start)
    say("Looking for the site's page list")
    from_map = [u for u in (clean_link(x, start, key) for x in sitemap_urls(start, maps)) if u]
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise SiteError("The part that opens web pages is not installed yet. Tell me \"update yourself\", or run \"1 - Setup.bat\" again.")
    pages, docs, external, blocked, failed = [], set(), {}, 0, []
    queue = [(start, 0, True)]
    queued = {_same(start)}
    per_path = {}
    info = {"start": start, "domain": key, "sitemap": len(from_map), "shown": False}
    with sync_playwright() as pw:
        browser, headless = _launch(pw, show)
        info["shown"] = not headless
        try:
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 800}, ignore_https_errors=True,
                                      locale="en-ZA")
            page = ctx.new_page()
            while queue and len(pages) < max_pages:
                if stopped():
                    info["stopped"] = True
                    break
                # pages linked from the menu first, then the shallowest
                queue.sort(key=lambda t: (not t[2], t[1]))
                url, depth, _ = queue.pop(0)
                if not rp.can_fetch("Trillion-SiteReader", url):
                    blocked += 1
                    continue
                say(f"Reading page {len(pages) + 1} of up to {max_pages}: {urlparse(url).path or '/'}")
                t0 = time.time()
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    try:
                        page.wait_for_load_state("load", timeout=8000)
                    except Exception:
                        pass
                    for _ in range(4):                       # bring in pictures and sections that only load on scrolling
                        page.mouse.wheel(0, 1600)
                        page.wait_for_timeout(250)
                    page.evaluate("window.scrollTo(0, 0)")
                    data = page.evaluate(EXTRACT_JS)
                except Exception as e:
                    try:                                     # a fresh tab, so the browser's error page cannot cut into the next one
                        page.close()
                    except Exception:
                        pass
                    page = ctx.new_page()
                    if not pages and url.startswith("https://") and not info.get("tried_http"):
                        info["tried_http"] = True            # an older site that only answers on plain http
                        alt = "http://" + url[8:]
                        queued.add(_same(alt))
                        queue.insert(0, (alt, depth, True))
                        continue
                    failed.append({"url": url, "error": str(e).splitlines()[0][:160]})
                    continue
                status = resp.status if resp else 0
                final = page.url
                if host_key(urlparse(final).hostname) != key:
                    failed.append({"url": url, "error": f"Sent on to another site: {urlparse(final).hostname}"})
                    continue
                if pages and _same(final) in {_same(p["url"]) for p in pages}:
                    continue                                     # a redirect to a page already read
                if not pages and _same(final) != _same(start):
                    queued.add(_same(final))
                text = data.pop("text", "")[:TEXT_KEEP]
                links = data.pop("links", [])
                p = dict(data, url=final, status=status, depth=depth, words=len(text.split()), text=text,
                         seconds=round(time.time() - t0, 1), emails=sorted(set(EMAIL.findall(text)))[:10],
                         phones=sorted(set(m.strip() for m in PHONE.findall(text)))[:10],
                         prices=list(dict.fromkeys(m.strip() for m in PRICE.findall(text)))[:20])
                internal = []
                for ln in links:
                    href = ln.get("href") or ""
                    if href.startswith(("mailto:", "tel:")):
                        (p["emails"] if href.startswith("mailto:") else p["phones"]).append(href.split(":", 1)[1].split("?")[0])
                        continue
                    u = urlparse(href)
                    if u.scheme in ("http", "https") and u.hostname and host_key(u.hostname) != key:
                        external[host_key(u.hostname)] = external.get(host_key(u.hostname), 0) + 1
                        continue
                    c = clean_link(href, final, key)
                    if not c:
                        continue
                    if DOC_EXT.search(urlparse(c).path):
                        docs.add(c)
                        continue
                    if SKIP_EXT.search(urlparse(c).path):
                        continue
                    internal.append(c)
                    s_ = _same(c)
                    if s_ in queued:
                        continue
                    base_path = urlparse(c).path
                    if urlparse(c).query:                        # many filtered versions of one list: keep a few
                        per_path[base_path] = per_path.get(base_path, 0) + 1
                        if per_path[base_path] > 3:
                            continue
                    queued.add(s_)
                    queue.append((c, depth + 1, bool(ln.get("nav"))))
                p["emails"] = sorted(set(p["emails"]))[:10]
                p["phones"] = sorted(set(p["phones"]))[:10]
                p["links_out"] = len(set(internal))
                if shots_dir and (len(pages) < 6 and (depth <= 1)):
                    try:
                        shot = Path(shots_dir) / f"page{len(pages) + 1}.jpg"
                        page.screenshot(path=str(shot), type="jpeg", quality=82)
                        p["shot"] = str(shot)
                    except Exception:
                        pass
                pages.append(p)
                # pages from the sitemap join once the menu pages are in, so a big shop does not fill up on products
                if len(pages) == 1:
                    for c in from_map:
                        if _same(c) not in queued and not SKIP_EXT.search(urlparse(c).path):
                            queued.add(_same(c))
                            queue.append((c, 2, False))
                time.sleep(PAUSE)
        finally:
            browser.close()
    if not pages:
        if blocked:
            raise SiteError("The site's robots.txt asks programs not to read it, so I left it alone.")
        why = failed[0]["error"] if failed else "no pages answered"
        raise SiteError(f"I could not open {start}: {why}")
    info.update(found=len(queued), left=len(queue), blocked=blocked, failed=failed[:20], documents=sorted(docs)[:40],
                external=sorted(external.items(), key=lambda kv: -kv[1])[:20])
    return pages, info


# ---------- checks that need no brain ----------
def health(pages, info):
    """Plain checks on every page: what is missing, slow or broken. Each issue has a page, a level and a fix."""
    issues = []
    def add(p, level, what, fix, kind=None):
        issues.append({"url": p["url"], "level": level, "what": what, "fix": fix, "kind": kind or what})
    titles = {}
    for p in pages:
        titles.setdefault(p["title"].lower(), []).append(p)
    for p in pages:
        if p["status"] >= 400:
            add(p, "high", f"Page answers with error {p['status']}", "Fix or remove the page and the links to it.", "Broken page (error)")
            continue
        if not p["title"]:
            add(p, "high", "No page title", "Give the page a title of 50 to 60 characters; it is the line Google shows.", "No page title")
        elif len(p["title"]) > 65:
            add(p, "low", f"Title is long ({len(p['title'])} characters)", "Keep titles under about 60 characters.", "Title too long")
        elif len(titles.get(p["title"].lower(), [])) > 1:
            add(p, "medium", "Same title as another page", "Give each page its own title.", "Same title as another page")
        if not p["description"]:
            add(p, "medium", "No meta description", "Add a one-line description (120 to 160 characters) for search results.", "No meta description")
        if not p["h1"]:
            add(p, "medium", "No main heading (H1)", "Give the page one clear main heading.", "No main heading")
        elif len(p["h1"]) > 1:
            add(p, "low", f"{len(p['h1'])} main headings (H1)", "Use one H1 per page; make the others H2.", "More than one main heading")
        if p["words"] < 150 and p["depth"] > 0:
            add(p, "low", f"Very little text ({p['words']} words)", "Thin pages rank poorly; add useful detail or merge the page.", "Very little text")
        if p["no_alt"]:
            add(p, "low" if p["no_alt"] < 5 else "medium", f"{p['no_alt']} of {p['images']} pictures have no description (alt text)",
                "Describe each picture in its alt text, for search engines and screen readers.", "Pictures without descriptions")
        if p["load_ms"] > 4000 or p["seconds"] > 8:
            add(p, "medium", f"Slow to load ({max(p['load_ms'] / 1000, p['seconds']):.1f} s)", "Shrink large pictures and remove unused scripts.", "Slow to load")
        if not p["viewport"]:
            add(p, "high", "Not set up for phones (no viewport tag)", "Add a viewport meta tag so the page fits phone screens.", "Not set up for phones")
    plain = [p for p in pages if p["url"].startswith("http://")]
    if plain:
        add(plain[0], "high", f"Not secure: {len(plain)} page{'s' if len(plain) != 1 else ''} without https",
            "Serve the whole site over https with a certificate; browsers mark plain pages as not secure.", "Not secure (no https)")
    for f in info.get("failed", []):
        issues.append({"url": f["url"], "level": "high", "what": "Page did not open: " + f["error"], "fix": "Check the link and the page.",
                       "kind": "Page did not open"})
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: order[i["level"]])
    ok = sum(1 for p in pages if not any(i["url"] == p["url"] for i in issues))
    words = sum(p["words"] for p in pages)
    loads = [p["load_ms"] or p["seconds"] * 1000 for p in pages if p["status"] < 400]
    contacts = {"emails": sorted({e for p in pages for e in p["emails"]})[:12], "phones": sorted({x for p in pages for x in p["phones"]})[:12],
                "socials": sorted({s for p in pages for s in p.get("socials", [])})[:12]}
    score = max(0, 100 - sum({"high": 8, "medium": 3, "low": 1}[i["level"]] for i in issues) * 100 // max(1, len(pages) * 12))
    return {"issues": issues, "pages_ok": ok, "words": words, "images": sum(p["images"] for p in pages),
            "no_alt": sum(p["no_alt"] for p in pages), "avg_load": max(0.1, round(sum(loads) / len(loads) / 1000, 1)) if loads else None,
            "counts": {k: sum(1 for i in issues if i["level"] == k) for k in ("high", "medium", "low")},
            "score": score, "contacts": contacts,
            "prices": list(dict.fromkeys(x for p in pages for x in p["prices"]))[:20],
            "structured": sorted({s for p in pages for s in p["structured"]})[:15],
            "https": all(p["url"].startswith("https://") for p in pages)}


# ---------- the brain's part ----------
def _ask(llm, user, prompt, system="You are a careful website analyst. Answer with JSON only."):
    try:
        msg = llm(user, [{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    except Exception:
        return {}                                # the brain is not answering: the plain checks still make the files
    text = (msg or {}).get("content") or ""
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        return {}


def _strs(v, n=8, size=260):
    out = []
    for x in (v if isinstance(v, list) else []):
        if isinstance(x, dict):
            x = " - ".join(str(x.get(k)) for k in ("title", "name", "detail", "text") if x.get(k))
        x = re.sub(r"\s+", " ", str(x or "")).strip()
        if x:
            out.append(x[:size])
    return out[:n]


def _pairs(v, n=8):
    out = []
    for x in (v if isinstance(v, list) else []):
        if isinstance(x, dict):
            t = str(x.get("title") or x.get("name") or "").strip()
            d = str(x.get("detail") or x.get("description") or x.get("why") or "").strip()
            pr = str(x.get("priority") or "").strip().lower()
        else:
            t, d, pr = str(x).strip(), "", ""
        if t:
            out.append({"title": t[:90], "detail": d[:320], "priority": pr if pr in ("high", "medium", "low") else ""})
    return out[:n]


def page_notes(llm, user, pages, say=None, stopped=lambda: False):
    notes = []
    picked = sorted(pages, key=lambda p: (p["depth"], -p["words"]))[:NOTE_PAGES]
    for i, p in enumerate(picked, 1):
        if stopped():
            break
        if say:
            say(f"Studying page {i} of {len(picked)}: {p['title'][:60] or urlparse(p['url']).path}")
        if p["words"] < 20:
            notes.append({"url": p["url"], "title": p["title"], "summary": "Almost no text on this page.", "facts": []})
            continue
        j = _ask(llm, user, (
            f"Page address: {p['url']}\nTitle: {p['title']}\nHeadings: {'; '.join(p['h1'] + p['headings'][:15])}\n"
            f"Words on the page (cut short):\n{p['text'][:4500]}\n\n"
            'Return {"summary": "two or three plain sentences on what this page is for", '
            '"facts": ["up to six specific facts worth keeping: products or services with prices, opening hours, places, '
            'numbers, offers, claims"]}. Only use what is on the page.'))
        notes.append({"url": p["url"], "title": p["title"], "summary": str(j.get("summary") or "")[:600],
                      "facts": _strs(j.get("facts"), 6, 200)})
    return notes


def analyse(llm, user, pages, info, checks, focus="", say=None, stopped=lambda: False):
    notes = page_notes(llm, user, pages, say, stopped)
    if say:
        say("Writing the analysis")
    home = pages[0]
    digest = "\n".join(f"- {n['title'] or n['url']} ({urlparse(n['url']).path}): {n['summary']} "
                       + ("Facts: " + "; ".join(n["facts"]) if n["facts"] else "") for n in notes)
    other = [p["title"] or urlparse(p["url"]).path for p in pages if p["url"] not in {n["url"] for n in notes}][:60]
    stats = (f"{len(pages)} pages read, {checks['words']} words, {checks['images']} pictures. Issues found: "
             f"{checks['counts']['high']} serious, {checks['counts']['medium']} medium, {checks['counts']['low']} small. "
             f"Average load {checks['avg_load']} s. Contact found: {', '.join(checks['contacts']['emails'][:3] + checks['contacts']['phones'][:3]) or 'none'}. "
             f"Social links: {', '.join(checks['contacts']['socials'][:6]) or 'none'}. Prices seen: {', '.join(checks['prices'][:10]) or 'none'}.")
    ask = (f"Website: {info['start']}\nHome page title: {home['title']}\nHome page description: {home['description']}\n"
           f"Page notes:\n{digest[:9000]}\nOther pages: {'; '.join(other)[:1500]}\nChecks: {stats}\n"
           + (f"\nThe user asked for this: {focus[:600]}\n" if focus else "")
           + '\nReturn JSON with these keys:\n'
             '{"name": "the business or site name", "one_liner": "what the site is, in one sentence",\n'
             ' "summary": "a paragraph of 4 to 6 sentences",\n'
             ' "audience": "who the site is for", "tone": "how the site sounds and looks, one sentence",\n'
             ' "offering": [{"title": "a product, service or section", "detail": "one sentence, with prices if known"}],\n'
             ' "key_facts": ["specific facts: prices, places, hours, numbers"],\n'
             ' "strengths": ["what the site does well"], "weaknesses": ["what is missing or weak"],\n'
             ' "recommendations": [{"title": "short action", "detail": "why and how", "priority": "high|medium|low"}],\n'
             ' "answer": "if the user asked something specific, answer it here in a paragraph; otherwise empty",\n'
             ' "next_steps": ["three short next steps"]}\n'
             "Use only what the notes and checks show. Up to 6 items in each list. Plain English, no marketing fluff.")
    j = _ask(llm, user, ask) if not stopped() else {}
    name = str(j.get("name") or "").strip() or (home.get("og_title") or home["title"] or info["domain"]).split("|")[0].split(" - ")[0].strip()
    result = {
        "name": name[:80] or info["domain"], "one_liner": str(j.get("one_liner") or home["description"] or "")[:300],
        "summary": str(j.get("summary") or "")[:1800], "audience": str(j.get("audience") or "")[:400],
        "tone": str(j.get("tone") or "")[:300], "offering": _pairs(j.get("offering"), 8), "key_facts": _strs(j.get("key_facts"), 8),
        "strengths": _strs(j.get("strengths"), 6), "weaknesses": _strs(j.get("weaknesses"), 6),
        "recommendations": _pairs(j.get("recommendations"), 6), "answer": str(j.get("answer") or "")[:2000],
        "next_steps": _strs(j.get("next_steps"), 4), "notes": notes, "brain": bool(j),
    }
    if not result["summary"]:                     # the brain did not answer: still give something true
        result["summary"] = (f"{result['name']} has {len(pages)} pages that were read. " + " ".join(n["summary"] for n in notes[:3]))[:1200]
    if not result["recommendations"]:
        seen = set()
        for i in checks["issues"]:
            if i["what"].split(" (")[0] not in seen:
                seen.add(i["what"].split(" (")[0])
                result["recommendations"].append({"title": i["fix"].split(";")[0][:90], "detail": i["what"], "priority": i["level"]})
            if len(result["recommendations"]) >= 5:
                break
    return result


# ---------- jobs ----------
def folder_for(user, domain):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    f = core.out_dir(user, "Trillion Websites") / f"{domain} {stamp}"
    f.mkdir(parents=True, exist_ok=True)
    return f


def wants(text):
    """Which files a request asks for. No mention means a report and a presentation."""
    t = text.lower()
    out = []
    if re.search(r"present|slide|deck|power ?point|pptx|pitch", t):
        out.append("pptx")
    if re.search(r"document|report|word\b|docx|write[- ]?up|summary doc|brief", t):
        out.append("docx")
    if re.search(r"spread ?sheet|excel|xlsx|sheet|table of", t):
        out.append("xlsx")
    return out or ["docx", "pptx"]


def start(user, url, outputs=None, focus="", max_pages=DEFAULT_PAGES, show=False):
    """Begin reading in the background. Returns the job row right away."""
    url = normalise_url(url)
    host = urlparse(url).hostname
    if not public_host(host):
        raise SiteError("That address is on this PC or the home network. I only read websites out on the internet.")
    uid = user["id"]
    if core.q("SELECT 1 FROM site_reads WHERE user_id=? AND status='running'", (uid,), one=True):
        raise SiteError("I am already reading a website for you. Let that one finish, or press Stop on the Websites page.")
    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = DEFAULT_PAGES
    max_pages = max(3, min(max_pages, max(PAGE_CHOICES)))
    outputs = [o for o in (outputs or ["docx", "pptx"]) if o in OUTPUTS] or ["docx", "pptx"]
    tid = core.add_task(uid, f"Read and analyse {host_key(host)}")
    jid = core.x("INSERT INTO site_reads(user_id,url,domain,status,created,focus,outputs,max_pages,show,task_id,detail) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid, url, host_key(host), "running", time.time(), str(focus or "")[:1000],
                                                    json.dumps(outputs), max_pages, 1 if show else 0, tid, "Starting the browser"))
    t = threading.Thread(target=_run, args=(dict(user), jid), daemon=True)
    with _LOCK:
        RUNNING[jid] = t
    t.start()
    return job(user, jid)


def _set(jid, **kw):
    core.x("UPDATE site_reads SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", tuple(kw.values()) + (jid,))


def _run(user, jid):
    j = core.q("SELECT * FROM site_reads WHERE id=?", (jid,), one=True)
    tid = j["task_id"]
    stopped = lambda: jid in STOP

    def say(text):
        _set(jid, detail=text[:200])
        core.set_task(tid, detail=text[:200])

    try:
        folder = folder_for(user, j["domain"])
        shots = folder / "pictures"
        shots.mkdir(exist_ok=True)
        _set(jid, folder=str(folder))
        pages, info = crawl(j["url"], j["max_pages"], bool(j["show"]), shots, say, stopped)
        _set(jid, pages=len(pages))
        checks = health(pages, info)
        result = analyse(core.LLM, user, pages, info, checks, j["focus"], say, stopped)
        (folder / "pages.json").write_text(json.dumps({"info": info, "pages": pages}, ensure_ascii=False), encoding="utf-8")
        files = {}
        if not stopped():
            import sitedocs
            for kind in json.loads(j["outputs"]):
                say({"pptx": "Making the presentation", "docx": "Writing the report", "xlsx": "Making the spreadsheet"}[kind])
                try:
                    files[kind] = str(sitedocs.make(kind, folder, result, checks, pages, info, user))
                except Exception as e:
                    files[kind + "_error"] = f"{type(e).__name__}: {e}"[:200]
        out = {"analysis": result, "checks": {k: v for k, v in checks.items() if k != "issues"},
               "issues": checks["issues"][:150], "info": {k: v for k, v in info.items()}, "files": files,
               "pages": [{"url": p["url"], "title": p["title"], "status": p["status"], "words": p["words"]} for p in pages]}
        status = "stopped" if stopped() else "done"
        made = [{"pptx": "presentation", "docx": "report", "xlsx": "spreadsheet"}[k] for k in files if k in OUTPUTS]
        detail = (f"Read {len(pages)} pages of {j['domain']}" + (f"; made the {' and '.join(made)}" if made else "")
                  + ("; stopped early" if status == "stopped" else ""))
        _set(jid, status=status, finished=time.time(), result=json.dumps(out, ensure_ascii=False), detail=detail)
        core.set_task(tid, status="completed", detail=detail)
        core.log(user["id"], detail, "done")
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant",
               detail + ". Everything is on the Websites page and in your folder Trillion Websites.", time.time()))
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
    if core.q("SELECT 1 FROM site_reads WHERE id=? AND user_id=? AND status='running'", (jid, user["id"]), one=True):
        STOP.add(jid)
        _set(jid, detail="Stopping after this page")
        return True
    return False


def job(user, jid):
    r = core.q("SELECT * FROM site_reads WHERE id=? AND user_id=?", (jid, user["id"]), one=True)
    if not r:
        return None
    d = dict(r)
    d["outputs"] = json.loads(d["outputs"] or "[]")
    d["result"] = json.loads(d["result"] or "{}")
    return d


def jobs(user, limit=30):
    rows = core.q("SELECT id,url,domain,status,created,finished,pages,detail,focus,outputs,max_pages,folder FROM site_reads "
                  "WHERE user_id=? ORDER BY id DESC LIMIT ?", (user["id"], limit))
    return [dict(r, outputs=json.loads(r["outputs"] or "[]")) for r in rows]


def forget(user, jid):
    core.x("DELETE FROM site_reads WHERE id=? AND user_id=? AND status!='running'", (jid, user["id"]))


# ---------- chat ----------
URL_IN = re.compile(r"\b((?:https?://)?(?:[a-z0-9-]+\.)+(?:co\.za|org\.za|gov\.za|ac\.za|net\.za|com|net|org|io|ai|app|dev|"
                    r"co|info|biz|store|shop|online|site|xyz|me|tv|uk|us|de|nl|au|nz|ca|africa|za)(?::\d{2,5})?(?:/[^\s,;!?\"')]*)?)", re.I)
VERB = re.compile(r"\b(read|analy[sz]e|analysis|go through|crawl|scan|review|study|research|look through|audit|check out|"
                  r"summari[sz]e|break down|dig into)\b", re.I)
SITE_WORD = re.compile(r"\b(web ?site|site|web ?page|url|domain|home ?page)\b", re.I)
SHOW = re.compile(r"\b(open|show)\b[^.?!]{0,30}\b(browser|edge|chrome|window|screen)\b|\bon (my|the) (pc|screen)\b", re.I)
PAGES = re.compile(r"\b(\d{1,3})\s*pages?\b", re.I)


def request(text):
    """(url, outputs, show, pages) when the words ask to read a website, else None."""
    m = URL_IN.search(text)
    if not m or not VERB.search(text):
        return None
    if "@" in text[max(0, m.start() - 1):m.start() + 1]:
        return None
    if not (SITE_WORD.search(text) or re.search(r"https?://", m.group(1), re.I) or
            re.search(r"\b(read|analy[sz]e|crawl|audit|go through)\b\s+(?:the\s+)?\S*" + re.escape(m.group(1).split("/")[0]), text, re.I)):
        return None
    pm = PAGES.search(text)
    return {"url": m.group(1), "outputs": wants(text), "show": bool(SHOW.search(text)),
            "max_pages": int(pm.group(1)) if pm else DEFAULT_PAGES}


def started_reply(j):
    made = {"pptx": "a presentation", "docx": "a report", "xlsx": "a spreadsheet"}
    outs = [made[o] for o in j["outputs"]]
    what = ", ".join(outs[:-1]) + (" and " if len(outs) > 1 else "") + outs[-1]
    return (f"I am reading {j['domain']} now, up to {j['max_pages']} pages"
            + (", in a browser window on the PC so you can watch" if j["show"] else "")
            + f". Then I will analyse it and make {what}. A small site takes a few minutes; you can follow it on the "
              "Websites page, and I will say when it is done.")
