"""Alerts on the watch. Trillion posts a notification on the login's own phone over the phone link,
and a paired watch (a Galaxy Watch, for example) shows it the way it shows any phone notification.
Nothing is installed on the watch. Each login gets alerts on its own phone only."""
import datetime
import re
import shlex
import threading
import time

import core
import phone

KINDS = {
    "approval": "Something needs your OK",
    "job": "A long job finished (one that took more than a minute)",
    "sale": "A new store sale",
    "site": "Your website looks down",
    "brief": "A short version of each routine briefing",
    "meeting": "Meeting mode: people you let through, and the summary afterwards",
}
DEFAULT_KINDS = {"approval": True, "job": True, "sale": True, "site": True, "brief": False, "meeting": True}
LONG_JOB = 60                  # seconds: quicker jobs finish while you are still looking at the screen
REPEAT = 300                   # the same alert is not sent twice within five minutes
MAX_TEXT = 400
LAST = {}                      # user id -> {"t", "ok", "msg", "title"}
_SENT = {}                     # (user id, title, text) -> time sent
_GUARD = threading.Lock()
_N = [0]


def prefs(user):
    w = core.settings(user).get("watch") or {}
    kinds = w.get("kinds") or {}
    return {"on": bool(w.get("on")),
            "kinds": {k: bool(kinds.get(k, DEFAULT_KINDS[k])) for k in KINDS},
            "quiet": bool(w.get("quiet", True)),
            "from": _hhmm(w.get("from"), "22:00"), "to": _hhmm(w.get("to"), "06:00")}


def _hhmm(v, default):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return default
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def save_prefs(user, data):
    old = prefs(user)
    kinds = dict(old["kinds"])
    for k, v in (data.get("kinds") or {}).items():
        if k in KINDS:
            kinds[k] = bool(v)
    new = {"on": bool(data.get("on", old["on"])), "kinds": kinds, "quiet": bool(data.get("quiet", old["quiet"])),
           "from": _hhmm(data.get("from"), old["from"]), "to": _hhmm(data.get("to"), old["to"])}
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    s = core.settings(row)
    s["watch"] = new
    core.save_settings(user["id"], s)
    return new


def quiet_now(p, now=None):
    if not p["quiet"]:
        return False
    t = (now or datetime.datetime.now()).strftime("%H:%M")
    a, b = p["from"], p["to"]
    if a == b:
        return False
    return (a <= t < b) if a < b else (t >= a or t < b)


def clean(text, limit=MAX_TEXT):
    text = re.sub(r"[\x00-\x1f\x7f\s]+", " ", str(text or "")).strip()     # one line: a watch has no room for layout
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def command(title, text, tag):
    """The phone command that posts one notification. Everything is quoted so a title can hold any character."""
    return ("cmd notification post -S bigtext -t " + shlex.quote(clean(title, 60) or "Trillion")
            + " " + shlex.quote(tag) + " " + shlex.quote(clean(text) or " "))


def send(user, title, text, test=False):
    """Post now and wait for the answer. Used by the test button and the chat tool."""
    with _GUARD:
        _N[0] += 1
        tag = f"trillion_{int(time.time())}_{_N[0]}"
    try:
        out = phone.sh(user, command(title, text, tag), timeout=15)
        if re.search(r"Unknown command|not found|Exception|Error", out or "", re.I):
            raise phone.PhoneError("This phone did not accept the alert. It needs Android 10 or newer.")
        LAST[user["id"]] = {"t": time.time(), "ok": True, "msg": "Sent", "title": clean(title, 60)}
        return {"ok": True}
    except Exception as e:
        msg = str(e) if type(e).__name__ == "PhoneError" else f"{type(e).__name__}: {e}"
        LAST[user["id"]] = {"t": time.time(), "ok": False, "msg": msg, "title": clean(title, 60)}
        if not test:
            core.log(user["id"], f"Watch alert not sent: {msg}"[:300], "info")
        return {"ok": False, "error": msg}


def alert(user, kind, title, text, wait=False):
    """Send an alert of this kind if the login switched it on. Never raises and never holds up the caller."""
    try:
        row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True) or user
        p = prefs(row)
        if not p["on"] or not p["kinds"].get(kind) or not core.settings(row).get("phone_serial") or quiet_now(p):
            return False
        key = (row["id"], clean(title, 60), clean(text))
        now = time.time()
        with _GUARD:
            if now - _SENT.get(key, 0) < REPEAT:
                return False
            _SENT[key] = now
            for k, t in list(_SENT.items()):
                if now - t > REPEAT:
                    _SENT.pop(k, None)
        if wait:
            return send(row, title, text)["ok"]
        threading.Thread(target=send, args=(row, title, text), daemon=True).start()
        return True
    except Exception:
        return False


def state(user):
    p = prefs(user)
    labels = {k: v for k, v in KINDS.items() if k != "sale" or core.settings(user).get("store_on")}   # only the store's owner sells
    return {**p, "labels": labels, "linked": bool(core.settings(user).get("phone_serial")),
            "quiet_now": quiet_now(p), "last": LAST.get(user["id"])}


# ---------- what raises an alert ----------
def task_finished(tid, status):
    """Called by core.set_task when a job ends."""
    try:
        t = core.q("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
        if not t or time.time() - (t["created"] or time.time()) < LONG_JOB:
            return
        u = core.q("SELECT * FROM users WHERE id=?", (t["user_id"],), one=True)
        if not u:
            return
        title = "Done" if status == "completed" else "Did not work"
        alert(u, "job", f"Trillion: {title}", f"{t['title']}" + (f" - {t['detail']}" if t["detail"] else ""))
    except Exception:
        pass


def sales(user, names):
    """names: [(product, amount)] for sales not seen before."""
    if not names:
        return
    if len(names) == 1:
        name, amount = names[0]
        alert(user, "sale", "New sale", f"{name}" + (f" for {amount}" if amount else ""))
    else:
        alert(user, "sale", f"{len(names)} new sales",
              "; ".join(f"{n}" + (f" {a}" if a else "") for n, a in names[:4]) + (" and more" if len(names) > 4 else ""))


def briefing(user, name, cards):
    bits = [clean(c.get("say"), 140) for c in cards if c.get("say") and not c.get("error")]
    if bits:
        alert(user, "brief", name, " ".join(bits))


core.ON_TASK_END.append(task_finished)
