"""Cameras: view your RTSP cameras (O-Kam Pro PTZ, Wyze via the bridge, any ONVIF/RTSP cam) from Trillion.

Everything is local. Trillion can show a camera two ways, both straight off its RTSP stream with ffmpeg
(which it already has): a still SNAPSHOT (saved and also castable to a Nest display), and a continuous
LIVE view (an MJPEG stream the dashboard plays in a plain <img>, so it works on the PC and on your phone
over Tailscale). PTZ (moving the camera) is best-effort over ONVIF, which only some models support.

Cameras are managed by the owner and visible to every login (they are the household's cameras).
Passwords are the camera's own RTSP/ONVIF credentials. They are kept ENCRYPTED in the key store and put
back into the stream URL only at runtime - never written to a plaintext database column - and nothing
goes to a cloud.
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
    _migrate_passwords()


def _owner_id():
    o = core.q("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    return o["id"] if o else None


_PW_IN_URL = re.compile(r"^(rtsp://[^:/@]+):([^@/]+)@(.*)$", re.I)


def _split_pw(url):
    """Split 'rtsp://user:pass@rest' -> ('rtsp://user@rest', 'pass'). No password -> (url, None)."""
    m = _PW_IN_URL.match((url or "").strip())
    if not m:
        return (url or "").strip(), None
    return f"{m.group(1)}@{m.group(3)}", m.group(2)


def _inject_pw(url, pw):
    """Put a password back into 'rtsp://user@rest' -> 'rtsp://user:pass@rest'."""
    if not pw:
        return url
    m = re.match(r"^(rtsp://[^:/@]+)@(.*)$", url or "", re.I)
    return f"{m.group(1)}:{pw}@{m.group(2)}" if m else url


def resolve_url(cam):
    """The full RTSP URL with the password put back from the encrypted store, used only at runtime.
    Falls back to the stored URL as-is (covers manual full-URL cameras and any un-migrated row)."""
    url = cam.get("url") or ""
    if _PW_IN_URL.match(url):          # already carries a password (legacy / manual entry)
        return url
    oid = _owner_id()
    pw = core.get_secret(oid, f"cam_pw_{cam['id']}") if oid else None
    return _inject_pw(url, pw) if pw else url


def _migrate_passwords():
    """One-off: lift any password embedded in a stored URL into the encrypted secret store."""
    oid = _owner_id()
    if not oid:
        return
    for r in core.q("SELECT * FROM cameras"):
        stripped, pw = _split_pw(r["url"])
        if pw:
            try:
                core.set_secret(oid, f"cam_pw_{r['id']}", pw)
                core.x("UPDATE cameras SET url=? WHERE id=?", (stripped, r["id"]))
            except Exception:
                pass
        # ONVIF password hidden out of the plaintext JSON column too
        try:
            cfg = json.loads(r["onvif"] or "{}")
        except Exception:
            cfg = {}
        if cfg.get("pass"):
            try:
                core.set_secret(oid, f"cam_onvif_{r['id']}", cfg.pop("pass"))
                core.x("UPDATE cameras SET onvif=? WHERE id=?", (json.dumps(cfg) if cfg else "", r["id"]))
            except Exception:
                pass


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
    stripped, pw = _split_pw(url)                      # keep the password out of the DB column
    onvif_pw = (onvif or {}).pop("pass", None) if isinstance(onvif, dict) else None
    cid = core.x("INSERT INTO cameras(name,url,kind,onvif,created) VALUES(?,?,?,?,?)",
                 (name, stripped, kind, json.dumps(onvif) if onvif else "", time.time()))
    oid = _owner_id()
    if oid:
        if pw:
            core.set_secret(oid, f"cam_pw_{cid}", pw)
        if onvif_pw:
            core.set_secret(oid, f"cam_onvif_{cid}", onvif_pw)
    return cid


def remove_secrets(cid):
    oid = _owner_id()
    if oid:
        for nm in (f"cam_pw_{cid}", f"cam_onvif_{cid}"):
            try:
                core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (oid, nm))
            except Exception:
                pass


def remove(cid):
    core.x("DELETE FROM cameras WHERE id=?", (int(cid),))
    remove_secrets(int(cid))


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
    url = resolve_url(cam)
    cmd = [_ffmpeg(), "-nostdin", "-y"]
    if str(url).lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]          # TCP is far more reliable than UDP for RTSP snapshots
    cmd += ["-i", url, "-frames:v", "1", "-q:v", "3", str(out)]
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


def webcam_snapshot(index=0, timeout=20):
    """Grab one frame from a webcam physically on THIS PC (built-in or USB) - not a network camera.
    Returns the saved filename in SNAP_DIR. Tries OpenCV first, then ffmpeg's DirectShow capture."""
    import os
    fn = f"webcam_{int(time.time())}.jpg"
    out = SNAP_DIR / fn
    # 1) OpenCV - simplest and most reliable when it's installed.
    try:
        import cv2
        backend = getattr(cv2, "CAP_DSHOW", 0) if os.name == "nt" else 0
        cap = cv2.VideoCapture(index, backend)
        try:
            ok, frame = False, None
            if cap.isOpened():
                for _ in range(6):                 # drop the first frames while the sensor warms up / exposes
                    ok, frame = cap.read()
            if ok and frame is not None:
                cv2.imwrite(str(out), frame)
        finally:
            cap.release()
        if out.exists() and out.stat().st_size > 0:
            _prune()
            return fn
    except ImportError:
        pass
    except Exception:
        pass
    # 2) ffmpeg DirectShow fallback on Windows (ffmpeg ships with Trillion, so this needs no extra install).
    if os.name == "nt":
        dev = _first_dshow_video()
        if dev:
            cmd = [_ffmpeg(), "-nostdin", "-y", "-f", "dshow", "-i", f"video={dev}", "-frames:v", "1", str(out)]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except Exception:
                pass
            if out.exists() and out.stat().st_size > 0:
                _prune()
                return fn
    raise CameraError("Could not reach a webcam on this PC. Make sure a camera is connected and not already in "
                      "use by another app (Teams, Zoom, Skype or the Camera app), then try again.")


def _first_dshow_video():
    """Name of the first DirectShow video device, read from ffmpeg's device list (Windows)."""
    try:
        r = subprocess.run([_ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, timeout=15)
        txt = (r.stderr or "") + (r.stdout or "")
        names = re.findall(r'"([^"]+)"\s*\(video\)', txt)        # newer ffmpeg marks the type on the same line
        if names:
            return names[0]
        for m in re.finditer(r'"([^"]+)"', txt):                 # older ffmpeg: just take the first quoted name
            return m.group(1)
    except Exception:
        return None
    return None


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


# ---------- live view (MJPEG over HTTP, plays in a plain <img>) ----------
MJPEG_BOUNDARY = "ffmpeg"   # the boundary ffmpeg's mpjpeg muxer emits


def live_cmd(cam, fps=8, quality=6, height=0):
    """ffmpeg argv that turns the camera's RTSP stream into an MJPEG multipart stream on stdout.
    Low frame rate + optional downscale keeps it light enough for a phone over Tailscale."""
    url = resolve_url(cam)
    cmd = [_ffmpeg(), "-nostdin", "-loglevel", "error"]
    if str(url).lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-fflags", "nobuffer", "-flags", "low_delay"]
    cmd += ["-i", url, "-an", "-r", str(max(1, min(int(fps), 20)))]
    if int(height) > 0:
        cmd += ["-vf", f"scale=-2:{int(height)}"]
    cmd += ["-q:v", str(max(2, min(int(quality), 15))), "-f", "mpjpeg", "-"]
    return cmd


def mjpeg_stream(cam, fps=8, quality=6, height=0, idle_limit=180):
    """Generator: spawn ffmpeg and pipe its MJPEG bytes straight through. Kills ffmpeg when the
    viewer disconnects (the generator is closed) or no data arrives for idle_limit seconds."""
    proc = subprocess.Popen(live_cmd(cam, fps, quality, height),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    start = time.time()
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            start = time.time()
            yield chunk
            if time.time() - start > idle_limit:
                break
    finally:
        try:
            proc.kill()
        except Exception:
            pass


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
    oid = _owner_id()
    onvif_pw = cfg.get("pass") or (core.get_secret(oid, f"cam_onvif_{cam['id']}") if oid else None) \
        or (core.get_secret(oid, f"cam_pw_{cam['id']}") if oid else None) or ""
    action = (action or "").lower()
    try:
        cam_o = ONVIFCamera(cfg["host"], int(cfg.get("port", 80)), cfg.get("user", "admin"), onvif_pw)
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
        detail = (str(e) or "").strip().splitlines()[0] if str(e).strip() else type(e).__name__
        low = detail.lower()
        if any(w in low for w in ("refused", "timed out", "timeout", "unreachable", "no route", "getaddrinfo", "connection")):
            hint = " - the ONVIF port is probably wrong (O-Kam usually uses 8000, not 80). Edit the camera and set ONVIF port to 8000."
        elif any(w in low for w in ("auth", "401", "unauthorized", "not authorized", "sender not authorized", "password")):
            hint = " - the ONVIF username/password is being rejected. For O-Kam it is usually admin plus the password you set in the O-Kam app."
        else:
            hint = " - this camera may simply not support ONVIF control (some O-Kam models only move from the O-Kam app)."
        raise CameraError(f"Could not move {cam['name']}: {detail[:180]}{hint}")
