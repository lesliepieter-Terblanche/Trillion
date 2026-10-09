"""Trillion extensions: instruction skills (SKILL.md playbooks) and connectors (MCP servers)."""
import json, os, queue, re, shlex, shutil, subprocess, sys, threading, time
from pathlib import Path

import httpx

import core

SKILLS_DIR = core.DATA / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
MAX_SKILL_BYTES = 120_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills(slug TEXT PRIMARY KEY, name TEXT, description TEXT, source TEXT,
  status TEXT DEFAULT 'active', created REAL);
CREATE TABLE IF NOT EXISTS user_skills(user_id INTEGER, slug TEXT, enabled INTEGER DEFAULT 1, PRIMARY KEY(user_id, slug));
CREATE TABLE IF NOT EXISTS connectors(id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT, command TEXT, created REAL);
CREATE TABLE IF NOT EXISTS connector_access(connector_id INTEGER, user_id INTEGER, enabled INTEGER DEFAULT 1,
  PRIMARY KEY(connector_id, user_id));
"""


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


# =====================================================================================
# Skills
# =====================================================================================
def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:60] or "skill"


def parse_skill(text):
    """Split optional '--- name/description ---' frontmatter from the body."""
    meta, body = {}, text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip() in ("name", "description"):
                meta[k.strip()] = v.strip().strip("\"'")
    if not meta.get("name"):
        h = re.search(r"^#\s+(.+)$", body, re.M)
        meta["name"] = h.group(1).strip() if h else "Untitled skill"
    if not meta.get("description"):
        para = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")), "")
        meta["description"] = para[:300]
    return meta["name"][:80], meta["description"][:400], body.strip()


def github_raw_candidates(url):
    """Turn a pasted GitHub link into the raw SKILL.md addresses worth trying."""
    url = url.strip()
    if url.startswith("https://raw.githubusercontent.com/"):
        return [url]
    m = re.match(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(tree|blob)/([^/]+)(?:/(.*))?)?/?$", url)
    if not m:
        raise ValueError("That is not a GitHub link. Paste the address of a skill folder or SKILL.md on github.com.")
    owner, repo, kind, ref, path = m.groups()
    path = (path or "").strip("/")
    refs = [ref] if ref else ["main", "master"]
    out = []
    for r in refs:
        base = f"https://raw.githubusercontent.com/{owner}/{repo}/{r}/"
        if path.lower().endswith(".md"):
            out.append(base + path)
        else:
            for fname in ("SKILL.md", "skill.md"):
                out.append(base + (path + "/" if path else "") + fname)
    return out


def fetch_skill(url):
    last = "nothing found"
    for raw in github_raw_candidates(url):
        try:
            r = httpx.get(raw, timeout=20, follow_redirects=True)
        except Exception as e:
            last = type(e).__name__
            continue
        if r.status_code == 200 and r.text.strip():
            if len(r.content) > MAX_SKILL_BYTES:
                raise ValueError("That file is too large to be a skill.")
            name, desc, body = parse_skill(r.text)
            return {"name": name, "description": desc, "body": body, "raw_url": raw, "source": url,
                    "has_scripts": bool(re.search(r"scripts/|\.py\b|```(bash|sh|python)", body))}
        last = f"code {r.status_code}"
    raise ValueError(f"No SKILL.md at that link ({last}). Open the skill's own folder on GitHub and paste that address. "
                     "Private repositories cannot be read.")


def save_skill(name, description, body, source, status="active", owner_id=None):
    slug = slugify(name)
    d = SKILLS_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    core.x("INSERT INTO skills(slug,name,description,source,status,created) VALUES(?,?,?,?,?,?) "
           "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, description=excluded.description, "
           "source=excluded.source, status=excluded.status",
           (slug, name, description, source, status, time.time()))
    if owner_id and status == "active":
        set_skill_enabled(owner_id, slug, True)
    return slug


def set_skill_enabled(uid, slug, enabled):
    core.x("INSERT INTO user_skills(user_id,slug,enabled) VALUES(?,?,?) "
           "ON CONFLICT(user_id,slug) DO UPDATE SET enabled=excluded.enabled", (uid, slug, 1 if enabled else 0))


def list_skills(uid):
    rows = core.q("SELECT s.*, COALESCE(u.enabled,0) AS on_ FROM skills s LEFT JOIN user_skills u "
                  "ON u.slug=s.slug AND u.user_id=? ORDER BY s.status DESC, s.name", (uid,))
    return [{"slug": r["slug"], "name": r["name"], "description": r["description"], "source": r["source"],
             "status": r["status"], "enabled": bool(r["on_"])} for r in rows]


def enabled_skills(uid):
    return [s for s in list_skills(uid) if s["enabled"] and s["status"] == "active"]


def skill_body(slug):
    p = SKILLS_DIR / slugify(slug) / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def find_skill(uid, name):
    want = slugify(name)
    for s in enabled_skills(uid):
        if s["slug"] == want or slugify(s["name"]) == want:
            return s
    return None


def remove_skill(slug):
    slug = slugify(slug)
    core.x("DELETE FROM skills WHERE slug=?", (slug,))
    core.x("DELETE FROM user_skills WHERE slug=?", (slug,))
    shutil.rmtree(SKILLS_DIR / slug, ignore_errors=True)


# =====================================================================================
# Connectors (MCP servers over stdio)
# =====================================================================================
CATALOG = [
    {"key": "browser", "name": "Browser control",
     "description": "Opens and drives a real browser: visit sites, click, fill in forms, read pages. (Playwright MCP, by Microsoft)",
     "command": "npx -y @playwright/mcp@latest", "needs": "Node.js"},
    {"key": "windows", "name": "Windows control",
     "description": "Operates Windows itself: open apps, click, type, read what is on screen. (Windows-MCP)",
     "command": "uvx windows-mcp", "needs": "uv"},
]


def resolve_command(parts):
    exe = parts[0]
    here = Path(sys.executable).parent
    for cand in (here / exe, here / (exe + ".exe")):
        if cand.exists():
            return [str(cand)] + parts[1:]
    found = shutil.which(exe)
    if not found:
        hint = {"npx": "Node.js is missing. Run '1 - Setup.bat' again; it installs only what is missing.",
                "node": "Node.js is missing. Run '1 - Setup.bat' again.",
                "uvx": "The 'uv' helper is missing. Tell Trillion to update herself, then restart her."}.get(exe, "")
        raise RuntimeError(f"Cannot find the program '{exe}'. {hint}".strip())
    return [found] + parts[1:]


class MCP:
    """A minimal Model Context Protocol client: one helper program, talked to over stdin/stdout."""

    def __init__(self, name, command):
        self.name, self.command = name, command
        self.proc, self.tools_cache, self.error = None, None, ""
        self._id, self._lock, self._pending, self._stderr = 0, threading.Lock(), {}, []

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.alive():
            return
        parts = shlex.split(self.command, posix=(os.name != "nt"))
        parts = [p.strip('"') for p in parts]
        argv = resolve_command(parts)
        self.tools_cache, self._stderr = None, []
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     cwd=str(core.DATA))
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "trillion", "version": "0.2"}}, timeout=240)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception as e:
            tail = " | ".join(self._stderr[-3:])
            self.stop()
            raise RuntimeError(f"{self.name} did not start: {e}. {tail}"[:400])

    def _read(self):
        proc = self.proc
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            q = self._pending.get(msg.get("id"))
            if q is not None and ("result" in msg or "error" in msg):
                q.put(msg)

    def _drain(self):
        for line in self.proc.stderr:
            self._stderr.append(line.strip()[:200])
            del self._stderr[:-20]

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=90):
        with self._lock:
            self._id += 1
            rid = self._id
        q = queue.Queue()
        self._pending[rid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            deadline = time.time() + timeout
            while True:
                try:
                    msg = q.get(timeout=1)
                    break
                except queue.Empty:
                    if not self.alive():
                        raise RuntimeError("the helper program stopped")
                    if time.time() > deadline:
                        raise RuntimeError("no answer in time")
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            raise RuntimeError(str(msg["error"].get("message", msg["error"]))[:300])
        return msg["result"]

    def tools(self):
        self.start()
        if self.tools_cache is None:
            self.tools_cache = self.request("tools/list").get("tools", [])
        return self.tools_cache

    def call(self, tool, arguments):
        self.start()
        res = self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=180)
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        other = [c.get("type") for c in res.get("content", []) if c.get("type") != "text"]
        out = {"result": text[:6000]}
        if other:
            out["also_returned"] = other
        if res.get("isError"):
            out = {"error": text[:1500] or "the connector reported an error"}
        return out

    def stop(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


LIVE = {}


def client(row):
    c = LIVE.get(row["id"])
    if c is None or c.command != row["command"]:
        if c:
            c.stop()
        c = LIVE[row["id"]] = MCP(row["name"], row["command"])
    return c


def add_connector(name, description, command, owner_id):
    name = re.sub(r"\s+", " ", name).strip()[:60]
    if not name or not command.strip():
        raise ValueError("A connector needs a name and a command.")
    cid = core.x("INSERT INTO connectors(name,description,command,created) VALUES(?,?,?,?)",
                 (name, description[:400], command.strip(), time.time()))
    core.x("INSERT OR REPLACE INTO connector_access(connector_id,user_id,enabled) VALUES(?,?,1)", (cid, owner_id))
    return cid


def remove_connector(cid):
    c = LIVE.pop(cid, None)
    if c:
        c.stop()
    core.x("DELETE FROM connectors WHERE id=?", (cid,))
    core.x("DELETE FROM connector_access WHERE connector_id=?", (cid,))


def list_connectors(user):
    out = []
    for r in core.q("SELECT * FROM connectors ORDER BY name"):
        acc = core.q("SELECT * FROM connector_access WHERE connector_id=?", (r["id"],))
        mine = next((a for a in acc if a["user_id"] == user["id"]), None)
        if not mine and not user["is_admin"]:
            continue
        live = LIVE.get(r["id"])
        item = {"id": r["id"], "name": r["name"], "description": r["description"],
                "allowed": bool(mine), "enabled": bool(mine and mine["enabled"]),
                "running": bool(live and live.alive())}
        if user["is_admin"]:
            item["command"] = r["command"]
            item["access"] = [a["user_id"] for a in acc]
        out.append(item)
    return out


def enabled_connectors(uid):
    return core.q("SELECT c.* FROM connectors c JOIN connector_access a ON a.connector_id=c.id "
                  "WHERE a.user_id=? AND a.enabled=1 ORDER BY c.name", (uid,))


def find_connector(uid, name):
    want = slugify(name)
    for r in enabled_connectors(uid):
        if slugify(r["name"]) == want or want in slugify(r["name"]):
            return r
    return None


def brief_tools(tools):
    out = []
    for t in tools:
        props = (t.get("inputSchema") or {}).get("properties") or {}
        req = set((t.get("inputSchema") or {}).get("required") or [])
        out.append({"tool": t.get("name"), "what": (t.get("description") or "")[:160],
                    "arguments": {k: ((v.get("type") or "any") + ("" if k in req else ", optional"))
                                  for k, v in list(props.items())[:10]}})
    return out
