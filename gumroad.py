"""A login's own Gumroad store: sales, products, discount codes and payouts, read and managed through Gumroad's own API.

Each login connects its own store with an access token made on Gumroad (Settings > Advanced > Applications). The token is
kept encrypted under that login only. Sales are copied into this PC's database so totals are instant and work when Gumroad
is slow; buyer details never leave this PC.
"""
import json
import os
import re
import time
from urllib.parse import quote

import httpx

import core

BASE = os.environ.get("TRILLION_GUMROAD", "https://api.gumroad.com/v2").rstrip("/")
SECRET = "gumroad_token"
EVERY = int(os.environ.get("TRILLION_GUMROAD_EVERY", 5 * 60))        # seconds between checks in the background
LOOK_BACK = 45                                                       # days re-read on every check, so refunds show up
MAX_PAGES = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS gumroad_sales(user_id INTEGER, id TEXT, created REAL, product_id TEXT, product TEXT, email TEXT,
  price INTEGER, fee INTEGER, currency TEXT, shown TEXT, refunded INTEGER DEFAULT 0, partial INTEGER DEFAULT 0,
  disputed INTEGER DEFAULT 0, code TEXT, seen INTEGER DEFAULT 1, PRIMARY KEY(user_id, id));
CREATE TABLE IF NOT EXISTS gumroad_store(user_id INTEGER PRIMARY KEY, account TEXT, products TEXT, payouts TEXT, notes TEXT,
  synced REAL, error TEXT, complete INTEGER DEFAULT 0);
"""


class GumroadError(Exception):
    pass


def init():
    c = core._conn()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def token(user):
    return core.get_secret(user["id"], SECRET)


def call(user, method, path, tok=None, **params):
    """One request to Gumroad. Returns the answer; raises GumroadError with a plain sentence when it fails."""
    tok = tok or token(user)
    if not tok:
        raise GumroadError("Your Gumroad store is not connected yet. Connect it on the Store page.")
    params = {k: ("true" if v is True else "false" if v is False else v) for k, v in params.items() if v is not None}
    kw = {"params": params} if method == "GET" else {"data": params}
    try:
        r = httpx.request(method, BASE + path, headers={"Authorization": "Bearer " + tok}, timeout=40, **kw)
    except httpx.HTTPError as e:
        raise GumroadError("I could not reach Gumroad (" + type(e).__name__ + "). Check the internet connection.")
    try:
        body = r.json()
    except Exception:
        body = {}
    said = str(body.get("message") or "").strip() if isinstance(body, dict) else ""
    if r.status_code == 401:
        raise GumroadError("Gumroad did not accept the access token. Make a new one on Gumroad and connect again.")
    if r.status_code == 403:
        raise GumroadError("The access token is not allowed to do that" + (": " + said if said else ".")
                           + " On Gumroad, give the application that permission and make a new token.")
    if r.status_code == 429:
        raise GumroadError("Gumroad says too many requests. I will try again in a few minutes.")
    if r.status_code >= 500:
        raise GumroadError(f"Gumroad has a problem on its side (error {r.status_code}). Try again later.")
    if r.status_code >= 400 or not isinstance(body, dict) or body.get("success") is False:
        raise GumroadError(said or f"Gumroad refused that (error {r.status_code}).")
    return body


def _pid(x):
    return quote(str(x), safe="")


def _when(iso):
    try:
        from datetime import datetime, timezone
        return datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time()


def _store(uid):
    r = core.q("SELECT * FROM gumroad_store WHERE user_id=?", (uid,), one=True)
    if not r:
        core.x("INSERT OR IGNORE INTO gumroad_store(user_id) VALUES(?)", (uid,))
        r = core.q("SELECT * FROM gumroad_store WHERE user_id=?", (uid,), one=True)
    return dict(r)


def _save(uid, **kw):
    _store(uid)
    for k, v in kw.items():
        core.x(f"UPDATE gumroad_store SET {k}=? WHERE user_id=?", (json.dumps(v) if isinstance(v, (dict, list)) else v, uid))


def _load(text, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


# ---------- connecting ----------
def connect(user, tok):
    tok = re.sub(r"\s+", "", str(tok or ""))
    if len(tok) < 20:
        raise GumroadError("That does not look like a Gumroad access token. Copy the whole token from Gumroad.")
    me = call(user, "GET", "/user", tok=tok).get("user") or {}
    core.set_secret(user["id"], SECRET, tok)
    core.x("DELETE FROM gumroad_sales WHERE user_id=?", (user["id"],))
    core.x("DELETE FROM gumroad_store WHERE user_id=?", (user["id"],))
    _save(user["id"], account={"name": me.get("name") or "", "email": me.get("email") or "", "url": me.get("url") or ""})
    return sync(user)


def disconnect(user):
    core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], SECRET))
    core.x("DELETE FROM gumroad_sales WHERE user_id=?", (user["id"],))
    core.x("DELETE FROM gumroad_store WHERE user_id=?", (user["id"],))


# ---------- copying the store to this PC ----------
def _keep_sale(uid, s, fresh):
    sid = str(s.get("id") or "")
    if not sid:
        return False
    code = s.get("offer_code")
    code = (code.get("name") if isinstance(code, dict) else code) or ""
    vals = (_when(s.get("created_at")), str(s.get("product_id") or ""), str(s.get("product_name") or ""),
            str(s.get("purchase_email") or s.get("email") or ""), int(s.get("price") or 0), int(s.get("gumroad_fee") or 0),
            str(s.get("currency") or "usd").lower(), str(s.get("formatted_total_price") or s.get("formatted_display_price") or ""),
            1 if s.get("refunded") else 0, 1 if s.get("partially_refunded") else 0,
            1 if (s.get("disputed") or s.get("chargedback")) else 0, str(code))
    if core.q("SELECT 1 FROM gumroad_sales WHERE user_id=? AND id=?", (uid, sid), one=True):
        core.x("UPDATE gumroad_sales SET created=?,product_id=?,product=?,email=?,price=?,fee=?,currency=?,shown=?,refunded=?,"
               "partial=?,disputed=?,code=? WHERE user_id=? AND id=?", vals + (uid, sid))
        return False
    core.x("INSERT INTO gumroad_sales(created,product_id,product,email,price,fee,currency,shown,refunded,partial,disputed,code,"
           "user_id,id,seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (uid, sid, 0 if fresh else 1))
    return True


def sync(user, everything=False):
    """Read the store from Gumroad. The first time all sales are copied; after that only the last weeks are re-read.
    Returns the number of sales not seen before."""
    uid = user["id"]
    st = _store(uid)
    first = everything or not st["complete"]
    notes = {}
    try:
        products = call(user, "GET", "/products").get("products") or []
        _save(uid, products=[{"id": p.get("id"), "name": p.get("name") or "", "price": int(p.get("price") or 0),
                              "currency": str(p.get("currency") or "usd").lower(), "shown": p.get("formatted_price") or "",
                              "published": bool(p.get("published")), "url": p.get("short_url") or "",
                              "sales": p.get("sales_count"), "earned_usd_cents": p.get("sales_usd_cents"),
                              "thumb": p.get("thumbnail_url") or "", "membership": bool(p.get("is_tiered_membership"))}
                             for p in products if not p.get("deleted")])
        params = {} if first else {"after": time.strftime("%Y-%m-%d", time.localtime(time.time() - LOOK_BACK * 86400))}
        new, names, key = 0, [], None
        try:
            for _ in range(MAX_PAGES):
                page = call(user, "GET", "/sales", page_key=key, **params)
                for s in page.get("sales") or []:
                    if _keep_sale(uid, s, fresh=not first):
                        new += 1
                        names.append((s.get("product_name") or "a product", s.get("formatted_total_price") or ""))
                key = page.get("next_page_key")
                if not key:
                    break
        except GumroadError as e:
            if "not allowed" not in str(e):
                raise
            notes["sales"] = "The access token may not read sales. Give the application the view_sales permission on Gumroad."
        try:
            pay = call(user, "GET", "/payouts").get("payouts") or []
            _save(uid, payouts=[{"id": p.get("id"), "amount": p.get("amount"), "currency": p.get("currency"), "status": p.get("status"),
                                 "created": p.get("created_at"), "paid": p.get("processed_at"),
                                 "to": p.get("bank_account_visual") or p.get("paypal_email") or ""} for p in pay[:12]])
        except GumroadError as e:
            if "not allowed" not in str(e):
                raise
            notes["payouts"] = "The access token may not read payouts. Give the application the view_payouts permission on Gumroad."
        _save(uid, synced=time.time(), error="", complete=1, notes=notes)
        if new and not first:
            for name, amount in names[:5]:
                core.log(uid, f"Gumroad sale: {name}" + (f" for {amount}" if amount else ""), "done")
        return new
    except GumroadError as e:
        _save(uid, synced=time.time(), error=str(e))
        raise


# ---------- figures ----------
def _money(cents, cur):
    sym = {"usd": "$", "eur": "€", "gbp": "£", "zar": "R", "aud": "A$", "cad": "C$", "inr": "₹"}.get(cur, "")
    whole = cents / (1 if cur == "jpy" else 100)
    return (sym or "") + (f"{whole:,.0f}" if cur == "jpy" else f"{whole:,.2f}") + ("" if sym else " " + cur.upper())


def _totals(rows):
    """Sales, money taken and money after Gumroad's fee, per currency. Fully refunded sales are left out."""
    out = {}
    for r in rows:
        if r["refunded"]:
            continue
        t = out.setdefault(r["currency"], {"count": 0, "gross": 0, "net": 0})
        t["count"] += 1
        t["gross"] += r["price"]
        t["net"] += r["price"] - r["fee"]
    lst = [{"currency": c, "count": t["count"], "gross": _money(t["gross"], c), "net": _money(t["net"], c),
            "gross_cents": t["gross"]} for c, t in sorted(out.items(), key=lambda kv: -kv[1]["gross"])]
    return {"count": sum(t["count"] for t in out.values()), "money": lst,
            "text": " + ".join(m["gross"] for m in lst) if lst else _money(0, "usd")}


def overview(user, mark_seen=False):
    """Everything the Store page shows."""
    uid = user["id"]
    if not token(user):
        return {"connected": False}
    st = _store(uid)
    rows = [dict(r) for r in core.q("SELECT * FROM gumroad_sales WHERE user_id=? ORDER BY created DESC", (uid,))]
    now = time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    month = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    since = lambda t: [r for r in rows if r["created"] >= t]
    periods = [("today", "Today", since(midnight)), ("week", "Last 7 days", since(midnight - 6 * 86400)),
               ("days30", "Last 30 days", since(midnight - 29 * 86400)), ("month", "This month", since(month)),
               ("all", "All time", rows)]
    main = (_totals(rows)["money"] or [{"currency": "usd"}])[0]["currency"]
    days = []
    for i in range(29, -1, -1):
        a = midnight - i * 86400
        day = [r for r in rows if a <= r["created"] < a + 86400 and not r["refunded"] and r["currency"] == main]
        days.append({"day": time.strftime("%d %b", time.localtime(a)), "count": len(day), "cents": sum(r["price"] for r in day),
                     "shown": _money(sum(r["price"] for r in day), main)})
    top = {}
    for r in since(midnight - 29 * 86400):
        if not r["refunded"]:
            t = top.setdefault((r["product"], r["currency"]), [0, 0])
            t[0] += 1
            t[1] += r["price"]
    new = [r for r in rows if not r["seen"]]
    if mark_seen and new:
        core.x("UPDATE gumroad_sales SET seen=1 WHERE user_id=?", (uid,))
    return {"connected": True, "account": _load(st["account"], {}), "synced": st["synced"], "error": st["error"] or "",
            "notes": _load(st["notes"], {}), "currency": main,
            "periods": [dict(_totals(rs), key=k, label=lab) for k, lab, rs in periods],
            "days": days,
            "top": [{"product": k[0], "count": v[0], "money": _money(v[1], k[1])} for k, v in sorted(top.items(), key=lambda kv: -kv[1][1])[:8]],
            "products": _load(st["products"], []), "payouts": _load(st["payouts"], []),
            "new": len(new),
            "sales": [{"id": r["id"], "when": r["created"], "product": r["product"], "email": r["email"],
                       "amount": r["shown"] or _money(r["price"], r["currency"]), "code": r["code"],
                       "state": "refunded" if r["refunded"] else "part refunded" if r["partial"] else "disputed" if r["disputed"] else "",
                       "new": not r["seen"]} for r in rows[:60]]}


def unseen(uid):
    r = core.q("SELECT COUNT(*) c FROM gumroad_sales WHERE user_id=? AND seen=0", (uid,), one=True)
    return r["c"] if r else 0


def say_sales(user, period="today"):
    """One spoken sentence about sales in a period: today, week, days30, month or all."""
    o = overview(user)
    if not o["connected"]:
        return "Your Gumroad store is not connected yet. Open the Store page and connect it."
    p = next((x for x in o["periods"] if x["key"] == period), o["periods"][0])
    words = {"today": "today", "week": "in the last 7 days", "days30": "in the last 30 days", "month": "this month", "all": "in total"}[p["key"]]
    if not p["count"]:
        return f"No Gumroad sales {words}." + (" " + o["error"] if o["error"] else "")
    net = " + ".join(m["net"] for m in p["money"])
    best = o["top"][0]["product"] if o["top"] and p["key"] != "today" else ""
    return (f"Gumroad {words}: {p['count']} sale{'s' if p['count'] != 1 else ''}, {p['text']} taken, {net} after Gumroad's fee."
            + (f" Best seller in the last 30 days: {best}." if best else ""))


# ---------- managing ----------
def find_product(user, name):
    """One product by its name or part of it. Never guesses between several."""
    products = _load(_store(user["id"])["products"], [])
    want = re.sub(r"\s+", " ", str(name or "").lower().strip().strip("\"'"))
    hits = [p for p in products if p["id"] == name] or [p for p in products if p["name"].lower() == want] \
        or [p for p in products if want and want in p["name"].lower()]
    if not hits and want:
        import difflib
        hits = [p for p in products if difflib.SequenceMatcher(None, want, p["name"].lower()).ratio() >= 0.75]
    if not hits:
        raise GumroadError(f"I could not find a product called '{name}' in your Gumroad store.")
    if len(hits) > 1:
        raise GumroadError("More than one product matches: " + "; ".join(p["name"] for p in hits[:6]) + ". Say exactly which one.")
    return hits[0]


def _refresh_products(user):
    try:
        sync(user)
    except GumroadError:
        pass


def set_published(user, pid, on):
    call(user, "PUT", f"/products/{_pid(pid)}/{'enable' if on else 'disable'}")
    _refresh_products(user)


def update_product(user, pid, name=None, price_cents=None):
    if name is None and price_cents is None:
        raise GumroadError("Nothing to change.")
    if price_cents is not None and (int(price_cents) < 0 or int(price_cents) > 5_000_000):
        raise GumroadError("That price does not look right.")
    call(user, "PUT", f"/products/{_pid(pid)}", name=(name or None), price=(int(price_cents) if price_cents is not None else None))
    _refresh_products(user)


def delete_product(user, pid):
    call(user, "DELETE", f"/products/{_pid(pid)}")
    _refresh_products(user)


def codes(user, pid):
    out = call(user, "GET", f"/products/{_pid(pid)}/offer_codes").get("offer_codes") or []
    cur = next((p["currency"] for p in _load(_store(user["id"])["products"], []) if p["id"] == pid), "usd")
    return [{"id": c.get("id"), "name": c.get("name"), "used": c.get("times_used") or 0, "limit": c.get("max_purchase_count"),
             "all_products": bool(c.get("universal")),
             "off": (f"{c['percent_off']}%" if c.get("percent_off") is not None else _money(int(c.get("amount_cents") or 0), cur))}
            for c in out]


def create_code(user, pid, name, amount, percent=True, limit=None, all_products=False):
    name = re.sub(r"[^A-Za-z0-9_-]", "", str(name or "")).upper()[:40]
    if len(name) < 2:
        raise GumroadError("Give the code a name of letters and numbers, like SPRING20.")
    amount = float(amount)
    if percent and not 1 <= amount <= 100:
        raise GumroadError("A percentage discount must be between 1 and 100.")
    if amount <= 0:
        raise GumroadError("The discount must be more than nothing.")
    call(user, "POST", f"/products/{_pid(pid)}/offer_codes", name=name, amount_off=int(round(amount if percent else amount * 100)),
         offer_type="percent" if percent else "cents", max_purchase_count=(int(limit) if limit else None),
         universal=True if all_products else None)
    return name


def delete_code(user, pid, cid):
    call(user, "DELETE", f"/products/{_pid(pid)}/offer_codes/{_pid(cid)}")


def refund(user, sale_id, amount_cents=None):
    call(user, "PUT", f"/sales/{_pid(sale_id)}/refund", amount_cents=(int(amount_cents) if amount_cents else None))
    _refresh_products(user)


def resend_receipt(user, sale_id):
    call(user, "POST", f"/sales/{_pid(sale_id)}/resend_receipt")


def reviews(user, pid):
    out = call(user, "GET", f"/products/{_pid(pid)}/reviews").get("product_reviews") or []
    return [{"stars": r.get("rating"), "text": r.get("message") or "", "by": r.get("rater_name") or "", "when": r.get("created_at")}
            for r in out[:30]]


def find_sale(user, sale):
    """A sale by its number, or the most recent sale to an email address."""
    uid = user["id"]
    r = core.q("SELECT * FROM gumroad_sales WHERE user_id=? AND id=?", (uid, str(sale)), one=True)
    if not r and "@" in str(sale):
        r = core.q("SELECT * FROM gumroad_sales WHERE user_id=? AND lower(email)=? ORDER BY created DESC", (uid, str(sale).lower().strip()), one=True)
    if not r:
        raise GumroadError("I could not find that sale in your Gumroad store.")
    return dict(r)


def background():
    """Check every connected store every few minutes."""
    while True:
        time.sleep(EVERY)
        try:
            for u in core.q("SELECT * FROM users"):
                if token(u):
                    try:
                        sync(dict(u))
                    except Exception:
                        pass
        except Exception:
            pass
