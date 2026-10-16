"""Meeting mode: while a login is in a meeting, Trillion answers their WhatsApps on their own phone so they are not
disturbed, and hands them a summary afterwards.

- Messages: one short, honest reply per person per meeting ("in a meeting until 11:00, I will get back to you"),
  written by the brain to fit what was asked, never promising anything. Further messages from the same person are
  collected quietly, so two auto-repliers cannot talk to each other for ever.
- Voice notes: the newest one is taken off the phone and turned into words, so the reply and the summary know what
  was said.
- Calls: the ringing call is declined and the caller gets a message saying when there will be a call back.
- Groups are never answered. People on the "let through" list are never auto-answered; they tap the watch instead.
- When the meeting ends (the time runs out, or "I'm out of the meeting"), a summary goes to the dashboard, the chat
  and the watch.
"""
import datetime
import json
import os
import re
import tempfile
import time
from pathlib import Path

import core

VOICE_DIRS = ["/storage/emulated/0/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Voice Notes",
              "/storage/emulated/0/WhatsApp/Media/WhatsApp Voice Notes",
              "/storage/emulated/0/Android/media/com.whatsapp.w4b/WhatsApp Business/Media/WhatsApp Business Voice Notes"]
DURATIONS = {"30m": 1800, "1h": 3600, "90m": 5400, "2h": 7200, "3h": 10800, "open": 0}
MAX_REPLIES = 25                  # a safety limit per meeting, so nothing can run away
_SEEN_VOICE = set()

core.x("""CREATE TABLE IF NOT EXISTS meetings(id INTEGER PRIMARY KEY, user_id INTEGER, started REAL, until REAL, ended REAL,
          reason TEXT DEFAULT '', log TEXT DEFAULT '[]', summary TEXT DEFAULT '')""")


# ---------- settings and state ----------
def prefs(user):
    s = core.settings(user).get("meeting_prefs") or {}
    return {"calls": s.get("calls", True), "voice": s.get("voice", True), "vip": [str(x).strip() for x in s.get("vip", []) if str(x).strip()],
            "reason": str(s.get("reason") or "in a meeting")[:60], "tone": s.get("tone", "friendly"),
            "template": str(s.get("template") or "")[:400]}


def _reason_phrase(reason):
    """Tidy whatever is in the reason box so it always slots into 'I'm ___ right now' and reads correctly,
    whether the user typed a short phrase ('in a meeting') or a whole sentence ('I am currently in a meeting.')."""
    r = re.sub(r"\s+", " ", str(reason or "")).strip().strip(".!?,; ")
    while True:
        n = re.sub(r"^(?:i\s*'?\s*m|i\s+am|currently|right\s+now|busy)\b[\s,]*", "", r, flags=re.I).strip()
        if n == r:
            break
        r = n
    r = re.sub(r"[\s,]*right now$", "", r, flags=re.I).strip()
    return r or "in a meeting"


def fill_template(tmpl, back, name):
    """Fill a user-written full message. {time}/{back} -> when they'll be back; {name} -> their name."""
    out = re.sub(r"\{\s*(?:time|back)\s*\}", back, str(tmpl), flags=re.I)
    out = re.sub(r"\{\s*name\s*\}", name, out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip()[:700]


def save_prefs(user, data):
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    st = core.settings(row)
    old = prefs(row)
    vip = data.get("vip", old["vip"])
    if isinstance(vip, str):
        vip = [x for x in re.split(r"[,\n]", vip)]
    st["meeting_prefs"] = {"calls": bool(data.get("calls", old["calls"])), "voice": bool(data.get("voice", old["voice"])),
                           "vip": [x.strip()[:40] for x in vip if x.strip()][:30],
                           "reason": str(data.get("reason") or old["reason"]).strip()[:60] or "in a meeting",
                           "tone": data.get("tone") if data.get("tone") in ("friendly", "formal") else old["tone"],
                           "template": str(data.get("template", old["template"]) or "").strip()[:400]}
    core.save_settings(user["id"], st)
    return prefs(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


def current(user):
    r = core.q("SELECT * FROM meetings WHERE user_id=? AND ended IS NULL ORDER BY id DESC LIMIT 1", (user["id"],), one=True)
    return dict(r) if r else None


def active(user):
    m = current(user)
    if m and m["until"] and time.time() >= m["until"]:
        finish(user)                                   # its time has run out
        return None
    return m


def start(user, seconds=3600, until=None, reason=""):
    if current(user):
        finish(user, quiet=True)
    now = time.time()
    end = until or (now + seconds if seconds else 0)
    mid = core.x("INSERT INTO meetings(user_id,started,until,reason) VALUES(?,?,?,?)", (user["id"], now, end, reason[:60]))
    core.log(user["id"], "Meeting mode on" + (f" until {hhmm(end)}" if end else ""), "info")
    return state(user)


def hhmm(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""


def _log(m, entry):
    log = json.loads(m["log"] or "[]")
    log.append(entry)
    core.x("UPDATE meetings SET log=? WHERE id=?", (json.dumps(log), m["id"]))
    m["log"] = json.dumps(log)


def finish(user, quiet=False):
    """End the meeting and hand over what happened."""
    m = current(user)
    if not m:
        return None
    log = json.loads(m["log"] or "[]")
    summary = summarise(user, log)
    core.x("UPDATE meetings SET ended=?, summary=? WHERE id=?", (time.time(), summary, m["id"]))
    if log and not quiet:
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant", summary, time.time()))
        try:
            import watch
            watch.alert(user, "meeting", "While you were in your meeting", summary[:380])
        except Exception:
            pass
    core.log(user["id"], "Meeting mode off" + (f": {len(log)} thing{'s' if len(log) != 1 else ''} came in" if log else ""), "done")
    return summary


def summarise(user, log):
    if not log:
        return "Nothing came in on WhatsApp while you were in your meeting."
    people = {}
    for e in log:
        people.setdefault(e["who"], []).append(e)
    lines = []
    for who, es in people.items():
        calls = sum(1 for e in es if e["kind"] == "call")
        bits = []
        if calls:
            bits.append(f"called {calls}x" if calls > 1 else "called")
        for e in es:
            if e["kind"] != "call":
                bits.append(("voice note: " if e["kind"] == "voice note" else "") + f"\"{e['text'][:160]}\"")
        what = "; ".join(bits)
        tag = " (let through to you)" if any(e.get("vip") for e in es) else (" (answered)" if any(e.get("replied") for e in es) else "")
        lines.append(f"- {who}{tag}: {what}")
    n_calls = sum(1 for e in log if e["kind"] == "call")
    head = (f"While you were in your meeting: {len(people)} {'person' if len(people) == 1 else 'people'} got in touch"
            + (f", {n_calls} missed call{'s' if n_calls != 1 else ''}" if n_calls else "") + ".")
    return head + "\n" + "\n".join(lines)


def state(user):
    m = active(user)
    last = core.q("SELECT * FROM meetings WHERE user_id=? AND ended IS NOT NULL ORDER BY id DESC LIMIT 1", (user["id"],), one=True)
    return {"on": bool(m), "until": m["until"] if m else 0, "started": m["started"] if m else 0,
            "so_far": len(json.loads(m["log"] or "[]")) if m else 0, "prefs": prefs(user),
            "last": {"ended": last["ended"], "summary": last["summary"]} if last else None,
            "linked": bool(core.settings(user).get("phone_serial")),
            "auto_unlock": bool(core.settings(user).get("phone_auto_unlock"))}


# ---------- what came in ----------
def _vip(p, who):
    w = who.lower()
    return any(v.lower() in w or w in v.lower() for v in p["vip"])


def _replied_to(m, chat):
    return any(e.get("replied") and e["chat"] == chat for e in json.loads(m["log"] or "[]"))


def handle(user, row, send, decline=None, voice_text=None):
    """One new WhatsApp while in a meeting. send(chat, text) sends on the phone; decline() declines a ringing call;
    voice_text() returns the newest voice note as words. Returns what was done."""
    m = active(user)
    if not m:
        return None
    p = prefs(user)
    entry = {"t": time.time(), "who": row["sender"], "chat": row["chat"], "kind": row["kind"], "text": row["text"][:500]}
    if row.get("is_group"):
        entry["group"] = True
        _log(m, entry)
        return "group"
    if _vip(p, row["sender"]):
        entry["vip"] = True
        _log(m, entry)
        try:
            import watch
            watch.alert(user, "meeting", f"{row['sender']} ({row['kind']})", row["text"][:300])
        except Exception:
            pass
        return "let through"
    if row["kind"] == "voice note" and p["voice"] and voice_text:
        try:
            words = voice_text()
            if words:
                entry["text"] = words[:1000]
        except Exception:
            pass
    did = "noted"
    if row["kind"] == "call" and p["calls"] and decline and re.search(r"\bincoming\b|\bringing\b|\bcalling\b", row["text"], re.I):
        try:
            if decline():
                entry["declined"] = True
                did = "declined"
        except Exception:
            pass
    replies = sum(1 for e in json.loads(m["log"] or "[]") if e.get("replied"))
    if (row["kind"] != "call" or p["calls"]) and not _replied_to(m, row["chat"]) and replies < MAX_REPLIES:
        text = compose(user, m, p, entry)
        try:
            ok = send(row["chat"], text)
        except Exception:
            ok = False
        if ok:
            entry["replied"] = text
            did = "answered" if did == "noted" else did + " and answered"
    _log(m, entry)
    return did


def compose(user, m, p, entry):
    """The reply: short, honest, no promises. The brain fits it to what was asked; a plain one if the brain is busy."""
    back = f"after {hhmm(m['until'])}" if m["until"] else "as soon as I am out"
    if p.get("template"):                                    # the user wrote the whole message themselves
        return fill_template(p["template"], back, user["name"])
    reason = _reason_phrase(p["reason"])
    if entry["kind"] == "call":
        return f"Sorry I missed your call. I'm {reason} right now and will call you back {back}. If it's urgent, send me a message here."
    plain = f"Hi, I'm {reason} right now and will get back to you {back}."
    try:
        msg = core.LLM(user, [
            {"role": "system", "content": f"You answer WhatsApps for {user['name']} while they are {reason}. Write ONE short reply "
             f"(at most two sentences), {'warm and casual' if p['tone'] == 'friendly' else 'polite and professional'}, in the language "
             f"the person wrote in (often Afrikaans or English). Say {user['name']} is {reason} and will reply {back}. "
             "If they asked something, acknowledge it in a few words but never answer it, never agree to anything, never give "
             "prices, times, money or promises. Write as the first person, as if you were " + user["name"] + ". No quotes, no signature."},
            {"role": "user", "content": f"{entry['who']} wrote ({entry['kind']}): {entry['text'][:800]}"}], fast=True)
        text = re.sub(r"\s+", " ", (msg or {}).get("content") or "").strip().strip('"“”')
        if 8 < len(text) < 400 and not re.search(r"\bR\s?\d|\$\d|\bI (agree|promise|will pay|accept)\b", text, re.I):
            return text
    except Exception:
        pass
    return plain


# ---------- the phone ----------
def phone_decline(user):
    """Tap Decline on WhatsApp's ringing-call screen."""
    import phone
    nodes = phone._nodes(user)
    hit = phone._find(nodes, ids=("com.whatsapp:id/decline_incoming_call_view", "com.whatsapp:id/decline_button",
                                  "com.whatsapp.w4b:id/decline_incoming_call_view"), label=r"^(decline|reject|weier)$")
    if not hit:
        return False
    phone.sh(user, f"input tap {hit['x']} {hit['y']}")
    return True


def phone_voice_text(user, transcribe):
    """The newest voice note on the phone, not used before, turned into words."""
    import phone
    newest = ""
    for d in VOICE_DIRS:
        out = phone.sh(user, f"find '{d}' -type f -name '*.opus' -mmin -15 2>/dev/null")
        files = [ln.strip() for ln in (out or "").splitlines() if ln.strip().endswith(".opus") and ln.strip() not in _SEEN_VOICE]
        if files:
            newest = sorted(files)[-1]                 # PTT-YYYYMMDD-WA0007: the name sorts by time
            break
    if not newest:
        return ""
    _SEEN_VOICE.add(newest)
    serial = phone.working_serial(user)
    tmp = Path(tempfile.mkdtemp()) / "note.opus"
    code, out = phone._run(["-s", serial, "pull", newest, str(tmp)], timeout=60)
    if code != 0 or not tmp.exists():
        return ""
    try:
        return transcribe(tmp.read_bytes())
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------- chat ----------
ON = re.compile(r"\b(?:i'?m|i am|going|heading|off)\b[^.?!]{0,15}\b(?:in(?:to)? (?:a |my |the )?(?:meeting|call|session|class|church|interview)|"
                r"busy|meeting mode on)\b|^\s*(?:in|into|off to)\s+(?:a |my |the )?(?:meeting|call)\b|\bmeeting mode\s*(?:on)?\s*$|\b(?:switch|turn|put)\b[^.?!]{0,10}\bmeeting mode\b(?!.*\boff\b)", re.I)
OFF = re.compile(r"\b(?:out of|done with|finished (?:with )?|back from|left)\b[^.?!]{0,12}\b(?:meeting|call|session)\b|"
                 r"\bmeeting(?:'s| is)? (?:over|done|finished)\b|\bmeeting mode off\b|\b(?:end|stop|switch off|turn off)\b[^.?!]{0,8}\bmeeting mode\b|"
                 r"\bi'?m back\b", re.I)
UNTIL = re.compile(r"\b(?:until|till|til|to)\s+(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?\b", re.I)
FOR = re.compile(r"\bfor\s+(?:an?|one|(\d+(?:\.\d+)?)|half an?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)


def parse(text, now=None):
    """('on', until_ts) / ('off', None) / None."""
    if len(text) > 120:
        return None
    if OFF.search(text):
        return ("off", None)
    if not ON.search(text):
        return None
    now = now or datetime.datetime.now()
    u = UNTIL.search(text)
    if u:
        h, mi = int(u.group(1)), int(u.group(2) or 0)
        ap = (u.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        elif not ap and h < 7:                       # "until 3" during the working day means 15:00
            h += 12
        end = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if end <= now:
            end += datetime.timedelta(days=1) if (end + datetime.timedelta(hours=12)) <= now else datetime.timedelta(hours=12)
        return ("on", end.timestamp())
    f = FOR.search(text)
    if f:
        if f.group(0).lower().startswith("for half"):
            n = 0.5
        else:
            n = float(f.group(1) or 1)
        secs = n * (60 if f.group(2).lower().startswith("m") else 3600)
        return ("on", (now + datetime.timedelta(seconds=secs)).timestamp())
    return ("on", (now + datetime.timedelta(hours=1)).timestamp())
