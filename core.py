"""Trillion core: storage, users, settings, secrets, activity, vitals."""
import os, json, time, sqlite3, secrets, hashlib, subprocess, datetime
from pathlib import Path

import httpx
import psutil
from cryptography.fernet import Fernet

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TRILLION_DATA", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "trillion.db"
OLLAMA = os.environ.get("TRILLION_OLLAMA", "http://127.0.0.1:11434")

DEFAULT_SETTINGS = {
    "assistant_name": "Trillion",
    "brand": "",
    "accent": "#ffb454",
    "site": "",
    "brain": "local",              # local | gemini | anthropic
    "local_model": "qwen2.5:14b",
    "cloud_model": "gemini-2.5-flash",
    "voice": "browser",            # browser | elevenlabs
    "eleven_voice_id": "21m00Tcm4TlvDq8ikWAM",
    "roots": [],                   # folders this login may touch; ["*"] = whole PC
    "personality": "professional", # professional | warm | witty
    "daily_review": True,
    "phone_country_code": "27",    # used to turn 082... into +2782... for WhatsApp
}
SECRET_NAMES = ["gemini", "anthropic", "elevenlabs", "pexels"]
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


def user_roots(user):
    r = settings(user).get("roots") or []
    if user["is_admin"] and not r:
        r = ["*"]
    if "*" in r and not user["is_admin"]:
        r = [x_ for x_ in r if x_ != "*"]
    return r or [str(user_home(user))]


# ---------- encrypted keys ----------
def _fernet():
    kf = DATA / "secret.key"
    if not kf.exists():
        kf.write_bytes(Fernet.generate_key())
    return Fernet(kf.read_bytes())


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


def set_task(tid, status=None, detail=None):
    if status is not None:
        x("UPDATE tasks SET status=?, updated=? WHERE id=?", (status, time.time(), tid))
    if detail is not None:
        x("UPDATE tasks SET detail=?, updated=? WHERE id=?", (detail[:200], time.time(), tid))


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


def pick_local_model(pref):
    names = [m["name"] for m in models()["list"] if "embed" not in m["name"]]
    if not names:
        raise RuntimeError("No local model is installed yet. Run Setup, or 'ollama pull qwen2.5:14b'.")
    if pref in names:
        return pref
    for n in names:
        if n.split(":")[0] == pref.split(":")[0]:
            return n
    return names[0]


def embed(text):
    try:
        r = httpx.post(OLLAMA + "/api/embed", json={"model": "nomic-embed-text", "input": text[:4000]}, timeout=20)
        return r.json()["embeddings"][0]
    except Exception:
        return None
