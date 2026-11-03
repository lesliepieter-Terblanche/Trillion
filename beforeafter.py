"""BEFORE / AFTER pitch card for a site Trillion built: the prospect's old site next to the new one, on
desktop and phone, as one image to open an outreach message with. Runs the screenshot + compositing in a
short-lived subprocess (beforeafter_run.py) so a browser crash never touches Trillion's web server.

The card is saved next to the site as before-after.jpg. Needs Playwright + Pillow (the same setup the pitch
video uses); a browser already on the PC (Edge/Chrome) is used, so there's no big download.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import core

RUNNER = Path(__file__).resolve().parent / "beforeafter_run.py"
JOBS = {}            # site id -> {status, step, error, file}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _row(user, sid):
    return core.q("SELECT * FROM gensites WHERE id=? AND user_id=?", (sid, user["id"]), one=True)


def _accent(detail):
    try:
        import web
        style = detail.get("style") or "violet"
        return web.PALETTES.get(style, web.PALETTES["violet"]).get("C_ACCENT", "#C9A84C")
    except Exception:
        return "#C9A84C"


def card_path(user, sid):
    row = _row(user, sid)
    if not row:
        return None
    p = Path(row["folder"]) / "before-after.jpg"
    return str(p) if p.exists() else None


def make(user, sid, old_url, progress=None):
    say = progress or (lambda t: None)
    row = _row(user, sid)
    if not row:
        return {"error": "I couldn't find that website."}
    old_url = str(old_url or "").strip()
    if not old_url:
        return {"error": "Give me the prospect's current website address for the 'before' shot."}
    if not old_url.lower().startswith("http"):
        old_url = "https://" + old_url
    folder = Path(row["folder"])
    index = folder / "index.html"
    if not index.exists():
        return {"error": "The new site's files are missing — rebuild it first."}
    detail = json.loads(row["detail"] or "{}")
    name = row["business"] or detail.get("business") or "Your business"
    out = folder / "before-after.jpg"
    new_url = index.as_uri()
    try:
        import pitchvideo
        exe = pitchvideo._browser_exe()
    except Exception:
        exe = ""
    env = dict(os.environ)
    if exe:
        env["BA_CHROME"] = exe
    say("Taking the before and after shots")
    try:
        r = subprocess.run([__import__("sys").executable, str(RUNNER), old_url, new_url, str(out), name,
                            _accent(detail)], capture_output=True, text=True, timeout=300, env=env,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"error": "The before/after shots took too long — the old site may be very slow. Try again."}
    if "BA_OK" not in (r.stdout or "") or not out.exists():
        tail = ((r.stderr or "") + (r.stdout or ""))[-240:]
        return {"error": "Could not build the card. " + tail}
    say("Card ready")
    return {"ok": True, "site_id": sid, "file": str(out), "old_url": old_url,
            "note": "The before/after card is saved next to the site; show it to the user."}


def start(user, sid, old_url):
    if JOBS.get(sid, {}).get("status") == "running":
        return {"status": "running"}
    JOBS[sid] = {"status": "running", "step": "Starting", "error": "", "file": ""}
    uid = user["id"]

    def work():
        try:
            u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
            r = make(dict(u), sid, old_url, progress=lambda t: JOBS[sid].update(step=t))
            JOBS[sid].update(status="error" if r.get("error") else "done", error=r.get("error", ""),
                             file=r.get("file", ""))
        except Exception as e:
            JOBS[sid].update(status="error", error=f"{type(e).__name__}: {e}"[:300], step="Failed")

    threading.Thread(target=work, daemon=True).start()
    return {"status": "running"}


def status(user, sid):
    j = JOBS.get(sid)
    if not j:
        p = card_path(user, sid)
        return {"status": "done" if p else "idle", "has_card": bool(p)}
    return {"status": j["status"], "step": j.get("step", ""), "error": j.get("error", ""),
            "has_card": bool(card_path(user, sid))}
