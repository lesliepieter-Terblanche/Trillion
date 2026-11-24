"""Bespoke website mode: design a one-off page from the brief with the user's FREE Gemini key, instead of
filling a template. The premium look still can't be left to chance, so the output goes through a finish-check
(render it in the headless browser, catch overflow / placeholder / empty / broken), with one repair pass.

Everything here is best-effort: any failure returns None and the studio falls back to the hybrid template
build, which always works. Only Gemini is used (free tier) - never a paid provider - so the free rule holds.
"""
import json
import re

import httpx

import core

GEMINI_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL = "gemini-2.5-flash"   # free tier
DESIGN_MODEL = "qwen2.5-coder:14b"  # strong LOCAL coder (free, ~9 GB at Q4, fits a 16 GB card) - preferred
MAX_REPAIRS = 1


def design_model(user):
    return (core.settings(user).get("design_model") or DESIGN_MODEL).strip()


def _local_ready(user):
    try:
        return core.has_model(design_model(user))
    except Exception:
        return False


def available(user):
    """Bespoke is possible if either the local coder is installed or a Gemini key is set."""
    return _local_ready(user) or bool(core.get_secret(user["id"], "gemini"))


def _design(user, messages, temperature=0.6):
    """One design call. Prefers the strong LOCAL coding model (free, offline); falls back to the user's free
    Gemini key. Returns text, or '' on failure."""
    if _local_ready(user):
        try:
            r = httpx.post(core.OLLAMA + "/api/chat", timeout=600, json={
                "model": design_model(user), "messages": messages, "stream": False,
                "keep_alive": "20m", "options": {"temperature": temperature, "num_ctx": 12288}})
            r.raise_for_status()
            txt = (r.json().get("message") or {}).get("content") or ""
            if txt.strip():
                return txt
        except Exception:
            pass
    key = core.get_secret(user["id"], "gemini")
    if key:
        try:
            r = httpx.post(GEMINI_OPENAI + "/chat/completions", timeout=180,
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": GEMINI_MODEL, "temperature": temperature, "messages": messages})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception:
            return ""
    return ""


def _strip_html(text):
    """Pull the HTML document out of a model reply (it often wraps it in a ```html fence or adds chatter)."""
    t = str(text or "")
    m = re.search(r"```(?:html)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1)
    i = t.lower().find("<!doctype")
    if i < 0:
        i = t.lower().find("<html")
    if i >= 0:
        t = t[i:]
    j = t.lower().rfind("</html>")
    if j >= 0:
        t = t[:j + 7]
    return t.strip()


_TASTE = (
    "You are an award-winning art director who ships sites that WIN Awwwards Site of the Day and look like the "
    "igloo.inc / motion.dev / studio-grade gallery. You write ONE complete, self-contained HTML document and "
    "nothing else. This must feel expensive, bespoke and cinematic - never like a Bootstrap/template site.\n"
    "\nULTRA-PREMIUM DIRECTION (this is what separates a winning site from a generic one):\n"
    "- A dramatic full-viewport hero with a LIVING animated background you build in <canvas> or CSS (a flowing "
    "gradient/particle/light field, grain overlay, subtle parallax) - NOT a flat colour. The hero type is huge "
    "(clamp up to ~8vw), tight letter-spacing, and animates in word-by-word.\n"
    "- Editorial, confident layout: generous whitespace, a strong type scale, asymmetry and large section "
    "numbers/labels. Avoid centre-everything and avoid rows of identical drop-shadow cards.\n"
    "- A refined, restrained palette: usually a deep dark base with ONE accent that fits the trade, fine 1px "
    "borders, soft glows - not the cliche purple-to-blue gradient on white. Add a faint film-grain/noise layer.\n"
    "- Craft details: a sticky header that shrinks/condenses on scroll, smooth-scroll, hover micro-interactions, "
    "an accent underline/line-draw, number counters or a marquee where it fits. Every section reveals on scroll "
    "from a VISIBLE resting state.\n"
    "- Real, persuasive, specific copy from the brief (benefit-led, mentions the business by name). The site must "
    "feel made for THIS business and no other.\n"
    "\nNON-NEGOTIABLE RULES (a beautiful but broken page is a FAIL):\n"
    "- Output only the HTML document (<!doctype html> ... </html>). No explanation, no markdown fence.\n"
    "- Include <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> in the head.\n"
    "- All CSS in one <style> tag in the head. All JS in one <script> tag before </body>.\n"
    "- Fonts: you MAY use Google Fonts via <link href=\"https://fonts.googleapis.com/...\">. Pick a characterful "
    "display face + a clean body face that SUIT THIS SPECIFIC BUSINESS; never reuse a default pairing.\n"
    "- Animation libs: you MAY load GSAP and/or AOS from https://cdnjs.cloudflare.com only. Never leave content "
    "stuck at opacity:0 if its reveal fails - reveals must end visible.\n"
    "- IMAGES: use ONLY the exact photo filenames provided, as <img src=\"filename.jpg\"> with loading=\"lazy\". "
    "Do NOT invent image URLs or hotlink stock - any other image host will be blank. If no photos are given, "
    "design a striking site without photos (animated gradient/canvas, bold type, SVG).\n"
    "- RESPONSIVE (critical): must work at 390px wide with NO horizontal scroll; everything stacks to one column "
    "on phones; images max-width:100%. The header nav MUST collapse into a hamburger/toggle menu on phones - "
    "never let the full nav links stack down the page and fill the screen. Use a @media (max-width:860px) query "
    "with a working button that shows/hides the menu.\n"
    "- Include, in a sensible order: a header/nav with the business name, a hero (headline + subline + 1-2 "
    "buttons), a services/offer section, a gallery if photos exist, a reviews/testimonials section, a clear "
    "contact section with the phone and address, and a footer. Fold in any requested add-ons.\n"
    "- Legible contrast everywhere; a visible focus state on links/buttons; honour prefers-reduced-motion.")


def _brief(info, photos_rel):
    name = info.get("name") or info.get("business") or "The business"
    services = info.get("services") or []
    svc = "; ".join((s.get("t", "") + (": " + s.get("d", "") if s.get("d") else "")) for s in services if isinstance(s, dict)) \
        if services and isinstance(services[0], dict) else ", ".join(map(str, services))
    brief = {
        "business": name, "category": info.get("category") or info.get("cat") or "",
        "tagline": info.get("tagline") or "", "about": info.get("about") or info.get("desc") or "",
        "services": svc, "phone": info.get("phone") or "", "address": info.get("address") or info.get("addr") or "",
        "hours": info.get("hours") or "", "addons": info.get("addons") or [],
        "photos": photos_rel or [], "country": "South Africa",
    }
    return brief


def generate(user, info, folder, photos_rel, progress=None):
    """Design index.html from the brief with Gemini, finish-check and repair once. Returns the HTML, or None
    so the caller falls back to the hybrid template build."""
    say = progress or (lambda t: None)
    if not available(user):
        return None
    brief = _brief(info, photos_rel)
    say("Designing a one-off layout with Gemini")
    msgs = [{"role": "system", "content": _TASTE},
            {"role": "user", "content": "Design the website for this business. Brief (JSON):\n"
             + json.dumps(brief, ensure_ascii=False)
             + "\n\nReturn the complete HTML document only."}]
    html = _strip_html(_design(user, msgs))
    if not _looks_like_page(html):
        return None
    for _ in range(MAX_REPAIRS):
        issues = _check(folder, html)
        if not issues:
            break
        say("Polishing the design")
        fix = _design(user, msgs + [
            {"role": "assistant", "content": html[:60000]},
            {"role": "user", "content": "Fix these issues and return the COMPLETE corrected HTML document only, "
             "keeping the same design:\n- " + "\n- ".join(issues)}])
        fixed = _strip_html(fix)
        if _looks_like_page(fixed):
            html = fixed
    if not _looks_like_page(html):
        return None
    return _inject_nav_fix(html)   # guarantee a working mobile menu + no sideways scroll, deterministically


_NAV_FIX = """
<style id="t-navfix">
@media(max-width:860px){
 html,body{overflow-x:hidden!important;max-width:100%!important}
 .t-burger{display:flex!important;position:fixed!important;top:12px!important;right:12px!important;
  z-index:100000!important;width:48px!important;height:48px!important;align-items:center!important;
  justify-content:center!important;border-radius:12px!important;border:1px solid rgba(255,255,255,.28)!important;
  background:rgba(18,18,20,.62)!important;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);
  cursor:pointer!important;padding:0!important;margin:0!important;box-shadow:none!important;outline:none!important;
  -webkit-appearance:none!important;appearance:none!important;color:#fff!important}
 .t-burger:hover,.t-burger:focus,.t-burger:active{background:rgba(18,18,20,.62)!important;box-shadow:none!important}
 .t-burger span,.t-burger span::before,.t-burger span::after{content:"";position:absolute;width:22px;height:2px;
  background:#fff!important;border-radius:2px;transition:.25s;left:50%;margin-left:-11px}
 .t-burger span{top:50%}.t-burger span::before{top:-7px}.t-burger span::after{top:7px}
 body.t-open .t-burger span{background:transparent}
 body.t-open .t-burger span::before{top:0;transform:rotate(45deg)}
 body.t-open .t-burger span::after{top:0;transform:rotate(-45deg)}
 [data-tnav]{position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:auto!important;
  display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;
  gap:6px!important;width:100%!important;max-width:100%!important;height:100vh!important;height:100dvh!important;
  bottom:0!important;margin:0!important;padding:84px 20px 32px!important;list-style:none!important;
  background:#0b0b0d!important;-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
  transform:translateY(-104%);transition:transform .34s cubic-bezier(.4,0,.2,1);z-index:99990;overflow:auto}
 body.t-open [data-tnav]{transform:translateY(0)}
 [data-tnav]>*{width:auto!important;text-align:center!important;font-size:19px!important}
}
@media(min-width:861px){.t-burger{display:none!important}}
</style>
<script>(function(){try{
 if(document.querySelector('.t-burger'))return;
 var nav=document.querySelector('header nav ul,header ul,header nav,nav ul,nav');
 if(!nav)return;
 nav.setAttribute('data-tnav','');
 var b=document.createElement('button');b.type='button';b.className='t-burger';
 b.setAttribute('aria-label','Menu');b.innerHTML='<span></span>';b.style.display='none';
 document.body.appendChild(b);
 b.addEventListener('click',function(){document.body.classList.toggle('t-open');});
 nav.addEventListener('click',function(e){if(e.target.closest('a'))document.body.classList.remove('t-open');});
 addEventListener('resize',function(){if(innerWidth>860)document.body.classList.remove('t-open');});
}catch(e){}})();</script>
"""


def _inject_nav_fix(html):
    """Guarantee a working collapsible mobile menu on any bespoke page, whatever markup the model wrote.
    Also forces no-horizontal-scroll on phones. Deterministic - doesn't rely on the model getting it right."""
    if not html or "t-navfix" in html:
        return html
    h = html
    if "name=\"viewport\"" not in h.lower() and "name='viewport'" not in h.lower():
        h = re.sub(r"(<head[^>]*>)", r'\1<meta name="viewport" content="width=device-width, initial-scale=1">',
                   h, count=1, flags=re.I)
    if re.search(r"</body>", h, re.I):
        return re.sub(r"</body>", _NAV_FIX + "</body>", h, count=1, flags=re.I)
    return h + _NAV_FIX


def _looks_like_page(html):
    h = (html or "").lower()
    return len(html or "") > 1200 and "<html" in h and "</html>" in h and "<style" in h and "__" not in html[:4000]


def _check(folder, html):
    """Render the page in the installed headless browser and return a list of problems to fix. Empty if clean
    or if the browser isn't available (then we trust the model)."""
    issues = []
    low = html.lower()
    if re.search(r"lorem ipsum|placeholder text|your text here|example\.com", low):
        issues.append("Remove placeholder/lorem text and use the real business copy from the brief.")
    if re.search(r"__[a-z0-9_]+__", html, re.I):
        issues.append("Remove any leftover __TOKEN__ placeholders.")
    try:
        from playwright.sync_api import sync_playwright
        import tempfile
        import os
        with tempfile.NamedTemporaryFile("w", suffix=".html", dir=str(folder), delete=False, encoding="utf-8") as fh:
            fh.write(html)
            tmp = fh.name
        try:
            with sync_playwright() as pw:
                br = pw.chromium.launch(args=["--no-sandbox"])
                errs = []
                pg = br.new_page(viewport={"width": 390, "height": 840})
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                pg.goto("file://" + tmp, wait_until="load", timeout=30000)
                pg.wait_for_timeout(900)
                sw = pg.evaluate("document.documentElement.scrollWidth")
                txt = pg.evaluate("document.body.innerText.length")
                br.close()
            if sw and sw > 404:
                issues.append(f"The page scrolls sideways on a phone (content {sw}px wide in a 390px screen). "
                              "Make it fit 390px with no horizontal scroll.")
            if txt is not None and txt < 200:
                issues.append("The page has almost no visible text - fill in the real sections and copy.")
            if errs:
                issues.append("Fix this JavaScript error so animations run: " + errs[0][:160])
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass
    except Exception:
        pass   # no browser here: trust the model, hybrid remains the safety net
    return issues
