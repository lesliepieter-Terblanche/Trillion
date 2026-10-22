"""The website-campaign CRM: one row per business ("lead"), its generated site, and where it is in the
pipeline - site created -> emailed -> followed up -> replied -> accepted / declined. Drives the tracking
dashboard and the 3-email outreach sequence (pitch now, follow-ups on day 1 and day 3), with replies
detected from Gmail so hot leads surface for you to close.
"""
import json
import threading
import time

import core

# pipeline order (left to right on the dashboard)
STATUSES = ["site_created", "emailed", "followed_up", "replied", "accepted", "declined"]
OPEN = ("site_created", "emailed", "followed_up")   # still in the sequence
DAY = 86400


def init():
    core.x("""CREATE TABLE IF NOT EXISTS leads(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, business TEXT, category TEXT, area TEXT,
        email TEXT, phone TEXT, site_id INTEGER, token TEXT, link TEXT, status TEXT,
        emailed_at REAL, followups INTEGER DEFAULT 0, next_action REAL, last_reply_at REAL,
        note TEXT, created REAL, updated REAL)""")


def _now():
    return time.time()


def on_site_created(user, site_id, token, info):
    """When Web Studio makes a site, open (or update) a lead for it."""
    init()
    uid = user["id"]
    biz = str(info.get("business") or "Business")
    area = (info.get("location") or "").strip()
    # one lead per business+area: a rebuild updates it rather than piling up duplicates
    row = core.q("SELECT id, email FROM leads WHERE user_id=? AND site_id=?", (uid, site_id), one=True) or \
        core.q("SELECT id, email FROM leads WHERE user_id=? AND lower(business)=? AND lower(COALESCE(area,''))=?",
               (uid, biz.lower(), area.lower()), one=True)
    fields = dict(business=biz, category=info.get("category") or "", area=area,
                  phone=info.get("phone") or "", site_id=site_id, token=token,
                  link=info.get("source_link") or "", updated=_now())
    if row:
        if not (row["email"] or "") and info.get("email"):   # don't wipe an email already captured
            fields["email"] = info["email"]
        sets = ", ".join(f"{k}=?" for k in fields)
        core.x(f"UPDATE leads SET {sets} WHERE id=?", (*fields.values(), row["id"]))
        return row["id"]
    return core.x("""INSERT INTO leads(user_id,business,category,area,phone,email,site_id,token,link,status,created,updated)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (uid, biz, fields["category"], area, fields["phone"], info.get("email") or "",
                   site_id, token, fields["link"], "site_created", _now(), _now()))


def find_and_build(user, query, limit=10, progress=None):
    """Search Google Maps for a type + area, keep the businesses with no website, and auto-generate a site
    for each (which opens a lead on the dashboard)."""
    import maps
    import web
    say = progress or (lambda t: None)
    say(f"Searching Google Maps for: {query}")
    res = maps.search(user, query, limit=limit, only_no_website=True)
    if res.get("error"):
        return res
    found = res.get("leads") or []
    have = {(r["business"].lower(), (r["area"] or "").lower())
            for r in core.q("SELECT business, area FROM leads WHERE user_id=?", (user["id"],))}
    new = [i for i in found if (str(i.get("business", "")).lower(), str(i.get("location", "")).lower()) not in have]
    skipped = len(found) - len(new)
    say(f"Found {len(found)} with no website (scanned {res.get('scanned', 0)}); {len(new)} new. Building…")
    built = 0
    for info in new:
        try:
            info["addons"] = ["whatsapp", "form"]      # sensible defaults for a cold lead
            r = web.build(user, dict(info), progress=None)
            built += 1
            say(f"Built {built} of {len(new)}: {r.get('title')}")
        except Exception:
            continue
    return {"ok": True, "found": len(found), "built": built, "skipped": skipped,
            "scanned": res.get("scanned", 0), "with_website": res.get("with_website", 0)}


def delete(user, lid):
    core.x("DELETE FROM leads WHERE id=? AND user_id=?", (lid, user["id"]))
    return {"ok": True}


def start_find(user, query, limit=10):
    init()
    tid = core.add_task(user["id"], f"Find & build: {query}", domain="work") if hasattr(core, "add_task") else None

    def go():
        try:
            res = find_and_build(user, query, limit, progress=(lambda t: core.set_task(tid, detail=t)) if tid else None)
            msg = res.get("error") or f"Built {res.get('built', 0)} site(s) from {res.get('found', 0)} leads"
            if tid:
                core.set_task(tid, status="failed" if res.get("error") else "completed", detail=msg[:200])
        except Exception as e:
            if tid:
                core.set_task(tid, status="failed", detail=f"Find failed: {type(e).__name__}")
    threading.Thread(target=go, daemon=True).start()
    return {"task_id": tid, "started": True}


def _row(user, lid):
    return core.q("SELECT * FROM leads WHERE id=? AND user_id=?", (lid, user["id"]), one=True)


def update(user, lid, **fields):
    fields = {k: v for k, v in fields.items() if k in
              ("business", "category", "area", "email", "phone", "status", "note")}
    if not fields:
        return {"ok": True}
    fields["updated"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    core.x(f"UPDATE leads SET {sets} WHERE id=? AND user_id=?", (*fields.values(), lid, user["id"]))
    return {"ok": True}


def set_status(user, lid, status):
    if status not in STATUSES:
        return {"error": "unknown status"}
    core.x("UPDATE leads SET status=?, updated=? WHERE id=? AND user_id=?", (status, _now(), lid, user["id"]))
    return {"ok": True, "status": status}


def listing(user):
    init()
    out = []
    for r in core.q("SELECT * FROM leads WHERE user_id=? ORDER BY id DESC", (user["id"],)):
        out.append({k: r[k] for k in r.keys()})
    return out


def stats(user):
    init()
    counts = {s: 0 for s in STATUSES}
    total = 0
    for r in core.q("SELECT status, COUNT(*) c FROM leads WHERE user_id=? GROUP BY status", (user["id"],)):
        counts[r["status"]] = counts.get(r["status"], 0) + r["c"]
        total += r["c"]
    hot = counts["replied"]                      # replied-but-not-closed = focus these
    return {"total": total, "counts": counts, "hot": hot,
            "emailed_total": counts["emailed"] + counts["followed_up"] + counts["replied"] + counts["accepted"] + counts["declined"]}


# ---------- outreach ----------
def preview_url(host, token, scheme="https"):
    return f"{scheme}://{host}/site/{token}/"


def _email_html(user, lead, link, stage):
    biz = lead["business"]
    me = core.q("SELECT name FROM users WHERE id=?", (user["id"],), one=True)
    who = (me["name"] if me and me["name"] else "").split(" ")[0] or "me"
    if stage == 0:
        intro = (f"I came across {biz} on Google and noticed you don't have a website yet, so I built a free "
                 f"example of what one could look like for you.")
        close = "Have a look — no obligation at all. If you like it, just reply and we can make it yours."
    elif stage == 1:
        intro = f"Just checking you saw the sample website I put together for {biz}."
        close = "Happy to tweak the colours, text or photos — tell me what you'd change."
    else:
        intro = f"Last note from me about the {biz} website — I don't want to be a pest."
        close = "If now isn't the time, no problem at all. Reply any time and the sample's ready to go live."
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#222">
<p>Hi there,</p>
<p>{intro}</p>
<p><a href="{link}" style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;padding:12px 22px;border-radius:40px;font-weight:600">View your sample website</a></p>
<p>{close}</p>
<p>Thanks,<br>{who}</p>
<p style="font-size:12px;color:#999">If you'd rather not hear from me, just reply "stop" and I won't follow up.</p>
</div>"""


def _subject(lead, stage):
    biz = lead["business"]
    return [f"A website I made for {biz}", f"Did you get a chance to look, {biz}?",
            f"One last note about the {biz} website"][min(stage, 2)]


def send_pitch(user, lid, host, scheme="https"):
    import mail
    lead = _row(user, lid)
    if not lead:
        return {"error": "Lead not found."}
    if not (lead["email"] or "").strip():
        return {"error": "Add the owner's email address first."}
    if not mail.connected(user):
        return {"error": "Connect your Gmail first (on the dashboard)."}
    if not lead["token"]:
        return {"error": "This lead has no generated site yet."}
    link = preview_url(host, lead["token"], scheme)
    stage = int(lead["followups"] or 0) if lead["status"] in ("emailed", "followed_up") else 0
    try:
        mail.send(user, lead["email"], _subject(lead, stage), _email_html(user, lead, link, stage))
    except Exception as e:
        return {"error": f"Could not send: {type(e).__name__}. Check your Gmail connection."}
    if stage == 0:
        core.x("UPDATE leads SET status='emailed', emailed_at=?, followups=0, next_action=?, updated=? WHERE id=?",
               (_now(), _now() + DAY, _now(), lid))
    else:
        core.x("UPDATE leads SET status='followed_up', followups=followups+1, next_action=?, updated=? WHERE id=?",
               (_now() + 2 * DAY, _now(), lid))
    return {"ok": True, "emailed": lead["email"]}


def tick(user, host, scheme="https"):
    """Run from the background loop: send any due follow-ups, and mark leads that have replied."""
    import mail
    if not mail.connected(user):
        return
    uid = user["id"]
    # 1) due follow-ups (up to 2), for leads still open and not yet replied
    due = core.q("""SELECT * FROM leads WHERE user_id=? AND status IN ('emailed','followed_up')
                    AND email IS NOT NULL AND email!='' AND next_action IS NOT NULL AND next_action<=?
                    AND followups<2 ORDER BY id LIMIT 10""", (uid, _now()))
    for lead in due:
        try:
            send_pitch(user, lead["id"], host, scheme)
        except Exception:
            pass
    # 2) replies
    open_leads = core.q("""SELECT id,email FROM leads WHERE user_id=? AND status IN ('emailed','followed_up')
                           AND email IS NOT NULL AND email!=''""", (uid,))
    emails = [l["email"] for l in open_leads]
    if emails:
        replied = mail.check_replies(user, emails)
        for l in open_leads:
            if (l["email"] or "").lower() in replied:
                core.x("UPDATE leads SET status='replied', last_reply_at=?, updated=? WHERE id=?",
                       (_now(), _now(), l["id"]))
