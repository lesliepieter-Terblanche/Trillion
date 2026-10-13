"""Business apps that run on this PC beside Trillion, in Docker: social posting, email lists, website visitors,
invoices, search rankings, a customer inbox and two automation tools.

The owner installs each app once; it then serves every login. Each login signs up inside the app (or is invited by
the owner) and pastes its own key on the Apps page, so Trillion acts in that app as that login and nobody else.
Install files come from each project's own GitHub, fixed at a known-good version, and are set up with passwords made
on this PC. Nothing here is sent anywhere else.
"""
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import httpx

import core

RAW = os.environ.get("TRILLION_GH_RAW", "https://raw.githubusercontent.com")
ZIP = os.environ.get("TRILLION_GH_ZIP", "https://codeload.github.com")
HOME = core.DATA / "apps"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROJECT = "trillion-{}"

APPS = {
    "postiz": {
        "name": "Postiz", "port": 4007, "group": "Marketing",
        "what": "Schedule posts and videos to TikTok, Facebook, Instagram, YouTube, X, LinkedIn and more from one calendar.",
        "repo": "gitroomhq/postiz-docker-compose", "sha": "dd4969e5e694cd009619a0d53cff14c21104580b", "get": "zip",
        "keep": ["docker-compose.yaml", "dynamicconfig/"], "ram": "about 3 GB",
        "connect": [("api_key", "Public API key", "In Postiz: Settings, Developers, Public API. Copy the key.")],
        "signup": "Open Postiz and create your account (each login makes its own). Then connect your social accounts inside Postiz.",
        "note": "Each social network needs its own developer app keys before Postiz can post to it (TikTok, Facebook, etc.). "
                "Postiz's own guide walks through each one; ask me and I will help.",
    },
    "listmonk": {
        "name": "Listmonk", "port": 9000, "group": "Marketing",
        "what": "Email newsletters and mailing lists: collect buyers and readers, send launches and updates.",
        "repo": "knadh/listmonk", "sha": "99b492d0869dd7e5c7c1a498927450e07f005667", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml"}, "ram": "about 300 MB",
        "connect": [("user", "API user name", "In Listmonk: Admin, Users, New, type API. Give it a name."),
                    ("token", "API token", "Shown once when you create the API user.")],
        "signup": "Sign in with the admin details below, then add an API user for each login under Admin, Users.",
        "note": "To actually send email, add your email provider's SMTP details in Listmonk under Settings, SMTP.",
    },
    "umami": {
        "name": "Umami", "port": 3002, "group": "Marketing",
        "what": "Private visitor statistics for your websites: visitors, pages, where they came from.",
        "repo": "umami-software/umami", "sha": "ec0ff50388c264ed8ce46f00967e92f7e71476ae", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml"}, "ram": "about 400 MB",
        "connect": [("username", "Umami user name", "Your Umami sign-in name."), ("password", "Umami password", "")],
        "signup": "First sign-in is admin / umami. Change that password at once, then add a user for each login under Settings, Users. "
                  "Add each website and paste the tracking code Umami gives you into the site.",
    },
    "invoiceninja": {
        "name": "Invoice Ninja", "port": 8012, "group": "Accounting",
        "what": "Quotes, invoices, payments, expenses and a client portal. Works in rand and with VAT.",
        "repo": "invoiceninja/dockerfiles", "sha": "543448f7f9f1ce322661003b469332b886b954bc", "get": "zip",
        "keep": ["debian/"], "ram": "about 1 GB",
        "connect": [("token", "API token", "In Invoice Ninja: Settings, Account Management, Integrations, API Tokens.")],
        "signup": "Sign in with the admin details below. Add a company for each business (Settings, Account Management) "
                  "and a user for each login.",
        "note": "To email invoices, add your SMTP details in Invoice Ninja under Settings, Email Settings.",
    },
    "serpbear": {
        "name": "SerpBear", "port": 3003, "group": "Marketing",
        "what": "Tracks where your websites rank on Google for the search words you choose, every day.",
        "repo": "towfiqi/serpbear", "sha": "a1328fb4142dde40e0011c646339c4d4dbabdaca", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml"}, "ram": "about 300 MB", "shared": True,
        "connect": [],
        "signup": "Sign in with the details below. Add your websites and search words. Every login can ask me about rankings.",
        "note": "SerpBear needs a free scraping service key (it suggests several) under Settings, Scraper, before it can check Google.",
    },
    "chatwoot": {
        "name": "Chatwoot", "port": 3010, "group": "Customers",
        "what": "One inbox for customer messages from your website chat, WhatsApp, Facebook, Instagram and email.",
        "repo": "chatwoot/chatwoot", "sha": "5e0afa9363b45e5dd727bbd90568fc667610a188", "get": "raw",
        "files": {"docker-compose.production.yaml": "docker-compose.yaml", ".env.example": ".env"}, "ram": "about 2 GB",
        "connect": [("token", "Access token", "In Chatwoot: click your picture, Profile settings, Access Token.")],
        "signup": "Open Chatwoot and create the first account (it becomes the owner). Add an agent for each login under Settings, Agents.",
    },
    "n8n": {
        "name": "n8n", "port": 5678, "group": "Automation",
        "what": "Connect apps with 'when this happens, do that' workflows, for example a new sale adds the buyer to your list.",
        "get": "own", "ram": "about 400 MB",
        "connect": [("api_key", "API key", "In n8n: Settings, n8n API, Create an API key.")],
        "signup": "Open n8n and create the owner account. Invite each login under Settings, Users.",
        "note": "n8n is free to use for your own business under its 'Sustainable Use' licence.",
    },
    "activepieces": {
        "name": "Activepieces", "port": 8085, "group": "Automation",
        "what": "Easier, picture-based automations between hundreds of apps, for when n8n feels too technical.",
        "repo": "activepieces/activepieces", "sha": "03e0f4deb84775cab53f36f426670294c31ce9e2", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml", ".env.example": ".env"}, "ram": "about 1.5 GB",
        "connect": [],
        "signup": "Open Activepieces and create the first account. Invite each login from inside the app.",
    },
}
GROUPS = ["Marketing", "Customers", "Accounting", "Automation"]
_JOBS, _LOCK = {}, threading.Lock()
_CACHE = {"t": 0, "v": None}

core.x("""CREATE TABLE IF NOT EXISTS apps(key TEXT PRIMARY KEY, status TEXT, detail TEXT DEFAULT '', host TEXT DEFAULT '',
          installed REAL, updated REAL, log TEXT DEFAULT '')""")
core.x("UPDATE apps SET status='failed', detail='Trillion restarted during the install. Press Install again.' WHERE status='installing'")


class AppError(Exception):
    pass


# ---------- Docker ----------
def docker_exe():
    p = shutil.which("docker")
    if p:
        return p
    for c in (r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",):
        if os.path.exists(c):
            return c
    return None


def run(args, cwd=None, timeout=120, feed=None):
    exe = docker_exe()
    if not exe:
        raise AppError("Docker is not installed.")
    try:
        if feed is None:
            r = subprocess.run([exe] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        p = subprocess.Popen([exe] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        out, start = [], time.time()
        for line in p.stdout:
            out.append(line.rstrip())
            feed(line.rstrip())
            if time.time() - start > timeout:
                p.kill()
                raise AppError("Docker took too long. Check your internet and press Install again.")
        p.wait()
        return p.returncode, "\n".join(out[-200:])
    except subprocess.TimeoutExpired:
        raise AppError("Docker did not answer in time.")


def docker_state():
    exe = docker_exe()
    if not exe:
        return {"installed": False, "running": False}
    try:
        code, out = run(["info", "--format", "{{.ServerVersion}}"], timeout=20)
    except AppError:
        return {"installed": True, "running": False}
    ok = code == 0 and re.search(r"\d+\.\d+", out or "")
    return {"installed": True, "running": bool(ok), "version": out.strip()[:20] if ok else ""}


def start_docker_desktop():
    for c in (r"C:\Program Files\Docker\Docker\Docker Desktop.exe",):
        if os.path.exists(c):
            subprocess.Popen([c], creationflags=NO_WINDOW)
            return True
    return False


def install_docker():
    """Docker Desktop through winget. Windows asks for permission, and a restart is usually needed after."""
    winget = shutil.which("winget")
    if not winget:
        raise AppError("Windows is missing winget. Install 'App Installer' from the Microsoft Store, then try again.")
    p = subprocess.Popen([winget, "install", "-e", "--id", "Docker.DockerDesktop", "--accept-source-agreements",
                          "--accept-package-agreements", "--silent"], creationflags=NO_WINDOW)
    return p.pid


def running_projects():
    if time.time() - _CACHE["t"] < 8 and _CACHE["v"] is not None:
        return _CACHE["v"]
    found = {}
    try:
        code, out = run(["compose", "ls", "--all", "--format", "json"], timeout=25)
        if code == 0:
            for p in json.loads(out or "[]"):
                found[p.get("Name", "")] = str(p.get("Status", "")).lower()
    except (AppError, ValueError):
        pass
    _CACHE.update(t=time.time(), v=found)
    return found


# ---------- files and settings ----------
def folder(key):
    f = HOME / key
    f.mkdir(parents=True, exist_ok=True)
    return f


def rnd(n=24):
    return secrets.token_hex(n)


def secret(key, name, make=None):
    """A setting made once for this app's install, kept encrypted under the owner."""
    owner = core.q("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    oid = owner["id"] if owner else 1
    v = core.get_secret(oid, f"app:{key}:{name}")
    if not v and make is not None:
        v = make() if callable(make) else make
        core.set_secret(oid, f"app:{key}:{name}", v)
    return v


def fetch(a, dest):
    """Download this app's official install files at the pinned version."""
    if a["get"] == "raw":
        for src, to in a["files"].items():
            r = httpx.get(f"{RAW}/{a['repo']}/{a['sha']}/{src}", timeout=60, follow_redirects=True)
            if r.status_code != 200:
                raise AppError(f"Could not download {src} from GitHub ({r.status_code}). Check your internet and try again.")
            (dest / to).write_bytes(r.content)
    elif a["get"] == "zip":
        r = httpx.get(f"{ZIP}/{a['repo']}/zip/{a['sha']}", timeout=120, follow_redirects=True)
        if r.status_code != 200:
            raise AppError(f"Could not download the install files from GitHub ({r.status_code}).")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = z.namelist()[0].split("/")[0] + "/"
        for name in z.namelist():
            rel = name[len(root):]
            if not rel or name.endswith("/"):
                continue
            if not any(rel == k or (k.endswith("/") and rel.startswith(k)) for k in a["keep"]):
                continue
            for k in a["keep"]:                       # a kept folder's contents land at the top (debian/x -> x)
                if k.endswith("/") and rel.startswith(k) and k != "dynamicconfig/":
                    rel = rel[len(k):]
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(name))


def set_env(path, values):
    """Change KEY=value lines in an env file, adding the ones that are missing."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m and m.group(1) in values:
            lines[i] = f"{m.group(1)}={values[m.group(1)]}"
            done.add(m.group(1))
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def yaml_str(v):
    return json.dumps(str(v))


def configure(key, dest, host):
    """Write this PC's settings: passwords, the address phones use, ports that do not clash."""
    a = APPS[key]
    url = f"http://{host}:{a['port']}"
    over = None
    if key == "postiz":
        over = f"""services:
  postiz:
    environment:
      MAIN_URL: {yaml_str(url)}
      FRONTEND_URL: {yaml_str(url)}
      NEXT_PUBLIC_BACKEND_URL: {yaml_str(url + '/api')}
      JWT_SECRET: {yaml_str(secret(key, 'jwt', rnd))}
  temporal:
    ports: !reset []
  temporal-ui:
    ports: !reset []
"""
    elif key == "listmonk":
        set_env(dest / ".env", {"LISTMONK_ADMIN_USER": "admin", "LISTMONK_ADMIN_PASSWORD": secret(key, "admin_password", lambda: rnd(10))})
        over = """services:
  db:
    ports: !reset []
"""
    elif key == "umami":
        over = f"""services:
  umami:
    ports: !reset ["{a['port']}:3000"]
    environment:
      APP_SECRET: {yaml_str(secret(key, 'app_secret', rnd))}
      TWO_FACTOR_ENCRYPTION_KEY: {yaml_str(secret(key, '2fa', lambda: rnd(32)))}
"""
    elif key == "invoiceninja":
        import base64
        set_env(dest / ".env", {
            "APP_URL": url, "APP_DEBUG": "false",
            "APP_KEY": secret(key, "app_key", lambda: "base64:" + base64.b64encode(os.urandom(32)).decode()),
            "DB_PASSWORD": secret(key, "db", rnd), "MYSQL_PASSWORD": secret(key, "db", rnd),
            "DB_ROOT_PASSWORD": secret(key, "db_root", rnd), "MYSQL_ROOT_PASSWORD": secret(key, "db_root", rnd),
            "IN_USER_EMAIL": secret(key, "admin_email", "admin@trillion.local"),
            "IN_PASSWORD": secret(key, "admin_password", lambda: rnd(10))})
        over = f"""services:
  app:
    pull_policy: always
  nginx:
    ports: !reset ["{a['port']}:80"]
"""
    elif key == "serpbear":
        set_env(dest / ".env", {"USER": "admin", "PASSWORD": secret(key, "admin_password", lambda: rnd(10)),
                                "SECRET": secret(key, "secret", rnd), "APIKEY": secret(key, "api_key", rnd),
                                "PORT": str(a["port"]), "NEXT_PUBLIC_APP_URL": url})
    elif key == "chatwoot":
        pg, rd = secret(key, "db", rnd), secret(key, "redis", rnd)
        set_env(dest / ".env", {"SECRET_KEY_BASE": secret(key, "secret_key_base", lambda: rnd(64)), "FRONTEND_URL": url,
                                "POSTGRES_HOST": "postgres", "POSTGRES_USERNAME": "postgres", "POSTGRES_PASSWORD": pg,
                                "REDIS_URL": "redis://redis:6379", "REDIS_PASSWORD": rd, "RAILS_ENV": "production",
                                "ENABLE_ACCOUNT_SIGNUP": "false"})
        over = f"""services:
  rails:
    ports: !reset ["{a['port']}:3000"]
  postgres:
    ports: !reset []
    environment:
      - POSTGRES_PASSWORD={pg}
  redis:
    ports: !reset []
"""
    elif key == "n8n":
        (dest / "docker-compose.yml").write_text(f"""services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    restart: unless-stopped
    ports:
      - "{a['port']}:5678"
    environment:
      - N8N_HOST={host}
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - WEBHOOK_URL={url}/
      - N8N_SECURE_COOKIE=false
      - N8N_RUNNERS_ENABLED=true
      - GENERIC_TIMEZONE=Africa/Johannesburg
      - TZ=Africa/Johannesburg
    volumes:
      - n8n_data:/home/node/.n8n
volumes:
  n8n_data:
""", encoding="utf-8")
    elif key == "activepieces":
        set_env(dest / ".env", {"AP_API_KEY": secret(key, "api_key", lambda: rnd(64)), "AP_ENCRYPTION_KEY": secret(key, "enc", lambda: rnd(16)),
                                "AP_JWT_SECRET": secret(key, "jwt", rnd), "AP_POSTGRES_PASSWORD": secret(key, "db", rnd),
                                "AP_FRONTEND_URL": url, "AP_TELEMETRY_ENABLED": "false"})
        over = f"""services:
  app:
    container_name: trillion-activepieces-app
    ports: !reset ["{a['port']}:80"]
  worker:
    deploy:
      replicas: 1
  postgres:
    container_name: trillion-activepieces-postgres
  redis:
    container_name: trillion-activepieces-redis
"""
    if over:
        (dest / "docker-compose.override.yml").write_text(over, encoding="utf-8")


def compose_files(dest):
    main = next((n for n in ("docker-compose.yml", "docker-compose.yaml") if (dest / n).exists()), None)
    if not main:
        raise AppError("The install files are missing. Press Install again.")
    args = ["-f", main]
    if (dest / "docker-compose.override.yml").exists():
        args += ["-f", "docker-compose.override.yml"]
    return args


def compose(key, *args, timeout=600, feed=None):
    dest = folder(key)
    return run(["compose", "-p", PROJECT.format(key)] + compose_files(dest) + list(args), cwd=str(dest), timeout=timeout, feed=feed)


# ---------- install and manage ----------
def _set(key, **kw):
    kw["updated"] = time.time()
    if not core.q("SELECT 1 FROM apps WHERE key=?", (key,), one=True):
        core.x("INSERT INTO apps(key,status) VALUES(?,?)", (key, "none"))
    core.x("UPDATE apps SET " + ",".join(f"{k}=?" for k in kw) + " WHERE key=?", tuple(kw.values()) + (key,))


def install(key, host):
    if key not in APPS:
        raise AppError("Unknown app.")
    with _LOCK:
        if any(t.is_alive() for t in _JOBS.values()):
            raise AppError("Another app is being installed. Let it finish first.")
        st = docker_state()
        if not st["installed"]:
            raise AppError("Docker is not installed yet. Press Install Docker first.")
        if not st["running"]:
            raise AppError("Docker is installed but not running. Press Start Docker and wait until it says it is running.")
        _set(key, status="installing", detail="Getting the install files", host=host, log="")
        t = threading.Thread(target=_install, args=(key, host), daemon=True)
        _JOBS[key] = t
        t.start()


def _install(key, host):
    a = APPS[key]
    lines = []

    def feed(line):
        if line.strip():
            lines.append(line[:200])
            _set(key, detail=_friendly(line), log="\n".join(lines[-60:]))

    try:
        dest = folder(key)
        if a["get"] != "own":
            fetch(a, dest)
        configure(key, dest, host)
        if key == "chatwoot":
            _set(key, detail="Preparing Chatwoot's database (first time only)")
            code, out = compose(key, "run", "--rm", "rails", "bundle", "exec", "rails", "db:chatwoot_prepare", timeout=2400, feed=feed)
            if code != 0:
                raise AppError("Chatwoot's database could not be prepared. " + _tail(out))
        _set(key, detail="Downloading and starting (the first time can take 5 to 20 minutes)")
        code, out = compose(key, "up", "-d", timeout=3600, feed=feed)
        if code != 0:
            raise AppError("Docker could not start it. " + _tail(out))
        _set(key, detail="Waiting for it to answer")
        if wait_up(key):
            _set(key, status="installed", installed=time.time(), detail="Running")
            owner = core.q("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
            if owner:
                core.log(owner["id"], f"{a['name']} is installed and running", "done")
        else:
            _set(key, status="installed", installed=time.time(),
                 detail="Started, but not answering yet. Big apps can need a few more minutes; press Refresh.")
    except AppError as e:
        _set(key, status="failed", detail=str(e)[:400])
    except Exception as e:
        _set(key, status="failed", detail=f"Something went wrong: {type(e).__name__}: {e}"[:400])
    finally:
        _CACHE["t"] = 0


def _tail(out):
    bits = [ln for ln in (out or "").splitlines() if ln.strip()][-3:]
    return " | ".join(bits)[:300]


def _friendly(line):
    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
    if re.search(r"pull|download|extract|waiting", line, re.I):
        return "Downloading: " + line[:120]
    return line[:160]


def wait_up(key, seconds=300):
    url = f"http://127.0.0.1:{APPS[key]['port']}/"
    end = time.time() + seconds
    while time.time() < end:
        try:
            r = httpx.get(url, timeout=5, follow_redirects=False)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def action(key, what):
    if key not in APPS:
        raise AppError("Unknown app.")
    row = core.q("SELECT * FROM apps WHERE key=?", (key,), one=True)
    if not row or row["status"] not in ("installed", "failed", "stopped"):
        raise AppError("That app is not installed.")
    if what == "start":
        code, out = compose(key, "up", "-d", timeout=900)
        new = "installed"
    elif what == "stop":
        code, out = compose(key, "stop", timeout=300)
        new = "stopped"
    elif what == "remove":                    # the app goes, its saved data stays for a later install
        code, out = compose(key, "down", timeout=300)
        new = "none"
    elif what == "erase":                     # the app and everything it saved
        code, out = compose(key, "down", "-v", timeout=300)
        if code == 0:
            shutil.rmtree(HOME / key, ignore_errors=True)
        new = "none"
    else:
        raise AppError("Unknown action.")
    _CACHE["t"] = 0
    if code != 0:
        raise AppError("Docker said: " + _tail(out))
    _set(key, status=new, detail={"start": "Running", "stop": "Stopped", "remove": "Removed; its data is kept",
                                  "erase": "Removed with all its data"}[what])
    return True


def listing(user, host):
    st = docker_state()
    projects = running_projects() if st["running"] else {}
    rows = {r["key"]: dict(r) for r in core.q("SELECT * FROM apps")}
    out = []
    for key, a in APPS.items():
        r = rows.get(key) or {"status": "none", "detail": ""}
        status = r["status"]
        live = projects.get(PROJECT.format(key), "")
        if status in ("installed", "stopped") and st["running"]:
            status = "running" if "running" in live else ("stopped" if live else status)
        conn = connection(user, key)
        item = {"key": key, "name": a["name"], "what": a["what"], "group": a["group"], "ram": a["ram"], "status": status,
                "detail": r.get("detail") or "", "url": f"http://{host}:{a['port']}", "port": a["port"],
                "connect": [{"field": f, "label": l, "hint": h} for f, l, h in a["connect"]],
                "connected": bool(conn) or a.get("shared", False) and status == "running",
                "auto": not a["connect"], "signup": a["signup"], "note": a.get("note", ""),
                "can_chat": key in CHAT}
        if user["is_admin"]:
            item["log"] = r.get("log") or ""
            item["admin"] = admin_details(key)
        out.append(item)
    return {"docker": st, "apps": out, "groups": GROUPS}


def admin_details(key):
    """The first sign-in details Trillion made, shown to the owner only."""
    d = {}
    if key == "listmonk":
        d = {"user": "admin", "password": secret(key, "admin_password")}
    elif key == "invoiceninja":
        d = {"user": secret(key, "admin_email"), "password": secret(key, "admin_password")}
    elif key == "serpbear":
        d = {"user": "admin", "password": secret(key, "admin_password")}
    elif key == "umami":
        d = {"user": "admin", "password": "umami (change it at once)"}
    return {k: v for k, v in d.items() if v}


# ---------- each login's own connection ----------
def connection(user, key):
    raw = core.get_secret(user["id"], f"appconn:{key}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def connect(user, key, values):
    a = APPS.get(key)
    if not a:
        raise AppError("Unknown app.")
    vals = {f: str(values.get(f) or "").strip() for f, _, _ in a["connect"]}
    missing = [l for f, l, _ in a["connect"] if not vals[f]]
    if missing:
        raise AppError("Fill in: " + ", ".join(missing))
    test = TESTS.get(key)
    if test:
        extra = test(vals)                      # raises AppError if the app does not accept it
        vals.update(extra or {})
    core.set_secret(user["id"], f"appconn:{key}", json.dumps(vals))
    return True


def disconnect(user, key):
    core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], f"appconn:{key}"))


def base(key):
    return os.environ.get(f"TRILLION_APP_{key.upper()}") or f"http://127.0.0.1:{APPS[key]['port']}"


def http(key, method, path, headers=None, auth=None, **kw):
    try:
        r = httpx.request(method, base(key) + path, headers=headers or {}, auth=auth, timeout=40, **kw)
    except httpx.HTTPError:
        raise AppError(f"{APPS[key]['name']} is not answering. Start it on the Apps page.")
    if r.status_code in (401, 403):
        raise AppError(f"{APPS[key]['name']} did not accept the key. Connect it again on the Apps page.")
    if r.status_code >= 400:
        try:
            msg = r.json()
            msg = msg.get("message") or msg.get("error") or msg.get("detail") or str(msg)
        except ValueError:
            msg = r.text[:200]
        raise AppError(f"{APPS[key]['name']} said: {str(msg)[:200]}")
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


def need(user, key):
    c = connection(user, key)
    if APPS[key].get("shared"):
        return {}
    if not c:
        raise AppError(f"{APPS[key]['name']} is not connected for {user['name']} yet. Connect it on the Apps page.")
    return c


# per app: how Trillion signs in
def _postiz(c):
    return {"Authorization": c["api_key"]}


def _listmonk(c):
    return {"Authorization": f"token {c['user']}:{c['token']}"}


def _umami_token(c):
    j = http("umami", "POST", "/api/auth/login", json={"username": c["username"], "password": c["password"]})
    if j.get("requiresTwoFactor"):
        raise AppError("This Umami user has two-step sign-in on. Make a separate Umami user for Trillion without it.")
    if not j.get("token"):
        raise AppError("Umami did not accept that user name and password.")
    return j["token"]


def _ninja(c):
    return {"X-API-TOKEN": c["token"], "X-Requested-With": "XMLHttpRequest"}


def _chatwoot(c):
    return {"api_access_token": c["token"]}


def _n8n(c):
    return {"X-N8N-API-KEY": c["api_key"]}


def _test_chatwoot(v):
    j = http("chatwoot", "GET", "/api/v1/profile", headers=_chatwoot(v))
    accts = j.get("accounts") or []
    if not accts:
        raise AppError("That Chatwoot user is not in any account yet.")
    return {"account": str(accts[0]["id"])}


TESTS = {
    "postiz": lambda v: http("postiz", "GET", "/api/public/v1/integrations", headers=_postiz(v)) and None,
    "listmonk": lambda v: http("listmonk", "GET", "/api/lists?per_page=1", headers=_listmonk(v)) and None,
    "umami": lambda v: _umami_token(v) and None,
    "invoiceninja": lambda v: http("invoiceninja", "GET", "/api/v1/clients?per_page=1", headers=_ninja(v)) and None,
    "chatwoot": _test_chatwoot,
    "n8n": lambda v: http("n8n", "GET", "/api/v1/workflows?limit=1", headers=_n8n(v)) and None,
}


# ---------- what chat can do in each app ----------
def social_channels(user):
    c = need(user, "postiz")
    items = http("postiz", "GET", "/api/public/v1/integrations", headers=_postiz(c)) or []
    return [{"id": i.get("id"), "name": i.get("name"), "network": i.get("identifier"), "off": bool(i.get("disabled"))} for i in items]


SETTINGS = {"tiktok": lambda title: {"__type": "tiktok", "title": title[:90], "privacy_level": "PUBLIC_TO_EVERYONE", "duet": False,
                                     "stitch": False, "comment": True, "autoAddMusic": "no", "brand_content_toggle": False,
                                     "brand_organic_toggle": False, "content_posting_method": "DIRECT_POST"},
            "instagram": lambda title: {"__type": "instagram", "post_type": "post"},
            "instagram-standalone": lambda title: {"__type": "instagram-standalone", "post_type": "post"},
            "youtube": lambda title: {"__type": "youtube", "title": title[:95] or "New video", "type": "public"},
            "x": lambda title: {"__type": "x", "who_can_reply_post": "everyone"},
            "linkedin": lambda title: {"__type": "linkedin", "post_as_images_carousel": False},
            "pinterest": lambda title: {"__type": "pinterest", "title": title[:95]}}


def social_post(user, text, networks=None, when="", files=None):
    """Post or schedule one message (with pictures or a video) to some or all connected channels."""
    c = need(user, "postiz")
    chans = [x for x in social_channels(user) if not x["off"]]
    if networks:
        want = [str(n).lower().strip() for n in networks]
        chans = [x for x in chans if any(w in (x["network"] or "") or w in (x["name"] or "").lower() for w in want)]
    if not chans:
        raise AppError("No matching social account is connected in Postiz.")
    media = []
    for f in files or []:
        p = Path(f)
        with open(p, "rb") as fh:
            m = http("postiz", "POST", "/api/public/v1/upload", headers=_postiz(c), files={"file": (p.name, fh)})
        if m.get("id") and m.get("path"):
            media.append({"id": m["id"], "path": m["path"]})
    if any(x["network"] in ("tiktok", "youtube", "instagram", "instagram-standalone", "pinterest") for x in chans) and not media:
        raise AppError("TikTok, YouTube, Instagram and Pinterest need a picture or video with the post. Attach one.")
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if when:
        try:
            d = _dt.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=2)))      # South African time
            d = d.astimezone(_dt.timezone.utc)
        except ValueError:
            raise AppError("I could not read that time. Say it like 2026-10-14 18:00.")
        kind = "schedule" if d > now + _dt.timedelta(minutes=1) else "now"
    else:
        d, kind = now, "now"
    title = text.strip().split("\n")[0][:90]
    body = {"type": kind, "date": d.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "shortLink": False, "tags": [],
            "posts": [{"integration": {"id": x["id"]}, "value": [{"content": text, "image": media}],
                       "settings": SETTINGS.get(x["network"], lambda t, n=x["network"]: {"__type": n})(title)} for x in chans]}
    http("postiz", "POST", "/api/public/v1/posts", headers=_postiz(c), json=body)
    return {"posted": kind == "now", "scheduled_for": d.isoformat() if kind == "schedule" else "",
            "channels": [f"{x['name']} ({x['network']})" for x in chans], "media": len(media)}


def email_lists(user):
    c = need(user, "listmonk")
    j = http("listmonk", "GET", "/api/lists?per_page=all", headers=_listmonk(c))
    return [{"id": x["id"], "name": x["name"], "subscribers": x.get("subscriber_count")} for x in (j.get("data") or {}).get("results", [])]


def _list_ids(user, names):
    lists = email_lists(user)
    if not names:
        return [lists[0]["id"]] if lists else []
    out = []
    for n in names:
        hit = next((x for x in lists if str(n).lower() in x["name"].lower()), None)
        if not hit:
            raise AppError(f"There is no Listmonk list called {n}. Lists: {', '.join(x['name'] for x in lists) or 'none yet'}.")
        out.append(hit["id"])
    return out


def email_add(user, email, name="", lists=None):
    c = need(user, "listmonk")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""):
        raise AppError("That does not look like an email address.")
    ids = _list_ids(user, lists)
    http("listmonk", "POST", "/api/subscribers", headers=_listmonk(c),
         json={"email": email, "name": name or email.split("@")[0], "status": "enabled", "lists": ids, "preconfirm_subscriptions": True})
    return {"added": email, "lists": ids}


def email_campaign(user, subject, body, lists=None):
    """A draft campaign; sending is a separate step that needs approval."""
    c = need(user, "listmonk")
    ids = _list_ids(user, lists)
    j = http("listmonk", "POST", "/api/campaigns", headers=_listmonk(c),
             json={"name": subject[:120], "subject": subject[:200], "lists": ids, "type": "regular",
                   "content_type": "markdown", "body": body})
    d = j.get("data") or {}
    return {"draft": d.get("id"), "subject": subject, "lists": ids}


def email_send(user, campaign):
    c = need(user, "listmonk")
    http("listmonk", "PUT", f"/api/campaigns/{int(campaign)}/status", headers=_listmonk(c), json={"status": "running"})
    return {"sending": int(campaign)}


def visitors(user, site="", days=7):
    c = need(user, "umami")
    tok = {"Authorization": f"Bearer {_umami_token(c)}"}
    sites = http("umami", "GET", "/api/websites", headers=tok)
    sites = sites.get("data", sites) if isinstance(sites, dict) else sites
    if site:
        sites = [s for s in sites if site.lower() in (s.get("domain") or "").lower() or site.lower() in (s.get("name") or "").lower()]
    if not sites:
        raise AppError("No matching website in Umami. Add it there first.")
    end = int(time.time() * 1000)
    start = end - int(days) * 86400000
    out = []
    for s in sites[:6]:
        st = http("umami", "GET", f"/api/websites/{s['id']}/stats?startAt={start}&endAt={end}", headers=tok)
        val = lambda k: (st.get(k) or {}).get("value", st.get(k)) if isinstance(st.get(k), dict) else st.get(k)
        out.append({"site": s.get("domain") or s.get("name"), "visitors": val("visitors"), "visits": val("visits"),
                    "pageviews": val("pageviews"), "bounces": val("bounces")})
    return {"days": int(days), "sites": out}


def invoices(user, status="unpaid"):
    c = need(user, "invoiceninja")
    q = "?per_page=50&include=client" + ("&client_status=unpaid" if status == "unpaid" else "&client_status=overdue" if status == "overdue" else "")
    j = http("invoiceninja", "GET", "/api/v1/invoices" + q, headers=_ninja(c))
    return [{"number": i.get("number"), "client": ((i.get("client") or {}).get("data") or i.get("client") or {}).get("name", ""),
             "amount": i.get("amount"), "balance": i.get("balance"), "due": i.get("due_date"), "id": i.get("id")}
            for i in j.get("data", [])]


def invoice_create(user, client, items, note=""):
    """A draft invoice for a client (made if new). Emailing it is a separate step that needs approval."""
    c = need(user, "invoiceninja")
    found = http("invoiceninja", "GET", "/api/v1/clients?filter=" + httpx.QueryParams({"q": client})["q"], headers=_ninja(c)).get("data", [])
    match = next((x for x in found if (x.get("name") or "").lower() == client.lower()), found[0] if found else None)
    if not match:
        match = http("invoiceninja", "POST", "/api/v1/clients", headers=_ninja(c), json={"name": client}).get("data") or {}
    lines = []
    for it in items or []:
        try:
            cost = float(str(it.get("amount") or it.get("cost") or 0).replace("R", "").replace(" ", "").replace(",", ""))
        except ValueError:
            raise AppError(f"I could not read the amount for {it.get('description')}.")
        lines.append({"product_key": str(it.get("description") or "Item")[:60], "notes": str(it.get("description") or ""),
                      "cost": cost, "quantity": float(it.get("quantity") or 1)})
    if not lines:
        raise AppError("Say what the invoice is for and how much.")
    inv = http("invoiceninja", "POST", "/api/v1/invoices", headers=_ninja(c),
               json={"client_id": match.get("id"), "line_items": lines, "public_notes": note}).get("data") or {}
    return {"invoice": inv.get("number"), "id": inv.get("id"), "client": match.get("name", client), "amount": inv.get("amount")}


def invoice_email(user, invoice_id):
    c = need(user, "invoiceninja")
    http("invoiceninja", "POST", "/api/v1/invoices/bulk", headers=_ninja(c), json={"action": "email", "ids": [invoice_id]})
    return {"emailed": invoice_id}


def rankings(user, domain=""):
    key = {"Authorization": f"Bearer {secret('serpbear', 'api_key') or ''}"}
    doms = http("serpbear", "GET", "/api/domains", headers=key).get("domains", [])
    if domain:
        doms = [d for d in doms if domain.lower() in d.get("domain", "").lower()]
    if not doms:
        raise AppError("That website is not in SerpBear yet. Add it and its search words there.")
    out = []
    for d in doms[:4]:
        kws = http("serpbear", "GET", "/api/keywords?domain=" + d["domain"], headers=key).get("keywords", [])
        out.append({"domain": d["domain"], "keywords": [{"keyword": k.get("keyword"), "position": k.get("position") or None,
                                                         "device": k.get("device"), "country": k.get("country")} for k in kws[:30]]})
    return out


def inbox(user, status="open"):
    c = need(user, "chatwoot")
    j = http("chatwoot", "GET", f"/api/v1/accounts/{c['account']}/conversations?status={status}", headers=_chatwoot(c))
    rows = (j.get("data") or {}).get("payload", [])
    out = []
    for r in rows[:25]:
        last = (r.get("messages") or [{}])[-1]
        out.append({"id": r.get("id"), "from": ((r.get("meta") or {}).get("sender") or {}).get("name", ""),
                    "channel": r.get("channel") or (r.get("meta") or {}).get("channel", ""), "unread": r.get("unread_count"),
                    "last": (last.get("content") or "")[:200]})
    return out


def inbox_reply(user, conversation, message):
    c = need(user, "chatwoot")
    http("chatwoot", "POST", f"/api/v1/accounts/{c['account']}/conversations/{int(conversation)}/messages",
         headers=_chatwoot(c), json={"content": message, "message_type": "outgoing"})
    return {"replied": int(conversation)}


def workflows(user):
    c = need(user, "n8n")
    j = http("n8n", "GET", "/api/v1/workflows?limit=100", headers=_n8n(c))
    return [{"id": w.get("id"), "name": w.get("name"), "on": bool(w.get("active"))} for w in j.get("data", [])]


def workflow_switch(user, name, on):
    c = need(user, "n8n")
    ws = workflows(user)
    hit = next((w for w in ws if w["name"].lower() == str(name).lower()), None) or \
        next((w for w in ws if str(name).lower() in w["name"].lower()), None)
    if not hit:
        raise AppError(f"No n8n workflow called {name}.")
    http("n8n", "POST", f"/api/v1/workflows/{hit['id']}/{'activate' if on else 'deactivate'}", headers=_n8n(c))
    return {"workflow": hit["name"], "on": bool(on)}


CHAT = {"postiz", "listmonk", "umami", "invoiceninja", "serpbear", "chatwoot", "n8n"}
TOOL_APP = {"social_channels": "postiz", "social_post": "postiz", "email_lists": "listmonk", "email_add": "listmonk",
            "email_campaign": "listmonk", "email_send": "listmonk", "site_visitors": "umami", "invoices_list": "invoiceninja",
            "invoice_create": "invoiceninja", "invoice_email": "invoiceninja", "seo_rankings": "serpbear",
            "inbox_list": "chatwoot", "inbox_reply": "chatwoot", "automations": "n8n", "automation_switch": "n8n"}


def usable(user, key):
    if APPS[key].get("shared"):
        r = core.q("SELECT status FROM apps WHERE key=?", (key,), one=True)
        return bool(r and r["status"] in ("installed", "running"))
    return bool(connection(user, key))
