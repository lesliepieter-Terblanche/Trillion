"""Check-ins: Trillion tells a login when something is waiting on them, and rings the phone when it is urgent.

What counts as waiting:
- something that needs their OK (an approval), urgent after 30 minutes;
- a job that did not work, or one still running after two hours;
- people owed a call-back or an answer after meeting mode (urgent for calls, right after the meeting);
- their website being down (urgent);
- overdue invoices, when Invoice Ninja is connected;
- WhatsApps left unanswered for more than two hours, when the WhatsApp inbox is on.

At the chosen times (08:00 and 14:00 to start with) everything still open comes as one notification on the phone and
watch, and in the chat. In between, an urgent item comes at once, and can ring the phone. Nothing is sent during a
meeting, during the watch's quiet hours, or about something marked done. The same thing is not repeated within four hours.
"""
import datetime
import json
import re
import time

import core

REPEAT = 4 * 3600
CALLBACK_MAX = 2                  # a call-back is mentioned at most twice
DEFAULT_TIMES = ["08:00", "14:00"]

core.x("""CREATE TABLE IF NOT EXISTS nudges(user_id INTEGER, key TEXT, first REAL, last REAL, count INTEGER DEFAULT 0,
          done INTEGER DEFAULT 0, PRIMARY KEY(user_id, key))""")


def _hhmm(v, default):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else default


def prefs(user):
    s = core.settings(user).get("nudge") or {}
    times = [t for t in (_hhmm(x, "") for x in s.get("times", DEFAULT_TIMES)) if t] or DEFAULT_TIMES
    return {"on": s.get("on", True), "times": sorted(set(times))[:6], "ring": s.get("ring", True),
            "ring_from": _hhmm(s.get("ring_from"), "08:00"), "ring_to": _hhmm(s.get("ring_to"), "19:00"),
            "chat": s.get("chat", True)}


def save_prefs(user, data):
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    st = core.settings(row)
    old = prefs(row)
    times = data.get("times", old["times"])
    if isinstance(times, str):
        times = re.split(r"[,\s]+", times)
    st["nudge"] = {"on": bool(data.get("on", old["on"])), "times": [t for t in (_hhmm(x, "") for x in times) if t] or DEFAULT_TIMES,
                   "ring": bool(data.get("ring", old["ring"])), "ring_from": _hhmm(data.get("ring_from"), old["ring_from"]),
                   "ring_to": _hhmm(data.get("ring_to"), old["ring_to"]), "chat": bool(data.get("chat", old["chat"]))}
    core.save_settings(user["id"], st)
    return prefs(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


def _ago(ts):
    m = int((time.time() - ts) // 60)
    return f"{m} min" if m < 90 else (f"{m // 60} h" if m < 48 * 60 else f"{m // 1440} days")


# ---------- what is waiting ----------
def collect(user, sites=None, slow=False):
    uid, now = user["id"], time.time()
    items = []
    add = lambda key, text, urgent=False, kind="", go="": items.append({"key": key, "text": text, "urgent": urgent, "kind": kind, "go": go})
    for a in core.q("SELECT id, summary, created FROM approvals WHERE user_id=? AND status='pending'", (uid,)):
        add(f"appr:{a['id']}", f"Waiting for your OK ({_ago(a['created'])}): {a['summary'][:120]}", now - a["created"] > 1800, "approval", "control")
    for t in core.q("SELECT id, title, detail, created, updated FROM tasks WHERE user_id=? AND status='failed' AND updated>=?", (uid, now - 86400)):
        add(f"fail:{t['id']}", f"Did not work: {t['title'][:70]}" + (f" ({t['detail'][:80]})" if t["detail"] else ""), False, "job", "control")
    for t in core.q("SELECT id, title, created FROM tasks WHERE user_id=? AND status='in_progress' AND created<?", (uid, now - 7200)):
        add(f"stuck:{t['id']}", f"Still running after {_ago(t['created'])}: {t['title'][:80]}", False, "job", "control")
    try:
        for m in core.q("SELECT id, ended, log FROM meetings WHERE user_id=? AND ended IS NOT NULL AND ended>=?", (uid, now - 86400)):
            seen = set()
            for e in json.loads(m["log"] or "[]"):
                if e.get("group") or (e["who"], e["kind"] == "call") in seen:
                    continue
                if e["kind"] == "call":
                    seen.add((e["who"], True))
                    add(f"cb:{m['id']}:{e['who']}", f"Call {e['who']} back (they called during your meeting)", now - m["ended"] < 7200, "callback", "phone")
                elif e.get("replied") or e.get("vip"):
                    seen.add((e["who"], False))
                    add(f"ans:{m['id']}:{e['who']}", f"Get back to {e['who']}: \"{e['text'][:90]}\"", False, "callback", "phone")
    except Exception:
        pass
    site = core.settings(user).get("site")
    if site and sites and sites.get(site) and not sites[site].get("ok", True):
        add(f"site:{site}:{datetime.date.today()}", f"Your website {site} is not answering", True, "site", "projects")
    if core.settings(user).get("wa_inbox"):
        r = core.q("SELECT COUNT(*) c, MIN(ts) t FROM wa_inbox WHERE user_id=? AND seen=0 AND is_group=0 AND (replied IS NULL OR replied='') "
                   "AND ts<? AND ts>?", (uid, now - 7200, now - 3 * 86400), one=True)
        if r and r["c"]:
            add(f"wa:{datetime.date.today()}", f"{r['c']} WhatsApp{'s' if r['c'] != 1 else ''} unanswered for over two hours", False, "whatsapp", "phone")
    if slow:
        try:
            import apps
            if apps.usable(user, "invoiceninja"):
                for i in apps.invoices(user, "overdue")[:5]:
                    add(f"inv:{i['number']}", f"Invoice {i['number']} to {i['client']} is overdue (R {i['balance']})", False, "invoice", "apps")
        except Exception:
            pass
    done = {r["key"] for r in core.q("SELECT key FROM nudges WHERE user_id=? AND done=1", (uid,))}
    return [i for i in items if i["key"] not in done]


def _rec(uid, key):
    return core.q("SELECT * FROM nudges WHERE user_id=? AND key=?", (uid, key), one=True)


def _mark(uid, key):
    now = time.time()
    if _rec(uid, key):
        core.x("UPDATE nudges SET last=?, count=count+1 WHERE user_id=? AND key=?", (now, uid, key))
    else:
        core.x("INSERT INTO nudges(user_id,key,first,last,count) VALUES(?,?,?,?,1)", (uid, key, now, now))


def mark_done(user, key):
    if _rec(user["id"], key):
        core.x("UPDATE nudges SET done=1 WHERE user_id=? AND key=?", (user["id"], key))
    else:
        core.x("INSERT INTO nudges(user_id,key,first,last,count,done) VALUES(?,?,?,?,0,1)", (user["id"], key, time.time(), time.time()))


def _fresh(uid, it):
    r = _rec(uid, it["key"])
    if not r:
        return True
    if it["key"].startswith(("cb:", "ans:")) and r["count"] >= CALLBACK_MAX:
        return False
    return time.time() - r["last"] >= REPEAT


# ---------- telling the user ----------
def _quiet(user, now):
    try:
        import watch
        return watch.quiet_now(watch.prefs(user), now)
    except Exception:
        return False


def _in(now, a, b):
    t = now.strftime("%H:%M")
    return a <= t < b if a < b else (t >= a or t < b)


def tell(user, items, title, ring=False, now=None):
    """Phone notification (the watch shows it), a chat line, and the alarm sound for something urgent."""
    now = now or datetime.datetime.now()
    p = prefs(user)
    text = "\n".join("- " + i["text"] for i in items[:8]) + (f"\n- and {len(items) - 8} more" if len(items) > 8 else "")
    sent = {"phone": False, "rang": False, "chat": False}
    if core.settings(user).get("phone_serial"):
        try:
            import watch
            sent["phone"] = watch.send(user, title, text.replace("\n", "  "))["ok"]
        except Exception:
            pass
        if ring and p["ring"] and _in(now, p["ring_from"], p["ring_to"]):
            try:
                import phone
                phone.ring(user, f"Trillion: {items[0]['text'][:50]}")
                sent["rang"] = True
            except Exception:
                pass
    if p["chat"]:
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant", f"{title}\n{text}", time.time()))
        sent["chat"] = True
    core.log(user["id"], f"{title}: {len(items)} item{'s' if len(items) != 1 else ''}", "waiting")
    for i in items:
        _mark(user["id"], i["key"])
    return sent


def check(user, sites=None, now=None, force=False):
    """Run from the background every minute. Returns what was sent, or None."""
    now = now or datetime.datetime.now()
    p = prefs(user)
    if not p["on"] and not force:
        return None
    try:
        import meeting
        if meeting.active(user) and not force:
            return None
    except Exception:
        pass
    uid = user["id"]
    slot = next((t for t in p["times"] if t <= now.strftime("%H:%M") and now.strftime("%H:%M") < _plus(t, 30)), None)
    row = core.q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    st = core.settings(row)
    slot_key = f"{now.date()} {slot}" if slot else None
    if force or (slot and st.get("nudge_last_slot") != slot_key):
        if slot_key:
            st["nudge_last_slot"] = slot_key
            core.save_settings(uid, st)
        if _quiet(user, now) and not force:
            return None
        items = [i for i in collect(user, sites, slow=True) if force or _fresh(uid, i)]
        if not items:
            return {"sent": False, "items": 0}
        n = len(items)
        res = tell(user, items, f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you", ring=False, now=now)
        return dict(res, sent=True, items=n)
    if _quiet(user, now):
        return None
    urgent = [i for i in collect(user, sites) if i["urgent"] and not _rec(uid, i["key"])]
    if urgent:
        res = tell(user, urgent, "Needs you now", ring=True, now=now)
        return dict(res, sent=True, items=len(urgent), urgent=True)
    return None


def _plus(hhmm, minutes):
    h, m = map(int, hhmm.split(":"))
    t = h * 60 + m + minutes
    return "23:59" if t >= 1440 else f"{t // 60:02d}:{t % 60:02d}"


def state(user, sites=None):
    items = collect(user, sites)
    return {"prefs": prefs(user), "items": items, "linked": bool(core.settings(user).get("phone_serial"))}


ASK = re.compile(r"\b(what'?s|what is|anything|is there anything|what do i have)\b[^.?!]{0,20}\b(outstanding|waiting|pending|"
                 r"need(?:s|ing)? me|for me to do|open)\b|\bwhat needs (?:me|my attention)\b", re.I)


def answer(user, sites=None):
    items = collect(user, sites, slow=True)
    if not items:
        return "Nothing is waiting on you right now."
    return f"{len(items)} thing{'s' if len(items) != 1 else ''} waiting on you:\n" + "\n".join("- " + i["text"] for i in items[:12])
