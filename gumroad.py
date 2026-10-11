"""A login's own Gumroad store: sales, products, discount codes and payouts, read and managed through Gumroad's own API.

Each login connects its own store with an access token made on Gumroad (Settings > Advanced > Applications). The token is
kept encrypted under that login only. Sales are copied into this PC's database so totals are instant and work when Gumroad
is slow; buyer details never leave this PC.
"""
import json
import os
import re
import threading
import time
from urllib.parse import quote

import httpx

import core

BASE = os.environ.get("TRILLION_GUMROAD", "https://api.gumroad.com/v2").rstrip("/")
INFO = os.environ.get("TRILLION_GUMROAD_INFO", "https://gumroad.com/oauth/token/info")    # tells which permissions a token has
SECRET = "gumroad_token"
PART = 100 * 1024 * 1024                         # Gumroad takes a file in pieces of 100 MB
# what each Gumroad permission lets Trillion do, in the order the Store page shows them
PERMS = [("view_profile", "See your products", ("view_profile", "edit_products", "view_sales")),
         ("view_sales", "See sales and buyers", ("view_sales",)),
         ("view_payouts", "See payouts", ("view_payouts",)),
         ("edit_products", "Create, change, publish and delete products, files and discount codes", ("edit_products",)),
         ("edit_sales", "Refund sales and resend receipts", ("edit_sales", "refund_sales"))]
CHOICES = {900: "every 15 minutes", 3600: "every hour", 86400: "once a day", 604800: "once a week"}   # how often to check for sales
EVERY = 900
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
    pairs = params.pop("_pairs", None)             # a list of (name, value) in order, for Gumroad's array parameters
    params = {k: ("true" if v is True else "false" if v is False else v) for k, v in params.items() if v is not None}
    head = {"Authorization": "Bearer " + tok}
    if pairs is not None:
        from urllib.parse import urlencode
        head["Content-Type"] = "application/x-www-form-urlencoded"
        kw = {"content": urlencode(list(params.items()) + list(pairs))}
    else:
        kw = {"params": params} if method == "GET" else {"data": params}
    try:
        r = httpx.request(method, BASE + path, headers=head, timeout=60, **kw)
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
    new = sync(user)
    try:
        permissions(user)
    except Exception:
        pass
    return new


def disconnect(user):
    core.x("DELETE FROM secrets WHERE user_id=? AND name=?", (user["id"], SECRET))
    core.x("DELETE FROM gumroad_sales WHERE user_id=?", (user["id"],))
    core.x("DELETE FROM gumroad_store WHERE user_id=?", (user["id"],))


# ---------- what the token may do ----------
def permissions(user):
    """Which of the things Trillion does this token is allowed to do. Asks Gumroad for the token's own list; if Gumroad
    will not say, the reading permissions are found by trying them and the changing ones are marked as not known."""
    tok = token(user)
    if not tok:
        raise GumroadError("Your Gumroad store is not connected yet.")
    scopes, how = None, "tried"
    try:
        r = httpx.get(INFO, headers={"Authorization": "Bearer " + tok}, timeout=25)
        j = r.json() if r.status_code == 200 else {}
        got = j.get("scope") if "scope" in j else j.get("scopes")
        if isinstance(got, str):
            got = got.split()
        if isinstance(got, list):
            scopes, how = [str(x) for x in got], "gumroad"
    except Exception:
        pass
    out = []
    if scopes is not None:
        for key, label, any_of in PERMS:
            out.append({"key": key, "label": label, "ok": "account" in scopes or any(x in scopes for x in any_of)})
    else:
        def can(path):
            try:
                call(user, "GET", path)
                return True
            except GumroadError as e:
                return False if "not allowed" in str(e) else None
        tried = {"view_profile": can("/products"), "view_sales": can("/sales"), "view_payouts": can("/payouts")}
        for key, label, _ in PERMS:
            out.append({"key": key, "label": label, "ok": tried.get(key)})
    res = {"list": out, "how": how, "when": time.time(), "scopes": scopes or []}
    notes = _load(_store(user["id"])["notes"], {})
    notes["perms"] = res
    _save(user["id"], notes=notes)
    return res


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
    core.x("INSERT OR IGNORE INTO gumroad_sales(created,product_id,product,email,price,fee,currency,shown,refunded,partial,disputed,code,"
           "user_id,id,seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (uid, sid, 0 if fresh else 1))
    return True


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def sync(user, everything=False):
    """Read the store from Gumroad. The first time all sales are copied; after that only the last weeks are re-read.
    Returns the number of sales not seen before. One check at a time per login: the timed check and a pressed
    Check now must not both file the same sale."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(user["id"], threading.Lock())
    with lock:
        return _sync(user, everything)


def _sync(user, everything=False):
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
        old = _load(st["notes"], {})
        if old.get("perms"):
            notes["perms"] = old["perms"]
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
            "every": every(user), "every_words": CHOICES[every(user)],
            "notes": {k: v for k, v in _load(st["notes"], {}).items() if k != "perms"}, "perms": _load(st["notes"], {}).get("perms"), "currency": main,
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


def upload_file(user, path, name=None, progress=None):
    """Send one file from this PC to Gumroad's storage and return the address to attach to a product."""
    size = os.path.getsize(path)
    if size <= 0:
        raise GumroadError(f"{os.path.basename(path)} is empty.")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or os.path.basename(path))[:150]
    start = call(user, "POST", "/files/presign", filename=name, file_size=size)
    parts, done = sorted(start.get("parts") or [], key=lambda p: p["part_number"]), []
    if not parts or not start.get("upload_id"):
        raise GumroadError("Gumroad did not give a place to upload the file to.")
    try:
        with open(path, "rb") as f:
            for i, p in enumerate(parts):
                chunk = f.read(PART)
                r = httpx.put(p["presigned_url"], content=chunk, timeout=httpx.Timeout(60, write=1800, read=1800))
                if r.status_code >= 300 or not r.headers.get("etag"):
                    raise GumroadError(f"The upload of {name} stopped at part {i + 1} (error {r.status_code}).")
                done.append((p["part_number"], r.headers["etag"]))
                if progress:
                    progress(min(size, (i + 1) * PART), size)
    except (httpx.HTTPError, OSError, GumroadError) as e:
        try:
            call(user, "POST", "/files/abort", upload_id=start["upload_id"], key=start.get("key"))
        except GumroadError:
            pass
        raise e if isinstance(e, GumroadError) else GumroadError(f"The upload of {name} failed ({type(e).__name__}). Nothing was created.")
    pairs = []
    for n, tag in done:
        pairs += [("parts[][part_number]", n), ("parts[][etag]", tag)]
    fin = call(user, "POST", "/files/complete", upload_id=start["upload_id"], key=start.get("key"), _pairs=pairs)
    return fin.get("file_url") or start.get("file_url")


def _html(text):
    """Plain typed text as the simple HTML Gumroad wants for a description."""
    import html
    text = str(text or "").strip()
    if not text or re.search(r"<(p|br|ul|ol|h\d|div)\b", text, re.I):
        return text or None
    return "".join("<p>" + html.escape(par.strip()).replace("\n", "<br>") + "</p>" for par in re.split(r"\n\s*\n", text) if par.strip())


def create_product(user, name, price_cents, description="", files=None, publish=True, currency=None, summary=None, progress=None):
    """Make a new product on Gumroad, upload its files and, if asked, put it on sale. files is a list of (path, name).
    Returns {"product", "published", "warning"}."""
    name = str(name or "").strip()[:200]
    if len(name) < 2:
        raise GumroadError("Give the product a name.")
    price_cents = int(price_cents)
    if price_cents < 0 or price_cents > 5_000_000:
        raise GumroadError("That price does not look right.")
    files = [(str(p), n) for p, n in (files or [])]
    for p, n in files:
        if not os.path.isfile(p):
            raise GumroadError(f"I cannot find the file {n or os.path.basename(p)} on the PC any more.")
    urls = [upload_file(user, p, n, progress) for p, n in files]
    kinds = {os.path.splitext(n or p)[1].lower() for p, n in files}
    pairs = [("files[][url]", u) for u in urls]
    out = call(user, "POST", "/products", name=name, price=price_cents, description=_html(description),
               native_type="ebook" if kinds and kinds <= {".pdf", ".epub", ".mobi"} else "digital",
               price_currency_type=(currency or "").lower() or None, custom_summary=(str(summary).strip()[:200] if summary else None),
               published=bool(publish), _pairs=pairs)
    p = out.get("product") or {}
    _refresh_products(user)
    return {"product": {"id": p.get("id"), "name": p.get("name") or name, "url": p.get("short_url") or "",
                        "shown": p.get("formatted_price") or _money(price_cents, (currency or "usd").lower())},
            "published": bool(p.get("published")), "files": len(urls), "warning": str(out.get("warning") or "")}


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


def every(user):
    """How often this login wants its store checked, in seconds."""
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)       # the saved choice, not a copy read earlier
    try:
        v = int(core.settings(row or user).get("store_every") or EVERY)
    except (TypeError, ValueError):
        v = EVERY
    return v if v in CHOICES else EVERY


def due(user, now=None):
    st = core.q("SELECT synced FROM gumroad_store WHERE user_id=?", (user["id"],), one=True)
    return not st or not st["synced"] or (now or time.time()) - st["synced"] >= every(user)


def background(pause=60):
    """Look once a minute for stores whose next check has come round, each at the pace its login chose."""
    while True:
        time.sleep(pause)
        try:
            for u in core.q("SELECT * FROM users"):
                u = dict(u)
                if token(u) and core.settings(u).get("store_on") and due(u):
                    try:
                        sync(u)
                    except Exception:
                        pass
        except Exception:
            pass
