"""How Trillion is growing: a plain, visual picture of what she's learned, the skills and automations she's
gained, the documents she can now search, and how busy she's been — all from local data, owner only.
"""
import time

import core


def _one(q, a=()):
    r = core.q(q, a, one=True)
    return (r[0] if r else 0) or 0


def summary(user):
    if not user.get("is_admin"):
        return {"error": "This view is for the owner login only."}
    uid = user["id"]
    now = time.time()

    # knowledge learned (nightly learning files 'Learned …' notes)
    learned_total = _one("SELECT COUNT(*) FROM notes WHERE user_id=? AND title LIKE 'Learned %'", (uid,))
    learned_week = _one("SELECT COUNT(*) FROM notes WHERE user_id=? AND title LIKE 'Learned %' AND ts>=?",
                        (uid, now - 7 * 86400))
    learned = [{"title": r["title"], "body": (r["body"] or "")[:160]} for r in core.q(
        "SELECT title, body FROM notes WHERE user_id=? AND title LIKE 'Learned %' ORDER BY id DESC LIMIT 8", (uid,))]

    # memory + documents
    notes_total = _one("SELECT COUNT(*) FROM notes WHERE user_id=?", (uid,))
    docs_sources = _one("SELECT COUNT(DISTINCT source) FROM corpus WHERE user_id=?", (uid,)) \
        if core.q("SELECT name FROM sqlite_master WHERE type='table' AND name='corpus'", one=True) else 0
    docs_passages = _one("SELECT COUNT(*) FROM corpus WHERE user_id=?", (uid,)) if docs_sources else 0

    # automations (evolve suggestions)
    sug = {"pending": 0, "applied": 0, "dismissed": 0}
    for r in core.q("SELECT status, COUNT(*) c FROM suggestions WHERE user_id=? GROUP BY status", (uid,)):
        sug[r["status"]] = r["c"]
    applied_recent = [r["title"] for r in core.q(
        "SELECT title FROM suggestions WHERE user_id=? AND status='applied' ORDER BY id DESC LIMIT 6", (uid,))]

    # skills gained
    skills = {"active": 0, "draft": 0}
    for r in core.q("SELECT status, COUNT(*) c FROM skills GROUP BY status"):
        skills[r["status"]] = r.get("c", 0) if isinstance(r, dict) else r["c"]
    draft_skills = [{"name": r["name"], "description": (r["description"] or "")[:140]} for r in core.q(
        "SELECT name, description FROM skills WHERE status='draft' ORDER BY COALESCE(created,0) DESC LIMIT 6")]

    # connectors
    connectors = _one("SELECT COUNT(*) FROM connectors")

    # activity — last 30 days, done vs needed-you, per day for a simple chart
    days = []
    done_30 = waiting_30 = 0
    for d in range(29, -1, -1):
        start = core.day_start() - d * 86400
        end = start + 86400
        done = _one("SELECT COUNT(*) FROM activity WHERE user_id=? AND kind='done' AND ts>=? AND ts<?", (uid, start, end))
        wait = _one("SELECT COUNT(*) FROM activity WHERE user_id=? AND kind='waiting' AND ts>=? AND ts<?", (uid, start, end))
        done_30 += done
        waiting_30 += wait
        days.append({"day": time.strftime("%d/%m", time.localtime(start)), "done": done, "waiting": wait})

    # self-heal history
    heals = [{"text": r["text"], "when": r["ts"]} for r in core.q(
        "SELECT text, ts FROM activity WHERE user_id=? AND text LIKE 'Self-heal:%' ORDER BY id DESC LIMIT 6", (uid,))]

    return {
        "tiles": {
            "learned": learned_total, "learned_week": learned_week,
            "skills": skills["active"], "skills_draft": skills["draft"],
            "automations": sug["applied"], "automations_pending": sug["pending"],
            "documents": docs_sources, "doc_passages": docs_passages,
            "notes": notes_total, "connectors": connectors,
            "done_30": done_30, "waiting_30": waiting_30,
        },
        "activity": days,
        "learned_recent": learned,
        "automations_recent": applied_recent,
        "draft_skills": draft_skills,
        "selfheal_recent": heals,
        "when": now,
    }
