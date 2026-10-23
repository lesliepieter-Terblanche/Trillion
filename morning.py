"""Morning briefing: one friendly start-of-day rundown, at a time you choose.

It pulls together what Trillion already knows - what's waiting on you (the same items as the check-ins:
approvals, call-backs, unanswered WhatsApps, a site that's down), a quick snapshot of your Gumroad store,
and, if you like, the morning Bible verse - and delivers it as a chat message, a phone notification, and
(optionally) spoken aloud on a Google Home. Everything is local; nothing leaves the PC.

This is the positive "good morning, here's your day" counterpart to nudge.py (which is the "something
needs you" check-in). It fires once a day at the chosen time and never during the watch's quiet hours.
"""
import datetime
import re
import time

import core

DEFAULTS = {"on": False, "time": "07:00", "chat": True, "phone": True,
            "speak": False, "device": "", "store": True, "verse": False}


def _hhmm(v, default):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else default


def prefs(user):
    s = core.settings(user).get("morning") or {}
    p = dict(DEFAULTS)
    p.update({k: s[k] for k in DEFAULTS if k in s})
    p["time"] = _hhmm(p.get("time"), DEFAULTS["time"])
    return p


def save_prefs(user, data):
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    st = core.settings(row)
    cur = prefs(row)
    merged = dict(cur)
    for k in DEFAULTS:
        if k in data:
            merged[k] = data[k]
    merged["on"] = bool(merged["on"]); merged["chat"] = bool(merged["chat"]); merged["phone"] = bool(merged["phone"])
    merged["speak"] = bool(merged["speak"]); merged["store"] = bool(merged["store"]); merged["verse"] = bool(merged["verse"])
    merged["time"] = _hhmm(merged.get("time"), DEFAULTS["time"])
    merged["device"] = str(merged.get("device") or "")[:80]
    st["morning"] = merged
    core.save_settings(user["id"], st)
    return prefs(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


# ---------- building the briefing ----------
def _greeting(name, now):
    who = (name or "").strip().split()[0].capitalize() if name else ""
    return f"Good morning{', ' + who if who else ''}. It's {now.strftime('%A, %-d %B')}."


def build(user, sites=None, now=None):
    """Returns {'title', 'lines' (for chat/phone), 'spoken' (one flowing paragraph for a speaker)}."""
    now = now or datetime.datetime.now()
    p = prefs(user)
    lines, spoken = [], [_greeting(user.get("name"), now)]

    # What's waiting on you - reuse the check-in collector so the two always agree.
    waiting = []
    try:
        import nudge
        waiting = [i for i in nudge.collect(user, sites) if not i.get("done")]
    except Exception:
        waiting = []
    if waiting:
        lines.append(f"Waiting on you ({len(waiting)}):")
        for it in waiting[:6]:
            lines.append("  - " + it["text"])
        if len(waiting) > 6:
            lines.append(f"  - and {len(waiting) - 6} more")
        top = "; ".join(it["text"] for it in waiting[:3])
        spoken.append(f"{len(waiting)} thing{'s' if len(waiting) != 1 else ''} waiting on you: {top}.")
    else:
        lines.append("Nothing waiting on you - inbox and approvals are clear.")
        spoken.append("Nothing is waiting on you - you're all clear.")

    # Your store (only if Gumroad is connected).
    if p["store"]:
        try:
            import gumroad
            if gumroad.overview(user).get("connected"):
                s = gumroad.say_sales(user, "week")
                lines.append(s)
                spoken.append(s)
        except Exception:
            pass

    # Competitor sites that changed since yesterday (only once Competitor watch is installed and has sites).
    try:
        import apps
        if apps.usable(user, "changedetection"):
            ch = apps.competitor_changes(user, days=1)
            if ch:
                lines.append(f"Competitors changed ({len(ch)}):")
                for c in ch[:4]:
                    what = "; ".join(c["added"][:2]) or ("removed: " + "; ".join(c["removed"][:2]))
                    lines.append(f"  - {c['name']}: {what}"[:220])
                spoken.append(f"{len(ch)} competitor site{'s' if len(ch) != 1 else ''} changed overnight, starting with "
                              f"{ch[0]['name']}.")
    except Exception:
        pass

    # Morning verse (only if you asked for it).
    if p["verse"]:
        try:
            import bible
            v = bible.daily(user, "morning")
            ref = bible.say(v).split(". ")[0]
            lines.append(f"Verse for the day - {ref}: {v['text']}")
            spoken.append(f"And your verse for the day. {bible.say(v)}")
        except Exception:
            pass

    title = "Your morning briefing"
    return {"title": title, "lines": lines, "spoken": " ".join(spoken)}


# ---------- delivering it ----------
def deliver(user, brief, p=None):
    p = p or prefs(user)
    text = "\n".join(brief["lines"])
    sent = {"chat": False, "phone": False, "spoken": False}
    if p["chat"]:
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
               (user["id"], "assistant", f"{brief['title']}\n{text}", time.time()))
        sent["chat"] = True
    if p["phone"] and core.settings(user).get("phone_serial"):
        try:
            import watch
            sent["phone"] = watch.send(user, brief["title"], text.replace("\n", "  "))["ok"]
        except Exception:
            pass
    if p["speak"]:
        try:
            import cast
            if cast.available():
                cast.announce(brief["spoken"], p["device"] or None)
                sent["spoken"] = True
        except Exception:
            pass
    try:
        core.log(user["id"], f"Morning briefing: {len(brief['lines'])} line(s)", "done")
    except Exception:
        pass
    return sent


def send_now(user, sites=None):
    """Build and deliver right now (the 'Send now' button / a chat request)."""
    brief = build(user, sites)
    sent = deliver(user, brief)
    return {"sent": sent, "title": brief["title"], "lines": brief["lines"]}


# ---------- the daily trigger (called from the background loop every minute) ----------
def check(user, sites=None, now=None):
    now = now or datetime.datetime.now()
    p = prefs(user)
    if not p["on"]:
        return None
    try:
        import meeting
        if meeting.active(user):
            return None
    except Exception:
        pass
    if now.strftime("%H:%M") != p["time"]:
        return None
    st = core.settings(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))
    if st.get("morning_last") == now.date().isoformat():     # already sent today
        return None
    try:
        import watch
        if watch.quiet_now(watch.prefs(user), now):
            return None
    except Exception:
        pass
    st["morning_last"] = now.date().isoformat()
    core.save_settings(user["id"], st)
    brief = build(user, sites, now)
    return {"sent": deliver(user, brief, p), "items": len(brief["lines"])}


def state(user):
    p = prefs(user)
    devices = []
    try:
        import cast
        if cast.available():
            devices = [d.get("name") for d in cast.discover() if d.get("name")]
    except Exception:
        devices = []
    return {"prefs": p, "cast_devices": devices,
            "can_speak": bool(devices), "has_phone": bool(core.settings(user).get("phone_serial"))}
