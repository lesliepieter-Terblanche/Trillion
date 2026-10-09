"""Trillion tools: what the assistant can actually do on the PC."""
import os, json, math, shutil, subprocess, time, fnmatch
from pathlib import Path

import httpx

import core


def fn(name, desc, props, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required if required is not None else list(props)}}}


S = {"type": "string"}
SPECS = [
    fn("list_folder", "List the files and folders inside a folder.", {"path": S}),
    fn("find_files", "Search a folder (and its subfolders) for files matching a pattern like *.pdf or *invoice*.",
       {"folder": S, "pattern": S}),
    fn("read_file", "Read a text file.", {"path": S}),
    fn("write_file", "Create or overwrite a text file (notes, .txt, .md, .csv, code).", {"path": S, "content": S}),
    fn("make_folder", "Create a folder.", {"path": S}),
    fn("copy_path", "Copy a file or folder.", {"source": S, "destination": S}),
    fn("move_path", "Move or rename a file or folder.", {"source": S, "destination": S}),
    fn("delete_path", "Delete a file or folder. Always needs the user's approval on the dashboard.", {"path": S}),
    fn("open_path", "Open a file, folder or program on the PC screen.", {"path": S}),
    fn("create_word", "Create a Word document (.docx).",
       {"path": S, "title": S, "paragraphs": {"type": "array", "items": S}}),
    fn("create_excel", "Create an Excel workbook (.xlsx). rows is a list of rows; the first row is the header.",
       {"path": S, "sheet_name": S, "rows": {"type": "array", "items": {"type": "array", "items": S}}}),
    fn("create_powerpoint", "Create a PowerPoint deck (.pptx).",
       {"path": S, "title": S, "slides": {"type": "array", "items": {"type": "object", "properties": {
           "title": S, "bullets": {"type": "array", "items": S}}}}}),
    fn("save_note", "Save something worth remembering to the second brain. domain is PERSONAL or WORK.",
       {"title": S, "body": S, "domain": S}, ["title", "body"]),
    fn("search_memory", "Search the second brain for things saved earlier.", {"query": S}),
    fn("check_website", "Check whether a website is up and how fast it answers.", {"url": S}),
    fn("system_status", "Get the PC's current processor, memory, disk and graphics card readings.", {}),
    fn("use_skill", "Load the full instructions of one of the user's installed skills. Call this before doing "
       "a job that one of the listed skills covers, then follow what it says.", {"name": S}),
    fn("draft_skill", "Write down a reusable playbook for a job the user does repeatedly. It is saved as a draft "
       "and only becomes usable after the owner approves it on the Skills page.",
       {"name": S, "description": S, "instructions": S}),
    fn("connector_tools", "List what one of the user's connectors can do (its tools and their arguments). "
       "Call this before connector_call.", {"connector": S}),
    fn("connector_call", "Use one tool of a connector. Get the exact tool name and arguments from connector_tools first.",
       {"connector": S, "tool": S, "arguments": {"type": "object"}}, ["connector", "tool"]),
    fn("phone_status", "Get the user's own mobile phone status: battery, charging, storage, Wi-Fi, model, screen on or off.", {}),
    fn("phone_open_app", "Open an app on the user's own phone, for example WhatsApp, Chrome or Maps. The phone must be unlocked.",
       {"app": S}),
    fn("phone_find_contact", "Look up a contact's phone numbers on the user's phone by name.", {"name": S}),
    fn("phone_send_whatsapp", "Send a WhatsApp message from the user's own phone. contact is a contact name or a phone "
       "number. Always needs the user's approval on the dashboard before it is sent.", {"contact": S, "message": S}),
    fn("phone_locate", "Find where the user's own phone is: its last known position with a map link.", {}),
    fn("phone_ring", "Make the user's own phone play a loud sound so it can be found nearby.", {}),
    fn("phone_screenshot", "Take a screenshot of the user's phone screen and save it to their folder.", {}),
    fn("update_self", "Update Trillion herself to the newest version from GitHub, then restart. Owner only. "
       "Use when the user says update yourself, get the latest version, or pull the update.", {}),
    fn("run_command", "Run a Windows command. Owner only, and always needs approval on the dashboard.",
       {"command": S}),
]
NEEDS_APPROVAL = {"delete_path", "run_command", "phone_send_whatsapp"}


def resolve(user, path):
    raw = str(path or "").strip().strip('"')
    home = core.user_home(user)
    if raw in ("", ".", "~"):
        p = home
    elif raw.startswith("~"):
        p = home / raw[1:].lstrip("/\\")
    else:
        p = Path(os.path.expandvars(raw))
        if not p.is_absolute():
            p = home / p
    p = p.resolve()
    roots = core.user_roots(user)
    if "*" in roots:
        return p
    for r in roots:
        R = Path(r).resolve()
        if p == R or R in p.parents:
            return p
    raise PermissionError(f"{user['name']} is not allowed to use {p}")


def describe(name, a):
    g = lambda k: str(a.get(k, ""))[:70]
    return {
        "list_folder": f"Looking in {g('path')}", "find_files": f"Searching for {g('pattern')}",
        "read_file": f"Reading {g('path')}", "write_file": f"Writing {g('path')}",
        "make_folder": f"Creating folder {g('path')}", "copy_path": f"Copying {g('source')}",
        "move_path": f"Moving {g('source')}", "delete_path": f"Delete {g('path')}",
        "open_path": f"Opening {g('path')}", "create_word": f"Creating Word document {g('path')}",
        "create_excel": f"Creating Excel workbook {g('path')}",
        "create_powerpoint": f"Creating PowerPoint {g('path')}", "save_note": f"Remembering: {g('title')}",
        "search_memory": f"Searching memory for {g('query')}", "check_website": f"Checking {g('url')}",
        "use_skill": f"Reading the skill {g('name')}", "draft_skill": f"Drafting a playbook: {g('name')}",
        "connector_tools": f"Checking what {g('connector')} can do",
        "connector_call": f"Using {g('connector')}: {g('tool')}",
        "phone_status": "Checking the phone", "phone_open_app": f"Opening {g('app')} on the phone",
        "phone_find_contact": f"Looking up {g('name')} in contacts",
        "phone_send_whatsapp": f"Send WhatsApp to {a.get('_name') or g('contact')}"
                               + (f" (+{a['contact']})" if a.get('_name') else "") + f": \"{str(a.get('message', ''))[:120]}\"",
        "phone_locate": "Locating the phone", "phone_ring": "Ringing the phone",
        "phone_screenshot": "Taking a phone screenshot",
        "system_status": "Checking the PC", "update_self": "Updating myself from GitHub", "run_command": f"Run command: {g('command')}",
    }.get(name, name)


def execute(user, name, args, task_id=None, approved=False):
    """Run one tool. Returns a JSON-safe dict. Never raises."""
    uid = user["id"]
    try:
        if name not in {s["function"]["name"] for s in SPECS}:
            return {"error": f"Unknown tool {name}"}
        if name == "run_command" and not user["is_admin"]:
            return {"error": "Only the owner login can run commands."}
        if name == "update_self" and not user["is_admin"]:
            return {"error": "Only the owner login can update Trillion."}
        if name in NEEDS_APPROVAL and not approved:
            if name == "delete_path":
                resolve(user, args.get("path"))  # permission check before asking
            if name == "phone_send_whatsapp":  # settle exactly who it goes to before asking
                import phone
                who, number = phone.resolve_contact(user, str(args.get("contact", "")))
                args = {"contact": number, "message": str(args.get("message", "")), "_name": who}
                if not args["message"].strip():
                    return {"error": "There is no message to send."}
            summary = describe(name, args)
            core.x("INSERT INTO approvals(user_id,task_id,tool,args,summary,created) VALUES(?,?,?,?,?,?)",
                   (uid, task_id, name, json.dumps(args), summary, time.time()))
            core.log(uid, f"Waiting on you: {summary}", "waiting")
            return {"status": "waiting_for_approval",
                    "note": "Not done yet. The user must approve this on the dashboard."}
        out = globals()["t_" + name](user, **args)
        core.log(uid, describe(name, args), "done")
        return out
    except PermissionError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"Wrong details for {name}: {e}"}
    except Exception as e:
        if type(e).__name__ == "PhoneError":
            return {"error": str(e)}
        return {"error": f"{type(e).__name__}: {e}"}


# ---------- files ----------
def t_list_folder(user, path="."):
    p = resolve(user, path)
    items = []
    for e in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))[:300]:
        try:
            items.append({"name": e.name, "type": "folder" if e.is_dir() else "file",
                          "size": e.stat().st_size if e.is_file() else None})
        except OSError:
            pass
    return {"folder": str(p), "items": items}


def t_find_files(user, folder=".", pattern="*"):
    p = resolve(user, folder)
    hits = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d not in ("node_modules", "AppData")]
        for f in files:
            if fnmatch.fnmatch(f.lower(), pattern.lower()):
                hits.append(str(Path(root) / f))
                if len(hits) >= 100:
                    return {"matches": hits, "note": "Stopped at 100 matches."}
    return {"matches": hits}


def t_read_file(user, path):
    p = resolve(user, path)
    if p.stat().st_size > 2_000_000:
        return {"error": "File is too large to read as text."}
    return {"path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")[:20000]}


def t_write_file(user, path, content):
    p = resolve(user, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"saved": str(p)}


def t_make_folder(user, path):
    p = resolve(user, path)
    p.mkdir(parents=True, exist_ok=True)
    return {"created": str(p)}


def t_copy_path(user, source, destination):
    s, d = resolve(user, source), resolve(user, destination)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return {"error": f"{d} already exists. Choose another name."}
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s, d) if s.is_dir() else shutil.copy2(s, d)
    return {"copied_to": str(d)}


def t_move_path(user, source, destination):
    s, d = resolve(user, source), resolve(user, destination)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return {"error": f"{d} already exists. Choose another name."}
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"moved_to": str(d)}


def t_delete_path(user, path):
    p = resolve(user, path)
    if p == core.user_home(user) or len(p.parts) <= 2:
        return {"error": "Refusing to delete a top-level folder."}
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    return {"deleted": str(p)}


def t_open_path(user, path):
    p = resolve(user, path)
    if hasattr(os, "startfile"):
        os.startfile(str(p))  # Windows only
        return {"opened": str(p)}
    return {"error": "Opening things on screen only works on the Windows PC."}


# ---------- documents ----------
def _with_ext(user, path, ext):
    p = resolve(user, path)
    if p.suffix.lower() != ext:
        p = p.with_suffix(ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def t_create_word(user, path, title="", paragraphs=None):
    from docx import Document
    p = _with_ext(user, path, ".docx")
    d = Document()
    if title:
        d.add_heading(title, 0)
    for para in paragraphs or []:
        d.add_paragraph(str(para))
    d.save(p)
    return {"saved": str(p)}


def t_create_excel(user, path, sheet_name="Sheet1", rows=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    p = _with_ext(user, path, ".xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Sheet1")[:31]
    for r in rows or []:
        out = []
        for v in r:
            try:
                out.append(float(v) if str(v).replace(".", "", 1).replace("-", "", 1).isdigit() else v)
            except Exception:
                out.append(v)
        ws.append(out)
    for c in ws[1] if ws.max_row else []:
        c.font = Font(bold=True)
    wb.save(p)
    return {"saved": str(p)}


def t_create_powerpoint(user, path, title="", slides=None):
    from pptx import Presentation
    p = _with_ext(user, path, ".pptx")
    prs = Presentation()
    if title:
        s = prs.slides.add_slide(prs.slide_layouts[0])
        s.shapes.title.text = title
    for sl in slides or []:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = str(sl.get("title", ""))
        tf = s.placeholders[1].text_frame
        bullets = [str(b) for b in sl.get("bullets", [])]
        if bullets:
            tf.text = bullets[0]
            for b in bullets[1:]:
                tf.add_paragraph().text = b
    prs.save(p)
    return {"saved": str(p)}


# ---------- memory ----------
def add_note(uid, title, body, domain="PERSONAL"):
    domain = "WORK" if str(domain).upper().startswith("W") else "PERSONAL"
    emb = core.embed(f"{title}\n{body}")
    return core.x("INSERT INTO notes(user_id,title,body,domain,ts,emb) VALUES(?,?,?,?,?,?)",
                  (uid, title[:200], body[:8000], domain, time.time(), json.dumps(emb) if emb else None))


def search_notes(uid, query, k=5):
    rows = core.q("SELECT * FROM notes WHERE user_id=? ORDER BY ts DESC LIMIT 2000", (uid,))
    if not rows:
        return []
    qe = core.embed(query) if query else None
    scored = []
    words = [w for w in query.lower().split() if len(w) > 2]
    for r in rows:
        cos = 0.0
        if qe and r["emb"]:
            e = json.loads(r["emb"])
            dot = sum(a * b for a, b in zip(qe, e))
            na, nb = math.sqrt(sum(a * a for a in qe)), math.sqrt(sum(b * b for b in e))
            cos = dot / (na * nb) if na and nb else 0.0
        text = (r["title"] + " " + r["body"]).lower()
        hits = sum(1 for w in words if w in text)
        if hits or cos > 0.55 or not query:
            scored.append((cos + 0.15 * hits, r))
    scored.sort(key=lambda s: -s[0])
    return [{"id": r["id"], "title": r["title"], "body": r["body"], "domain": r["domain"], "ts": r["ts"]}
            for s, r in scored[:k]]


def t_save_note(user, title, body, domain="PERSONAL"):
    return {"saved_note": add_note(user["id"], title, body, domain)}


def t_search_memory(user, query):
    return {"results": [{"title": n["title"], "body": n["body"][:1500]} for n in search_notes(user["id"], query)]}


# ---------- web / system ----------
def site_check(url):
    if not url.startswith("http"):
        url = "https://" + url
    t = time.time()
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Trillion/0.1"})
        return {"url": url, "ok": r.status_code < 400, "status": r.status_code,
                "ms": int((time.time() - t) * 1000), "checked": time.time()}
    except Exception as e:
        return {"url": url, "ok": False, "status": 0, "error": type(e).__name__, "checked": time.time()}


def t_check_website(user, url):
    return site_check(url)


def t_system_status(user):
    return core.vitals()


def t_run_command(user, command):
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120,
                       cwd=str(core.user_home(user)))
    return {"exit_code": r.returncode, "output": (r.stdout + r.stderr)[-4000:]}


# ---------- self-update ----------
def _git(*args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", *args], cwd=str(core.BASE), capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def version():
    try:
        v = (core.BASE / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        v = "0.1"
    try:
        code, out = _git("rev-parse", "--short", "HEAD", timeout=5)
        if code == 0:
            v += " (" + out + ")"
    except Exception:
        pass
    return v


def t_update_self(user):
    if not (core.BASE / ".git").exists():
        return {"error": "Trillion is not connected to GitHub yet. Run '3 - Connect to GitHub.bat' once."}
    try:
        code, before = _git("rev-parse", "HEAD", timeout=10)
        code, out = _git("pull", "--ff-only")
    except FileNotFoundError:
        return {"error": "Git is not installed. Run Setup again."}
    if code != 0:
        return {"error": "Could not pull the update: " + out[-400:]}
    _, after = _git("rev-parse", "HEAD", timeout=10)
    if before == after:
        return {"status": "already_up_to_date", "version": version()}
    _, changes = _git("log", "--pretty=%s", f"{before}..{after}", timeout=10)
    _, files = _git("diff", "--name-only", before, after, timeout=10)
    if "requirements.txt" in files:
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
                       cwd=str(core.BASE), timeout=900)
    import threading
    threading.Timer(4, lambda: os._exit(42)).start()  # the start script restarts on code 42
    return {"status": "updated", "version": version(), "what_changed": changes.splitlines()[:10],
            "note": "Restarting in a few seconds. Tell the user the dashboard will reconnect by itself."}


# ---------- skills and connectors ----------
def t_use_skill(user, name):
    import extend
    sk = extend.find_skill(user["id"], name)
    if not sk:
        return {"error": f"No switched-on skill called {name}.",
                "available": [x["name"] for x in extend.enabled_skills(user["id"])]}
    return {"skill": sk["name"], "instructions": (extend.skill_body(sk["slug"]) or "")[:14000],
            "note": "Follow these instructions with your own tools. You cannot run scripts the skill refers to."}


def t_draft_skill(user, name, description, instructions):
    import extend
    name = name[:70]
    if core.q("SELECT 1 FROM skills WHERE slug=?", (extend.slugify(name),)):  # never overwrite an existing skill
        name = f"{name} (draft {time.strftime('%d %b %H%M')})"
    slug = extend.save_skill(name, description[:400], instructions[:20000], f"drafted for {user['name']}",
                             status="draft")
    core.log(user["id"], f"Waiting on you: new playbook '{name[:60]}' needs approval on the Skills page", "waiting")
    return {"status": "saved_as_draft", "name": name, "slug": slug,
            "note": "Tell the user it is waiting for the owner's approval on the Skills page."}


def t_connector_tools(user, connector):
    import extend
    row = extend.find_connector(user["id"], connector)
    if not row:
        return {"error": f"No switched-on connector called {connector}.",
                "available": [r["name"] for r in extend.enabled_connectors(user["id"])]}
    return {"connector": row["name"], "tools": extend.brief_tools(extend.client(row).tools())}


def t_connector_call(user, connector, tool, arguments=None):
    import extend
    row = extend.find_connector(user["id"], connector)
    if not row:
        return {"error": f"No switched-on connector called {connector}."}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    return extend.client(row).call(tool, arguments or {})


# ---------- the user's own phone ----------
def t_phone_status(user):
    import phone
    return phone.status(user)


def t_phone_open_app(user, app):
    import phone
    return phone.open_app(user, app)


def t_phone_find_contact(user, name):
    import phone
    found = phone.contacts(user, name)
    return {"contacts": [{"name": k, "numbers": v} for k, v in list(found.items())[:10]]} if found \
        else {"contacts": [], "note": f"Nobody called {name} in the phone's contacts."}


def t_phone_send_whatsapp(user, contact, message, _name=None):
    import phone
    res = phone.send_whatsapp(user, contact, message)
    if _name:
        res["to"] = _name
    return res


def t_phone_locate(user):
    import phone
    return phone.locate(user)


def t_phone_ring(user):
    import phone
    return phone.ring(user)


def t_phone_screenshot(user):
    import phone
    return phone.screenshot(user)
