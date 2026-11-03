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


def record_reply(token, answer):
    """A prospect clicked Yes/No in the pitch email or deck. Public (keyed by the site token). Marks the lead
    accepted or declined and timestamps it, so it shows on the owner's dashboard. Returns the business name."""
    init()
    token = str(token or "").strip()
    yes = str(answer or "").lower() in ("yes", "y", "1", "accept", "accepted")
    row = core.q("SELECT id, business, user_id FROM leads WHERE token=?", (token,), one=True)
    biz = (row["business"] if row else "") or "your business"
    if row:
        status = "accepted" if yes else "declined"
        core.x("UPDATE leads SET status=?, last_reply_at=?, note=?, updated=? WHERE id=?",
               (status, _now(), "Replied via website: " + ("YES — interested" if yes else "not right now"),
                _now(), row["id"]))
        try:
            import watch
            watch.alert({"id": row["user_id"]}, "lead",
                        ("A prospect said YES" if yes else "A prospect replied"),
                        f"{biz} {'wants the website — follow up!' if yes else 'said not right now.'}")
        except Exception:
            pass
    return {"ok": True, "yes": yes, "business": biz, "known": bool(row)}


def record_quote(token, features, note="", contact=""):
    """A prospect used the 'request a price for more features' form on the pitch deck. Public (keyed by the
    site token). Records the request against the lead, marks it a hot reply, and alerts the owner."""
    init()
    token = str(token or "").strip()
    row = core.q("SELECT id, business, user_id, status, note FROM leads WHERE token=?", (token,), one=True)
    biz = (row["business"] if row else "") or "your business"
    try:
        import web
        labels = [web._DECK_FEATURES.get(str(f).lower(), str(f)) for f in (features or []) if str(f).strip()]
    except Exception:
        labels = [str(f) for f in (features or []) if str(f).strip()]
    note = " ".join(str(note or "").split())[:500]
    contact = " ".join(str(contact or "").split())[:160]
    if row:
        parts = ["Requested a price for more features via the deck:"]
        parts.append("wants " + (", ".join(labels) if labels else "extra features"))
        if note:
            parts.append(f'note: "{note}"')
        if contact:
            parts.append(f"contact: {contact}")
        summary = " — ".join(parts)
        # a price request is a hot signal: surface it as 'replied' unless already accepted/declined
        new_status = "replied" if (row["status"] or "") in ("site_created", "emailed", "followed_up") else row["status"]
        core.x("UPDATE leads SET status=?, last_reply_at=?, note=?, updated=? WHERE id=?",
               (new_status, _now(), summary, _now(), row["id"]))
        try:
            import watch
            watch.alert({"id": row["user_id"]}, "lead", "A prospect wants a quote",
                        f"{biz} asked for a price on more features — follow up!")
        except Exception:
            pass
    return {"ok": True, "business": biz, "features": labels, "note": note, "contact": contact, "known": bool(row)}


def quote_page(business, features, contact=""):
    """Styled thank-you page shown after the prospect submits the 'more features' form (served under /site/)."""
    biz = business or "your business"
    acc = "#7c5cff"
    want = (", ".join(features)) if features else "the extra features you mentioned"
    how = (f" I'll reply on {contact}." if contact else " I'll be in touch shortly with a price.")
    body = (f"Thanks — I've noted that you'd like a price for {want} on the {biz} website.{how}")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Thank you</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b12;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;padding:24px}}
.c{{max-width:460px;text-align:center}}.i{{width:76px;height:76px;border-radius:50%;margin:0 auto 22px;
background:linear-gradient(100deg,{acc},#d94fd0);display:flex;align-items:center;justify-content:center;font-size:36px}}
h1{{font-size:28px;margin:0 0 12px;letter-spacing:-.01em}}p{{color:#aeb3c2;line-height:1.65;font-size:16px}}</style></head>
<body><div class="c"><div class="i">&#128221;</div><h1>Got it — thank you!</h1><p>{body}</p></div></body></html>"""


def reply_page(answer, business):
    """A styled thank-you page shown after the prospect clicks Yes/No (served under /site/)."""
    yes = str(answer or "").lower() in ("yes", "y", "1", "accept", "accepted")
    biz = business or "your business"
    head = "Brilliant — thank you!" if yes else "Thanks for letting me know"
    body = (f"I've noted that you'd like to go ahead with the website for {biz}. I'll be in touch very shortly to "
            "sort out the details and get it live.") if yes else \
           (f"No problem at all. The sample for {biz} will stay ready if you change your mind — just reply to the "
            "email any time.")
    acc = "#7c5cff"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Thank you</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b12;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;padding:24px}}
.c{{max-width:440px;text-align:center}}.i{{width:76px;height:76px;border-radius:50%;margin:0 auto 22px;
background:linear-gradient(100deg,{acc},#d94fd0);display:flex;align-items:center;justify-content:center;font-size:38px}}
h1{{font-size:28px;margin:0 0 12px;letter-spacing:-.01em}}p{{color:#aeb3c2;line-height:1.65;font-size:16px}}</style></head>
<body><div class="c"><div class="i">{'&#10003;' if yes else '&#128075;'}</div><h1>{head}</h1><p>{body}</p></div></body></html>"""


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


def _email_html(user, lead, base, stage, has_video=False, pline="", feats=None):
    biz = lead["business"]
    me = core.q("SELECT name FROM users WHERE id=?", (user["id"],), one=True)
    who = (me["name"] if me and me["name"] else "").split(" ")[0] or "me"
    base = base if base.endswith("/") else base + "/"
    deck, site, video = base + "pitch.html", base, base + "pitch.mp4"
    yes, no = base + "reply?a=yes", base + "reply?a=no"
    more = deck + "#more"
    feats = feats or []
    feat_html = ""
    if feats:
        items = "".join(
            '<tr><td valign="top" style="color:#16a34a;font-weight:800;padding:4px 10px 4px 0">&#10003;</td>'
            f'<td style="padding:4px 0">{f}</td></tr>' for f in feats)
        feat_html = ('<p style="margin:24px 0 8px;font-weight:600">What you get</p>'
                     f'<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:15px">{items}</table>')
    if stage == 0:
        intro = (f"I came across {biz} on Google and built a free sample website to show what one could look like "
                 "for you — real content, works on a phone, ready to go.")
        close = "Take a look below. No obligation, nothing to pay to look."
    elif stage == 1:
        intro = f"Just checking you saw the sample website I put together for {biz}."
        close = "Happy to tweak the colours, text or photos — tell me what you'd change."
    else:
        intro = f"Last note from me about the {biz} website — I don't want to be a pest."
        close = "If now isn't the time, no problem at all. The sample's ready whenever you are."
    vid = (f'<a href="{video}" style="display:inline-block;background:#f3f4f6;color:#111;text-decoration:none;'
           'padding:12px 22px;border-radius:40px;font-weight:600;margin:4px 6px">&#9654; Watch the 30-sec video</a>'
           ) if has_video else ""
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#222;max-width:560px">
<p>Hi there,</p>
<p>{intro}</p>
<p style="margin:22px 0">
<a href="{deck}" style="display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;padding:13px 24px;border-radius:40px;font-weight:700;margin:4px 6px">See your website &amp; the details</a>
{vid}</p>
{feat_html}
<p>{close}</p>
<p style="margin:18px 0"><a href="{more}" style="display:inline-block;background:#f3f4f6;color:#111;text-decoration:none;padding:12px 22px;border-radius:40px;font-weight:600">Want more features? Request a price &#8594;</a></p>
<p style="margin:26px 0 10px;font-weight:600">Want it? One tap:</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="padding-right:10px"><a href="{yes}" style="display:inline-block;background:#16a34a;color:#fff;text-decoration:none;padding:13px 26px;border-radius:40px;font-weight:700">Yes, let's do it</a></td>
<td><a href="{no}" style="display:inline-block;background:#f3f4f6;color:#555;text-decoration:none;padding:13px 26px;border-radius:40px;font-weight:600">Not right now</a></td>
</tr></table>
{('<p style="margin:22px 0 4px;font-weight:600">Price</p><p style="margin:0">' + pline + '</p>') if pline else ''}
<p style="font-size:13px;color:#888;margin-top:20px">The price above is the once-off cost for the website. A domain name and hosting are arranged on your side (about R100&ndash;R250 a year for a domain, plus a low-cost hosting plan); I'll guide you through connecting it. Full details are on the page above.</p>
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
    has_video = False
    pline = ""
    feats = []
    try:
        import os, json as _json, web
        site = core.q("SELECT folder, detail FROM gensites WHERE token=?", (lead["token"],), one=True)
        if site:
            has_video = bool(os.path.exists(os.path.join(site["folder"], "pitch.mp4")))
            detail = _json.loads(site["detail"] or "{}") or {}
            addons = detail.get("addons") or []
            pline = web.price_line(addons)
            feats = web.feature_list(lead["business"], addons)
            web.refresh_pitch(site["folder"], detail)      # ensure the deck has the latest (incl. the more-features form)
    except Exception:
        pass
    try:
        mail.send(user, lead["email"], _subject(lead, stage), _email_html(user, lead, link, stage, has_video, pline, feats))
    except Exception as e:
        return {"error": f"Could not send: {type(e).__name__}. Check your Gmail connection."}
    if stage == 0:
        core.x("UPDATE leads SET status='emailed', emailed_at=?, followups=0, next_action=?, updated=? WHERE id=?",
               (_now(), _now() + DAY, _now(), lid))
    else:
        core.x("UPDATE leads SET status='followed_up', followups=followups+1, next_action=?, updated=? WHERE id=?",
               (_now() + 2 * DAY, _now(), lid))
    return {"ok": True, "emailed": lead["email"]}


def send_for_site(user, sid, email, host, scheme="https"):
    """Pitch the lead behind a generated site straight from Web Studio: set its email, then send the pitch."""
    init()
    row = core.q("SELECT id FROM leads WHERE user_id=? AND site_id=?", (user["id"], sid), one=True)
    if not row:
        return {"error": "No lead for this site yet — generate the site first."}
    email = (email or "").strip()
    if email:
        core.x("UPDATE leads SET email=?, updated=? WHERE id=?", (email, _now(), row["id"]))
    return send_pitch(user, row["id"], host, scheme)


def send_whatsapp_for_site(user, sid, phone_no, host, scheme="https"):
    """WhatsApp the pitch deck for a generated site straight from Web Studio — for when there's no email.
    Sets the lead's phone if one is supplied, then sends over WhatsApp."""
    init()
    row = core.q("SELECT id FROM leads WHERE user_id=? AND site_id=?", (user["id"], sid), one=True)
    if not row:
        return {"error": "No lead for this site yet — generate the site first."}
    phone_no = (phone_no or "").strip()
    if phone_no:
        core.x("UPDATE leads SET phone=?, updated=? WHERE id=?", (phone_no, _now(), row["id"]))
    return send_whatsapp(user, row["id"], host, scheme)


# WhatsApp sending drives the linked phone over ADB (open WhatsApp, attach, press Send) — that takes several
# seconds and can fail for plain reasons (phone asleep/locked, not linked, WhatsApp missing). Run it in the
# background so the page never just hangs, and report progress/outcome the UI can poll.
_WA_JOBS = {}            # (uid, sid) -> {status: sending|done|error, detail/error/to/note}
_WA_LOCK = threading.Lock()


def start_whatsapp_for_site(user, sid, phone_no, host, scheme="https"):
    init()
    uid, sid = user["id"], int(sid)
    key = (uid, sid)
    with _WA_LOCK:
        if (_WA_JOBS.get(key) or {}).get("status") == "sending":
            return dict(_WA_JOBS[key])
    row = core.q("SELECT id, phone FROM leads WHERE user_id=? AND site_id=?", (uid, sid), one=True)
    if not row:
        return {"status": "error", "error": "No lead for this site yet — generate the site first."}
    phone_no = (phone_no or "").strip()
    if not phone_no and not (row["phone"] or "").strip():
        return {"status": "error", "error": "Enter the owner's WhatsApp number first."}
    job = {"status": "sending", "detail": "Opening WhatsApp on your phone and attaching the pitch… (watch your phone)"}
    with _WA_LOCK:
        _WA_JOBS[key] = dict(job)

    def go():
        try:
            r = send_whatsapp_for_site(user, sid, phone_no, host, scheme)
            if r.get("error"):
                out = {"status": "error", "error": r["error"]}
            else:
                out = {"status": "done", "to": r.get("to") or phone_no, "note": r.get("note", "")}
        except Exception as e:
            out = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:160]}"}
        with _WA_LOCK:
            _WA_JOBS[key] = out
    threading.Thread(target=go, daemon=True).start()
    return job


def whatsapp_status(user, sid):
    with _WA_LOCK:
        return dict(_WA_JOBS.get((user["id"], int(sid))) or {"status": "idle"})


def send_whatsapp(user, lid, host, scheme="https"):
    """Send the pitch deck + live preview link to a lead over WhatsApp (their phone), using Trillion's phone link.
    Best for no-website leads, who have a mobile but rarely an email."""
    import phone
    lead = _row(user, lid)
    if not lead:
        return {"error": "Lead not found."}
    if not (lead["phone"] or "").strip():
        return {"error": "This lead has no phone number."}
    if not lead["token"]:
        return {"error": "This lead has no generated site yet."}
    base = preview_url(host, lead["token"], scheme)
    deck = (base if base.endswith("/") else base + "/") + "pitch.html"
    biz = lead["business"]
    me = core.q("SELECT name FROM users WHERE id=?", (user["id"],), one=True)
    who = (me["name"] if me and me["name"] else "").split(" ")[0] or ""
    pline, bullets = "", ""
    try:
        import json as _json, web
        site = core.q("SELECT folder, detail FROM gensites WHERE token=?", (lead["token"],), one=True)
        if site:
            detail = _json.loads(site["detail"] or "{}") or {}
            addons = detail.get("addons") or []
            pline = web.price_line(addons)
            bullets = "\n".join("✓ " + f for f in web.feature_list(biz, addons))
            web.refresh_pitch(site["folder"], detail)      # ensure the deck has the latest (incl. the more-features form)
    except Exception:
        pass
    msg = (f"Hi, I came across {biz} on Google and built a free sample website to show what one could look like "
           f"for you. Have a look here (there's a Yes/No button at the bottom to let me know):\n{deck}\n\n"
           + (f"What you get:\n{bullets}\n\n" if bullets else "")
           + (f"Price: {pline}\n(Domain & hosting are arranged on your side.)\n\n" if pline else "")
           + f"Want more features? Ask for a price here:\n{deck}#more\n\n"
           + "No obligation at all." + (f"\n\n{who}" if who else ""))
    try:
        from pathlib import Path as _P
        site = core.q("SELECT folder FROM gensites WHERE id=? AND user_id=?", (lead["site_id"], user["id"]), one=True) \
            if lead["site_id"] else None
        vid = _P(site["folder"]) / "pitch.mp4" if site and site["folder"] else None
        phone.send_whatsapp(user, lead["phone"], msg, [str(vid)] if vid and vid.exists() else None)
    except Exception as e:
        return {"error": f"Could not send WhatsApp: {str(e)[:160]}"}
    core.x("UPDATE leads SET status=CASE WHEN status='site_created' THEN 'emailed' ELSE status END, "
           "emailed_at=COALESCE(emailed_at,?), note='WhatsApp sent', updated=? WHERE id=?", (_now(), _now(), lid))
    return {"ok": True, "to": lead["phone"], "via": "whatsapp"}


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
