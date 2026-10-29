"""Tier-2 interactive add-ons for generated sites: a shop/cart, member login, appointment booking and a
community forum. Each keeps its own state per site (keyed by the site's public token) in the local database,
so a generated site can take real orders, bookings, sign-ups and posts without any third-party service.

Visitor-facing actions are reached under /site/<token>/__api/<action> (so they work over the public Funnel,
which only exposes /site/). The owner sees everything back in Trillion via activity()/set_status().
Nothing here takes payment; orders and bookings are captured as leads and the owner is pinged.
"""
import hashlib
import json
import re
import secrets
import time

import core


def init():
    core.x("""CREATE TABLE IF NOT EXISTS site_members(
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, email TEXT, name TEXT, pwhash TEXT, salt TEXT,
        session TEXT, created REAL)""")
    core.x("""CREATE TABLE IF NOT EXISTS site_orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, name TEXT, contact TEXT, items TEXT, total TEXT,
        note TEXT, status TEXT DEFAULT 'new', created REAL)""")
    core.x("""CREATE TABLE IF NOT EXISTS site_bookings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, name TEXT, contact TEXT, slot TEXT, note TEXT,
        status TEXT DEFAULT 'new', created REAL)""")
    core.x("""CREATE TABLE IF NOT EXISTS site_posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, parent INTEGER DEFAULT 0, name TEXT, body TEXT,
        created REAL)""")


def _site(token):
    """The gensites row behind a public token, as a plain dict (user_id, business, phone, email)."""
    r = core.q("SELECT user_id,business,detail FROM gensites WHERE token=?", (str(token),), one=True)
    if not r:
        return None
    try:
        d = json.loads(r["detail"] or "{}")
    except Exception:
        d = {}
    info = d.get("info") or {}
    return {"user_id": r["user_id"], "business": r["business"] or info.get("business") or "the business",
            "phone": info.get("phone") or "", "email": info.get("email") or ""}


def _clip(s, n=600):
    return str(s or "").strip()[:n]


def _wa(phone, text):
    import web
    num = web._wa_number(phone)
    if not num:
        return ""
    from urllib.parse import quote_plus
    return f"https://wa.me/{num}?text={quote_plus(text[:900])}"


# ---- members -------------------------------------------------------------------------------------
def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", str(pw).encode(), salt.encode(), 120_000).hex()


def signup(token, email, name, password):
    init()
    email = _clip(email, 120).lower()
    name = _clip(name, 80) or email.split("@")[0]
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "Enter a valid email."}
    if len(str(password or "")) < 6:
        return {"error": "Password must be at least 6 characters."}
    if core.q("SELECT id FROM site_members WHERE token=? AND email=?", (token, email), one=True):
        return {"error": "That email is already registered. Try logging in."}
    salt = secrets.token_hex(8)
    sess = secrets.token_urlsafe(18)
    core.x("INSERT INTO site_members(token,email,name,pwhash,salt,session,created) VALUES(?,?,?,?,?,?,?)",
           (token, email, name, _hash(password, salt), salt, sess, time.time()))
    return {"ok": True, "session": sess, "name": name, "email": email}


def login(token, email, password):
    init()
    email = _clip(email, 120).lower()
    r = core.q("SELECT * FROM site_members WHERE token=? AND email=?", (token, email), one=True)
    if not r or _hash(password, r["salt"]) != r["pwhash"]:
        return {"error": "Wrong email or password."}
    sess = secrets.token_urlsafe(18)
    core.x("UPDATE site_members SET session=? WHERE id=?", (sess, r["id"]))
    return {"ok": True, "session": sess, "name": r["name"], "email": r["email"]}


def me(token, session):
    init()
    r = core.q("SELECT name,email FROM site_members WHERE token=? AND session=?",
               (token, _clip(session, 80)), one=True)
    return {"ok": True, "name": r["name"], "email": r["email"]} if r else {"ok": False}


# ---- orders (shop) -------------------------------------------------------------------------------
def place_order(token, name, contact, items, note=""):
    init()
    site = _site(token)
    if not site:
        return {"error": "Unknown site."}
    name, contact = _clip(name, 80), _clip(contact, 120)
    if not name or not contact:
        return {"error": "Please give your name and a contact number or email."}
    items = items if isinstance(items, list) else []
    clean, total = [], 0.0
    for it in items[:40]:
        if not isinstance(it, dict):
            continue
        qty = max(1, min(99, int(it.get("qty") or 1)))
        pr = it.get("price") or ""
        clean.append({"name": _clip(it.get("name"), 80), "price": _clip(pr, 20), "qty": qty})
        m = re.search(r"[\d][\d\s.,]*", str(pr))
        if m:
            try:
                total += float(m.group().replace(" ", "").replace(",", "")) * qty
            except Exception:
                pass
    if not clean:
        return {"error": "Your cart is empty."}
    tot = f"R{total:,.2f}" if total else ""
    oid = core.x("INSERT INTO site_orders(token,name,contact,items,total,note,status,created) "
                 "VALUES(?,?,?,?,?,?,?,?)",
                 (token, name, contact, json.dumps(clean), tot, _clip(note, 500), "new", time.time()))
    _notify(site, f"New order #{oid} on your website from {name} ({contact}): "
            + ", ".join(f"{c['qty']}x {c['name']}" for c in clean) + (f" — {tot}" if tot else ""))
    lines = "\n".join(f"- {c['qty']}x {c['name']}" + (f" ({c['price']})" if c['price'] else "") for c in clean)
    wa = _wa(site["phone"], f"Hi {site['business']}, I placed an order on your website:\n{lines}\n"
             f"{('Total ' + tot) if tot else ''}\nName: {name}\nContact: {contact}")
    return {"ok": True, "id": oid, "total": tot, "wa": wa,
            "message": "Order received. The business will be in touch to confirm."}


# ---- bookings (appointments) ---------------------------------------------------------------------
def book(token, name, contact, slot, note=""):
    init()
    site = _site(token)
    if not site:
        return {"error": "Unknown site."}
    name, contact, slot = _clip(name, 80), _clip(contact, 120), _clip(slot, 80)
    if not name or not contact or not slot:
        return {"error": "Please give your name, contact and a preferred time."}
    bid = core.x("INSERT INTO site_bookings(token,name,contact,slot,note,status,created) VALUES(?,?,?,?,?,?,?)",
                 (token, name, contact, slot, _clip(note, 500), "new", time.time()))
    _notify(site, f"New booking #{bid} on your website: {name} ({contact}) for {slot}"
            + (f" — {note}" if note else ""))
    wa = _wa(site["phone"], f"Hi {site['business']}, I'd like to book for {slot}.\nName: {name}\n"
             f"Contact: {contact}" + (f"\n{note}" if note else ""))
    return {"ok": True, "id": bid, "wa": wa, "message": "Booking request sent. You'll get a confirmation shortly."}


# ---- forum (community) ---------------------------------------------------------------------------
def forum_list(token):
    init()
    rows = core.q("SELECT id,parent,name,body,created FROM site_posts WHERE token=? ORDER BY id DESC LIMIT 200",
                  (token,))
    posts = [dict(r) for r in rows]
    threads = [p for p in posts if not p["parent"]]
    for t in threads:
        t["replies"] = sorted([p for p in posts if p["parent"] == t["id"]], key=lambda x: x["id"])
    return {"ok": True, "threads": threads}


def forum_post(token, name, body, parent=0):
    init()
    if not _site(token):
        return {"error": "Unknown site."}
    name, body = _clip(name, 60) or "Guest", _clip(body, 1500)
    if not body:
        return {"error": "Write something first."}
    try:
        parent = int(parent or 0)
    except Exception:
        parent = 0
    pid = core.x("INSERT INTO site_posts(token,parent,name,body,created) VALUES(?,?,?,?,?)",
                 (token, parent, name, body, time.time()))
    return {"ok": True, "id": pid}


def _notify(site, text):
    try:
        import watch
        watch.alert({"id": site["user_id"]}, "site", "Website activity", text)
    except Exception:
        pass
    try:
        core.log(site["user_id"], text, "site")
    except Exception:
        pass


# ---- public dispatch (called from the /site/<token>/__api/<action> route) ------------------------
def handle(token, action, data, method="POST"):
    data = data or {}
    if not _site(token):
        return {"error": "Unknown site."}
    try:
        if action == "signup":
            return signup(token, data.get("email"), data.get("name"), data.get("password"))
        if action == "login":
            return login(token, data.get("email"), data.get("password"))
        if action == "me":
            return me(token, data.get("session"))
        if action == "order":
            return place_order(token, data.get("name"), data.get("contact"), data.get("items"), data.get("note"))
        if action == "book":
            return book(token, data.get("name"), data.get("contact"), data.get("slot"), data.get("note"))
        if action == "forum":
            if method == "GET":
                return forum_list(token)
            return forum_post(token, data.get("name"), data.get("body"), data.get("parent"))
    except Exception as e:
        return {"error": "Something went wrong.", "detail": str(e)[:120]}
    return {"error": "Unknown action."}


# ---- owner view (back in Trillion, auth'd) -------------------------------------------------------
def _owned_token(user, sid):
    r = core.q("SELECT token FROM gensites WHERE id=? AND user_id=?", (sid, user["id"]), one=True)
    return r["token"] if r else None


def activity(user, sid):
    init()
    token = _owned_token(user, sid)
    if not token:
        return {"error": "Not found."}
    orders = [dict(r) for r in core.q("SELECT * FROM site_orders WHERE token=? ORDER BY id DESC LIMIT 50", (token,))]
    for o in orders:
        try:
            o["items"] = json.loads(o["items"])
        except Exception:
            o["items"] = []
    bookings = [dict(r) for r in core.q("SELECT * FROM site_bookings WHERE token=? ORDER BY id DESC LIMIT 50", (token,))]
    members = [{"name": r["name"], "email": r["email"], "created": r["created"]} for r in
               core.q("SELECT name,email,created FROM site_members WHERE token=? ORDER BY id DESC LIMIT 100", (token,))]
    posts = core.q("SELECT COUNT(*) c FROM site_posts WHERE token=?", (token,), one=True)
    return {"ok": True, "orders": orders, "bookings": bookings, "members": members,
            "counts": {"orders": len(orders), "bookings": len(bookings), "members": len(members),
                       "posts": posts["c"] if posts else 0}}


def set_status(user, sid, kind, item_id, status):
    token = _owned_token(user, sid)
    if not token:
        return {"error": "Not found."}
    table = {"order": "site_orders", "booking": "site_bookings"}.get(kind)
    if not table:
        return {"error": "Unknown kind."}
    status = _clip(status, 20) or "new"
    core.x(f"UPDATE {table} SET status=? WHERE id=? AND token=?", (status, int(item_id), token))
    return {"ok": True}
