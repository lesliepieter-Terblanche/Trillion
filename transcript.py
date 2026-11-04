"""Transcripts for Trillion: pull the words out of a YouTube video or a podcast from just a link — free and
local. Great for turning a reference video into a script, researching a topic, or summarising a talk.

How it gets the words, cheapest first:
  1. YouTube captions via youtube-transcript-api (instant, no download).
  2. yt-dlp subtitles / auto-captions (covers podcasts and other sites, and YouTube when 1 fails).
  3. Last resort: download the audio with yt-dlp and transcribe it on this PC with the local Whisper model.

Nothing paid and no API key. youtube-transcript-api and yt-dlp install once (small), like the other helpers.
"""
import re
import subprocess
import sys
import threading

import core

JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()

_YT_ID = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/|v/))([A-Za-z0-9_-]{11})")


def ready():
    try:
        import youtube_transcript_api  # noqa: F401
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


# ---------- setup ----------
def install():
    log = []
    try:
        for args in ([sys.executable, "-m", "pip", "install", "--upgrade", "--break-system-packages",
                      "youtube-transcript-api", "yt-dlp"],
                     [sys.executable, "-m", "pip", "install", "--upgrade", "youtube-transcript-api", "yt-dlp"]):
            r = subprocess.run(args, capture_output=True, text=True, timeout=1200)
            log.append((r.stdout or "")[-1200:])
            if r.returncode == 0:
                break
        if r.returncode != 0:
            log.append((r.stderr or "")[-1200:])
            raise RuntimeError("Could not install the transcript tools. " + (r.stderr or "")[-200:])
        import importlib
        importlib.invalidate_caches()
        if not ready():
            raise RuntimeError("Installed but could not import the transcript tools.")
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up transcripts", error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")), error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Transcripts ready" if res.get("ok") else "Transcript setup failed",
                         "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


def state():
    return {"ready": ready(), "job": dict(JOB)}


# ---------- helpers ----------
def _clean(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text


def _from_captions_api(vid):
    """YouTube captions via youtube-transcript-api. Returns (text, lang) or (None, None)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return None, None
    try:
        try:
            lst = YouTubeTranscriptApi.list_transcripts(vid)
            tr = None
            for want in (["en", "en-GB", "en-US"],):
                try:
                    tr = lst.find_transcript(want)
                    break
                except Exception:
                    pass
            if tr is None:                       # any language, translated to English if possible
                tr = next(iter(lst))
                try:
                    if tr.is_translatable:
                        tr = tr.translate("en")
                except Exception:
                    pass
            rows = tr.fetch()
            lang = getattr(tr, "language_code", "")
        except Exception:
            rows = YouTubeTranscriptApi.get_transcript(vid, languages=["en", "en-GB", "en-US"])
            lang = "en"
        text = _clean(" ".join(r.get("text", "") for r in rows if r.get("text")))
        return (text or None), lang
    except Exception:
        return None, None


def _ydl_info(url):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": ["en.*", "en"]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _parse_vtt(data):
    out = []
    for line in str(data or "").splitlines():
        line = line.strip()
        if not line or "-->" in line or line.upper().startswith(("WEBVTT", "NOTE", "KIND", "LANGUAGE")) or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line)           # inline timing tags
        out.append(line)
    # collapse consecutive duplicate lines (auto-captions repeat a lot)
    dedup = []
    for ln in out:
        if not dedup or dedup[-1] != ln:
            dedup.append(ln)
    return _clean(" ".join(dedup))


def _from_ydl_subs(info):
    """Fetch a subtitle/auto-caption track yt-dlp found and turn it into text. Returns (text, lang) or (None,None)."""
    import httpx
    subs = {}
    subs.update(info.get("subtitles") or {})
    autos = info.get("automatic_captions") or {}
    for k, v in autos.items():
        subs.setdefault(k, v)
    if not subs:
        return None, None
    langs = [k for k in subs if k.lower().startswith("en")] or list(subs)
    for lang in langs:
        tracks = subs.get(lang) or []
        pick = next((t for t in tracks if t.get("ext") in ("vtt", "srv3", "json3")), tracks[0] if tracks else None)
        if not pick or not pick.get("url"):
            continue
        try:
            r = httpx.get(pick["url"], timeout=30, follow_redirects=True)
            if r.status_code != 200:
                continue
            if pick.get("ext") == "json3" or r.text.lstrip().startswith("{"):
                import json as _json
                events = (_json.loads(r.text).get("events") or [])
                text = _clean(" ".join(seg.get("utf8", "") for e in events for seg in (e.get("segs") or [])))
            else:
                text = _parse_vtt(r.text)
            if text:
                return text, lang
        except Exception:
            continue
    return None, None


def _from_whisper(url, say):
    """Last resort: download the audio and transcribe it on this PC with the local Whisper model."""
    import tempfile, os, glob
    try:
        import yt_dlp
        import app as _app
    except Exception:
        return None
    say("No captions found — downloading the audio to transcribe it on this PC (slower)")
    tmp = tempfile.mkdtemp(prefix="trans-")
    out = os.path.join(tmp, "a.%(ext)s")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "format": "bestaudio/best", "outtmpl": out}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = glob.glob(os.path.join(tmp, "a.*"))
        if not files:
            return None
        with open(files[0], "rb") as f:
            data = f.read()
        say("Transcribing on the local model")
        return _clean(_app.transcribe(data))
    except Exception:
        return None
    finally:
        try:
            for f in glob.glob(os.path.join(tmp, "*")):
                os.remove(f)
            os.rmdir(tmp)
        except Exception:
            pass


# ---------- the one call ----------
def get(url, allow_whisper=True, progress=None):
    """Return {ok, title, text, words, url, lang, via} for a video/podcast link. Best-effort, never raises."""
    say = progress or (lambda t: None)
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "Give a video or podcast link."}
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    if not ready():
        return {"ok": False, "error": "Transcripts aren't set up yet. Open the Scrape page and press "
                                      "'Set up transcripts'."}
    title, text, lang, via = "", None, "", ""
    m = _YT_ID.search(url)
    if m:
        say("Reading the YouTube captions")
        text, lang = _from_captions_api(m.group(1))
        if text:
            via = "captions"
    # metadata (and the subtitle fallback) via yt-dlp
    info = None
    if not text or not title:
        try:
            info = _ydl_info(url)
            title = (info.get("title") or "")[:200]
        except Exception:
            info = None
    if not text and info is not None:
        say("Looking for a subtitle track")
        text, lang = _from_ydl_subs(info)
        if text:
            via = "subtitles"
    if not text and allow_whisper:
        text = _from_whisper(url, say)
        if text:
            via = "whisper (local)"
    if not text:
        return {"ok": False, "error": "No transcript, captions or subtitles were available for that link, and "
                                      "the audio couldn't be transcribed.", "title": title, "url": url}
    return {"ok": True, "title": title, "url": url, "lang": lang, "via": via,
            "words": len(text.split()), "text": text}
