"""Web Studio pitch video: a 30-second cinematic walkthrough of a site Trillion built, to send with the pitch.

Takes desktop and phone screenshots of the finished site, lays them out as an animated HTML composition (title card,
browser walkthrough, phone walkthrough, scores or features, call to action) and renders it to MP4 with HyperFrames
(github.com/heygen-com/hyperframes, Apache-2.0). If the Kokoro voice is ready, a spoken voice-over is added for free.
The finished file is saved next to the site as pitch.mp4.
"""
import html
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import core

HF = "hyperframes@0.8.189"
W, H = 1920, 1080
JOBS = {}                       # site id -> {"status", "step", "error", "file"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FEATURE_TEXT = {"whatsapp": "WhatsApp button on every page", "form": "Enquiry form straight to your inbox",
                "booking": "Online bookings", "promo": "Promotions banner you can change any time",
                "pricelist": "Price list your customers can read on their phone"}


def _e(s):
    return html.escape(str(s or ""), quote=True)


def _browser_exe():
    """A real Chrome/Edge executable on this PC (Edge ships with Windows), for HyperFrames and as a launch fallback.
    Unlike extend._find_chrome (which returns '' when Chrome is installed, for Lighthouse), this returns the path."""
    import glob as _glob
    cands = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        cands += [Path(base, "Google", "Chrome", "Application", "chrome.exe"),
                  Path(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    for p in cands:
        try:
            if p.exists():
                return str(p)
        except Exception:
            pass
    for root in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
                 str(Path.home() / ".cache" / "ms-playwright")):
        hits = sorted(_glob.glob(os.path.join(root, "chromium-*", "chrome-win*", "chrome.exe")) +
                      _glob.glob(os.path.join(root, "chromium-*", "chrome-linux*", "chrome")), reverse=True)
        if hits:
            return hits[0]
    return ""


def _find_browser():
    """HyperFrames needs a Chrome-family browser: prefer a real installed Chrome/Edge, else whatever extend finds."""
    exe = _browser_exe()
    if exe:
        return exe
    try:
        import extend
        return extend._find_chrome()
    except Exception:
        return ""


def _launch(p, args):
    """Launch a browser that's actually on the PC — a real Chrome/Edge binary, then Edge/Chrome by channel
    (Edge ships with Windows), and only then Playwright's bundled Chromium (often not downloaded)."""
    exe = _browser_exe()
    if exe:
        try:
            return p.chromium.launch(executable_path=exe, args=args)
        except Exception:
            pass
    for ch in ("msedge", "chrome"):
        try:
            return p.chromium.launch(channel=ch, args=args)
        except Exception:
            pass
    return p.chromium.launch(args=args)


def _env():
    env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1", DO_NOT_TRACK="1", HYPERFRAMES_NO_UPDATE_CHECK="1",
               HYPERFRAMES_SKIP_SKILLS="1")
    try:
        import video
        ff = video.find_ffmpeg()
        if ff:
            env["HYPERFRAMES_FFMPEG_PATH"] = env["FFMPEG_PATH"] = ff
            env["PATH"] = str(Path(ff).parent) + os.pathsep + env.get("PATH", "")
    except Exception:
        pass
    br = _find_browser()
    if br:
        env["HYPERFRAMES_BROWSER_PATH"] = br
    return env


# ---------- 1. screenshots of the finished site ----------
SHOW_ALL = ("[data-aos]{opacity:1!important;transform:none!important;transition:none!important}"
            ".word{opacity:1!important;transform:none!important}")


def shots(site_html, out_dir):
    """Full-page desktop and phone pictures of the site (scroll-reveal animations switched on first)."""
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = _launch(p, ["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        try:
            for name, vp, scale in (("desktop", {"width": 1440, "height": 900}, 1),
                                    ("phone", {"width": 390, "height": 844}, 2)):
                pg = b.new_page(viewport=vp, device_scale_factor=scale)
                pg.goto(Path(site_html).as_uri(), wait_until="load", timeout=60000)
                pg.add_style_tag(content=SHOW_ALL)
                pg.wait_for_timeout(2600)                          # the hero animation settles
                h = min(pg.evaluate("document.documentElement.scrollHeight"), 5200 if name == "desktop" else 7000)
                path = Path(out_dir) / f"{name}.png"
                pg.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": vp["width"], "height": h}, full_page=True)
                out[name] = {"file": path.name, "w": vp["width"] * scale, "h": h * scale}
                pg.close()
        finally:
            b.close()
    return out


# ---------- 2. the composition ----------
def composition(d):
    """d: business, category, location, palette (C_ keys), desktop/phone shot info, features, scores, sender, contact,
    voice (file name or "")."""
    pal = d["palette"]
    bg, acc, acc2, glow, text = pal["C_BG"], pal["C_ACCENT"], pal["C_ACCENT2"], pal["C_GLOW"], "#f5f5f8"
    if pal.get("mode") == "light":                       # light sites still get a dark, cinematic film around them
        bg, glow = "#0b0d14", acc
    dk, ph = d["desktop"], d["phone"]
    # browser frame shows 1440x900 of the page; scroll the rest
    frame_w, frame_h = 1400, 790
    dk_scale = frame_w / dk["w"]
    dk_scroll = max(0, dk["h"] * dk_scale - (frame_h - 44))
    ph_w, ph_h = 340, 700                                  # phone screen area
    ph_scale = ph_w / ph["w"]
    ph_scroll = max(0, ph["h"] * ph_scale - ph_h)
    feats = d["features"][:4]
    sc = d.get("scores")
    if sc:
        rows = "".join(
            f'<div class="srow"><span class="sl">{_e(k)}</span><span class="old" data-to="{int(v[0])}">0</span>'
            f'<span class="arrow">→</span><span class="new" data-to="{int(v[1])}">0</span></div>'
            for k, v in sc.items())
        s4 = (f'<div class="kick">Speed and quality, scored by Google Lighthouse</div>'
              f'<div class="shead"><span></span><span>Your site today</span><span></span><span>Your new site</span></div>{rows}')
    else:
        s4 = ('<div class="kick">Everything included</div><ul class="inc">' +
              "".join(f"<li>{_e(f)}</li>" for f in (feats or ["Fast on every phone", "Click to call",
                                                               "Your Google reviews", "Ready to go live this week"])) + "</ul>")
    voice = (f'<audio id="vo" class="clip" src="{_e(d["voice"])}" data-start="0" data-duration="30" '
             f'data-track-index="9"></audio>') if d.get("voice") else ""
    sender = " · ".join(x for x in (d.get("sender"), d.get("contact")) if x)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width={W}, height={H}" />
<script src="gsap.min.js"></script>
<style>
:root{{--bg:{bg};--a:{acc};--b:{acc2};--g:{glow};--t:{text}}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:var(--bg);font-family:"Segoe UI",Inter,Arial,sans-serif;color:var(--t)}}
#root{{position:relative;width:100%;height:100%;overflow:hidden}}
.clip{{position:absolute;inset:0}}
.bgglow{{position:absolute;inset:-20%;background:radial-gradient(45% 45% at 70% 35%,var(--g),transparent 70%),radial-gradient(40% 40% at 20% 80%,color-mix(in srgb,var(--a) 35%,transparent),transparent 70%)}}
.grad{{background:linear-gradient(100deg,var(--a),var(--b));-webkit-background-clip:text;background-clip:text;color:transparent}}
.center{{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
.eyebrow{{font-size:26px;letter-spacing:.22em;text-transform:uppercase;opacity:.75}}
.big{{font-size:132px;font-weight:800;letter-spacing:-.04em;line-height:1;margin:34px 0 26px;max-width:1600px}}
.sub{{font-size:40px;opacity:.85}}
.browser{{position:absolute;left:{(W - frame_w) // 2}px;top:150px;width:{frame_w}px;height:{frame_h}px;border-radius:22px;overflow:hidden;background:#111;box-shadow:0 60px 140px -30px rgba(0,0,0,.8),0 0 0 1px rgba(255,255,255,.08)}}
.bar{{height:44px;background:#1b1c22;display:flex;align-items:center;gap:10px;padding:0 18px}}
.bar i{{width:13px;height:13px;border-radius:50%;background:#3a3b44;display:block}}
.url{{margin-left:22px;height:26px;border-radius:13px;background:#2a2b33;color:#9a9aa6;font-size:15px;display:flex;align-items:center;padding:0 16px}}
.view{{position:absolute;top:44px;left:0;right:0;bottom:0;overflow:hidden}}
.view img{{width:100%;display:block}}
.cap{{position:absolute;left:0;right:0;top:56px;text-align:center;font-size:40px;font-weight:700;letter-spacing:-.02em}}
.phone{{position:absolute;right:250px;top:{(H - ph_h - 40) // 2}px;width:{ph_w + 28}px;height:{ph_h + 40}px;border-radius:58px;background:#0c0c10;padding:20px 14px;box-shadow:0 60px 140px -30px rgba(0,0,0,.85),0 0 0 2px rgba(255,255,255,.12)}}
.pscreen{{width:{ph_w}px;height:{ph_h}px;border-radius:40px;overflow:hidden;position:relative}}
.pscreen img{{width:100%;display:block}}
.ptext{{position:absolute;left:200px;top:300px;width:900px}}
.ptext h2{{font-size:92px;font-weight:800;letter-spacing:-.035em;line-height:1.02;margin-bottom:40px}}
.ptext li{{list-style:none;font-size:36px;margin:20px 0;display:flex;gap:18px;align-items:center}}
.ptext li:before{{content:"";width:14px;height:14px;border-radius:50%;background:var(--a);box-shadow:0 0 18px var(--a);flex:none}}
.kick{{font-size:30px;letter-spacing:.16em;text-transform:uppercase;opacity:.75;margin-bottom:56px}}
.shead,.srow{{display:grid;grid-template-columns:360px 260px 120px 260px;align-items:center;text-align:center}}
.shead{{font-size:26px;opacity:.7;margin-bottom:14px}}
.srow{{font-size:96px;font-weight:800;margin:6px 0}}.sl{{font-size:38px;font-weight:600;text-align:left}}
.old{{color:#ff6b6b}}.new{{color:var(--a)}}.arrow{{font-size:60px;opacity:.5}}
.inc li{{list-style:none;font-size:48px;font-weight:600;margin:22px 0}}
.inc li:before{{content:"✓  ";color:var(--a)}}
.cta{{font-size:112px;font-weight:800;letter-spacing:-.04em;line-height:1.02;max-width:1500px}}
.who{{margin-top:44px;font-size:40px;opacity:.85}}
.pill{{display:inline-block;margin-top:46px;padding:22px 46px;border-radius:60px;background:linear-gradient(100deg,var(--a),var(--b));color:#fff;font-size:36px;font-weight:700}}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="30" data-width="{W}" data-height="{H}">
 <div class="bgglow" id="glow"></div>
 <section id="s1" class="clip center" data-start="0" data-duration="3.6" data-track-index="0">
  <div class="eyebrow" id="s1e">Website concept · {_e(d.get('category'))}{(' · ' + _e(d['location'])) if d.get('location') else ''}</div>
  <div class="big" id="s1t"><span class="grad">{_e(d['business'])}</span></div>
  <div class="sub" id="s1s">A new website, ready to go live</div>
 </section>
 <section id="s2" class="clip" data-start="3.6" data-duration="11.4" data-track-index="1">
  <div class="cap" id="s2c">Built to win customers the moment they land</div>
  <div class="browser" id="s2b"><div class="bar"><i></i><i></i><i></i><div class="url">Preview · {_e(d['business'])}</div></div>
   <div class="view"><img id="s2i" src="{_e(dk['file'])}" style="width:{frame_w}px" /></div></div>
 </section>
 <section id="s3" class="clip" data-start="15" data-duration="8" data-track-index="2">
  <div class="ptext" id="s3t"><h2>Made for <span class="grad">phones</span> first</h2><ul>{''.join(f'<li>{_e(f)}</li>' for f in (feats or ['Loads fast on mobile data', 'Tap to call', 'Your Google reviews']))}</ul></div>
  <div class="phone" id="s3p"><div class="pscreen"><img id="s3i" src="{_e(ph['file'])}" /></div></div>
 </section>
 <section id="s4" class="clip center" data-start="23" data-duration="3.8" data-track-index="3"><div id="s4w">{s4}</div></section>
 <section id="s5" class="clip center" data-start="26.8" data-duration="3.2" data-track-index="4">
  <div class="cta" id="s5t">Ready to go live <span class="grad">this week</span></div>
  {f'<div class="who" id="s5w">{_e(sender)}</div>' if sender else ''}
  <div class="pill" id="s5p">Reply to see it live</div>
 </section>
 {voice}
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#glow",{{scale:1,rotation:0}},{{scale:1.25,rotation:8,duration:30,ease:"none"}},0);
tl.fromTo("#s1e",{{opacity:0,y:30}},{{opacity:.75,y:0,duration:.8,ease:"power3.out"}},.2)
  .fromTo("#s1t",{{opacity:0,y:60,filter:"blur(14px)"}},{{opacity:1,y:0,filter:"blur(0px)",duration:1.1,ease:"power4.out"}},.35)
  .fromTo("#s1s",{{opacity:0,y:24}},{{opacity:.85,y:0,duration:.8,ease:"power3.out"}},.9)
  .to("#s1",{{opacity:0,duration:.45}},3.15);
tl.fromTo("#s2b",{{opacity:0,y:120,scale:.94}},{{opacity:1,y:0,scale:1,duration:1.1,ease:"power3.out"}},3.6)
  .fromTo("#s2c",{{opacity:0,y:-20}},{{opacity:1,y:0,duration:.8}},3.9)
  .fromTo("#s2i",{{y:0}},{{y:-{dk_scroll:.0f},duration:8.6,ease:"power1.inOut"}},5.4)
  .to("#s2",{{opacity:0,duration:.45}},14.55);
tl.fromTo("#s3p",{{opacity:0,x:200,rotation:4}},{{opacity:1,x:0,rotation:0,duration:1.1,ease:"power3.out"}},15)
  .fromTo("#s3t",{{opacity:0,x:-60}},{{opacity:1,x:0,duration:.9,ease:"power3.out"}},15.3)
  .fromTo("#s3t li",{{opacity:0,x:-30}},{{opacity:1,x:0,duration:.5,stagger:.25}},16)
  .fromTo("#s3i",{{y:0}},{{y:-{ph_scroll:.0f},duration:5.6,ease:"power1.inOut"}},16.4)
  .to("#s3",{{opacity:0,duration:.45}},22.55);
tl.fromTo("#s4w",{{opacity:0,scale:.96}},{{opacity:1,scale:1,duration:.8,ease:"power3.out"}},23)
  .fromTo("#s4w .inc li",{{opacity:0,y:20}},{{opacity:1,y:0,duration:.45,stagger:.18}},23.3);
document.querySelectorAll("#s4w [data-to]").forEach(function(el){{var o={{v:0}};
  tl.to(o,{{v:+el.dataset.to,duration:1.4,ease:"power2.out",onUpdate:function(){{el.textContent=Math.round(o.v);}}}},23.4);}});
tl.to("#s4",{{opacity:0,duration:.4}},26.4);
tl.fromTo("#s5t",{{opacity:0,y:50,filter:"blur(10px)"}},{{opacity:1,y:0,filter:"blur(0px)",duration:1,ease:"power4.out"}},26.9)
  .fromTo("#s5 .who",{{opacity:0}},{{opacity:.85,duration:.6}},27.5)
  .fromTo("#s5p",{{opacity:0,y:20}},{{opacity:1,y:0,duration:.6,ease:"back.out(2)"}},27.8);
window.__timelines["main"] = tl;
tl.seek(0);
</script></body></html>"""


def script_for(d):
    """The voice-over, timed to the scenes."""
    b = d["business"]
    feats = d["features"][:2]
    lines = [f"Here's a new website for {b}.",
             "It's built to win customers the moment they land, and looks the part on every screen.",
             "It's made for phones first" + (", with " + " and ".join(f.lower() for f in feats) if feats else "") + ".",
             ("Google scores it far higher than the site you have today." if d.get("scores")
              else "Everything's included, and it's ready to go live this week."),
             "Reply, and it can be live this week."]
    return " ".join(lines)


# ---------- 3. render ----------
def make(user, sid, scores=None, voice=True, progress=None):
    import web
    say = progress or (lambda t: None)
    row = web._row(user, sid)
    if not row:
        return {"error": f"I couldn't find website #{sid}."}
    folder = Path(row["folder"])
    det = json.loads(row["detail"] or "{}")
    info = det.get("info") or {}
    pal = dict(web.PALETTES.get(det.get("style") or "violet", web.PALETTES["violet"]))
    work = folder / "pitch-video"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    say("Taking pictures of the site")
    sh = shots(folder / "index.html", work)
    s = core.settings(user)
    d = {"business": row["business"] or row["title"], "category": info.get("category") or row["category"],
         "location": info.get("location") or "", "palette": pal, "desktop": sh["desktop"], "phone": sh["phone"],
         "features": [FEATURE_TEXT[a] for a in (det.get("addons") or []) if a in FEATURE_TEXT] +
                     (["Your Google reviews on show"] if info.get("reviews") else []) + ["Tap to call"],
         "scores": scores or None, "sender": (dict(user).get("name") or "").strip(),
         "contact": s.get("phone_own_number") or "", "voice": ""}
    if voice:
        try:
            import localvoice
            say("Recording the voice-over")
            samples, sr = localvoice.synth_wait(script_for(d), s.get("kokoro_voice") or localvoice.DEFAULT_VOICE)
            if len(samples):
                (work / "voice.wav").write_bytes(localvoice.wav_bytes(samples, sr))
                d["voice"] = "voice.wav"
        except Exception:
            pass                                   # no voice yet: the film works without it
    shutil.copy2(core.BASE / "vendor" / "gsap" / "gsap.min.js", work / "gsap.min.js")   # no CDN needed to render
    (work / "index.html").write_text(composition(d), encoding="utf-8")
    (work / "hyperframes.json").write_text(json.dumps({"paths": {"assets": "."}}), encoding="utf-8")
    say("Rendering the video")
    out = folder / "pitch.mp4"
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    r = subprocess.run([npx, "-y", HF, "render", "-o", str(out), "--quiet"], cwd=str(work), env=_env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                       creationflags=NO_WINDOW)
    if r.returncode != 0 or not out.exists():
        tail = " | ".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-4:])
        return {"error": f"The video did not render: {tail}"[:500]}
    return {"ok": True, "file": str(out), "seconds": 30, "voice": bool(d["voice"])}


def start(user, sid, scores=None, voice=True):
    sid = int(sid)
    if JOBS.get(sid, {}).get("status") == "running":
        return {"status": "running"}
    JOBS[sid] = {"status": "running", "step": "Starting", "error": "", "file": ""}

    def go():
        try:
            r = make(user, sid, scores, voice, progress=lambda t: JOBS[sid].update(step=t))
            JOBS[sid].update(status="error" if r.get("error") else "done", error=r.get("error", ""),
                             file=r.get("file", ""), step="Done" if not r.get("error") else "Failed")
            core.log(user["id"], (f"Pitch video ready for site #{sid}" if not r.get("error")
                                  else f"Pitch video for site #{sid} failed: {r['error']}")[:400],
                     "done" if not r.get("error") else "waiting")
        except Exception as e:
            JOBS[sid].update(status="error", error=f"{type(e).__name__}: {e}"[:400], step="Failed")
    threading.Thread(target=go, daemon=True).start()
    return {"status": "running"}


def status(user, sid):
    sid = int(sid)
    j = dict(JOBS.get(sid) or {})
    import web
    row = web._row(user, sid)
    if row and (Path(row["folder"]) / "pitch.mp4").exists() and j.get("status") != "running":
        j.update(status="done", file=str(Path(row["folder"]) / "pitch.mp4"))
    return j or {"status": "none"}
