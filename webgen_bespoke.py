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
    "You are a world-class web designer who ships sites at the level of Awwwards and the motion.dev template "
    "gallery. You write ONE complete, self-contained HTML document and nothing else.\n"
    "NON-NEGOTIABLE RULES:\n"
    "- Output only the HTML document (<!doctype html> ... </html>). No explanation, no markdown fence.\n"
    "- All CSS in one <style> tag in the head. All JS in one <script> tag before </body>.\n"
    "- Fonts: you MAY use Google Fonts via <link href=\"https://fonts.googleapis.com/...\">. Pick a characterful "
    "display face + a clean body face that SUIT THIS SPECIFIC BUSINESS; never reuse a default pairing.\n"
    "- Animation: you MAY load GSAP and/or AOS from https://cdnjs.cloudflare.com only. Use motion with restraint "
    "- a hero entrance and scroll reveals from a VISIBLE resting state (never leave content stuck at opacity:0).\n"
    "- IMAGES: use ONLY the exact photo filenames provided, as <img src=\"filename.jpg\">. Do NOT invent image "
    "URLs or hotlink stock - any other image host will be blank. If no photos are given, design without photos "
    "(gradients, type, SVG, canvas).\n"
    "- Give THIS site its own identity: a palette, type and layout no other site would have. Pick a colour "
    "scheme that fits the trade and mood - avoid the cliché purple-to-blue gradient on white.\n"
    "- Real copy from the brief - the business's real name, services, hours, contact. Never lorem ipsum, never "
    "a leftover placeholder or a __TOKEN__.\n"
    "- Responsive: must work at 400px wide with NO horizontal scroll; stack to one column on phones; images "
    "max-width:100%.\n"
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
    return html if _looks_like_page(html) else None


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
