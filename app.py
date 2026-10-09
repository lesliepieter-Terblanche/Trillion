"""Trillion: a local agent OS for one PC and one family. Run with Start-Trillion.bat."""
import io, json, re, time, threading, datetime, zipfile

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse

import core
import extend
import phone
import tools
import video

core.init()
extend.init()
video.init()
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


core.LLM = llm_chat


def system_prompt(user, text):
    s = core.settings(user)
    mem = tools.search_notes(user["id"], text, k=4)
    memory = "\n".join(f"- {m['title']}: {m['body'][:300]}" for m in mem) or "(nothing relevant saved yet)"
    skills = "\n".join(f"- {k['name']}: {k['description'][:160]}" for k in extend.enabled_skills(user["id"])) or "(none)"
    connectors = "\n".join(f"- {c['name']}: {(c['description'] or '')[:160]}"
                           for c in extend.enabled_connectors(user["id"])) or "(none)"
    return f"""You are {s['assistant_name']}, a private AI assistant running on the family's own PC.
You are talking to {user['name']}{' (the owner)' if user['is_admin'] else ''}. Their business: {s['brand'] or 'none set'} ({s['site'] or 'no site'}).
Today is {datetime.datetime.now().strftime('%A %d %B %Y, %H:%M')}. Their home folder is {core.user_home(user)}.

How to work:
- Use your tools to actually do things. Never say something was done unless a tool result confirms it.
- Deleting files and running commands need approval: the tool will report waiting_for_approval. Then tell the user it is waiting for them on the dashboard, and do not retry it.
- If a tool returns an error, say plainly what went wrong.
- You can update your own software from GitHub with update_self (owner only).
- When the user tells you a lasting fact or preference, save it with save_note.
- Always reply in English.
- Replies are spoken aloud: keep them short and natural, no markdown, no lists.

Relevant things you remember:
{memory}

Skills switched on for this user (call use_skill with the name before doing that kind of job):
{skills}

Connectors switched on for this user (call connector_tools first, then connector_call):
{connectors}"""


UPDATE_RE = re.compile(r"\b(update|upgrade)\s+(yourself|your ?self|herself|trillion|now)\b|"
                       r"\b(pull|get|install)\s+(the\s+)?((latest|newest)\s+(version|update)|update)\b|"
                       r"\bcheck for (an? )?updates?\b", re.I)


VIDEO_RE = re.compile(r"\b(create|make|generate|produce|build|render)\b[^.?!]{0,80}\bvideo\b", re.I)


def video_request(text):
    m = re.search(r"(\d{1,2})[\s-]*(seconds?|secs?|s)\b", text, re.I)
    low = text.lower()
    fmt = "landscape" if re.search(r"landscape|horizontal|widescreen|youtube video", low) else ("square" if "square" in low else "vertical")
    return {"prompt": text, "seconds": int(m.group(1)) if m else 20, "format": fmt,
            "voiceover": not re.search(r"no voice|without (a )?voice|no narration|silent", low)}


def video_reply(res):
    if "error" in res:
        return "I could not make the video: " + res["error"]
    extra = " ".join(res.get("notes", []))
    return (f"Your video is ready: {res['title']}, {round(res['seconds'])} seconds, "
            f"{'with' if res['voice_over'] else 'without'} voice-over and {res['footage_clips']} stock clips. "
            f"It is on the Videos page and saved at {res['saved']}. {extra}").strip()


def update_reply(res):
    if "error" in res:
        return "I could not update: " + res["error"]
    if res.get("status") == "already_up_to_date":
        return f"I am already on the newest version, {res['version']}."
    changes = "; ".join(res.get("what_changed", [])[:4])
    return f"Updated to version {res['version']}. What changed: {changes}. I am restarting now and will be back in a few seconds."


def run_agent(user, text):
    uid = user["id"]
    BUSY[uid] = True
    STOP.discard(uid)
    tid = core.add_task(uid, text)
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "user", text, time.time()))
    core.log(uid, f'Heard: "{text[:140]}"', "voice")
    if UPDATE_RE.search(text):  # never leave this one to the model's judgement
        res = tools.execute(user, "update_self", {}, task_id=tid)
        reply = update_reply(res)
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    if VIDEO_RE.search(text):  # a long job with fixed steps: run it directly
        try:
            res = tools.execute(user, "create_video", video_request(text), task_id=tid)
        finally:
            BUSY[uid] = False
        reply = video_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}
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
_whisper_lock = threading.Lock()
HEARING = {"state": "not loaded"}


def load_whisper():
    global _whisper
    with _whisper_lock:
        if _whisper is not None:
            return _whisper
        import os
        HEARING["state"] = "loading"
        try:
            from faster_whisper import WhisperModel
            dev = os.environ.get("TRILLION_WHISPER_DEVICE", "cpu")
            _whisper = WhisperModel(os.environ.get("TRILLION_WHISPER_MODEL", "small"), device=dev,
                                    compute_type="float16" if dev == "cuda" else "int8")
            HEARING["state"] = "ready"
        except ImportError:
            HEARING["state"] = "not installed"
            raise
        except Exception as e:
            HEARING["state"] = f"failed: {type(e).__name__}"
            raise
        return _whisper


def decode_audio(data):
    """Browser recording (webm/ogg/mp4) -> 16 kHz mono float samples. Done here rather than inside
    faster-whisper, whose own decoder breaks on some PyAV versions."""
    import numpy as np
    try:
        import av
        buf = []
        with av.open(io.BytesIO(data), mode="r") as c:
            rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in c.decode(audio=0):
                out = rs.resample(frame)
                for f in (out if isinstance(out, list) else [out]):
                    if f is not None:
                        buf.append(f.to_ndarray().reshape(-1))
            try:
                for f in (rs.resample(None) or []):
                    buf.append(f.to_ndarray().reshape(-1))
            except Exception:
                pass
        if buf:
            return np.concatenate(buf).astype(np.float32) / 32768.0
    except Exception:
        pass
    import os, subprocess, tempfile  # fallback: FFmpeg, installed by Setup
    with tempfile.NamedTemporaryFile(suffix=".rec", delete=False) as f:
        f.write(data)
        name = f.name
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", name, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                           capture_output=True, timeout=60)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError("could not decode the recording: " + r.stderr.decode(errors="replace")[-160:])
        return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        os.unlink(name)


def transcribe(data):
    w = load_whisper()
    audio = decode_audio(data)
    if audio.size < 4000:  # under a quarter of a second
        return ""
    segs, _ = w.transcribe(audio, vad_filter=True, language="en")
    text = " ".join(s.text.strip() for s in segs).strip()
    if not text:  # the silence filter can swallow quiet speech; try once without it
        segs, _ = w.transcribe(audio, vad_filter=False, language="en")
        text = " ".join(s.text.strip() for s in segs).strip()
    return text


@app.post("/api/stt")
async def stt(request: Request, user=Depends(auth)):
    data = await request.body()
    if len(data) < 2000:
        core.log(user["id"], f"Microphone: the recording was empty ({len(data)} bytes)", "waiting")
        return {"text": "", "why": "The recording was empty. Tap MIC, speak, then tap it again."}
    try:
        text = await run_in_threadpool(transcribe, data)
    except ImportError:
        raise HTTPException(501, "Local hearing is not installed. Run '1 - Setup.bat' again.")
    except Exception as e:
        core.log(user["id"], f"Microphone: could not read the recording ({type(e).__name__}: {e})"[:300], "waiting")
        raise HTTPException(500, f"Could not read the recording: {type(e).__name__}: {str(e)[:120]}")
    if not text:
        core.log(user["id"], f"Microphone: heard no words in {len(data) // 1024} KB of sound", "waiting")
        return {"text": "", "why": "I got the recording but heard no words. Speak closer to the microphone."}
    return {"text": text}


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
        "day_start": ds, "now": time.time(), "version": VERSION, "hearing": HEARING["state"],
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
EDITABLE = ["phone_country_code", "assistant_name", "brand", "accent", "site", "brain", "local_model", "cloud_model", "voice",
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
        elif name == "pexels":
            r = httpx.get("https://api.pexels.com/videos/search", params={"query": "city", "per_page": 1},
                          headers={"Authorization": key}, timeout=15)
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


# ---------- videos ----------
def _video(vid, user):
    v = core.q("SELECT * FROM videos WHERE id=? AND user_id=?", (vid, user["id"]), one=True)
    if not v:
        raise HTTPException(404, "No such video")
    return v


@app.get("/api/videos")
def videos_list(user=Depends(auth)):
    import os
    out = []
    for v in core.q("SELECT * FROM videos WHERE user_id=? ORDER BY id DESC LIMIT 60", (user["id"],)):
        d = dict(v)
        d["detail"] = json.loads(v["detail"] or "{}")
        d["exists"] = os.path.exists(v["path"])
        out.append(d)
    return out


@app.get("/api/videos/{vid}/file")
def videos_file(vid: int, user=Depends(auth)):
    v = _video(vid, user)
    return FileResponse(v["path"], media_type="video/mp4", filename=v["path"].replace("\\", "/").split("/")[-1])


@app.get("/api/videos/{vid}/thumb")
def videos_thumb(vid: int, user=Depends(auth)):
    v = _video(vid, user)
    if not v["thumb"]:
        raise HTTPException(404, "No picture")
    return FileResponse(v["thumb"], media_type="image/jpeg")


@app.delete("/api/videos/{vid}")
def videos_delete(vid: int, user=Depends(auth)):
    import os
    v = _video(vid, user)
    for f in (v["path"], v["thumb"]):
        try:
            if f:
                os.unlink(f)
        except OSError:
            pass
    core.x("DELETE FROM videos WHERE id=?", (vid,))
    return {"ok": True}


# ---------- each login's own phone ----------
@app.get("/api/phone")
def phone_info(user=Depends(auth)):
    s = core.settings(user)
    out = {"linked": s.get("phone_serial"), "model": s.get("phone_model", ""), "adb": True, "devices": []}
    try:
        for d in phone.devices():
            o = phone.owner_of(d["serial"])
            d["linked_to"] = o["name"] if o else None
            out["devices"].append(d)
    except phone.PhoneError as e:
        out["adb"], out["error"] = False, str(e)
    return out


@app.post("/api/phone/link")
def phone_link(p: dict = Body(...), user=Depends(auth)):
    serial = str(p.get("serial", ""))
    dev = next((d for d in phone.devices() if d["serial"] == serial), None)
    if not dev:
        raise HTTPException(404, "That phone is not connected right now")
    if dev["state"] != "device":
        raise HTTPException(400, "Unlock the phone and tap Allow on the USB debugging prompt, then try again")
    o = phone.owner_of(serial)
    if o and o["id"] != user["id"]:
        raise HTTPException(403, f"That phone is already linked to {o['name']}")
    s = core.settings(user)
    s["phone_serial"], s["phone_model"] = serial, dev["model"]
    core.save_settings(user["id"], s)
    core.log(user["id"], f"Phone linked: {dev['model'] or serial}", "done")
    return {"ok": True}


@app.post("/api/phone/wireless")
def phone_wireless(p: dict = Body(...), user=Depends(auth)):
    try:
        target = phone.go_wireless(user, str(p.get("address", "")))
    except phone.PhoneError as e:
        raise HTTPException(400, str(e))
    s = core.settings(user)
    s["phone_serial"] = target
    core.save_settings(user["id"], s)
    return {"ok": True, "address": target}


@app.post("/api/phone/unlink")
def phone_unlink(user=Depends(auth)):
    s = core.settings(user)
    s.pop("phone_serial", None)
    s.pop("phone_model", None)
    core.save_settings(user["id"], s)
    return {"ok": True}


@app.post("/api/phone/test")
def phone_test(user=Depends(auth)):
    try:
        return {"ok": True, "status": phone.status(user)}
    except phone.PhoneError as e:
        return {"ok": False, "error": str(e)}


# ---------- skills ----------
@app.get("/api/skills")
def skills_list(user=Depends(auth)):
    return extend.list_skills(user["id"])


@app.post("/api/skills/preview")
def skills_preview(p: dict = Body(...), user=Depends(admin)):
    try:
        sk = extend.fetch_skill(p.get("url", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": sk["name"], "description": sk["description"], "excerpt": sk["body"][:1800],
            "length": len(sk["body"]), "has_scripts": sk["has_scripts"], "raw_url": sk["raw_url"]}


@app.post("/api/skills/install")
def skills_install(p: dict = Body(...), user=Depends(admin)):
    try:
        sk = extend.fetch_skill(p.get("url", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    slug = extend.save_skill(sk["name"], sk["description"], sk["body"], sk["source"], owner_id=user["id"])
    core.log(user["id"], f"Skill installed: {sk['name']}", "done")
    return {"slug": slug, "name": sk["name"]}


@app.post("/api/skills/{slug}/toggle")
def skills_toggle(slug: str, p: dict = Body(...), user=Depends(auth)):
    if not core.q("SELECT 1 FROM skills WHERE slug=? AND status='active'", (slug,)):
        raise HTTPException(404, "No such skill")
    extend.set_skill_enabled(user["id"], slug, bool(p.get("enabled")))
    return {"ok": True}


@app.post("/api/skills/{slug}/approve")
def skills_approve(slug: str, user=Depends(admin)):
    core.x("UPDATE skills SET status='active' WHERE slug=?", (slug,))
    extend.set_skill_enabled(user["id"], slug, True)
    return {"ok": True}


@app.get("/api/skills/{slug}")
def skills_read(slug: str, user=Depends(auth)):
    body = extend.skill_body(slug)
    if body is None:
        raise HTTPException(404, "No such skill")
    return {"body": body}


@app.delete("/api/skills/{slug}")
def skills_delete(slug: str, user=Depends(admin)):
    extend.remove_skill(slug)
    return {"ok": True}


# ---------- connectors ----------
@app.get("/api/connectors")
def connectors_list(user=Depends(auth)):
    out = {"connectors": extend.list_connectors(user)}
    if user["is_admin"]:
        have = {c["name"] for c in out["connectors"]}
        out["catalog"] = [c for c in extend.CATALOG if c["name"] not in have]
        out["people"] = [{"id": u["id"], "name": u["name"]} for u in core.q("SELECT id, name FROM users ORDER BY id")]
    return out


@app.post("/api/connectors")
def connectors_add(p: dict = Body(...), user=Depends(admin)):
    cat = next((c for c in extend.CATALOG if c["key"] == p.get("catalog")), None)
    try:
        if cat:
            cid = extend.add_connector(cat["name"], cat["description"], cat["command"], user["id"])
        else:
            cid = extend.add_connector(p.get("name", ""), p.get("description", ""), p.get("command", ""), user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "A connector with that name already exists.")
    core.log(user["id"], f"Connector added: {cat['name'] if cat else p.get('name')}", "done")
    return {"id": cid}


@app.post("/api/connectors/{cid}/test")
def connectors_test(cid: int, user=Depends(admin)):
    row = core.q("SELECT * FROM connectors WHERE id=?", (cid,), one=True)
    if not row:
        raise HTTPException(404, "No such connector")
    try:
        t = extend.client(row).tools()
        return {"ok": True, "tools": [x.get("name") for x in t]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


@app.post("/api/connectors/{cid}/access")
def connectors_access(cid: int, p: dict = Body(...), user=Depends(admin)):
    uid = int(p.get("user_id", 0))
    if p.get("allowed"):
        core.x("INSERT OR IGNORE INTO connector_access(connector_id,user_id,enabled) VALUES(?,?,1)", (cid, uid))
    else:
        core.x("DELETE FROM connector_access WHERE connector_id=? AND user_id=?", (cid, uid))
    return {"ok": True}


@app.post("/api/connectors/{cid}/toggle")
def connectors_toggle(cid: int, p: dict = Body(...), user=Depends(auth)):
    if not core.q("SELECT 1 FROM connector_access WHERE connector_id=? AND user_id=?", (cid, user["id"])):
        raise HTTPException(403, "The owner has not given this login access to that connector")
    core.x("UPDATE connector_access SET enabled=? WHERE connector_id=? AND user_id=?",
           (1 if p.get("enabled") else 0, cid, user["id"]))
    return {"ok": True}


@app.delete("/api/connectors/{cid}")
def connectors_delete(cid: int, user=Depends(admin)):
    extend.remove_connector(cid)
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


@app.post("/api/admin/update")
def admin_update(user=Depends(admin)):
    res = tools.execute(user, "update_self", {})
    return dict(res, message=update_reply(res))


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

    def warm():  # load the hearing model up front so the first spoken command is not slow
        try:
            load_whisper()
        except Exception:
            pass
    threading.Thread(target=warm, daemon=True).start()
