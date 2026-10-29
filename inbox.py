"""Inbox agent: Trillion reads the user's real Gmail inbox, sorts what actually needs them from the noise, and
drafts replies in their voice. Nothing is sent — drafting only; sending stays a deliberate, separate step.

Uses the same per-login Gmail App Password as the campaign mail (mail.py). Local brain does the triage, so
nothing leaves the PC except the IMAP read of the user's own mailbox.
"""
import email
import imaplib
import json
import re
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

import core
import mail


def _llm(user, system, prompt):
    try:
        fn = getattr(core, "LLM", None)
        return (fn(user, [{"role": "system", "content": system}, {"role": "user", "content": prompt}])
                .get("content") or "") if fn else ""
    except Exception:
        return ""


def _dec(s):
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _open(user):
    a, p = mail._creds(user)
    if not (a and p):
        return None, None
    M = imaplib.IMAP4_SSL(mail.IMAP_HOST, timeout=25)
    M.login(a, p)
    M.select("INBOX")
    return M, a


def recent(user, days=3, limit=25):
    """Headers (+ a short snippet) of recent inbox messages."""
    out = []
    try:
        M, _ = _open(user)
        if not M:
            return out
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - days * 86400))
        typ, data = M.search(None, "SINCE", since)
        ids = (data[0].split() if data and data[0] else [])[-limit:]
        for i in reversed(ids):
            typ, md = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[1]<0.400>)")
            if typ != "OK" or not md:
                continue
            hdr, snip = "", ""
            for part in md:
                if isinstance(part, tuple) and part[1]:
                    txt = part[1].decode("utf-8", "ignore") if isinstance(part[1], (bytes, bytearray)) else str(part[1])
                    if re.search(r"(?i)^(from|subject|date):", txt):
                        hdr += txt
                    else:
                        snip += txt
            frm = subj = dt = ""
            for line in hdr.splitlines():
                low = line.lower()
                if low.startswith("from:"):
                    frm = _dec(line.split(":", 1)[1].strip())
                elif low.startswith("subject:"):
                    subj = _dec(line.split(":", 1)[1].strip())
                elif low.startswith("date:"):
                    dt = line.split(":", 1)[1].strip()
            out.append({"uid": i.decode() if isinstance(i, bytes) else str(i),
                        "from": frm, "email": parseaddr(frm)[1], "subject": subj, "date": dt,
                        "snippet": re.sub(r"\s+", " ", snip)[:300]})
        M.logout()
    except Exception:
        pass
    return out


def triage(user):
    """Sort recent inbox into what needs action vs FYI, with a one-line summary and priority."""
    if not mail.connected(user):
        return {"error": "Connect your Gmail first (on the Campaign dashboard)."}
    items = recent(user)
    if not items:
        return {"ok": True, "items": [], "note": "Inbox is quiet — nothing in the last few days."}
    listing = "\n".join(f'{n}. From: {it["from"]} | Subject: {it["subject"]} | {it["snippet"][:120]}'
                        for n, it in enumerate(items))
    raw = _llm(user,
               "You triage an inbox. Reply with JSON only: a list of objects {i, priority, needs_action, summary}. "
               "i is the number. priority is 'high','medium' or 'low'. needs_action is true if the user must do or "
               "reply to something. summary is one short line of what it is and what's wanted.",
               f"Emails:\n{listing}\n\nReturn the JSON list.")
    verdicts = {}
    try:
        for v in json.loads(raw[raw.index("["): raw.rindex("]") + 1]):
            if isinstance(v, dict) and isinstance(v.get("i"), int):
                verdicts[v["i"]] = v
    except Exception:
        pass
    rank = {"high": 0, "medium": 1, "low": 2}
    for n, it in enumerate(items):
        v = verdicts.get(n, {})
        it["priority"] = v.get("priority", "low")
        it["needs_action"] = bool(v.get("needs_action"))
        it["summary"] = v.get("summary") or it["subject"]
    items.sort(key=lambda x: (not x["needs_action"], rank.get(x["priority"], 2)))
    need = [i for i in items if i["needs_action"]]
    return {"ok": True, "items": items, "need_action": len(need),
            "note": f"{len(need)} of {len(items)} need you." if need else "Nothing urgent — all FYI."}


def _body(user, uid):
    try:
        M, _ = _open(user)
        if not M:
            return "", "", ""
        typ, md = M.fetch(uid.encode() if isinstance(uid, str) else uid, "(RFC822)")
        M.logout()
        if typ != "OK" or not md or not md[0]:
            return "", "", ""
        msg = email.message_from_bytes(md[0][1])
        frm = _dec(msg.get("From", ""))
        subj = _dec(msg.get("Subject", ""))
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", "ignore")
                    break
            if not body:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        body = mail._plain(part.get_payload(decode=True).decode("utf-8", "ignore"))
                        break
        else:
            raw = msg.get_payload(decode=True)
            body = (raw.decode("utf-8", "ignore") if raw else "")
            if msg.get_content_type() == "text/html":
                body = mail._plain(body)
        return frm, subj, re.sub(r"\n{3,}", "\n\n", body).strip()[:4000]
    except Exception:
        return "", "", ""


def draft_reply(user, uid, instruction=""):
    """Write a reply in the user's voice. Returns the draft only — nothing is sent."""
    if not mail.connected(user):
        return {"error": "Connect your Gmail first."}
    frm, subj, body = _body(user, uid)
    if not body and not subj:
        return {"error": "I couldn't open that email to reply to it."}
    name = (user.get("name") or "").split(" ")[0]
    style = ""
    try:
        import extend
        if any("writing-style" in (s.get("name") or "") for s in extend.enabled_skills(user["id"])):
            style = " Match the user's own saved writing style."
    except Exception:
        pass
    draft = _llm(user,
                 f"You draft an email reply as {user.get('name') or 'the user'}. Natural, professional, concise, "
                 f"matching the sender's tone (often Afrikaans or English).{style} Reply with the email body only — "
                 "no subject line, no commentary.",
                 f"Reply to this email.{(' The user wants: ' + instruction) if instruction else ''}\n\n"
                 f"From: {frm}\nSubject: {subj}\n\n{body}")
    draft = (draft or "").strip()
    if name and name.lower() not in draft[-60:].lower():
        draft = draft + f"\n\n{name}"
    return {"ok": True, "to": parseaddr(frm)[1], "subject": "Re: " + re.sub(r"(?i)^re:\s*", "", subj),
            "draft": draft, "note": "This is a draft only — read it, then send it yourself if it's right."}
