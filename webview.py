"""A login's own web page (a tracker, a portal) opened by Trillion herself, on this PC, and shown inside the dashboard.

Why: the page then works the same from the PC and from the phone over Tailscale, and Trillion can type the page's login
code for you, which she cannot do in a tab of your own browser. She drives a hidden browser window on this PC (Microsoft
Edge, which Windows already has), sends a picture of it to the dashboard, and passes your clicks and typing back.

Nothing is kept: pictures and page text live in memory only and go only to the login that owns the link. The hidden
browser keeps its own profile folder per login and link, so a page that stores its data in the browser keeps it.
"""
import os
import queue
import re
import threading
import time

import core

IDLE_CLOSE = int(os.environ.get("TRILLION_WEB_IDLE", 8 * 60))       # close the hidden browser after this long unwatched
SESSIONS = {}                                                       # (user id, link id) -> Session
LOCK = threading.Lock()
LOCK_WORDS = re.compile(r"enter (your )?(pin|passcode|code)|pin to unlock|to unlock|locked", re.I)

FIND_JS = r"""
(code) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none' && r.bottom > 0 && r.top < innerHeight; };
  const centre = e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  const text = (document.body.innerText || '').slice(0, 4000);
  const els = [...document.querySelectorAll('button,[role=button],input[type=button],input[type=submit],a,div,span,td,li')].filter(vis);
  const label = e => ((e.innerText || e.value || e.getAttribute('aria-label') || '') + '').trim();
  // the smallest visible element whose whole label is this text: that is the key itself, not the box around the keypad
  const key = t => els.filter(e => label(e) === t).sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0];
  const digits = {};
  for (const d of '0123456789') { const k = key(d); if (k) digits[d] = centre(k); }
  const field = [...document.querySelectorAll('input')].filter(vis).filter(i => ['password', 'tel', 'number', 'text', ''].includes(i.type || ''));
  const go = els.filter(e => /^(unlock|log ?in|sign ?in|submit|enter|continue|ok)$/i.test(label(e)))
                .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0];
  return { text, digits, keypad: Object.keys(digits).length === 10, field: field.length === 1 ? centre(field[0]) : null,
           fieldIsSecret: field.length === 1 && field[0].type === 'password', go: go ? centre(go) : null };
}
"""


class Session(threading.Thread):
    """One hidden browser page. Everything that touches the browser runs on this thread; callers queue a job and wait."""

    def __init__(self, user, link, code):
        super().__init__(daemon=True)
        self.user, self.link, self.code = dict(user), dict(link), code or ""
        self.jobs = queue.Queue()
        self.seen = time.time()
        self.error = ""
        self.ready = threading.Event()
        self.state = "starting"            # starting | open | locked | unlocked | refused
        self.tries = 0
        self.size = (1100, 720)
        self.browser_name = ""
        self.alive = True

    # ---- called from other threads ----
    def call(self, fn, timeout=40):
        if not self.alive:
            raise RuntimeError(self.error or "That page was closed. Open it again.")
        done, box = threading.Event(), {}
        self.jobs.put((fn, done, box))
        if not done.wait(timeout):
            raise RuntimeError("The page on the PC is not answering.")
        if "error" in box:
            raise RuntimeError(box["error"])
        self.seen = time.time()
        return box.get("value")

    def stop(self):
        self.alive = False
        self.jobs.put((None, None, None))

    # ---- the browser thread ----
    def run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self.error = ("The part that lets me open web pages myself is not installed yet. Tell me \"update yourself\" once more, "
                          "or run \"1 - Setup.bat\" again.")
            self.alive = False
            self.ready.set()
            return
        profile = core.DATA / "users" / self.user["name"].lower() / "browser" / re.sub(r"[^\w-]", "_", self.link["id"])
        profile.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as pw:
                ctx = None
                for channel in ("msedge", "chrome", None):          # Edge is on every Windows PC; her own browser is the last resort
                    try:
                        kw = dict(user_data_dir=str(profile), headless=True, viewport={"width": self.size[0], "height": self.size[1]})
                        if channel:
                            kw["channel"] = channel
                        ctx = pw.chromium.launch_persistent_context(**kw)
                        self.browser_name = channel or "chromium"
                        break
                    except Exception as e:
                        self.error = str(e).splitlines()[0][:200]
                if not ctx:
                    self.error = "I could not start a browser on the PC (" + self.error + ")."
                    self.alive = False
                    self.ready.set()
                    return
                self.error = ""
                self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    self.page.goto(self.link["url"], wait_until="domcontentloaded", timeout=45000)
                    self.page.wait_for_timeout(1500)
                    self.state = "open"
                    self._unlock()
                except Exception as e:
                    self.error = "The page did not open: " + str(e).splitlines()[0][:160]
                self.ready.set()
                while self.alive:
                    try:
                        fn, done, box = self.jobs.get(timeout=5)
                    except queue.Empty:
                        if time.time() - self.seen > IDLE_CLOSE:
                            break
                        continue
                    if fn is None:
                        break
                    try:
                        box["value"] = fn(self)
                    except Exception as e:
                        box["error"] = str(e).splitlines()[0][:200]
                    done.set()
                try:
                    ctx.close()
                except Exception:
                    pass
        except Exception as e:
            self.error = self.error or ("The browser on the PC stopped: " + str(e).splitlines()[0][:160])
        finally:
            self.alive = False
            self.ready.set()
            with LOCK:
                if SESSIONS.get((self.user["id"], self.link["id"])) is self:
                    SESSIONS.pop((self.user["id"], self.link["id"]), None)

    def _looks_locked(self, found):
        return bool(LOCK_WORDS.search(found["text"])) and (found["keypad"] or found["fieldIsSecret"])

    def _unlock(self):
        """If the page is showing its lock screen, type this link's saved code. Stops after two refusals so a wrong
        code can never be hammered at the page."""
        found = self.page.evaluate(FIND_JS, self.code)
        if not self._looks_locked(found):
            self.state = "unlocked" if self.state in ("locked", "unlocked", "refused") else "open"
            return
        if not self.code:
            self.state = "locked"
            return
        if self.tries >= 2:
            self.state = "refused"
            return
        self.tries += 1
        if found["keypad"] and self.code.isdigit():
            for d in self.code:
                x, y = found["digits"][d]
                self.page.mouse.click(x, y)
                self.page.wait_for_timeout(110)
        elif found["field"]:
            self.page.mouse.click(*found["field"])
            self.page.keyboard.type(self.code, delay=40)
        else:
            self.state = "locked"
            return
        self.page.wait_for_timeout(250)
        again = self.page.evaluate(FIND_JS, self.code)
        if self._looks_locked(again):                      # some pages unlock on the last digit, others wait for a button
            if again["go"]:
                self.page.mouse.click(*again["go"])
            else:
                self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(1300)
        after = self.page.evaluate(FIND_JS, self.code)
        if self._looks_locked(after):
            self.state = "refused" if self.tries >= 2 else "locked"
        else:
            self.state, self.tries = "unlocked", 0

    # ---- jobs (run on the browser thread) ----
    def shot(self):
        if self.state != "refused":
            self._unlock()                                  # the page locks itself again after a while: let her back in
        return self.page.screenshot(type="jpeg", quality=62)

    def text(self):
        return (self.page.evaluate("document.body.innerText") or "")[:20000]

    def resize(self, w, h):
        w, h = max(320, min(1600, int(w))), max(420, min(1200, int(h)))
        if (w, h) != self.size:
            self.size = (w, h)
            self.page.set_viewport_size({"width": w, "height": h})
            self.page.wait_for_timeout(250)


def session(user, link, code, start=True):
    key = (user["id"], link["id"])
    with LOCK:
        s = SESSIONS.get(key)
        if s and s.alive:
            if code and code != s.code:                     # a code was just saved: let her try it
                s.code, s.tries = code, 0
                if s.state in ("locked", "refused"):
                    s.state = "locked"
            return s
        if not start:
            return None
        s = SESSIONS[key] = Session(user, link, code)
        s.start()
    s.ready.wait(70)
    return s


def close(user, lid):
    with LOCK:
        s = SESSIONS.pop((user["id"], lid), None)
    if s:
        s.stop()


def close_all(uid):
    with LOCK:
        gone = [k for k in SESSIONS if k[0] == uid]
        found = [SESSIONS.pop(k) for k in gone]
    for s in found:
        s.stop()
