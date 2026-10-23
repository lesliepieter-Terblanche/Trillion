"""Kokoro: a free voice that runs on this PC (no ElevenLabs tokens). Kokoro-82M through kokoro-onnx (Apache-2.0,
github.com/thewh1teagle/kokoro-onnx; model by hexgrad, github.com/hexgrad/kokoro, Apache-2.0).

The model (about 120 MB) downloads once, in the background, the first time a login picks the Kokoro voice. Until it is
ready - or if anything goes wrong - Trillion simply falls back to the PC's own voice, so speech never stops.
"""
import io
import threading
import time
import wave

import core

DIR = core.DATA / "models" / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {"kokoro-v1.0.int8.onnx": 80_000_000, "voices-v1.0.bin": 20_000_000}   # name: smallest sane size
SR = 24000
DEFAULT_VOICE = "bf_emma"
# the English voices worth offering (a = American, b = British; f = female, m = male)
VOICES = {
    "bf_emma": "Emma (British, female)", "bf_isabella": "Isabella (British, female)", "bf_alice": "Alice (British, female)",
    "bm_george": "George (British, male)", "bm_fable": "Fable (British, male)", "bm_lewis": "Lewis (British, male)",
    "af_heart": "Heart (American, female)", "af_bella": "Bella (American, female)", "af_nicole": "Nicole (American, female)",
    "am_michael": "Michael (American, male)", "am_fenrir": "Fenrir (American, male)", "am_puck": "Puck (American, male)",
}
STATE = {"status": "not_downloaded", "error": "", "model": None}
_LOCK = threading.Lock()          # one sentence at a time through the model
_DL = threading.Lock()


def _have_files():
    return all((DIR / n).exists() and (DIR / n).stat().st_size >= size for n, size in FILES.items())


def status():
    if STATE["model"] is not None:
        return "ready"
    if _have_files() and STATE["status"] not in ("loading", "error"):
        return "downloaded"
    return STATE["status"]


def _download():
    import httpx
    with _DL:
        if _have_files():
            return
        STATE.update(status="downloading", error="")
        DIR.mkdir(parents=True, exist_ok=True)
        for name, size in FILES.items():
            dest = DIR / name
            if dest.exists() and dest.stat().st_size >= size:
                continue
            part = dest.with_suffix(dest.suffix + ".part")
            with httpx.stream("GET", RELEASE + name, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            if part.stat().st_size < size:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"{name} downloaded incompletely")
            part.replace(dest)


def _load():
    if STATE["model"] is not None:
        return STATE["model"]
    with _LOCK:
        if STATE["model"] is None:
            _download()
            STATE["status"] = "loading"
            from kokoro_onnx import Kokoro
            STATE["model"] = Kokoro(str(DIR / "kokoro-v1.0.int8.onnx"), str(DIR / "voices-v1.0.bin"))
            STATE["status"] = "ready"
    return STATE["model"]


def prepare():
    """Get the voice ready in the background (download + load), without holding anything up."""
    if STATE["model"] is not None or STATE["status"] in ("downloading", "loading"):
        return

    def go():
        try:
            _load()
        except Exception as e:
            STATE.update(status="error", error=f"{type(e).__name__}: {e}"[:300], retry_at=time.time() + 600)
    STATE["status"] = "downloading" if not _have_files() else "loading"
    threading.Thread(target=go, daemon=True).start()


def synth(text, voice=None, speed=1.0):
    """Words -> (float samples, sample rate). Raises if the voice is not ready yet (callers fall back)."""
    if STATE["model"] is None:
        if STATE["status"] == "error" and STATE.get("retry_at", 0) > time.time():
            raise RuntimeError(STATE["error"] or "Kokoro is not available")
        prepare()
        raise RuntimeError("Kokoro is still getting ready")
    v = voice if voice in VOICES else DEFAULT_VOICE
    text = " ".join(str(text or "").split())[:1200]
    if not text:
        import numpy as np
        return np.zeros(0, dtype="float32"), SR
    with _LOCK:
        samples, sr = STATE["model"].create(text, voice=v, speed=float(speed or 1.0),
                                            lang="en-gb" if v.startswith("b") else "en-us")
    return samples, sr


def wav_bytes(samples, sr):
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm16k(samples, sr):
    """Samples resampled to 16 kHz 16-bit, for phone calls."""
    import numpy as np
    if len(samples) == 0:
        return np.zeros(0, dtype=np.int16)
    n = int(len(samples) * 16000 / sr)
    out = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return (np.clip(out, -1, 1) * 32767).astype(np.int16)


def synth_wait(text, voice=None, speed=1.0, wait=180):
    """Like synth, but waits for the voice to finish loading (for jobs that are not live, such as videos)."""
    end = time.time() + wait
    prepare()
    while STATE["model"] is None and time.time() < end and STATE["status"] != "error":
        time.sleep(1)
    return synth(text, voice, speed)
