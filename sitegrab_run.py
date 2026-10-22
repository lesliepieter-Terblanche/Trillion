"""Subprocess runner for the website photo-grabber. Opens a URL in a headless Chromium, lets the page's
JavaScript run, scrolls to trigger lazy images, optionally follows a couple of shop/product links, and
collects the real product photos the page renders. Downloads them through the browser's own session (so
protected CDNs work), validates and saves them as JPEGs, and prints `RESULT <json list of paths>`.

Run standalone so a browser crash can never affect Trillion:
  python sitegrab_run.py --selfcheck
  python sitegrab_run.py --url <url> --out <dir> --limit 8 --w 1080 --h 1920
"""
import argparse
import io
import json
import sys

SKIP = ("logo", "icon", "sprite", "favicon", "placeholder", "loader", "spinner", "avatar",
        "badge", "payment", "visa", "mastercard", "paypal", "flag", "pixel", "tracking", "banner")

# JS that reads every rendered image (and CSS background images) off the live DOM
COLLECT = """() => {
  const out = [];
  const push = (u, w, h) => { if (u) out.push({u, w: w||0, h: h||0}); };
  for (const img of document.querySelectorAll('img')) {
    push(img.currentSrc || img.src, img.naturalWidth, img.naturalHeight);
  }
  for (const el of document.querySelectorAll('*')) {
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = bg.match(/url\\(["']?(.*?)["']?\\)/);
    if (m && m[1]) push(m[1], el.clientWidth, el.clientHeight);
  }
  const og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
  if (og && og.content) push(og.content, 1200, 1200);
  return out;
}"""

LINKS = """() => {
  const seen = new Set(); const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.href || '';
    if (/\\/(product|products|shop|collection|collections|item|store)\\b/i.test(h) && !seen.has(h)) {
      seen.add(h); out.push(h);
    }
  }
  return out.slice(0, 4);
}"""


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    for _ in range(6):                      # scroll to pull in lazy-loaded product images
        try:
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(500)
        except Exception:
            break
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass


def _clean(url, cands):
    from urllib.parse import urljoin
    seen, out = set(), []
    for c in cands:
        u = (c.get("u") or "").strip()
        if not u or u.startswith("data:"):
            continue
        if u.startswith("//"):
            u = "https:" + u
        u = urljoin(url, u)
        lu = u.lower().split("?")[0]
        if not lu.startswith("http") or lu.endswith(".svg"):
            continue
        if lu in seen or any(b in lu for b in SKIP):
            continue
        seen.add(lu)
        out.append((u, int(c.get("w") or 0), int(c.get("h") or 0)))
    return out


def grab(url, out_dir, limit, w, h):
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageOps
    saved = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, device_scale_factor=1,
                                  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                             "(KHTML, like Gecko) Chrome/122 Safari/537.36")
        page = ctx.new_page()
        if not url.lower().startswith("http"):
            url = "https://" + url
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            browser.close()
            return []
        _settle(page)
        cands = list(page.evaluate(COLLECT) or [])
        try:
            links = list(page.evaluate(LINKS) or [])
        except Exception:
            links = []
        for link in links[:2]:              # a product/shop page usually has the best, biggest shots
            if len([c for c in cands if (c.get("w") or 0) >= 400]) >= limit * 3:
                break
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=25000)
                _settle(page)
                cands += list(page.evaluate(COLLECT) or [])
            except Exception:
                continue
        ranked = _clean(url, cands)
        ranked.sort(key=lambda t: t[1] * t[2], reverse=True)   # biggest rendered first = the real product shots
        i = 0
        for u, iw, ih in ranked:
            if len(saved) >= limit:
                break
            try:
                resp = ctx.request.get(u, timeout=20000)
                if not resp.ok:
                    continue
                body = resp.body()
                if not body or len(body) < 6000:               # tiny = icon/thumb
                    continue
                with Image.open(io.BytesIO(body)) as im:
                    pw_, ph_ = im.size
                    ar = pw_ / max(1, ph_)
                    if pw_ < 500 or ph_ < 500 or ar > 4 or ar < 0.25:
                        continue
                    dest = f"{out_dir}/shot-{i}.jpg"
                    ImageOps.exif_transpose(im).convert("RGB").save(dest, "JPEG", quality=92)
                saved.append(dest)
                i += 1
            except Exception:
                continue
        browser.close()
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--w", type=int, default=1080)
    ap.add_argument("--h", type=int, default=1920)
    a = ap.parse_args()
    if a.selfcheck:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                b.close()
            print("SELFCHECK_OK")
            return 0
        except Exception as e:
            print("SELFCHECK_FAIL", type(e).__name__, str(e)[:200], file=sys.stderr)
            return 1
    try:
        paths = grab(a.url, a.out, a.limit, a.w, a.h)
    except Exception as e:
        print("GRAB_FAIL", type(e).__name__, str(e)[:200], file=sys.stderr)
        paths = []
    print("RESULT " + json.dumps(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
