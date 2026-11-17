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
                 "You plan short cinematic marketing videos. Reply with ONLY valid JSON, no prose, in this exact shape:\n"
                 '{"title": "...", "cta": "short call to action", "music_mood": "two or three words", '
                 '"scenes": [{"headline": "2-4 word on-screen line", "caption": "one short line", '
                 '"prompt": "a vivid 60-90 word text-to-video prompt: subject, setting, light, camera move, what moves, '
                 'ambient sound; photoreal and cinematic; NO on-screen text, logos or captions", "seconds": 5}]}'},
                {"role": "user", "content":
                 f"Business: {brand}" + (f" (website {site})" if site else "") + ".\n"
                 f"Make a {scene_count}-scene cinematic marketing video. Brief: {brief}\n"
                 "Each scene is one continuous moving shot that sells the business's feeling. Keep each prompt free of "
                 "any text, words, signage or logos (those are added later). seconds is 4-8 per scene."}]
            ).get("content") or "")
        except Exception:
            text = ""
    data = _loads(text)
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if isinstance(scenes, list) and scenes:
        out["title"] = str(data.get("title") or out["title"])[:80]
        out["cta"] = str(data.get("cta") or out["cta"])[:60]
        out["music_mood"] = str(data.get("music_mood") or out["music_mood"])[:40]
        clean = []
        for sc in scenes[:6]:
            if not isinstance(sc, dict):
                continue
            prompt = str(sc.get("prompt") or "").strip()
            if len(prompt) < 20:
                continue
            try:
                secs = max(4.0, min(8.0, float(sc.get("seconds") or 5)))
            except (TypeError, ValueError):
                secs = 5.0
            clean.append({"headline": str(sc.get("headline") or "")[:40], "caption": str(sc.get("caption") or "")[:80],
                          "prompt": prompt[:1200], "seconds": secs})
        out["scenes"] = clean
    if not out["scenes"]:                                   # brain gave nothing usable: one safe scene from the brief
        out["scenes"] = [{"headline": brand[:40], "caption": "",
                          "prompt": f"A cinematic, photorealistic establishing shot for {brand}: {brief}. Natural light, "
                                    "slow confident camera move, rich detail, matching ambient sound. No on-screen text.",
                          "seconds": 5.0}]
    return out


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
    st = freevideo.status(user)
    if not st["running"]:
        return {"error": freevideo.NOT_RUNNING}
    if not st["ready"]:
        return {"error": "ComfyUI is running but FreeVideo is not installed in it. Double-click "
                         "\"6 - Install FreeVideo.bat\" in the Trillion folder and finish its setup, then ask again."}

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

    for i, sc in enumerate(scenes):
        if i in done:
            say(f"Scene {i + 1}/{len(scenes)} already made - reusing it")
            sc["clip"] = done[i]
            continue
        say(f"Scene {i + 1}/{len(scenes)}: generating the clip on the graphics card (about 8 min each)")
        res = freevideo.create(core.LLM, user, sc["prompt"], seconds=sc["seconds"], fmt=fmt,
                               progress=lambda t, n=i: say(f"Scene {n + 1}/{len(scenes)}: {t}"))
        if res.get("error"):
            # Save progress so a retry resumes here, and report which scene failed.
            manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))
            return {"error": f"Scene {i + 1} failed: {res['error']}", "job_id": job_id,
                    "done": len(done), "total": len(scenes)}
        done[i] = res["saved"]
        sc["clip"] = res["saved"]
        manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))   # resumable: saved after each scene

    say("Assembling the branded marketing video")
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{_slug(p.get('title') or p['brand'])}-marketing-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    total = sum(float(sc.get("seconds") or 5) for sc in scenes) + 3.0
    rscenes = [{"headline": sc.get("headline", ""), "caption": sc.get("caption", ""), "clip": sc["clip"]} for sc in scenes]
    music = _pick_music()
    try:
        video.render(str(out), p["brand"], rscenes, website=p.get("website", ""), accent=p.get("accent", "#ffb454"),
                     fmt=fmt, seconds=total, music=music, progress=lambda t: say(t))
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


def _pick_music():
    """A music bed from the shared music folder, if any is there; otherwise no music (silent bed)."""
    try:
        d = core.DATA / "music"
        for f in sorted(d.glob("*.mp3")) + sorted(d.glob("*.m4a")) + sorted(d.glob("*.wav")):
            return str(f)
    except Exception:
        pass
    return None
