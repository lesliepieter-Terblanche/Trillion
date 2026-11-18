"""Stickman video director - turn a script or topic into a multi-scene animated stickman video.

Built for the user's faceless stickman channels (YoreTales, Ifinite, the What-If and survival series). The brain
directs the story into scenes with a spoken voiceover; a locked character "look" is repeated in every shot's prompt
so the stick figure stays the SAME across the whole video (the character-consistency trick); each scene is rendered
on the chosen engine (local FreeVideo for free, or cloud), then stitched with the voiceover and captions.

Every scene clip is saved as it finishes, so a long render resumes from the last finished scene, never from scratch.
"""
import json
import re
import time
from pathlib import Path

import core
import video
import genvideo
from marketing import _slug, _loads, _pick_music

JOBS = core.DATA / "stickman"
FMT_OK = ("vertical", "landscape", "square")

# The visual styles the director can lock a video into.
LOOKS = {
    "classic":   "clean black stick-figure characters drawn with bold confident black lines on a plain off-white "
                 "background, minimal flat 2D style",
    "noir":      "white stick-figure characters with soft rim light on a dark moody background, cinematic 2.5D depth "
                 "and film-noir shadows",
    "paper":     "hand-drawn stick-figure characters in sketchy pencil lines on a paper-textured background, doodle "
                 "notebook style",
    "neon":      "glowing neon stick-figure outlines on a dark background, synthwave colours and soft glow",
    "storybook": "soft watercolour stick-figure characters in a warm storybook world, gentle muted colours",
}
# Repeated in every scene prompt - this is what keeps the character the same shot to shot.
NEG = ("Keep the SAME stick-figure character design in every shot - identical proportions, line weight and colours. "
       "No on-screen text, no captions, no subtitles, no logos, no watermarks, no signatures.")


def looks():
    return list(LOOKS)


def plan(user, brief, look="classic", fmt="vertical", scene_count=6):
    """Direct the script/topic into scenes. Returns a dict with a look bible and scenes[]. Never raises."""
    look = look if look in LOOKS else "classic"
    fmt = fmt if fmt in FMT_OK else "vertical"
    scene_count = max(3, min(8, int(scene_count or 6)))
    # Channel name for the end card: "... for YoreTales", else the brand/assistant name.
    s = core.settings(user)
    m = re.search(r"\bfor\s+([A-Za-z0-9][\w .&'-]{1,40})", brief or "")
    channel = (m.group(1).strip(" .") if m else (s.get("brand") or "")).strip()
    out = {"title": "Stickman video", "look": look, "fmt": fmt, "channel": channel,
           "accent": s.get("accent") or "#ffb454", "bible": "", "scenes": []}
    text = ""
    if core.LLM:
        try:
            text = (core.LLM(user, [
                {"role": "system", "content":
                 "You are a stickman video director for a faceless YouTube channel. Reply with ONLY valid JSON:\n"
                 '{"title":"short video title","bible":"one sentence locking the recurring stick-figure character(s) - '
                 'who they are, colour accents, any signature prop - so every shot matches","scenes":['
                 '{"narration":"one spoken sentence of the story, 12-24 words","action":"what happens in THIS shot - '
                 'subject, action, camera move, mood; NO style words and NO on-screen text","caption":"optional 2-5 '
                 'word on-screen caption or empty","seconds":6}]}'},
                {"role": "user", "content":
                 f"Direct a {scene_count}-scene stickman video. The narration lines must read as one continuous story "
                 f"when played in order, with a strong hook first and a payoff last. Script or topic:\n{brief}"}]
            ).get("content") or "")
        except Exception:
            text = ""
    data = _loads(text)
    if isinstance(data, dict):
        out["title"] = str(data.get("title") or out["title"])[:80]
        out["bible"] = str(data.get("bible") or "").strip()[:300]
        for sc in (data.get("scenes") or [])[:8]:
            if not isinstance(sc, dict):
                continue
            action = str(sc.get("action") or "").strip()
            narr = str(sc.get("narration") or "").strip()
            if len(action) < 8 and len(narr) < 8:
                continue
            try:
                secs = max(4.0, min(10.0, float(sc.get("seconds") or 6)))
            except (TypeError, ValueError):
                secs = 6.0
            out["scenes"].append({"narration": narr[:220], "action": action[:400],
                                  "caption": str(sc.get("caption") or "")[:40], "seconds": secs})
    if not out["scenes"]:                                   # nothing usable: one scene straight from the brief
        out["scenes"] = [{"narration": brief[:200], "action": f"A stick figure acts out: {brief}", "caption": "",
                          "seconds": 6.0}]
    if not out["bible"]:
        out["bible"] = "one consistent black stick-figure character"
    # Compose each scene's full video prompt: the locked look + this shot's action + the chosen style + consistency rule.
    look_desc = LOOKS[look]
    for sc in out["scenes"]:
        sc["prompt"] = (f"2D stick-figure animation. Recurring character: {out['bible']}. "
                        f"This shot: {sc['action']}. Visual style: {look_desc}. {NEG}")
    return out


def make(user, brief, look="classic", fmt="vertical", scene_count=6, job_id=None, progress=None):
    """Plan -> one stickman clip per scene (saved, resumable) -> stitched video with voiceover. {saved,...} or {error}."""
    say = progress or (lambda t: None)
    if genvideo.engine(user) == "handoff":
        p = plan(user, brief, look, fmt, scene_count)
        return {"handoff": True, "plan": p,
                "note": "Video engine is set to Claude hand-off, so I directed the video instead of rendering it. "
                        "Generate each scene prompt in the Claude app, then switch the engine to Local or Cloud and ask "
                        "again to have me assemble it with the voiceover."}
    ok, why = genvideo.ready(user)
    if not ok:
        return {"error": why}

    job_id = job_id or (_slug(brief, "stickman")[:24] + "-" + time.strftime("%Y%m%d-%H%M%S"))
    jdir = JOBS / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    planfile = jdir / "plan.json"
    if planfile.exists():
        try:
            p = json.loads(planfile.read_text())
        except Exception:
            p = plan(user, brief, look, fmt, scene_count)
    else:
        say("Directing the scenes")
        p = plan(user, brief, look, fmt, scene_count)
        planfile.write_text(json.dumps(p))
    fmt = p.get("fmt") or fmt
    scenes = p["scenes"]

    done = {}
    manifest = jdir / "clips.json"
    if manifest.exists():
        try:
            done = {int(k): v for k, v in json.loads(manifest.read_text()).items() if Path(v).exists()}
        except Exception:
            done = {}

    for i, sc in enumerate(scenes):
        if i in done and Path(done[i]).exists():
            say(f"Scene {i + 1}/{len(scenes)} already made - reusing it")
            sc["clip"] = done[i]
            continue
        say(f"Scene {i + 1}/{len(scenes)}: animating ({genvideo.engine(user)})")
        res = genvideo.clip(user, sc["prompt"], seconds=sc["seconds"], fmt=fmt,
                            progress=lambda t, n=i: say(f"Scene {n + 1}/{len(scenes)}: {t}"))
        if res.get("error"):
            manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))
            return {"error": f"Scene {i + 1} failed: {res['error']}", "job_id": job_id,
                    "done": len(done), "total": len(scenes)}
        done[i] = res["saved"]
        sc["clip"] = res["saved"]
        manifest.write_text(json.dumps({str(k): v for k, v in done.items()}))   # resumable: saved after each scene

    # Voiceover: the narration lines read as one continuous story, in the free Kokoro voice.
    vo_path, vo_secs = None, 0.0
    script = " ".join(sc.get("narration", "").strip() for sc in scenes if sc.get("narration", "").strip())
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

    say("Stitching the video")
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{_slug(p.get('title') or 'stickman')}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    scene_secs = sum(float(sc.get("seconds") or 6) for sc in scenes)
    total = max(scene_secs, vo_secs) + 2.0
    rscenes = [{"headline": "", "caption": sc.get("caption", ""), "clip": sc["clip"]} for sc in scenes]
    music = _pick_music()
    try:
        video.render(str(out), p.get("channel") or "", rscenes, website="", accent=p.get("accent", "#ffb454"),
                     fmt=fmt, seconds=total, vo_path=vo_path, music=music, progress=lambda t: say(t))
    except Exception as e:
        return {"error": f"The scenes were made but stitching failed ({type(e).__name__}: {e}). The scene clips are "
                         "saved in your Trillion Videos folder.", "job_id": job_id}
    if not out.exists() or out.stat().st_size < 2000:
        return {"error": "Stitching produced an empty file. The scene clips are saved in your Trillion Videos folder."}

    secs = video.media_seconds(out) or total
    detail = {"engine": f"Stickman director ({genvideo.engine(user)})", "look": p.get("look"), "format": fmt,
              "scenes": [sc.get("caption") or sc.get("narration", "")[:40] for sc in scenes],
              "notes": ["Animated stickman video, made on this PC."]}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (user["id"], p.get("title") or "Stickman video", str(out), "", secs, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": p.get("title") or "Stickman video", "saved": str(out),
            "seconds": round(secs, 1), "scenes": len(scenes), "look": p.get("look"), "job_id": job_id}
