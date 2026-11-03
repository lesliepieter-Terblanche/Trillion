"""Scene montage: turn the user's OWN scene images (hand-drawn stickmen, storyboards, photos) into a
finished, narrated 1080p video - completely local and free.

This is the "upload my own scenes and you complete it" engine. The user hands over a picture for each
scene (and, optionally, a line of narration for it); Trillion gives each still a slow cinematic camera
move (Ken-Burns), speaks the line in the local Kokoro voice, burns a caption, lays a soft music bed
under it, and stitches the scenes into one MP4. Nothing is sent to the internet and no key is needed.

Two things set it apart from the classic video engine (video.py) and the stock-footage engine (mpt.py):
  - the voice-over is the local Kokoro voice (localvoice), so it is free and needs no ElevenLabs key;
  - the render is RESUMABLE - each scene is rendered to its own chunk that is saved as it finishes, so a
    long video that is interrupted picks up from the last finished scene instead of starting over.

It reuses video.py's helpers (ffmpeg, fonts, music, sizes) so a montage looks and sounds like the rest.
"""
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import core
import localvoice
import video

WORK = core.DATA / "montage"           # per-job folders live here; kept so an interrupted job can resume
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
MIN_SCENE = 2.6                        # a scene is never shorter than this, even with a tiny caption
PAD_SCENE = 0.6                        # a breath of silence after each line

# Fixed cinematic "looks" - deterministic ffmpeg grades applied to each scene's moving still. No tokens, no
# cloud model: just colour, grain and vignette, so every video can have a distinct feel. The caption stays
# crisp because the grade is applied to the picture before the caption is laid over it.
STYLES = {
    "clean":  {"label": "Clean", "grade": ""},
    "film":   {"label": "Cinematic film", "grade": "eq=contrast=1.08:saturation=1.12:gamma=0.98,"
               "noise=alls=7:allf=t,vignette=PI/5"},
    "warm":   {"label": "Warm / golden", "grade": "eq=saturation=1.10:contrast=1.05,"
               "colorbalance=rm=0.10:gm=0.03:bm=-0.09,vignette=PI/6"},
    "cool":   {"label": "Cool / midnight", "grade": "eq=saturation=1.05:contrast=1.06,"
               "colorbalance=rm=-0.06:gm=0.0:bm=0.12,vignette=PI/6"},
    "noir":   {"label": "Black & white", "grade": "hue=s=0,eq=contrast=1.25:brightness=0.02,"
               "noise=alls=6:allf=t,vignette=PI/5"},
    "paper":  {"label": "Hand-drawn paper", "grade": "eq=saturation=0.55:contrast=1.05:gamma=1.04,"
               "colorbalance=rs=0.12:gs=0.04:bs=-0.10,noise=alls=11:allf=t"},
    "bold":   {"label": "Bold graphic", "grade": "eq=saturation=1.5:contrast=1.2,"
               "curves=preset=increase_contrast"},
    "retro":  {"label": "Retro VHS", "grade": "rgbashift=rh=4:bh=-4,noise=alls=12:allf=t,"
               "eq=saturation=1.3:contrast=1.08,vignette=PI/5"},
}
DEFAULT_STYLE = "film"


def styles():
    """[(key, label)] for the UI, in a sensible order."""
    order = ["film", "clean", "warm", "cool", "noir", "paper", "bold", "retro"]
    return [(k, STYLES[k]["label"]) for k in order if k in STYLES]


def _grade(style):
    return STYLES.get(style or "", STYLES[DEFAULT_STYLE])["grade"]


def _slug(text, fallback="montage"):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40] or fallback


def _wrap(draw, text, font, max_w):
    words, lines, cur = str(text or "").split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


# ---------- one scene's still, fitted to the frame with a soft filled background ----------
def _prep_still(src, dest, w, h):
    """Fit any picture into a w x h frame without distortion: the picture sits whole on top of a blurred,
    enlarged copy of itself, so a stickman on white paper looks intentional rather than stretched."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
    # blurred cover background
    bg = ImageOps.fit(im, (w, h), method=Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    bg = Image.eval(bg, lambda p: int(p * 0.72))        # darken so the subject pops
    # the picture itself, contained within ~92% of the frame
    fg = im.copy()
    fg.thumbnail((int(w * 0.92), int(h * 0.82)), Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    bg.save(dest, "JPEG", quality=92)
    return dest


def _caption_png(text, dest, w, h):
    """A lower-third caption card the width of the frame, transparent elsewhere, overlaid on the scene."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not str(text or "").strip():
        img.save(dest)
        return dest
    d = ImageDraw.Draw(img)
    font = video._font(max(34, w // 24), bold=True)
    pad = int(w * 0.06)
    lines = _wrap(d, text, font, w - pad * 2)[:3]
    asc, desc = font.getmetrics()
    lh = asc + desc + 10
    block = lh * len(lines)
    band = block + int(h * 0.06)
    top = h - band - int(h * 0.07)
    # soft dark gradient band behind the words
    grad = Image.new("L", (1, band), 0)
    for yy in range(band):
        grad.putpixel((0, yy), int(190 * min(1.0, yy / (band * 0.5))))
    grad = grad.resize((w, band))
    panel = Image.new("RGBA", (w, band), (8, 10, 16, 255))
    panel.putalpha(grad)
    img.alpha_composite(panel, (0, top))
    y = top + (band - block) // 2
    for ln in lines:
        tw = d.textlength(ln, font=font)
        x = (w - tw) // 2
        d.text((x + 2, y + 2), ln, font=font, fill=(0, 0, 0, 170))       # shadow
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += lh
    img.save(dest)
    return dest


# ---------- narration for one scene (local Kokoro voice) ----------
def _narrate(text, dest_wav, voice=None):
    """Speak one line locally. Returns its duration in seconds, or 0.0 if there is nothing to say / no voice."""
    text = " ".join(str(text or "").split())
    if not text:
        return 0.0
    try:
        samples, sr = localvoice.synth_wait(text, voice=voice, speed=1.0, wait=180)
    except Exception:
        return 0.0
    if samples is None or len(samples) == 0:
        return 0.0
    with open(dest_wav, "wb") as f:
        f.write(localvoice.wav_bytes(samples, sr))
    return len(samples) / float(sr)


# ---------- render one scene to its own chunk (the resumable unit) ----------
def _render_scene(idx, still, caption_png, narration_wav, dur, out_mp4, w, h, style=DEFAULT_STYLE):
    ff = video.find_ffmpeg()
    frames = max(1, int(round(dur * video.FPS)))
    # alternate the camera move so consecutive scenes don't feel identical
    zoom_in = (idx % 2 == 0)
    if zoom_in:
        zexpr = "z='min(zoom+0.0012,1.14)'"
        xexpr, yexpr = "x='iw/2-(iw/zoom/2)'", "y='ih/2-(ih/zoom/2)'"
    else:
        zexpr = "z='if(lte(zoom,1.0),1.14,max(1.001,zoom-0.0012))'"
        xexpr, yexpr = "x='iw/2-(iw/zoom/2)'", "y='ih/2-(ih/zoom/2)'"
    grade = _grade(style)
    grade = ("," + grade) if grade else ""          # applied to the picture, before the caption goes on top
    # pre-scale big so zoompan is smooth, zoompan down to frame size, grade, then lay the caption over it
    vf = (f"[0:v]scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2},"
          f"zoompan={zexpr}:{xexpr}:{yexpr}:d={frames}:s={w}x{h}:fps={video.FPS},"
          f"setsar=1{grade}[bg];[bg][1:v]overlay=0:0:format=auto[v]")
    has_audio = bool(narration_wav and os.path.exists(narration_wav))
    cmd = [ff, "-y", "-v", "error", "-loop", "1", "-i", str(still), "-i", str(caption_png)]
    if has_audio:
        # the spoken line, padded with trailing silence to the scene's full length (-t caps it)
        cmd += ["-i", str(narration_wav),
                "-filter_complex", vf + ";[2:a]apad[a]",
                "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", vf, "-map", "[v]", "-map", "2:a"]
    cmd += ["-t", f"{dur:.3f}", "-r", str(video.FPS), "-c:v", "libx264", "-preset", "medium",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(out_mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not os.path.exists(out_mp4) or os.path.getsize(out_mp4) < 2000:
        raise RuntimeError("A scene failed to render. " + ((r.stderr or "")[-180:]))
    return out_mp4


# ---------- the whole montage ----------
def build(user, title, scenes, fmt="vertical", voice=None, style=DEFAULT_STYLE, progress=None, job_dir=None):
    """scenes: list of {"image": <path on the PC>, "text": <narration/caption, optional>}.
    Renders each scene to its own saved chunk (resumable), then stitches them with a music bed.
    style picks a fixed cinematic look (see STYLES). Returns the usual video dict."""
    say = progress or (lambda t: None)
    uid = user["id"]
    fmt = fmt if fmt in video.SIZES else "vertical"
    style = style if style in STYLES else DEFAULT_STYLE
    w, h = video.SIZES[fmt]
    scenes = [s for s in (scenes or []) if s and s.get("image") and os.path.exists(s["image"])
              and str(s["image"]).lower().endswith(IMAGE_EXT)][:12]
    if not scenes:
        raise RuntimeError("No usable scene images were given. Attach a picture for each scene and try again.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = _slug(title, "montage")
    job = Path(job_dir) if job_dir else (WORK / f"{slug}-{stamp}")
    job.mkdir(parents=True, exist_ok=True)
    manifest = job / "manifest.json"
    if manifest.exists():                                   # resuming: keep the scene list and look we started with
        try:
            m = json.loads(manifest.read_text())
            scenes = m.get("scenes", scenes)
            style = m.get("style", style) if m.get("style") in STYLES else style
        except Exception:
            pass
    else:
        manifest.write_text(json.dumps({"title": title, "fmt": fmt, "style": style, "scenes": scenes}, ensure_ascii=False))

    chunks, resumed = [], 0
    for i, sc in enumerate(scenes):
        chunk = job / f"scene_{i:03d}.mp4"
        if chunk.exists() and chunk.stat().st_size > 2000:  # already finished on an earlier run
            chunks.append(chunk)
            resumed += 1
            continue
        say(f"Scene {i + 1} of {len(scenes)}: preparing the picture")
        still = _prep_still(sc["image"], job / f"still_{i:03d}.jpg", w, h)
        cap = _caption_png(sc.get("text", ""), job / f"cap_{i:03d}.png", w, h)
        say(f"Scene {i + 1} of {len(scenes)}: recording the voice")
        wav = job / f"vo_{i:03d}.wav"
        dur = _narrate(sc.get("text", ""), wav, voice=voice)
        dur = max(MIN_SCENE, dur + PAD_SCENE)
        say(f"Scene {i + 1} of {len(scenes)}: rendering")
        _render_scene(i, still, cap, wav if dur and os.path.exists(wav) else None, dur, chunk, w, h, style=style)
        chunks.append(chunk)

    # stitch the chunks
    say("Stitching the scenes together")
    listfile = job / "concat.txt"
    listfile.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks))
    ff = video.find_ffmpeg()
    stitched = job / "stitched.mp4"
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                        "-c", "copy", "-movflags", "+faststart", str(stitched)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not stitched.exists():          # a copy-concat can fail if chunks differ; re-encode
        subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listfile),
                        "-r", str(video.FPS), "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "192k", str(stitched)], capture_output=True, timeout=900)
    if not stitched.exists() or stitched.stat().st_size < 2000:
        raise RuntimeError("The scenes were rendered but could not be stitched together.")

    total = video.media_seconds(stitched) or 0
    # soft music bed, ducked under the narration
    say("Adding music")
    music, music_name = video.pick_music()
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-{stamp}.mp4"
    mixed = False
    if music and os.path.exists(music):
        r = subprocess.run(
            [ff, "-y", "-v", "error", "-i", str(stitched), "-stream_loop", "-1", "-i", str(music),
             "-filter_complex",
             "[1:a]volume=0.18[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=2[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)], capture_output=True, text=True, timeout=600)
        mixed = r.returncode == 0 and out.exists() and out.stat().st_size > 2000
    if not mixed:
        shutil.copy2(stitched, out)

    thumb = str(out)[:-4] + ".jpg"
    try:
        subprocess.run([ff, "-y", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1",
                        "-vf", "scale=360:-2", thumb], capture_output=True, timeout=60)
    except Exception:
        thumb = ""
    secs = video.media_seconds(out) or total
    notes = [f"Made on this PC from {len(scenes)} scene picture{'s' if len(scenes) != 1 else ''} you gave, "
             "with the local voice - nothing was sent to the internet and no key was needed.",
             "Rendered scene by scene, so a long one can be resumed if it is interrupted."]
    notes.append(f"Look: {STYLES[style]['label']}.")
    if resumed:
        notes.append(f"{resumed} scene{'s' if resumed != 1 else ''} picked up from an earlier run.")
    detail = {"engine": "Montage (your scenes)", "format": fmt, "style": style,
              "scenes": [sc.get("text", "")[:80] for sc in scenes],
              "narration": " ".join(sc.get("text", "") for sc in scenes), "footage": [], "footage_source": "",
              "music": music_name if mixed else "", "ai_pictures": 0, "voiceover": True, "notes": notes,
              "request": f"Montage of {len(scenes)} uploaded scenes", "media": [sc["image"] for sc in scenes],
              "job_dir": str(job)}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (uid, title or "Scene montage", str(out), thumb if thumb and os.path.exists(thumb) else "",
                  secs, json.dumps(detail), time.time()))
    # the finished video is kept; drop the big intermediate stills to save space, keep chunks for a possible redo
    for p in job.glob("still_*.jpg"):
        try:
            p.unlink()
        except OSError:
            pass
    return {"video_id": vid, "title": title or "Scene montage", "saved": str(out), "seconds": round(secs, 1),
            "scenes": len(scenes), "resumed": resumed, "voice_over": True, "engine": "montage", "notes": notes}


# ---------- optional: let the local brain write a short line for each scene from a topic ----------
def script_for(user, topic, n):
    """When the user gives pictures but no words, ask the local brain for one short narration line per scene."""
    n = max(1, min(12, int(n)))
    ask = (f'Write exactly {n} short narration lines for a {n}-scene video about: "{topic}". '
           "One line per scene, in order, each a single spoken sentence of 6-16 words, plain and warm. "
           "Return ONLY the lines, numbered 1. to " + str(n) + ".")
    try:
        txt = core.LLM(user, ask) or ""
    except Exception:
        return [""] * n
    lines = []
    for ln in txt.splitlines():
        m = re.match(r"\s*\d+[.)]\s*(.+)", ln)
        if m:
            lines.append(m.group(1).strip().strip('"'))
    while len(lines) < n:
        lines.append("")
    return lines[:n]
