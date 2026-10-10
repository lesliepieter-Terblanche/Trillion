"""Trillion video maker: turns a plain request into a finished MP4, with no media supplied.

Steps: the brain writes a short script and scene list; real footage for each scene is fetched from
Pexels (free stock video; needs a free key in Settings); ElevenLabs reads the voice-over (if that
login has a key); then everything is graded, titled, captioned, mixed with music and encoded by
FFmpeg. Without a Pexels key the scenes use abstract light backdrops instead of footage.
"""
import base64, json, re, time, wave

import httpx

import core

import glob, math, os, random, shutil, subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIZES = {"vertical": (1080, 1920), "square": (1080, 1080), "landscape": (1920, 1080)}
FPS = 30
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    for pat in (r"Microsoft\WinGet\Links\ffmpeg.exe", r"Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"):
        hits = glob.glob(os.path.join(local, pat), recursive=True)
        if hits:
            return hits[0]
    raise RuntimeError("FFmpeg was not found. Run '1 - Setup.bat' again, then restart Trillion.")


def _font(size, bold=True):
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = (["bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"])
    cands = [os.path.join(win, n) for n in names] + [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in cands:
        if os.path.exists(c):
            f = ImageFont.truetype(c, size)
            if "bahnschrift" in c.lower():
                try:
                    f.set_variation_by_name("Bold")
                except Exception:
                    pass
            return f
    return ImageFont.load_default()


def _hex(c, default=(255, 180, 84)):
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in str(text).split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:5]


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _abstract_bg(w, h, accent, seed):
    """A moody light-leak backdrop for scenes without a photo."""
    rnd = random.Random(seed)
    W, H = int(w * 1.3), int(h * 1.3)
    small = Image.new("RGB", (W // 8, H // 8), (9, 8, 13))
    d = ImageDraw.Draw(small)
    teal = (40, 190, 200)
    for i in range(7):
        col = accent if i % 2 == 0 else teal
        col = tuple(int(c * rnd.uniform(0.35, 0.8)) for c in col)
        r = rnd.randint(small.width // 6, small.width // 2)
        x, y = rnd.randint(0, small.width), rnd.randint(0, small.height)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col)
    small = small.filter(ImageFilter.GaussianBlur(small.width // 7))
    return small.resize((W, H), Image.BICUBIC)


def _cover(img, w, h, scale=1.3):
    """Resize a photo so it covers the frame with room to move the camera."""
    img = img.convert("RGB")
    k = max(w * scale / img.width, h * scale / img.height)
    return img.resize((max(w, int(img.width * k)), max(h, int(img.height * k))), Image.LANCZOS)


def _camera(base, w, h, t, move):
    """Slow push-in or pull-out with a drift (the Ken Burns move)."""
    z0, z1, dx, dy = move
    z = z0 + (z1 - z0) * t
    cw, ch = int(w * z), int(h * z)
    cw, ch = min(cw, base.width), min(ch, base.height)
    cx = (base.width - cw) / 2 + dx * (base.width - cw) / 2 * (t * 2 - 1)
    cy = (base.height - ch) / 2 + dy * (base.height - ch) / 2 * (t * 2 - 1)
    box = (int(cx), int(cy), int(cx) + cw, int(cy) + ch)
    return base.resize((w, h), Image.BILINEAR, box=box)


def _overlays(w, h):
    grad = Image.new("L", (1, h))
    for y in range(h):
        t = y / h
        grad.putpixel((0, y), int(255 * (0.20 + 0.62 * max(0.0, (t - 0.35) / 0.65) ** 1.4)))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade.putalpha(grad.resize((w, h)))
    vig = Image.new("L", (w // 8, h // 8), 0)
    ImageDraw.Draw(vig).ellipse([-w // 40, -h // 40, w // 8 + w // 40, h // 8 + h // 40], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(w // 28)).resize((w, h), Image.BICUBIC)
    vig = Image.eval(vig, lambda v: 110 + int(v * 145 / 255))
    vig = Image.merge("RGB", (vig, vig, vig))
    grains = []
    for i in range(5):
        n = Image.effect_noise((w // 2, h // 2), 26).resize((w, h), Image.NEAREST)
        g = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        g.putalpha(Image.eval(n, lambda v: max(0, min(255, int(abs(v - 128) * 0.40)))))
        grains.append(g)
    return shade, vig, grains



CACHE = core.DATA / "video_cache"
MUSIC = core.DATA / "music"
END_LEN = 3.0


def init():
    CACHE.mkdir(parents=True, exist_ok=True)
    MUSIC.mkdir(parents=True, exist_ok=True)
    c = core._conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, path TEXT, "
                  "thumb TEXT, seconds REAL, detail TEXT, created REAL)")
        c.commit()
    finally:
        c.close()


def find_ffprobe():
    ff = find_ffmpeg()
    cand = os.path.join(os.path.dirname(ff), "ffprobe" + (".exe" if ff.lower().endswith(".exe") else ""))
    return cand if os.path.exists(cand) else (shutil.which("ffprobe") or "ffprobe")


def media_seconds(path):
    try:
        out = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


# ---------- 1. the script ----------
def plan(llm, user, prompt, seconds, n_scenes, with_voice):
    s = core.settings(user)
    words = int((seconds - END_LEN) * 2.3)
    ask = f"""Write a {seconds}-second cinematic social media video for this request: "{prompt}"
Brand: {s.get('brand') or 'none'}. Website: {s.get('site') or 'none'}.
Reply with JSON only, in exactly this shape:
{{"title": "short title", "scenes": [{{"search": "2 to 4 plain words describing generic stock footage for this moment, no brand names",
"headline": "at most 5 punchy words", "caption": "one short supporting sentence"}}],
"narration": "a voice-over of about {words} words for the whole video, warm and confident, no hashtags"}}
Give exactly {n_scenes} scenes. English only."""
    data = {}
    try:
        text = (llm(user, [{"role": "system", "content": "You are a film director who writes tight social video scripts. Reply with JSON only."},
                           {"role": "user", "content": ask}]).get("content") or "")
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        data = {}
    scenes = [x for x in data.get("scenes", []) if isinstance(x, dict) and x.get("headline")][:n_scenes]
    if not scenes:  # the brain did not give usable JSON: fall back to a plain structure
        topic = re.sub(r"\b(create|make|generate|produce|me|a|an|the|video|cinematic|premium|social|media|second|seconds|for|\d+)\b",
                       " ", prompt, flags=re.I)
        topic = re.sub(r"\s+", " ", topic).strip() or (s.get("brand") or "Your story")
        scenes = [{"search": topic, "headline": topic[:40], "caption": ""}] * n_scenes
        data = {"title": topic[:50], "narration": ""}
    for sc in scenes:
        sc["search"] = re.sub(r"[^\w\s-]", "", str(sc.get("search") or sc["headline"]))[:60]
        sc["headline"], sc["caption"] = str(sc["headline"])[:60], str(sc.get("caption") or "")[:140]
    narration = re.sub(r"\s+", " ", str(data.get("narration") or "")).strip() if with_voice else ""
    return {"title": str(data.get("title") or prompt)[:70], "scenes": scenes, "narration": narration}


# ---------- 2. footage ----------
def pexels_clip(key, query, w, h, need_seconds, used):
    """Find and download one stock clip. Returns (local path, credit) or (None, None)."""
    orient = "portrait" if h > w else ("landscape" if w > h else "square")
    try:
        r = httpx.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=25,
                      params={"query": query, "orientation": orient, "per_page": 8, "size": "medium"})
        r.raise_for_status()
        vids = r.json().get("videos", [])
    except Exception:
        return None, None
    vids = [v for v in vids if v.get("id") not in used and v.get("duration", 0) >= need_seconds] or \
           [v for v in vids if v.get("id") not in used]
    for v in vids:
        files = [f for f in v.get("video_files", []) if f.get("link") and (f.get("file_type") or "").endswith("mp4") and f.get("height")]
        if not files:
            continue
        files.sort(key=lambda f: (abs(f["height"] - h), f["height"] < h))  # closest to the frame, not below it
        f = files[0]
        path = CACHE / f"pexels-{v['id']}-{f['height']}.mp4"
        if not path.exists():
            try:
                with httpx.stream("GET", f["link"], timeout=120, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as out:
                        for chunk in resp.iter_bytes(1 << 16):
                            out.write(chunk)
            except Exception:
                path.unlink(missing_ok=True)
                continue
        used.add(v["id"])
        return str(path), (v.get("user") or {}).get("name", "Pexels")
    return None, None


def pixabay_clip(key, query, w, h, need_seconds, used):
    """Same job as pexels_clip, from Pixabay (free key, no orientation filter: clips are cropped to fit)."""
    try:
        r = httpx.get("https://pixabay.com/api/videos/", timeout=25,
                      params={"key": key, "q": query[:100], "per_page": 10, "video_type": "film", "safesearch": "true"})
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception:
        return None, None
    hits = [v for v in hits if v.get("id") not in used and v.get("duration", 0) >= need_seconds] or \
           [v for v in hits if v.get("id") not in used]
    for v in hits:
        sizes = [f for f in (v.get("videos") or {}).values() if f.get("url") and f.get("height")]
        if not sizes:
            continue
        # tall output crops the middle of a wide clip, so it needs more source height
        want = h if w >= h else min(1080, h)
        sizes.sort(key=lambda f: (f["height"] > 1440, abs(f["height"] - want)))
        f = sizes[0]
        path = CACHE / f"pixabay-{v['id']}-{f['height']}.mp4"
        if not path.exists():
            try:
                with httpx.stream("GET", f["url"], timeout=180, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as out:
                        for chunk in resp.iter_bytes(1 << 16):
                            out.write(chunk)
            except Exception:
                path.unlink(missing_ok=True)
                continue
        used.add(v["id"])
        return str(path), v.get("user") or "Pixabay"
    return None, None


def clip_frames(path, dur, w, h):
    """Decode a stock clip to graded frames at the output size."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},"
          "eq=contrast=1.08:saturation=1.12:brightness=-0.02,"
          "colorbalance=rs=-0.05:bs=0.07:rh=0.06:bh=-0.07,vignette=PI/5")
    p = subprocess.Popen([find_ffmpeg(), "-v", "error", "-stream_loop", "-1", "-i", path, "-t", f"{dur + 0.3:.2f}", "-vf", vf,
                          "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            yield Image.frombytes("RGB", (w, h), b)
    finally:
        p.kill()


# ---------- 3. voice and music ----------
def voice_over(key, voice_id, text, out_path):
    """ElevenLabs speech with per-character timings (for captions). Returns [(start, end, words)]."""
    body = {"text": text, "model_id": "eleven_multilingual_v2"}
    try:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps", headers={"xi-api-key": key},
                       json=body, timeout=120)
        r.raise_for_status()
        j = r.json()
        open(out_path, "wb").write(base64.b64decode(j["audio_base64"]))
        al = j.get("alignment") or {}
        return caption_chunks(al.get("characters") or [], al.get("character_start_times_seconds") or [],
                              al.get("character_end_times_seconds") or [])
    except Exception:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", headers={"xi-api-key": key},
                       json=body, timeout=120)
        r.raise_for_status()
        open(out_path, "wb").write(r.content)
        return None


def caption_chunks(chars, starts, ends):
    words, cur, ws = [], "", None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((ws, b, cur))
                cur, ws = "", None
        else:
            if not cur:
                ws = a
            cur += ch
            last = b
    if cur:
        words.append((ws, last, cur))
    chunks, group = [], []
    for w in words:
        group.append(w)
        text = " ".join(x[2] for x in group)
        if len(group) >= 5 or len(text) >= 26 or w[2][-1:] in ".!?":
            chunks.append((group[0][0], group[-1][1], text))
            group = []
    if group:
        chunks.append((group[0][0], group[-1][1], " ".join(x[2] for x in group)))
    return chunks


def even_chunks(text, duration):
    """Fallback caption timing when the voice service gives no timings."""
    words = text.split()
    groups = [" ".join(words[i:i + 4]) for i in range(0, len(words), 4)]
    total = sum(len(g) for g in groups) or 1
    out, t = [], 0.0
    for g in groups:
        d = duration * len(g) / total
        out.append((t, t + d, g))
        t += d
    return out


def ambient_track():
    """A simple original ambient bed, generated once, so videos always have music that is safe to post."""
    path = MUSIC / "trillion-ambient.wav"
    if path.exists():
        return str(path)
    import numpy as np
    sr, bar = 44100, 8.0
    chords = [(110.0, 164.81, 220.0, 246.94, 329.63), (87.31, 130.81, 174.61, 261.63, 349.23),
              (130.81, 196.0, 261.63, 329.63, 392.0), (98.0, 146.83, 196.0, 293.66, 392.0)]
    n = int(sr * bar)
    t = np.arange(n) / sr
    env = np.clip(np.minimum(t / 2.0, (bar - t) / 2.0), 0, 1) ** 1.5
    left, right = np.zeros(n * len(chords) * 2), np.zeros(n * len(chords) * 2)
    for rep in range(2):
        for i, ch in enumerate(chords):
            seg_l, seg_r = np.zeros(n), np.zeros(n)
            for k, f in enumerate(ch):
                for harm, amp in ((1, 1.0), (2, 0.28), (3, 0.10)):
                    lfo = 1 + 0.15 * np.sin(2 * np.pi * (0.07 + 0.03 * k) * t + k)
                    seg_l += amp * lfo * np.sin(2 * np.pi * f * harm * 0.998 * t + k)
                    seg_r += amp * lfo * np.sin(2 * np.pi * f * harm * 1.002 * t + k * 1.7)
            pulse = np.sin(2 * np.pi * ch[0] / 2 * t) * (0.5 + 0.5 * np.cos(2 * np.pi * 0.5 * t)) ** 6 * 1.6
            at = (rep * len(chords) + i) * n
            left[at:at + n] += (seg_l + pulse) * env
            right[at:at + n] += (seg_r + pulse) * env
    mix = np.stack([left, right], axis=1)
    hop = int(sr * 0.31)  # one soft echo for space
    mix[hop:] += 0.35 * mix[:-hop]
    mix = (mix / np.abs(mix).max() * 0.7 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(mix.tobytes())
    return str(path)


def pick_music():
    own = [f for f in MUSIC.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg") and f.name != "trillion-ambient.wav"]
    if own:
        return str(random.choice(own)), own[0].name
    return ambient_track(), "built-in ambient"


# ---------- 4. the render ----------
def render(out_path, brand, scenes, website="", accent="#ffb454", fmt="vertical", seconds=20,
           vo_path=None, captions=None, music=None, progress=None):
    w, h = SIZES.get(fmt, SIZES["vertical"])
    accent = _hex(accent)
    body = max(seconds - END_LEN, 4.0)
    per = body / len(scenes)
    seconds = body + END_LEN
    total = int(seconds * FPS)
    margin = int(w * 0.08)
    wide = fmt == "landscape"
    f_head = _font(int(w * (0.062 if wide else 0.100)))
    f_cap = _font(int(w * (0.024 if wide else 0.040)), bold=False)
    f_sub = _font(int(w * (0.028 if wide else 0.046)))
    f_tag = _font(int(w * (0.018 if wide else 0.030)))
    f_brand = _font(int(w * (0.09 if wide else 0.15)))
    shade, vig, grains = _overlays(w, h)
    bar = int(h * (0.045 if fmt == "vertical" else 0.09))
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for i, sc in enumerate(scenes):
        sc["head"] = _wrap(probe, str(sc.get("headline", "")).upper(), f_head, w - 2 * margin)[:3]
        sc["cap"] = [] if captions else _wrap(probe, sc.get("caption", ""), f_cap, w - 2 * margin)[:3]
        if not sc.get("clip"):
            sc["base"] = _abstract_bg(w, h, accent, seed=i + 1)
            sc["move"] = (1.22, 1.0, 0.6, 0.3) if i % 2 == 0 else (1.0, 1.22, -0.6, 0.3)
    end_bg = _abstract_bg(w, h, accent, seed=99)
    head_bottom = int(h * (0.66 if captions else 0.80)) if not wide else int(h * (0.70 if captions else 0.84))

    def text_layer(sc, lt, idx):
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lh, ch = int(f_head.size * 1.06), int(f_cap.size * 1.35)
        block = len(sc["head"]) * lh + (int(h * 0.02) + len(sc["cap"]) * ch if sc["cap"] else 0)
        y = head_bottom - block
        a = _ease(lt / 0.5)
        d.rectangle([margin, y - int(h * 0.03), margin + int(w * 0.16 * _ease(lt / 0.7)), y - int(h * 0.03) + max(4, w // 180)],
                    fill=accent + (255,))
        for j, line in enumerate(sc["head"]):
            la = _ease((lt - 0.12 * j) / 0.55)
            d.text((margin + 3, y + j * lh + int((1 - la) * h * 0.03) + 3), line, font=f_head, fill=(0, 0, 0, int(150 * la)))
            d.text((margin, y + j * lh + int((1 - la) * h * 0.03)), line, font=f_head, fill=(255, 255, 255, int(255 * la)))
        cy = y + len(sc["head"]) * lh + int(h * 0.02)
        ca = _ease((lt - 0.55) / 0.6)
        for j, line in enumerate(sc["cap"]):
            d.text((margin, cy + j * ch), line, font=f_cap, fill=(240, 234, 224, int(255 * ca)))
        if brand:
            d.text((margin, bar + int(h * 0.025)), "  ".join(brand.upper()), font=f_tag, fill=accent + (int(255 * a),))
        return layer

    sub_cache = {}

    def subtitle(t):
        for k, (a, b, text) in enumerate(captions or []):
            if a - 0.05 <= t <= b + 0.25:
                if k not in sub_cache:
                    lines = _wrap(probe, text, f_sub, w - 2 * margin - 40)[:2]
                    lw = max(int(probe.textlength(ln, font=f_sub)) for ln in lines) + 56
                    lhh = int(f_sub.size * 1.3)
                    img = Image.new("RGBA", (lw, lhh * len(lines) + 28), (0, 0, 0, 0))
                    dd = ImageDraw.Draw(img)
                    dd.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=18, fill=(0, 0, 0, 175))
                    for j, ln in enumerate(lines):
                        dd.text(((lw - dd.textlength(ln, font=f_sub)) / 2, 12 + j * lhh), ln, font=f_sub, fill=(255, 255, 255, 255))
                    sub_cache[k] = img
                return sub_cache[k]
        return None

    video_only = str(out_path) + ".video.mp4"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen([find_ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                            "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", video_only],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    cache = {}

    def emit(frame, t, fade):
        nonlocal n
        g = grains[n % len(grains)]
        frame.paste(g, (0, 0), g)
        sub = subtitle(t - 0.4)
        if sub is not None:
            frame.paste(sub, ((w - sub.width) // 2, int(h * (0.78 if not wide else 0.80))), sub)
        if fade < 1.0:
            frame = Image.eval(frame, lambda v, f=max(0.0, fade): int(v * f))
        bh = int(bar * _ease(t / 0.8))
        if bh:
            d = ImageDraw.Draw(frame)
            d.rectangle([0, 0, w, bh], fill=(0, 0, 0))
            d.rectangle([0, h - bh, w, h], fill=(0, 0, 0))
        enc.stdin.write(frame.tobytes())
        n += 1
        if progress and n % 45 == 0:
            progress(int(100 * n / total))

    try:
        for idx, sc in enumerate(scenes):
            frames_needed = int(round((idx + 1) * per * FPS)) - int(round(idx * per * FPS))
            src = clip_frames(sc["clip"], per, w, h) if sc.get("clip") else None
            last = None
            for k in range(frames_needed):
                lt = k / FPS
                frame = None
                if src is not None:
                    frame = next(src, None)
                    if frame is None:
                        frame = last.copy() if last is not None else None
                    else:
                        last = frame.copy()
                if frame is None:
                    if "base" not in sc:
                        sc["base"] = _abstract_bg(w, h, accent, seed=idx + 1)
                        sc["move"] = (1.22, 1.0, 0.6, 0.3)
                    frame = ImageChops.multiply(_camera(sc["base"], w, h, lt / per, sc["move"]), vig)
                frame.paste(shade, (0, 0), shade)
                layer = (cache.get(idx) or cache.setdefault(idx, text_layer(sc, 9.0, idx))) if lt > 1.4 else text_layer(sc, lt, idx)
                frame.paste(layer, (0, 0), layer)
                emit(frame, idx * per + lt, min(1.0, lt / 0.3 if idx == 0 else lt / 0.2 + 0.25, (per - lt) / 0.2 + 0.25))
            if src is not None:
                src.close()
        fb = f_brand
        while probe.textlength(brand.upper(), font=fb) > w - 2 * margin and fb.size > 30:
            fb = _font(int(fb.size * 0.9))
        while n < total:
            lt = n / FPS - body
            frame = ImageChops.multiply(_camera(end_bg, w, h, max(0, lt) / END_LEN, (1.25, 1.05, 0.3, 0.2)), vig)
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            a = _ease(lt / 0.6)
            bw = d.textlength(brand.upper(), font=fb)
            by = int(h * 0.44) - fb.size // 2
            d.text(((w - bw) / 2, by + int((1 - a) * h * 0.02)), brand.upper(), font=fb, fill=(255, 255, 255, int(255 * a)))
            lw = int(w * 0.22 * _ease((lt - 0.3) / 0.6))
            ly = by + int(fb.size * 1.25)
            d.rectangle([(w - lw) / 2, ly, (w + lw) / 2, ly + max(4, w // 180)], fill=accent + (255,))
            if website:
                site = website.replace("https://", "").replace("http://", "").strip("/")
                sa = _ease((lt - 0.6) / 0.6)
                d.text(((w - d.textlength(site, font=f_cap)) / 2, ly + int(h * 0.03)), site, font=f_cap,
                       fill=(240, 234, 224, int(255 * sa)))
            frame.paste(layer, (0, 0), layer)
            emit(frame, n / FPS, min(1.0, lt / 0.35, (END_LEN - lt) / 0.6))
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        if enc.wait() != 0:
            raise RuntimeError("FFmpeg could not write the video: " + err[-300:])
    except BrokenPipeError:
        raise RuntimeError("FFmpeg stopped early: " + enc.stderr.read().decode(errors="replace")[-300:])
    finally:
        if enc.poll() is None:
            enc.kill()

    # sound: voice on top, music underneath
    cmd = [find_ffmpeg(), "-y", "-v", "error", "-i", video_only]
    filt, last_a = [], None
    if vo_path:
        cmd += ["-i", str(vo_path)]
        filt.append("[1:a]adelay=400|400,volume=1.0[vo]")
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        mi = 2 if vo_path else 1
        filt.append(f"[{mi}:a]volume={0.16 if vo_path else 0.55},afade=t=in:d=1.5,afade=t=out:st={seconds - 2.5:.2f}:d=2.5[bg]")
    if vo_path and music:
        filt.append("[bg][vo]amix=inputs=2:duration=first:normalize=0[a]")
        last_a = "[a]"
    elif vo_path:
        last_a = "[vo]"
    elif music:
        last_a = "[bg]"
    if last_a:
        cmd += ["-filter_complex", ";".join(filt), "-map", "0:v", "-map", last_a, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-t", f"{seconds:.2f}", "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        os.unlink(video_only)
    except OSError:
        pass
    if r.returncode != 0:
        raise RuntimeError("FFmpeg could not add the sound: " + (r.stderr or "")[-300:])
    return seconds


# ---------- 5. the whole job ----------
def create(llm, user, prompt, seconds=20, fmt="vertical", voiceover=True, progress=None):
    init()
    s = core.settings(user)
    uid = user["id"]
    seconds = max(8, min(60, int(seconds or 20)))
    fmt = fmt if fmt in SIZES else "vertical"
    w, h = SIZES[fmt]
    say = progress or (lambda text: None)
    n_scenes = max(2, min(8, round((seconds - END_LEN) / 4.2)))
    el_key = core.get_secret(uid, "elevenlabs") if voiceover else None
    px_key = core.get_secret(uid, "pexels")
    pb_key = core.get_secret(uid, "pixabay")
    find_clip, stock_key, stock_name = (pexels_clip, px_key, "Pexels") if px_key else (pixabay_clip, pb_key, "Pixabay")
    notes = []

    say("Writing the script")
    p = plan(llm, user, prompt, seconds, n_scenes, bool(el_key))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-")[:40] or "video"
    folder = core.user_home(user) / "Trillion Videos"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-{stamp}.mp4"

    vo_path, caps = None, None
    if voiceover and not el_key:
        notes.append("No voice-over: add an ElevenLabs key in Settings to get one.")
    if el_key and p["narration"]:
        say("Recording the voice-over")
        vo_path = CACHE / f"vo-{uid}-{stamp}.mp3"
        try:
            caps = voice_over(el_key, s.get("eleven_voice_id") or "21m00Tcm4TlvDq8ikWAM", p["narration"], vo_path)
            core.bump_usage(uid, "elevenlabs")
            dur = media_seconds(vo_path)
            if not caps:
                caps = even_chunks(p["narration"], dur or (seconds - END_LEN - 1))
            if dur and dur + 0.9 > seconds - END_LEN:
                seconds = min(75, int(dur + 0.9 + END_LEN + 0.999))
        except Exception as e:
            vo_path, caps = None, None
            notes.append(f"The voice-over failed ({type(e).__name__}), so this one has music and titles only.")

    credits, used = [], set()
    per = (seconds - END_LEN) / len(p["scenes"])
    if stock_key:
        for i, sc in enumerate(p["scenes"]):
            say(f"Finding footage {i + 1} of {len(p['scenes'])}")
            clip, who = find_clip(stock_key, sc["search"], w, h, per, used)
            if clip:
                sc["clip"] = clip
                credits.append(who)
        missing = sum(1 for sc in p["scenes"] if not sc.get("clip"))
        if missing:
            notes.append(f"No stock footage was found for {missing} of {len(p['scenes'])} scenes, so those use a light backdrop.")
    else:
        notes.append("No stock footage: add a free Pixabay key in Settings to get real footage. This one uses light backdrops.")

    music, music_name = pick_music()
    say("Rendering")
    seconds = render(out, s.get("brand") or "", p["scenes"], website=s.get("site") or "", accent=s.get("accent") or "#ffb454",
                     fmt=fmt, seconds=seconds, vo_path=vo_path, captions=caps, music=music,
                     progress=lambda pct: say(f"Rendering {pct}%"))
    thumb = str(out)[:-4] + ".jpg"
    subprocess.run([find_ffmpeg(), "-y", "-v", "error", "-ss", "1.6", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2", thumb],
                   capture_output=True)
    if vo_path:
        try:
            os.unlink(vo_path)
        except OSError:
            pass
    detail = {"scenes": [sc["headline"] for sc in p["scenes"]], "narration": p["narration"] if vo_path else "",
              "footage": sorted(set(credits)), "footage_source": stock_name if credits else "", "music": music_name, "format": fmt, "notes": notes}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (uid, p["title"], str(out), thumb if os.path.exists(thumb) else "", seconds, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": p["title"], "saved": str(out), "seconds": round(seconds, 1),
            "footage_clips": len(credits), "voice_over": bool(vo_path), "notes": notes}
