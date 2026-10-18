"""Cameras: view your RTSP cameras (O-Kam Pro PTZ, Wyze via the bridge, any ONVIF/RTSP cam) from Trillion.

Everything is local: Trillion grabs a still frame straight off the camera's RTSP stream with ffmpeg
(which it already has), saves it, and serves it to the dashboard / your phone, or casts it to a Nest
display. PTZ (moving the camera) is best-effort over ONVIF, which only some models support.

Cameras are managed by the owner and visible to every login (they are the household's cameras).
Passwords are the camera's own RTSP credentials, kept in this database on the PC; nothing goes to a cloud.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import core

SNAP_DIR = core.DATA / "camsnaps"
SNAP_DIR.mkdir(parents=True, exist_ok=True)


class CameraError(Exception):
    pass


def init():
    core.x("""CREATE TABLE IF NOT EXISTS cameras(id INTEGER PRIMARY KEY, name TEXT, url TEXT, kind TEXT DEFAULT 'rtsp',
              onvif TEXT DEFAULT '', created REAL)""")


# ---------- presets: build the fiddly RTSP URL for common cameras ----------
def okam_url(ip, password, lens=0, user="admin"):
    """O-Kam Pro: RTSP on port 10554. Lens 0 is the main view; a dual-lens cam usually has lens 1 too."""
    return f"rtsp://{user}:{password}@{ip}:10554/tcp/av0_{int(lens)}"


def wyze_url(bridge_host, cam_name):
    """docker-wyze-bridge serves each camera as rtsp://<bridge>:8554/<camera-nickname> (lowercase, dashes)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(cam_name).lower()).strip("-")
    return f"rtsp://{bridge_host}:8554/{slug}"


# ---------- registry ----------
def add(name, url, kind="rtsp", onvif=None):
    name = (name or "").strip()[:60]
    url = (url or "").strip()
    if not name or not re.match(r"rtsp://", url, re.I):
        raise CameraError("A camera needs a name and an rtsp:// address.")
    return core.x("INSERT INTO cameras(name,url,kind,onvif,created) VALUES(?,?,?,?,?)",
                  (name, url, kind, json.dumps(onvif) if onvif else "", time.time()))


def remove(cid):
    core.x("DELETE FROM cameras WHERE id=?", (int(cid),))


def all_cams():
    return [dict(r) for r in core.q("SELECT * FROM cameras ORDER BY name")]


def listing():
    """Safe view for the UI/tools: never exposes the password inside the URL."""
    out = []
    for r in core.q("SELECT * FROM cameras ORDER BY name"):
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"],
                    "address": re.sub(r"//[^@/]+@", "//", r["url"]),
                    "ptz": bool(r["onvif"])})
    return out


def find(name_or_id):
    s = str(name_or_id).strip()
    if s.isdigit():
        r = core.q("SELECT * FROM cameras WHERE id=?", (int(s),), one=True)
        if r:
            return dict(r)
    want = re.sub(r"[^a-z0-9]", "", s.lower())
    for r in core.q("SELECT * FROM cameras ORDER BY name"):
        if want and want in re.sub(r"[^a-z0-9]", "", r["name"].lower()):
            return dict(r)
    return None


# ---------- snapshot ----------
def _ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def snapshot(cam, timeout=25):
    """Grab one frame from the camera's RTSP stream. Returns the saved filename (in SNAP_DIR)."""
    fn = f"cam{cam['id']}_{int(time.time())}.jpg"
    out = SNAP_DIR / fn
    cmd = [_ffmpeg(), "-nostdin", "-y"]
    if str(cam["url"]).lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]          # TCP is far more reliable than UDP for RTSP snapshots
    cmd += ["-i", cam["url"], "-frames:v", "1", "-q:v", "3", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CameraError(f"{cam['name']} did not answer in time. Check it is on and the PC can reach it on the network.")
    if not out.exists() or out.stat().st_size == 0:
        tail = (r.stderr or "").strip().splitlines()[-1:] if r.stderr else []
        raise CameraError(f"Could not get a picture from {cam['name']}. "
                          + (tail[0][:160] if tail else "Check the address, username and password."))
    _prune()
    return fn


def _prune(keep_seconds=3600):
    now = time.time()
    for f in SNAP_DIR.glob("*.jpg"):
        try:
            if now - f.stat().st_mtime > keep_seconds:
                f.unlink()
        except Exception:
            pass


def snap_path(name):
    if not re.fullmatch(r"[A-Za-z0-9_]+\.jpg", name or ""):
        return None
    p = (SNAP_DIR / name).resolve()
    if p.parent != SNAP_DIR.resolve() or not p.exists():
        return None
    return p


# ---------- PTZ (best-effort, ONVIF) ----------
_MOVES = {"left": (-0.5, 0, 0), "right": (0.5, 0, 0), "up": (0, 0.5, 0), "down": (0, -0.5, 0),
          "zoom_in": (0, 0, 0.5), "zoom_out": (0, 0, -0.5)}


def ptz(cam, action, seconds=0.6):
    """Move a PTZ camera over ONVIF. Needs the 'onvif-zeep' library and an ONVIF-capable camera."""
    cfg = {}
    try:
        cfg = json.loads(cam.get("onvif") or "{}")
    except Exception:
        cfg = {}
    if not cfg.get("host"):
        raise CameraError(f"{cam['name']} has no ONVIF details set, so I can't move it. Add them on the Cameras page "
                          "(many cameras don't support ONVIF, so moving may not be possible).")
    try:
        from onvif import ONVIFCamera
    except Exception:
        raise CameraError("Moving a camera needs the 'onvif-zeep' part. Run Setup again, or 'pip install onvif-zeep'.")
    action = (action or "").lower()
    try:
        cam_o = ONVIFCamera(cfg["host"], int(cfg.get("port", 80)), cfg.get("user", "admin"), cfg.get("pass", ""))
        media = cam_o.create_media_service()
        ptz_s = cam_o.create_ptz_service()
        token = media.GetProfiles()[0].token
        if action == "stop":
            ptz_s.Stop({"ProfileToken": token})
            return "stopped"
        if action not in _MOVES:
            raise CameraError("Move must be left, right, up, down, zoom_in, zoom_out or stop.")
        x, y, z = _MOVES[action]
        req = ptz_s.create_type("ContinuousMove")
        req.ProfileToken = token
        req.Velocity = {"PanTilt": {"x": x, "y": y}, "Zoom": {"x": z}}
        ptz_s.ContinuousMove(req)
        time.sleep(max(0.1, min(float(seconds), 3.0)))
        ptz_s.Stop({"ProfileToken": token})
        return action
    except CameraError:
        raise
    except Exception as e:
        raise CameraError(f"Could not move {cam['name']}: {type(e).__name__}. It may not support ONVIF control.")
