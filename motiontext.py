"""Animated title & branding overlays for videos - the "write HTML, render MP4" idea (like HeyGen HyperFrames),
done natively with the headless Chrome (Playwright) and FFmpeg that Trillion already uses. No extra install.

It renders a transparent, animated title card (headline + subtitle + accent wipe) frame by frame in a headless
browser, then FFmpeg burns it onto a scene clip. This is the premium, motion-graphics replacement for the flat
text that video.render stamps on. Any failure returns None so the caller keeps the plain clip and the old static
text path still works.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import video   # for SIZES, FPS and find_ffmpeg

FPS = video.FPS
ACCENT = "#49d6e6"   # the cyan the user signed off on; override per brand


def available():
    """True only if the headless browser is installed on this PC (same one the site screenshots use)."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _html(headline, subtitle, accent, w, h, seconds):
    """A transparent, animated lower-third sized to the video. Titles in early, holds, fades out near the end."""
    out_at = max(1.6, seconds - 0.6)
    head_px = int(h * 0.058)
    sub_px = int(h * 0.021)
    # headline can be two lines; keep it punchy
    head = _esc(headline).upper()
    sub = _esc(subtitle)
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{{margin:0;width:{w}px;height:{h}px;background:transparent;overflow:hidden;
    font-family:'Segoe UI','Arial Black',Arial,'Liberation Sans','DejaVu Sans',sans-serif;-webkit-font-smoothing:antialiased}}
  .wrap{{position:absolute;left:{int(w*0.06)}px;bottom:{int(h*0.16)}px;width:{int(w*0.84)}px}}
  .bar{{width:{int(w*0.11)}px;height:{max(5,int(h*0.004))}px;background:{accent};transform-origin:left;border-radius:4px;
    animation:wipe .6s .3s cubic-bezier(.2,.8,.2,1) both}}
  .head{{margin:{int(h*0.014)}px 0 0;color:#fff;font-weight:800;font-size:{head_px}px;line-height:.98;
    letter-spacing:-1px;text-shadow:0 6px 40px rgba(0,0,0,.55);animation:rise .6s .5s cubic-bezier(.2,.8,.2,1) both}}
  .sub{{margin:{int(h*0.012)}px 0 0;color:#eaf6f8;font-weight:500;font-size:{sub_px}px;opacity:.92;
    text-shadow:0 3px 20px rgba(0,0,0,.5);animation:rise .6s .9s cubic-bezier(.2,.8,.2,1) both}}
  .all{{animation:out .6s {out_at:.2f}s ease both}}
  @keyframes wipe{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
  @keyframes rise{{from{{opacity:0;transform:translateY({int(h*0.024)}px)}}to{{opacity:1;transform:translateY(0)}}}}
  @keyframes out{{from{{opacity:1}}to{{opacity:0}}}}
</style></head><body><div class="all"><div class="wrap">
  <div class="bar"></div><h1 class="head">{head}</h1>{sub_html}
</div></div></body></html>"""


def _render_frames(headline, subtitle, accent, w, h, seconds, folder):
    """Render transparent PNG frames of the animated title. Returns frame count, or 0 on failure."""
    from playwright.sync_api import sync_playwright
    n = max(1, int(round(seconds * FPS)))
    html = _html(headline, subtitle, accent, w, h, seconds)
    hp = folder / "title.html"
    hp.write_text(html, encoding="utf-8")
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--no-sandbox"])
        pg = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        pg.goto("file://" + str(hp))
        pg.evaluate("() => document.getAnimations().forEach(a => a.pause())")
        for i in range(n):
            t = i / FPS * 1000.0
            pg.evaluate("(ms) => Promise.all(document.getAnimations().map(a => { a.currentTime = ms; return a.ready; }))", t)
            pg.screenshot(path=str(folder / f"f{i:04d}.png"), omit_background=True)
        br.close()
    return n


def title_clip(src_clip, out_clip, headline, subtitle="", accent=ACCENT, seconds=5.0, fmt="vertical", progress=None):
    """Burn an animated title onto src_clip -> out_clip. Returns out_clip on success, None on any failure
    (caller then keeps the untitled clip). Idempotent: if out_clip already exists it is reused."""
    say = progress or (lambda t: None)
    if os.path.exists(out_clip) and os.path.getsize(out_clip) > 2000:
        return out_clip                      # already made - resume without re-rendering
    if not available() or not os.path.exists(src_clip):
        return None
    if not str(headline or "").strip():
        return None                          # nothing to show; keep the plain clip
    w, h = video.SIZES.get(fmt, video.SIZES["vertical"])
    try:
        seconds = max(2.0, float(seconds or 5))
    except (TypeError, ValueError):
        seconds = 5.0
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        try:
            say("Animating the title")
            n = _render_frames(headline, subtitle, accent, w, h, seconds, folder)
            if not n:
                return None
        except Exception:
            return None
        say("Burning the title onto the clip")
        vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setpts=PTS-STARTPTS[bg];"
              f"[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
        cmd = [video.find_ffmpeg(), "-y", "-v", "error", "-i", str(src_clip),
               "-framerate", str(FPS), "-i", str(folder / "f%04d.png"),
               "-filter_complex", vf, "-map", "[v]", "-an", "-r", str(FPS),
               "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(out_clip)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600)
        except Exception:
            return None
    if r.returncode == 0 and os.path.exists(out_clip) and os.path.getsize(out_clip) > 2000:
        return out_clip
    return None
