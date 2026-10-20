"""Scrape a site into structured data, using your local brain - no cloud, no tokens.

Built on ScrapeGraphAI (MIT), which drives a browser, reads the page, and has an LLM pull out exactly
what you asked for as clean JSON. It is a heavy library (LangChain + a browser), so it lives in its OWN
isolated environment under data/scrape-env and is called as a separate process - it never touches
Trillion's own packages. The brain is your local Ollama model, so nothing leaves the PC.

Three "skills" (pipelines), and Trillion asks which one fits what you need:
  - page   : pull data straight off one web page            (SmartScraperGraph)
  - search : find and scrape the top results for a question  (SearchGraph)
  - multi  : the same question across several pages you list  (SmartScraperMultiGraph)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import core

ENV_DIR = core.DATA / "scrape-env"
RUNNER = ENV_DIR / "trillion_scrape_runner.py"
JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()

MODES = {
    "page":   {"label": "This one page",  "need": "url",
               "hint": "Pull data straight off a single web page.", "cls": "SmartScraperGraph"},
    "search": {"label": "Search the web", "need": "query",
               "hint": "Find and scrape the top results for a question.", "cls": "SearchGraph"},
    "multi":  {"label": "Several pages",  "need": "urls",
               "hint": "Ask the same thing across several pages you list.", "cls": "SmartScraperMultiGraph"},
}


# ---------- the isolated environment ----------
def env_python():
    return str(ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def ready():
    """True once the isolated env exists and ScrapeGraphAI is importable in it."""
    if not Path(env_python()).exists() or not RUNNER.exists():
        return False
    try:
        r = subprocess.run([env_python(), "-c", "import scrapegraphai"], capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _uv():
    return shutil.which("uv")


def _run(cmd, timeout, log):
    log.append("$ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.stdout:
        log.append(r.stdout[-1500:])
    if r.returncode != 0:
        log.append((r.stderr or "")[-1500:])
    return r.returncode


def _write_runner():
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    RUNNER.write_text(RUNNER_SRC, encoding="utf-8")


def install():
    """Create data/scrape-env and install ScrapeGraphAI + a browser into it. Minutes, and big."""
    log = []
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    uv = _uv()
    try:
        if uv:
            if _run([uv, "venv", str(ENV_DIR), "--python", "3.12"], 600, log) != 0:
                _run([uv, "venv", str(ENV_DIR)], 600, log)            # fall back to whatever Python is here
            if _run([uv, "pip", "install", "--python", env_python(), "scrapegraphai"], 2400, log) != 0:
                raise RuntimeError("Could not install scrapegraphai. " + (log[-1] if log else ""))
        else:
            if _run([sys.executable, "-m", "venv", str(ENV_DIR)], 600, log) != 0:
                raise RuntimeError("Could not create the scrape environment.")
            if _run([env_python(), "-m", "pip", "install", "--upgrade", "pip"], 600, log) != 0:
                pass
            if _run([env_python(), "-m", "pip", "install", "scrapegraphai"], 3000, log) != 0:
                raise RuntimeError("Could not install scrapegraphai. " + (log[-1] if log else ""))
        # the browser ScrapeGraphAI drives (best-effort; the page fetch still works for many sites without it)
        _run([env_python(), "-m", "playwright", "install", "chromium"], 1200, log)
        _write_runner()
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    """Kick off the install in the background; progress shows in JOB."""
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up the scraper (this takes a few minutes)", error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")), error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Scraper ready" if res.get("ok") else "Scraper setup failed", "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


# ---------- running a scrape ----------
def _model():
    s = core.settings(core.q("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1", one=True) or {})
    pref = s.get("heavy_model") or "qwen2.5:14b"
    try:
        return core.pick_local_model(pref)
    except Exception:
        return pref


def run(mode, prompt, source, model=None, timeout=420):
    """Run one scrape. `source` is a URL (page), a list of URLs (multi), or ignored (search uses the prompt).
    Returns {'ok':True,'data':...} or {'ok':False,'error':...}. Blocks; callers run it off the request thread."""
    if mode not in MODES:
        return {"ok": False, "error": f"Unknown scrape type '{mode}'."}
    if not ready():
        return {"ok": False, "error": "The scraper isn't set up yet. Install it on the Scrape panel first."}
    if not str(prompt or "").strip():
        return {"ok": False, "error": "Tell me what to pull from the page."}
    job = {"mode": mode, "prompt": str(prompt).strip(),
           "source": source, "model": model or _model(), "base_url": core.OLLAMA}
    try:
        r = subprocess.run([env_python(), str(RUNNER)], input=json.dumps(job), text=True,
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The scrape took too long and was stopped. Try a simpler page or ask."}
    out = (r.stdout or "").strip()
    for line in reversed(out.splitlines()):          # the runner prints one JSON line last
        line = line.strip()
        if line.startswith("{"):
            try:
                res = json.loads(line)
                return res if "ok" in res else {"ok": True, "data": res}
            except Exception:
                break
    err = (r.stderr or out or "no output").strip().splitlines()
    return {"ok": False, "error": (err[-1] if err else "The scraper returned nothing.")[:300]}


def run_async(mode, prompt, source, done_cb, model=None):
    def go():
        done_cb(run(mode, prompt, source, model))
    threading.Thread(target=go, daemon=True).start()


def state():
    return {"ready": ready(), "job": dict(JOB),
            "modes": [{"key": k, "label": v["label"], "need": v["need"], "hint": v["hint"]} for k, v in MODES.items()],
            "model": _model()}


# ---------- the runner that lives inside the isolated env ----------
RUNNER_SRC = r'''
import json, sys

def main():
    job = json.loads(sys.stdin.read() or "{}")
    mode = job.get("mode"); prompt = job.get("prompt") or ""
    source = job.get("source"); model = job.get("model") or "qwen2.5:14b"
    base_url = job.get("base_url") or "http://127.0.0.1:11434"
    cfg = {
        "llm": {"model": "ollama/" + model, "model_tokens": 8192, "base_url": base_url, "format": "json"},
        "embeddings": {"model": "ollama/nomic-embed-text", "base_url": base_url},
        "verbose": False, "headless": True,
    }
    try:
        if mode == "page":
            from scrapegraphai.graphs import SmartScraperGraph
            g = SmartScraperGraph(prompt=prompt, source=source or "", config=cfg)
        elif mode == "search":
            from scrapegraphai.graphs import SearchGraph
            g = SearchGraph(prompt=prompt, config=cfg)
        elif mode == "multi":
            from scrapegraphai.graphs import SmartScraperMultiGraph
            srcs = source if isinstance(source, list) else [s for s in (source or "").split() if s]
            g = SmartScraperMultiGraph(prompt=prompt, source=srcs, config=cfg)
        else:
            print(json.dumps({"ok": False, "error": "unknown mode"})); return
        result = g.run()
        print(json.dumps({"ok": True, "data": result}, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__ + ": " + str(e)}))

if __name__ == "__main__":
    main()
'''
