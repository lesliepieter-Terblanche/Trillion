"""AI-generated video clips made on this PC's own graphics card by FreeVideo (github.com/FlashML-org/FreeVideo).

FreeVideo is a separate program (installed with "6 - Install FreeVideo.bat"). It runs inside ComfyUI and listens on this
PC only. Trillion does not contain any of it: she just hands it a description over its local address and collects the clip.
"""
import json
import os
import random
import re
import subprocess
import time

import httpx

import core

DEFAULT_URL = os.environ.get("TRILLION_FREEVIDEO_URL", "http://127.0.0.1:8188")
# sizes the engine was measured at (multiples of 32); "light" keeps a 16 GB card comfortable
SIZES = {"landscape": (1344, 768), "vertical": (768, 1344), "square": (1024, 1024)}
MAX_WAIT = int(os.environ.get("TRILLION_FREEVIDEO_WAIT", 90 * 60))
POLL = float(os.environ.get("TRILLION_FREEVIDEO_POLL", 5))
NOT_RUNNING = ("FreeVideo is not running on the PC. Double-click \"6 - Install FreeVideo.bat\" in the Trillion folder: it installs "
               "FreeVideo the first time and opens it after that. Wait until its page opens in the browser, then ask me again.")


def url(user):
    return str(core.settings(user).get("freevideo_url") or DEFAULT_URL).rstrip("/")


def status(user):
    """Is the engine reachable, and does it have the FreeVideo part installed?"""
    base = url(user)
    try:
        r = httpx.get(base + "/object_info/FreeVideoGenerate", timeout=3)
    except Exception:
        return {"running": False, "ready": False, "url": base, "note": "FreeVideo is not running."}
    ok = r.status_code == 200 and "FreeVideoGenerate" in r.text
    return {"running": True, "ready": ok, "url": base,
            "note": "Ready." if ok else "ComfyUI is running but FreeVideo is not installed in it."}


def free_graphics_memory():
    """Her own brain models sit in the graphics card's memory for hours; the video model needs that room."""
    freed = []
    try:
        for m in httpx.get(core.OLLAMA + "/api/ps", timeout=3).json().get("models", []):
            name = m.get("name") or m.get("model")
            if name:
                httpx.post(core.OLLAMA + "/api/generate", json={"model": name, "keep_alive": 0}, timeout=20)
                freed.append(name)
    except Exception:
        pass
    return freed


def describe_shot(llm, user, prompt, seconds):
    """Turn a short request into the rich one-shot description video models respond to. Falls back to the request itself."""
    topic = re.sub(r"^\s*(please\s+)?(create|make|generate|produce|render)\b[^:]{0,60}?(video|clip)s?\b\s*(of|about|showing|with|for|:)?\s*",
                   "", prompt, flags=re.I)
    topic = re.sub(r"\b\d{1,2}[\s-]*(seconds?|secs?|s)\b|\b(vertical|square|landscape|horizontal|widescreen)\b", " ", topic, flags=re.I)
    topic = re.sub(r"\s+", " ", topic).strip(" ,:.-") or prompt.strip()
    title = topic[:60] or "AI clip"
    text = ""
    if llm:
        try:
            text = (llm(user, [
                {"role": "system", "content": "You write prompts for a text-to-video model. Reply with the prompt only: one paragraph, "
                 "70 to 110 words, plain English, no lists, no quotation marks."},
                {"role": "user", "content": f"Write the prompt for one continuous {seconds:g}-second shot of: {topic}\n"
                 "Describe the subject, the setting, the light, the camera movement, what moves in the shot, and the sound "
                 "(ambience or music). Photorealistic and cinematic. No on-screen text, captions or logos."}]).get("content") or "")
        except Exception:
            text = ""
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    if len(text) < 40:
        text = f"A cinematic, photorealistic shot of {topic}. Natural light, gentle camera movement, rich detail, matching ambient sound. No on-screen text."
    return title, text[:1500]


def _mp4_in(outputs):
    """ComfyUI lists what a job saved; find the video among it, whatever key the node used."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if str(x.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov")):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(outputs)
    return found[-1] if found else None


def _why(entry):
    for kind, data in (entry.get("status") or {}).get("messages", []):
        if kind == "execution_error" and isinstance(data, dict):
            return str(data.get("exception_message") or data.get("exception_type") or "")[:300]
    return ""


def create(llm, user, prompt, seconds=5, fmt="vertical", progress=None):
    import video                                             # her own video maker: only for ffmpeg and the videos folder
    say = progress or (lambda text: None)
    uid = user["id"]
    try:
        seconds = float(seconds or 5)
    except (TypeError, ValueError):
        seconds = 5.0
    seconds = max(2.0, min(15.0, seconds))
    fmt = fmt if fmt in SIZES else "vertical"
    w, h = SIZES[fmt]
    st = status(user)
    if not st["running"]:
        return {"error": NOT_RUNNING}
    if not st["ready"]:
        return {"error": "ComfyUI is running on the PC but FreeVideo is not installed in it. Double-click \"6 - Install FreeVideo.bat\" "
                         "in the Trillion folder and choose that ComfyUI folder."}
    base = st["url"]

    say("Writing the shot description")
    title, shot = describe_shot(llm, user, prompt, seconds)
    say("Making room on the graphics card")
    freed = free_graphics_memory()

    graph = {"1": {"class_type": "FreeVideoGenerate",
                   "inputs": {"text": shot, "width": w, "height": h, "seconds": seconds,
                              "seed": random.randint(1, 2 ** 31), "two_pass": True}}}
    try:
        r = httpx.post(base + "/prompt", json={"prompt": graph, "client_id": f"trillion-{uid}"}, timeout=60)
        data = r.json()
    except Exception as e:
        return {"error": f"FreeVideo did not accept the job ({type(e).__name__})."}
    job = data.get("prompt_id") if isinstance(data, dict) else None
    if r.status_code != 200 or not job:
        why = ""
        if isinstance(data, dict):
            errs = [str(e.get("details") or e.get("message") or "") for n in (data.get("node_errors") or {}).values()
                    for e in (n.get("errors") or [])]
            why = "; ".join(x for x in errs if x) or str((data.get("error") or {}).get("message") if isinstance(data.get("error"), dict)
                                                         else data.get("error") or "")
        return {"error": "FreeVideo refused the job" + (f": {why[:300]}" if why else ". Open FreeVideo on the PC and finish its setup first.")}

    started, entry, gone = time.time(), None, 0
    while time.time() - started < MAX_WAIT:
        time.sleep(POLL)
        mins = int((time.time() - started) // 60)
        say(f"Generating the clip on the graphics card ({mins} min so far; a 10-second clip takes about 8 minutes)")
        try:
            hist = httpx.get(f"{base}/history/{job}", timeout=15).json()
        except Exception:
            gone += 1
            if gone > 24:                                    # two minutes of silence: the engine was closed or crashed
                return {"error": "FreeVideo stopped answering while it was working. Check its window on the PC."}
            continue
        gone = 0
        entry = hist.get(job) if isinstance(hist, dict) else None
        if entry and ((entry.get("status") or {}).get("completed") or entry.get("outputs")
                      or (entry.get("status") or {}).get("status_str") == "error"):
            break
        entry = None
    if not entry:
        try:
            httpx.post(base + "/interrupt", timeout=10)
        except Exception:
            pass
        return {"error": f"The clip was not finished after {MAX_WAIT // 60} minutes, so I stopped it."}
    if (entry.get("status") or {}).get("status_str") == "error":
        why = _why(entry)
        return {"error": "FreeVideo could not make the clip" + (f": {why}" if why else ". Its window on the PC shows the reason.")}
    item = _mp4_in(entry.get("outputs") or {})
    if not item:
        return {"error": "FreeVideo finished but I could not find the clip it saved. Look in FreeVideo's own history on the PC."}

    say("Collecting the clip")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "ai-clip"
    folder = core.user_home(user) / "Trillion Videos"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{slug}-ai-{stamp}{os.path.splitext(item['filename'])[1].lower()}"
    try:
        with httpx.stream("GET", base + "/view", timeout=300, params={"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                                                    "type": item.get("type", "output")}) as resp:
            if resp.status_code != 200:
                return {"error": "The clip was made but I could not copy it out of FreeVideo. It is in FreeVideo's own output folder."}
            with open(out, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    except Exception as e:
        return {"error": f"The clip was made but copying it failed ({type(e).__name__}). It is in FreeVideo's own output folder."}
    if out.stat().st_size < 2000:
        return {"error": "FreeVideo handed back an empty file. Try again, or check its window on the PC."}

    thumb, real = str(out)[:-len(out.suffix)] + ".jpg", 0
    try:
        subprocess.run([video.find_ffmpeg(), "-y", "-v", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2", thumb],
                       capture_output=True, timeout=60)
        real = video.media_seconds(out) or 0
    except Exception:
        pass
    took = int(time.time() - started)
    notes = [f"Generated on this PC by FreeVideo in {took // 60} min {took % 60} s; nothing was sent to the internet."]
    if freed:
        notes.append("I cleared my own brain from the graphics card to make room, so my next answer will be slow to start.")
    detail = {"engine": "FreeVideo", "prompt": shot, "format": fmt, "scenes": [], "narration": "", "footage": [], "footage_source": "",
              "music": "", "ai_pictures": 0, "notes": notes}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (uid, title + " (AI clip)", str(out), thumb if os.path.exists(thumb) else "", real or seconds, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": title, "saved": str(out), "seconds": round(real or seconds, 1), "minutes": round(took / 60, 1),
            "notes": notes}
