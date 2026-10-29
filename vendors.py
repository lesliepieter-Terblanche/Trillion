"""Vendor / channel intelligence: a workspace for a channel manager's vendor portfolio.

Keep each vendor's context — target, partners, QBR date, notes — and ask Trillion for a QBR-ready brief that
pulls it all together: the stored context, anything in the user's own documents and notes about that vendor,
and fresh news from the local web search. Built on the deep-research and ask-my-stuff pieces; fully local.
"""
import time

import core

FIELDS = ("category", "target", "partners", "qbr", "status", "notes")


def init():
    core.x("""CREATE TABLE IF NOT EXISTS vendors(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, category TEXT DEFAULT '',
        target TEXT DEFAULT '', partners TEXT DEFAULT '', qbr TEXT DEFAULT '', status TEXT DEFAULT 'active',
        notes TEXT DEFAULT '', created REAL, updated REAL)""")


def _find(user, name):
    init()
    n = str(name or "").strip().lower()
    return core.q("SELECT * FROM vendors WHERE user_id=? AND lower(name)=?", (user["id"], n), one=True) or \
        core.q("SELECT * FROM vendors WHERE user_id=? AND lower(name) LIKE ?", (user["id"], f"%{n}%"), one=True)


def add(user, name, **fields):
    init()
    name = str(name or "").strip()
    if not name:
        return {"error": "Give the vendor a name."}
    row = _find(user, name)
    f = {k: str(v)[:2000] for k, v in fields.items() if k in FIELDS and v is not None}
    if row:
        if f:
            sets = ", ".join(f"{k}=?" for k in f)
            core.x(f"UPDATE vendors SET {sets}, updated=? WHERE id=?", (*f.values(), time.time(), row["id"]))
        return {"ok": True, "updated": name}
    core.x("""INSERT INTO vendors(user_id,name,category,target,partners,qbr,status,notes,created,updated)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
           (user["id"], name, f.get("category", ""), f.get("target", ""), f.get("partners", ""),
            f.get("qbr", ""), f.get("status", "active"), f.get("notes", ""), time.time(), time.time()))
    return {"ok": True, "added": name}


def update(user, name, **fields):
    row = _find(user, name)
    if not row:
        return add(user, name, **fields)
    return add(user, row["name"], **fields)


def remove(user, name):
    row = _find(user, name)
    if row:
        core.x("DELETE FROM vendors WHERE id=?", (row["id"],))
    return {"ok": True}


def listing(user):
    init()
    return [{k: r[k] for k in r.keys() if k != "user_id"} for r in
            core.q("SELECT * FROM vendors WHERE user_id=? ORDER BY name", (user["id"],))]


def _llm(user, system, prompt):
    try:
        fn = getattr(core, "LLM", None)
        return (fn(user, [{"role": "system", "content": system}, {"role": "user", "content": prompt}])
                .get("content") or "") if fn else ""
    except Exception:
        return ""


def brief(user, name, fresh=True, progress=None):
    """A QBR-ready brief for one vendor: stored context + the user's own docs/notes + fresh web news."""
    say = progress or (lambda t: None)
    row = _find(user, name)
    vname = row["name"] if row else str(name).strip()
    if not vname:
        return {"error": "Which vendor?"}
    parts = []
    if row:
        for k in FIELDS:
            if row[k]:
                parts.append(f"{k.capitalize()}: {row[k]}")
    # the user's own documents and notes
    say("Checking your documents and notes")
    try:
        import askstuff
        for h in askstuff.search(user, vname, k=4):
            parts.append(f"[Your doc: {h['title']}] {h['text'][:400]}")
    except Exception:
        pass
    try:
        import tools
        for nte in tools.search_notes(user["id"], vname, k=4):
            parts.append(f"[Your note: {nte.get('title')}] {(nte.get('body') or '')[:300]}")
    except Exception:
        pass
    # fresh news
    sources = []
    if fresh:
        say(f"Researching recent {vname} news")
        try:
            import research
            r = research.research(user, f"{vname} channel partner program news {time.strftime('%Y')}",
                                  progress=say)
            if r.get("brief"):
                parts.append(f"[Recent web news]\n{r['brief']}")
                sources = r.get("sources", [])
        except Exception:
            pass
    if not parts:
        return {"error": f"I have nothing on {vname} yet. Add context with vendor_add, or index your documents."}
    say("Writing the brief")
    out = _llm(user,
               "You are a channel manager's analyst. Write a crisp QBR-ready vendor brief from the material: where "
               "things stand, partners, any risks or opportunities, recent news, and 2-3 suggested next actions. "
               "Factual, grounded only in what's given. Use short sections.",
               f"Vendor: {vname}\n\nMaterial:\n" + "\n\n".join(parts)[:14000])
    out = (out or "").strip() or "Not enough to brief on yet."
    saved = None
    try:
        import tools
        body = out + ("\n\nSources:\n" + "\n".join(f"[{s['n']}] {s['title']} — {s['url']}" for s in sources) if sources else "")
        saved = tools.add_note(user["id"], f"Vendor brief: {vname}", body, "WORK")
    except Exception:
        pass
    return {"ok": True, "vendor": vname, "brief": out, "sources": sources, "saved_note_id": saved,
            "note": "Present the vendor brief; it's saved to the user's notes."}
