"""Cinematic marketing videos for the user's businesses - AI-generated moving scenes, finished with branding.

The flow: the brain writes a short scene plan for the business, FreeVideo (Wan-class model on this PC's own
graphics card) renders a real moving clip for each scene, then video.render() stitches them into a branded
1080p video with the business's accent colour, headlines, captions, music and a call-to-action end card.

Every scene clip is saved as it finishes, so a long render (a clip is ~8 min) only ever resumes from the last
finished scene - never from scratch (the user's standing rule). Finished clips live in a job folder and are
reused on a re-run with the same job id.
"""
import json
import re
import time
from pathlib import Path

import core
import video
import freevideo

JOBS = core.DATA / "marketing"
FMT_OK = ("vertical", "landscape", "square")


def _slug(text, fallback="marketing"):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:50] or fallback


# Words that mean the model just echoed the request instead of writing a real on-screen line. A headline
# or title containing any of these (or a URL) is command text, not ad copy, and must never be shown.
_BANNED = re.compile(r"\b(marketing|promo\w*|advert\w*|commercial|video|clip|showcas\w*|showing|feature[sd]?|"
                     r"cinematic|voice ?over|voiceover|narrat\w*|generate|premium|scene|footage)\b|https?://|www\.", re.I)


def _clean_head(text, fallback=""):
    """A short on-screen headline, or the fallback if the model echoed the brief/command instead of ad copy."""
    t = re.sub(r"[\"“”'’`]", "", str(text or "")).strip(" .,:;-–—")
    t = re.sub(r"\s+", " ", t)
    if not t or _BANNED.search(t) or len(t) > 32 or len(t.split()) > 6:
        return fallback
    return t[:40]


def _clean_caption(text):
    """A supporting on-screen line. Dropped if it is command text rather than real copy."""
    t = re.sub(r"[\"“”'’`]", "", str(text or "")).strip()
    t = re.sub(r"\s+", " ", t)
    return "" if (not t or _BANNED.search(t) or len(t) > 90) else t[:80]


def _brand_bits(user, brief):
    """Business name, website and accent colour to brand the video with."""
    s = core.settings(user)
    brand = (s.get("brand") or "").strip()
    site = (s.get("site") or "").strip()
    accent = s.get("accent") or "#ffb454"
    # Let the brief name a business explicitly ("... for TackleTrail"); fall back to the login's own brand.
    m = re.search(r"\bfor\s+([A-Za-z0-9][\w .&'-]{1,40})", brief or "")
    if m:
        brand = m.group(1).strip(" .")
    return brand or (s.get("assistant_name") or "Our Brand"), site, accent


def plan(user, brief, fmt="vertical", scene_count=4):
    """Ask the brain for a scene plan. Returns a dict with brand, scenes[], cta, music_mood - never raises."""
    brand, site, accent = _brand_bits(user, brief)
    fmt = fmt if fmt in FMT_OK else "vertical"
    scene_count = max(2, min(6, int(scene_count or 4)))
    out = {"brand": brand, "website": site, "accent": accent, "fmt": fmt,
           "title": brand + " - marketing", "cta": (f"Visit {site}" if site else f"Discover {brand}"),
           "music_mood": "uplifting cinematic", "scenes": []}
    llm = core.LLM
    text = ""
    if llm:
        try:
            text = (llm(user, [
                {"role": "system", "content":
                 "You plan short cinematic marketing videos with a spoken voiceover. Reply with ONLY valid JSON, no prose:\n"
                 '{"title": "...", "cta": "short call to action", "music_mood": "two or three words", '
                 '"scenes": [{"kind": "scene" | "feature", "headline": "2-4 word on-screen line", '
                 '"caption": "one short on-screen line", "narration": "one spoken sentence, 12-22 words, that plays over '
                 'this scene", "prompt": "for kind=scene only: a vivid 60-90 word text-to-video prompt - subject, setting, '
                 'golden-hour light, slow cinematic camera move, what moves, ambient sound; photoreal; NO text/logos", '
                 '"page": "for kind=feature only: the website section to show, e.g. maps, search, listings, home", '
                 '"seconds": 5}]}'},
                {"role": "user", "content":
                 f"Business: {brand}" + (f" (website {site})" if site else "") + ".\n"
                 f"Make a {scene_count}-scene cinematic marketing video with a flowing voiceover. Brief: {brief}\n"
                 "Mix kind=scene (cinematic moving shots that sell the feeling - e.g. fishing at dawn, a campfire, a boat "
                 "cutting across a dam) with one or two kind=feature scenes that show a real part of the website (e.g. the "
                 "maps). The narration lines should read as one continuous advert when played in order. Keep every scene "
                 "prompt free of text, words, signage or logos. seconds is 4-8 per scene."}]
            ).get("content") or "")
        except Exception:
            text = ""
    data = _loads(text)
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if isinstance(scenes, list) and scenes:
        # The title brands the file and the video; never let the raw brief/command become it.
        out["title"] = _clean_head(data.get("title"), brand) or brand
        out["cta"] = _clean_caption(data.get("cta")) or out["cta"]
        out["music_mood"] = str(data.get("music_mood") or out["music_mood"])[:40]
        clean = []
        for sc in scenes[:6]:
            if not isinstance(sc, dict):
                continue
            kind = "feature" if str(sc.get("kind") or "").lower() == "feature" else "scene"
            prompt = str(sc.get("prompt") or "").strip()
            if kind == "scene" and len(prompt) < 20:
                continue
            try:
                secs = max(4.0, min(8.0, float(sc.get("seconds") or 5)))
            except (TypeError, ValueError):
                secs = 5.0
            clean.append({"kind": kind, "headline": _clean_head(sc.get("headline")),
                          "caption": _clean_caption(sc.get("caption")), "narration": str(sc.get("narration") or "")[:200],
                          "prompt": prompt[:1200], "page": str(sc.get("page") or "").strip()[:40], "seconds": secs})
        out["scenes"] = clean
    # Guarantee the video is actually cinematic: at least two moving AI scenes, at most two website
    # screenshots, and it never opens or closes on a flat screenshot. A weak brain often returns all
    # "feature" scenes (just the site) or scenes with no prompt - that produced flat, footage-less videos.
    cine = [s for s in out["scenes"] if s["kind"] == "scene" and len(s.get("prompt", "")) >= 20]
    feat = [s for s in out["scenes"] if s["kind"] == "feature"][:2]
    while len(cine) < 2:
        cine.append(_default_scene(brand, brief, len(cine)))
    ordered = [cine[0]]                                     # open on a cinematic shot
    rest = cine[1:]
    for k, fsc in enumerate(feat):                          # weave the site screenshots between the moving shots
        ordered.append(fsc)
        if rest:
            ordered.append(rest.pop(0))
    ordered += rest
    if ordered and ordered[-1]["kind"] == "feature" and cine:   # never end on a flat screenshot
        ordered.append(_default_scene(brand, brief, len(cine)))
    out["scenes"] = ordered[:6]
    return out


# A few varied cinematic shots to fall back on when the brain can't plan real moving scenes, so the video
# always has flowing footage rather than flat slides. Each is a complete text-to-video prompt.
_CINE_MOVES = ("a slow confident push-in", "a gentle aerial drift", "a smooth dolly gliding past",
               "a rising crane reveal", "a slow orbit")


def _default_scene(brand, brief, i):
    move = _CINE_MOVES[i % len(_CINE_MOVES)]
    return {"kind": "scene", "headline": "", "caption": "", "narration": "",
            "prompt": (f"A cinematic, photorealistic shot that captures the feeling of {brand}. {brief}. "
                       f"Golden-hour light, {move}, shallow depth of field, rich natural detail, soft ambient "
                       "sound. Photoreal, filmic colour. No on-screen text, no words, no signage, no logos.")[:1200],
            "page": "", "seconds": 5.0}


def _loads(text):
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)                # dig the JSON object out of any surrounding chatter
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def make(user, brief, fmt="vertical", scene_count=4, job_id=None, progress=None):
    """Plan -> one AI clip per scene (saved, resumable) -> one branded video. Returns {saved,...} or {error}."""
    say = progress or (lambda t: None)
    import genvideo
    if genvideo.engine(user) == "handoff":
        # Can't auto-assemble on hand-off: plan it and hand back the per-scene prompts to run in the Claude app.
        p = plan(user, brief, fmt, scene_count)
        return {"handoff": True, "plan": p,
                "note": "Video engine is set to Claude hand-off, so I planned the video instead of rendering it. "
                        "Generate each scene prompt in the Claude app, then switch the engine to Local or Cloud (Settings) "
                        "and ask again to have me assemble it with voiceover and branding."}
    ok, why = genvideo.ready(user)
    if not ok:
        return {"error": why}

    job_id = job_id or (_slug(brief)[:24] + "-" + time.strftime("%Y%m%d-%H%M%S"))
    jdir = JOBS / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    planfile = jdir / "plan.json"
    if planfile.exists():                                   # resuming: reuse the plan we already made
        try:
            p = json.loads(planfile.read_text())
        except Exception:
            p = plan(user, brief, fmt, scene_count)
    else:
        say("Planning the scenes")
        p = plan(user, brief, fmt, scene_count)
        planfile.write_text(json.dumps(p))
    fmt = p.get("fmt") or fmt
    scenes = p["scenes"]

    done = {}                                              # scene index -> saved clip path, from earlier runs
    manifest = jdir / "clips.json"
    if manifest.exists():
        try:
            done = {int(k): v for k, v in json.loads(manifest.read_text()).items() if Path(v).exists()}
        except Exception:
            done = {}

    w, h = video.SIZES.get(fmt, video.SIZES["vertical"])
    for i, sc in enumerate(scenes):
        if i in done and Path(done[i]).exists():
            say(f"Scene {i + 1}/{len(scenes)} already made - reusing it")
            sc["clip" if done[i].lower().endswith((".mp4", ".mov", ".webm")) else "image"] = done[i]
            continue
        if sc.get("kind") == "feature" and p.get("website"):
            say(f"Scene {i + 1}/{len(scenes)}: capturing the {sc.get('page') or 'website'}")
            shot = _shot_url(p["website"], sc.get("page", ""), jdir / f"feature-{i}.jpg", w, h)
            if shot:
                done[i] = shot
                sc["image"] = shot
                manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))
                continue
            # screenshot failed: fall back to a cinematic AI scene for this feature
            sc.setdefault("prompt", f"A cinematic, photorealistic shot evoking {sc.get('headline') or p['brand']}. "
                                    "Golden-hour light, slow camera move. No on-screen text.")
        say(f"Scene {i + 1}/{len(scenes)}: generating the clip ({genvideo.engine(user)})")
        res = genvideo.clip(user, sc.get("prompt") or sc.get("narration") or p["brand"],
                            seconds=sc["seconds"], fmt=fmt,
                            progress=lambda t, n=i: say(f"Scene {n + 1}/{len(scenes)}: {t}"))
        if res.get("error"):
            manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))
            return {"error": f"Scene {i + 1} failed: {res['error']}", "job_id": job_id,
                    "done": len(done), "total": len(scenes)}
        done[i] = res["saved"]
        sc["clip"] = res["saved"]
        manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))   # resumable: saved after each scene

    # Voiceover: read the scene narration lines as one continuous advert, in the free Kokoro voice.
    vo_path, vo_secs = None, 0.0
    script = " ".join(sc.get("narration", "").strip() for sc in scenes if sc.get("narration", "").strip())
    if p.get("cta"):
        script = (script + " " + p["cta"] + ".").strip()
    if script:
        say("Recording the voiceover")
        try:
            import localvoice
            samples, sr = localvoice.synth_wait(script, voice=core.settings(user).get("kokoro_voice") or None)
            if len(samples):
                wav = jdir / "voiceover.wav"
                wav.write_bytes(localvoice.wav_bytes(samples, sr))
                vo_path = str(wav)
                vo_secs = len(samples) / float(sr)
        except Exception:
            vo_path = None

    say("Assembling the branded marketing video")
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{_slug(p.get('title') or p['brand'])}-marketing-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    scene_secs = sum(float(sc.get("seconds") or 5) for sc in scenes)
    total = max(scene_secs, vo_secs) + 3.0                 # fit the scenes to the voiceover length when there is one
    rscenes = [{"headline": sc.get("headline", ""), "caption": sc.get("caption", ""),
                **({"clip": sc["clip"]} if sc.get("clip") else {}), **({"image": sc["image"]} if sc.get("image") else {})}
               for sc in scenes]
    music = _pick_music()
    try:
        video.render(str(out), p["brand"], rscenes, website=p.get("website", ""), accent=p.get("accent", "#ffb454"),
                     fmt=fmt, seconds=total, vo_path=vo_path, music=music, progress=lambda t: say(t))
    except Exception as e:
        return {"error": f"The clips were made but assembling the final video failed ({type(e).__name__}: {e}). "
                         f"The scene clips are saved in your Trillion Videos folder.", "job_id": job_id}
    if not out.exists() or out.stat().st_size < 2000:
        return {"error": "Assembly produced an empty file. The scene clips are saved in your Trillion Videos folder."}

    secs = video.media_seconds(out) or total
    detail = {"engine": "FreeVideo + Trillion reel", "format": fmt, "cta": p.get("cta", ""),
              "scenes": [sc.get("headline", "") for sc in scenes], "notes": ["Cinematic marketing video, made on this PC."]}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (user["id"], p.get("title") or (p["brand"] + " marketing"), str(out), "", secs, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": p.get("title") or (p["brand"] + " marketing"), "saved": str(out),
            "seconds": round(secs, 1), "scenes": len(scenes), "cta": p.get("cta", ""), "job_id": job_id}


def _shot_url(website, page, dest, w, h):
    """Screenshot a page of the user's live site (for a 'feature' scene, e.g. the maps). None on any failure."""
    base = str(website or "").strip()
    if not base:
        return None
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    url = base.rstrip("/")
    page = str(page or "").strip().strip("/")
    if page and page.lower() not in ("home", "homepage", "landing", "main"):
        url = url + "/" + page
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            br = pw.chromium.launch(args=["--no-sandbox"])
            pg = br.new_page(viewport={"width": max(1280, w), "height": max(900, h)}, device_scale_factor=2)
            pg.goto(url, wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(1200)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(dest), full_page=False)
            br.close()
        return str(dest) if Path(dest).exists() and Path(dest).stat().st_size > 2000 else None
    except Exception:
        return None


def _pick_music():
    """A music bed from the shared music folder, if any is there; otherwise no music (silent bed)."""
    try:
        d = core.DATA / "music"
        for f in sorted(d.glob("*.mp3")) + sorted(d.glob("*.m4a")) + sorted(d.glob("*.wav")):
            return str(f)
    except Exception:
        pass
    return None
