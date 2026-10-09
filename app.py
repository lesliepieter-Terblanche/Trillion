"""Trillion: a local agent OS for one PC and one family. Run with Start-Trillion.bat."""
import io, json, time, threading, datetime, zipfile

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse

import core
import tools

core.init()
app = FastAPI(title="Trillion")
COOKIE = "trillion"
BUSY, STOP, SITES, LAST_PROVIDER = {}, set(), {}, {}
VERSION = tools.version()

PROVIDERS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com/v1",
}


# ---------- auth ----------
def auth(request: Request):
    u = core.user_by_token(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Please sign in")
    return u


def admin(user=Depends(auth)):
    if not user["is_admin"]:
        raise HTTPException(403, "Owner only")
    return user


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    page = "index.html" if core.user_by_token(request.cookies.get(COOKIE)) else "login.html"
    return HTMLResponse((core.BASE / "static" / page).read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/users")
def users():
    return [{"name": u["name"], "has_password": bool(u["pw_hash"]),
             "accent": core.settings(u)["accent"], "brand": core.settings(u)["brand"]}
            for u in core.q("SELECT * FROM users ORDER BY id")]


@app.post("/api/login")
def login(response: Response, p: dict = Body(...)):
    u = core.q("SELECT * FROM users WHERE name=?", (p.get("name", ""),), one=True)
    pw = p.get("password", "")
    if not u:
        raise HTTPException(404, "No such login")
    if not u["pw_hash"]:
        if len(pw) < 6:
            raise HTTPException(400, "Choose a password of at least 6 characters")
        core.set_password(u["id"], pw)
        core.log(u["id"], "Password created", "info")
    elif not core.check_password(u, pw):
        time.sleep(1)
        raise HTTPException(401, "Wrong password")
    response.set_cookie(COOKIE, core.new_session(u["id"]), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    core.x("DELETE FROM sessions WHERE token=?", (request.cookies.get(COOKIE),))
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- the brain ----------
def llm_chat(user, messages, tool_specs=None):
    """One model call in OpenAI chat format. Local by default; a cloud brain only if that login enabled it."""
    s = core.settings(user)
    uid = user["id"]
    brain = s.get("brain", "local")
    key = core.get_secret(uid, brain) if brain in PROVIDERS else None
    body = {"messages": messages}
    if tool_specs:
        body.update(tools=tool_specs, tool_choice="auto")
    if key:
        try:
            r = httpx.post(PROVIDERS[brain] + "/chat/completions", timeout=180,
                           headers={"Authorization": f"Bearer {key}"},
                           json=dict(body, model=s.get("cloud_model") or "gemini-2.5-flash"))
            r.raise_for_status()
            core.bump_usage(uid, brain)
            LAST_PROVIDER[uid] = brain
            return r.json()["choices"][0]["message"]
        except Exception as e:
            core.log(uid, f"Cloud brain ({brain}) failed, used the local one instead: {type(e).__name__}", "waiting")
    model = core.pick_local_model(s.get("local_model", "qwen2.5:14b"))
    # Ollama's own chat endpoint, so the memory window can be set. The default window is too
    # small for the tool list and cuts the instructions off, which makes the model ramble.
    native = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            fixed = []
            for c in m["tool_calls"]:
                a = c["function"].get("arguments") or {}
                if not isinstance(a, dict):
                    try:
                        a = json.loads(a)
                    except Exception:
                        a = {}
                fixed.append({"function": {"name": c["function"]["name"], "arguments": a}})
            m["tool_calls"] = fixed
        m.pop("tool_call_id", None)
        native.append(m)
    req = {"model": model, "messages": native, "stream": False, "keep_alive": "30m",
           "options": {"num_ctx": int(s.get("context_size", 12288)), "temperature": 0.2}}
    if tool_specs:
        req["tools"] = tool_specs
    try:
        r = httpx.post(core.OLLAMA + "/api/chat", timeout=600, json=req)
    except httpx.ConnectError:
        raise RuntimeError("The local brain (Ollama) is not running. Start Ollama and try again.")
    if r.status_code == 400 and "does not support tools" in r.text:
        raise RuntimeError(f"The model {model} cannot use tools. Set the local model to qwen2.5:14b in Settings.")
    r.raise_for_status()
    LAST_PROVIDER[uid] = model
    return r.json()["message"]


def system_prompt(user, text):
    s = core.settings(user)
    mem = tools.search_notes(user["id"], text, k=4)
    memory = "\n".join(f"- {m['title']}: {m['body'][:300]}" for m in mem) or "(nothing relevant saved yet)"
    return f"""You are {s['assistant_name']}, a private AI assistant running on the family's own PC.
You are talking to {user['name']}{' (the owner)' if user['is_admin'] else ''}. Their business: {s['brand'] or 'none set'} ({s['site'] or 'no site'}).
Today is {datetime.datetime.now().strftime('%A %d %B %Y, %H:%M')}. Their home folder is {core.user_home(user)}.

How to work:
- Use your tools to actually do things. Never say something was done unless a tool result confirms it.
- Deleting files and running commands need approval: the tool will report waiting_for_approval. Then tell the user it is waiting for them on the dashboard, and do not retry it.
- If a tool returns an error, say plainly what went wrong.
- When the user tells you a lasting fact or preference, save it with save_note.
- Always reply in English.
- Replies are spoken aloud: keep them short and natural, no markdown, no lists.

Relevant things you remember:
{memory}"""


def run_agent(user, text):
    uid = user["id"]
    BUSY[uid] = True
    STOP.discard(uid)
    tid = core.add_task(uid, text)
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "user", text, time.time()))
    core.log(uid, f'Heard: "{text[:140]}"', "voice")
    hist = core.q("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 12", (uid,))[::-1]
    msgs = [{"role": "system", "content": system_prompt(user, text)}] + [dict(h) for h in hist]
    status, waiting, reply = "completed", False, ""
    try:
        for _ in range(10):
            if uid in STOP:
                reply, status = "Stopped.", "failed"
                break
            m = llm_chat(user, msgs, tools.SPECS)
            calls = m.get("tool_calls") or []
            if not calls:
                reply = (m.get("content") or "").strip() or "Done."
                break
            msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
            for c in calls:
                name = c["function"]["name"]
                raw = c["function"].get("arguments") or "{}"
                try:
                    args = raw if isinstance(raw, dict) else json.loads(raw)
                except Exception:
                    args = {}
                core.set_task(tid, detail=tools.describe(name, args))
                result = tools.execute(user, name, args, task_id=tid)
                waiting = waiting or result.get("status") == "waiting_for_approval"
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name),
                             "content": json.dumps(result, default=str)[:6000]})
        else:
            reply = "That took more steps than I allow in one go. Tell me to continue and I'll pick it up."
    except Exception as e:
        status = "failed"
        reply = str(e) if isinstance(e, RuntimeError) else f"Something went wrong: {type(e).__name__}: {e}"
        core.log(uid, reply[:300], "waiting")
    finally:
        BUSY[uid] = False
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
    core.set_task(tid, status="waiting" if waiting and status == "completed" else status, detail=reply[:200])
    core.log(uid, f"Answered by {LAST_PROVIDER.get(uid, 'local')}", "info")
    return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}


@app.post("/api/chat")
def chat(p: dict = Body(...), user=Depends(auth)):
    text = (p.get("message") or "").strip()
    if not text:
        raise HTTPException(400, "Nothing to do")
    if BUSY.get(user["id"]):
        raise HTTPException(409, "Still busy with the previous job")
    return run_agent(user, text[:4000])


@app.post("/api/stop")
def stop(user=Depends(auth)):
    STOP.add(user["id"])
    return {"ok": True}


@app.post("/api/approvals/{aid}")
def decide(aid: int, p: dict = Body(...), user=Depends(auth)):
    a = core.q("SELECT * FROM approvals WHERE id=? AND user_id=? AND status='pending'", (aid, user["id"]), one=True)
    if not a:
        raise HTTPException(404, "Nothing waiting with that number")
    if p.get("decision") == "approve":
        result = tools.execute(user, a["tool"], json.loads(a["args"]), task_id=a["task_id"], approved=True)
        ok = "error" not in result
        core.x("UPDATE approvals SET status=? WHERE id=?", ("done" if ok else "failed", aid))
        if not ok:
            core.log(user["id"], f"Could not do it: {result['error']}"[:300], "waiting")
    else:
        result, ok = {"declined": True}, True
        core.x("UPDATE approvals SET status='declined' WHERE id=?", (aid,))
        core.log(user["id"], f"You declined: {a['summary']}", "info")
    if a["task_id"] and not core.q("SELECT 1 FROM approvals WHERE task_id=? AND status='pending'", (a["task_id"],)):
        core.set_task(a["task_id"], status="completed" if ok else "failed")
    return result


# ---------- voice ----------
_whisper = None


def transcribe(data):
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        import os
        dev = os.environ.get("TRILLION_WHISPER_DEVICE", "cpu")
        _whisper = WhisperModel(os.environ.get("TRILLION_WHISPER_MODEL", "small"), device=dev,
                                compute_type="float16" if dev == "cuda" else "int8")
    segs, _ = _whisper.transcribe(io.BytesIO(data), vad_filter=True)
    return " ".join(s.text.strip() for s in segs).strip()


@app.post("/api/stt")
async def stt(request: Request, user=Depends(auth)):
    data = await request.body()
    if len(data) < 800:
        return {"text": ""}
    try:
        return {"text": await run_in_threadpool(transcribe, data)}
    except ImportError:
        raise HTTPException(501, "Local hearing is not installed yet. Run Setup again.")
    except Exception as e:
        raise HTTPException(500, f"Could not understand the recording: {type(e).__name__}")


@app.post("/api/tts")
def tts(p: dict = Body(...), user=Depends(auth)):
    s = core.settings(user)
    key = core.get_secret(user["id"], "elevenlabs") if s.get("voice") == "elevenlabs" else None
    if not key:
        return Response(status_code=204)  # the browser speaks with the PC's own voice
    try:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s['eleven_voice_id']}",
                       params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key}, timeout=60,
                       json={"text": (p.get("text") or "")[:1500], "model_id": "eleven_turbo_v2_5"})
        r.raise_for_status()
        core.bump_usage(user["id"], "elevenlabs")
        return Response(r.content, media_type="audio/mpeg")
    except Exception as e:
        core.log(user["id"], f"ElevenLabs voice failed, used the PC voice: {type(e).__name__}", "waiting")
        return Response(status_code=204)


# ---------- dashboard state ----------
@app.get("/api/state")
def state(user=Depends(auth)):
    uid, s = user["id"], core.settings(user)
    ds = core.day_start()
    tasks = core.q("SELECT * FROM tasks WHERE user_id=? AND (created>=? OR status IN ('in_progress','waiting')) "
                   "ORDER BY id DESC LIMIT 60", (uid, ds))
    use = core.usage_today(uid)
    n = lambda d: core.q("SELECT COUNT(*) c FROM notes WHERE user_id=? AND domain=?", (uid, d), one=True)["c"]
    return {
        "user": {"name": user["name"], "is_admin": bool(user["is_admin"]), "settings": s,
                 "home": str(core.user_home(user))},
        "busy": bool(BUSY.get(uid)), "provider": LAST_PROVIDER.get(uid, "local"),
        "vitals": core.vitals(), "models": core.models(),
        "tasks": [dict(t) for t in tasks],
        "approvals": [dict(a) for a in core.q(
            "SELECT id, summary, tool, created FROM approvals WHERE user_id=? AND status='pending' ORDER BY id", (uid,))],
        "activity": [dict(a) for a in core.q(
            "SELECT ts, text, kind FROM activity WHERE user_id=? ORDER BY id DESC LIMIT 40", (uid,))],
        "usage": use, "cloud_calls": sum(use.values()),
        "notes": {"personal": n("PERSONAL"), "work": n("WORK"),
                  "today": core.q("SELECT COUNT(*) c FROM notes WHERE user_id=? AND ts>=?", (uid, ds), one=True)["c"]},
        "site": SITES.get(s.get("site")) if s.get("site") else None,
        "day_start": ds, "now": time.time(), "version": VERSION,
    }


@app.post("/api/messages/clear")
def messages_clear(user=Depends(auth)):
    core.x("DELETE FROM messages WHERE user_id=?", (user["id"],))
    return {"ok": True}


@app.get("/api/messages")
def messages(user=Depends(auth)):
    return [dict(m) for m in core.q(
        "SELECT role, content, ts FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 30", (user["id"],))][::-1]


# ---------- settings and keys ----------
EDITABLE = ["assistant_name", "brand", "accent", "site", "brain", "local_model", "cloud_model", "voice",
            "eleven_voice_id", "daily_review"]


@app.put("/api/settings")
def put_settings(p: dict = Body(...), user=Depends(auth)):
    s = core.settings(user)
    for k in EDITABLE:
        if k in p:
            s[k] = p[k]
    if s["brain"] not in ("local", "gemini", "anthropic"):
        s["brain"] = "local"
    core.save_settings(user["id"], s)
    if s.get("site"):
        threading.Thread(target=lambda: SITES.update({s["site"]: tools.site_check(s["site"])}), daemon=True).start()
    return s


@app.get("/api/secrets")
def secrets_list(user=Depends(auth)):
    return core.list_secrets(user["id"])


@app.put("/api/secrets/{name}")
def secret_put(name: str, p: dict = Body(...), user=Depends(auth)):
    if name not in core.SECRET_NAMES:
        raise HTTPException(404, "Unknown connection")
    value = (p.get("value") or "").strip() or core.get_secret(user["id"], name, only_enabled=False)
    if not value:
        raise HTTPException(400, "Paste the key first")
    core.set_secret(user["id"], name, value, bool(p.get("enabled", True)))
    return {"ok": True}


@app.delete("/api/secrets/{name}")
def secret_del(name: str, user=Depends(auth)):
    core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], name))
    return {"ok": True}


@app.post("/api/secrets/{name}/test")
def secret_test(name: str, user=Depends(auth)):
    key = core.get_secret(user["id"], name, only_enabled=False)
    if not key:
        raise HTTPException(400, "No key saved")
    try:
        if name == "gemini":
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key}, timeout=15)
        elif name == "anthropic":
            r = httpx.get("https://api.anthropic.com/v1/models", timeout=15,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            r = httpx.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key}, timeout=15)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "status": 0, "error": type(e).__name__}


# ---------- files and memory ----------
@app.get("/api/files")
def files(path: str = "", user=Depends(auth)):
    try:
        return tools.t_list_folder(user, path or ".")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.get("/api/download")
def download(path: str, user=Depends(auth)):
    try:
        p = tools.resolve(user, path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "Not a file")
    return FileResponse(p, filename=p.name)


@app.get("/api/notes")
def notes(query: str = "", user=Depends(auth)):
    if query:
        return tools.search_notes(user["id"], query, k=20)
    return [dict(r) for r in core.q(
        "SELECT id, title, body, domain, ts FROM notes WHERE user_id=? ORDER BY ts DESC LIMIT 60", (user["id"],))]


@app.post("/api/notes")
def note_add(p: dict = Body(...), user=Depends(auth)):
    if not (p.get("title") or "").strip():
        raise HTTPException(400, "Give the note a title")
    return {"id": tools.add_note(user["id"], p["title"], p.get("body", ""), p.get("domain", "PERSONAL"))}


@app.delete("/api/notes/{nid}")
def note_del(nid: int, user=Depends(auth)):
    core.x("DELETE FROM notes WHERE id=? AND user_id=?", (nid, user["id"]))
    return {"ok": True}


# ---------- owner view ----------
@app.get("/api/admin/family")
def family(user=Depends(admin)):
    out = []
    for u in core.q("SELECT * FROM users ORDER BY id"):
        s = core.settings(u)
        out.append({"id": u["id"], "name": u["name"], "brand": s["brand"], "site": s["site"], "brain": s["brain"],
                    "has_password": bool(u["pw_hash"]), "roots": core.user_roots(u),
                    "connections": [c["name"] for c in core.list_secrets(u["id"]) if c["set"] and c["enabled"]],
                    "cloud_calls": sum(core.usage_today(u["id"]).values()),
                    "site_status": SITES.get(s["site"])})
    return out


@app.put("/api/admin/family/{uid}")
def family_put(uid: int, p: dict = Body(...), user=Depends(admin)):
    u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        raise HTTPException(404, "No such login")
    if "roots" in p:
        s = core.settings(u)
        s["roots"] = [r.strip() for r in p["roots"] if r.strip() and (r.strip() != "*" or u["is_admin"])]
        core.save_settings(uid, s)
    if p.get("reset_password"):
        core.x("UPDATE users SET pw_hash=NULL, salt=NULL WHERE id=?", (uid,))
        core.x("DELETE FROM sessions WHERE user_id=?", (uid,))
    return {"ok": True}


@app.get("/api/admin/backup")
def backup(user=Depends(admin)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in core.DATA.rglob("*"):
            if f.is_file() and not f.name.endswith(("-wal", "-shm")):
                z.write(f, f.relative_to(core.DATA))
    name = f"trillion-backup-{core.today()}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------- background: site watch and the daily review ----------
def daily_review(user):
    uid = user["id"]
    rows = core.q("SELECT role, content FROM messages WHERE user_id=? AND ts>=? ORDER BY id", (uid, core.day_start()))
    if len(rows) < 2:
        return 0
    convo = "\n".join(f"{r['role']}: {r['content'][:500]}" for r in rows)[-9000:]
    m = llm_chat(user, [
        {"role": "system", "content": "You review a day's conversation between an assistant and its user and "
         "extract lasting knowledge: facts about the user, their preferences, their businesses, and how they like "
         "jobs done. Reply with JSON only: a list of up to 6 objects with keys title, body, domain "
         "(PERSONAL or WORK). Reply [] if nothing is worth keeping."},
        {"role": "user", "content": convo}])
    text = (m.get("content") or "").strip()
    try:
        items = json.loads(text[text.index("["): text.rindex("]") + 1])
    except Exception:
        items = []
    for it in items[:6]:
        if isinstance(it, dict) and it.get("title"):
            tools.add_note(uid, f"Learned {core.today()}: {it['title']}", str(it.get("body", "")), it.get("domain", "PERSONAL"))
    core.log(uid, f"Daily review done: {len(items[:6])} new things learned", "done")
    return len(items[:6])


@app.post("/api/review")
def review_now(user=Depends(auth)):
    try:
        return {"learned": daily_review(user)}
    except Exception as e:
        raise HTTPException(500, str(e))


def background():
    last_sites = 0
    while True:
        try:
            if time.time() - last_sites > 180:
                last_sites = time.time()
                for u in core.q("SELECT * FROM users"):
                    site = core.settings(u).get("site")
                    if site:
                        prev = SITES.get(site)
                        SITES[site] = res = tools.site_check(site)
                        if prev and prev["ok"] and not res["ok"]:
                            core.log(u["id"], f"{site} looks down", "waiting")
            now = datetime.datetime.now()
            if now.hour == 23 and now.minute >= 30:
                for u in core.q("SELECT * FROM users"):
                    s = core.settings(u)
                    if s.get("daily_review") and s.get("last_review") != core.today():
                        s["last_review"] = core.today()
                        core.save_settings(u["id"], s)
                        try:
                            daily_review(u)
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(60)


@app.on_event("startup")
def start_background():
    threading.Thread(target=background, daemon=True).start()
