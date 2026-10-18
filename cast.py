"""Cast to Google Home / Nest speakers and Chromecast on the local network.

Built on pychromecast - it talks to the devices directly over your Wi-Fi, with no Google account and
nothing sent to the cloud. Two things Trillion can do:
  - announce(text): speak a short message out loud on a Google Home / Nest speaker.
  - cast_media(url): play an audio or video URL on a Chromecast or speaker, plus volume/play/pause/stop.

For an announcement Trillion makes the speech into an audio file, serves it from its own web server
(GET /cast/audio/<file>), and tells the speaker to play that address. The speaker fetches it over the
LAN, so the PC and the speaker must be on the same network (they are, through your Deco).

Everything here is best-effort and import-guarded: if pychromecast or a text-to-speech engine is not
installed, the calling tool returns a clear message instead of crashing.
"""
import os
import re
import socket
import time
import uuid

import core

CAST_DIR = core.DATA / "cast"
CAST_DIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("TRILLION_PORT", "8787"))
_cache = {"t": 0, "v": []}


class CastError(Exception):
    pass


def _pc():
    try:
        import pychromecast
        return pychromecast
    except Exception:
        return None


def available():
    return _pc() is not None


def lan_ip():
    """The PC's address on the local network, so a speaker can fetch audio from Trillion."""
    if os.environ.get("TRILLION_LAN_IP"):
        return os.environ["TRILLION_LAN_IP"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def discover(timeout=6, force=False):
    """Google Home / Chromecast devices seen on the network. Cached briefly."""
    pc = _pc()
    if not pc:
        return []
    if not force and time.time() - _cache["t"] < 30 and _cache["v"]:
        return _cache["v"]
    chromecasts, browser = pc.get_chromecasts(timeout=timeout)
    out = []
    for cc in chromecasts:
        out.append({"name": cc.name, "model": getattr(cc, "model_name", "") or getattr(cc, "cast_type", "")})
    try:
        pc.discovery.stop_discovery(browser)
    except Exception:
        pass
    _cache.update(t=time.time(), v=out)
    return out


def _connect(name=None, timeout=8):
    """Find and connect to one device by (partial) name, or the only/first one. Returns (cc, browser, pc)."""
    pc = _pc()
    if not pc:
        raise CastError("Casting isn't set up: pychromecast isn't installed. Run Setup again, or "
                        "'pip install pychromecast'.")
    chromecasts, browser = pc.get_chromecasts(timeout=timeout)
    if not chromecasts:
        raise CastError("No Google Home or Chromecast found on the network. Check they are on and on the same Wi-Fi.")
    cc = None
    if name:
        want = re.sub(r"[^a-z0-9]", "", name.lower())
        cc = next((c for c in chromecasts if want in re.sub(r"[^a-z0-9]", "", (c.name or "").lower())), None)
        if not cc:
            names = ", ".join(c.name for c in chromecasts)
            try:
                pc.discovery.stop_discovery(browser)
            except Exception:
                pass
            raise CastError(f"No cast device called '{name}'. Found: {names}.")
    else:
        cc = chromecasts[0]
    cc.wait(timeout=timeout)
    return cc, browser, pc


def _done(pc, browser):
    try:
        pc.discovery.stop_discovery(browser)
    except Exception:
        pass


def cast_url(url, device=None, content_type="audio/mpeg", title=None):
    """Play a media URL on a device. Returns the device name."""
    cc, browser, pc = _connect(device)
    try:
        cc.media_controller.play_media(url, content_type, title=title)
        cc.media_controller.block_until_active(timeout=8)
        return cc.name
    finally:
        _done(pc, browser)


def _synth(text):
    """Turn text into a small audio file in CAST_DIR. Tries gTTS (natural, needs internet on the PC),
    then pyttsx3 (offline, Windows SAPI). Returns the filename, or None if no engine is available."""
    text = (text or "").strip()
    if not text:
        return None
    fid = uuid.uuid4().hex
    try:
        from gtts import gTTS
        fn = f"{fid}.mp3"
        gTTS(text=text[:600]).save(str(CAST_DIR / fn))
        return fn
    except Exception:
        pass
    try:
        import pyttsx3
        fn = f"{fid}.wav"
        eng = pyttsx3.init()
        eng.save_to_file(text[:600], str(CAST_DIR / fn))
        eng.runAndWait()
        if (CAST_DIR / fn).exists() and (CAST_DIR / fn).stat().st_size > 0:
            return fn
    except Exception:
        pass
    return None


def announce(text, device=None):
    """Speak a short message out loud on a Google Home / cast speaker."""
    fn = _synth(text)
    if not fn:
        raise CastError("I couldn't make the announcement audio: no text-to-speech engine is installed. "
                        "Run Setup again, or 'pip install gTTS' (or pyttsx3).")
    ctype = "audio/mpeg" if fn.endswith(".mp3") else "audio/wav"
    _prune()
    url = f"http://{lan_ip()}:{PORT}/cast/audio/{fn}"
    return cast_url(url, device, ctype, title="Trillion")


def control(action, device=None, level=None):
    """play | pause | stop | volume (level 0-100)."""
    cc, browser, pc = _connect(device)
    try:
        mc = cc.media_controller
        action = (action or "").lower()
        if action in ("pause", "stop_speaking"):
            mc.pause()
        elif action in ("play", "resume", "unpause"):
            mc.play()
        elif action in ("stop", "quiet", "silence"):
            mc.stop()
        elif action in ("volume", "set_volume"):
            if level is None:
                raise CastError("Tell me a volume level from 0 to 100.")
            lvl = float(level)
            cc.set_volume(max(0.0, min(1.0, lvl / 100.0 if lvl > 1 else lvl)))
        else:
            raise CastError("Action must be play, pause, stop or volume.")
        return cc.name
    finally:
        _done(pc, browser)


def _prune(keep_seconds=1800):
    """Clear out old announcement audio files."""
    now = time.time()
    for f in CAST_DIR.glob("*"):
        try:
            if now - f.stat().st_mtime > keep_seconds:
                f.unlink()
        except Exception:
            pass


def audio_path(name):
    """Resolve a served audio file safely inside CAST_DIR (used by the /cast/audio endpoint)."""
    if not re.fullmatch(r"[A-Za-z0-9]+\.(mp3|wav)", name or ""):
        return None
    p = (CAST_DIR / name).resolve()
    if p.parent != CAST_DIR.resolve() or not p.exists():
        return None
    return p
