"""Trillion tools: what the assistant can actually do on the PC."""
import os, re, json, math, shutil, subprocess, time, fnmatch
from pathlib import Path

import httpx

import core
import watch


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
    fn("read_website", "Open a website in a browser on the PC, read the whole site page by page, analyse it, and make files from "
       "it: outputs can include pptx (presentation), docx (report document) and xlsx (spreadsheet of pages). focus is what the "
       "user wants to learn or the purpose of the files. Runs in the background; tell the user it has started.",
       {"url": S, "outputs": {"type": "array", "items": S}, "focus": S, "max_pages": {"type": "integer"}}, ["url"]),
    fn("clone_website", "Clone a website into website files and folders. mode copy = a full working copy (only the user's own "
       "site, or one whose owner gave permission: set permission true only if the user said so). mode rebuild = a new site for "
       "the user's own business in the layout and style of url, with new words; brand is the business name and about what it does.",
       {"url": S, "mode": S, "brand": S, "about": S, "permission": {"type": "boolean"}, "max_pages": {"type": "integer"}}, ["url", "mode"]),
    fn("check_website", "Check whether a website is up and how fast it answers.", {"url": S}),
    fn("system_status", "Get the PC's current processor, memory, disk and graphics card readings.", {}),
    fn("use_skill", "Load the full instructions of one of the user's installed skills. Call this before doing "
       "a job that one of the listed skills covers, then follow what it says.", {"name": S}),
    fn("draft_skill", "Write down a reusable playbook for a job the user does repeatedly. It is saved as a draft "
       "and only becomes usable after the owner approves it on the Skills page.",
       {"name": S, "description": S, "instructions": S}),
    fn("connector_tools", "List what one of the user's connectors can do (its tools and their arguments). "
       "Call this before connector_call. A connector with many tools first returns only their names: call again with "
       "find set to a few words (for example: javascript, website, decompile) to get the matching tools in full.",
       {"connector": S, "find": S}, ["connector"]),
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
    fn("watch_alert", "Show a short alert on the user's watch (it arrives as a notification on their phone, which the watch "
       "mirrors). Use when they ask to be told something on their watch.", {"title": S, "text": S}, ["text"]),
    fn("phone_read_screen", "Read what is on the user's phone screen right now: the visible texts, buttons and fields. "
       "Do this before tapping so you know what is there.", {}),
    fn("phone_tap", "Tap something on the user's phone screen. Give the visible text of the button (target), or x and y.",
       {"target": S, "x": {"type": "integer"}, "y": {"type": "integer"}}, []),
    fn("phone_type", "Type text into the field that is selected on the user's phone. Tap the field first.", {"text": S}),
    fn("phone_press", "Press a phone key: back, home, recents, enter, delete, volume up, volume down, play, pause, next, lock, wake.",
       {"key": S}),
    fn("phone_swipe", "Swipe the phone screen up, down, left or right (swipe up to scroll down a page).", {"direction": S}),
    fn("phone_shell", "Run an Android shell command on the user's phone. Owner only, and always needs approval.", {"command": S}),
    fn("phone_screenshot", "Take a screenshot of the user's phone screen and save it to their folder.", {}),
    fn("create_image", "Create a picture (PNG) from a description with Google's image model, using this login's own Gemini key. "
       "shape is square, tall or wide.", {"prompt": S, "shape": S}, ["prompt"]),
    fn("create_video", "Create a finished cinematic social media video (MP4) from a plain description. No media is "
       "needed: she writes the script, finds stock footage, adds voice-over, music, titles and captions. "
       "format is vertical, square or landscape. Takes a few minutes.",
       {"prompt": S, "seconds": {"type": "integer"}, "format": S, "voiceover": {"type": "boolean"}}, ["prompt"]),
    fn("create_ai_clip", "Generate a short AI-made video clip (2 to 15 seconds, with sound) on this PC's own graphics card with "
       "FreeVideo. Use only when the user asks for an AI video, AI clip or generated clip. Takes several minutes. "
       "format is vertical, square or landscape.",
       {"prompt": S, "seconds": {"type": "integer"}, "format": S}, ["prompt"]),
    fn("create_ebook", "Write and lay out a designed ebook (PDF with cover, contents page and chapters) from a plain description. "
       "Takes several minutes.", {"prompt": S, "chapters": {"type": "integer"}}, ["prompt"]),
    fn("gumroad_sales", "The user's Gumroad store sales and earnings for a period. period is one of: today, week, days30, month, all.",
       {"period": S}),
    fn("gumroad_publish", "Publish or unpublish one product in the user's Gumroad store. product is its name.",
       {"product": S, "published": {"type": "boolean"}}),
    fn("gumroad_discount_code", "Create a percentage discount code for one product in the user's Gumroad store.",
       {"product": S, "code": S, "percent": {"type": "integer"}}),
    fn("gumroad_set_price", "Change the price of one product in the user's Gumroad store. price is in the product's currency, "
       "like 9.99. Needs the user's approval.", {"product": S, "price": {"type": "number"}}),
    fn("gumroad_refund", "Refund one Gumroad sale in full. sale is the buyer's email address or the sale number. "
       "Needs the user's approval.", {"sale": S}),
    fn("update_self", "Update Trillion herself to the newest version from GitHub, then restart. Owner only. "
       "Use when the user says update yourself, get the latest version, or pull the update.", {}),
    fn("run_command", "Run a Windows command. Owner only, and always needs approval on the dashboard.",
       {"command": S}),
]
NEEDS_APPROVAL = {"delete_path", "run_command", "phone_send_whatsapp", "phone_shell", "gumroad_set_price", "gumroad_refund"}
FULL_PHONE = {"phone_read_screen", "phone_tap", "phone_type", "phone_press", "phone_swipe", "phone_shell"}


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
        "create_image": f"Creating a picture: {g('prompt')[:80]}",
        "create_powerpoint": f"Creating PowerPoint {g('path')}", "save_note": f"Remembering: {g('title')}",
        "search_memory": f"Searching memory for {g('query')}", "check_website": f"Checking {g('url')}",
        "use_skill": f"Reading the skill {g('name')}", "draft_skill": f"Drafting a playbook: {g('name')}",
        "connector_tools": f"Checking what {g('connector')} can do",
        "connector_call": f"Using {g('connector')}: {g('tool')}",
        "gumroad_sales": "Reading Gumroad sales", "gumroad_publish": f"{'Publish' if a.get('published') else 'Unpublish'} {g('product')} on Gumroad",
        "gumroad_discount_code": f"Gumroad code {g('code')}: {g('percent')}% off {g('product')}",
        "gumroad_set_price": f"Change the Gumroad price of {a.get('_name') or g('product')} to {g('price')}",
        "gumroad_refund": f"Refund the Gumroad sale of {a.get('_name') or g('sale')}",
        "phone_status": "Checking the phone", "phone_open_app": f"Opening {g('app')} on the phone",
        "phone_find_contact": f"Looking up {g('name')} in contacts",
        "phone_send_whatsapp": f"Send WhatsApp to {a.get('_name') or g('contact')}"
                               + (f" (+{a.get('contact')})" if a.get('_name') and a.get('contact') and not str(a.get('contact', '')).startswith('chat:') else "") + (f": \"{str(a.get('message', ''))[:120]}\"" if str(a.get('message', '')).strip() else "")
                               + (f" with {len(a['files'])} file{'s' if len(a['files']) != 1 else ''}" if a.get('files') else ""),
        "phone_read_screen": "Reading the phone screen", "phone_tap": f"Tapping {g('target') or 'the screen'}",
        "phone_type": "Typing on the phone", "phone_press": f"Pressing {g('key')}", "phone_swipe": f"Swiping {g('direction')}",
        "phone_shell": f"Run on the phone: {g('command')}",
        "phone_locate": "Locating the phone", "phone_ring": "Ringing the phone",
        "clone_website": f"{'Copying' if g('mode') == 'copy' else 'Rebuilding the style of'} {g('url')}",
        "read_website": f"Reading the website {g('url')}",
        "watch_alert": f"Alert on the watch: {g('text')[:80]}",
        "phone_screenshot": "Taking a phone screenshot",
        "create_video": "Creating a video",
        "create_ai_clip": "Generating an AI clip",
        "create_ebook": "Writing an ebook",
        "system_status": "Checking the PC", "update_self": "Updating myself from GitHub", "run_command": f"Run command: {g('command')}",
    }.get(name, name)


def execute(user, name, args, task_id=None, approved=False, direct=False):
    """Run one tool. Returns a JSON-safe dict. Never raises."""
    uid = user["id"]
    try:
        if name not in {s["function"]["name"] for s in SPECS}:
            return {"error": f"Unknown tool {name}"}
        if name == "run_command" and not user["is_admin"]:
            return {"error": "Only the owner login can run commands."}
        if name == "phone_shell" and not user["is_admin"]:
            return {"error": "Only the owner login can run commands on a phone."}
        if name in FULL_PHONE and not core.settings(user).get("phone_full_control"):
            return {"error": "Full phone control is switched off. Switch it on on the Phone page."}
        if name == "update_self" and not user["is_admin"]:
            return {"error": "Only the owner login can update Trillion."}
        if name in NEEDS_APPROVAL and not approved:
            if name == "delete_path":
                resolve(user, args.get("path"))  # permission check before asking
            if name == "gumroad_set_price":       # settle exactly which product before asking
                import gumroad
                p = gumroad.find_product(user, args.get("product"))
                args = {"product": p["id"], "price": float(args.get("price")), "_name": f"{p['name']} (now {p['shown']})"}
            if name == "gumroad_refund":          # settle exactly which sale before asking
                import gumroad
                s_ = gumroad.find_sale(user, args.get("sale"))
                if s_["refunded"]:
                    return {"error": "That sale is already refunded."}
                args = {"sale": s_["id"], "_name": f"{s_['product']} to {s_['email']} ({s_['shown']})"}
            if name == "phone_send_whatsapp":  # settle exactly who it goes to before asking
                import phone
                who, number = phone.resolve_contact(user, str(args.get("contact", "")))
                ids = [int(i) for i in (args.get("files") or []) if str(i).isdigit()][:10]
                args = {"contact": number, "message": str(args.get("message") or ""), "_name": who}
                if ids:
                    if len(whatsapp_files(user, ids)) != len(set(ids)):
                        return {"error": "One of the attached files is no longer on the PC. Attach it again."}
                    args["files"] = ids
                if not args["message"].strip() and not ids:
                    return {"error": "There is no message to send."}
                st = core.settings(user)
                own = phone.normalise(st["phone_own_number"], str(st.get("phone_country_code", "27"))) if st.get("phone_own_number") else None
                in_contacts = who != number and not re.fullmatch(r"[\d\s+()-]{7,}", who)   # a saved name, not a typed number
                # the user's standing choice: saved contacts get the message at once, however the request was worded
                if st.get("whatsapp_auto_contacts") and (in_contacts or number == own):
                    out = t_phone_send_whatsapp(user, **args)
                    core.log(uid, describe(name, args), "done")
                    return out
            summary = describe(name, args)
            core.x("INSERT INTO approvals(user_id,task_id,tool,args,summary,created) VALUES(?,?,?,?,?,?)",
                   (uid, task_id, name, json.dumps(args), summary, time.time()))
            core.log(uid, f"Waiting on you: {summary}", "waiting")
            watch.alert(user, "approval", "Trillion needs your OK", summary)
            return {"status": "waiting_for_approval",
                    "note": "Not done yet. The user must approve this on the dashboard."}
        if name in ("create_video", "create_ai_clip", "create_ebook"):
            args = dict(args, _task=task_id)
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


RUNNING = None  # the version this running copy was started with (set at start-up)


def t_update_self(user):
    if not (core.BASE / ".git").exists():
        return {"error": "Trillion is not connected to GitHub yet. Run '3 - Connect to GitHub.bat' once."}
    was = RUNNING or version()
    try:
        _, before = _git("rev-parse", "HEAD", timeout=10)
        code, out = _git("fetch", "origin")
    except FileNotFoundError:
        return {"error": "Git is not installed. Run Setup again."}
    except subprocess.TimeoutExpired:
        return {"error": "GitHub did not answer in time. Check the internet connection and try again."}
    if code != 0:
        return {"error": "Could not reach GitHub: " + out[-400:]}
    code, target = _git("rev-parse", "--abbrev-ref", "@{u}", timeout=10)
    if code != 0 or not target.strip():
        target = "origin/main"
    target = target.strip().splitlines()[-1]
    code, out = _git("merge", "--ff-only", target)
    if code != 0:
        # something on this PC is in the way of a normal update (an edited or half-written file):
        # take GitHub's copy exactly. Logins, keys, memory and files live in data/, which git never touches.
        code, out = _git("reset", "--hard", target)
        if code != 0:
            return {"error": "Could not apply the update: " + out[-400:]}
    _, after = _git("rev-parse", "HEAD", timeout=10)
    now = version()
    if before == after and now == was:
        return {"status": "already_up_to_date", "version": now}
    changes, files = "", ""
    if before != after:
        _, changes = _git("log", "--pretty=%s", f"{before}..{after}", timeout=10)
        _, files = _git("diff", "--name-only", before, after, timeout=10)
    if "requirements.txt" in files:
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
                       cwd=str(core.BASE), timeout=900)
    import threading
    threading.Timer(4, lambda: os._exit(42)).start()  # the start script restarts on code 42
    return {"status": "updated", "version": now, "was": was, "what_changed": changes.splitlines()[:10],
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


MANY_TOOLS = 30


def t_connector_tools(user, connector, find=""):
    import extend
    row = extend.find_connector(user["id"], connector)
    if not row:
        return {"error": f"No switched-on connector called {connector}.",
                "available": [r["name"] for r in extend.enabled_connectors(user["id"])]}
    tools = extend.client(row).tools()
    words = [w for w in re.findall(r"[a-z0-9]{3,}", str(find or "").lower())]
    if words:      # a long list narrowed down: tools whose name or description has the words, best matches first
        hay = lambda t: (t.get("name", "") + " " + (t.get("description") or "")).lower()
        scored = sorted(((sum(w in hay(t) for w in words) + sum(w in t.get("name", "").lower() for w in words), i, t)
                         for i, t in enumerate(tools)), key=lambda x: (-x[0], x[1]))
        hits = [t for n, _, t in scored if n][:12]
        return {"connector": row["name"], "find": find, "tools": extend.brief_tools(hits),
                "note": "" if hits else "No tool matched those words. Try other words, or leave find empty for the list of names."}
    if len(tools) > MANY_TOOLS:   # too many to describe at once for a local brain: names first
        return {"connector": row["name"], "count": len(tools), "names": [t.get("name") for t in tools],
                "note": "This connector has many tools. Call connector_tools again with find set to a few words "
                        "to get the matching ones with their arguments."}
    return {"connector": row["name"], "tools": extend.brief_tools(tools)}


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
        else {"contacts": [], "note": f"Nobody called {name} in the contact list I can read. phone_send_whatsapp can still be "
                                      "called with the name: it looks the person up inside WhatsApp itself."}


def whatsapp_files(user, ids):
    """The files a login attached, by their upload numbers. Only that login's own files; anything else is left out."""
    import attach
    out = []
    for i in (ids or [])[:10]:
        f = attach.get(user, i) if str(i).isdigit() else None
        if f and f["path"] not in [o["path"] for o in out]:
            out.append(f)
    return out


def t_phone_send_whatsapp(user, contact, message="", _name=None, files=None):
    import phone
    found = whatsapp_files(user, files)
    if files and len(found) != len(set(str(i) for i in files[:10])):
        return {"error": "One of the attached files is no longer on the PC. Attach it again."}
    res = phone.send_whatsapp(user, contact, message, [f["path"] for f in found])
    if _name and not str(contact).startswith("chat:"):
        res["to"] = _name
    return res


def _gum(fn, user=None):
    import gumroad
    try:
        return fn(gumroad)
    except gumroad.GumroadError as e:
        return {"error": str(e)}


def t_gumroad_sales(user, period="today"):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    period = {"7": "week", "30": "days30", "total": "all"}.get(str(period).lower(), str(period).lower())
    def go(g):
        try:
            g.sync(user)
        except g.GumroadError:
            pass
        return {"answer": g.say_sales(user, period if period in ("today", "week", "days30", "month", "all") else "today")}
    return _gum(go)


def t_gumroad_publish(user, product, published=True):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        g.set_published(user, p["id"], bool(published))
        return {"ok": True, "product": p["name"], "published": bool(published)}
    return _gum(go)


def t_gumroad_discount_code(user, product, code="", percent=10):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        return {"ok": True, "product": p["name"], "code": g.create_code(user, p["id"], code or f"SAVE{int(percent)}", percent), "percent": percent}
    return _gum(go)


def t_gumroad_set_price(user, product, price, _name=None):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        g.update_product(user, p["id"], price_cents=int(round(float(price) * (1 if p["currency"] == "jpy" else 100))))
        return {"ok": True, "product": p["name"], "price": price}
    return _gum(go)


def t_gumroad_refund(user, sale, _name=None):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        s_ = g.find_sale(user, sale)
        g.refund(user, s_["id"])
        return {"ok": True, "refunded": s_["product"], "to": s_["email"]}
    return _gum(go)


def t_phone_locate(user):
    import phone
    return phone.locate(user)


def t_phone_ring(user):
    import phone
    return phone.ring(user)


def t_clone_website(user, url, mode="copy", brand="", about="", permission=False, max_pages=None):
    import siteclone, siteread
    try:
        j = siteclone.start(user, mode, url, {"brand": brand, "about": about},
                            max_pages or (siteclone.DEFAULT_COPY_PAGES if mode == "copy" else 4), bool(permission))
    except siteread.SiteError as e:
        return {"error": str(e)}
    return {"started": True, "note": siteclone.started_reply(j)}


def t_read_website(user, url, outputs=None, focus="", max_pages=40):
    import siteread
    try:
        j = siteread.start(user, url, outputs or siteread.wants(focus or ""), focus, max_pages)
    except siteread.SiteError as e:
        return {"error": str(e)}
    return {"started": True, "note": siteread.started_reply(j)}


def t_watch_alert(user, text, title="Trillion"):
    r = watch.send(user, title or "Trillion", text)
    return {"ok": True, "note": "Posted on the phone; the watch shows it if phone notifications are mirrored."} if r["ok"] else {"error": r["error"]}


def t_phone_screenshot(user):
    import phone
    return phone.screenshot(user)


# ---------- pictures ----------
IMAGE_API = os.environ.get("TRILLION_IMAGE_API", "https://generativelanguage.googleapis.com/v1beta/models")
IMAGE_MODELS = ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"]


def t_create_image(user, prompt, shape="square"):
    """Google's image model, called with the login's own key. Tries the newest model first, then the older one."""
    import base64
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"error": "Say what the picture should show."}
    key = core.get_secret(user["id"], "gemini")
    if not key:
        return {"error": "Making pictures needs a Google Gemini key. Add one under Settings, Keys (Google Gemini), tick it on and click SAVE."}
    shape = str(shape or "square").lower()
    ratio = "9:16" if shape in ("tall", "vertical", "portrait") else ("16:9" if shape in ("wide", "landscape", "horizontal") else "1:1")
    wanted = [m for m in [core.settings(user).get("image_model")] + IMAGE_MODELS if m]
    last = "no answer"
    for model in dict.fromkeys(wanted):
        for cfg in ({"responseModalities": ["IMAGE", "TEXT"], "imageConfig": {"aspectRatio": ratio}}, {"responseModalities": ["IMAGE", "TEXT"]}):
            try:
                r = httpx.post(f"{IMAGE_API}/{model}:generateContent", params={"key": key}, timeout=180,
                               json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg})
            except Exception as e:
                last = f"could not reach Google ({type(e).__name__})"
                break
            if r.status_code == 200:
                data = None
                for cand in r.json().get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        blob = part.get("inlineData") or part.get("inline_data")
                        if blob and blob.get("data"):
                            data = base64.b64decode(blob["data"])
                            break
                    if data:
                        break
                if not data:
                    last = "Google answered without a picture (the request may have been refused)"
                    break
                folder = core.user_home(user) / "Trillion Images"
                folder.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "picture"
                path = folder / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.png"
                path.write_bytes(data)
                core.bump_usage(user["id"], "gemini")
                return {"saved": str(path), "file": path.name, "model": model, "shape": ratio}
            try:
                msg = (r.json().get("error") or {}).get("message", "")[:200]
            except Exception:
                msg = r.text[:200]
            last = {429: "your Gemini key has used up its allowance for now", 403: "Google refused the key for picture making",
                    401: "Google did not accept the key"}.get(r.status_code, f"code {r.status_code}") + (f": {msg}" if msg else "")
            if r.status_code == 400 and "imageConfig" in cfg:
                continue                       # this model does not take a shape: ask again without it
            break
        if last.startswith(("your Gemini key", "Google did not accept", "could not reach")):
            break
    return {"error": "I could not make the picture: " + last + "."}


# ---------- video ----------
def t_create_video(user, prompt, seconds=20, format="vertical", voiceover=True, _task=None, media=None):
    import video
    fmt = str(format or "vertical").lower()
    fmt = "landscape" if fmt in ("landscape", "horizontal", "wide", "youtube") else ("square" if fmt == "square" else "vertical")
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return video.create(core.LLM, user, prompt, seconds, fmt, bool(voiceover), progress=say, media=media)


def t_create_ai_clip(user, prompt, seconds=5, format="vertical", _task=None):
    import freevideo
    fmt = str(format or "vertical").lower()
    fmt = "landscape" if fmt in ("landscape", "horizontal", "wide", "youtube") else ("square" if fmt == "square" else "vertical")
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return freevideo.create(core.LLM, user, prompt, seconds, fmt, progress=say)


def t_create_ebook(user, prompt, chapters=5, _task=None, sources=None, images=None, pictures=False):
    import ebook
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return ebook.create(core.LLM, user, prompt, chapters, progress=say, sources=sources, images=images, pictures=bool(pictures))


def t_phone_read_screen(user):
    import phone
    return phone.screen(user)


def t_phone_tap(user, target="", x=None, y=None):
    import phone
    return phone.tap(user, target, x, y)


def t_phone_type(user, text):
    import phone
    return phone.type_text(user, text)


def t_phone_press(user, key):
    import phone
    return phone.press(user, key)


def t_phone_swipe(user, direction):
    import phone
    return phone.swipe(user, direction)


def t_phone_shell(user, command):
    import phone
    return phone.shell(user, command)
