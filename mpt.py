"""Cinematic video engine: MoneyPrinterTurbo (MIT), run locally in its own isolated space.

MoneyPrinterTurbo turns a subject into a finished short video - the local brain writes the script, real
stock footage (Pexels/Pixabay) becomes the moving background, with a free Edge-TTS voice, subtitles and
music. It is a big app, so it lives in its OWN cloned repo + venv under data/mpt and is driven headlessly
through its CLI; it never touches Trillion's own packages. The brain is your local Ollama model and the
footage uses the same free Pixabay key the classic engine uses - nothing paid, nothing leaves the PC bar
the footage lookups.

This is the opt-in upgrade over video.py (the classic engine). The classic engine stays as a fallback
until this one is proven on the PC.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import core

ROOT = core.DATA / "mpt"
REPO = ROOT / "MoneyPrinterTurbo"
REPO_URL = "https://github.com/harry0703/MoneyPrinterTurbo.git"
JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()

ASPECT = {"vertical": "9:16", "portrait": "9:16", "landscape": "16:9", "horizontal": "16:9",
          "wide": "16:9", "youtube": "16:9", "square": "1:1"}


def _git():
    return shutil.which("git") or "git"


def _uv():
    """uv as a command list. It ships inside Trillion's venv, so it's usually not on PATH - find its binary
    via the uv package, or next to the running Python, before giving up."""
    p = shutil.which("uv")
    if p:
        return [p]
    try:
        import uv as _uvpkg
        b = _uvpkg.find_uv_bin()
        if b and os.path.exists(b):
            return [b]
    except Exception:
        pass
    base = Path(sys.executable).parent
    for name in ("uv.exe", "uv", "Scripts/uv.exe", "bin/uv"):
        cand = base / name
        if cand.exists():
            return [str(cand)]
    return None


def ready():
    """Installed and runnable: the repo is cloned, deps are in, and a config.toml exists."""
    return (REPO / "cli.py").exists() and (REPO / "config.toml").exists() and (REPO / ".venv").exists()


# ---------- config ----------
def _model():
    s = core.settings(core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True) or {})
    pref = s.get("heavy_model") or s.get("local_model") or "qwen2.5:14b"
    try:
        return core.pick_local_model(pref)
    except Exception:
        return pref


def _pixabay_key():
    owner = core.q("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    # use the saved key whether or not it's toggled on - the user chose this engine to get footage
    return (core.get_secret(owner["id"], "pixabay", only_enabled=False) if owner else None) or ""


def _set_key(text, key, value, section="app"):
    """Set `key = value` inside config.toml text: replace the line if present, else add it under [section]."""
    pat = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=).*$")
    if pat.search(text):
        return pat.sub(rf"\1 {value}", text, count=1)
    sec = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
    m = sec.search(text)
    if m:
        i = m.end()
        return text[:i] + f"\n{key} = {value}" + text[i:]
    return text + f"\n[{section}]\n{key} = {value}\n"


def write_config():
    """Point MoneyPrinterTurbo at the local Ollama brain and the same free Pixabay key the classic engine uses."""
    example = REPO / "config.example.toml"
    cfg = REPO / "config.toml"
    text = cfg.read_text(encoding="utf-8") if cfg.exists() else (example.read_text(encoding="utf-8") if example.exists() else "[app]\n")
    base = (core.OLLAMA.rstrip("/")) + "/v1"
    key = _pixabay_key()
    text = _set_key(text, "llm_provider", '"ollama"')
    text = _set_key(text, "ollama_base_url", f'"{base}"')
    text = _set_key(text, "ollama_model_name", f'"{_model()}"')
    text = _set_key(text, "video_source", '"pixabay"')
    text = _set_key(text, "pixabay_api_keys", f'["{key}"]' if key else "[]")
    text = _set_key(text, "subtitle_provider", '"whisper"')
    ff = shutil.which("ffmpeg")
    if ff:
        text = _set_key(text, "ffmpeg_path", f'"{ff}"')
    cfg.write_text(text, encoding="utf-8")
    return str(cfg)


# ---------- install ----------
def _run(cmd, timeout, log, cwd=None):
    log.append("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if r.stdout:
        log.append(r.stdout[-1500:])
    if r.returncode != 0:
        log.append((r.stderr or "")[-1500:])
    return r.returncode


def install():
    log = []
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        if not (REPO / "cli.py").exists():
            if _run([_git(), "clone", "--depth", "1", REPO_URL, str(REPO)], 1200, log) != 0:
                raise RuntimeError("Could not download MoneyPrinterTurbo. " + (log[-1] if log else ""))
        else:
            _run([_git(), "-C", str(REPO), "pull", "--ff-only"], 300, log)
        uv = _uv()
        if not uv:
            raise RuntimeError("uv is needed to set up the video engine. Run Setup again.")
        if _run(uv + ["sync"], 3600, log, cwd=str(REPO)) != 0:        # creates .venv and installs MoneyPrinterTurbo's deps
            raise RuntimeError("Could not install the video engine's parts. " + (log[-1] if log else ""))
        write_config()
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up the cinematic video engine (a big one-time download)",
                   error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")), error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Cinematic video engine ready" if res.get("ok") else "Video engine setup failed",
                         "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


# ---------- making a video ----------
def _newest_output(since):
    best, best_t = None, since
    for p in glob.glob(str(REPO / "storage" / "tasks" / "**" / "*.mp4"), recursive=True):
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        name = os.path.basename(p).lower()
        if mt >= best_t or (best is None and mt > since - 5):
            if "final" in name or "combined" in name or best is None:
                best, best_t = p, mt
    return best


def create(user, prompt, seconds=20, fmt="vertical", voiceover=True, progress=None, media=None):
    """Make a video with MoneyPrinterTurbo. Same return shape as the classic engine so callers don't care which ran."""
    say = progress or (lambda t: None)
    if not ready():
        raise RuntimeError("The cinematic engine isn't set up yet. Open the Videos page and press 'Set up the cinematic engine'.")
    if not _pixabay_key():
        raise RuntimeError("Add a free Pixabay key under Settings > Keys first - it's what fetches the moving footage.")
    write_config()                                     # refresh in case the model or key changed
    aspect = ASPECT.get(str(fmt or "vertical").lower(), "9:16")
    uv = _uv()
    if not uv:
        raise RuntimeError("uv is needed to run the video engine. Run Setup again.")
    subject = str(prompt)
    try:
        import video as _v
        if _v._URL_IN.search(subject):                 # ground the subject in the real site so it stays on-brand
            say("Reading the website")
            subject = _v.site_subject(core.LLM, user, subject)
    except Exception:
        pass
    say("Writing the script and finding footage (this takes a few minutes)")
    started = time.time()
    cmd = uv + ["run", "python", "cli.py", "--video-subject", subject[:800], "--video-aspect", aspect]
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        raise RuntimeError("The video took too long and was stopped. Try a shorter, simpler subject.")
    out = _newest_output(started)
    if not out or not os.path.exists(out):
        tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["no output"])[-1]
        raise RuntimeError("MoneyPrinterTurbo did not produce a video. " + tail[:200])
    say("Saving")
    uid = user["id"]
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", str(prompt).lower()).strip("-")[:40] or "video"
    dest = folder / f"{slug}-{stamp}.mp4"
    shutil.copy2(out, dest)
    thumb = str(dest)[:-4] + ".jpg"
    try:
        ff = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run([ff, "-y", "-v", "error", "-ss", "1.2", "-i", str(dest), "-frames:v", "1", "-vf", "scale=360:-2", thumb],
                       capture_output=True, timeout=60)
    except Exception:
        thumb = ""
    try:
        secs = None
        import video as _v
        secs = _v.media_seconds(str(dest))
    except Exception:
        secs = seconds
    detail = {"engine": "MoneyPrinterTurbo", "format": fmt, "aspect": aspect, "subject": str(prompt)[:200],
              "request": str(prompt), "voiceover": bool(voiceover), "media": [],
              "notes": ["Made with the cinematic engine (MoneyPrinterTurbo): real stock footage, local brain, free voice."]}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (uid, str(prompt)[:80], str(dest), thumb if thumb and os.path.exists(thumb) else "",
                  secs or seconds, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": str(prompt)[:80], "saved": str(dest), "seconds": round(secs or seconds, 1),
            "footage_clips": None, "ai_pictures": 0, "voice_over": True, "engine": "MoneyPrinterTurbo",
            "notes": detail["notes"]}


def state():
    return {"ready": ready(), "job": dict(JOB), "has_key": bool(_pixabay_key())}
