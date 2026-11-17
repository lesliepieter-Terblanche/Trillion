"""Trillion core: storage, users, settings, secrets, activity, vitals."""
import os, json, time, sqlite3, secrets, hashlib, subprocess, datetime
from pathlib import Path

import httpx
import psutil
from cryptography.fernet import Fernet

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TRILLION_DATA", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
import backup as _backup                                # a restore asked for before the last restart goes in first
_backup.apply_pending(DATA, BASE)
DB = DATA / "trillion.db"
OLLAMA = os.environ.get("TRILLION_OLLAMA", "http://127.0.0.1:11434")

DEFAULT_SETTINGS = {
    "assistant_name": "Trillion",
    "brand": "",
    "accent": "#ffb454",
    "site": "",
    "brain": "local",              # local | gemini | anthropic
    "local_model": "qwen3:14b",    # strong at calling tools; falls back to whatever is installed (see pick_local_model)
    "heavy_model": "mistral-small:24b",   # the bigger local brain for hard/coding jobs (fits 16 GB VRAM at Q4)
    "vision_model": "",                    # local model used to SEE images (webcam) - blank = auto-pick what's installed
    "freevideo_hq": False,                 # two-pass AI video (higher quality, needs more VRAM) - off keeps 16 GB cards happy
    "video_engine": "local",               # where AI clips come from: local (free, FreeVideo) | cloud (paid API) | handoff (Claude app)
    "cloud_video_model": "wan-video/wan-2.2-t2v-a14b",  # the Replicate model used when video_engine is "cloud"
    "heavy_answers": True,                 # route hard jobs (code, deep reasoning) to the heavy model when it is installed
    "cloud_model": "gemini-2.5-flash",
    "voice": "browser",            # browser | kokoro (free, on this PC) | elevenlabs
    "kokoro_voice": "bf_emma",
    "eleven_voice_id": "21m00Tcm4TlvDq8ikWAM",
    "roots": [],                   # folders this login may touch; ["*"] = whole PC
    "personality": "professional", # professional | warm | witty
    "daily_review": True,
    "auto_evolve": False,
    "phone_full_control": False,   # read the screen, tap, type, swipe: off until the login switches it on
    "desktop_control": False,      # owner-only: let Trillion see and operate this PC's screen; off until switched on
    "quick_actions": ["whatsapp", "ring", "find", "briefing", "video", "openwa"],   # the buttons on this login's home screen
    "phone_auto_unlock": False,        # unlock the phone by herself with the saved code when a command needs it
    "fast_answers": True,              # plain questions go to the small quick model; anything she has to do uses the full one
    "fast_model": "qwen3:4b",
    "security_monitor": True,          # owner-only: watch this PC + home network for attacks/intruders and alert
    "security_autodefend": False,      # also act (block the attacker's IP, stop a rogue program) - not just alert
    "security_watch_devices": True,    # also alert on new/unknown devices on the Wi-Fi (off = watch this PC only)
    "wa_inbox": True,                  # show incoming WhatsApp messages (read from the phone's notifications) on the dashboard
    "freevideo_url": "",               # where FreeVideo listens on this PC; empty means the usual http://127.0.0.1:8188
    "video_ai_images": True,           # scenes with no stock footage get an AI-made picture instead of a plain backdrop
    "wake_word": False,                # voice mode only acts on speech that includes her name
    "theme": "night",                  # colour mode: night, day, ocean, forest or royal
    "whatsapp_auto_contacts": False,   # send WhatsApps to saved contacts straight away, without the approval step
    "phone_country_code": "27",    # used to turn 082... into +2782... for WhatsApp
    "call_done_announce": True,    # when a "call me when done" job finishes, also announce it on a Google Home
    "call_done_device": "",        # which cast speaker to announce on (empty = the only/first one)
}
SECRET_NAMES = ["gemini", "anthropic", "elevenlabs", "pixabay", "google_places"]
LLM = None  # set by app.py: the brain, for jobs that need it outside the chat loop


# ---------- database ----------
def _conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def q(sql, a=(), one=False):
    c = _conn()
    try:
        rows = c.execute(sql, a).fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        c.close()


def x(sql, a=()):
    c = _conn()
    try:
        cur = c.execute(sql, a)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE, pw_hash TEXT, salt TEXT,
  is_admin INTEGER DEFAULT 0, settings TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS secrets(user_id INTEGER, name TEXT, value BLOB, enabled INTEGER DEFAULT 1,
  PRIMARY KEY(user_id, name));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, status TEXT,
  domain TEXT DEFAULT 'PERSONAL', detail TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, user_id INTEGER, task_id INTEGER, tool TEXT,
  args TEXT, summary TEXT, status TEXT DEFAULT 'pending', created REAL);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, user_id INTEGER, ts REAL, text TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT,
  domain TEXT DEFAULT 'PERSONAL', ts REAL, emb TEXT);
CREATE TABLE IF NOT EXISTS usage(day TEXT, user_id INTEGER, provider TEXT, calls INTEGER DEFAULT 0,
  PRIMARY KEY(day, user_id, provider));
CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY, task_id INTEGER, ts REAL, kind TEXT, name TEXT, label TEXT DEFAULT '');
"""


def init():
    c = _conn()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()
    if not q("SELECT 1 FROM users LIMIT 1"):
        fam = json.loads((BASE / "family.json").read_text(encoding="utf-8"))
        for f in fam:
            s = dict(DEFAULT_SETTINGS, brand=f.get("brand", ""), accent=f.get("accent", "#ffb454"),
                     site=f.get("site", ""))
            s["roots"] = ["*"] if f.get("is_admin") else []
            x("INSERT INTO users(name,is_admin,settings) VALUES(?,?,?)",
              (f["name"], 1 if f.get("is_admin") else 0, json.dumps(s)))


# ---------- users / auth ----------
def hash_pw(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def set_password(uid, pw):
    salt = secrets.token_hex(16)
    x("UPDATE users SET pw_hash=?, salt=? WHERE id=?", (hash_pw(pw, salt), salt, uid))


def check_password(user, pw):
    if not user["pw_hash"]:
        return False
    return secrets.compare_digest(hash_pw(pw, user["salt"]), user["pw_hash"])


def new_session(uid):
    tok = secrets.token_urlsafe(32)
    x("INSERT INTO sessions VALUES(?,?,?)", (tok, uid, time.time()))
    return tok


def user_by_token(tok):
    if not tok:
        return None
    return q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (tok,), one=True)


def settings(user):
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(user["settings"] or "{}"))
    except Exception:
        pass
    return s


def save_settings(uid, s):
    x("UPDATE users SET settings=? WHERE id=?", (json.dumps(s), uid))


def user_home(user):
    """Where relative paths land. Admin: the Windows user folder. Others: their own Trillion folder."""
    if user["is_admin"]:
        return Path.home()
    p = DATA / "users" / user["name"].lower() / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def out_dir(user, name):
    """Where a finished file of this kind is saved: the drive the owner chose, or the usual place."""
    import storage
    return storage.out_dir(user, name)


def user_roots(user):
    r = settings(user).get("roots") or []
    if user["is_admin"] and not r:
        r = ["*"]
    if "*" in r and not user["is_admin"]:
        r = [x_ for x_ in r if x_ != "*"]
    r = r or [str(user_home(user))]
    if "*" not in r:
        import storage
        extra = storage.user_folder(user)               # finished files saved on another drive are still theirs
        if extra:
            r = r + [str(extra)]
    return r


# ---------- encrypted keys ----------
# A second copy of the key that unlocks saved keys, outside the Trillion folder. If data/secret.key ever goes missing
# or gets replaced (a moved folder, a sync tool, a restore), the saved keys can still be read instead of being lost.
KEY_MIRROR = Path(os.environ.get("APPDATA") or Path.home()) / "Trillion" / "secret.key"
_FERNET = {"f": None}


def _unlocks(key_bytes):
    """True if this key can read the saved keys (or there are none yet to test)."""
    try:
        r = q("SELECT value FROM secrets LIMIT 1", one=True)
    except Exception:
        return True
    if not r:
        return True
    try:
        Fernet(key_bytes).decrypt(r["value"])
        return True
    except Exception:
        return False


def _mirror(key_bytes):
    try:
        KEY_MIRROR.parent.mkdir(parents=True, exist_ok=True)
        if not KEY_MIRROR.exists() or KEY_MIRROR.read_bytes().strip() != key_bytes.strip():
            KEY_MIRROR.write_bytes(key_bytes)
    except Exception:
        pass


def _fernet():
    if _FERNET["f"]:
        return _FERNET["f"]
    kf = DATA / "secret.key"
    main = kf.read_bytes().strip() if kf.exists() else b""
    spare = KEY_MIRROR.read_bytes().strip() if KEY_MIRROR.exists() else b""
    if main and _unlocks(main):
        key = main
    elif spare and _unlocks(spare):              # the data copy is missing or wrong, but the spare still works
        key = spare
        if main:
            kf.with_name("secret.key.replaced").write_bytes(main)
        kf.write_bytes(key)
    elif main:                                    # neither unlocks the saved keys: keep the main one, change nothing
        key = main
    else:                                         # first run: make the key
        key = Fernet.generate_key()
        kf.write_bytes(key)
    _mirror(key)
    _FERNET["f"] = Fernet(key)
    return _FERNET["f"]


def set_secret(uid, name, value, enabled=True):
    x("INSERT INTO secrets(user_id,name,value,enabled) VALUES(?,?,?,?) "
      "ON CONFLICT(user_id,name) DO UPDATE SET value=excluded.value, enabled=excluded.enabled",
      (uid, name, _fernet().encrypt(value.encode()), 1 if enabled else 0))


def get_secret(uid, name, only_enabled=True):
    r = q("SELECT * FROM secrets WHERE user_id=? AND name=?", (uid, name), one=True)
    if not r or (only_enabled and not r["enabled"]):
        return None
    try:
        return _fernet().decrypt(r["value"]).decode()
    except Exception:
        return None


def list_secrets(uid):
    out = []
    for n in SECRET_NAMES:
        r = q("SELECT * FROM secrets WHERE user_id=? AND name=?", (uid, n), one=True)
        v = get_secret(uid, n, only_enabled=False) if r else None
        out.append({"name": n, "set": bool(v), "enabled": bool(r and r["enabled"]),
                    "hint": ("…" + v[-4:]) if v else ""})
    return out


# ---------- activity / tasks / usage ----------
def log(uid, text, kind="info"):
    x("INSERT INTO activity(user_id,ts,text,kind) VALUES(?,?,?,?)", (uid, time.time(), text[:400], kind))


def add_task(uid, title, status="in_progress", domain="PERSONAL"):
    t = time.time()
    return x("INSERT INTO tasks(user_id,title,status,domain,created,updated) VALUES(?,?,?,?,?,?)",
             (uid, title[:120], status, domain, t, t))


ON_TASK_END = []      # called with (task id, status) when a job ends; the watch alerts listen here


def set_task(tid, status=None, detail=None):
    if status is not None:
        x("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, time.time(), tid))
    if detail is not None:
        x("UPDATE tasks SET detail=?, updated=? WHERE id=?", (detail[:200], time.time(), tid))
        if detail.strip() and (status is None or status in ("in_progress", "waiting")):
            add_step(tid, "progress", detail[:60], detail)   # keep a live timeline of what it's doing now
    if status in ("completed", "failed"):
        for fn in ON_TASK_END:
            try:
                fn(tid, status)
            except Exception:
                pass


def add_step(task_id, kind, name, label=""):
    """Record one stop on a job's path (kind: model, skill, connector, tool). Consecutive identical
    stops are collapsed, so the workflow reads as a clean chain rather than a flood of repeats."""
    if not task_id:
        return
    name = (name or "")[:60]
    try:
        last = q("SELECT kind, name FROM steps WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,), one=True)
        if last and last["kind"] == kind and last["name"] == name:
            return
        x("INSERT INTO steps(task_id,ts,kind,name,label) VALUES(?,?,?,?,?)", (task_id, time.time(), kind, name, (label or "")[:140]))
    except Exception:
        pass


def steps_for(task_id):
    return [dict(r) for r in q("SELECT ts, kind, name, label FROM steps WHERE task_id=? ORDER BY id", (task_id,))]


def prune_steps(keep_seconds=86400):
    try:
        x("DELETE FROM steps WHERE ts < ?", (time.time() - keep_seconds,))
    except Exception:
        pass


def today():
    return datetime.date.today().isoformat()


def day_start():
    return datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp()


def bump_usage(uid, provider):
    x("INSERT INTO usage(day,user_id,provider,calls) VALUES(?,?,?,1) "
      "ON CONFLICT(day,user_id,provider) DO UPDATE SET calls=calls+1", (today(), uid, provider))


def usage_today(uid):
    rows = q("SELECT provider, calls FROM usage WHERE day=? AND user_id=?", (today(), uid))
    return {r["provider"]: r["calls"] for r in rows}


# ---------- machine vitals ----------
_gpu_cache = {"t": 0, "v": None}


def gpu():
    if time.time() - _gpu_cache["t"] < 2:
        return _gpu_cache["v"]
    v = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            n, mu, mt, temp, util = [p.strip() for p in out.splitlines()[0].split(",")]
            v = {"name": n, "mem_used": float(mu) / 1024, "mem_total": float(mt) / 1024,
                 "temp": float(temp), "util": float(util)}
    except Exception:
        v = None
    _gpu_cache.update(t=time.time(), v=v)
    return v


def vitals():
    vm = psutil.virtual_memory()
    try:
        du = psutil.disk_usage(Path.home().anchor or "/")
        disk = {"used": du.used / 2 ** 30, "total": du.total / 2 ** 30}
    except Exception:
        disk = None
    return {"cpu": psutil.cpu_percent(interval=None), "ram_used": vm.used / 2 ** 30,
            "ram_total": vm.total / 2 ** 30, "disk": disk, "gpu": gpu()}


_model_cache = {"t": 0, "v": None}


def models():
    if time.time() - _model_cache["t"] < 5 and _model_cache["v"]:
        return _model_cache["v"]
    v = {"ok": False, "list": []}
    try:
        tags = httpx.get(OLLAMA + "/api/tags", timeout=1.5).json().get("models", [])
        try:
            ps = httpx.get(OLLAMA + "/api/ps", timeout=1.5).json().get("models", [])
        except Exception:
            ps = []
        loaded = {m["name"]: m.get("size_vram", 0) for m in ps}
        v = {"ok": True, "list": [{"name": m["name"], "size": m.get("size", 0) / 2 ** 30,
                                   "loaded": m["name"] in loaded,
                                   "vram": loaded.get(m["name"], 0) / 2 ** 30} for m in tags]}
    except Exception:
        pass
    _model_cache.update(t=time.time(), v=v)
    return v


# Local models ranked by how reliably they drive tools, best first. When the chosen brain isn't installed we
# fall back to the best of these that is, instead of just the first model in the list (which could be a tiny one).
# Everyday-brain fallback order (14B-class first so the chat stays fast and fits 16 GB VRAM; the 24B
# mistral-small is the heavy model for hard jobs, so it's last here, not the constant chat brain).
TOOL_MODEL_RANK = ["qwen3", "qwen2.5", "llama3.1", "llama3.3", "hermes3", "command-r", "gemma3", "mistral-small"]


def pick_local_model(pref):
    names = [m["name"] for m in models()["list"] if "embed" not in m["name"]]
    if not names:
        raise RuntimeError("No local model is installed yet. Run Setup, or 'ollama pull qwen3:14b'.")
    if pref in names:
        return pref
    for n in names:                                   # same family, any size (qwen3:14b -> qwen3:8b etc.)
        if n.split(":")[0] == pref.split(":")[0]:
            return n
    for fam in TOOL_MODEL_RANK:                        # else the best tool-caller that is installed
        hit = next((n for n in names if n.split(":")[0] == fam), None)
        if hit:
            return hit
    return names[0]


def has_model(pref):
    """True if a model of this family is installed (so we never route to one that isn't there)."""
    if not pref:
        return False
    fam = pref.split(":")[0]
    return any(m["name"] == pref or m["name"].split(":")[0] == fam
               for m in models()["list"] if "embed" not in m["name"])


def embed(text):
    try:
        r = httpx.post(OLLAMA + "/api/embed", json={"model": "nomic-embed-text", "input": text[:4000]}, timeout=20)
        return r.json()["embeddings"][0]
    except Exception:
        return None
