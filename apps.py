"""Business apps that run on this PC beside Trillion, in Docker: search rankings and two automation tools.

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
    "serpbear": {
        "disk": 1,
        "name": "SerpBear", "port": 3003, "group": "Marketing",
        "what": "Tracks where your websites rank on Google for the search words you choose, every day.",
        "repo": "towfiqi/serpbear", "sha": "a1328fb4142dde40e0011c646339c4d4dbabdaca", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml"}, "ram": "about 300 MB", "shared": True,
        "connect": [],
        "signup": "Sign in with the details below. Add your websites and search words. Every login can ask me about rankings.",
        "note": "SerpBear needs a free scraping service key (it suggests several) under Settings, Scraper, before it can check Google.",
    },
    "n8n": {
        "disk": 1.5,
        "name": "n8n", "port": 5678, "group": "Automation",
        "what": "Connect apps with 'when this happens, do that' workflows, for example a new sale adds the buyer to your list.",
        "get": "own", "ram": "about 400 MB",
        "connect": [("api_key", "API key", "In n8n: Settings, n8n API, Create an API key.")],
        "signup": "Open n8n and create the owner account. Invite each login under Settings, Users.",
        "note": "n8n is free to use for your own business under its 'Sustainable Use' licence.",
    },
    "activepieces": {
        "disk": 3,
        "name": "Activepieces", "port": 8085, "group": "Automation",
        "what": "Easier, picture-based automations between hundreds of apps, for when n8n feels too technical.",
        "repo": "activepieces/activepieces", "sha": "03e0f4deb84775cab53f36f426670294c31ce9e2", "get": "raw",
        "files": {"docker-compose.yml": "docker-compose.yml", ".env.example": ".env"}, "ram": "about 1.5 GB",
        "connect": [],
        "signup": "Open Activepieces and create the first account. Invite each login from inside the app.",
    },
    "searxng": {
        "disk": 1,
        "name": "Private search (SearXNG)", "port": 8899, "group": "Knowledge",
        "what": "A private web search engine running on this PC. Trillion searches through it with no API key, and nothing "
                "is sent to Google or tracked.",
        "get": "own", "ram": "about 300 MB", "connect": [], "shared": True,
        "signup": "Nothing to sign up. Once it says running, Trillion can search the web herself (just ask her to look something up).",
        "note": "Keyless and private. Results are gathered from search engines without tracking you.",
    },
    "wyze-bridge": {
        "disk": 2,
        "name": "Wyze Bridge", "port": 5000, "group": "Cameras",
        "what": "Brings your Wyze cameras onto the local network so Trillion can see them, without the Wyze cloud for "
                "viewing. Sign in once with your Wyze account in its web page; each camera then has a local link.",
        "get": "own", "ram": "about 300 MB", "connect": [], "shared": True,
        "signup": "Open the Wyze Bridge page, sign in with your Wyze account (add a Wyze API key if it asks - free from "
                  "the Wyze developer site). Your cameras appear. Then add each one on the Cameras page (Phone page) using "
                  "the 'Wyze (via bridge)' type and the camera's name.",
        "note": "Viewing is local once set up. Uses the open docker-wyze-bridge project.",
    },
    "anythingllm": {
        "disk": 3,
        "name": "AnythingLLM", "port": 3005, "group": "Knowledge",
        "what": "Your own private document library you can chat with. Drop in PDFs, Word files and notes, and it answers "
                "from them using your local brain.",
        "get": "own", "ram": "about 1 GB", "connect": [],
        "signup": "Open AnythingLLM, create the first account, make a workspace and upload your documents. It is wired to "
                  "your local Ollama brain, so asking it questions is free.",
        "note": "Uses your local Ollama model, so there are no tokens to pay and nothing leaves the PC.",
    },
}
GROUPS = ["Marketing", "Customers", "Accounting", "Automation", "Knowledge", "Cameras"]
# the one port each app is opened on: (compose service, port inside the container)
PUBLISH = {"serpbear": ("app", 3000), "n8n": ("n8n", 5678), "activepieces": ("app", 80),
           "searxng": ("searxng", 8080), "anythingllm": ("anythingllm", 3001), "wyze-bridge": ("wyze-bridge", 5000)}
MEM_CHOICES = (0, 6, 8, 10, 12, 16)
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


# ---------- the owner's safety settings ----------
def prefs():
    o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    st = core.settings(o) if o else {}
    return {"private": st.get("apps_private", True), "memory": int(st.get("docker_mem_gb", 12) or 0)}


def save_prefs(private=None, memory=None):
    o = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    st = core.settings(o)
    if private is not None:
        st["apps_private"] = bool(private)
    if memory is not None:
        if int(memory) not in MEM_CHOICES:
            raise AppError("Choose 6, 8, 10, 12 or 16 GB, or no limit.")
        st["docker_mem_gb"] = int(memory)
    core.save_settings(o["id"], st)
    return prefs()


def wslconfig_path():
    return Path(os.environ.get("TRILLION_WSLCONFIG") or (Path.home() / ".wslconfig"))


def write_wslconfig(gb):
    """Cap the memory Docker's Linux part may take. Other settings in the file are kept as they are."""
    p = wslconfig_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    out, in_wsl2, done, saw = [], False, False, False
    for ln in lines:
        h = re.match(r"^\s*\[(.+?)\]\s*$", ln)
        if h:
            if in_wsl2 and not done and gb:
                out.append(f"memory={gb}GB")
                done = True
            in_wsl2 = h.group(1).strip().lower() == "wsl2"
            saw = saw or in_wsl2
            out.append(ln)
            continue
        if in_wsl2 and re.match(r"^\s*memory\s*=", ln, re.I):
            if gb and not done:
                out.append(f"memory={gb}GB")
                done = True
            continue
        out.append(ln)
    if in_wsl2 and not done and gb:
        out.append(f"memory={gb}GB")
        done = True
    if not saw and gb:
        out += (["", "[wsl2]"] if out else ["[wsl2]"]) + [f"memory={gb}GB"]
    p.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    return str(p)


def current_cap():
    p = wslconfig_path()
    if not p.exists():
        return 0
    in_wsl2 = False
    for ln in p.read_text(encoding="utf-8").splitlines():
        h = re.match(r"^\s*\[(.+?)\]", ln)
        if h:
            in_wsl2 = h.group(1).strip().lower() == "wsl2"
        m = re.match(r"^\s*memory\s*=\s*(\d+)\s*GB", ln, re.I)
        if in_wsl2 and m:
            return int(m.group(1))
    return 0


def restart_docker():
    """Make a new memory cap take effect: Docker's Linux part restarts, and the apps come back by themselves."""
    wsl = shutil.which("wsl")
    if wsl:
        subprocess.run([wsl, "--shutdown"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    time.sleep(2)
    return start_docker_desktop()


def tailscale_exe():
    p = shutil.which("tailscale")
    if p:
        return p
    c = r"C:\Program Files\Tailscale\tailscale.exe"
    return c if os.path.exists(c) else None


def share_on_tailscale(port, on=True):
    """Make an app that only listens on this PC reachable from your own Tailscale devices, and nowhere else."""
    exe = tailscale_exe()
    if not exe:
        return False
    args = [exe, "serve", "--bg", f"--http={port}", f"http://127.0.0.1:{port}"] if on else [exe, "serve", f"--http={port}", "off"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def publish(key):
    port = APPS[key]["port"]
    inner = PUBLISH[key][1]
    return f'"127.0.0.1:{port}:{inner}"' if prefs()["private"] else f'"{port}:{inner}"'


def docker_drive_free():
    import storage
    return storage.free_on(os.environ.get("LOCALAPPDATA") or Path.home())


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
    if key == "serpbear":
        set_env(dest / ".env", {"USER": "admin", "PASSWORD": secret(key, "admin_password", lambda: rnd(10)),
                                "SECRET": secret(key, "secret", rnd), "APIKEY": secret(key, "api_key", rnd),
                                "PORT": str(a["port"]), "NEXT_PUBLIC_APP_URL": url})
        over = f"""services:
  app:
    ports: !reset [{publish(key)}]
"""
    elif key == "n8n":
        (dest / "docker-compose.yml").write_text(f"""services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    restart: unless-stopped
    ports:
      - {publish(key)}
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
    ports: !reset [{publish(key)}]
  worker:
    deploy:
      replicas: 1
  postgres:
    container_name: trillion-activepieces-postgres
  redis:
    container_name: trillion-activepieces-redis
"""
    elif key == "searxng":
        conf = dest / "searxng"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "settings.yml").write_text(f"""# written by Trillion
use_default_settings: true
server:
  secret_key: {yaml_str(secret(key, 'secret', rnd))}
  limiter: false
  image_proxy: true
search:
  formats:
    - html
    - json
""", encoding="utf-8")
        (dest / "docker-compose.yml").write_text(f"""services:
  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    ports:
      - {publish(key)}
    volumes:
      - ./searxng:/etc/searxng
    environment:
      - SEARXNG_BASE_URL={yaml_str(url + '/')}
""", encoding="utf-8")
    elif key == "wyze-bridge":
        (dest / "tokens").mkdir(parents=True, exist_ok=True)
        pfx = "127.0.0.1:" if prefs()["private"] else ""
        (dest / "docker-compose.yml").write_text(f"""services:
  wyze-bridge:
    image: mrlt8/wyze-bridge:latest
    restart: unless-stopped
    ports:
      - "{pfx}{a['port']}:5000"
      - "{pfx}8554:8554"
      - "{pfx}8888:8888"
    environment:
      - WB_AUTH=False
    volumes:
      - ./tokens:/tokens
""", encoding="utf-8")
    elif key == "anythingllm":
        (dest / "storage").mkdir(parents=True, exist_ok=True)
        (dest / "docker-compose.yml").write_text(f"""services:
  anythingllm:
    image: mintplexlabs/anythingllm:latest
    restart: unless-stopped
    ports:
      - {publish(key)}
    cap_add:
      - SYS_ADMIN
    environment:
      - STORAGE_DIR=/app/server/storage
      - LLM_PROVIDER=ollama
      - OLLAMA_BASE_PATH=http://host.docker.internal:11434
      - OLLAMA_MODEL_PREF=qwen2.5:14b
      - OLLAMA_MODEL_TOKEN_LIMIT=8192
      - EMBEDDING_ENGINE=native
      - VECTOR_DB=lancedb
      - WHISPER_PROVIDER=local
      - TTS_PROVIDER=native
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./storage:/app/server/storage
""", encoding="utf-8")
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
        free = docker_drive_free()
        need = APPS[key]["disk"] + 5
        if free is not None and free < need * 1024 ** 3:
            raise AppError(f"Not enough space on C: for {APPS[key]['name']}: it needs about {APPS[key]['disk']} GB plus room to work, "
                           f"and C: has {free / 1024 ** 3:.1f} GB free. Free up space on the Files page (Free up C:) first.")
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
        _set(key, detail="Downloading and starting (the first time can take 5 to 20 minutes)")
        code, out = compose(key, "up", "-d", timeout=3600, feed=feed)
        if code != 0:
            raise AppError("Docker could not start it. " + _tail(out))
        _set(key, detail="Waiting for it to answer")
        shared = share_on_tailscale(a["port"]) if prefs()["private"] else None
        if shared is False:
            _set(key, detail="Running on this PC. Tailscale could not share it with your phone; see the Apps page.")
        if wait_up(key):
            _set(key, status="installed", installed=time.time(),
                 detail=("Running on this PC only: Tailscale would not share it with your phone. Open the Tailscale app on the PC, "
                         "sign in, then press Remove and Install again.") if shared is False else "Running")
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
        share_on_tailscale(APPS[key]["port"], on=False)
        new = "none"
    elif what == "erase":                     # the app and everything it saved
        code, out = compose(key, "down", "-v", timeout=300)
        share_on_tailscale(APPS[key]["port"], on=False)
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


def apply_access(host):
    """After the owner changes who may open the apps: rewrite each installed app's ports and restart it."""
    done, failed = [], []
    for r in core.q("SELECT * FROM apps WHERE status IN ('installed','stopped','running')"):
        key = r["key"]
        try:
            configure(key, folder(key), r["host"] or host)
            code, out = compose(key, "up", "-d", timeout=900)
            if code != 0:
                raise AppError(_tail(out))
            share_on_tailscale(APPS[key]["port"], on=prefs()["private"])
            done.append(APPS[key]["name"])
        except AppError as e:
            failed.append(f"{APPS[key]['name']}: {e}")
    _CACHE["t"] = 0
    return {"done": done, "failed": failed}


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
    res = {"docker": st, "apps": out, "groups": GROUPS}
    if user["is_admin"]:
        free = docker_drive_free()
        res["safety"] = dict(prefs(), cap_now=current_cap(), choices=list(MEM_CHOICES),
                             c_free=free, tailscale=bool(tailscale_exe()))
    return res


def admin_details(key):
    """The first sign-in details Trillion made, shown to the owner only."""
    d = {}
    if key == "serpbear":
        d = {"user": "admin", "password": secret(key, "admin_password")}
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
def _n8n(c):
    return {"X-N8N-API-KEY": c["api_key"]}


TESTS = {
    "n8n": lambda v: http("n8n", "GET", "/api/v1/workflows?limit=1", headers=_n8n(v)) and None,
}


# ---------- what chat can do in each app ----------
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


CHAT = {"serpbear", "n8n"}
TOOL_APP = {"seo_rankings": "serpbear", "automations": "n8n", "automation_switch": "n8n"}


def usable(user, key):
    if APPS[key].get("shared"):
        r = core.q("SELECT status FROM apps WHERE key=?", (key,), one=True)
        return bool(r and r["status"] in ("installed", "running"))
    return bool(connection(user, key))


# ---------- apps the owner removed for good ----------
DROPPED_APPS = {"postiz": 4007, "listmonk": 9000, "umami": 3002, "invoiceninja": 8012, "chatwoot": 3010}


def retire_dropped_apps():
    """Business apps the owner asked to remove fully (15 Oct 2026). Erase their containers and volumes, stop sharing
    their ports on Tailscale, delete their folders, and wipe every login's keys and saved sign-ins for them. Runs
    once (a marker keeps it from coming back). If Docker is off, the dashboard records go now and the containers are
    cleared the next time Trillion starts with Docker running."""
    marker = core.DATA / "dropped_apps_v1.done"
    if marker.exists():
        return
    present = []
    for key in DROPPED_APPS:
        if (core.q("SELECT 1 FROM apps WHERE key=?", (key,), one=True)
                or (HOME / key).exists()
                or core.q("SELECT 1 FROM secrets WHERE name LIKE ? OR name=? LIMIT 1",
                          (f"app:{key}:%", f"appconn:{key}"), one=True)):
            present.append(key)
    if not present:
        marker.write_text("done", encoding="utf-8")
        return
    docker_ok = False
    try:
        docker_ok = bool(docker_state().get("running"))
    except Exception:
        docker_ok = False
    for key in present:
        if docker_ok:
            try:
                run(["compose", "-p", PROJECT.format(key), "down", "-v", "--remove-orphans"], timeout=300)
            except Exception:
                pass
            try:
                share_on_tailscale(DROPPED_APPS[key], on=False)
            except Exception:
                pass
            shutil.rmtree(HOME / key, ignore_errors=True)
        try:
            core.x("DELETE FROM apps WHERE key=?", (key,))
            core.x("DELETE FROM secrets WHERE name LIKE ? OR name=?", (f"app:{key}:%", f"appconn:{key}"))
        except Exception:
            pass
    owner = core.q("SELECT id FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    if owner:
        core.log(owner["id"], "Removed business apps for good: " + ", ".join(sorted(present))
                 + ("" if docker_ok else " (Docker was off; their containers and data clear next time Docker is running)"),
                 "done")
    if docker_ok:
        marker.write_text("done", encoding="utf-8")   # otherwise retry next start to clear the containers
