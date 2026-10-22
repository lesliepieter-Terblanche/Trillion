"""Browser-render photo grabber: open a website in a real (headless) browser, let its JavaScript run, and
lift the actual product photos off the live page - so a video "for vapeessence.co.za" uses the real products,
not stock. Many modern stores (VapeEssence, TrueHome, TackleTrail) are JavaScript apps whose page source has
no images at all; only a browser that runs the page can see them.

This is an OPT-IN engine. It uses Playwright + a headless Chromium, installed once into Trillion's own Python
on the PC. It runs as a short-lived subprocess (sitegrab_run.py) so a browser crash can never take down
Trillion, and so it never fights the web server's event loop. Nothing paid, nothing leaves the PC bar loading
the page you asked for.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

import core

ROOT = core.DATA / "sitegrab"
SENTINEL = ROOT / ".ready"                 # written once the browser is installed and proven
RUNNER = Path(__file__).resolve().parent / "sitegrab_run.py"
JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()


def _have_playwright():
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def ready():
    """Installed and proven: Playwright imports and the one-time browser setup has completed."""
    return _have_playwright() and SENTINEL.exists()


# ---------- install ----------
def _pip(*args, timeout=1800):
    return subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages", *args],
                          capture_output=True, text=True, timeout=timeout)


def install():
    log = []
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        if not _have_playwright():
            log.append("$ pip install playwright")
            r = _pip("playwright")
            log.append((r.stdout or "")[-1200:])
            if r.returncode != 0:
                log.append((r.stderr or "")[-1200:])
                raise RuntimeError("Could not install Playwright. " + (r.stderr or "")[-200:])
        log.append("$ playwright install chromium")
        r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           capture_output=True, text=True, timeout=2400)
        log.append((r.stdout or "")[-1200:])
        if r.returncode != 0:
            log.append((r.stderr or "")[-1200:])
            raise RuntimeError("Could not download the browser. " + (r.stderr or "")[-200:])
        # prove it actually launches before calling it ready
        chk = subprocess.run([sys.executable, str(RUNNER), "--selfcheck"],
                             capture_output=True, text=True, timeout=120)
        if chk.returncode != 0 or "SELFCHECK_OK" not in (chk.stdout or ""):
            log.append((chk.stdout or "")[-800:] + (chk.stderr or "")[-800:])
            raise RuntimeError("The browser installed but would not start. " + (chk.stderr or chk.stdout or "")[-200:])
        SENTINEL.write_text("ok", encoding="utf-8")
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up the browser photo-grabber (a one-time download)",
                   error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")),
                       error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Website photo-grabber ready" if res.get("ok") else "Photo-grabber setup failed",
                         "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


# ---------- grabbing ----------
def grab(url, w, h, uid, limit=8, progress=None):
    """Render the page in a headless browser and return local JPEG paths of the real product photos it shows.
    Falls back to [] on any problem, so the caller can use stock footage instead."""
    say = progress or (lambda t: None)
    if not ready():
        return []
    out_dir = ROOT / "shots" / str(uid)
    out_dir.mkdir(parents=True, exist_ok=True)
    say("Opening the site in a browser to find the photos")
    try:
        r = subprocess.run([sys.executable, str(RUNNER), "--url", str(url), "--out", str(out_dir),
                            "--limit", str(int(limit)), "--w", str(int(w)), "--h", str(int(h))],
                           capture_output=True, text=True, timeout=150)
    except subprocess.TimeoutExpired:
        return []
    paths = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("RESULT "):
            try:
                paths = [p for p in json.loads(line[7:]) if p and Path(p).exists()]
            except Exception:
                paths = []
    if paths:
        say(f"Pulled {len(paths)} real photo{'s' if len(paths) != 1 else ''} off the site")
    return paths


def state():
    return {"ready": ready(), "have_lib": _have_playwright(), "job": dict(JOB)}
