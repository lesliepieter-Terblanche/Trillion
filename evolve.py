"""Evolve: Trillion gets better on her own, every day.

Two halves, both nightly and both local (nothing leaves the PC):
  1. Learning - handled by daily_review in app.py: it reads the whole day's conversation (every chat, not
     just jobs) and files lasting knowledge - facts, preferences, how you like things done - into the
     second brain. This module adds the second half.
  2. Automation - this module looks at what you've been doing by hand (the week's activity plus today's
     chat) and proposes automations. The safe, reversible, notify-only ones (a morning briefing, check-ins)
     can be switched on automatically when you allow it; anything that sends, pays or deletes is NEVER
     auto-applied - it waits for your OK. Everything it does is logged and reversible.

A suggestion is a small record: a title, one line for you to read, and a machine 'action' from a short
safe whitelist. Suggestions show on the Workflow page and are counted in the morning briefing.
"""
import json
import time

import core

core.x("""CREATE TABLE IF NOT EXISTS suggestions(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT,
          detail TEXT DEFAULT '', action TEXT DEFAULT '', status TEXT DEFAULT 'pending', ts REAL)""")

SAFE = {"morning_on", "nudge_on"}        # reversible + notify-only: fine to switch on by itself
KNOWN = SAFE | {"tip", "skill"}          # 'tip' = advice only; 'skill' = a drafted playbook awaiting approval


def auto_on(user):
    return bool(core.settings(user).get("auto_evolve", False))


def _recent_titles(uid, days=14):
    return {r["title"] for r in core.q(
        "SELECT title FROM suggestions WHERE user_id=? AND ts>=?", (uid, time.time() - days * 86400))}


def suggest(user, llm_chat):
    """Propose automations from the week's activity and today's chat. Returns how many were added.
    Safe ones are switched on automatically only when auto_evolve is on."""
    uid = user["id"]
    now, ds = time.time(), core.day_start()
    acts = core.q("SELECT text, kind FROM activity WHERE user_id=? AND ts>=? ORDER BY id", (uid, ds - 6 * 86400))
    msgs = core.q("SELECT role, content FROM messages WHERE user_id=? AND ts>=? ORDER BY id", (uid, ds))
    if not acts and len(msgs) < 2:
        return 0
    hist = "\n".join(f"{a['kind']}: {a['text'][:120]}" for a in acts)[-6000:]
    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in msgs)[-4000:]
    sys = ("You help a personal assistant become more autonomous for its user. From the user's recent "
           "activity and today's chat, find things the user keeps doing by hand that the assistant could do "
           "on its own, and propose automations. Reply with JSON only: a list (max 4) of objects "
           "{title, detail, action}. title: a few words. detail: one plain sentence the user reads. "
           "action is exactly one of: "
           '{"kind":"morning_on","time":"HH:MM"} to switch on a daily morning briefing; '
           '{"kind":"nudge_on"} to switch on check-ins; '
           '{"kind":"skill","name":"short-name","description":"one line: when to use this","instructions":"the '
           'numbered steps"} to save a MULTI-STEP task the user has done by hand more than once as a reusable '
           'playbook (it is saved as a draft for the owner to approve, never auto-run); '
           'or {"kind":"tip","say":"<the exact phrase the user can say to make it happen>"} for anything else. '
           "Only propose something that genuinely recurs. Never propose anything that sends messages, spends "
           "money, or deletes things as automatic. Reply [] if nothing is worth automating.")
    try:
        m = llm_chat(user, [{"role": "system", "content": sys},
                            {"role": "user", "content": f"Recent activity:\n{hist}\n\nToday's chat:\n{convo}"}])
    except Exception:
        return 0
    text = (m.get("content") or "").strip()
    try:
        items = json.loads(text[text.index("["): text.rindex("]") + 1])
    except Exception:
        items = []
    seen = _recent_titles(uid)
    auto = auto_on(user)
    made = 0
    for it in items[:4]:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        title = str(it["title"])[:80]
        if title in seen:
            continue
        action = it.get("action") if isinstance(it.get("action"), dict) else {}
        if action.get("kind") not in KNOWN:
            action = {"kind": "tip", "say": ""}        # anything unknown degrades to safe advice only
        if action.get("kind") == "skill" and not str(action.get("instructions") or "").strip():
            action = {"kind": "tip", "say": ""}        # a skill with no steps is just advice
        sid = core.x("INSERT INTO suggestions(user_id,title,detail,action,status,ts) VALUES(?,?,?,?,?,?)",
                     (uid, title, str(it.get("detail", ""))[:200], json.dumps(action), "pending", now))
        made += 1
        seen.add(title)
        if auto and action["kind"] in SAFE:
            try:
                apply(user, sid)
            except Exception:
                pass
    if made:
        core.log(uid, f"Evolve: {made} automation suggestion{'s' if made != 1 else ''}"
                 + (" (safe ones switched on)" if auto else ""), "done")
    return made


def apply(user, sid):
    """Carry out a suggestion's action. Only the safe whitelist actually changes anything; a 'tip' is just
    acknowledged (the user acts on it by saying the phrase)."""
    uid = user["id"]
    row = core.q("SELECT * FROM suggestions WHERE id=? AND user_id=?", (int(sid), uid), one=True)
    if not row or row["status"] != "pending":
        return {"ok": False, "error": "Already handled."}
    try:
        action = json.loads(row["action"] or "{}")
    except Exception:
        action = {}
    kind = action.get("kind")
    if kind == "morning_on":
        import morning
        morning.save_prefs(user, {"on": True, **({"time": action["time"]} if action.get("time") else {})})
    elif kind == "nudge_on":
        import nudge
        nudge.save_prefs(user, {"on": True})
    elif kind == "skill":
        import extend
        name = (action.get("name") or row["title"])[:70]
        if core.q("SELECT 1 FROM skills WHERE slug=?", (extend.slugify(name),), one=True):
            name = f"{name} (draft {time.strftime('%d %b %H%M')})"
        extend.save_skill(name, (action.get("description") or row["detail"])[:400],
                          (action.get("instructions") or "")[:20000], "self-authored by Trillion", status="draft")
        core.x("UPDATE suggestions SET status='applied' WHERE id=?", (int(sid),))
        try:
            core.log(uid, f"New playbook drafted: '{name[:60]}' — approve it on the Skills page", "waiting")
        except Exception:
            pass
        return {"ok": True, "applied": "skill", "note": "Saved as a draft skill; enable it on the Skills page."}
    core.x("UPDATE suggestions SET status='applied' WHERE id=?", (int(sid),))
    try:
        core.log(uid, f"Automation set up: {row['title']}", "done")
    except Exception:
        pass
    return {"ok": True, "applied": kind or "tip"}


def dismiss(user, sid):
    core.x("UPDATE suggestions SET status='dismissed' WHERE id=? AND user_id=?", (int(sid), user["id"]))
    return {"ok": True}


def open_list(user):
    out = []
    for r in core.q("SELECT id, title, detail, action, ts FROM suggestions WHERE user_id=? AND status='pending' "
                    "ORDER BY id DESC LIMIT 12", (user["id"],)):
        try:
            act = json.loads(r["action"] or "{}")
        except Exception:
            act = {}
        out.append({"id": r["id"], "title": r["title"], "detail": r["detail"],
                    "kind": act.get("kind", "tip"), "say": act.get("say", ""), "ts": r["ts"],
                    "safe": act.get("kind") in SAFE})
    return out


def learned_recent(user, days=7):
    return [{"title": r["title"], "body": r["body"]} for r in core.q(
        "SELECT title, body FROM notes WHERE user_id=? AND ts>=? AND title LIKE 'Learned %' ORDER BY id DESC LIMIT 12",
        (user["id"], time.time() - days * 86400))]


def state(user):
    return {"suggestions": open_list(user), "learned": learned_recent(user),
            "auto": auto_on(user), "open_count": len(open_list(user))}


def set_auto(user, on):
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    st = core.settings(row)
    st["auto_evolve"] = bool(on)
    core.save_settings(user["id"], st)
    return auto_on(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))
