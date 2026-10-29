"""Deep research: Trillion plans a question into several web searches, reads the top pages, and writes a short
sourced brief — all through the local SearXNG engine and the local brain, so it's private and token-free.

Bounded on purpose (a few queries, a handful of pages) so it finishes in under a minute and doesn't hammer the
web. The finished brief is also saved to the user's notes so they can find it later.
"""
import json
import re

import httpx

import core

UA = {"User-Agent": "Mozilla/5.0 (compatible; Trillion/1.0)"}


def _llm(user, system, prompt):
    try:
        fn = getattr(core, "LLM", None)
        if not fn:
            return ""
        return (fn(user, [{"role": "system", "content": system}, {"role": "user", "content": prompt}])
                .get("content") or "")
    except Exception:
        return ""


def _search(query, count=5):
    try:
        import apps
        base = apps.base("searxng")
    except Exception:
        base = "http://127.0.0.1:8899"
    try:
        r = httpx.get(base + "/search", params={"q": query, "format": "json", "safesearch": 1},
                      timeout=25, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None            # None signals SearXNG unavailable
    out = []
    for x in (data.get("results") or [])[:count]:
        if x.get("url"):
            out.append({"title": x.get("title") or x["url"], "url": x["url"], "snippet": (x.get("content") or "")[:300]})
    return out


def _strip(s):
    s = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", s)).strip()


def _fetch(url, cap=3000):
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers=UA)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", "text/html"):
            return _strip(r.text)[:cap]
        if r.status_code == 200:
            return _strip(r.text)[:cap]
    except Exception:
        pass
    return ""


def _queries(user, question):
    raw = _llm(user, "You plan web research. Reply with a JSON array of strings only.",
               f'Break this research question into 3 focused web search queries. Question: "{question}". '
               f'Reply with ONLY a JSON array of 3 short query strings.')
    try:
        qs = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
        qs = [str(q).strip() for q in qs if str(q).strip()][:3]
        if qs:
            return qs
    except Exception:
        pass
    return [question]


def research(user, question, progress=None):
    say = progress or (lambda t: None)
    question = (question or "").strip()
    if not question:
        return {"error": "What should I research?"}
    say("Planning the searches")
    queries = _queries(user, question)
    results, seen = [], set()
    for q in queries:
        say(f"Searching: {q}")
        hits = _search(q, count=5)
        if hits is None:
            return {"error": "Deep research needs the private web search (SearXNG). Install it on the Apps page, "
                             "then try again."}
        for h in hits:
            if h["url"] not in seen:
                seen.add(h["url"])
                results.append(h)
    if not results:
        return {"error": "I couldn't find anything on that. Try rewording the question."}
    # read the top pages
    sources, context = [], []
    for i, h in enumerate(results[:6], 1):
        say(f"Reading source {i}: {h['title'][:50]}")
        body = _fetch(h["url"]) or h["snippet"]
        sources.append({"n": i, "title": h["title"], "url": h["url"]})
        context.append(f"[{i}] {h['title']} — {h['url']}\n{body}")
    say("Writing the brief")
    brief = _llm(
        user,
        "You are a sharp research analyst. Write a concise, factual brief grounded ONLY in the sources. Cite with "
        "[n] after each claim. If the sources disagree or don't answer part of it, say so. No fluff.",
        f'Research question: {question}\n\nSources:\n' + "\n\n".join(context)[:14000] +
        '\n\nWrite: a 2-3 sentence summary, then 4-7 key findings as short points each ending with its [n] citation(s).'
    ) or "I read the sources but couldn't compose a brief. The sources are listed below."
    src_lines = "\n".join(f"[{s['n']}] {s['title']} — {s['url']}" for s in sources)
    full = f"{brief}\n\nSources:\n{src_lines}"
    note_id = None
    try:
        import tools
        note_id = tools.add_note(user["id"], f"Research: {question[:80]}", full, "WORK")
    except Exception:
        pass
    return {"ok": True, "question": question, "brief": brief, "sources": sources,
            "saved_note_id": note_id, "note": "Present the brief with its sources. It's saved to the user's notes."}
