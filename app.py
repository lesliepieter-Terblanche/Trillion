"""Trillion: a local agent OS for one PC and one family. Run with Start-Trillion.bat."""
import io, json, os, re, time, threading, datetime, zipfile

import httpx
from fastapi import FastAPI, Request, Response, HTTPException, Depends, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse

import core
import extend
import phone
import routines
import tools
import video
import attach

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
import bible
bible.init()
video.init()
attach.init()
import gumroad
import watch
import siteread
gumroad.init()
routines.init()
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
BUSY, STOP, SITES, LAST_PROVIDER = {}, set(), {}, {}
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
            return r.json()["choices"][0]["message"]
        except Exception as e:
            core.log(uid, f"Cloud brain ({brain}) failed, used the local one instead: {type(e).__name__}", "waiting")
    model = core.pick_local_model(s.get("local_model", "qwen2.5:14b"))
    if fast:   # a plain question needs no tools: the small model answers several times faster
        small = core.pick_local_model(s.get("fast_model") or "qwen2.5:3b")
        if small.split(":")[0] in ("qwen2.5", "gemma3", "llama3.2", "phi4-mini") and small != model:
            model = small
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
- On the user's phone, never confirm a payment or purchase, delete anything, or send a message by tapping unless the user asked for exactly that in this request.
- When the user tells you a lasting fact or preference, save it with save_note.
- Always reply in English.
- Replies are spoken aloud: keep them short and natural, no markdown, no lists.

Your personality: {PERSONAS.get(s.get('personality'), PERSONAS['professional'])[1]}
{PERSONA_RULE}

Relevant things you remember:
{memory}

Skills switched on for this user (call use_skill with the name before doing that kind of job):
{skills}

Connectors switched on for this user (call connector_tools first, then connector_call):
{connectors}

Routines this user has set up (each runs by itself when they say its phrase; they are edited on the Routines page):
{routines.prompt_line(user)}"""


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


# words that mean she has to DO something or look something up: those go to the full brain with its tools
NEEDS_TOOLS = re.compile(
    r"\b(creat\w*|make|made|build|write|wrote|draft|generat\w*|open|launch|start|run|delet\w*|remov\w*|mov(?:e|ing)|renam\w*|cop(?:y|ies)|past\w*|"
    r"find|search|look\s+(?:up|for|in)|list|show|read|check|sav\w*|remember|recall|not(?:e|es)|memory|forget|send|sent|messag\w*|text|whats ?app|"
    r"email|call|phone|cell|contact|file|folder|document|docx?|word|excel|sheet|spreadsheet|powerpoint|deck|slide|pdf|download\w*|desktop|"
    r"website|site|browser|web|online|internet|skill|connector|install|update|video|screenshot|window|app|program|schedul\w*|remind\w*|"
    r"today|tomorrow|yesterday|latest|current|news|weather|price|my)\b", re.I)


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
    return [s_ for s_ in tools.SPECS if s_["function"]["name"] not in skip]


VIDEO_RE = re.compile(r"\b(create|make|generate|produce|build|render)\b[^.?!]{0,80}\bvideo\b", re.I)


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
    return (f"Your video is ready: {res['title']}, {round(res['seconds'])} seconds, "
            f"{'with' if res['voice_over'] else 'without'} voice-over, {res['footage_clips']} stock clip{'s' if res['footage_clips'] != 1 else ''}"
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
        BUSY[uid] = False
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
        core.set_task(tid, status="completed", detail=reply[:200])
        return {"reply": reply, "task_id": tid, "provider": "built-in"}
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
    specs = tools_for(user, text)
    fast = bool(core.settings(user).get("fast_answers", True)) and not NEEDS_TOOLS.search(text) and len(text) < 400 and not files
    if fast:
        specs = None
    LIVE.pop(uid, None)
    msgs = [{"role": "system", "content": system_prompt(user, text) + (
        "\n\nThis turn is a plain question: you have no tools now. Just answer it, briefly and directly." if fast else "")}] + [dict(h) for h in hist]
    status, waiting, reply = "completed", False, ""
    try:
        for _ in range(10):
            if uid in STOP:
                reply, status = "Stopped.", "failed"
                break
            m = llm_chat(user, msgs, specs, fast=fast, live=True)
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
    if not waiting and re.search(r"\bapprov", reply, re.I) and not re.search(r"\bapprov", text, re.I):
        # the model talked about an approval that does not exist: say what really happened instead
        reply = ("I did not do that: nothing was prepared or sent. For a WhatsApp, say it like this: "
                 "send a WhatsApp to Sarie saying I am running late." if PHONE_WORDS.search(text) else
                 "I did not do that: nothing is waiting for approval. Tell me again in other words and I will try properly.")
        status = "failed"
    if file_notes:
        reply = (reply + "\n\n" + " ".join(file_notes)).strip()
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (uid, "assistant", reply, time.time()))
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


@app.get("/api/uploads/{fid}/file")
def upload_file(fid: int, user=Depends(auth)):
    import os, mimetypes
    f = attach.get(user, fid)
    if not f or not os.path.exists(f["path"]):
        raise HTTPException(404, "That file is no longer there")
    return FileResponse(f["path"], media_type=mimetypes.guess_type(f["name"])[0] or "application/octet-stream", filename=f["name"])


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
    path = core.user_home(user) / "Trillion Images" / name
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
EDITABLE = ["freevideo_url", "wake_word", "video_ai_images", "wa_inbox", "fast_answers", "theme", "quick_actions", "whatsapp_auto_contacts", "phone_own_number", "phone_full_control", "personality", "phone_country_code", "assistant_name", "brand", "accent", "site", "brain", "local_model", "cloud_model", "voice",
            "eleven_voice_id", "daily_review"]


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
        elif name == "pixabay":
            r = httpx.get("https://pixabay.com/api/videos/", params={"key": key, "q": "city", "per_page": 3}, timeout=15)
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
    fresh = 0
    for it in items:
        d = hashlib.sha1(f"{uid}|{it['chat']}|{it['sender']}|{it['text']}|{it['when'] or it['key']}".encode()).hexdigest()
        if core.q("SELECT 1 FROM wa_inbox WHERE dedupe=?", (d,), one=True):
            continue
        core.x("INSERT INTO wa_inbox(user_id,chat,sender,text,kind,is_group,ts,dedupe) VALUES(?,?,?,?,?,?,?,?)",
               (uid, it["chat"], it["sender"], it["text"], it["kind"], 1 if it["group"] else 0, it["when"] or time.time(), d))
        fresh += 1
        who = it["sender"] + (f" in {it['chat']}" if it["group"] else "")
        core.log(uid, f"WhatsApp from {who}: {it['text'][:80]}", "voice")
    return fresh


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


def wa_background():
    """Every 20 seconds, for each login with a linked phone and the inbox switched on."""
    while True:
        time.sleep(20)
        try:
            for u in core.q("SELECT * FROM users"):
                st = core.settings(u)
                if st.get("phone_serial") and st.get("wa_inbox") and not BUSY.get(u["id"]):
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

    def warm():  # load hearing and the brains up front so the first request is not slow
        try:
            load_whisper()
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
    threading.Thread(target=warm, daemon=True).start()
