"""Gmail for Trillion, per login, so it can send outreach and read replies for the website campaign (and
general email management). Uses the login's own Gmail over SMTP+IMAP with a Gmail App Password - no Google
Cloud project, no OAuth app verification. The app password is stored encrypted (secret 'gmail_app_password')
and only ever used to reach the user's own Gmail.

An App Password needs 2-Step Verification on the Google account, then is generated at
myaccount.google.com -> Security -> App passwords. It is 16 letters; spaces are ignored.
"""
import imaplib
import re
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

import core

SMTP_HOST = "smtp.gmail.com"
IMAP_HOST = "imap.gmail.com"


def _creds(user):
    uid = user["id"]
    return (core.get_secret(uid, "gmail_address", only_enabled=False) or "",
            core.get_secret(uid, "gmail_app_password", only_enabled=False) or "")


def connected(user):
    a, p = _creds(user)
    return bool(a and p)


def status(user):
    a, _ = _creds(user)
    return {"connected": bool(a), "address": a}


def connect(user, address, app_password):
    addr = (address or "").strip()
    pw = re.sub(r"\s+", "", app_password or "")
    if not addr or not pw:
        return {"ok": False, "error": "Enter your Gmail address and a Gmail App Password."}
    if "@" not in addr:
        return {"ok": False, "error": "That doesn't look like an email address."}
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, timeout=25)
        M.login(addr, pw)
        M.logout()
    except Exception:
        return {"ok": False, "error": ("Could not sign in to Gmail. Turn on 2-Step Verification, then use a "
                                       "16-letter App Password (not your normal password).")}
    core.set_secret(user["id"], "gmail_address", addr, True)
    core.set_secret(user["id"], "gmail_app_password", pw, True)
    return {"ok": True, "address": addr}


def disconnect(user):
    for n in ("gmail_address", "gmail_app_password"):
        try:
            core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], n))
        except Exception:
            pass
    return {"ok": True}


def _plain(html):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</p>", "\n\n", t)
    return re.sub(r"\s+\n", "\n", re.sub(r"<[^>]+>", "", t)).strip()


def send(user, to, subject, html, text=None):
    a, p = _creds(user)
    if not (a and p):
        raise RuntimeError("Gmail is not connected.")
    msg = MIMEMultipart("alternative")
    msg["From"] = a
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(text or _plain(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ctx, timeout=30) as s:
        s.login(a, p)
        s.sendmail(a, [to], msg.as_string())
    return {"ok": True}


def check_replies(user, addresses, since_days=21):
    """Which of these email addresses have a message sitting in the inbox (i.e. have replied). Returns a set
    of lowercased addresses. Best-effort; returns empty on any trouble."""
    a, p = _creds(user)
    want = {str(e).lower().strip() for e in (addresses or []) if e}
    found = set()
    if not (a and p) or not want:
        return found
    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, timeout=25)
        M.login(a, p)
        M.select("INBOX")
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - since_days * 86400))
        typ, data = M.search(None, "SINCE", since)
        ids = (data[0].split() if data and data[0] else [])[-300:]
        for i in ids:
            typ, md = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            if typ != "OK" or not md or not md[0]:
                continue
            hdr = md[0][1].decode("utf-8", "ignore") if isinstance(md[0][1], (bytes, bytearray)) else str(md[0][1])
            frm = parseaddr(hdr.split(":", 1)[-1]).__getitem__(1).lower() if ":" in hdr else ""
            if frm in want:
                found.add(frm)
        M.logout()
    except Exception:
        pass
    return found
