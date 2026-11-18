"""Cloud AI video (paid) through Replicate - the "top polish" option alongside the free local FreeVideo engine.

Replicate hosts the strong video models (Wan, Kling, Hailuo and others). The user adds a Replicate API token
(stored encrypted, never in plain text) and picks the model slug in Settings. This submits one text-to-video job,
waits for it, downloads the clip into the Trillion Videos folder and records it, matching freevideo.create()'s
return shape so the rest of the pipeline treats local and cloud clips the same.
"""
import json
import os
import re
import time

import httpx

import core

API = "https://api.replicate.com/v1"
MAX_WAIT = int(os.environ.get("TRILLION_CLOUDVIDEO_WAIT", 20 * 60))
POLL = 4
RATIO = {"vertical": "9:16", "square": "9:16", "landscape": "16:9"}   # the default model has no square; use portrait


def ready(user):
    return bool(core.get_secret(user["id"], "replicate"))


def _token(user):
    return (core.get_secret(user["id"], "replicate") or "").strip()


def create(llm, user, prompt, seconds=5, fmt="vertical", progress=None):
    """Make one clip on Replicate. Returns {saved,...} like freevideo.create(), or {error}."""
    say = progress or (lambda t: None)
    token = _token(user)
    if not token:
        return {"error": "No Replicate API key is set. Add it in Settings (video) to use the cloud video engine, "
                         "or switch the engine to Local to use your own graphics card for free."}
    model = (core.settings(user).get("cloud_video_model") or "wan-video/wan-2.2-t2v-fast").strip().strip("/")
    # Keep the prompt clean (no on-screen text/logos - those are added later by the reel).
    shot = re.sub(r"\s+", " ", str(prompt or "")).strip()[:1500]
    if llm:
        try:
            better = (llm(user, [
                {"role": "system", "content": "You write one text-to-video prompt. Reply with the prompt only: one "
                 "paragraph, 50-90 words, photoreal and cinematic, golden-hour light, slow camera move, matching ambient "
                 "sound. No on-screen text, captions or logos."},
                {"role": "user", "content": f"Prompt for a {seconds:g}-second cinematic shot of: {shot}"}]).get("content") or "")
            if len(better.strip()) > 40:
                shot = re.sub(r"\s+", " ", better).strip()[:1500]
        except Exception:
            pass

    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json", "Prefer": "wait=1"}
    # Inputs for the default model (wan-video/wan-2.2-t2v-fast). It takes num_frames (81-100), not a duration, and has
    # no square ratio. A different model in Settings may want different fields - then tune this.
    frames = max(81, min(100, int(round(seconds * 16))))
    body = {"input": {"prompt": shot, "aspect_ratio": RATIO.get(fmt, "9:16"),
                      "resolution": "720p", "num_frames": frames, "frames_per_second": 16}}
    say("Sending the shot to the cloud video model")
    try:
        r = httpx.post(f"{API}/models/{model}/predictions", headers=headers, json=body, timeout=60)
    except Exception as e:
        return {"error": f"Could not reach the cloud video service ({type(e).__name__})."}
    if r.status_code == 404:
        return {"error": f"The cloud video model '{model}' was not found on Replicate. Set a valid model in Settings "
                         "(for example wan-video/wan-2.2-t2v-fast)."}
    if r.status_code in (401, 403):
        return {"error": "Replicate refused your API key (401). Get a fresh token at replicate.com/account/api-tokens "
                         "- it starts with 'r8_'. Copy the whole token with no spaces, paste it under Settings, Keys, "
                         "Replicate, and make sure billing is set up on your Replicate account (it needs a card even for "
                         "pay-as-you-go). Then try again."}
    if r.status_code >= 300:
        return {"error": f"The cloud video service refused the job ({r.status_code}): {r.text[:200]}"}
    pred = r.json()
    get_url = (pred.get("urls") or {}).get("get")
    started = time.time()
    while pred.get("status") in ("starting", "processing") and time.time() - started < MAX_WAIT:
        mins = int((time.time() - started) // 60)
        say(f"Generating the clip in the cloud ({mins} min so far)")
        time.sleep(POLL)
        try:
            pred = httpx.get(get_url, headers=headers, timeout=30).json()
        except Exception:
            continue
    if pred.get("status") != "succeeded":
        return {"error": f"The cloud clip did not finish ({pred.get('status')}): {str(pred.get('error'))[:200]}"}

    out_url = pred.get("output")
    if isinstance(out_url, list):
        out_url = next((u for u in out_url if isinstance(u, str)), None)
    if not isinstance(out_url, str) or not out_url.startswith("http"):
        return {"error": "The cloud model finished but returned no video file."}

    say("Downloading the clip")
    import video
    title = shot[:60] or "AI clip"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "ai-clip"
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(out_url.split("?")[0])[1].lower() or ".mp4"
    out = folder / f"{slug}-cloud-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    try:
        with httpx.stream("GET", out_url, timeout=300) as resp:
            if resp.status_code != 200:
                return {"error": "The clip was made but the download failed."}
            with open(out, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    except Exception as e:
        return {"error": f"The clip was made but downloading it failed ({type(e).__name__})."}
    if out.stat().st_size < 2000:
        return {"error": "The cloud service returned an empty file."}

    secs = video.media_seconds(out) or seconds
    detail = {"engine": f"Cloud ({model})", "prompt": shot, "format": fmt, "notes": ["Made on Replicate (paid)."]}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (user["id"], title + " (cloud clip)", str(out), "", secs, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": title, "saved": str(out), "seconds": round(secs, 1), "engine": "cloud"}
