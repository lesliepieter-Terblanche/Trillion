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
    # 0.4.1: the Windows control connector needs the word "serve" on its command
    core.x("UPDATE connectors SET command='uvx windows-mcp serve' WHERE command='uvx windows-mcp'")
    starter_pack()
    add_rea_once()
    try:
        install_packs()
    except Exception:
        pass
    try:
        all_skills_on_once()
    except Exception:
        pass


def add_rea_once():
    """The owner asked for REA (12 Oct 2026): add it for him once. A marker keeps it from coming back if he removes it."""
    marker = core.DATA / "rea_connector_v1.done"
    if marker.exists():
        return
    owner = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    if not owner:
        return
    cat = next(c for c in CATALOG if c["key"] == "rea")
    if cat["name"] not in {r["name"] for r in core.q("SELECT name FROM connectors")}:
        add_connector(cat["name"], cat["description"], cat["command"], owner["id"])
        core.log(owner["id"], "Connector added: Reverse engineering (REA)", "done")
    marker.write_text("added", encoding="utf-8")


def starter_pack():
    """One time only: give the owner the three starter connectors he asked for. A marker file keeps
    this from re-adding anything he later removes."""
    marker = core.DATA / "starter_connectors_v1.done"
    if marker.exists():
        return
    owner = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    if not owner:
        return
    have = {r["name"] for r in core.q("SELECT name FROM connectors")}
    for key in ("browser", "fetch", "thinking"):
        cat = next(c for c in CATALOG if c["key"] == key)
        if cat["name"] not in have:
            add_connector(cat["name"], cat["description"], cat["command"], owner["id"])
    marker.write_text("added", encoding="utf-8")
    core.log(owner["id"], "Starter connectors added: Browser control, Fetch, Sequential thinking", "done")


# =====================================================================================
# Skills
# =====================================================================================
PACKS_DIR = core.BASE / "skill_packs"
# what each pack held when it was first shipped with a single per-pack marker (up to 0.7.9)
PACK_BASELINE = {
    "replica": ["replica-architect", "replica-backend", "replica-brand", "replica-build", "replica-deploy", "replica-design",
                "replica-diff", "replica-entrepreneur", "replica-launch", "replica-recon", "replica-test"],
    "anthropic-web-artifacts": ["web-artifacts-builder"], "superpowers": ["subagent-driven-development"],
    "superpowers-problem-solving": ["scale-game"], "ai-skills-imagen": ["imagen"],
    "mattpocock-skills": ["grilling", "loop-me"], "marketingskills": ["seo-audit"],
}


def install_packs():
    """Skill packs that ship with Trillion (each a folder of skill folders). Installed once per pack and switched on for
    the owner; a marker file keeps a skill he later removes from coming back. Other logins switch them on themselves."""
    import shutil
    if not PACKS_DIR.exists():
        return
    owner = core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True)
    for pack in sorted(p for p in PACKS_DIR.iterdir() if p.is_dir()):
        if not owner:
            continue
        # which of this pack's skills have been installed before (so one he removed never comes back, and one added
        # to the pack later still arrives)
        marker = core.DATA / f"skill_pack_{pack.name}_v2.json"
        try:
            done = set(json.loads(marker.read_text(encoding="utf-8")))
        except Exception:
            done = set(PACK_BASELINE.get(pack.name, [])) if (core.DATA / f"skill_pack_{pack.name}_v1.done").exists() else set()
        src = (pack / "SOURCE.txt").read_text(encoding="utf-8").strip().splitlines() if (pack / "SOURCE.txt").exists() else []
        link = next((ln.split("Source:", 1)[1].strip() for ln in src if ln.startswith("Source:")), f"bundled pack: {pack.name}")
        names = []
        off = set()
        for ln in src:
            if ln.lower().startswith("off:"):
                off |= {x.strip() for x in ln.split(":", 1)[1].split(",") if x.strip()}
        shared = None
        for extra in sorted(f for f in pack.iterdir() if f.is_dir() and not (f / "SKILL.md").exists()):
            shared = SKILLS_DIR / f"{pack.name}-{extra.name}"        # files the pack's skills have in common
            shutil.copytree(extra, shared, dirs_exist_ok=True)
        for folder in sorted(f for f in pack.iterdir() if (f / "SKILL.md").exists() and f.name not in done):
            done.add(folder.name)
            name, desc, body = parse_skill((folder / "SKILL.md").read_text(encoding="utf-8"))
            slug = slugify(name)
            dest = SKILLS_DIR / slug
            dest.mkdir(parents=True, exist_ok=True)
            for f in folder.iterdir():                     # the templates and helper files travel with the skill
                if f.is_file() and f.name != "SKILL.md":
                    shutil.copy2(f, dest / f.name)
                elif f.is_dir():
                    shutil.copytree(f, dest / f.name, dirs_exist_ok=True)
            extras = sorted(str(f.relative_to(folder)).replace("\\", "/") for f in folder.rglob("*")
                            if f.is_file() and f.name not in ("SKILL.md", "LICENSE.txt", "LICENSE"))
            if extras:
                body += (f"\n\n---\nOn this PC, the files that belong to this skill ({', '.join(extras)}) are in the folder: {dest}\n"
                         "Read templates from there with read_file. Its scripts and tools can only be run with run_command, which needs the owner's approval.")
            if shared:
                body += f"\nFiles this skill shares with the others in its pack (it may call them 'shared/...') are in: {shared}"
            # a pack can ask for some or all of its skills to be installed switched off (playbooks she cannot really carry out)
            stay_off = folder.name in off or any(ln.strip().lower() == "enabled: no" for ln in src)
            save_skill(name, desc, body, link, owner_id=None if stay_off else owner["id"])
            names.append(name)
        marker.write_text(json.dumps(sorted(done)), encoding="utf-8")
        if names:
            core.log(owner["id"], f"Skill pack added ({pack.name}): " + ", ".join(names), "done")

def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:60] or "skill"


def parse_skill(text):
    """Split optional '--- name/description ---' frontmatter from the body."""
    meta, body = {}, text
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        key = None
        for line in m.group(1).splitlines():
            if key and line[:1] in (" ", "\t") and line.strip():      # a description written over several lines
                meta[key] = (meta[key] + " " + line.strip()).strip()
                continue
            k, sep, v = line.partition(":")
            key = None
            if sep and k.strip() in ("name", "description"):
                v = v.strip().strip("\"'")
                key = k.strip()
                meta[key] = "" if v in (">", ">-", "|", "|-", ">+", "|+") else v
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


def all_skills_on_once():
    """0.7.16: Pieter asked for every skill to be on for every login. Done once; after that a skill someone switches off stays off."""
    marker = core.DATA / "skills_all_on_v1.done"
    if marker.exists():
        return
    core.x("UPDATE user_skills SET enabled=1 WHERE slug IN (SELECT slug FROM skills WHERE status='active')")
    marker.write_text("done", encoding="utf-8")


def list_skills(uid):
    # every skill is on for every login unless that login switched it off: fill in the ones this login has no choice for yet
    core.x("INSERT OR IGNORE INTO user_skills(user_id,slug,enabled) SELECT ?, slug, 1 FROM skills WHERE status='active'", (uid,))
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
    {"key": "fetch", "name": "Fetch",
     "description": "Reads any web page as plain text. Lighter and faster than opening a browser. (Official MCP server)",
     "command": "uvx mcp-server-fetch", "needs": "uv"},
    {"key": "thinking", "name": "Sequential thinking",
     "description": "A scratchpad for planning multi-step jobs one step at a time. (Official MCP server)",
     "command": "npx -y @modelcontextprotocol/server-sequential-thinking", "needs": "Node.js"},
    {"key": "rea", "name": "Reverse engineering (REA)",
     "description": "Looks inside apps and websites without their source code: JavaScript and Electron apps, .NET programs, "
                    "web pages and, with Ghidra installed, native programs. Explains how a feature works and shows the evidence. "
                    "Owner only. Every REA job waits for your OK first, even on a phone call; heavy native-binary jobs are handed "
                    "to Claude Code on your Pro plan, which does the work and reports back. (REA, github.com/morluto/rea, MIT licence)",
     "command": "npx -y rea-agents@6.4.0 mcp", "needs": "Node.js 22.19 or newer"},
    {"key": "windows", "name": "Windows control",
     "description": "Operates Windows itself: open apps, click, type, read what is on screen. (Windows-MCP)",
     "command": "uvx windows-mcp serve", "needs": "uv"},
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
                                     cwd=str(core.DATA),
                                     env=dict(os.environ, ANONYMIZED_TELEMETRY="false"))  # no usage reporting
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


def _type_name(schema):
    t = schema.get("type") if isinstance(schema, dict) else None
    if isinstance(t, list):
        t = " or ".join(str(x) for x in t if x != "null")
    return str(t or "any")


def brief_tools(tools):
    out = []
    for t in tools:
        props = (t.get("inputSchema") or {}).get("properties") or {}
        req = set((t.get("inputSchema") or {}).get("required") or [])
        out.append({"tool": t.get("name"), "what": (t.get("description") or "")[:160],
                    "arguments": {k: (_type_name(v) + ("" if k in req else ", optional"))
                                  for k, v in list(props.items())[:10]}})
    return out
