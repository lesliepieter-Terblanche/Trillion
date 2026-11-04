"""Trillion: a local agent OS for one PC and one family. Run with Start-Trillion.bat."""
import io, json, os, re, time, threading, datetime, zipfile
from urllib.parse import urlparse as urlparse_

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse, StreamingResponse

import core
import extend
import phone
import routines
import tools
import video
import attach
import selfcheck

core.init()
for _u in core.q("SELECT * FROM users WHERE is_admin=1"):   # the owner asked for this; the other logins choose for themselves
    try:
        _raw = json.loads(_u["settings"] or "{}")
    except Exception:
        _raw = {}
    if not _raw.get("wa_auto_set"):
        _raw["whatsapp_auto_contacts"] = _raw["wa_auto_set"] = True
        core.save_settings(_u["id"], _raw)
# 0.7.18: Brandon's own health tracker sits on his home screen. Only the address is set here; its login code is typed
# once into his own login and kept encrypted on this PC, never in the program's files.
for _u in core.q("SELECT * FROM users WHERE lower(name)='brandon'"):
    try:
        _raw = json.loads(_u["settings"] or "{}")
    except Exception:
        _raw = {}
    if not _raw.get("links_health_set"):
        _raw["links"] = [l for l in (_raw.get("links") or []) if l.get("id") != "health"] + [
            {"id": "health", "name": "Health tracker", "url": "https://vapeessence.co.za/health-tracker"}]
        _raw["links_health_set"] = True
        core.save_settings(_u["id"], _raw)
# 0.7.25: the Gumroad store belongs to Brandon's login. The owner can switch it on for another login on the Family page.
for _u in core.q("SELECT * FROM users"):
    try:
        _raw = json.loads(_u["settings"] or "{}")
    except Exception:
        _raw = {}
    if not _raw.get("store_set"):
        _raw["store_on"], _raw["store_set"] = _u["name"].lower() == "brandon", True
        core.save_settings(_u["id"], _raw)
extend.init()
import cameras
cameras.init()
import bible
bible.init()
video.init()
attach.init()
import gumroad
import watch
import siteread
import siteclone
import apps
import storage
import backup
import meeting
import nudge
import morning
import scrape
import evolve
import voicecall
import web
import mail
import leads
import maps
import manuals
gumroad.init()
manuals.init()
routines.init()
web.init()
leads.init()
# 0.7.28: Bible study belongs to Lewellyn's login. His morning and evening routines read the day's verse aloud, a different
# one each. The owner can switch Bible study on for another login on the Family page.
for _u in core.q("SELECT * FROM users"):
    try:
        _raw = json.loads(_u["settings"] or "{}")
    except Exception:
        _raw = {}
    if not _raw.get("bible_set"):
        _raw["bible_on"], _raw["bible_set"] = _u["name"].lower() in ("lewellyn", "llewellyn"), True
        core.save_settings(_u["id"], _raw)
        if _raw["bible_on"]:
            _lu = core.q("SELECT * FROM users WHERE id=?", (_u["id"],), one=True)
            for _r in routines.list_for(_lu):
                _which = "morning" if "morning" in _r["name"].lower() else "night" if re.search(r"evening|night", _r["name"], re.I) else None
                if _which:
                    _secs = [dict(x, on=True, which=_which) if x["type"] == "verse" else x for x in _r["sections"]]
                    _secs.sort(key=lambda x: x["type"] != "verse")            # the verse comes first
                    core.x("UPDATE routines SET sections=? WHERE id=?", (json.dumps(_secs), _r["id"]))
app = FastAPI(title="Trillion")
COOKIE = "trillion"
BUSY, STOP, SITES, LAST_PROVIDER, LAST_MODEL = {}, set(), {}, {}, {}
VERSION = tools.version()
tools.RUNNING = VERSION

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
LIVE = {}   # uid -> the answer as far as it has been written, so the page can show it while she is still writing


def llm_chat(user, messages, tool_specs=None, fast=False, live=False):
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
            LAST_MODEL[uid] = f"{brain}: {s.get('cloud_model') or 'gemini-2.5-flash'}"
            return r.json()["choices"][0]["message"]
        except Exception as e:
            core.log(uid, f"Cloud brain ({brain}) failed, used the local one instead: {type(e).__name__}", "waiting")
    model = core.pick_local_model(s.get("local_model", "qwen2.5:14b"))
    if fast:   # a plain question needs no tools: the small model answers several times faster
        small = core.pick_local_model(s.get("fast_model") or "qwen2.5:3b")
        if small.split(":")[0] in ("qwen2.5", "gemma3", "llama3.2", "phi4-mini") and small != model:
            model = small
    elif s.get("heavy_answers", True) and _wants_heavy(messages):
        # a hard job (code, deep reasoning) goes to the bigger local brain, but only if it is actually installed
        hv = s.get("heavy_model") or "mistral-small:24b"
        if core.has_model(hv) and hv.split(":")[0] != model.split(":")[0]:
            model = core.pick_local_model(hv)
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
    LAST_MODEL[uid] = model
    LAST_PROVIDER[uid] = "local"
    req = {"model": model, "messages": native, "stream": True, "keep_alive": "4h",
           "options": {"num_ctx": 6144 if fast else int(s.get("context_size", 12288)), "temperature": 0.2}}
    if tool_specs:
        req["tools"] = tool_specs
    content, calls = "", []
    try:
        with httpx.stream("POST", core.OLLAMA + "/api/chat", timeout=600, json=req) as r:
            if r.status_code == 400:
                body_text = r.read().decode(errors="replace")
                if "does not support tools" in body_text:
                    raise RuntimeError(f"The model {model} cannot use tools. Set the local model to qwen2.5:14b in Settings.")
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                msg = j.get("message") or {}
                if msg.get("tool_calls"):
                    calls += msg["tool_calls"]
                if msg.get("content"):
                    content += msg["content"]
                    if live and not calls:
                        LIVE[uid] = content
                if uid in STOP:
                    break
                if j.get("done"):
                    break
    except httpx.ConnectError:
        raise RuntimeError("The local brain (Ollama) is not running. Start Ollama and try again.")
    LAST_PROVIDER[uid] = model
    out = {"role": "assistant", "content": content}
    if calls:
        out["tool_calls"] = calls
    return out


core.LLM = llm_chat

PERSONAS = {
    "professional": ("Professional", "Calm, precise and efficient. Get to the point, no small talk, no jokes. "
                     "Sound like a capable chief of staff."),
    "warm": ("Warm", "Friendly, upbeat and encouraging. Use the user's first name now and then, celebrate small wins, "
             "and keep things light without being silly. Never swear."),
    "witty": ("Witty", "Sharp-tongued, funny and irreverent, like a mate who happens to be brilliant. Crack jokes, tease "
              "the user a little, use dry sarcasm, and swear freely and casually (shit, damn, bloody, fuck and the like) "
              "the way friends do. Never aim insults at who someone is: no slurs, nothing about race, religion, sex or "
              "disability. The humour never gets in the way of doing the job properly, and you still report results and "
              "errors accurately."),
}
PERSONA_RULE = ("This personality is only for how you talk to the user in chat. Anything you write for someone else, such as "
                "a WhatsApp message, an email, a document, a note or a video script, stays clean and professional unless "
                "the user explicitly asks for another tone.")
PERSONA_RE = re.compile(r"\b(?:switch|change|go|turn)\b.{0,25}\b(professional|warm|friendly|witty|funny|sarcastic|serious)\b"
                        r"|\b(?:be|use)\b.{0,12}\b(professional|warm|friendly|witty|funny|sarcastic|serious)\b.{0,12}\b(?:personality|mode|voice|style)\b"
                        r"|^\s*be\s+(professional|warm|friendly|witty|funny|sarcastic|serious)[.!]?\s*$", re.I)
PERSONA_WORDS = {"professional": "professional", "serious": "professional", "warm": "warm", "friendly": "warm",
                 "witty": "witty", "funny": "witty", "sarcastic": "witty"}
PERSONA_HELLO = {"professional": "Understood. Professional mode from here on.",
                 "warm": "Lovely, warm mode it is. Happy to be here with you!",
                 "witty": "Right, gloves off. Witty mode it is, so try to keep up, and don't blame me when it gets bloody honest."}



def _jobs_block(uid):
    ds = core.day_start()
    running = core.q("SELECT title, status, detail, created FROM tasks WHERE user_id=? AND status IN "
                     "('in_progress','waiting') ORDER BY id DESC LIMIT 8", (uid,))
    done_today = core.q("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND status='completed' AND updated>=?",
                        (uid, ds), one=True)["c"]
    recent = core.q("SELECT title FROM tasks WHERE user_id=? AND status='completed' AND updated>=? ORDER BY id DESC LIMIT 6",
                    (uid, ds))

    def _ago(c):
        mm = int((time.time() - c) // 60)
        return f"{mm} min" if mm < 90 else f"{mm // 60} h"
    if running:
        lines = "\n".join(f"- {r['title']} — {r['detail'] or 'working on it'} (started {_ago(r['created'])} ago)"
                          + (" [waiting for your OK on the dashboard]" if r["status"] == "waiting" else "")
                          for r in running)
    else:
        lines = "- Nothing is running right now."
    recent_line = ", ".join(r["title"] for r in recent) if recent else "nothing yet"
    return f"{len(running)} job(s) running, {done_today} finished today.\n{lines}\nFinished today: {recent_line}."


def system_prompt(user, text):
    s = core.settings(user)
    mem = tools.search_notes(user["id"], text, k=4)
    memory = "\n".join(f"- {m['title']}: {m['body'][:300]}" for m in mem) or "(nothing relevant saved yet)"
    skills = "\n".join(f"- {k['name']}: {k['description'][:160]}" for k in extend.enabled_skills(user["id"])) or "(none)"
    connectors = "\n".join(f"- {c['name']}: {(c['description'] or '')[:160]}"
                           for c in extend.enabled_connectors(user["id"])) or "(none)"
    return f"""LANGUAGE: Always write every reply in English only. Never use Thai, Chinese, Afrikaans or any other
language, whatever language appears in tool results, device names, web pages or the user's text. English, always.

You are {s['assistant_name']}, a private AI assistant running on the family's own PC.
You are talking to {user['name']}{' (the owner)' if user['is_admin'] else ''}. Their business: {s['brand'] or 'none set'} ({s['site'] or 'no site'}).
Today is {datetime.datetime.now().strftime('%A %d %B %Y, %H:%M')}. Their home folder is {core.user_home(user)}.

How to work:
- Use your tools to actually do things. Never say something was done unless a tool result confirms it.
- To get a go-ahead, call the tool - it creates the approval card by itself. Never ask "ready to proceed?" in words and wait; just call the tool.
- Deleting files and running commands need approval: the tool will report waiting_for_approval. Then tell the user it is waiting for them on the dashboard, and do not retry it.
- To reverse-engineer an app on the phone, call rea_phone_app with the app's name - it pulls the APK and hands it to Claude Code. To see what servers an app talks to, call rea_phone_traffic. Do not take screenshots for either, and do not ask the user to approve first; they run on their own.
- If a tool returns an error, say plainly what went wrong.
- You can update your own software from GitHub with update_self (owner only).
- On the user's phone, never confirm a payment or purchase, delete anything, or send a message by tapping unless the user asked for exactly that in this request.
- When the user tells you a lasting fact or preference, save it with save_note.
- When the user asks what you are working on, how a project or job is going, for progress, an update or feedback on one, answer from "What you are working on right now" below. Never say you don't know about your own projects - that list is the truth.
- Reply in English only — every word, every time (see the LANGUAGE rule at the top). Device names or text in another language stay as-is, but your own sentences are always English.
- Replies are spoken aloud: keep them short and natural, no markdown, no lists.

Your personality: {PERSONAS.get(s.get('personality'), PERSONAS['professional'])[1]}
{PERSONA_RULE}

What you are working on right now (use this to answer anything about your projects, progress or feedback):
{_jobs_block(user['id'])}

Relevant things you remember:
{memory}

Skills switched on for this user (call use_skill with the name before doing that kind of job):
{skills}

Connectors switched on for this user (call connector_tools first, then connector_call):
{connectors}

Routines this user has set up (each runs by itself when they say its phrase; they are edited on the Routines page):
{routines.prompt_line(user)}

Workshop manuals you have read in full and can search page by page (search_manual, then read_manual_page for a whole page;
add_manual reads in a new PDF; split_manual saves one PDF per section). For any question about the vehicle, answer from
the manual, say the section and page, give the manual's own fix steps and cautions, and give the page link so they can
see the picture. Never make up a step, a torque value or a part location that the manual does not give:
{manuals.prompt_line(user)}"""


UPDATE_RE = re.compile(r"\b(update|upgrade)\s+(yourself|your ?self|herself|trillion|now)\b|"
                       r"\bup[dg][a-z]{2,5}\s+(yourself|your ?self|ur ?self|herself)\b|"   # typing slips: updste, updaet
                       r"\bupadte\s+(yourself|your ?self)\b|"
                       r"\b(pull|get|install)\s+(the\s+)?((latest|newest)\s+(version|update)|update)\b|"
                       r"\bcheck for (an? )?updates?\b", re.I)


THEMES = {"night": "#ffb454", "day": "#b45309", "ocean": "#38bdf8", "forest": "#86efac", "royal": "#c084fc"}
WAKE_RE = re.compile(r"\bwake ?word\b.{0,20}\b(on|off)\b|\b(?:turn|switch|enable|disable|activate|use|stop)\b.{0,25}\bwake ?word\b", re.I)
THEME_RE = re.compile(r"\b(?:switch|change|go|turn|set|use|put)\b.{0,30}\b(night|dark|day|light|ocean|forest|royal)\b.{0,12}\b(?:mode|theme|colou?rs?)\b|"
                      r"^\s*(night|dark|day|light|ocean|forest|royal)\s+(?:mode|theme)[.!]?\s*$", re.I)
VERSION_RE = re.compile(r"\b(?:what|which)\s+version\b|\bversion\s+(?:are|is|do)\b|\byour\s+version\b", re.I)
_PH = r"(?:my |the )?(?:cell ?phone|mobile(?: phone)?|phone|cell|galaxy|samsung)"
_WA = r"what'?s ?(?:app|up)"
_OBJ = (r"(?:this|these|that|those|it|them|(?:(?:the|my|this|these|that|those)\s+)?(?:\w+\s+){0,2}?(?:files?|photos?|pictures?|pics?|images?|"
        r"documents?|docs?|videos?|clips?|pdfs?|attachments?|ebooks?|books?|recordings?|voice notes?))")
_SAY = r"(?:\s*(?:,?\s*saying|that says|to say|with the message|with the caption|:)\s+(.+?))?[.!?]?\s*$"
WA_WITH = re.compile(r"\s+(?:with|and|plus)\s+(?:(?:the|this|these|my|an?)\s+)?(?:attached\s+)?" + _OBJ.replace("this|these|that|those|it|them|", "attached|attachment|", 1)
                     + r"(?:\s+attached)?\s*$", re.I)
PHONE_RULES = [
    # a file was attached and should go out on WhatsApp: "send this to Brandon on WhatsApp", "WhatsApp these to Sarie saying ..."
    ("whatsapp_file", re.compile(r"^\s*(?:(?:hey\s+|ok\s+)?\w+,\s+)?(?:please\s+|can you\s+|could you\s+|will you\s+)*(?:send|forward|share|" + _WA + r")\s+" + _OBJ + r"\s+to\s+(.+?)\s+(?:on|via|by|over|using|with)\s+" + _WA + r"(?:\s+message)?" + _SAY, re.I | re.S)),
    ("whatsapp_file", re.compile(r"^\s*(?:(?:hey\s+|ok\s+)?\w+,\s+)?(?:please\s+|can you\s+|could you\s+|will you\s+)*" + _WA + r"\s+" + _OBJ + r"\s+to\s+(.+?)" + _SAY, re.I | re.S)),
    ("whatsapp_file", re.compile(r"^\s*(?:(?:hey\s+|ok\s+)?\w+,\s+)?(?:please\s+|can you\s+|could you\s+|will you\s+)*(?:send|forward|" + _WA + r")\s+(.{2,60}?)\s+" + _OBJ + r"\s+(?:on|via|by|over|using|with)\s+" + _WA + _SAY, re.I | re.S)),
    ("whatsapp_file", re.compile(r"^\s*(?:(?:hey\s+|ok\s+)?\w+,\s+)?(?:please\s+|can you\s+|could you\s+|will you\s+)*" + _WA + r"\s+(me|myself)\s+" + _OBJ + _SAY, re.I | re.S)),
    ("whatsapp_file", re.compile(r"^\s*(?:send\s+(?:an?\s+)?)?" + _WA + r"\s+(?:to\s+)?(?!me\b|myself\b|this\b|these\b|it\b|them\b)([^,.!?:]{2,40}?)[.!?]?\s*$", re.I)),
    ("mynumber", re.compile(r"\bmy\s+(?:own\s+)?(?:%s|cell ?phone|cell|mobile|phone)\s+number\s+is\s+([+\d][\d\s()-]{6,})" % r"what'?s ?(?:app|up)", re.I)),
    ("whatsapp_me", re.compile(r"\b(?:send|whats ?app|text|message)\s+(?:me|myself)\s+(?:an?\s+)?(?:quick\s+|test\s+)*(?:%s|message|text)?(?:\s+(?:message|text))?"
                               r"(?:\s+on\s+%s)?\s*(?:(?:,?\s*saying|that says|to say|:)\s+(.+))?[.!?]?\s*$" % (r"what'?s ?(?:app|up)", r"what'?s ?(?:app|up)"), re.I | re.S)),
    ("whatsapp", re.compile(r"\bwhat'?s ?(?:app|up)\b.{0,20}?\bsend\s+(.+?)\s+(?:a\s+)?(?:message|text)\s*(?:saying|that says|to say|:|\bthat\b)\s+(.+)$", re.I | re.S)),
    ("whatsapp", re.compile(r"\b(?:send\s+)?(?:a\s+)?what'?s ?(?:app|up)\s+(?:message\s+)?(?:to\s+)?(.+?)\s*(?:,?\s*saying|that says|to say|:|\bthat\b)\s+(.+)$", re.I | re.S)),
    ("whatsapp", re.compile(r"\bsend\s+(.+?)\s+a\s+what'?s ?(?:app|up)(?:\s+message)?\s*(?:saying|that says|to say|:|\bthat\b)\s+(.+)$", re.I | re.S)),
    ("whatsapp", re.compile(r"\b(?:tell|message|text)\s+(.+?)\s+on\s+what'?s ?(?:app|up)\s*(?:saying|that|:)?\s+(.+)$", re.I | re.S)),
    # a WhatsApp with no wording given: "send a test whatsapp to Sarie", "send Sarie a test whatsapp", "whatsapp Sarie"
    ("whatsapp_plain", re.compile(r"\b(?:send\s+)?(?:an?\s+)?(?:quick\s+|test\s+)*what'?s ?(?:app|up)(?:\s+message)?\s+to\s+(?!me\b|myself\b)([^,.!?:]{2,60}?)[.!?]?\s*$", re.I)),
    ("whatsapp_plain", re.compile(r"\bsend\s+(?!me\b|myself\b)([^,.!?:]{2,60}?)\s+an?\s+(?:quick\s+|test\s+)*what'?s ?(?:app|up)(?:\s+message)?[.!?]?\s*$", re.I)),
    ("whatsapp_plain", re.compile(r"\bsend\s+an?\s+(?:quick\s+)?test\s+(?:message\s+)?to\s+(?!me\b|myself\b)([^,.!?:]{2,60}?)\s+on\s+what'?s ?(?:app|up)[.!?]?\s*$", re.I)),
    ("open", re.compile(r"\b(?:open|launch|start)\s+(?:up\s+)?(?:my\s+|the\s+)?(.+?)\s+(?:app\s+)?on\s+" + _PH, re.I)),
    # "open my WhatsApp" with no device named: these apps live on the phone
    ("open", re.compile(r"\b(?:open|launch|start)\s+(?:up\s+)?(?:my\s+|the\s+)?(what'?s ?(?:app|up)(?: business)?|instagram|tiktok|telegram|"
                        r"facebook|messenger|messages|gallery|camera|contacts|youtube|spotify|gmail|maps|chrome|play store|tailscale)"
                        r"(?:\s+app)?\s*(?:for me|please|now|,?\s*\w+)?[.!?]?\s*$", re.I)),
    ("wa_read", re.compile(r"\b(?:read|check|show|any|what(?:'s| are| is))\b.{0,20}\b(?:new\s+|unread\s+|incoming\s+)?%s(?:\s*(?:messages?|msgs?|s|es))?\b"
                           % r"what'?s ?(?:app|up)", re.I)),
    ("unlock", re.compile(r"\bunlock\s+" + _PH + r"\b", re.I)),
    ("simple:sleep", re.compile(r"\b(?:put|send|set)\b.{0,10}" + _PH + r".{0,10}\b(?:to sleep|sleep mode|asleep)\b|\b(?:lock|sleep)\s+" + _PH + r"\b|"
                                + _PH + r"\s+(?:to\s+)?sleep(?: mode)?\b|\bturn\s+(?:off\s+)?" + _PH + r"(?:'s)?\s+screen\s+off\b|\bturn off\s+" + _PH + r"(?:'s)?\s+screen\b", re.I)),
    ("simple:wake", re.compile(r"\bwake(?:\s+up)?\s+" + _PH + r"\b|\bturn on\s+" + _PH + r"(?:'s)?\s+screen\b", re.I)),
    ("simple:dnd_off", re.compile(r"\b(?:do not disturb|dnd)\b.{0,25}\boff\b|\b(?:turn|switch)\s+off\b.{0,25}\b(?:do not disturb|dnd)\b", re.I)),
    ("simple:dnd_on", re.compile(r"\b(?:turn|switch|put|set)\b.{0,25}\b(?:do not disturb|dnd)\b|\b(?:do not disturb|dnd)\s+(?:mode\s+)?on\b|"
                                 r"^\s*(?:do not disturb|dnd)(?: mode)?[.!]?\s*$", re.I)),
    ("simple:unmute", re.compile(r"\bunmute\s+" + _PH + r"\b|\b(?:turn|switch)\b.{0,8}" + _PH + r"(?:'s)?\s+(?:sound|ringer)\s+(?:back\s+)?on\b|\btake\s+" + _PH + r"\s+off silent\b", re.I)),
    ("simple:mute", re.compile(r"\b(?:mute|silence)\s+" + _PH + r"\b|\b(?:put|set)\s+" + _PH + r"\s+(?:on|to)\s+silent\b", re.I)),
    ("simple:next", re.compile(r"\b(?:next|skip)\s+(?:song|track)\b.{0,12}" + _PH, re.I)),
    ("simple:previous", re.compile(r"\bprevious\s+(?:song|track)\b.{0,12}" + _PH, re.I)),
    ("simple:playpause", re.compile(r"\b(?:play|pause|resume|stop)\s+(?:the\s+)?(?:music|song|audio)\s+on\s+" + _PH, re.I)),
    ("screen", re.compile(r"\bwhat(?:'s| is)\s+(?:showing\s+)?on\s+" + _PH + r"(?:\s+screen)?|\bread\s+" + _PH + r"(?:'s)?\s+screen", re.I)),
    ("press", re.compile(r"\b(?:press|go|hit)\s+(?:the\s+)?(back|home|recents|enter|lock|wake|play|pause|next|previous|"
                         r"volume up|volume down|mute)(?:\s+(?:button|key))?\s+on\s+" + _PH, re.I)),
    ("swipe", re.compile(r"\b(?:swipe|scroll)\s+(up|down|left|right)\s+on\s+" + _PH, re.I)),
    ("tap", re.compile(r"\b(?:tap|click|press)\s+(?:on\s+)?(?:the\s+)?[\"\u201c']?(.+?)[\"\u201d']?\s+(?:button\s+)?on\s+" + _PH, re.I)),
    ("type", re.compile(r"\btype\s+[\"\u201c']?(.+?)[\"\u201d']?\s+on\s+" + _PH, re.I)),
    ("screenshot", re.compile(r"\bscreenshot\b.{0,30}" + _PH + "|" + _PH + r".{0,30}\bscreenshot\b", re.I)),
    ("ring", re.compile(r"\b(?:ring|buzz|make .{0,10}noise)\b.{0,20}" + _PH, re.I)),
    ("locate", re.compile(r"\b(?:where(?:'s| is)|find|locate|track)\b.{0,15}" + _PH, re.I)),
    ("status", re.compile(_PH + r".{0,40}\b(?:battery|status|storage|charge|charging|space|stats)\b|"
                          r"\b(?:battery|status|storage|charge|stats)\b.{0,30}" + _PH + r"|\bhow(?:'s| is)\s+" + _PH, re.I)),
]
PHONE_WORDS = re.compile(r"\b(phone|cell|mobile|what'?s ?(?:app|up)|contact|battery|galaxy|samsung)\b", re.I)
# the model falsely claiming it put something up for approval (not merely explaining that approval is needed)
CLAIMS_PENDING = re.compile(r"\b(waiting for (?:your |my )?(?:ok|approval)|pending (?:your )?approval|"
                            r"(?:sent|queued|submitted|put) (?:it |this )?(?:up )?for (?:your )?approval|"
                            r"awaiting (?:your )?approval|approve it (?:on|in|from|via)|for your approval now)\b", re.I)
SELFCHECK_RE = re.compile(r"\b(?:self[-\s]?check|health check|system check|run a check|are you (?:ok|working|healthy|production[-\s]?ready)|"
                          r"is everything (?:ok|working)|check (?:yourself|your systems|everything is working))\b", re.I)
# "install the smart/bigger model" - pull the heavy local brain on demand
PULL_HEAVY_RE = re.compile(r"\b(?:install|get|download|pull|set ?up|add)\b.{0,25}\b(?:smart(?:er)?|bigger|heav(?:y|ier)|mistral|24b)\b"
                           r"(?:.{0,15}\b(?:model|brain)\b)?", re.I)
# "which skills/connectors aren't being used" - the usage audit
USAGE_RE = re.compile(r"\b(?:which|what|show|list)\b.{0,40}\b(?:skills?|connectors?|connections?)\b.{0,30}"
                      r"\b(?:not|never|un)?\s?(?:used|using|unused|in use)\b"
                      r"|\b(?:unused|idle)\b.{0,15}\b(?:skills?|connectors?|connections?)\b"
                      r"|\b(?:skill|connector|connection)\s+usage\b", re.I)
MORNING_RE = re.compile(r"\b(?:morning|daily|today'?s)\s+(?:brief(?:ing)?|rundown|summary|update)\b"
                        r"|\bbrief\s+me\b|\bwhat'?s?\s+(?:my|on my)\s+(?:day|plate)\b", re.I)
SCRAPE_RE = re.compile(r"\bscrape\b|\b(?:pull|grab|extract|harvest)\b.{0,50}\b(?:from|off)\b.{0,30}https?://"
                       r"|\bget\b.{0,40}\bfrom (?:this|the) (?:page|site|url|website)\b", re.I)
PROJECTS_RE = re.compile(r"\bwhat(?:'?s| is| are)?\s+(?:you\s+)?(?:working on|running)\b"
                         r"|\bwhat are you (?:doing|busy with)\b"
                         r"|\b(?:my|the|any|your|current(?:ly)?)\s+(?:running\s+)?(?:projects?|jobs?)\b"
                         r"|\bproject\s+(?:status|progress|update|feedback|list)\b"
                         r"|\b(?:list|show)\b.{0,15}\b(?:projects?|jobs?)\b"
                         r"|\brecall\b.{0,20}\bprojects?\b|\bany\s+(?:projects?|jobs?)\s+running\b", re.I)
SCRAPE_URL_RE = re.compile(r"https?://[^\s,]+", re.I)
PENDING_SCRAPE = {}          # uid -> {prompt, urls, mode, ts}


def _scrape_mode_pick(text):
    t = (text or "").strip().lower()
    if re.fullmatch(r"\s*3\s*", t) or re.search(r"\bseveral\b|\bmultiple\b|\bmany\b|\bmulti\b|\bthese pages\b|\bfew pages\b", t):
        return "multi"
    if re.fullmatch(r"\s*2\s*", t) or re.search(r"\bsearch\b|\bweb\b|\bgoogle\b|\btop results?\b", t):
        return "search"
    if re.fullmatch(r"\s*1\s*", t) or re.search(r"\bone page\b|\bthis (?:one )?page\b|\bsingle page\b|\bjust (?:this|one) page\b|\bpage\b", t):
        return "page"
    return None


def _scrape_prompt(text):
    """What to pull - the message minus the trigger words and any URLs."""
    t = SCRAPE_URL_RE.sub("", text or "")
    t = re.sub(r"\b(?:please|can you|could you|scrape|pull|grab|extract|harvest|get)\b", " ", t, flags=re.I)
    t = re.sub(r"\bfrom (?:this|the)? ?(?:page|site|url|website)?\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .,:-")
    return re.sub(r"\s+\b(?:from|off|at|on|in|of)\s*$", "", t, flags=re.I).strip(" .,:-")


def _builtin_reply(uid, tid, reply, detail=""):
    BUSY[uid] = False
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
    core.set_task(tid, status="completed", detail=(detail or reply)[:200])
    return {"reply": reply, "task_id": tid, "provider": "built-in"}


def _chat_reply(uid, tid, reply):
    """For a built-in that just answers a question (version, what's-outstanding, a report): it's a chat,
    not a project, so drop the task row it was given."""
    BUSY[uid] = False
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
    core.x("DELETE FROM tasks WHERE id=?", (tid,))
    core.x("DELETE FROM steps WHERE task_id=?", (tid,))
    return {"reply": reply, "task_id": None, "provider": "built-in"}


def _scrape_table(data):
    """Render a scrape result as a markdown table when it's row-like, else pretty JSON."""
    rows = None
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        rows = data
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                rows = v
                break
    if rows:
        cols = []
        for r in rows[:30]:
            for k in r:
                if k not in cols:
                    cols.append(str(k))
        cols = cols[:8]
        out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
        for r in rows[:30]:
            out.append("| " + " | ".join(str(r.get(c, ""))[:60].replace("\n", " ").replace("|", "/") for c in cols) + " |")
        if len(rows) > 30:
            out.append(f"...and {len(rows) - 30} more rows")
        return "\n".join(out)
    try:
        return "```\n" + json.dumps(data, indent=2, default=str)[:2500] + "\n```"
    except Exception:
        return str(data)[:2000]


def scrape_in_background(user, mode, prompt, source):
    def done(res):
        if res.get("ok"):
            msg = "Here's what I scraped:\n\n" + _scrape_table(res["data"])
        else:
            msg = "The scrape didn't work: " + str(res.get("error", "unknown"))
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant", msg, time.time()))
        try:
            watch.send(user, "Scrape done" if res.get("ok") else "Scrape failed", msg[:120].replace("\n", " "))
        except Exception:
            pass
    scrape.run_async(mode, prompt, source, done)


def _scrape_launch_or_ask(user, uid, tid, pend):
    mode = pend["mode"]
    prompt = pend.get("prompt") or ""
    urls = pend.get("urls") or []
    if mode == "search":
        if not prompt:
            return _builtin_reply(uid, tid, "What should I search for and pull? For example: \"best vape deals, with the product name and price\".", "scrape: need query")
        source = None
    elif mode == "page":
        if not urls:
            return _builtin_reply(uid, tid, "Paste the web address of the page you want scraped.", "scrape: need url")
        source = urls[0]
    else:  # multi
        if not urls:
            return _builtin_reply(uid, tid, "Paste the web addresses (two or more) you want scraped, separated by spaces.", "scrape: need urls")
        source = urls
    PENDING_SCRAPE.pop(uid, None)
    label = {"page": "this page", "search": "the web", "multi": "several pages"}[mode]
    scrape_in_background(user, mode, prompt, source)
    return _builtin_reply(uid, tid, f"On it — scraping {label} with your local brain. This takes a minute or two; I'll post the results right here when it's done.", "scrape running")


def scrape_chat(user, uid, tid, text):
    """Returns a reply dict if this message is part of a scrape, else None so normal handling continues.
    First time round it asks which 'skill' (page / search / several) to use."""
    now = time.time()
    pend = PENDING_SCRAPE.get(uid)
    if pend and now - pend["ts"] > 900:
        PENDING_SCRAPE.pop(uid, None)
        pend = None
    if pend:
        urls = SCRAPE_URL_RE.findall(text)
        if not pend.get("mode"):
            mode = _scrape_mode_pick(text)
            if not mode:
                PENDING_SCRAPE.pop(uid, None)       # they said something unrelated - don't trap them
                return None
            pend["mode"] = mode
        if urls:
            pend["urls"] = urls
        if not pend.get("prompt"):
            p = _scrape_prompt(text)
            if p:
                pend["prompt"] = p
        pend["ts"] = now
        return _scrape_launch_or_ask(user, uid, tid, pend)
    if SCRAPE_RE.search(text):
        if not scrape.ready():
            return _builtin_reply(uid, tid, "I can scrape sites, but the scraper isn't set up yet. Open the Scrape panel on the Tools page and press Set up (one-time, a few minutes to install), then ask me again.", "scrape: not installed")
        PENDING_SCRAPE[uid] = {"prompt": _scrape_prompt(text), "urls": SCRAPE_URL_RE.findall(text), "mode": None, "ts": now}
        return _builtin_reply(uid, tid,
            "Sure — what kind of scrape?\n"
            "1) This one page — pull data off a single web address\n"
            "2) Search the web — find and scrape the top results for a question\n"
            "3) Several pages — the same question across a few addresses\n"
            "Reply 1, 2 or 3 (or use the Scrape panel on the Tools page).", "scrape: asking which skill")
    return None


def pull_model_bg(user, name):
    """Download a local model through Ollama in the background, then let the brain use it."""
    uid = user["id"]

    def go():
        try:
            with httpx.stream("POST", core.OLLAMA + "/api/pull", json={"model": name}, timeout=None) as r:
                for _ in r.iter_lines():
                    pass
            core._model_cache["t"] = 0
            core.log(uid, f"Smart model ready: {name}", "done")
            try:
                watch.alert(user, "info", "Smart model ready", f"{name} is installed. Trillion will use it for heavy jobs.")
            except Exception:
                pass
        except Exception as e:
            core.log(uid, f"Could not download {name}: {type(e).__name__}", "waiting")

    threading.Thread(target=go, daemon=True).start()
# reverse-engineering an app on the phone - run the tool directly (the local brain tends to only talk about it)
REA_PHONE_RE = re.compile(r"\b(?:reverse[-\s]?engineer|use rea on|run rea on|do rea on|rea on|decompile|analy[sz]e|look inside|"
                          r"pull the apk of)\b\s+(?:the\s+)?(?P<app>.+?)(?:\s+app)?\s+(?:on|from)\s+(?:my|the|this)\s+(?:phone|screen)\b", re.I)
# a request that means "the app currently open on the phone" rather than a named one
REA_FG_RE = re.compile(r"\b(?:on (?:my|the) screen|that'?s? open|currently open|in the foreground|the (?:open|current|foreground) app|"
                       r"this app|the app i'?m (?:on|in|using))\b", re.I)
FG_WORDS = {"this app", "the app", "this", "open app", "the open app", "current app", "the current app",
            "foreground", "foreground app", "the foreground app", "it"}
REA_TRAFFIC_RE = re.compile(r"\b(?:what\s+servers?\s+does|what\s+does|capture|monitor|sniff)\b\s+(?:the\s+)?(?P<app>.+?)"
                            r"(?:\s+app)?\s+(?:connects?\s+to|talks?\s+to|sends?\s+to|network traffic|traffic)\b", re.I)
REA_CONFIRM_RE = re.compile(r"^\s*(?:yes|yeah|yep|ok(?:ay)?|sure|go ahead|go for it|do it|proceed(?:\s+and\s+open\s+it)?|"
                            r"continue|carry on|please do|lets do (?:it|this))\b", re.I)
PREV_APP_RE = re.compile(r"(?:reverse[-\s]?engineer|apk (?:file )?of|\bopen\b|analy[sz]e)\s+(?:the\s+)?"
                         r"(?P<app>[A-Za-z0-9][\w.&'-]+(?: [A-Za-z0-9][\w.&'-]*){0,3}?)\s+app\b", re.I)


# ---------- the Gumroad store, asked about or managed in plain words ----------
STORE_SALES = re.compile(r"\bhow much (?:did|have|has) (?:i|we|the store|my store|gumroad)\b.{0,20}\b(?:s[eo]ll|sold|ma[dk]e|earn(?:ed)?|taken?|bring in|brought in)\b|"
                         r"\bhow many (?:sales|orders|copies|ebooks?|books?)\b|\b(?:any|new) (?:new )?(?:sales|orders)\b|"
                         r"\b(?:sales|earnings|revenue|income|takings)\b.{0,25}\b(?:today|this week|this month|so far|gum ?road|store)\b|"
                         r"\b(?:gum ?road|store|shop)\b.{0,30}\b(?:sales|earnings|revenue|income|doing|going|numbers|figures|report)\b|"
                         r"\bhow (?:is|are|did) (?:my |the )?(?:gum ?road|store|shop|sales)\b", re.I)
STORE_PUB = re.compile(r"\b(un-?publish|re-?publish|publish|take down|hide|put back|switch off|switch on)\s+(?:my\s+|the\s+)?(.+?)\s+"
                       r"(?:on|from|in|at)\s+(?:my\s+|the\s+)?(?:gum ?road|store|shop)\b", re.I)
STORE_CODE = re.compile(r"\b(?:make|create|add|set up|give me)\s+(?:me\s+)?an?\s+(\d{1,3})\s*(?:%|percent|per cent)\s+(?:off\s+)?"
                        r"(?:discount\s+|coupon\s+|offer\s+|promo\s+)?code(?:\s+(?:called|named)\s+([A-Za-z0-9_-]{2,40}))?\s+(?:for|on)\s+"
                        r"(?:my\s+|the\s+)?(.+?)(?:\s+(?:on|in)\s+(?:gum ?road|my store|the store))?[.!?]?\s*$", re.I)


# ---------- Bible study, asked for in plain words ----------
BIBLE_VERSE = re.compile(r"\b(?:verse|scripture)\s+(?:of|for)\s+the\s+(?:day|morning|evening|night)\b|\b(?:today'?s|this (?:morning|evening)'?s?|tonight'?s|daily|morning|evening)\s+(?:bible\s+)?(?:verse|scripture)\b", re.I)
BIBLE_READ = re.compile(r"^\s*(?:please\s+)?(?:read|open|show|look up|what does|what is|give me)\s+(?:me\s+)?(?:the\s+)?(?:bible\s+)?(?:verse\s+|passage\s+|chapter\s+)?(.+?)(?:\s+say)?[.?!]?\s*$", re.I)
BIBLE_FIND = re.compile(r"\b(?:search|find|look)\b.{0,25}\b(?:bible|scripture|verses?)\b.{0,12}?\b(?:for|about|with|on|containing)\s+(.+?)[.?!]?\s*$", re.I)
BIBLE_ASK = re.compile(r"\b(?:explain|meaning of|what does .{1,60} mean|what does the bible say|what does scripture say|study|context of|"
                       r"help me understand|bible study|bybelstudie)\b", re.I)


def bible_shortcut(user, text):
    """Verse of the day, reading a passage, searching and study questions, for a login with Bible study on."""
    if len(text) > 400 or not bible.allowed(user):
        return None
    try:
        code = bible.version_of(user)
        if BIBLE_VERSE.search(text):
            night = bool(re.search(r"\b(evening|night|tonight)", text, re.I)) or (datetime.datetime.now().hour >= 17 and not re.search(r"\bmorning\b", text, re.I))
            p = bible.daily(user, "night" if night else "morning")
            return ("Tonight's verse" if night else "This morning's verse") + f" is {p['ref']}: {p['text']}", "bible"
        m = BIBLE_FIND.search(text)
        if m:
            want = m.group(1).strip().strip('"\u201c\u201d')
            r = bible.search(code, f'"{want}"', 8) if " " in want else {"total": 0}       # spoken words are a phrase first
            if not r["total"]:
                r = bible.search(code, want, 8)
            if not r["total"]:
                return f"I found no verse with {m.group(1)}.", "bible"
            return (f"{r['total']} verse{'s' if r['total'] != 1 else ''} with {m.group(1)}. " + " ".join(f"{h['ref']}: {h['t']}" for h in r["hits"][:3])
                    + (" The rest are on the Bible page." if r["total"] > 3 else "")), "bible"
        refs = bible.refs_in(text, 3)
        if BIBLE_ASK.search(text) and (refs or re.search(r"\bbible|scripture|verse|bybel\b", text, re.I)):
            h = bible.helper(llm_chat, user, text)
            return h["answer"] + ("\n\nPassages used: " + "; ".join(p.get("focus") or p["ref"] for p in h["passages"]) if h["passages"] else ""), "bible"
        m = BIBLE_READ.match(text)
        if m and refs and len(refs) == 1 and bible.refs_in(m.group(1), 1) and len(m.group(1)) < 40:
            p = bible.passage(code, *refs[0])
            body = " ".join((f"{x['n']} " if len(p["verses"]) > 1 else "") + x["t"] for x in p["verses"][:12])
            return f"{p['ref']}: {body}" + (" The rest of the chapter is on the Bible page." if len(p["verses"]) > 12 else ""), "bible"
        if refs and re.fullmatch(r"\s*" + bible.REF.pattern + r"\s*[.?]?\s*", text, re.I):      # just a reference on its own
            p = bible.passage(code, *refs[0])
            return f"{p['ref']}: " + " ".join((f"{x['n']} " if len(p["verses"]) > 1 else "") + x["t"] for x in p["verses"][:12]), "bible"
    except bible.BibleError as e:
        return str(e), "bible"
    return None


STORE_NEW = re.compile(r"\b(?:create|add|make|list|post|put up|sell)\s+(?:an?\s+|this\s+(?:as\s+an?\s+)?|it\s+as\s+an?\s+)?(?:new\s+)?(?:gum ?road\s+)?"
                       r"(?:product|listing|item)\s+(?:on\s+(?:gum ?road|my store|the store)\s+)?(?:called|named|titled)\s+[\"\u201c']?(.+?)[\"\u201d']?\s+"
                       r"(?:for|at|priced at|costing)\s+(?:\$|usd\s*|r\s*)?(\d+(?:[.,]\d{1,2})?)\b(.*)$", re.I | re.S)


def store_allowed(user):
    st = core.settings(user)
    return bool(st["store_on"]) if "store_on" in st else user["name"].lower() == "brandon"


def store_user(user=Depends(auth)):
    if not store_allowed(user):
        raise HTTPException(403, "The store is not switched on for this login.")
    return user


def store_shortcut(user, text, files=None):
    """Questions about the Gumroad store and simple changes to it, answered without the model."""
    if len(text) > 300 or not store_allowed(user):
        return None
    on = bool(gumroad.token(user))
    try:
        m = STORE_NEW.search(text)
        if m and on:
            rest = m.group(3) or ""
            live = bool(re.search(r"\b(publish|post|put it on sale|go live|make it live|on sale|sell it)\b", rest + " " + text[:m.start(1)], re.I))
            r = gumroad.create_product(user, m.group(1).strip(), int(round(float(m.group(2).replace(",", ".")) * 100)),
                                       files=[(f["path"], f["name"]) for f in (files or [])], publish=live)
            return created_sentence(r), "store"
        m = STORE_PUB.search(text)
        if m and on:
            p = gumroad.find_product(user, m.group(2))
            live = bool(re.match(r"(?:re-?publish|publish|put back|switch on)", m.group(1), re.I))
            gumroad.set_published(user, p["id"], live)
            return (f"{p['name']} is {'published again' if live else 'unpublished'} on Gumroad."
                    + ("" if live else " Buyers cannot see it until you publish it again.")), "store"
        m = STORE_CODE.search(text)
        if m and on:
            p = gumroad.find_product(user, m.group(3))
            pct = int(m.group(1))
            name = gumroad.create_code(user, p["id"], m.group(2) or f"SAVE{pct}", pct, percent=True)
            return f"Done. The code {name} gives {pct}% off {p['name']} on Gumroad.", "store"
        if STORE_SALES.search(text) and (on or re.search(r"\bgum ?road\b", text, re.I)):
            if not on:
                return "Your Gumroad store is not connected yet. Open the Store page and connect it with an access token.", "store"
            try:
                gumroad.sync(user)
            except gumroad.GumroadError:
                pass                                    # the saved figures are still worth giving; the error shows on the page
            low = text.lower()
            period = ("week" if re.search(r"\bweek\b|7 days", low) else "days30" if re.search(r"30 days|last month|past month", low)
                      else "month" if "month" in low else "all" if re.search(r"\b(total|all time|so far|ever|overall|altogether)\b", low)
                      else "today" if re.search(r"\btoday\b|\bnew (sales|orders)\b", low) else None)
            if period:
                return gumroad.say_sales(user, period), "store"
            return gumroad.say_sales(user, "today") + " " + gumroad.say_sales(user, "days30"), "store"
    except gumroad.GumroadError as e:
        return "I could not do that on Gumroad: " + str(e), "store"
    return None


def sent_sentence(r, msg):
    went, left = r.get("files_sent") or [], r.get("files_left") or []
    if not went and not left:
        if r["status"] == "send_pressed":
            return f'Sent to {r.get("to")} on WhatsApp: "{msg}". I pressed Send; I cannot confirm delivery from here.'
        return f'The WhatsApp to {r.get("to")} is typed in the chat but I could not press Send. Press it on the phone.'
    files = lambda names: (f"{len(names)} files ({', '.join(names[:6])})" if len(names) != 1 else names[0])
    if r["status"] == "send_pressed":
        what = (f'"{msg}" and ' if msg else "") + files(went)
        return f'Sent to {r.get("to")} on WhatsApp: {what}. I pressed Send; I cannot confirm delivery from here.'
    done = ([f'the message "{msg}"'] if msg and (went or "ready" in r.get("note", "")) else []) + ([files(went)] if went else [])
    return ((f'Sent to {r.get("to")} on WhatsApp: ' + " and ".join(done) + ". " if done else "") + r.get("note", "")
            + (f" Not sent yet: {', '.join(left[:6])}." if left else ""))


def wa_send(user, tid, who, msg, files, own=False):
    """One WhatsApp from a chat shortcut, with any files attached in this turn. Returns the shortcut's answer."""
    args = {"contact": who, "message": msg}
    if files:
        args["files"] = [f["id"] for f in files]
    r = tools.execute(user, "phone_send_whatsapp", args, task_id=tid, direct=True)
    if r.get("status") in ("send_pressed", "typed_not_sent"):
        return sent_sentence(r, msg), False, r
    if r.get("status") == "waiting_for_approval":
        a = core.q("SELECT summary FROM approvals WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],), one=True)
        return "Ready. " + (a["summary"] if a else "The message") + ". Press Approve in the red box and I will send it.", True, r
    return ("I could not send that WhatsApp: " + r.get("error", "unknown problem")), False, r


def phone_shortcut(user, text, tid, files=None):
    """Common phone requests are run directly, so they do not depend on the model choosing the right tool."""
    if len(text) > 400:
        return None
    files = files or []
    for kind, rx in PHONE_RULES:
        m = rx.search(text)
        if not m:
            continue
        if kind == "whatsapp_file":
            if not files:
                continue
            who = re.sub(r"^(?:my |to )", "", m.group(1).strip(" ,.:"), flags=re.I).strip()
            msg = ((m.group(2) if rx.groups > 1 else "") or "").strip().strip('"\u201c\u201d')
            if not who or len(who) > 60 or re.search(_WA + r"|\bsaying\b|\bmessage\b", who, re.I):
                continue
            who = WA_WITH.sub("", who).strip()
            if who.lower() in ("me", "myself"):
                who = str(core.settings(user).get("phone_own_number") or "").strip()
                if not who:
                    return ("I do not know your own number yet. Tell me once, like this: my WhatsApp number is 082 123 4567. "
                            "Then ask me again."), False, {"error": "own number not set"}
            return wa_send(user, tid, who, msg, files)
        if kind == "mynumber":
            st = core.settings(user)
            st["phone_own_number"] = re.sub(r"[^\d+]", "", m.group(1))
            core.save_settings(user["id"], st)
            return f"Saved. Your own number is {st['phone_own_number']}. Now you can say: send me a test WhatsApp.", False, {"saved": True}
        if kind == "whatsapp_me":
            if not re.search(r"what'?s ?(?:app|up)|\btest\b", text, re.I):
                continue
            own = str(core.settings(user).get("phone_own_number") or "").strip()
            if not own:
                return ("I do not know your own number yet. Tell me once, like this: my WhatsApp number is 082 123 4567. "
                        "Then ask me again."), False, {"error": "own number not set"}
            msg = (m.group(1) or "").strip().strip('"\u201c\u201d')
            if files and (msg or not re.search(r"\btest\b", text, re.I)):
                return wa_send(user, tid, own, msg, files)
            msg = msg or f"Test message from {core.settings(user).get('assistant_name') or 'Trillion'}."
            if files:
                return wa_send(user, tid, own, msg, files)
            r = tools.execute(user, "phone_send_whatsapp", {"contact": own, "message": msg}, task_id=tid, direct=True)
            if r.get("status") in ("send_pressed", "typed_not_sent"):
                return sent_sentence(r, msg), False, r
            if r.get("status") == "waiting_for_approval":
                return f'Ready. A WhatsApp to your own number ({own}): "{msg}". Approve it on the dashboard and I will send it.', True, r
            return ("I could not prepare that WhatsApp: " + r.get("error", "unknown problem")), False, r
        if kind == "whatsapp_plain":
            who = re.sub(r"^(?:my |to )", "", m.group(1).strip(), flags=re.I).strip()
            if files:                                   # the attached file is the message: no wording needed
                who = WA_WITH.sub("", who).strip()
                return wa_send(user, tid, who, f"Test message from {core.settings(user).get('assistant_name') or 'Trillion'}."
                               if re.search(r"\btest\b", text, re.I) else "", files)
            if not re.search(r"\btest\b", text, re.I):
                return f"What should the WhatsApp to {who} say? Tell me like this: send a WhatsApp to {who} saying ...", False, {"asked": True}
            msg = f"Test message from {core.settings(user).get('assistant_name') or 'Trillion'}."
            r = tools.execute(user, "phone_send_whatsapp", {"contact": who, "message": msg}, task_id=tid, direct=True)
            if r.get("status") in ("send_pressed", "typed_not_sent"):
                return sent_sentence(r, msg), False, r
            if r.get("status") == "waiting_for_approval":
                a = core.q("SELECT summary FROM approvals WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],), one=True)
                return "Ready. " + (a["summary"] if a else "The message") + ". Press Approve in the red box and I will send it.", True, r
            return ("I could not send that WhatsApp: " + r.get("error", "unknown problem")), False, r
        if kind == "whatsapp":
            who = m.group(1).strip(" ,.:")
            who = re.sub(r"^(?:and\s+)?(?:then\s+)?(?:send|tell|message|text)\s+", "", who, flags=re.I)
            who = re.sub(r"\s+(?:a|the)\s+(?:what'?s ?(?:app|up)\s+)?(?:message|text|note)$", "", who, flags=re.I)
            who = re.sub(r"^(?:my |to )", "", who, flags=re.I).strip()
            msg = m.group(2).strip().strip('"\u201c\u201d')
            if files:
                who = WA_WITH.sub("", who).strip()
            if not who or not msg or len(who) > 60:
                continue
            if files:
                return wa_send(user, tid, who, msg, files)
            r = tools.execute(user, "phone_send_whatsapp", {"contact": who, "message": msg}, task_id=tid, direct=True)
            if r.get("status") in ("send_pressed", "typed_not_sent"):
                return sent_sentence(r, msg), False, r
            if r.get("status") == "waiting_for_approval":
                a = core.q("SELECT summary FROM approvals WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],), one=True)
                return "Ready. " + (a["summary"] if a else "The message") + ". Approve it on the dashboard and I will send it.", True, r
            return ("I could not prepare that WhatsApp: " + r.get("error", "unknown problem")), False, r
        if kind == "open":
            if re.search(r"\b(pc|computer|desktop|laptop|browser|web)\b", text, re.I):
                continue
            app_name = re.sub(r"^what'?s ?(?:app|up)", "whatsapp", m.group(1).strip(), flags=re.I)
            app_name = re.sub(r"\s+app$", "", app_name, flags=re.I)
            r = tools.execute(user, "phone_open_app", {"app": app_name}, task_id=tid)
            shown = {"whatsapp": "WhatsApp", "whatsapp business": "WhatsApp Business", "tiktok": "TikTok"}.get(
                app_name.lower(), app_name.capitalize())
            return (f"{shown} is open on your phone." if "error" not in r else r["error"]), False, r
        if kind == "wa_read":
            if re.search(r"\b(send|open|launch|start)\b", text, re.I):
                continue
            wa_poll(user)
            rows = wa_rows(user["id"], only_new=True, limit=8)[::-1]
            if not rows:
                return "No new WhatsApp messages.", False, {"ok": True}
            core.x("UPDATE wa_inbox SET seen=1 WHERE user_id=?", (user["id"],))
            said = " ".join(f"{r['sender']}{' in ' + r['chat'] if r['is_group'] else ''} says: {r['text'][:220]}." for r in rows)
            return f"You have {len(rows)} new WhatsApp message{'s' if len(rows) != 1 else ''}. {said}", False, {"ok": True}
        if kind == "unlock":
            try:
                r = phone.unlock(user, explicit=True)
                return ("Your phone is unlocked." if r["was_locked"] else "Your phone was already unlocked."), False, r
            except phone.PhoneError as e:
                return str(e), False, {"error": str(e)}
        if kind.startswith("simple:"):
            try:
                return phone.simple(user, kind.split(":")[1]), False, {"ok": True}
            except phone.PhoneError as e:
                return str(e), False, {"error": str(e)}
        if kind == "screen":
            r = tools.execute(user, "phone_read_screen", {}, task_id=tid)
            if "error" in r:
                return r["error"], False, r
            seen = [i["text"] for i in r["items"] if not i["text"].startswith("(")][:14]
            names = {v: k for k, v in phone.APPS.items()}
            shown = names.get(r["app"], r["app"] or "an app")
            return (f"Your phone is showing {shown}. On screen: " + "; ".join(seen) + "."), False, r
        if kind == "press":
            scroll = {"back": "back", "home": "home"}
            r = tools.execute(user, "phone_press", {"key": m.group(1)}, task_id=tid)
            return (f"Pressed {m.group(1).lower()} on your phone." if "error" not in r else r["error"]), False, r
        if kind == "swipe":
            d = m.group(1).lower()
            if text.lower().lstrip().startswith("scroll"):      # scrolling down means swiping up
                d = {"down": "up", "up": "down"}.get(d, d)
            r = tools.execute(user, "phone_swipe", {"direction": d}, task_id=tid)
            return ("Done." if "error" not in r else r["error"]), False, r
        if kind == "tap":
            r = tools.execute(user, "phone_tap", {"target": m.group(1).strip()}, task_id=tid)
            return (f"Tapped {r['tapped']}." if "error" not in r else r["error"]), False, r
        if kind == "type":
            r = tools.execute(user, "phone_type", {"text": m.group(1)}, task_id=tid)
            return ("Typed it." if "error" not in r else r["error"]), False, r
        if kind == "screenshot":
            r = tools.execute(user, "phone_screenshot", {}, task_id=tid)
            return (f"Screenshot saved to {r['saved']}." if "error" not in r else r["error"]), False, r
        if kind == "ring":
            r = tools.execute(user, "phone_ring", {}, task_id=tid)
            return (r.get("note") or r.get("error") or "Done."), False, r
        if kind == "locate":
            r = tools.execute(user, "phone_locate", {}, task_id=tid)
            if "error" in r:
                return r["error"], False, r
            acc = f", accurate to about {r['accuracy_m']} metres" if r.get("accuracy_m") else ""
            return f"Your phone's last known position is here: {r['map']}{acc}.", False, r
        if kind == "status":
            r = tools.execute(user, "phone_status", {}, task_id=tid)
            return (phone_sentence(r) if "error" not in r else r["error"]), False, r
    return None


def phone_sentence(r):
    bits = [f"Your {r.get('model') or 'phone'} is at {r.get('battery_percent', '?')} percent"
            + (" and charging" if r.get("charging") else "")]
    if r.get("storage_free_gb") is not None:
        bits.append(f"with {r['storage_free_gb']} GB free of {r['storage_total_gb']}")
    out = ", ".join(bits) + "."
    if r.get("wifi"):
        out += f" It is on the Wi-Fi network {r['wifi']}."
    return out + f" The screen is {r.get('screen', 'off')}."


# a hard job that is worth the slower, bigger local brain: coding and deep reasoning
HEAVY_HINTS = re.compile(
    r"\b(cod\w*|program\w*|script|function|class|regex|algorithm|refactor|debug\w*|stack ?trace|exception|compile|"
    r"python|javascript|typescript|java\b|rust|golang|c\+\+|sql|bash|powershell|api|endpoint|json|yaml|docker|"
    r"analy[sz]e|reason\w*|prove|derive|architect\w*|design\s+(?:a|the|my)\s+\w+|plan\s+out|strateg\w*|"
    r"explain\s+in\s+detail|step\s*by\s*step|complex|complicated)\b", re.I)


def _wants_heavy(messages):
    """Does the latest user turn look like a hard job the bigger brain should take?"""
    last = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            last = c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if isinstance(p, dict)) if isinstance(c, list) else ""
            break
    return bool(last) and (len(last) > 400 or bool(HEAVY_HINTS.search(last)))


# words that mean she has to DO something or look something up: those go to the full brain with its tools
NEEDS_TOOLS = re.compile(
    r"\b(creat\w*|make|made|build|write|wrote|draft|generat\w*|open|launch|start|run|delet\w*|remov\w*|mov(?:e|ing)|renam\w*|cop(?:y|ies)|past\w*|"
    r"find|search|look\s+(?:up|for|in)|list|show|read|check|sav\w*|remember|recall|not(?:e|es)|memory|forget|send|sent|messag\w*|text|whats ?app|"
    r"email|call|phone|cell|contact|file|folder|document|docx?|word|excel|sheet|spreadsheet|powerpoint|deck|slide|pdf|download\w*|desktop|"
    r"website|site|browser|web|online|internet|skill|connector|install|update|video|screenshot|window|app|program|schedul\w*|remind\w*|"
    r"today|tomorrow|yesterday|latest|current|news|weather|price|my)\b", re.I)




def when_to_iso(words):
    """'tomorrow 6pm', '18:30', '2026-10-14 9am' -> '2026-10-14T18:00' in South African time."""
    w = words.lower().strip()
    day = datetime.date.today()
    if "tomorrow" in w:
        day += datetime.timedelta(days=1)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", w)
    if m:
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        w = w[m.end():]
    t = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
    h, mi = (int(t.group(1)), int(t.group(2) or 0)) if t else (9, 0)
    if t and t.group(3) == "pm" and h < 12:
        h += 12
    if t and t.group(3) == "am" and h == 12:
        h = 0
    return f"{day.isoformat()}T{h:02d}:{mi:02d}"


def tools_for(user, text):
    """Hand the brain only the tools this request could need: fewer choices, fewer wrong ones."""
    uid = user["id"]
    skip = {"update_self", "create_video", "create_ai_clip", "create_ebook"}                      # both handled directly
    if not (PHONE_WORDS.search(text) and core.settings(user).get("phone_serial")):
        skip |= {n for n in (s_["function"]["name"] for s_ in tools.SPECS) if n.startswith("phone_")}
    if not core.settings(user).get("phone_full_control"):
        skip |= tools.FULL_PHONE
    if not extend.enabled_skills(uid):
        skip.add("use_skill")
    if not extend.enabled_connectors(uid):
        skip |= {"connector_tools", "connector_call"}
    if not user["is_admin"]:
        skip.add("run_command")
    if not (user["is_admin"] and core.settings(user).get("phone_serial")):
        skip |= {"rea_phone_app", "rea_phone_traffic"}                                # owner only, and need a linked phone
    skip |= {t for t, k in apps.TOOL_APP.items() if not apps.usable(user, k)}       # only apps this login has connected
    if not manuals.has_any(user):
        skip |= {"search_manual", "read_manual_page", "split_manual"}
    return [s_ for s_ in tools.SPECS if s_["function"]["name"] not in skip]


VIDEO_RE = re.compile(r"\b(create|make|generate|produce|build|render)\b[^.?!]{0,80}\bvideo\b", re.I)
# the user's own scene pictures, to be "completed" into a video (stickmen / storyboard / these scenes)
MONTAGE_RE = re.compile(r"\b(stick\s?m(?:a|e)n|stick figures?|storyboard|these scenes?|my scenes?|each scene|"
                        r"scene by scene|complete (?:these|my|the) (?:scenes?|pictures?|drawings?|images?)|"
                        r"(?:turn|make) (?:these|my) (?:scenes?|pictures?|drawings?|sketches?|images?) into)\b", re.I)
REVISE_RE = re.compile(r"\b(change|fix|revise|redo|remake|update|edit)\b[^.?!]{0,30}\bvideo\b\s*#?\s*(\d+)\s*[:,\-]?\s*(.*)", re.I | re.S)


SITE_RE = re.compile(r"\b(?:check|is|how is|how's)\b.{0,12}\b(?:my|the|our)\s+(?:web ?site|site)\b", re.I)
PC_RE = re.compile(r"\b(?:status|health|how is|how's)\b.{0,14}\b(?:my |the |this )?(?:pc|computer)\b|\bpc status\b", re.I)
IMAGE_RE = re.compile(r"\b(create|make|generate|draw|design|paint)\b[^.?!]{0,30}\b(image|picture|photo|logo|icon|illustration|poster|banner|artwork|drawing)\b"
                      r"(?:\s+(?:of|for|about|showing|with|that shows))?\s*(.*)$", re.I | re.S)
OPEN_VIDEO_RE = re.compile(r"\b(open|play|show|watch|view)\b[^.?!]{0,30}\b(video|clip)s?\b", re.I)


# an AI-generated clip (FreeVideo on this PC) rather than her own edited video
AICLIP_RE = re.compile(r"\b(ai[- ]?(generated |made )?(video|clip|footage)s?|freevideo|free video (clip|engine)|(generated?|generative) (video|clip)s?|"
                       r"(video|clip) (with|using|by) ai|generate (me )?a clip)\b", re.I)


def aiclip_request(text):
    m = re.search(r"(\d{1,2})[\s-]*(seconds?|secs?|s)\b", text, re.I)
    low = text.lower()
    fmt = "landscape" if re.search(r"landscape|horizontal|widescreen|youtube", low) else ("square" if "square" in low else "vertical")
    return {"prompt": text, "seconds": int(m.group(1)) if m else 5, "format": fmt}


def aiclip_reply(res):
    if "error" in res:
        return "I could not make the AI clip: " + res["error"]
    return (f"Your AI clip is ready: {res['title']}, {res['seconds']:g} seconds. It took {res['minutes']:g} minutes. "
            f"It is on the Videos page and saved at {res['saved']}. " + " ".join(res.get("notes", []))).strip()


EBOOK_RE = re.compile(r"\b(create|make|write|generate|produce|build)\b[^.?!]{0,60}\b(e-?books?|lead magnet|guide ?book|pdf guide|digital guide)\b", re.I)


EBOOK_WORD = re.compile(r"\be-?books?\b", re.I)
EBOOK_FROM = re.compile(r"\b(create|make|turn|convert|build|write|generate|produce|into|from|as an?)\b", re.I)
PICTURES = re.compile(r"\b(images?|pictures?|photos?|illustrations?|visuals?|graphics?)\b", re.I)


def wants_ebook(text, files):
    """An ebook asked for outright, or any 'make this an ebook' said about an attached document."""
    if EBOOK_RE.search(text):
        return True
    return bool(any(f["kind"] == "document" for f in files) and EBOOK_WORD.search(text) and EBOOK_FROM.search(text))


def ebook_request(text, files=()):
    m = re.search(r"(\d{1,2})[\s-]*chapters?\b", text, re.I)
    no_pics = re.search(r"\b(no|without)\s+(images?|pictures?|photos?|illustrations?)\b", text, re.I)
    return {"prompt": text, "chapters": int(m.group(1)) if m else 5,
            "sources": [f["path"] for f in files if f["kind"] == "document"],
            "images": [f["path"] for f in files if f["kind"] == "image"],
            "pictures": bool(PICTURES.search(text)) and not no_pics}


def ebook_reply(res):
    if "error" in res:
        return "I could not make the ebook: " + res["error"]
    return (f"Your ebook is ready: {res['title']}. {res['pages']} pages, {res['chapters']} chapters, about {res['words']} words"
            f"{', ' + str(res['pictures']) + ' picture' + ('s' if res['pictures'] != 1 else '') if res.get('pictures') else ''}. "
            f"It is saved at {res['saved']}{', with an EPUB copy next to it' if res.get('epub') else ''}. " + ("" if res.get("from_document") else
               "I wrote it myself, so read it through and check the facts before you share or sell it. ")
            + " ".join(res.get("notes", []))).strip()


def my_links(user):
    return [l for l in (core.settings(user).get("links") or []) if isinstance(l, dict) and l.get("id") and l.get("url")]


def my_link(user, text):
    """'open my health tracker': one of this login's own saved links, asked for by name."""
    if len(text) > 120 or not re.search(r"\b(open|show|launch|go to|take me to|bring up|start)\b", text, re.I):
        return None
    low = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    for l in my_links(user):
        name = re.sub(r"[^a-z0-9 ]", " ", str(l.get("name", "")).lower()).strip()
        if name and re.search(r"\b" + re.escape(name) + r"\b", low):
            return l
    return None


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
    if res.get("engine") == "montage":                     # the user's own scenes, completed locally
        n = res.get("scenes", 0)
        return (f"Your video is ready: {res['title']}, {round(res['seconds'])} seconds, from your {n} "
                f"scene{'s' if n != 1 else ''} with the local voice. It is on the Videos page and saved at "
                f"{res['saved']}. {extra}").strip()
    clips = res.get("footage_clips") or 0
    return (f"Your video is ready: {res['title']}, {round(res['seconds'])} seconds, "
            f"{'with' if res.get('voice_over') else 'without'} voice-over, {clips} stock clip{'s' if clips != 1 else ''}"
            f"{' and ' + str(res['ai_pictures']) + ' AI-made picture' + ('s' if res['ai_pictures'] != 1 else '') if res.get('ai_pictures') else ''}. "
            f"It is on the Videos page and saved at {res['saved']}. {extra}").strip()


def update_reply(res):
    if "error" in res:
        return "I could not update: " + res["error"]
    if res.get("status") == "already_up_to_date":
        return f"I am already on the newest version, {res['version']}."
    changes = "; ".join(res.get("what_changed", [])[:4])
    if not changes:
        return (f"The newest version, {res['version']}, was already downloaded but I was still running "
                f"{res.get('was', 'an older one')}. I am restarting now to switch over and will be back in a few seconds.")
    return (f"Updated from {res.get('was', 'the old version')} to {res['version']}. What changed: {changes}. "
            "I am restarting now and will be back in a few seconds.")


def run_agent(user, text, files=None):
    uid = user["id"]
    files = files or []
    BUSY[uid] = True
    STOP.discard(uid)
    tid = core.add_task(uid, text)
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
           (uid, "user", text + "".join(attach.marker(f) for f in files), time.time()))
    core.log(uid, f'Heard: "{text[:140]}"', "voice")
    wm = WAKE_RE.search(text) if len(text) < 80 else None
    if wm:
        st = core.settings(user)
        st["wake_word"] = bool(re.search(r"\b(on|enable|activate|use|start)\b", text, re.I)) and not re.search(r"\b(off|disable|stop|no)\b", text, re.I)
        core.save_settings(uid, st)
        name = st.get("assistant_name") or "Trillion"
        reply = (f"Wake word is on. In voice mode, say {name} first and I will answer." if st["wake_word"]
                 else "Wake word is off. In voice mode I answer whenever you speak.")
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply)
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    cm = voicecall.parse(text)
    if cm and not files:
        vp = voicecall.prefs(user)
        if cm.get("stop"):
            for pl in voicecall.plans(user):
                voicecall.delete_plan(user, pl["id"])
            voicecall.save_prefs(user, {"on": False})
            reply = "I will not call you any more. Urgent things still come as a notification and a ring. Switch calls on again on the Phone page."
        elif not vp["target"] and not voicecall.registered(user):
            reply = "I cannot call you yet: set up Linphone on your phone so it signs in to me (steps on the Phone page)."
        elif cm.get("on_done"):
            BUSY[uid] = False
            running = core.q("SELECT id, title FROM tasks WHERE user_id=? AND status='in_progress' AND id<>?", (uid, tid))
            watch_ids = {r["id"] for r in running}
            topic = cm.get("topic") or "projects"
            if topic in ("let me know", "know", "me", ""):
                topic = "projects"
            DONE_CALLS[uid] = {"watch": watch_ids or None, "exclude": {tid}, "topic": topic}
            if watch_ids:
                what = running[0]["title"][:60] if len(running) == 1 else f"{len(running)} running jobs"
                reply = f"Will do — I'll call you on Linphone as soon as that finishes ({what}). I won't ring you before then."
            else:
                reply = ("Will do — I'll call you on Linphone as soon as the next job finishes. "
                         "Nothing is running right now, so if the project is something I start later, I'll ring you when it's done.")
        elif cm.get("now"):
            BUSY[uid] = False
            call_in_background(user, cm["topic"])
            reply = "Calling you now on Linphone."
        else:
            voicecall.add_plan(user, cm["at"], cm["days"], cm["topic"], cm["once"])
            if not vp["on"]:
                voicecall.save_prefs(user, {"on": True})
            when = {"daily": "every day", "weekdays": "every weekday", "weekends": "on weekends"}.get(cm["days"]) or \
                "on " + ", ".join(["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"][int(d)] for d in cm["days"])
            reply = (f"Done. I will call you {'tomorrow' if cm['once'] and cm['once'] != datetime.date.today().isoformat() else ('today' if cm['once'] else when)} "
                     f"at {cm['at']}" + (" with a breakdown of your projects and anything waiting on you." if cm["topic"] == "projects"
                                         else f" about {cm['topic']}.") + " Not during meeting mode.")
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    if nudge.ASK.search(text) and len(text) < 90:
        return _chat_reply(uid, tid, nudge.answer(user, SITES))
    if MORNING_RE.search(text) and len(text) < 60:
        res = morning.send_now(user, SITES)
        reply = res["title"] + "\n" + "\n".join(res["lines"])
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail="morning briefing")
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    if PROJECTS_RE.search(text) and len(text) < 80 and not NEEDS_TOOLS.search(text):
        ds = core.day_start()
        running = core.q("SELECT title, status, detail FROM tasks WHERE user_id=? AND status IN "
                         "('in_progress','waiting') ORDER BY id DESC LIMIT 8", (uid,))
        done_today = core.q("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND status='completed' AND updated>=?",
                            (uid, ds), one=True)["c"]
        if running:
            parts = "; ".join(f"{r['title']} — {r['detail'] or 'working on it'}"
                              + (" (waiting for your OK)" if r["status"] == "waiting" else "") for r in running)
            reply = f"You have {len(running)} running: {parts}. {done_today} finished today."
        else:
            reply = f"Nothing is running right now. {done_today} finished today."
        return _chat_reply(uid, tid, reply)
    sc = scrape_chat(user, uid, tid, text)
    if sc is not None:
        return sc
    mt = meeting.parse(text)
    if mt:
        if mt[0] == "on":
            meeting.start(user, until=mt[1])
            st_ = core.settings(user)
            reply = (f"Meeting mode is on until {meeting.hhmm(mt[1])}. I will answer your WhatsApps"
                     + (", decline calls with a message" if meeting.prefs(user)["calls"] else "")
                     + " and give you a summary when it ends. Say \"I'm out of the meeting\" to stop sooner.")
            if not st_.get("phone_serial"):
                reply = "Meeting mode is on, but no phone is linked, so I cannot answer WhatsApps. Link your phone on the Phone page."
            elif not st_.get("phone_auto_unlock"):
                reply += " Your phone must be unlocked for me to reply: save your unlock code on the Phone page and tick 'Unlock by herself'."
        else:
            summary = meeting.finish(user, quiet=True)
            reply = "Meeting mode is off. " + (summary or "It was not on.")
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    tm = THEME_RE.search(text) if len(text) < 80 else None
    if tm:
        name = {"dark": "night", "light": "day"}.get((tm.group(1) or tm.group(2)).lower(), (tm.group(1) or tm.group(2)).lower())
        st = core.settings(user)
        st["theme"], st["accent"] = name, THEMES[name]
        core.save_settings(uid, st)
        reply = f"{name.capitalize()} mode is on."
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply)
        return {"reply": reply, "task_id": tid, "provider": "built-in", "show": ""}
    if VERSION_RE.search(text) and not UPDATE_RE.search(text):
        st = core.settings(user)
        reply = (f"I am running version {VERSION}. WhatsApps to saved contacts: "
                 + ("sent straight away, no approval." if st.get("whatsapp_auto_contacts") else "wait for your approval (switch is off on the Phone page).")
                 + " Full phone control: " + ("on." if st.get("phone_full_control") else "off."))
        return _chat_reply(uid, tid, reply)
    if UPDATE_RE.search(text):  # never leave this one to the model's judgement
        res = tools.execute(user, "update_self", {}, task_id=tid)
        reply = update_reply(res)
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    rt = routines.match(user, text)
    if rt:  # "morning": run that login's routine straight away, no model needed
        core.x("UPDATE tasks SET domain='ROUTINE', title=? WHERE id=?", (rt["name"] + " routine", tid))
        try:
            out = routines.run(user, rt, SITES)
            status = "completed"
        except Exception as e:
            out, status = {"reply": f"I could not run the {rt['name']} routine: {type(e).__name__}: {e}", "brief": None}, "failed"
        finally:
            BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", out["reply"], time.time()))
        core.set_task(tid, status=status, detail=f"{rt['name']} routine")
        core.log(uid, f"Routine run: {rt['name']}", "done" if status == "completed" else "waiting")
        return {"reply": out["reply"], "task_id": tid, "provider": "built-in", "brief": out["brief"]}
    try:
        one = routines.ask(user, text, SITES)
    except Exception:
        one = None
    if one:  # "what's the weather": just that one section
        BUSY[uid] = False
        core.x("UPDATE tasks SET domain='ROUTINE', title=? WHERE id=?", (one["name"], tid))
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", one["reply"], time.time()))
        core.set_task(tid, status="completed", detail=one["name"])
        return {"reply": one["reply"], "task_id": tid, "provider": "built-in", "brief": one["brief"]}
    pm = PERSONA_RE.search(text) if len(text) < 90 else None
    if pm:  # "switch to witty mode": change it straight away
        choice = PERSONA_WORDS[(pm.group(1) or pm.group(2) or pm.group(3)).lower()]
        st = core.settings(user)
        st["personality"] = choice
        core.save_settings(uid, st)
        reply = PERSONA_HELLO[choice]
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    ss = bible_shortcut(user, text) or store_shortcut(user, text, files)
    if ss:
        reply, show = ss
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if reply.startswith("I could not") else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in", "show": show}
    if user["is_admin"] and PULL_HEAVY_RE.search(text) and not files:
        hv = core.settings(user).get("heavy_model") or "mistral-small:24b"
        BUSY[uid] = False
        if core.has_model(hv):
            reply = f"The smart model ({hv}) is already installed — I use it automatically for coding and heavy reasoning, and the fast model for everyday chat."
        else:
            pull_model_bg(user, hv)
            reply = (f"Started downloading the smart model ({hv}) in the background. It's about 14 GB, so give it a while — "
                     "I'll switch to it automatically for coding and deep reasoning once it's ready, and keep everyday chat on the fast local model.")
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail="Smart model")
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    if USAGE_RE.search(text) and not files:
        rep = extend.usage_report(user)
        BUSY[uid] = False

        def _line(x):
            if x["unused"]:
                return f"  - {x['name']}: not used yet"
            when = time.strftime("%d %b", time.localtime(x["last"])) if x.get("last") else "?"
            return f"  - {x['name']}: used {x['uses']}x (last {when})"

        parts = []
        if rep["skills"]:
            parts.append(f"Skills ({len(rep['unused_skills'])} not used yet):")
            parts += [_line(s) for s in rep["skills"]]
        if user["is_admin"] and rep["connectors"]:
            parts.append("")
            parts.append(f"Connectors ({len(rep['unused_connectors'])} not used yet):")
            parts += [_line(c) for c in rep["connectors"]]
        parts.append("")
        parts.append("Open the Skills page to see this with Uninstall buttons, or tell me 'remove the skill <name>'.")
        reply = "\n".join(parts) if rep["skills"] or rep["connectors"] else "You have no installed skills or connectors yet."
        return _chat_reply(uid, tid, reply)
    if user["is_admin"] and SELFCHECK_RE.search(text) and not files:
        res = selfcheck.run(user, SITES, HEARING, TAILSCALE)
        BUSY[uid] = False
        if "error" in res:
            reply = res["error"]
        else:
            lines = [selfcheck.spoken(res), ""]
            mark = {"ok": "OK", "warn": "!", "fail": "X"}
            lines += [f"[{mark.get(c['status'], '?')}] {c['name']}: {c['detail']}" + (f" -> {c['fix']}" if c['fix'] and c['status'] != 'ok' else "") for c in res["checks"]]
            reply = "\n".join(lines)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail="Self-check")
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
    # reverse-engineer an app on the phone, or capture its traffic: run the tool directly (owner + linked phone)
    if user["is_admin"] and core.settings(user).get("phone_serial") and not files:
        rea_kind, rea_app = None, None
        mt, ma = REA_TRAFFIC_RE.search(text), REA_PHONE_RE.search(text)
        if mt:
            rea_kind, rea_app = "traffic", mt.group("app")
        elif ma:
            rea_kind, rea_app = "app", ma.group("app")
        elif REA_CONFIRM_RE.match(text) and len(text) < 40:
            prev = core.q("SELECT content FROM messages WHERE user_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (uid,), one=True)
            body = (prev["content"] if prev else "").lower()
            pm = PREV_APP_RE.search(prev["content"] if prev else "")
            if pm and ("reverse" in body or "apk" in body or "rea " in body or "reverse-engineer" in body):
                rea_kind = "traffic" if ("traffic" in body or "connect to" in body or "servers" in body) else "app"
                rea_app = pm.group("app")
        if rea_app:
            app_name = re.sub(r"\s+", " ", rea_app).strip(" .\"'").rstrip(".?!")[:60]
            tool = "rea_phone_traffic" if rea_kind == "traffic" else "rea_phone_app"
            try:
                if REA_FG_RE.search(text) or app_name.lower() in FG_WORDS:   # they mean the app open on screen
                    app_name = phone.foreground_app(user)
                res = tools.execute(user, tool, {"app": app_name}, task_id=tid)
            except phone.PhoneError as e:
                res = {"error": str(e)}
            finally:
                BUSY[uid] = False
            reply = res.get("result") or res.get("error") or "Done."
            core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
            core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
            return {"reply": reply, "task_id": tid, "provider": "built-in"}
    ps = None
    try:
        ps = phone_shortcut(user, text, tid, files)
    except Exception as e:
        ps = (f"The phone request failed: {type(e).__name__}: {e}", False, {"error": str(e)})
    if ps:
        reply, waiting_, res = ps
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="waiting" if waiting_ else ("failed" if "error" in res else "completed"), detail=reply[:200])
        out = {"reply": reply, "task_id": tid, "provider": "built-in"}
        if isinstance(res, dict) and res.get("map") and res.get("latitude") is not None:   # find my phone: the page shows the map
            out["map"] = {"lat": res["latitude"], "lon": res["longitude"], "url": res["map"], "accuracy_m": res.get("accuracy_m"),
                          "title": (core.settings(user).get("phone_model") or "Your phone") + " was last here"}
        return out
    quick = None
    if len(text) < 70 and SITE_RE.search(text):
        site = core.settings(user).get("site")
        if not site:
            quick = "No website is set for this login. Add one under Settings, Look and name."
        else:
            SITES[site] = r = tools.site_check(site)
            quick = (f"{site} is online and answered in {r.get('ms', '?')} milliseconds." if r["ok"] else
                     f"{site} is not answering" + (f" (code {r['status']})." if r.get("status") else ". I could not reach it at all."))
    elif len(text) < 70 and PC_RE.search(text):
        v = core.vitals()
        g = v.get("gpu")
        quick = (f"The processor is at {v['cpu']:.0f} percent and memory is {v['ram_used']:.0f} of {v['ram_total']:.0f} GB used."
                 + (f" The graphics card is using {g['mem_used']:.1f} of {g['mem_total']:.0f} GB and is at {g['temp']:.0f} degrees." if g else "")
                 + (f" The disk has {v['disk']['total'] - v['disk']['used']:.0f} GB free." if v.get("disk") else ""))
    if quick:
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", quick, time.time()))
        core.set_task(tid, status="completed", detail=quick[:200])
        return {"reply": quick, "task_id": tid, "provider": "built-in"}
    im = IMAGE_RE.search(text) if not VIDEO_RE.search(text) and not wants_ebook(text, files) else None
    if im and len(text) < 600:
        want = (im.group(3) or "").strip(" .") or text
        kind = im.group(2).lower()
        if kind in ("logo", "icon", "poster", "banner"):
            want = f"{kind} {('for ' if not want.lower().startswith(('for', 'of')) else '')}{want}" if want != text else text
        shape = "tall" if re.search(r"\b(tall|vertical|portrait|story|tiktok|reel)\b", text, re.I) else (
            "wide" if re.search(r"\b(wide|landscape|horizontal|banner|youtube|cover)\b", text, re.I) else "square")
        core.set_task(tid, detail="Making the picture")
        try:
            res = tools.execute(user, "create_image", {"prompt": want, "shape": shape}, task_id=tid)
        finally:
            BUSY[uid] = False
        ok = "error" not in res
        reply = (f"Your picture is ready. It is saved at {res['saved']}." if ok else res["error"])
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed" if ok else "failed", detail=reply[:200])
        out = {"reply": reply, "task_id": tid, "provider": "gemini" if ok else "built-in"}
        if ok:
            out["image"] = {"url": "/api/images/" + res["file"], "title": want[:80], "saved": res["saved"]}
        return out
    lk = my_link(user, text)
    if lk:
        reply = f"Here is your {lk['name']}. Press the button to open it."
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in", "link": lk["id"]}
    if OPEN_VIDEO_RE.search(text) and not VIDEO_RE.search(text):
        import os
        v = core.q("SELECT * FROM videos WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
        if not v:
            reply = "There is no video yet. Ask me to create one first."
        elif not os.path.exists(v["path"]):
            reply = f"The newest video, {v['title']}, was moved or deleted from {v['path']}."
        else:
            on_pc = ""
            if hasattr(os, "startfile"):
                try:
                    os.startfile(v["path"])
                    on_pc = " It is also playing on the PC screen."
                except OSError:
                    pass
            reply = f"Here is {v['title']}. I have opened the Videos page: press play on the top one." + on_pc
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in", "show": "videos" if v else ""}
    asking = re.match(r"\s*(how|what|why|which|is|are|does|do|should|where|when|who)\b", text, re.I)   # a question, not an order
    if AICLIP_RE.search(text) and re.search(r"\b(create|make|generate|produce|render)\b", text, re.I) and not asking:
        try:
            res = tools.execute(user, "create_ai_clip", aiclip_request(text), task_id=tid)
        finally:
            BUSY[uid] = False
        reply = aiclip_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "FreeVideo", "show": "" if "error" in res else "videos"}
    clone_req = siteclone.request(text) if not files else None
    if clone_req and not asking:
        try:
            if clone_req["mode"] == "copy" and not clone_req["permission"] and not siteclone.is_own(user, clone_req["url"]):
                dom = siteread.host_key(urlparse_(siteread.normalise_url(clone_req["url"])).hostname)
                reply, ok_ = (f"{dom} is not one of your sites, so I will not make an exact copy of it: its words, pictures and "
                              "logos belong to its owner. Two ways forward: if the owner gave you permission, say \"clone "
                              f"{dom}, I have permission\". Or I can build your own site in its layout and style, with your "
                              f"words: say \"rebuild the layout of {dom} for <your business name>\" and tell me what the business does."), False
            else:
                brief = {"brand": clone_req["brand"], "about": clone_req["about"]}
                j = siteclone.start(user, clone_req["mode"], clone_req["url"], brief,
                                    clone_req["max_pages"] or (siteclone.DEFAULT_COPY_PAGES if clone_req["mode"] == "copy" else 4),
                                    clone_req["permission"])
                reply, ok_ = siteclone.started_reply(j), True
        except siteread.SiteError as e:
            reply, ok_ = str(e), False
        finally:
            BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed" if ok_ else "failed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in", "show": "sites" if ok_ else ""}
    site_req = siteread.request(text) if not files else None
    if site_req and not asking:
        try:
            j = siteread.start(user, site_req["url"], site_req["outputs"], text, site_req["max_pages"], site_req["show"])
            reply, ok_ = siteread.started_reply(j), True
        except siteread.SiteError as e:
            reply, ok_ = str(e), False
        finally:
            BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed" if ok_ else "failed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in", "show": "sites" if ok_ else ""}
    if wants_ebook(text, files) and not asking:
        try:
            res = tools.execute(user, "create_ebook", ebook_request(text, files), task_id=tid)
        finally:
            BUSY[uid] = False
        reply = ebook_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        out = {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}
        if "ebook_id" in res:
            out["ebook"] = {"id": res["ebook_id"], "title": res["title"], "pages": res["pages"], "epub": bool(res.get("epub"))}
        return out
    _mrev = REVISE_RE.search(text)
    if _mrev and not asking:                  # "change video #12: make it shorter" -> remake it with that change
        try:
            res = tools.execute(user, "revise_video",
                                {"video_id": int(_mrev.group(2)), "change": _mrev.group(3).strip()}, task_id=tid)
        finally:
            BUSY[uid] = False
        reply = video_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}
    _imgs = [f["path"] for f in files if f["kind"] == "image"]
    if _imgs and (MONTAGE_RE.search(text) or (VIDEO_RE.search(text) and len(_imgs) >= 2)) and not asking:
        # the user gave their own scene pictures to be completed into a narrated video (local + free + resumable)
        try:
            vr = video_request(text)
            low = text.lower()
            style = ""
            for kw, st in (("black and white", "noir"), ("black & white", "noir"), ("noir", "noir"),
                           ("hand-drawn", "paper"), ("hand drawn", "paper"), ("paper", "paper"),
                           ("vhs", "retro"), ("retro", "retro"), ("bold", "bold"), ("graphic", "bold"),
                           ("warm", "warm"), ("golden", "warm"), ("cool", "cool"), ("midnight", "cool"),
                           ("cinematic", "film"), ("film", "film"), ("clean", "clean")):
                if kw in low:
                    style = st
                    break
            scenes = [{"image": p, "text": ""} for p in _imgs]
            res = tools.execute(user, "montage_video",
                                {"scenes": scenes, "title": vr["prompt"][:60], "topic": vr["prompt"],
                                 "format": vr.get("format", "vertical"), "style": style}, task_id=tid)
        finally:
            BUSY[uid] = False
        reply = video_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}
    if VIDEO_RE.search(text) and not asking:  # a long job with fixed steps: run it directly
        try:
            media = [f["path"] for f in files if f["kind"] in ("image", "video")]
            res = tools.execute(user, "create_video", dict(video_request(text), media=media), task_id=tid)
        finally:
            BUSY[uid] = False
        reply = video_reply(res)
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="failed" if "error" in res else "completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}
    hist = [{"role": h["role"], "content": attach.for_brain(h["content"])} for h in
            core.q("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 12", (uid,))[::-1]]
    file_notes = []
    if files:                                   # read, listen to or look at what was attached, for this turn only
        try:
            seen, file_notes = attach.digest(files, hear=transcribe, say=lambda t_: core.set_task(tid, detail=t_))
        except Exception:
            seen, file_notes = "The user attached files, but they could not be opened.", ["I could not open the attached files."]
        if hist and hist[-1]["role"] == "user":
            hist[-1]["content"] = text + "\n\n" + seen
    manual_hits = []
    if manuals.about(user, text):                # a question about the vehicle: hand the brain the manual's best pages
        try:
            core.set_task(tid, detail="Looking it up in the workshop manual")
            manual_hits = manuals.search(user, text, limit=5)
        except Exception:
            manual_hits = []
    specs = tools_for(user, text)
    fast = bool(core.settings(user).get("fast_answers", True)) and not NEEDS_TOOLS.search(text) and len(text) < 400 \
        and not files and not manual_hits          # a manual question gets the full brain: it has pages to read
    if fast:
        specs = None
    LIVE.pop(uid, None)
    msgs = [{"role": "system", "content": system_prompt(user, text) + (
        "\n\nThis turn is a plain question: you have no tools now. Just answer it, briefly and directly." if fast else "")}] + [dict(h) for h in hist]
    if manual_hits:                             # what she looked up herself, not something the user sent
        msgs.insert(len(msgs) - 1, {"role": "system", "content": manuals.for_brain(manual_hits, user)})
    status, waiting, reply, used_tools = "completed", False, "", False
    try:
        for _ in range(10):
            if uid in STOP:
                reply, status = "Stopped.", "failed"
                break
            m = llm_chat(user, msgs, specs, fast=fast, live=True)
            calls = m.get("tool_calls") or []
            if calls:
                used_tools = True
                core.add_step(tid, "model", LAST_MODEL.get(uid, "local brain"), "thinking")
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
                if name == "phone_send_whatsapp" and re.fullmatch(r"[\d\s+()-]{7,}", str(args.get("contact", "")).strip()) \
                        and re.sub(r"\D", "", str(args["contact"]))[-7:] not in re.sub(r"\D", "", text):
                    # the model made a number up: only the person's name, or a number the user gave, is acceptable
                    result = {"error": "Do not invent a phone number. Call phone_send_whatsapp again with the person's name as contact."}
                else:
                    result = tools.execute(user, name, args, task_id=tid)
                waiting = waiting or result.get("status") == "waiting_for_approval"
                msgs.append({"role": "tool", "tool_call_id": c.get("id", name),
                             "content": json.dumps(result, default=str)[:15000 if name == "use_skill" else 6000]})
        else:
            reply = "That took more steps than I allow in one go. Tell me to continue and I'll pick it up."
    except Exception as e:
        status = "failed"
        reply = str(e) if isinstance(e, RuntimeError) else f"Something went wrong: {type(e).__name__}: {e}"
        core.log(uid, reply[:300], "waiting")
    finally:
        BUSY[uid] = False
    if not waiting and not asking and CLAIMS_PENDING.search(reply) and not re.search(r"\bapprov", text, re.I):
        # the model claimed something is waiting for approval when nothing is (not just explaining, and not answering a question)
        reply = ("I did not do that: nothing was prepared or sent. For a WhatsApp, say it like this: "
                 "send a WhatsApp to Sarie saying I am running late." if PHONE_WORDS.search(text) else
                 "I did not do that: nothing is waiting for approval. Tell me again in other words and I will try properly.")
        status = "failed"
    if file_notes:
        reply = (reply + "\n\n" + " ".join(file_notes)).strip()
    if manual_hits and status == "completed":   # the pages themselves, to open and look at the pictures
        reply += "\n\nManual pages:\n" + "\n".join(
            f"{h['section']}, page {h['page']}" + (f" ({h['ref']})" if h["ref"] else "") + f": {h['link']}"
            for h in manual_hits[:4])
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
    if not used_tools and status == "completed":
        # a normal chat (she just answered, did nothing) is not a project - keep it out of the queue and tiles
        core.x("DELETE FROM tasks WHERE id=?", (tid,))
        core.x("DELETE FROM steps WHERE task_id=?", (tid,))
        tid = None
    else:
        core.set_task(tid, status="waiting" if waiting and status == "completed" else status, detail=reply[:200])
    core.log(uid, f"Answered by {LAST_PROVIDER.get(uid, 'local')}", "info")
    return {"reply": reply, "task_id": tid, "provider": LAST_PROVIDER.get(uid, "local")}


@app.post("/api/chat")
def chat(p: dict = Body(...), user=Depends(auth)):
    text = (p.get("message") or "").strip()
    files = [f for f in (attach.get(user, i) for i in (p.get("attachments") or [])[:12] if str(i).isdigit()) if f]
    if not text and files:
        text = "Here " + ("is a file." if len(files) == 1 else f"are {len(files)} files.")
    if not text:
        raise HTTPException(400, "Nothing to do")
    if BUSY.get(user["id"]):
        raise HTTPException(409, "Still busy with the previous job")
    return run_agent(user, text[:4000], files)


@app.post("/api/phone/whatsapp")
def phone_whatsapp(p: dict = Body(...), user=Depends(auth)):
    """The Send a WhatsApp box on the Phone page: who, the message, and any files to go with it."""
    uid = user["id"]
    who, msg = str(p.get("to") or "").strip()[:80], str(p.get("message") or "").strip()[:4000]
    files = [f for f in (attach.get(user, i) for i in (p.get("attachments") or [])[:10] if str(i).isdigit()) if f]
    if not who:
        raise HTTPException(400, "Fill in who it is for.")
    if not msg and not files:
        raise HTTPException(400, "Type a message or attach a file.")
    if BUSY.get(uid):
        raise HTTPException(409, "Still busy with the previous job")
    BUSY[uid] = True
    text = f"Send a WhatsApp to {who}" + (f" saying {msg}" if msg else "")
    tid = core.add_task(uid, text)
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
           (uid, "user", text + "".join(attach.marker(f) for f in files), time.time()))
    try:
        reply, waiting_, res = wa_send(user, tid, who, msg, files)
    except Exception as e:
        reply, waiting_, res = f"I could not send that WhatsApp: {e}", False, {"error": str(e)}
    finally:
        BUSY[uid] = False
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
    core.set_task(tid, status="waiting" if waiting_ else ("failed" if "error" in res else "completed"), detail=reply[:200])
    return {"reply": reply, "task_id": tid, "provider": "built-in"}


# ---------- Bible study ----------
def bible_today(user):
    """The verse the Home page shows: the morning verse until five in the afternoon, the evening verse after that."""
    if not bible.allowed(user):
        return None
    try:
        p = bible.daily(user, "night" if datetime.datetime.now().hour >= 17 else "morning")
        return {"when": p["when"], "ref": p["ref"], "text": p["text"], "version": p["version_name"], "book": p["book"], "chapter": p["chapter"]}
    except bible.BibleError:
        return None


def bible_user(user=Depends(auth)):
    if not bible.allowed(user):
        raise HTTPException(403, "Bible study is not switched on for this login.")
    return user


def _bible_do(fn):
    try:
        return fn()
    except bible.BibleError as e:
        raise HTTPException(400, str(e))


@app.get("/api/bible")
def bible_home(user=Depends(bible_user)):
    code = bible.version_of(user)
    return {"version": code, "versions": bible.versions(), "books": bible.books(code),
            "morning": _bible_do(lambda: bible.daily(user, "morning")), "night": _bible_do(lambda: bible.daily(user, "night")),
            "notes": len(bible.notes(user))}


@app.post("/api/bible/version")
def bible_version(p: dict = Body(...), user=Depends(bible_user)):
    code = str(p.get("version") or "").lower()
    if code not in [v["code"] for v in bible.versions()]:
        raise HTTPException(400, "That translation is not installed.")
    st = core.settings(user)
    st["bible_version"] = code
    core.save_settings(user["id"], st)
    return bible_home(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.get("/api/bible/chapter")
def bible_chapter(book: int, chapter: int, user=Depends(bible_user)):
    out = _bible_do(lambda: bible.chapter(bible.version_of(user), book, chapter))
    out["notes"] = bible.notes(user, book, chapter)
    return out


@app.get("/api/bible/go")
def bible_go(ref: str, user=Depends(bible_user)):
    r = bible.parse(ref)
    if not r:
        raise HTTPException(400, "I could not read that reference. Write it like John 3:16 or Psalms 23.")
    return {"book": r[0], "chapter": r[1], "verse": r[2], "verse_to": r[3]}


@app.get("/api/bible/search")
def bible_search(q: str, user=Depends(bible_user)):
    return _bible_do(lambda: bible.search(bible.version_of(user), q[:120]))


@app.get("/api/bible/notes")
def bible_notes(q: str = "", user=Depends(bible_user)):
    return {"notes": bible.notes(user, q=q or None)}


@app.post("/api/bible/notes")
def bible_note_save(p: dict = Body(...), user=Depends(bible_user)):
    nid = _bible_do(lambda: bible.save_note(user, p.get("text"), ref=p.get("ref"), book=p.get("book"), chapter_=p.get("chapter"),
                                            verse=p.get("verse"), verse_to=p.get("verse_to"), nid=p.get("id")))
    return {"id": nid, "notes": bible.notes(user)}


@app.delete("/api/bible/notes/{nid}")
def bible_note_delete(nid: int, user=Depends(bible_user)):
    bible.delete_note(user, nid)
    return {"notes": bible.notes(user)}


@app.post("/api/bible/ask")
def bible_ask(p: dict = Body(...), user=Depends(bible_user)):
    return _bible_do(lambda: bible.helper(llm_chat, user, p.get("question")))


# ---------- this login's own Gumroad store ----------
def _store_do(fn):
    try:
        return fn()
    except gumroad.GumroadError as e:
        raise HTTPException(400, str(e))


@app.get("/api/store")
def store_get(seen: int = 0, user=Depends(store_user)):
    return gumroad.overview(user, mark_seen=bool(seen))


@app.post("/api/store/connect")
def store_connect(p: dict = Body(...), user=Depends(store_user)):
    _store_do(lambda: gumroad.connect(user, p.get("token")))
    core.log(user["id"], "Gumroad store connected", "done")
    return gumroad.overview(user)


@app.delete("/api/store")
def store_disconnect(user=Depends(store_user)):
    gumroad.disconnect(user)
    return {"connected": False}


@app.post("/api/store/permissions")
def store_permissions(user=Depends(store_user)):
    _store_do(lambda: gumroad.permissions(user))
    return gumroad.overview(user)


def _store_files(user, ids, ebook_id=None):
    """The files for a new product: this login's own uploads, and optionally an ebook Trillion made for this login."""
    files = []
    for i in (ids or [])[:10]:
        f = attach.get(user, i) if str(i).isdigit() else None
        if not f or not os.path.isfile(f["path"]):
            raise HTTPException(400, "One of the attached files is no longer on the PC. Attach it again.")
        files.append((f["path"], f["name"]))
    if ebook_id:
        import ebook
        ebook.init()
        e = core.q("SELECT * FROM ebooks WHERE id=? AND user_id=?", (int(ebook_id), user["id"]), one=True)
        if not e or not os.path.isfile(e["path"]):
            raise HTTPException(400, "That ebook is no longer on the PC.")
        files.append((e["path"], re.sub(r'[\\/:*?"<>|]', "", e["title"] or "ebook").strip()[:120] + ".pdf"))
        epub = os.path.splitext(e["path"])[0] + ".epub"
        if os.path.isfile(epub):
            files.append((epub, re.sub(r'[\\/:*?"<>|]', "", e["title"] or "ebook").strip()[:120] + ".epub"))
    return files


def created_sentence(r):
    p = r["product"]
    files = f" with {r['files']} file{'s' if r['files'] != 1 else ''}" if r["files"] else " with no file attached yet"
    if r["published"]:
        return f"{p['name']} is on sale on Gumroad for {p['shown']}{files}." + (f" {p['url']}" if p["url"] else "")
    return (f"{p['name']} is saved on Gumroad as a draft at {p['shown']}{files}. It is not on sale yet."
            + (" Gumroad says: " + r["warning"] if r["warning"] else " Press Publish on the Store page when you are ready."))


@app.post("/api/store/create")
def store_create(p: dict = Body(...), user=Depends(store_user)):
    try:
        price = float(str(p.get("price") or "0").replace(",", ".").replace("$", "").strip())
    except ValueError:
        raise HTTPException(400, "Type the price as a number, like 9.99.")
    cur = str(p.get("currency") or "").lower()
    if cur and not re.fullmatch(r"[a-z]{3}", cur):
        raise HTTPException(400, "Unknown currency.")
    files = _store_files(user, p.get("attachments"), p.get("ebook"))
    r = _store_do(lambda: gumroad.create_product(user, p.get("name"), int(round(price * (1 if cur == "jpy" else 100))),
                                                 description=p.get("description") or "", files=files, publish=bool(p.get("publish")),
                                                 currency=cur or None, summary=p.get("summary")))
    core.log(user["id"], f"Gumroad: created {r['product']['name']}" + (" and put it on sale" if r["published"] else " as a draft"), "done")
    return {"message": created_sentence(r), "result": r, "store": gumroad.overview(user)}


def app_host(request):
    """The address phones and the laptop reach this PC by: its Tailscale address when there is one."""
    ts = TAILSCALE.get("v") or {}
    return ts.get("ip") or ts.get("dns") or (request.url.hostname if request.url.hostname not in ("127.0.0.1", "localhost") else "localhost")


@app.get("/api/apps")
def apps_list(request: Request, user=Depends(auth)):
    return apps.listing(user, app_host(request), (TAILSCALE.get("v") or {}).get("dns"))


@app.post("/api/apps/docker")
def apps_docker(p: dict = Body(...), user=Depends(admin)):
    try:
        if p.get("do") == "install":
            apps.install_docker()
            return {"ok": True, "note": "Docker Desktop is installing. Windows may ask for permission and a restart; then press Start Docker."}
        if p.get("do") == "start":
            return {"ok": apps.start_docker_desktop(), "note": "Docker is starting. It takes a minute or two."}
    except apps.AppError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "Unknown")


@app.post("/api/apps/safety")
def apps_safety(p: dict = Body(...), request: Request = None, user=Depends(admin)):
    """The owner's choices: how much memory Docker may use, and whether the apps open on the home network."""
    try:
        old = apps.prefs()
        new = apps.save_prefs(p.get("private"), p.get("memory"))
        out = {"prefs": new, "notes": []}
        if p.get("memory") is not None:
            out["file"] = apps.write_wslconfig(new["memory"])
            if p.get("restart"):
                apps.restart_docker()
                out["notes"].append("Docker is restarting with the new memory limit. The apps come back by themselves in a minute or two.")
            else:
                out["notes"].append("Saved. It takes effect the next time Docker restarts (or the PC restarts).")
        if p.get("private") is not None and new["private"] != old["private"]:
            res = apps.apply_access(app_host(request))
            out["notes"].append(("Only this PC and your Tailscale devices can open the apps now." if new["private"]
                                 else "Any device on your home network can open the apps now.")
                                + (f" Updated: {', '.join(res['done'])}." if res["done"] else "")
                                + (f" Could not update: {'; '.join(res['failed'])}." if res["failed"] else ""))
        return out
    except apps.AppError as e:
        raise HTTPException(400, str(e))


@app.get("/api/backup")
def backup_get(user=Depends(admin)):
    st = core.settings(user)
    return {"job": dict(backup.JOB), "backups": backup.find_backups(), "target": st.get("backup_target") or backup.default_target(),
            "weekly": bool(st.get("backup_weekly")), "last": st.get("last_backup"), "report": backup.report(),
            "apps_waiting": bool(backup.pending_apps()), "tailscale": TAILSCALE.get("v") or {}}


@app.post("/api/backup")
def backup_make(p: dict = Body(...), user=Depends(admin)):
    try:
        backup.start(str(p.get("target") or ""), p.get("apps", True) is not False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/backup/settings")
def backup_settings(p: dict = Body(...), user=Depends(admin)):
    st = core.settings(user)
    if "weekly" in p:
        st["backup_weekly"] = bool(p["weekly"])
    if p.get("target"):
        st["backup_target"] = str(p["target"])
    core.save_settings(user["id"], st)
    return {"ok": True}


@app.post("/api/backup/restore")
def backup_restore(p: dict = Body(...), user=Depends(admin)):
    try:
        backup.request_restore(str(p.get("path") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "note": "Restoring. Trillion restarts in a few seconds; sign in again when the page comes back."}


@app.post("/api/backup/apps")
def backup_apps_restore(request: Request, user=Depends(admin)):
    try:
        return backup.restore_apps(app_host(request))
    except (ValueError, apps.AppError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/storage")
def storage_get(months: int = 12, user=Depends(admin)):
    months = months if months in (3, 6, 12, 24, 36) else 12
    return dict(storage.scan(months), job=dict(storage.JOB), history=storage.history())


@app.get("/api/storage/job")
def storage_job(user=Depends(admin)):
    return dict(storage.JOB)


@app.post("/api/storage/move")
def storage_move(p: dict = Body(...), user=Depends(admin)):
    try:
        storage.start_move(p.get("groups") or [], str(p.get("target") or ""), int(p.get("months") or 12))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/storage/undo")
def storage_undo(p: dict = Body(...), user=Depends(admin)):
    try:
        return storage.undo(str(p.get("id") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/storage/root")
def storage_root(p: dict = Body(...), user=Depends(admin)):
    try:
        return {"root": storage.set_root(str(p.get("target") or ""))}
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/apps/{key}/install")
def apps_install(key: str, request: Request, user=Depends(admin)):
    try:
        apps.install(key, app_host(request))
    except apps.AppError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/apps/{key}/{what}")
def apps_action(key: str, what: str, p: dict = Body(default={}), user=Depends(auth)):
    try:
        if what == "connect":
            apps.connect(user, key, p)
            return {"ok": True}
        if what == "disconnect":
            apps.disconnect(user, key)
            return {"ok": True}
        if not user["is_admin"]:
            raise HTTPException(403, "Only the owner can start, stop or remove apps.")
        if what in ("start", "stop", "remove", "erase"):
            return {"ok": apps.action(key, what)}
    except apps.AppError as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "Unknown")


@app.get("/api/clones")
def clones_list(user=Depends(auth)):
    return {"jobs": siteclone.jobs(user), "own": sorted(siteclone.own_domains(user)), "brand": core.settings(user).get("brand") or "",
            "gemini": bool(core.get_secret(user["id"], "gemini"))}


@app.post("/api/clones")
def clones_start(p: dict = Body(...), user=Depends(auth)):
    try:
        return siteclone.start(user, p.get("mode") or "copy", p.get("url"),
                               {"brand": p.get("brand"), "about": p.get("about"), "contact": p.get("contact")},
                               p.get("max_pages") or (siteclone.DEFAULT_COPY_PAGES if p.get("mode") != "rebuild" else 4),
                               bool(p.get("permission")), bool(p.get("my_colour")), bool(p.get("ai_pictures")))
    except siteread.SiteError as e:
        raise HTTPException(400, str(e))


@app.get("/api/clones/{jid}")
def clones_one(jid: int, user=Depends(auth)):
    j = siteclone.job(user, jid)
    if not j:
        raise HTTPException(404, "Not found")
    return j


@app.post("/api/clones/{jid}/stop")
def clones_stop(jid: int, user=Depends(auth)):
    return {"ok": siteclone.stop(user, jid)}


@app.delete("/api/clones/{jid}")
def clones_forget(jid: int, user=Depends(auth)):
    siteclone.forget(user, jid)
    return {"ok": True}


@app.get("/api/gensites")
def gensites_list(user=Depends(auth)):
    return {"sites": web.listing(user)}


@app.post("/api/gensites/from-link")
def gensites_from_link(p: dict = Body(...), user=Depends(auth)):
    link = (p.get("link") or "").strip()
    if not link:
        raise HTTPException(400, "Paste a Google Maps link first.")
    info = maps.extract(user, link)
    return {"business": info.get("business") or "", "category": info.get("category") or "",
            "location": info.get("location") or "", "address": info.get("address") or "",
            "phone": info.get("phone") or "", "hours": info.get("hours") or "", "about": info.get("about") or "",
            "rating": info.get("rating"), "has_website": info.get("has_website"),
            "photos": len(info.get("photos") or []), "have_key": bool(info.get("_have_key")),
            "api": info.get("_api") or "", "resolved": info.get("_resolved") or ""}


@app.post("/api/gensites")
def gensites_start(p: dict = Body(...), user=Depends(auth)):
    if not (p.get("business") or p.get("name") or p.get("link")):
        raise HTTPException(400, "Give a business name or paste a Google Maps link.")
    return web.start(user, p)


@app.get("/api/gmail")
def gmail_get(user=Depends(auth)):
    return mail.status(user)


@app.post("/api/gmail/connect")
def gmail_connect(p: dict = Body(...), user=Depends(auth)):
    return mail.connect(user, p.get("address"), p.get("app_password"))


@app.post("/api/gmail/disconnect")
def gmail_disconnect(user=Depends(auth)):
    return mail.disconnect(user)


@app.get("/api/leads")
def leads_list(user=Depends(auth)):
    return {"leads": leads.listing(user), "stats": leads.stats(user), "gmail": mail.status(user),
            "funnel": _funnel_state()}


def _funnel_state():
    st = apps.funnel_status()
    dns = (TAILSCALE.get("v") or {}).get("dns") or ""
    st["public_base"] = (f"https://{dns}/site/" if dns else "")
    return st


@app.get("/api/funnel")
def funnel_get(user=Depends(auth)):
    return _funnel_state()


@app.post("/api/funnel")
def funnel_set(p: dict = Body(...), user=Depends(admin)):
    res = apps.funnel_sites(8787, bool(p.get("on", True)))
    return {**res, **_funnel_state()}


@app.post("/api/leads/find")
def leads_find(p: dict = Body(...), user=Depends(auth)):
    q = (p.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "Say what to search for, e.g. 'barbers in Roodepoort'.")
    return leads.start_find(user, q, min(int(p.get("limit") or 10), 20))


@app.delete("/api/leads/{lid}")
def leads_delete(lid: int, user=Depends(auth)):
    return leads.delete(user, lid)


@app.patch("/api/leads/{lid}")
def leads_update(lid: int, p: dict = Body(...), user=Depends(auth)):
    if p.get("status"):
        leads.set_status(user, lid, p["status"])
    return leads.update(user, lid, **{k: v for k, v in p.items() if k != "status"})


@app.post("/api/leads/{lid}/send")
def leads_send(lid: int, request: Request, user=Depends(auth)):
    # the preview link goes to an outside prospect, so use the public Tailscale DNS name (not the private IP)
    dns = (TAILSCALE.get("v") or {}).get("dns") or app_host(request)
    return leads.send_pitch(user, lid, dns, "https")


@app.post("/api/leads/{lid}/whatsapp")
def leads_whatsapp(lid: int, request: Request, user=Depends(auth)):
    # WhatsApp the sample link to the lead's phone (best for no-website leads, who have a mobile but no email)
    dns = (TAILSCALE.get("v") or {}).get("dns") or app_host(request)
    return leads.send_whatsapp(user, lid, dns, "https")


@app.post("/api/gensites/{sid}/revise")
def gensites_revise(sid: int, p: dict = Body(...), user=Depends(auth)):
    opts = p.get("opts") if isinstance(p.get("opts"), dict) else {}
    change = (p.get("change") or "").strip()
    # the CHANGE panel may send only structured choices (no free text) — that's still a valid change
    has_opts = any(opts.get(k) for k in ("look", "colour", "color", "mode", "layout", "font", "pricelist")) \
        or isinstance(opts.get("addons"), list)
    if not change and not has_opts:
        raise HTTPException(400, "Choose something to change or type an instruction.")
    return web.start_revise(user, sid, change, opts=opts)


@app.post("/api/gensites/{sid}/pitch-video")
def gensite_pitch_video(sid: int, p: dict = Body(default={}), user=Depends(auth)):
    import pitchvideo
    return pitchvideo.start(user, sid, p.get("scores"), p.get("voice", True))


@app.get("/api/gensites/{sid}/pitch-video")
def gensite_pitch_video_status(sid: int, download: int = 0, user=Depends(auth)):
    import pitchvideo
    st = pitchvideo.status(user, sid)
    if download:
        if st.get("status") != "done" or not st.get("file"):
            raise HTTPException(404, "No pitch video yet")
        return FileResponse(st["file"], media_type="video/mp4", filename=f"pitch-{sid}.mp4")
    return {k: v for k, v in st.items() if k != "file"} | {"ready": st.get("status") == "done"}


@app.post("/api/gensites/{sid}/email-pitch")
def gensite_email_pitch(sid: int, request: Request, p: dict = Body(default={}), user=Depends(auth)):
    import leads
    dns = (TAILSCALE.get("v") or {}).get("dns") or app_host(request)
    return leads.send_for_site(user, sid, (p.get("email") or "").strip(), dns, "https")


@app.post("/api/gensites/{sid}/whatsapp-pitch")
def gensite_whatsapp_pitch(sid: int, request: Request, p: dict = Body(default={}), user=Depends(auth)):
    import leads
    dns = (TAILSCALE.get("v") or {}).get("dns") or app_host(request)
    # background: driving WhatsApp on the phone takes several seconds, so don't block the request
    return leads.start_whatsapp_for_site(user, sid, (p.get("phone") or "").strip(), dns, "https")


@app.get("/api/gensites/{sid}/whatsapp-pitch")
def gensite_whatsapp_pitch_status(sid: int, user=Depends(auth)):
    import leads
    return leads.whatsapp_status(user, sid)


@app.post("/api/gensites/{sid}/before-after")
def gensite_before_after(sid: int, p: dict = Body(default={}), user=Depends(auth)):
    import beforeafter
    return beforeafter.start(user, sid, (p.get("old_url") or "").strip())


@app.get("/api/gensites/{sid}/before-after")
def gensite_before_after_status(sid: int, download: int = 0, user=Depends(auth)):
    import beforeafter
    if download:
        f = beforeafter.card_path(user, sid)
        if not f:
            raise HTTPException(404, "No card yet")
        return FileResponse(f, media_type="image/jpeg", filename=f"before-after-{sid}.jpg")
    return beforeafter.status(user, sid)


@app.post("/api/prospect/score")
def prospect_score(p: dict = Body(...), user=Depends(auth)):
    import prospect
    urls = p.get("urls")
    if isinstance(urls, str):
        urls = [u for u in urls.replace(",", " ").split() if u.strip()]
    return prospect.rate(urls or [])


@app.delete("/api/gensites/{sid}")
def gensites_forget(sid: int, user=Depends(auth)):
    return web.forget(user, sid)


@app.get("/api/gensites/{sid}/file")
def gensites_file(sid: int, user=Depends(auth)):
    f = web.html_file(user, sid)
    if not f:
        raise HTTPException(404, "Not found")
    name = Path(f).parent.name + ".html"
    return FileResponse(f, media_type="text/html", filename=name)


@app.get("/api/gensites/{sid}/thumb")
def gensites_thumb(sid: int, user=Depends(auth)):
    row = web._row(user, sid)
    if not row or not row["thumb"] or not os.path.exists(row["thumb"]):
        raise HTTPException(404, "No preview")
    return FileResponse(row["thumb"], media_type="image/jpeg")


@app.get("/site/{token}/reply")
@app.get("/site/site/{token}/reply")
def gensite_reply(token: str, a: str = "", user=None):
    """Public accept/decline from the pitch email or deck. Declared before the file catch-all. Records the
    reply against the lead (so it shows on the owner's dashboard) and returns a thank-you page."""
    import leads
    r = leads.record_reply(token, a)
    return HTMLResponse(leads.reply_page(a, r.get("business")))


@app.get("/site/{token}/quote")
@app.get("/site/site/{token}/quote")
def gensite_quote(token: str, request: Request):
    """Public 'request a price for more features' form submit from the pitch deck. GET so the plain HTML form
    works over the Funnel with no JS. Records the request against the lead and shows a thank-you page."""
    import leads
    qp = request.query_params
    feats = qp.getlist("f")
    r = leads.record_quote(token, feats, qp.get("note", ""), qp.get("contact", ""))
    return HTMLResponse(leads.quote_page(r.get("business"), r.get("features") or [], r.get("contact") or ""))


@app.api_route("/site/{token}/__api/{action}", methods=["GET", "POST"])
@app.api_route("/site/site/{token}/__api/{action}", methods=["GET", "POST"])
async def site_tier2_api(token: str, action: str, request: Request):
    """Public, token-scoped backend for a generated site's tier-2 add-ons (shop, members, booking, forum).
    Declared before the file catch-all so '__api/...' isn't served as a file. Reachable over the Funnel
    (only /site/ is exposed) because the site calls it with a relative URL."""
    import siteapps
    data = {}
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        data = dict(request.query_params)
    out = await run_in_threadpool(siteapps.handle, token, action, data, request.method)
    return JSONResponse(out)


@app.get("/site/{token}/{path:path}")
@app.get("/site/site/{token}/{path:path}")   # Tailscale Funnel --set-path doesn't strip /site, so it arrives doubled.
# The doubled route is declared innermost so FastAPI registers it FIRST — otherwise /site/{token} greedily
# captures /site/site/TOKEN/ as token="site" and the preview 404s ("Not found").
def gensite_preview(token: str, path: str = ""):
    """Public preview link for a generated site: served sandboxed, same as clone previews."""
    p = web.preview_file(token, path)
    if not p:
        raise HTTPException(404, "Not found")
    return FileResponse(p, headers={"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-same-origin",
                                    "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@app.get("/clone/{token}/{path:path}")
def clone_preview(token: str, path: str = ""):
    """A private preview link for one clone. The page runs walled off (sandboxed): it cannot see Trillion,
    its sign-in or any other page, so a copied site's own scripts can do no harm here."""
    p = siteclone.preview_file(token, path)
    if not p:
        raise HTTPException(404, "Not found")
    return FileResponse(p, headers={"Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups",
                                    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                                    "Cache-Control": "no-store"})


@app.get("/api/gensites/{sid}/activity")
def gensite_activity(sid: int, user=Depends(auth)):
    import siteapps
    return siteapps.activity(user, sid)


@app.post("/api/gensites/{sid}/activity")
def gensite_activity_set(sid: int, p: dict = Body(...), user=Depends(auth)):
    import siteapps
    return siteapps.set_status(user, sid, (p.get("kind") or "").strip(), p.get("id"), (p.get("status") or "").strip())


@app.get("/api/sites")
def sites_list(user=Depends(auth)):
    return {"jobs": siteread.jobs(user), "choices": list(siteread.PAGE_CHOICES), "default": siteread.DEFAULT_PAGES}


@app.post("/api/sites")
def sites_start(p: dict = Body(...), user=Depends(auth)):
    try:
        return siteread.start(user, p.get("url"), p.get("outputs") or [], str(p.get("focus") or "")[:1000],
                              p.get("max_pages") or siteread.DEFAULT_PAGES, bool(p.get("show")))
    except siteread.SiteError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sites/{jid}")
def sites_one(jid: int, user=Depends(auth)):
    j = siteread.job(user, jid)
    if not j:
        raise HTTPException(404, "Not found")
    return j


@app.post("/api/sites/{jid}/stop")
def sites_stop(jid: int, user=Depends(auth)):
    return {"ok": siteread.stop(user, jid)}


@app.delete("/api/sites/{jid}")
def sites_forget(jid: int, user=Depends(auth)):
    siteread.forget(user, jid)
    return {"ok": True}


def project_briefing(user):
    """What is going on, per business, as a short spoken update."""
    uid, ds = user["id"], core.day_start()
    yday = ds - 86400
    facts = []
    st = core.settings(user)
    brand = st.get("brand") or user["name"]
    done = core.q("SELECT title FROM tasks WHERE user_id=? AND status='completed' AND created>=? ORDER BY id DESC LIMIT 6", (uid, yday))
    failed = core.q("SELECT title FROM tasks WHERE user_id=? AND status='failed' AND created>=? ORDER BY id DESC LIMIT 4", (uid, yday))
    running = core.q("SELECT title FROM tasks WHERE user_id=? AND status='in_progress' ORDER BY id DESC LIMIT 4", (uid,))
    facts.append(f"Business: {brand}" + (f", website {st['site']}" if st.get("site") else ""))
    if st.get("site") and SITES.get(st["site"]):
        facts.append("Website is " + ("up" if SITES[st["site"]].get("ok") else "DOWN"))
    if done:
        facts.append("Done since yesterday: " + "; ".join(r["title"][:60] for r in done))
    if failed:
        facts.append("Did not work: " + "; ".join(r["title"][:60] for r in failed))
    if running:
        facts.append("Still running: " + "; ".join(r["title"][:60] for r in running))
    try:
        if store_allowed(user) and gumroad.token(user):
            facts.append("Store: " + gumroad.say_sales(user, "days30")[:200])
    except Exception:
        pass
    for v in core.q("SELECT title FROM videos WHERE user_id=? AND created>=? LIMIT 3", (uid, yday)) if _has_table("videos") else []:
        facts.append(f"New video: {v['title'][:60]}")
    if user["is_admin"]:
        for u in core.q("SELECT * FROM users WHERE id!=?", (uid,)):
            s2 = core.settings(u)
            n = core.q("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND created>=?", (u["id"], yday), one=True)["c"]
            facts.append(f"{s2.get('brand') or u['name']} ({u['name']}): {n} job{'s' if n != 1 else ''} since yesterday"
                         + ("" if not s2.get("site") or not SITES.get(s2["site"]) or SITES[s2["site"]].get("ok") else ", website DOWN"))
    waiting = nudge.collect(user, SITES)
    if waiting:
        facts.append("Waiting on you: " + "; ".join(i["text"][:90] for i in waiting[:6]))
    plain = " ".join(facts)
    try:
        m = llm_chat(user, [
            {"role": "system", "content": f"You are {st.get('assistant_name') or 'Trillion'}, phoning {user['name']} with a morning update. "
             "Turn these facts into a spoken update of at most 120 words: warm, plain sentences, no lists, no symbols, no web "
             "addresses. Start with what needs them, then each business in a sentence or two. Use only these facts."},
            {"role": "user", "content": plain}], fast=True)
        text = re.sub(r"[*#_`]|https?://\S+", "", (m.get("content") or "")).strip()
        if len(text) > 40:
            return text
    except Exception:
        pass
    return plain


def _prep_phone_for_call(user):
    """Before Trillion rings, wake and unlock the phone and bring Linphone to the front, so the call
    actually comes through instead of sitting behind the lock screen. Best-effort: never blocks the call."""
    if not core.settings(user).get("phone_serial"):
        return
    try:
        phone.unlock(user, explicit=True)       # explicit: unlock for the call if a code is saved
    except Exception as e:
        core.log(user["id"], "Before calling: could not unlock the phone - " +
                 (str(e.args[0]) if getattr(e, "args", None) else type(e).__name__)
                 + " (save an unlock code on the Phone page so I can unlock it for calls)", "waiting")
    try:
        phone.open_app(user, "linphone")         # wakes/unlocks (again, harmless) and opens Linphone
    except Exception:
        try:
            phone.sh(user, "monkey -p org.linphone -c android.intent.category.LAUNCHER 1")
        except Exception:
            pass
    time.sleep(2.0)                              # let Linphone come to the foreground and register before the call


def place_call(user, topic="projects", items=None, opening=None):
    """Phone the login's Linphone. Returns how it went; never raises."""
    name = core.settings(user).get("assistant_name") or "Trillion"
    if opening:
        opening = opening
    elif items:
        opening = (f"Hi {user['name']}, it's {name}. " + (f"{len(items)} things need you. " if len(items) > 1 else "")
                   + " ".join(i["text"] for i in items[:4]) + ". What would you like me to do?")
    elif topic and topic != "projects":
        opening = f"Hi {user['name']}, it's {name}. You asked me to call you about {topic}. Anything you want me to do?"
    else:
        opening = f"Good {'morning' if datetime.datetime.now().hour < 12 else 'day'} {user['name']}, it's {name}. " + project_briefing(user) + " Anything you want me to do?"

    _prep_phone_for_call(user)
    answer = call_answer(user)
    ts = TAILSCALE.get("v") or {}
    try:
        res = voicecall.conversation(user, opening, answer, my_ip=ts.get("ip") or None,
                                     log=lambda w: core.log(user["id"], f'On the call you said: "{w[:120]}"', "voice"))
    except voicecall.CallError as e:
        res = {"result": "failed", "error": str(e), "heard": []}
    except Exception as e:
        res = {"result": "failed", "error": f"{type(e).__name__}: {e}", "heard": []}
    core.log(user["id"], "Call to your phone: " + {"answered": "answered", "no answer": "not answered", "busy": "busy",
                                                    "declined": "declined", "unreachable": "the phone could not be reached"}.get(res["result"], res.get("error", res["result"]))[:200],
             "done" if res["result"] == "answered" else "waiting")
    return res


def call_answer(user):
    """What Trillion does with something you say on a call: the normal chat, with no approvals except for payments."""
    def answer(words):
        if BUSY.get(user["id"]):
            return "I am busy with another job right now. Ask me again in a minute."
        try:
            with tools.on_call():
                return run_agent(user, words).get("reply") or "Done."
        except Exception as e:
            return f"That did not work: {type(e).__name__}."
    return answer


def incoming_call(user, call):
    """You phoned Trillion from Linphone."""
    name = core.settings(user).get("assistant_name") or "Trillion"
    part = "morning" if datetime.datetime.now().hour < 12 else ("afternoon" if datetime.datetime.now().hour < 18 else "evening")
    opening = f"Good {part} {user['name']}, it's {name}. What can I do for you?"
    res = voicecall.take_call(user, call, opening, call_answer(user),
                              log=lambda w: core.log(user["id"], f'On the call you said: "{w[:120]}"', "voice"))
    if res["result"] == "answered":
        core.log(user["id"], f"You called {name}: {len(res['heard'])} thing{'s' if len(res['heard']) != 1 else ''} said", "done")
    elif res["result"] == "failed":
        core.log(user["id"], f"Your call to {name} did not work: {res.get('error', '')}"[:200], "info")


voicecall.ON_INCOMING = incoming_call


def call_in_background(user, topic="projects", items=None, fallback=None, opening=None):
    def go():
        res = place_call(user, topic, items, opening=opening)
        if res["result"] != "answered" and fallback:
            try:
                fallback()
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()


# "call me when the project is done": each entry waits for a running job to finish, then calls once.
DONE_CALLS = {}   # uid -> {"watch": set(task_ids) or None (any future task), "exclude": set(task_ids), "topic": str}


def announce_done_bg(user):
    """When a 'call me when done' job finishes, also announce it out loud on a Google Home (best-effort)."""
    if not core.settings(user).get("call_done_announce", True):
        return

    def go():
        try:
            import cast
            if cast.available():
                cast.announce(f"{user['name']}, the job you asked about is finished.",
                              core.settings(user).get("call_done_device") or None)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def _call_on_task_done(tid, status):
    """Fires when any task ends. If the user asked to be called when their work finished, ring them now."""
    for uid, p in list(DONE_CALLS.items()):
        if tid in p.get("exclude", ()):
            continue
        ws = p.get("watch")
        if ws is not None and tid not in ws:
            continue
        DONE_CALLS.pop(uid, None)
        u = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
        if not u:
            continue
        announce_done_bg(u)          # also say it out loud on a Google Home, if set up
        opening = "Hi, you asked me to call when the work was done - it's finished. Here's where things stand."
        call_in_background(u, p.get("topic", "projects"), opening=opening,
                           fallback=lambda u=u: watch.send(u, "Your job is done",
                                                           "The work you asked about is finished; I tried to call you. The update is in the chat."))


if _call_on_task_done not in core.ON_TASK_END:
    core.ON_TASK_END.append(_call_on_task_done)


@app.get("/api/nudge")
def nudge_get(user=Depends(auth)):
    return nudge.state(user, SITES)


@app.post("/api/nudge/prefs")
def nudge_prefs(p: dict = Body(...), user=Depends(auth)):
    nudge.save_prefs(user, p)
    return nudge.state(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True), SITES)


@app.post("/api/nudge/done")
def nudge_done(p: dict = Body(...), user=Depends(auth)):
    nudge.mark_done(user, str(p.get("key") or "")[:200])
    return nudge.state(user, SITES)


@app.post("/api/nudge/test")
def nudge_test(user=Depends(auth)):
    res = nudge.check(user, SITES, force=True) or {"sent": False, "items": 0}
    return dict(nudge.state(user, SITES), result=res)


@app.get("/api/morning")
def morning_get(user=Depends(auth)):
    return morning.state(user)


@app.post("/api/morning/prefs")
def morning_prefs(p: dict = Body(...), user=Depends(auth)):
    morning.save_prefs(user, p)
    return morning.state(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.post("/api/morning/now")
def morning_now(user=Depends(auth)):
    res = morning.send_now(user, SITES)
    return dict(morning.state(user), result=res)


@app.get("/api/scrape")
def scrape_get(user=Depends(auth)):
    return scrape.state()


@app.post("/api/scrape/install")
def scrape_install(user=Depends(admin)):
    return scrape.start_install(user)


@app.get("/api/mpt")
def mpt_get(user=Depends(auth)):
    import mpt
    return mpt.state()


@app.post("/api/mpt/install")
def mpt_install(user=Depends(admin)):
    import mpt
    return mpt.start_install(user)


@app.get("/api/desktop")
def desktop_get(user=Depends(auth)):
    import desktop
    return dict(desktop.state(), enabled=bool(core.settings(user).get("desktop_control")))


@app.post("/api/desktop/install")
def desktop_install(user=Depends(admin)):
    import desktop
    return desktop.start_install(user)


@app.get("/api/webread")
def webread_get(user=Depends(auth)):
    import webread
    return webread.state()


@app.post("/api/webread/install")
def webread_install(user=Depends(admin)):
    import webread
    return webread.start_install(user)


@app.get("/api/transcript")
def transcript_get(user=Depends(auth)):
    import transcript
    return transcript.state()


@app.post("/api/transcript/install")
def transcript_install(user=Depends(admin)):
    import transcript
    return transcript.start_install(user)


@app.post("/api/montage")
def montage_build(p: dict = Body(...), user=Depends(auth)):
    """Build a video from the user's own scene pictures (Videos page). Scenes are uploaded images (by
    attachment id) each with optional narration; empty lines are written from the topic. Runs in the
    background and reports progress on the task widget, like the other long video jobs."""
    raw = p.get("scenes") or []
    scenes = []
    for sc in raw[:12]:
        f = attach.get(user, sc.get("id")) if str(sc.get("id") or "").isdigit() else None
        if f and f["kind"] == "image" and os.path.exists(f["path"]):
            scenes.append({"image": f["path"], "text": str(sc.get("text") or "").strip()})
    if not scenes:
        raise HTTPException(400, "Add a picture for each scene first (two or more).")
    title = (p.get("title") or "").strip() or "Scene montage"
    topic = (p.get("topic") or "").strip()
    fmt = (p.get("format") or "vertical").strip()
    style = (p.get("style") or "").strip()
    tid = core.add_task(user["id"], f"Scene montage: {title}")

    def go():
        try:
            tools.execute(user, "montage_video",
                          {"scenes": scenes, "title": title, "topic": topic, "format": fmt, "style": style}, task_id=tid)
            core.set_task(tid, status="completed", detail="Your scene video is ready on the Videos page.")
        except Exception as e:
            core.set_task(tid, status="failed", detail=f"{type(e).__name__}: {e}"[:200])
    threading.Thread(target=go, daemon=True).start()
    return {"task_id": tid, "scenes": len(scenes)}


@app.get("/api/sitegrab")
def sitegrab_get(user=Depends(auth)):
    import sitegrab
    return sitegrab.state()


@app.post("/api/sitegrab/install")
def sitegrab_install(user=Depends(admin)):
    import sitegrab
    return sitegrab.start_install(user)


@app.post("/api/scrape/run")
def scrape_run(p: dict = Body(...), user=Depends(auth)):
    mode = (p.get("mode") or "page").strip()
    prompt = (p.get("prompt") or "").strip()
    if mode == "page":
        source = (p.get("url") or "").strip()
    elif mode == "multi":
        source = [u for u in re.split(r"\s+", (p.get("urls") or "").strip()) if u]
    else:
        source = None
    return scrape.run(mode, prompt, source)       # sync def endpoint: runs in a threadpool, won't block the server


@app.get("/api/call")
def call_get(user=Depends(auth)):
    ts = TAILSCALE.get("v") or {}
    reg = voicecall.registered(user)
    return {"prefs": voicecall.prefs(user), "plans": voicecall.plans(user), "busy": voicecall.busy(user["id"]),
            "registered": {"ip": reg["ip"], "port": reg["port"], "agent": reg["agent"]} if reg else None,
            "registrar": dict(voicecall.REGISTRAR), "pc_ip": ts.get("ip") or "", "sip_user": user["name"].lower(),
            "devices": [d for d in ts.get("devices", []) if d.get("ip")] if user["is_admin"] else [],
            "tailscale": bool(ts.get("ip"))}


@app.post("/api/call/prefs")
def call_prefs(p: dict = Body(...), user=Depends(auth)):
    try:
        voicecall.save_prefs(user, p)
    except voicecall.CallError as e:
        raise HTTPException(400, str(e))
    return call_get(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.post("/api/call/plan")
def call_plan(p: dict = Body(...), user=Depends(auth)):
    try:
        if p.get("delete"):
            voicecall.delete_plan(user, p["delete"])
        else:
            voicecall.add_plan(user, str(p.get("at") or ""), str(p.get("days") or "daily"), str(p.get("topic") or "projects"))
    except voicecall.CallError as e:
        raise HTTPException(400, str(e))
    return call_get(user)


@app.post("/api/call/test")
def call_test(user=Depends(auth)):
    if not voicecall.prefs(user)["target"] and not voicecall.registered(user):
        raise HTTPException(400, "Your phone is not signed in yet. Set up Linphone as shown in the steps, or choose the phone.")
    if voicecall.busy(user["id"]):
        raise HTTPException(409, "A call is already on. Hang up in Linphone, or wait a moment and try again.")
    res = place_call(user, "a test call. If you can hear me, say hello")
    return res


@app.get("/api/meeting")
def meeting_get(user=Depends(auth)):
    return meeting.state(user)


@app.post("/api/meeting")
def meeting_set(p: dict = Body(...), user=Depends(auth)):
    if p.get("on"):
        secs = meeting.DURATIONS.get(str(p.get("for") or "1h"), 3600)
        meeting.start(user, seconds=secs)
    else:
        meeting.finish(user, quiet=True)
    return meeting.state(user)


@app.post("/api/meeting/prefs")
def meeting_prefs(p: dict = Body(...), user=Depends(auth)):
    meeting.save_prefs(user, p)
    return meeting.state(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.get("/api/watch")
def watch_get(user=Depends(auth)):
    return watch.state(user)


@app.post("/api/watch")
def watch_set(p: dict = Body(...), user=Depends(auth)):
    watch.save_prefs(user, p)
    return watch.state(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.post("/api/watch/test")
def watch_test(user=Depends(auth)):
    if not core.settings(user).get("phone_serial"):
        raise HTTPException(400, "Link your phone first on this page. The watch shows what arrives on the phone.")
    r = watch.send(user, "Trillion", f"Test alert for {user['name']}. If you see this on your watch, alerts work.", test=True)
    if not r["ok"]:
        raise HTTPException(400, r["error"])
    return {"ok": True}


@app.post("/api/store/every")
def store_every(p: dict = Body(...), user=Depends(store_user)):
    try:
        v = int(p.get("every"))
    except (TypeError, ValueError):
        v = 0
    if v not in gumroad.CHOICES:
        raise HTTPException(400, "Choose 15 minutes, 1 hour, once a day or once a week.")
    st = core.settings(user)
    st["store_every"] = v
    core.save_settings(user["id"], st)
    return gumroad.overview(user)


@app.post("/api/store/check")
def store_check(user=Depends(store_user)):
    _store_do(lambda: gumroad.sync(user))
    return gumroad.overview(user)


def _own_product(user, pid):
    p = next((x for x in gumroad.overview(user).get("products", []) if x["id"] == pid), None)
    if not p:
        raise HTTPException(404, "That product is not in your store.")
    return p


@app.post("/api/store/product")
def store_product(p: dict = Body(...), user=Depends(store_user)):
    prod, act = _own_product(user, str(p.get("id") or "")), p.get("action")
    if act in ("publish", "unpublish"):
        _store_do(lambda: gumroad.set_published(user, prod["id"], act == "publish"))
    elif act == "update":
        price = None
        if str(p.get("price") or "").strip() != "":
            try:
                price = int(round(float(str(p["price"]).replace(",", ".").strip()) * (1 if prod["currency"] == "jpy" else 100)))
            except ValueError:
                raise HTTPException(400, "Type the price as a number, like 9.99.")
        name = str(p.get("name") or "").strip()[:200] or None
        _store_do(lambda: gumroad.update_product(user, prod["id"], name if name != prod["name"] else None,
                                                 price if price != prod["price"] else None))
    elif act == "delete":
        _store_do(lambda: gumroad.delete_product(user, prod["id"]))
    else:
        raise HTTPException(400, "Unknown action")
    core.log(user["id"], f"Gumroad: {act} {prod['name']}", "done")
    return gumroad.overview(user)


@app.get("/api/store/codes")
def store_codes(product: str, user=Depends(store_user)):
    prod = _own_product(user, product)
    return {"codes": _store_do(lambda: gumroad.codes(user, prod["id"]))}


@app.post("/api/store/codes")
def store_code_add(p: dict = Body(...), user=Depends(store_user)):
    prod = _own_product(user, str(p.get("product") or ""))
    if p.get("delete"):
        _store_do(lambda: gumroad.delete_code(user, prod["id"], str(p["delete"])))
    else:
        try:
            amount = float(str(p.get("amount") or "").replace(",", ".").replace("%", "").strip())
        except ValueError:
            raise HTTPException(400, "Type the discount as a number.")
        _store_do(lambda: gumroad.create_code(user, prod["id"], p.get("name"), amount, percent=p.get("percent", True),
                                              limit=p.get("limit") or None, all_products=bool(p.get("all"))))
    return {"codes": _store_do(lambda: gumroad.codes(user, prod["id"]))}


@app.get("/api/store/reviews")
def store_reviews(product: str, user=Depends(store_user)):
    prod = _own_product(user, product)
    return {"reviews": _store_do(lambda: gumroad.reviews(user, prod["id"]))}


@app.post("/api/store/sale")
def store_sale(p: dict = Body(...), user=Depends(store_user)):
    sale, act = _store_do(lambda: gumroad.find_sale(user, str(p.get("id") or ""))), p.get("action")
    if act == "refund":
        _store_do(lambda: gumroad.refund(user, sale["id"]))
        core.log(user["id"], f"Gumroad: refunded {sale['product']} to {sale['email']}", "done")
    elif act == "receipt":
        _store_do(lambda: gumroad.resend_receipt(user, sale["id"]))
    else:
        raise HTTPException(400, "Unknown action")
    return gumroad.overview(user)


# ---------- this login's own links (a tracker, a portal), each with an optional login code kept encrypted ----------
def _link_view(user, l):
    return {"id": l["id"], "name": l.get("name", ""), "url": l["url"],
            "has_code": bool(core.get_secret(user["id"], "link:" + l["id"]))}


@app.get("/api/links")
def links_list(user=Depends(auth)):
    return [_link_view(user, l) for l in my_links(user)]


@app.post("/api/links")
def links_save(p: dict = Body(...), user=Depends(auth)):
    s = core.settings(user)
    links = [l for l in (s.get("links") or []) if isinstance(l, dict) and l.get("id")]
    lid = str(p.get("id") or "")
    cur = next((l for l in links if l["id"] == lid), None)
    name = str(p.get("name") or (cur or {}).get("name") or "").strip()[:40]
    url = str(p.get("url") or (cur or {}).get("url") or "").strip()[:500]
    if not name:
        raise HTTPException(400, "Give the link a name.")
    if not re.match(r"https?://[\w.-]+", url, re.I):
        raise HTTPException(400, "The address must start with https://")
    if not cur:
        if len(links) >= 12:
            raise HTTPException(400, "That is the most links one login can have. Remove one first.")
        lid = "l" + format(int(time.time() * 1000), "x")
        cur = {"id": lid}
        links.append(cur)
    cur["name"], cur["url"] = name, url
    s["links"] = links
    core.save_settings(user["id"], s)
    if "code" in p:
        code = str(p.get("code") or "").strip()[:200]
        if code:
            core.set_secret(user["id"], "link:" + lid, code)
        else:
            core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], "link:" + lid))
    return _link_view(user, cur)


@app.get("/api/links/{lid}/code")
def links_code(lid: str, user=Depends(auth)):
    if not any(l["id"] == lid for l in my_links(user)):
        raise HTTPException(404, "No such link")
    return {"code": core.get_secret(user["id"], "link:" + lid) or ""}


@app.delete("/api/links/{lid}")
def links_delete(lid: str, user=Depends(auth)):
    s = core.settings(user)
    s["links"] = [l for l in (s.get("links") or []) if isinstance(l, dict) and l.get("id") != lid]
    core.save_settings(user["id"], s)
    core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], "link:" + lid))
    return {"ok": True}


# ---------- a link opened by Trillion herself on this PC and shown in the dashboard (so she can type its login code) ----------
def _web(user, lid, start=False, size=None):
    import webview
    l = next((x for x in my_links(user) if x["id"] == lid), None)
    if not l:
        raise HTTPException(404, "No such link")
    s = webview.session(user, l, core.get_secret(user["id"], "link:" + lid) or "", start=start)
    if not s:
        raise HTTPException(409, "That page is not open. Open it again.")
    if s.error and not s.alive:
        raise HTTPException(503, s.error)
    try:
        if size:
            s.call(lambda z: z.resize(*size))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return s


def _web_do(s, fn):
    try:
        return s.call(fn)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


def _web_state(s):
    return {"state": s.state, "error": s.error, "has_code": bool(s.code),
            "note": {"unlocked": "Unlocked for you.", "locked": "This page is asking for its login code." if not s.code else
                     "The page is locked and I could not find where to type the code.",
                     "refused": "The page did not accept the saved login code. Check the code, then save it again."}.get(s.state, "")}


@app.post("/api/web/{lid}/open")
def web_open(lid: str, p: dict = Body(default={}), user=Depends(auth)):
    s = _web(user, lid, start=True, size=(p.get("w") or 1100, p.get("h") or 720))
    if s.error:
        raise HTTPException(503, s.error)
    return _web_state(s)


@app.get("/api/web/{lid}/shot")
def web_shot(lid: str, user=Depends(auth)):
    s = _web(user, lid)
    img = _web_do(s, lambda z: z.shot())
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-State": s.state})


@app.get("/api/web/{lid}/state")
def web_state(lid: str, user=Depends(auth)):
    return _web_state(_web(user, lid))


@app.post("/api/web/{lid}/do")
def web_do(lid: str, p: dict = Body(...), user=Depends(auth)):
    """One thing done to the page: a tap at a spot (0..1 across and down), typed words, a key, a scroll, back or reload."""
    s = _web(user, lid)
    kind = p.get("kind")
    if kind == "tap":
        x, y = max(0.0, min(1.0, float(p.get("x", 0)))), max(0.0, min(1.0, float(p.get("y", 0))))
        _web_do(s, lambda z: (z.page.mouse.click(x * z.size[0], y * z.size[1]), z.page.wait_for_timeout(350)))
    elif kind == "type":
        words = str(p.get("text") or "")[:500]
        _web_do(s, lambda z: z.page.keyboard.type(words, delay=20))
    elif kind == "key" and p.get("key") in ("Enter", "Backspace", "Tab", "Escape"):
        _web_do(s, lambda z: z.page.keyboard.press(p["key"]))
    elif kind == "scroll":
        dy = max(-2000, min(2000, int(p.get("dy", 400))))
        _web_do(s, lambda z: (z.page.mouse.move(z.size[0] / 2, z.size[1] / 2), z.page.mouse.wheel(0, dy), z.page.wait_for_timeout(250)))
    elif kind == "back":
        _web_do(s, lambda z: z.page.go_back(timeout=15000))
    elif kind == "reload":
        _web_do(s, lambda z: (z.page.reload(timeout=30000), z.page.wait_for_timeout(1200)))
    else:
        raise HTTPException(400, "I do not know that action")
    return _web_state(s)


@app.get("/api/web/{lid}/text")
def web_text(lid: str, user=Depends(auth)):
    return {"text": _web_do(_web(user, lid), lambda z: z.text())}


@app.post("/api/web/{lid}/close")
def web_close(lid: str, user=Depends(auth)):
    import webview
    webview.close(user, lid)
    return {"ok": True}


# ---------- files and media handed over in the conversation ----------
@app.post("/api/upload")
async def upload(request: Request, name: str = "file", user=Depends(auth)):
    """The file itself is the body of the request, so big videos are written straight to disk as they arrive."""
    path = attach.new_path(user, name)
    size = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > attach.MAX_BYTES:
                    raise HTTPException(413, f"That file is larger than {attach.MAX_BYTES // 1024 ** 3} GB.")
                f.write(chunk)
        if not size:
            raise HTTPException(400, "The file arrived empty.")
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    f = attach.record(user, path)
    core.log(user["id"], f"Received {f['name']}", "done")
    return {k: f[k] for k in ("id", "name", "kind", "size")}


@app.get("/api/manuals")
def manuals_list(user=Depends(auth)):
    return {"manuals": manuals.listing(user)}


@app.post("/api/manuals")
def manuals_add(p: dict = Body(...), user=Depends(auth)):
    try:
        return manuals.add(user, tools.resolve(user, p.get("path")), p.get("name"))
    except (manuals.ManualError, PermissionError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/manuals/{mid}/search")
def manuals_search(mid: int, q: str = "", user=Depends(auth)):
    return {"hits": manuals.search(user, q, str(mid), limit=12)}


@app.get("/api/manuals/{mid}/page/{page}.png")
def manuals_page_png(mid: int, page: int, user=Depends(auth)):
    try:
        return Response(manuals.page_png(user, mid, page), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})
    except manuals.ManualError as e:
        raise HTTPException(404, str(e))


@app.get("/api/manuals/{mid}/page/{page}.pdf")
def manuals_page_pdf(mid: int, page: int, user=Depends(auth)):
    try:
        data, name = manuals.page_pdf(user, mid, page)
    except manuals.ManualError as e:
        raise HTTPException(404, str(e))
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/api/manuals/{mid}/page/{page}", response_class=HTMLResponse)
def manuals_page_view(mid: int, page: int, user=Depends(auth)):
    """One page of a manual to look at closely: zoom with the wheel or two fingers, drag to move, turn it, step through."""
    m = manuals.get(user, mid)
    if not m:
        raise HTTPException(404, "That manual is not yours.")
    page = max(1, min(int(page), m["pages"] or 1))
    try:
        info = manuals.page_text(user, mid, page)
    except manuals.ManualError:
        info = {"section": "", "ref": ""}
    import html as _h
    title = _h.escape(f"{m['name']} - {info['section']} - page {page}" + (f" ({info['ref']})" if info["ref"] else ""))
    return MANUAL_VIEWER.replace("{TITLE}", title).replace("{MID}", str(mid)).replace("{PAGE}", str(page)) \
        .replace("{LAST}", str(m["pages"] or 1))


MANUAL_VIEWER = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{TITLE}</title><style>
:root{color-scheme:dark}body{margin:0;background:#111;color:#eee;font:15px system-ui,sans-serif;overflow:hidden;height:100vh;display:flex;flex-direction:column}
header{display:flex;gap:6px;align-items:center;padding:8px 10px;background:#1c1c1c;flex-wrap:wrap}
header b{flex:1 1 240px;font-weight:600;font-size:14px}
button,a.b{background:#2c2c2c;color:#eee;border:1px solid #444;border-radius:8px;padding:8px 12px;font:inherit;cursor:pointer;text-decoration:none}
button:hover,a.b:hover{background:#3a3a3a}#stage{flex:1;position:relative;overflow:hidden;touch-action:none;cursor:grab}
#pic{position:absolute;left:50%;top:50%;transform-origin:center;max-width:none;user-select:none;-webkit-user-drag:none;background:#fff}
#hint{position:absolute;bottom:8px;left:0;right:0;text-align:center;color:#999;font-size:12px;pointer-events:none}
</style></head><body><header><b>{TITLE}</b>
<button id="prev" title="Previous page">&larr;</button><button id="next" title="Next page">&rarr;</button>
<button id="zin" title="Zoom in">+</button><button id="zout" title="Zoom out">&minus;</button>
<button id="rl" title="Turn left">&#8634;</button><button id="rr" title="Turn right">&#8635;</button>
<button id="fit" title="Fit to screen">Fit</button><a class="b" href="/api/manuals/{MID}/page/{PAGE}.pdf" target="_blank">PDF</a></header>
<div id="stage"><img id="pic" src="/api/manuals/{MID}/page/{PAGE}.png" alt="{TITLE}" draggable="false">
<div id="hint">Scroll or pinch to zoom, drag to move, arrow keys for the next page</div></div>
<script>
const P={PAGE},L={LAST},img=document.getElementById('pic'),st=document.getElementById('stage');
let s=1,r=0,x=0,y=0;const draw=()=>img.style.transform=`translate(calc(-50% + ${x}px),calc(-50% + ${y}px)) rotate(${r}deg) scale(${s})`;
const fit=()=>{const w=img.naturalWidth,h=img.naturalHeight,turned=r%180!==0;const W=st.clientWidth,H=st.clientHeight;
  s=Math.min(W/(turned?h:w),H/(turned?w:h))*0.98;x=y=0;draw();};
img.onload=fit;addEventListener('resize',fit);
const zoom=(f,cx,cy)=>{const b=st.getBoundingClientRect();cx=(cx??b.left+b.width/2)-b.left-b.width/2;cy=(cy??b.top+b.height/2)-b.top-b.height/2;
  const n=Math.max(0.1,Math.min(12,s*f));x=cx-(cx-x)*n/s;y=cy-(cy-y)*n/s;s=n;draw();};
st.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY<0?1.15:1/1.15,e.clientX,e.clientY);},{passive:false});
const pts=new Map();let last=null;
st.addEventListener('pointerdown',e=>{st.setPointerCapture(e.pointerId);pts.set(e.pointerId,[e.clientX,e.clientY]);last=null;});
st.addEventListener('pointermove',e=>{if(!pts.has(e.pointerId))return;const o=pts.get(e.pointerId);pts.set(e.pointerId,[e.clientX,e.clientY]);
  if(pts.size===1){x+=e.clientX-o[0];y+=e.clientY-o[1];draw();}
  else if(pts.size===2){const [a,b]=[...pts.values()];const d=Math.hypot(a[0]-b[0],a[1]-b[1]);if(last)zoom(d/last,(a[0]+b[0])/2,(a[1]+b[1])/2);last=d;}});
const up=e=>{pts.delete(e.pointerId);last=null;};st.addEventListener('pointerup',up);st.addEventListener('pointercancel',up);
const go=n=>{if(n>=1&&n<=L)location.href='/api/manuals/{MID}/page/'+n;};
document.getElementById('prev').onclick=()=>go(P-1);document.getElementById('next').onclick=()=>go(P+1);
document.getElementById('zin').onclick=()=>zoom(1.3);document.getElementById('zout').onclick=()=>zoom(1/1.3);
document.getElementById('rl').onclick=()=>{r=(r+270)%360;fit();};document.getElementById('rr').onclick=()=>{r=(r+90)%360;fit();};
document.getElementById('fit').onclick=fit;
addEventListener('keydown',e=>{if(e.key==='ArrowLeft')go(P-1);if(e.key==='ArrowRight')go(P+1);if(e.key==='+'||e.key==='=')zoom(1.3);if(e.key==='-')zoom(1/1.3);});
</script></body></html>"""


@app.get("/api/uploads/{fid}/file")
def upload_file(fid: int, user=Depends(auth)):
    import os, mimetypes
    f = attach.get(user, fid)
    if not f or not os.path.exists(f["path"]):
        raise HTTPException(404, "That file is no longer there")
    return FileResponse(f["path"], media_type=mimetypes.guess_type(f["name"])[0] or "application/octet-stream", filename=f["name"])


@app.get("/api/selfcheck")
def selfcheck_get(user=Depends(admin)):
    return selfcheck.run(user, SITES, HEARING, TAILSCALE)


@app.post("/api/stop")
def stop(user=Depends(auth)):
    STOP.add(user["id"])
    BUSY[user["id"]] = False      # also free the lock, in case the worker already died and left it stuck
    return {"ok": True}


@app.get("/api/workflow")
def workflow(user=Depends(auth)):
    """Live view data: each running (or just-finished) job, its department, the model on it now, and the
    ordered chain of stops it has taken through models, skills, connectors and tools."""
    uid = user["id"]
    dept = {"WORK": "Work", "PERSONAL": "Personal"}

    def pack(t):
        steps = core.steps_for(t["id"])
        model = next((s["name"] for s in reversed(steps) if s["kind"] == "model"), "")
        return {"id": t["id"], "title": t["title"], "detail": t["detail"], "status": t["status"],
                "department": dept.get((t["domain"] or "").upper(), (t["domain"] or "General").title()),
                "started": t["created"], "updated": t["updated"], "model": model, "steps": steps}

    running = [pack(t) for t in core.q(
        "SELECT * FROM tasks WHERE user_id=? AND status IN ('in_progress','waiting') ORDER BY id DESC", (uid,))]
    for j in running:
        if not j["model"]:
            j["model"] = LAST_MODEL.get(uid, "local brain")
    recent = [p for p in (pack(t) for t in core.q(
        "SELECT * FROM tasks WHERE user_id=? AND status IN ('completed','failed') AND updated>=? ORDER BY id DESC LIMIT 8",
        (uid, core.day_start()))) if p["steps"]]
    return {"running": running, "recent": recent, "busy": bool(BUSY.get(uid)), "now": time.time()}


@app.get("/api/evolve")
def evolve_get(user=Depends(auth)):
    return evolve.state(user)


@app.post("/api/evolve/apply")
def evolve_apply(p: dict = Body(...), user=Depends(auth)):
    evolve.apply(user, int(p.get("id")))
    return evolve.state(user)


@app.post("/api/evolve/dismiss")
def evolve_dismiss(p: dict = Body(...), user=Depends(auth)):
    evolve.dismiss(user, int(p.get("id")))
    return evolve.state(user)


@app.post("/api/evolve/auto")
def evolve_auto(p: dict = Body(...), user=Depends(auth)):
    evolve.set_auto(user, bool(p.get("on")))
    return evolve.state(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


@app.post("/api/tasks/clear")
def tasks_clear(p: dict = Body(...), user=Depends(auth)):
    """Clear the job queue: one job, or everything stuck/failed/done. Cancelling a running job also
    breaks its loop (STOP) and frees the 'busy' lock so new work can start - the fix for a stuck queue."""
    uid = user["id"]
    tid = p.get("id")
    scope = (p.get("scope") or "").lower()
    freed = False
    if tid is not None:
        row = core.q("SELECT status FROM tasks WHERE id=? AND user_id=?", (int(tid), uid), one=True)
        if row and row["status"] in ("in_progress", "waiting"):
            STOP.add(uid); BUSY[uid] = False; freed = True
        core.x("DELETE FROM tasks WHERE id=? AND user_id=?", (int(tid), uid))
    elif scope == "running":
        STOP.add(uid); BUSY[uid] = False; freed = True
        core.x("DELETE FROM tasks WHERE user_id=? AND status IN ('in_progress','waiting')", (uid,))
    elif scope == "problems":
        core.x("DELETE FROM tasks WHERE user_id=? AND status='failed'", (uid,))
    elif scope == "done":
        core.x("DELETE FROM tasks WHERE user_id=? AND status='completed'", (uid,))
    elif scope == "all":
        STOP.add(uid); BUSY[uid] = False; freed = True
        core.x("DELETE FROM tasks WHERE user_id=?", (uid,))
    else:
        return JSONResponse({"error": "Nothing to clear - pass an id or a scope."}, status_code=200)
    try:
        core.x("DELETE FROM steps WHERE task_id NOT IN (SELECT id FROM tasks)")   # drop the path of any job we just removed
    except Exception:
        pass
    return {"ok": True, "freed": freed}


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


def _cuda_dlls():
    """The graphics-card libraries pip installs sit in their own folders; Windows has to be told where."""
    import os, sys, glob
    for base in sys.path:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")) + glob.glob(os.path.join(base, "nvidia", "*", "lib")):
            try:
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            except OSError:
                pass


def _cuda_safe(name):
    """Try hearing on the graphics card in a separate helper program first. A missing library there can stop a
    program dead rather than raise an error, and that must never take Trillion herself down. The answer is
    remembered, so the trial runs once per version."""
    import os, sys, subprocess
    if os.environ.get("TRILLION_SAFE"):
        return False
    mark = core.DATA / "hearing_gpu.txt"
    tag = VERSION.split(" ")[0]
    try:
        seen = mark.read_text(encoding="utf-8").split("|")
        if seen[0] == tag:
            return seen[1] == "ok"
    except Exception:
        pass
    mark.write_text(f"{tag}|bad", encoding="utf-8")       # stays "bad" unless the trial below finishes cleanly
    code = ("import os,sys,glob\n"
            "for b in sys.path:\n"
            "    for d in glob.glob(os.path.join(b,'nvidia','*','bin'))+glob.glob(os.path.join(b,'nvidia','*','lib')):\n"
            "        try:\n"
            "            os.add_dll_directory(d) if hasattr(os,'add_dll_directory') else None\n"
            "            os.environ['PATH']=d+os.pathsep+os.environ.get('PATH','')\n"
            "        except OSError: pass\n"
            "import numpy as np\n"
            "from faster_whisper import WhisperModel\n"
            f"m=WhisperModel({name!r},device='cuda',compute_type='float16')\n"
            "list(m.transcribe(np.zeros(16000,dtype='float32'),language='en')[0])\n"
            "print('GPU_OK')\n")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=240)
        ok = r.returncode == 0 and "GPU_OK" in r.stdout
    except Exception:
        ok = False
    mark.write_text(f"{tag}|{'ok' if ok else 'bad'}", encoding="utf-8")
    if not ok:
        core.log(1, "Hearing stays on the processor: the graphics card trial did not pass", "waiting")
    return ok


def load_whisper():
    global _whisper
    with _whisper_lock:
        if _whisper is not None:
            return _whisper
        import os
        HEARING["state"] = "loading"
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            HEARING["state"] = "not installed"
            raise
        name = os.environ.get("TRILLION_WHISPER_MODEL", "small")
        want = os.environ.get("TRILLION_WHISPER_DEVICE") or ("cuda" if core.gpu() else "cpu")
        if want == "cuda" and _cuda_safe(name):
            try:
                _cuda_dlls()
                _whisper = WhisperModel(name, device="cuda", compute_type="float16")
                HEARING.update(state="ready", device="graphics card")
                return _whisper
            except Exception as e:
                core.log(1, f"Hearing could not use the graphics card ({type(e).__name__}), so it runs on the processor", "waiting")
        try:
            _whisper = WhisperModel(name, device="cpu", compute_type="int8")
            HEARING.update(state="ready", device="processor")
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


@app.get("/api/images/{name}")
def image_file(name: str, user=Depends(auth)):
    """A picture she made for this login (only from that login's own Trillion Images folder)."""
    if not re.fullmatch(r"[\w.-]+\.png", name):
        raise HTTPException(404, "No such picture")
    path = core.out_dir(user, "Trillion Images") / name
    if not path.exists():
        raise HTTPException(404, "No such picture")
    return FileResponse(str(path), media_type="image/png")


@app.get("/api/live")
def live(user=Depends(auth)):
    """The answer so far, while she is still writing it."""
    return {"text": LIVE.get(user["id"], "") if BUSY.get(user["id"]) else ""}


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
    if s.get("voice") == "kokoro":                     # free local voice; falls back to the PC voice until ready
        try:
            import localvoice
            samples, sr = localvoice.synth((p.get("text") or "")[:1200], s.get("kokoro_voice"))
            if len(samples):
                return Response(localvoice.wav_bytes(samples, sr), media_type="audio/wav")
        except Exception:
            pass
        return Response(status_code=204)
    key = core.get_secret(user["id"], "elevenlabs") if s.get("voice") == "elevenlabs" else None
    if not key:
        return Response(status_code=204)  # the browser speaks with the PC's own voice
    try:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s['eleven_voice_id']}",
                       params={"output_format": "mp3_44100_128"}, headers={"xi-api-key": key}, timeout=60,
                       json={"text": (p.get("text") or "")[:2500], "model_id": "eleven_turbo_v2_5"})
        r.raise_for_status()
        core.bump_usage(user["id"], "elevenlabs")
        return Response(r.content, media_type="audio/mpeg")
    except Exception as e:
        core.log(user["id"], f"ElevenLabs voice failed, used the PC voice: {type(e).__name__}", "waiting")
        return Response(status_code=204)


@app.get("/api/tts/kokoro")
def kokoro_status(user=Depends(auth)):
    import localvoice
    if core.settings(user).get("voice") == "kokoro":
        localvoice.prepare()
    return {"status": localvoice.status(), "error": localvoice.STATE.get("error", ""), "voices": localvoice.VOICES}


# ---------- live phone and Tailscale readings for the dashboard ----------
PHONE_STATS, TAILSCALE = {}, {"t": 0, "v": None}


def refresh_phone(user):
    try:
        PHONE_STATS[user["id"]] = {"ok": True, "t": time.time(), **phone.status(user)}
    except Exception as e:
        prev = PHONE_STATS.get(user["id"]) or {}
        PHONE_STATS[user["id"]] = {**{k: v for k, v in prev.items() if k not in ("ok", "error")},
                                   "ok": False, "error": str(e), "t": prev.get("t", 0), "checked": time.time()}


def tailscale_status():
    import shutil, subprocess, os
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    if not (shutil.which("tailscale") or os.path.exists(exe)):
        return {"installed": False}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=8).stdout
        j = json.loads(out)
    except Exception as e:
        return {"installed": True, "running": False, "error": type(e).__name__}
    me = j.get("Self") or {}
    name = (me.get("DNSName") or "").rstrip(".")
    peers = [{"name": p.get("HostName") or (p.get("DNSName") or "").split(".")[0], "os": p.get("OS", ""),
              "online": bool(p.get("Online")), "ip": (p.get("TailscaleIPs") or [""])[0]}
             for p in (j.get("Peer") or {}).values()]
    peers.sort(key=lambda p: (not p["online"], p["name"].lower()))
    return {"installed": True, "running": j.get("BackendState") == "Running", "state": j.get("BackendState", ""),
            "name": me.get("HostName", ""), "dns": name, "ip": (me.get("TailscaleIPs") or [""])[0], "devices": peers[:20]}


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
        "day_start": ds, "now": time.time(), "bible_on": bible.allowed(user), "verse": bible_today(user), "store_new": gumroad.unseen(uid) if store_allowed(user) else 0, "store_on": store_allowed(user), "wa_unseen": core.q("SELECT COUNT(*) c FROM wa_inbox WHERE user_id=? AND seen=0", (uid,), one=True)["c"],
        "version": VERSION, "hearing": HEARING["state"], "hearing_device": HEARING.get("device", ""),
        "phone": ({"linked": True, "model": s.get("phone_model", ""), **(PHONE_STATS.get(uid) or {"pending": True})}
                  if s.get("phone_serial") else {"linked": False}),
        "tailscale": (TAILSCALE["v"] if user["is_admin"] else
                      {k: v for k, v in (TAILSCALE["v"] or {}).items() if k != "devices"}) if TAILSCALE["v"] else None,
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
EDITABLE = ["freevideo_url", "wake_word", "video_ai_images", "wa_inbox", "fast_answers", "theme", "quick_actions", "whatsapp_auto_contacts", "phone_own_number", "phone_full_control", "personality", "phone_country_code", "assistant_name", "brand", "accent", "site", "brain", "local_model", "heavy_model", "heavy_answers", "cloud_model", "voice", "kokoro_voice",
            "eleven_voice_id", "daily_review", "call_done_announce", "call_done_device"]


@app.put("/api/settings")
def put_settings(p: dict = Body(...), user=Depends(auth)):
    s = core.settings(user)
    for k in EDITABLE:
        if k in p:
            s[k] = p[k]
    if s["brain"] not in ("local", "gemini", "anthropic"):
        s["brain"] = "local"
    if s.get("theme") not in THEMES:
        s["theme"] = "night"
    if s.get("personality") not in PERSONAS:
        s["personality"] = "professional"
    if s.get("voice") not in ("browser", "kokoro", "elevenlabs"):
        s["voice"] = "browser"
    core.save_settings(user["id"], s)
    if s.get("voice") == "kokoro":                 # start the one-time download as soon as it is picked
        import localvoice
        localvoice.prepare()
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
        elif name == "pixabay":
            r = httpx.get("https://pixabay.com/api/videos/", params={"key": key, "q": "city", "per_page": 3}, timeout=15)
        elif name == "pexels":
            r = httpx.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                          params={"query": "office", "per_page": 1}, timeout=15)
        elif name == "google_places":
            r = httpx.post("https://places.googleapis.com/v1/places:searchText", timeout=15,
                           headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id",
                                    "Content-Type": "application/json"}, json={"textQuery": "coffee", "maxResultCount": 1})
            note = ""
            if r.status_code != 200:
                try:
                    note = r.json().get("error", {}).get("message", "")
                except Exception:
                    note = ""
            return {"ok": r.status_code == 200, "status": r.status_code, "note": note[:200]}
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


# ---------- routines ----------
@app.get("/api/routines")
def routines_list(user=Depends(auth)):
    return {"routines": routines.list_for(user), "sections": routines.SECTIONS,
            "feeds": [{"key": k, "label": v[0]} for k, v in routines.FEEDS.items()], "last": routines.last_brief(user)}


@app.post("/api/routines")
def routines_add(p: dict = Body(...), user=Depends(auth)):
    try:
        return {"id": routines.save(user, p)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/routines/{rid}")
def routines_put(rid: int, p: dict = Body(...), user=Depends(auth)):
    try:
        return {"id": routines.save(user, p, rid)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/routines/{rid}")
def routines_delete(rid: int, user=Depends(auth)):
    routines.delete(user, rid)
    return {"ok": True}


# ---------- videos ----------
def _video(vid, user):
    v = core.q("SELECT * FROM videos WHERE id=? AND user_id=?", (vid, user["id"]), one=True)
    if not v:
        raise HTTPException(404, "No such video")
    return v


@app.get("/api/ebooks")
def ebooks_list(user=Depends(auth)):
    import os, ebook
    ebook.init()
    rows = [dict(r, detail=json.loads(r["detail"] or "{}"), exists=os.path.exists(r["path"]))
            for r in core.q("SELECT * FROM ebooks WHERE user_id=? ORDER BY id DESC LIMIT 60", (user["id"],))]
    return {"status": ebook.status(), "ebooks": rows}


@app.get("/api/ebooks/{eid}/file")
def ebooks_file(eid: int, download: int = 0, epub: int = 0, user=Depends(auth)):
    import os
    r = core.q("SELECT * FROM ebooks WHERE id=? AND user_id=?", (eid, user["id"]), one=True)
    if not r or not os.path.exists(r["path"]):
        raise HTTPException(404, "That ebook is no longer there")
    if epub:
        ep = json.loads(r["detail"] or "{}").get("epub") or ""
        if not ep or not os.path.exists(ep):
            raise HTTPException(404, "There is no EPUB copy of that ebook")
        return FileResponse(ep, media_type="application/epub+zip", filename=ep.replace("\\", "/").split("/")[-1])
    name = r["path"].replace("\\", "/").split("/")[-1]
    return FileResponse(r["path"], media_type="application/pdf", filename=name,
                        content_disposition_type="attachment" if download else "inline")


@app.delete("/api/ebooks/{eid}")
def ebooks_delete(eid: int, user=Depends(auth)):
    import os
    r = core.q("SELECT * FROM ebooks WHERE id=? AND user_id=?", (eid, user["id"]), one=True)
    if not r:
        raise HTTPException(404, "No such ebook")
    for f in (r["path"], json.loads(r["detail"] or "{}").get("epub") or ""):
        try:
            if f:
                os.unlink(f)
        except OSError:
            pass
    core.x("DELETE FROM ebooks WHERE id=?", (eid,))
    return {"ok": True}


@app.get("/api/freevideo")
def freevideo_status(user=Depends(auth)):
    import freevideo
    return freevideo.status(user)


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
    except Exception as e:  # a slow or hung phone link must not empty the settings box
        out["adb"], out["error"] = False, (str(e) if isinstance(e, phone.PhoneError) else "The phone link did not answer. Click LOOK AGAIN.")
    # every phone linked to a login, so one that should not be there can be removed (the owner sees all, others their own)
    here = {d["serial"]: d["state"] for d in out["devices"]}
    out["all_linked"] = []
    for u in core.q("SELECT * FROM users ORDER BY id"):
        if not (user["is_admin"] or u["id"] == user["id"]):
            continue
        us = core.settings(u)
        if us.get("phone_serial"):
            known = [x for x in (us.get("phone_serial"), us.get("phone_usb"), us.get("phone_wifi")) if x]
            out["all_linked"].append({"user_id": u["id"], "user": u["name"], "model": us.get("phone_model", ""), "serial": us["phone_serial"],
                                      "mine": u["id"] == user["id"], "connected": any(here.get(x) == "device" for x in known)})
    return out


@app.post("/api/phone/remove")
def phone_remove(p: dict = Body(...), user=Depends(auth)):
    """Take a phone off a login (the owner may do it for any login), or make this PC forget a wireless phone nobody is linked to."""
    if p.get("user_id") is not None:
        target = core.q("SELECT * FROM users WHERE id=?", (int(p["user_id"]),), one=True)
        if not target or not (user["is_admin"] or target["id"] == user["id"]):
            raise HTTPException(403, "Only the owner can remove another login's phone")
        s = core.settings(target)
        serials = {x for x in (s.get("phone_serial"), s.get("phone_usb"), s.get("phone_wifi")) if x}
        model = s.get("phone_model") or s.get("phone_serial") or "phone"
        for k in ("phone_serial", "phone_model", "phone_usb", "phone_wifi"):
            s.pop(k, None)
        core.save_settings(target["id"], s)
        PHONE_STATS.pop(target["id"], None)
        CONTACT_CACHE.pop(target["id"], None)
        for x in serials:
            if ":" in x:                       # a wireless link: also close the connection, or it would just show up again
                try:
                    phone._run(["disconnect", x], timeout=10)
                except Exception:
                    pass
        for who in {user["id"], target["id"]}:
            core.log(who, f"Phone removed from {target['name']}: {model}", "done")
        return {"ok": True, "removed": model, "from": target["name"]}
    serial = str(p.get("serial") or "")
    if not user["is_admin"]:
        raise HTTPException(403, "Only the owner can do that")
    if phone.owner_of(serial):
        raise HTTPException(400, "That phone is linked to a login. Remove it from the login instead.")
    if ":" not in serial:
        raise HTTPException(400, "That phone is on a cable. Unplug it and it disappears from the list.")
    try:
        phone._run(["disconnect", serial], timeout=10)
    except Exception as e:
        raise HTTPException(400, f"Could not let go of it ({type(e).__name__}).")
    return {"ok": True, "forgot": serial}


CONTACT_CACHE = {}


@app.get("/api/phone/contacts")
def phone_contacts(fresh: int = 0, user=Depends(auth)):
    """Names only, for the pick-a-contact box. Kept for ten minutes so the phone is not asked on every keystroke."""
    c = CONTACT_CACHE.get(user["id"])
    if c and not fresh and time.time() - c["t"] < 600:
        return c["v"]
    try:
        names = sorted(phone.all_contacts(user), key=str.lower)
        v = {"ok": True, "names": names[:3000]}
        if not names:
            v = {"ok": False, "names": [], "error": "The phone gave me no contacts. You can still type the name: she looks it up in WhatsApp."}
    except Exception as e:
        v = {"ok": False, "names": [], "error": str(e) if isinstance(e, phone.PhoneError) else "The phone did not answer."}
    if v["ok"]:
        CONTACT_CACHE[user["id"]] = {"t": time.time(), "v": v}
    return v


# ---------- incoming WhatsApp, read from the phone's notifications ----------
core.x("""CREATE TABLE IF NOT EXISTS wa_inbox(id INTEGER PRIMARY KEY, user_id INTEGER, chat TEXT, sender TEXT, text TEXT, kind TEXT,
          is_group INTEGER DEFAULT 0, ts REAL, seen INTEGER DEFAULT 0, replied TEXT DEFAULT '', dedupe TEXT UNIQUE)""")
WA_STATE = {}


def wa_poll(user):
    """Look at the phone's notifications and file any WhatsApp message not seen before. Returns how many were new."""
    import hashlib
    uid = user["id"]
    try:
        items = phone.whatsapp_notifications(user)
        WA_STATE[uid] = {"ok": True, "t": time.time()}
    except Exception as e:
        WA_STATE[uid] = {"ok": False, "t": time.time(), "error": str(e)[:160]}
        return 0
    fresh, new_rows = 0, []
    for it in items:
        d = hashlib.sha1(f"{uid}|{it['chat']}|{it['sender']}|{it['text']}|{it['when'] or it['key']}".encode()).hexdigest()
        if core.q("SELECT 1 FROM wa_inbox WHERE dedupe=?", (d,), one=True):
            continue
        core.x("INSERT INTO wa_inbox(user_id,chat,sender,text,kind,is_group,ts,dedupe) VALUES(?,?,?,?,?,?,?,?)",
               (uid, it["chat"], it["sender"], it["text"], it["kind"], 1 if it["group"] else 0, it["when"] or time.time(), d))
        fresh += 1
        who = it["sender"] + (f" in {it['chat']}" if it["group"] else "")
        core.log(uid, f"WhatsApp from {who}: {it['text'][:80]}", "voice")
        new_rows.append({"sender": it["sender"], "chat": it["chat"], "text": it["text"], "kind": it["kind"], "is_group": it["group"], "dedupe": d})
    if new_rows and meeting.active(user):
        meeting_answer(user, new_rows)
    return fresh


def meeting_answer(user, rows):
    """Meeting mode: answer what just came in, on the phone, without the user."""
    def send(chat, text):          # meeting mode is the user's own standing say-so: no approval step
        who, number = phone.resolve_contact(user, chat)
        res = tools.t_phone_send_whatsapp(user, contact=number, message=text, _name=who)
        core.log(user["id"], f"Meeting mode replied to {who}: {text[:100]}", "done")
        return res.get("status") == "send_pressed"
    BUSY[user["id"]] = True
    try:
        for r in rows:
            did = meeting.handle(user, r, send, decline=lambda: meeting.phone_decline(user),
                                 voice_text=lambda: meeting.phone_voice_text(user, transcribe))
            if did and "answered" in did:
                core.x("UPDATE wa_inbox SET replied=? WHERE dedupe=?", ("(meeting mode) " + did, r["dedupe"]))
    finally:
        BUSY[user["id"]] = False


def wa_rows(uid, only_new=False, limit=60):
    rows = core.q("SELECT id, chat, sender, text, kind, is_group, ts, seen, replied FROM wa_inbox WHERE user_id=?"
                  + (" AND seen=0" if only_new else "") + " ORDER BY id DESC LIMIT ?", (uid, limit))
    return [dict(r) for r in rows]


@app.get("/api/wa")
def wa_list(user=Depends(auth)):
    uid = user["id"]
    return {"on": bool(core.settings(user).get("wa_inbox")), "linked": bool(core.settings(user).get("phone_serial")),
            "state": WA_STATE.get(uid), "messages": wa_rows(uid),
            "unseen": core.q("SELECT COUNT(*) c FROM wa_inbox WHERE user_id=? AND seen=0", (uid,), one=True)["c"]}


@app.post("/api/wa/check")
def wa_check(user=Depends(auth)):
    n = wa_poll(user)
    return dict(wa_list(user), fresh=n)


@app.post("/api/wa/seen")
def wa_seen(p: dict = Body(default={}), user=Depends(auth)):
    if p.get("id"):
        core.x("UPDATE wa_inbox SET seen=1 WHERE user_id=? AND id=?", (user["id"], int(p["id"])))
    else:
        core.x("UPDATE wa_inbox SET seen=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}


@app.delete("/api/wa")
def wa_clear(user=Depends(auth)):
    core.x("DELETE FROM wa_inbox WHERE user_id=?", (user["id"],))
    return {"ok": True}


def _wa_one(user, mid):
    r = core.q("SELECT * FROM wa_inbox WHERE user_id=? AND id=?", (user["id"], mid), one=True)
    if not r:
        raise HTTPException(404, "That message is no longer in the list")
    return r


@app.post("/api/wa/{mid}/suggest")
def wa_suggest(mid: int, user=Depends(auth)):
    """A draft reply in the user's voice. Nothing is sent: the user reads it, changes it and sends it."""
    r = _wa_one(user, mid)
    earlier = core.q("SELECT sender, text FROM wa_inbox WHERE user_id=? AND chat=? AND id<=? ORDER BY id DESC LIMIT 5",
                     (user["id"], r["chat"], mid))[::-1]
    convo = "\n".join(f"{e['sender']}: {e['text'][:400]}" for e in earlier)
    try:
        m = llm_chat(user, [
            {"role": "system", "content": f"You draft short WhatsApp replies for {user['name']}. Write ONE reply of one or two sentences, "
             "natural and friendly, in the same language the other person wrote in (often Afrikaans or English). "
             "Do not invent facts, times or promises. Reply with the message text only, no quotes, no explanation."},
            {"role": "user", "content": f"Latest messages in the chat '{r['chat']}':\n{convo}\n\nDraft {user['name']}'s reply."}], fast=True)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    text = (m.get("content") or "").strip().strip('"\u201c\u201d')
    if not text:
        raise HTTPException(503, "I could not think of a reply. Type one yourself.")
    return {"text": text[:600]}


@app.post("/api/wa/{mid}/reply")
def wa_reply(mid: int, p: dict = Body(...), user=Depends(auth)):
    r = _wa_one(user, mid)
    text = str(p.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Type the reply first")
    res = tools.execute(user, "phone_send_whatsapp", {"contact": r["chat"], "message": text}, direct=True)
    if res.get("status") in ("send_pressed", "typed_not_sent"):
        core.x("UPDATE wa_inbox SET seen=1, replied=? WHERE id=?", (text[:500], mid))
        return {"ok": res["status"] == "send_pressed", "message": sent_sentence(res, text)}
    if res.get("status") == "waiting_for_approval":
        return {"ok": True, "message": "Ready. Press Approve in the red box and I will send it."}
    raise HTTPException(409, res.get("error", "The reply could not be sent"))


@app.post("/api/phone/touch")
def phone_touch(p: dict = Body(...), user=Depends(auth)):
    """Using the phone through its picture on the dashboard: a tap, a drag, a key or some typing, exactly where the user did it."""
    if not core.settings(user).get("phone_full_control"):
        raise HTTPException(403, "Tick Full phone control on the Phone page to use the phone from here.")
    n = lambda k: max(0, min(10000, int(float(p.get(k, 0)))))
    kind = p.get("kind")
    try:
        if kind == "tap":
            phone.sh(user, f"input tap {n('x')} {n('y')}")
        elif kind == "hold":
            phone.sh(user, f"input swipe {n('x')} {n('y')} {n('x')} {n('y')} 700")
        elif kind == "swipe":
            ms = max(80, min(1500, int(float(p.get("ms", 250)))))
            phone.sh(user, f"input swipe {n('x')} {n('y')} {n('x2')} {n('y2')} {ms}")
        elif kind == "key":
            phone.press(user, str(p.get("key", "")))
        elif kind == "text":
            t = str(p.get("text", ""))[:500]
            if not t:
                raise HTTPException(400, "Nothing to type")
            if any(ord(c) > 126 or ord(c) < 32 for c in t):
                raise HTTPException(400, "I can only type plain letters, numbers and common symbols.")
            phone.sh(user, "input text '" + t.replace("'", "'\\''").replace(" ", "%s") + "'")
        else:
            raise HTTPException(400, "Unknown action")
    except phone.PhoneError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/phone/unlock")
def phone_unlock_get(user=Depends(auth)):
    st = core.settings(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))   # fresh, also right after a save
    saved = core.get_secret(user["id"], "phone_unlock")
    return {"set": bool(saved), "kind": saved.partition(":")[0] if saved else "", "auto": bool(st.get("phone_auto_unlock")),
            "failed": bool(st.get("phone_unlock_failed"))}


@app.put("/api/phone/unlock")
def phone_unlock_put(p: dict = Body(...), user=Depends(auth)):
    kind, code = str(p.get("kind", "pin")), str(p.get("code", ""))
    st = core.settings(user)
    if code:
        if kind not in ("pin", "password", "pattern"):
            raise HTTPException(400, "Choose PIN, password or pattern")
        if kind == "pin" and not re.fullmatch(r"\d{4,16}", code):
            raise HTTPException(400, "A PIN is 4 to 16 digits")
        if kind == "pattern" and (not re.fullmatch(r"[1-9]{4,9}", code) or len(set(code)) != len(code)):
            raise HTTPException(400, "A pattern is 4 to 9 different dots, as the numbers 1 to 9")
        if kind == "password" and (len(code) < 4 or any(ord(c) > 126 or ord(c) < 32 for c in code)):
            raise HTTPException(400, "I can only type a password made of plain letters, numbers and common symbols")
        core.set_secret(user["id"], "phone_unlock", kind + ":" + code)
        st.pop("phone_unlock_failed", None)
        core.log(user["id"], "Phone unlock code saved", "done")
    elif not core.get_secret(user["id"], "phone_unlock"):
        raise HTTPException(400, "Type the code first")
    st["phone_auto_unlock"] = bool(p.get("auto"))
    core.save_settings(user["id"], st)
    return phone_unlock_get(user)


@app.delete("/api/phone/unlock")
def phone_unlock_delete(user=Depends(auth)):
    core.x("DELETE FROM secrets WHERE user_id=? AND name='phone_unlock'", (user["id"],))
    st = core.settings(user)
    st["phone_auto_unlock"] = False
    st.pop("phone_unlock_failed", None)
    core.save_settings(user["id"], st)
    core.log(user["id"], "Phone unlock code removed", "done")
    return phone_unlock_get(user)


@app.get("/api/phone/screen.png")
def phone_screen_png(user=Depends(auth)):
    try:
        return Response(phone.screen_png(user), media_type="image/png", headers={"Cache-Control": "no-store"})
    except phone.PhoneError as e:
        raise HTTPException(409, str(e))


@app.get("/api/projects")
def projects(user=Depends(auth)):
    """One card per business. The owner sees every login's card (figures only); everyone else sees their own."""
    ds = core.day_start()
    out = []
    people = core.q("SELECT * FROM users ORDER BY id") if user["is_admin"] else [user]
    for u in people:
        st = core.settings(u)
        uid = u["id"]
        cnt = lambda sql, *a: core.q(sql, (uid, *a), one=True)["c"]
        vid = core.q("SELECT id, title, created, seconds FROM videos WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
        last = core.q("SELECT ts, text FROM activity WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
        mine = uid == user["id"]
        card = {
            "id": uid, "owner": u["name"], "mine": mine, "brand": st.get("brand") or u["name"], "site": st.get("site") or "",
            "accent": st.get("accent") or "#ffb454", "site_status": SITES.get(st.get("site")) if st.get("site") else None,
            "notes": cnt("SELECT COUNT(*) c FROM notes WHERE user_id=?"),
            "videos": cnt("SELECT COUNT(*) c FROM videos WHERE user_id=?"),
            "last_video": dict(vid) if vid else None,
            "done_today": cnt("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND status='completed' AND created>=?", ds),
            "failed_today": cnt("SELECT COUNT(*) c FROM tasks WHERE user_id=? AND status='failed' AND created>=?", ds),
            "waiting": cnt("SELECT COUNT(*) c FROM approvals WHERE user_id=? AND status='pending'"),
            "routines": cnt("SELECT COUNT(*) c FROM routines WHERE user_id=?") if _has_table("routines") else 0,
            "last_active": last["ts"] if last else None,
            "phone": bool(st.get("phone_serial")),
        }
        if mine:   # the detail of a project is only for the person it belongs to
            card["recent"] = [dict(t) for t in core.q(
                "SELECT title, status, created FROM tasks WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,))]
            card["recent_videos"] = [dict(v) for v in core.q(
                "SELECT id, title, created, seconds, thumb FROM videos WHERE user_id=? ORDER BY id DESC LIMIT 4", (uid,))]
        out.append(card)
    return out


def _has_table(name):
    return bool(core.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,), one=True))


@app.post("/api/projects/check")
def projects_check(user=Depends(auth)):
    site = core.settings(user).get("site")
    if not site:
        raise HTTPException(400, "No website is set for this login. Add one under Settings.")
    SITES[site] = res = tools.site_check(site)
    return res


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
    s["phone_wifi" if ":" in serial else "phone_usb"] = serial
    core.save_settings(user["id"], s)
    core.log(user["id"], f"Phone linked: {dev['model'] or serial}", "done")
    threading.Thread(target=refresh_phone, args=(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True),), daemon=True).start()
    return {"ok": True}


@app.post("/api/phone/wireless")
def phone_wireless(p: dict = Body(...), user=Depends(auth)):
    try:
        target, via = phone.go_wireless(user, str(p.get("address", "")))
    except phone.PhoneError as e:
        raise HTTPException(400, str(e))
    s = core.settings(user)
    if ":" not in str(s.get("phone_serial", "")):
        s["phone_usb"] = s.get("phone_serial")
    s["phone_serial"] = s["phone_wifi"] = target
    core.save_settings(user["id"], s)
    return {"ok": True, "address": target, "via": via}


@app.post("/api/phone/unlink")
def phone_unlink(user=Depends(auth)):
    s = core.settings(user)
    s.pop("phone_serial", None)
    s.pop("phone_model", None)
    s.pop("phone_usb", None)
    s.pop("phone_wifi", None)
    core.save_settings(user["id"], s)
    PHONE_STATS.pop(user["id"], None)
    return {"ok": True}


@app.post("/api/phone/refresh")
def phone_refresh(user=Depends(auth)):
    refresh_phone(user)
    return PHONE_STATS.get(user["id"])


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


@app.get("/api/usage_audit")
def usage_audit(user=Depends(auth)):
    """Which installed skills (and, for the owner, connectors) are being used, and which never have been."""
    return extend.usage_report(user)


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
            cid = extend.add_connector(cat["name"], cat["description"], cat["command"], user["id"], cat.get("env"))
        else:
            cid = extend.add_connector(p.get("name", ""), p.get("description", ""), p.get("command", ""), user["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "A connector with that name already exists.")
    core.log(user["id"], f"Connector added: {cat['name'] if cat else p.get('name')}", "done")
    return {"id": cid}


@app.get("/api/duplicates")
def duplicates(user=Depends(auth)):
    return extend.find_duplicates(user)


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


@app.get("/cam/snap/{name}")
def cam_snap(name: str):
    """Serve a camera snapshot to the dashboard or a cast display on the LAN (no login: a display fetches it)."""
    import cameras
    p = cameras.snap_path(name)
    if not p:
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/jpeg")


@app.get("/api/cameras")
def cameras_list(user=Depends(auth)):
    import cameras
    return {"cameras": cameras.listing()}


@app.post("/api/cameras")
def cameras_add(p: dict = Body(...), user=Depends(admin)):
    import cameras
    preset = (p.get("preset") or "").lower()
    if preset == "okam":
        url = cameras.okam_url(p.get("ip", ""), p.get("password", ""), p.get("lens", 0), p.get("user") or "admin")
    elif preset == "wyze":
        url = cameras.wyze_url(p.get("bridge_host") or "127.0.0.1", p.get("cam_name", ""))
    else:
        url = p.get("url", "")
    onvif = None
    if p.get("onvif_host"):
        onvif = {"host": p["onvif_host"], "port": int(p.get("onvif_port") or 80),
                 "user": p.get("onvif_user") or "admin", "pass": p.get("onvif_pass") or p.get("password") or ""}
    cameras.add(p.get("name", ""), url, preset or "rtsp", onvif)
    core.log(user["id"], f"Camera added: {p.get('name','')}", "done")
    return {"cameras": cameras.listing()}


@app.delete("/api/cameras/{cid}")
def cameras_delete(cid: int, user=Depends(admin)):
    import cameras
    cameras.remove(cid)
    return {"ok": True}


@app.post("/api/cameras/{cid}/ptz")
def cameras_ptz(cid: int, p: dict = Body(...), user=Depends(admin)):
    import cameras
    cam = core.q("SELECT * FROM cameras WHERE id=?", (cid,), one=True)
    if not cam:
        raise HTTPException(404)
    try:
        return {"moved": cameras.ptz(dict(cam), p.get("action", ""))}
    except cameras.CameraError as e:
        return JSONResponse({"error": str(e)}, status_code=200)


@app.post("/api/cameras/{cid}/snapshot")
def cameras_snapshot(cid: int, user=Depends(auth)):
    import cameras, cast
    cam = core.q("SELECT * FROM cameras WHERE id=?", (cid,), one=True)
    if not cam:
        raise HTTPException(404)
    try:
        fn = cameras.snapshot(dict(cam))
    except cameras.CameraError as e:
        return JSONResponse({"error": str(e)}, status_code=200)
    return {"image_url": f"http://{cast.lan_ip()}:{cast.PORT}/cam/snap/{fn}", "name": cam["name"]}


@app.get("/cam/live/{cid}")
def cameras_live(cid: int, user=Depends(auth), fps: int = 8, q: int = 6, h: int = 480):
    """Live view: a continuous MJPEG stream the dashboard shows in a plain <img>. Logged-in users only
    (the session cookie rides along with the image request). One ffmpeg per viewer, killed on disconnect."""
    import cameras
    cam = core.q("SELECT * FROM cameras WHERE id=?", (cid,), one=True)
    if not cam:
        raise HTTPException(404)
    gen = cameras.mjpeg_stream(dict(cam), fps=fps, quality=q, height=h)
    return StreamingResponse(gen, media_type=f"multipart/x-mixed-replace; boundary={cameras.MJPEG_BOUNDARY}",
                             headers={"Cache-Control": "no-store"})


@app.get("/cast/audio/{name}")
def cast_audio(name: str):
    """Serve a generated announcement audio file to a cast device on the LAN (no login: the speaker fetches it)."""
    import cast as _cast
    p = _cast.audio_path(name)
    if not p:
        raise HTTPException(404)
    return FileResponse(str(p), media_type="audio/mpeg" if name.endswith(".mp3") else "audio/wav")


@app.get("/api/cast")
def cast_list(user=Depends(auth)):
    import cast as _cast
    return {"available": _cast.available(), "devices": _cast.discover() if _cast.available() else []}


@app.get("/api/deco")
def deco_get(user=Depends(admin)):
    return extend.deco_status(user)


@app.post("/api/deco")
def deco_set(p: dict = Body(...), user=Depends(admin)):
    extend.setup_deco(user, (p.get("host") or "").strip() or None, (p.get("password") or "").strip() or None,
                      bool(p.get("allow_write")), bool(p.get("allow_dangerous")))
    core.log(user["id"], "TP-Link Deco connector set up", "done")
    return extend.deco_status(user)


@app.get("/api/growth")
def growth_ep(user=Depends(admin)):
    import growth
    return growth.summary(user)


@app.get("/api/vendors")
def vendors_list(user=Depends(auth)):
    import vendors
    return {"vendors": vendors.listing(user)}


@app.post("/api/vendors")
def vendors_save(p: dict = Body(...), user=Depends(auth)):
    import vendors
    name = (p.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the vendor a name.")
    f = {k: p.get(k) for k in ("category", "target", "partners", "qbr", "status", "notes") if p.get(k) is not None}
    return vendors.add(user, name, **f)


@app.post("/api/vendors/brief")
def vendors_brief(p: dict = Body(...), user=Depends(auth)):
    import vendors
    return vendors.brief(user, (p.get("name") or "").strip(), fresh=p.get("fresh", True) is not False)


@app.delete("/api/vendors/{name}")
def vendors_del(name: str, user=Depends(auth)):
    import vendors
    return vendors.remove(user, name)


@app.get("/api/boardroom")
def boardroom_list(user=Depends(auth)):
    import boardroom
    return {"verdicts": boardroom.history(user), "seats": boardroom.seats_list(user)}


@app.post("/api/boardroom/ask")
def boardroom_ask(p: dict = Body(...), user=Depends(auth)):
    import boardroom
    return boardroom.consult(user, (p.get("seat") or "").strip(), (p.get("question") or "").strip())


@app.post("/api/boardroom/advisor")
def boardroom_add_advisor(p: dict = Body(...), user=Depends(auth)):
    import boardroom
    return boardroom.add_advisor(user, (p.get("name") or "").strip(),
                                 pillar=(p.get("pillar") or "").strip(), focus=(p.get("focus") or "").strip())


@app.delete("/api/boardroom/advisor/{key}")
def boardroom_del_advisor(key: str, user=Depends(auth)):
    import boardroom
    return boardroom.remove_advisor(user, key)


@app.get("/api/boardroom/{bid}")
def boardroom_get(bid: int, user=Depends(auth)):
    import boardroom
    return boardroom.get(user, bid)


@app.post("/api/boardroom")
def boardroom_run(p: dict = Body(...), user=Depends(auth)):
    import boardroom
    decision = (p.get("decision") or "").strip()
    if not decision:
        raise HTTPException(400, "What's the decision?")
    options = [o for o in (p.get("options") or []) if str(o).strip()]
    mode = "quick" if str(p.get("mode") or "full").lower().startswith("q") else "full"
    seats = p.get("seats") or None
    return boardroom.run(user, decision, options=options, mode=mode, seats=seats)


@app.post("/api/tv/connect")
def tv_connect_ep(p: dict = Body(...), user=Depends(admin)):
    import tvadb
    return tvadb.connect((p.get("target") or "").strip())


@app.get("/api/tv/apps")
def tv_apps_ep(target: str, user=Depends(admin)):
    import tvadb
    return tvadb.apps(target.strip())


@app.post("/api/tv/key")
def tv_key_ep(p: dict = Body(...), user=Depends(admin)):
    import tvadb
    return tvadb.key((p.get("target") or "").strip(), (p.get("key") or "").strip())


@app.post("/api/tv/text")
def tv_text_ep(p: dict = Body(...), user=Depends(admin)):
    import tvadb
    return tvadb.text((p.get("target") or "").strip(), p.get("text") or "")


@app.post("/api/tv/launch")
def tv_launch_ep(p: dict = Body(...), user=Depends(admin)):
    import tvadb
    return tvadb.launch((p.get("target") or "").strip(), (p.get("package") or "").strip())


@app.get("/api/emergent")
def emergent_get(user=Depends(admin)):
    return extend.emergent_status(user)


@app.post("/api/emergent")
def emergent_set(p: dict = Body(...), user=Depends(admin)):
    extend.setup_emergent(user, (p.get("email") or "").strip() or None, (p.get("password") or "").strip() or None)
    core.log(user["id"], "Emergent.sh connector set up", "done")
    return extend.emergent_status(user)


# ---------- owner view ----------
@app.get("/api/admin/family")
def family(user=Depends(admin)):
    out = []
    for u in core.q("SELECT * FROM users ORDER BY id"):
        s = core.settings(u)
        out.append({"id": u["id"], "name": u["name"], "brand": s["brand"], "site": s["site"], "brain": s["brain"],
                    "has_password": bool(u["pw_hash"]), "roots": core.user_roots(u),
                    "store_on": store_allowed(u),
                    "bible_on": bible.allowed(u),
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
    if "bible_on" in p:
        s = core.settings(u)
        s["bible_on"], s["bible_set"] = bool(p["bible_on"]), True
        core.save_settings(uid, s)
    if "store_on" in p:
        s = core.settings(u)
        s["store_on"], s["store_set"] = bool(p["store_on"]), True
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
def admin_backup_zip(user=Depends(admin)):
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
    last_sites = last_phone = 0
    while True:
        try:
            TAILSCALE.update(t=time.time(), v=tailscale_status())
            if time.time() - last_phone > 110:
                last_phone = time.time()
                for u in core.q("SELECT * FROM users"):
                    if core.settings(u).get("phone_serial") and not BUSY.get(u["id"]):
                        refresh_phone(u)
            if time.time() - last_sites > 180:
                last_sites = time.time()
                for u in core.q("SELECT * FROM users"):
                    site = core.settings(u).get("site")
                    if site:
                        prev = SITES.get(site)
                        SITES[site] = res = tools.site_check(site)
                        if prev and prev["ok"] and not res["ok"]:
                            core.log(u["id"], f"{site} looks down", "waiting")
                            watch.alert(u, "site", "Website down", f"{site} is not answering. Trillion checks it every 3 minutes.")
            now = datetime.datetime.now()
            for u in core.q("SELECT * FROM users"):
                try:
                    vp = voicecall.prefs(u)
                    if vp["on"] and vp["target"] and not voicecall.busy(u["id"]) and not meeting.active(u):
                        for plan in voicecall.due_plans(u, now)[:1]:
                            voicecall.mark_ran(plan["id"], now.date().isoformat(), once=bool(plan["once_date"]))
                            call_in_background(u, plan["topic"], fallback=lambda u=u: watch.send(u, "Trillion tried to call you", "Your planned call was not answered. The update is in the chat."))
                except Exception:
                    pass
                try:
                    nudge.check(u, SITES, now)
                except Exception:
                    pass
                try:
                    morning.check(u, SITES, now)
                except Exception:
                    pass
                try:                              # website campaign: due follow-ups + reply detection, every 20 min
                    if now.minute % 20 == 0:
                        _ts = (TAILSCALE.get("v") or {})
                        _host = _ts.get("dns") or _ts.get("ip")
                        if _host:
                            leads.tick(u, _host, "https")
                except Exception:
                    pass
            try:
                if backup.due(now) and not backup.JOB.get("running"):
                    backup.start(backup.owner_settings().get("backup_target"))
            except Exception:
                pass
            if now.hour == 23 and now.minute >= 30:
                core.prune_steps()                    # drop old workflow trails
                for u in core.q("SELECT * FROM users"):
                    s = core.settings(u)
                    if s.get("last_review") != core.today():
                        s["last_review"] = core.today()
                        core.save_settings(u["id"], s)
                        if s.get("daily_review"):
                            try:
                                daily_review(u)          # learn lasting knowledge from the day
                            except Exception:
                                pass
                        try:
                            evolve.suggest(u, llm_chat)  # spot what to automate; apply safe ones if allowed
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(60)


def wa_background():
    """Every 20 seconds, for each login with a linked phone and the inbox switched on."""
    polled = {}
    while True:
        time.sleep(4)
        try:
            for u in core.q("SELECT * FROM users"):
                st = core.settings(u)
                in_meeting = bool(meeting.active(u))            # also ends a meeting whose time ran out
                pace = 8 if in_meeting else 20                  # look more often in a meeting, so a ringing call is caught
                if time.time() - polled.get(u["id"], 0) < pace:
                    continue
                if st.get("phone_serial") and (st.get("wa_inbox") or in_meeting) and not BUSY.get(u["id"]):
                    polled[u["id"]] = time.time()
                    last = WA_STATE.get(u["id"]) or {}
                    if not last.get("ok", True) and time.time() - last.get("t", 0) < 90:
                        continue          # phone was unreachable a moment ago: do not hammer it
                    wa_poll(u)
        except Exception:
            pass


@app.on_event("startup")
def start_background():
    threading.Thread(target=background, daemon=True).start()
    threading.Thread(target=wa_background, daemon=True).start()
    threading.Thread(target=gumroad.background, daemon=True).start()
    threading.Thread(target=voicecall.run_registrar, daemon=True).start()
    threading.Thread(target=apps.retire_dropped_apps, daemon=True).start()   # erase apps the owner removed for good

    def warm():  # load hearing and the brains up front so the first request is not slow
        try:
            load_whisper()
            if any(voicecall.prefs(u)["on"] for u in core.q("SELECT * FROM users")):
                voicecall.warm_call_model()             # the stronger model for phone calls, never during a call
        except Exception:
            pass
        try:
            names = [m["name"] for m in core.models()["list"]]
            for u in core.q("SELECT * FROM users WHERE is_admin=1 LIMIT 1"):
                st = core.settings(u)
                for want in (st.get("local_model", "qwen2.5:14b"), st.get("fast_model") or "qwen2.5:3b"):
                    if want in names:
                        httpx.post(core.OLLAMA + "/api/chat", timeout=300,
                                   json={"model": want, "messages": [], "keep_alive": "4h",
                                         "options": {"num_ctx": 12288 if want == st.get("local_model", "qwen2.5:14b") else 6144}})
        except Exception:
            pass
        try:                                            # flag anything broken at startup, so the owner sees it without asking
            time.sleep(8)
            for u in core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1"):
                res = selfcheck.run(u, SITES, HEARING, TAILSCALE)
                bad = [c for c in res.get("checks", []) if c["status"] == "fail"]
                if bad:
                    core.log(u["id"], "Self-check at start: " + "; ".join(f"{c['name']} - {c['detail']}" for c in bad)[:350], "waiting")
        except Exception:
            pass
    threading.Thread(target=warm, daemon=True).start()
