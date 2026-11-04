"""Computer control: let Trillion see and operate THIS Windows PC the way it already drives the phone.

Same idea as phone.py: instead of a photo the brain has to interpret, Trillion reads the live UI
Automation tree of whatever window is in front and hands the brain a plain list of the things on screen
that can be read, clicked or typed into, each with its position. The brain then calls click / type / press
by the text of an item (or by x,y), so it works with the ordinary local (text) model — no vision model
needed. Open an app, focus a window, scroll, screenshot.

Windows only, owner-login only, and off until the owner switches "Computer control" on. It uses Microsoft
UI Automation through the `uiautomation` package, installed once on the PC (like the other heavy helpers).
Everything is best-effort and guarded: a bad call returns a clear message instead of raising.
"""
import os
import re
import subprocess
import sys
import threading
import time

import core

class DesktopError(Exception):
    pass


JOB = {"running": False, "status": "", "error": "", "done": False, "log": ""}
_LOCK = threading.Lock()
CACHE = core.DATA / "desktop"

# UI Automation control types that are worth clicking / reading / typing into
_CLICKABLE = {"ButtonControl", "MenuItemControl", "ListItemControl", "TabItemControl", "HyperlinkControl",
              "CheckBoxControl", "RadioButtonControl", "TreeItemControl", "SplitButtonControl",
              "MenuItem", "Button", "ListItem", "TabItem", "Hyperlink"}
_EDITABLE = {"EditControl", "DocumentControl", "ComboBoxControl", "Edit", "Document", "ComboBox"}


def available():
    """True on Windows (where UI Automation exists)."""
    return sys.platform.startswith("win")


def _auto():
    import uiautomation as auto      # raises ImportError if not set up yet
    return auto


def ready():
    """Installed and usable: Windows, and the uiautomation package imports."""
    if not available():
        return False
    try:
        _auto()
        return True
    except Exception:
        return False


# ---------- one-time setup ----------
def install():
    log = []
    try:
        if not available():
            raise RuntimeError("Computer control works on Windows only.")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "uiautomation", "pillow"],
                           capture_output=True, text=True, timeout=600)
        log.append((r.stdout or "")[-1500:])
        if r.returncode != 0:
            log.append((r.stderr or "")[-1500:])
            raise RuntimeError("Could not install the computer-control parts. " + (r.stderr or "")[-200:])
        import importlib
        importlib.invalidate_caches()
        _auto()
        return {"ok": True, "log": "\n".join(log)}
    except Exception as e:
        return {"ok": False, "error": str(e), "log": "\n".join(log)}


def start_install(user=None):
    with _LOCK:
        if JOB["running"]:
            return JOB
        JOB.update(running=True, status="Setting up computer control", error="", done=False, log="")

    def go():
        res = install()
        with _LOCK:
            JOB.update(running=False, done=bool(res.get("ok")), error="" if res.get("ok") else res.get("error", "failed"),
                       status="Ready" if res.get("ok") else "Setup failed", log=res.get("log", ""))
        if user:
            try:
                core.log(user["id"], "Computer control ready" if res.get("ok") else "Computer control setup failed",
                         "done" if res.get("ok") else "error")
            except Exception:
                pass
    threading.Thread(target=go, daemon=True).start()
    return JOB


def state():
    return {"available": available(), "ready": ready(), "job": dict(JOB)}


def _guard():
    if not available():
        raise DesktopError("Computer control works on Windows only.")
    if not ready():
        raise DesktopError("Computer control isn't set up yet. Open the Phone page and press "
                           "'Set up computer control'.")


# ---------- reading the screen ----------
def _center(ctrl):
    try:
        r = ctrl.BoundingRectangle
        if r.width() <= 0 or r.height() <= 0:
            return None
        return int((r.left + r.right) / 2), int((r.top + r.bottom) / 2)
    except Exception:
        return None


def _foreground(auto):
    """The window in front, or the desktop root as a fallback."""
    try:
        c = auto.GetForegroundControl()
        if c:
            top = c.GetTopLevelControl()
            return top or c
    except Exception:
        pass
    return auto.GetRootControl()


def screen(user, limit=80):
    """The things on the active window right now: text labels, buttons and fields, each with x,y.
    The brain reads this, then calls computer_click / computer_type with an item's text (or x,y)."""
    _guard()
    auto = _auto()
    top = _foreground(auto)
    title = ""
    try:
        title = (top.Name or "")[:120]
    except Exception:
        pass
    items, seen = [], set()
    # breadth-first over the control tree of the active window, bounded in breadth and depth
    stack, nvisited = [(top, 0)], 0
    while stack and len(items) < limit and nvisited < 1500:
        ctrl, d = stack.pop(0)
        nvisited += 1
        if d > 18:
            continue
        try:
            children = ctrl.GetChildren()
        except Exception:
            children = []
        for ch in children:
            stack.append((ch, d + 1))
        try:
            name = (ctrl.Name or "").strip()
            ctype = ctrl.ControlTypeName
            enabled = bool(getattr(ctrl, "IsEnabled", True))
            offscreen = bool(getattr(ctrl, "IsOffscreen", False))
        except Exception:
            continue
        if offscreen or not enabled:
            continue
        clickable = ctype in _CLICKABLE
        field = ctype in _EDITABLE
        if not name and not field:
            continue
        c = _center(ctrl)
        if not c:
            continue
        key = (name[:40], c[0] // 14, c[1] // 14)
        if key in seen:
            continue
        seen.add(key)
        label = name[:90] or f"(empty {ctype.replace('Control', '').lower()} field)"
        items.append({"text": label, "x": c[0], "y": c[1],
                      **({"button": True} if clickable else {}),
                      **({"field": True} if field else {})})
    return {"window": title, "items": items,
            "note": "Use computer_click with the text of an item (or its x and y). computer_type types into "
                    "the field in focus. Read the screen again after each step to see what changed."}


def shot(user):
    """Save a screenshot of the whole screen to the desktop cache and return its path (for a vision brain
    or for the owner to glance at). Best-effort."""
    _guard()
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"screen-{int(time.time())}.png"
    try:
        from PIL import ImageGrab
        ImageGrab.grab().save(out)
        return {"saved": str(out)}
    except Exception as e:
        return {"error": f"Could not take a screenshot ({type(e).__name__})."}


# ---------- acting ----------
def _find(auto, target, want_field=False):
    """The on-screen control whose name best matches `target` (exact before partial; clickable first)."""
    top = _foreground(auto)
    want = str(target or "").strip().lower()
    hits = []
    stack, nvisited = [top], 0
    while stack and nvisited < 1500:
        ctrl = stack.pop(0)
        nvisited += 1
        try:
            for ch in ctrl.GetChildren():
                stack.append(ch)
            name = (ctrl.Name or "").strip()
            ctype = ctrl.ControlTypeName
            if getattr(ctrl, "IsOffscreen", False) or not getattr(ctrl, "IsEnabled", True):
                continue
        except Exception:
            continue
        if not name:
            continue
        low = name.lower()
        clickable = ctype in _CLICKABLE
        field = ctype in _EDITABLE
        if want_field and not field:
            continue
        if low == want:
            hits.append((0, not clickable, len(name), ctrl))
        elif want and want in low:
            hits.append((1, not clickable, len(name), ctrl))
    if not hits:
        return None
    hits.sort(key=lambda t: (t[0], t[1], t[2]))
    return hits[0][3]


def click(user, target="", x=None, y=None, button="left", double=False):
    """Click an on-screen item by its text, or at an x,y position."""
    _guard()
    auto = _auto()
    if x is not None and y is not None and str(x).lstrip("-").isdigit() and str(y).lstrip("-").isdigit():
        try:
            if button == "right":
                auto.RightClick(int(x), int(y))
            elif double:
                auto.Click(int(x), int(y)); auto.Click(int(x), int(y))
            else:
                auto.Click(int(x), int(y))
            return {"clicked": [int(x), int(y)]}
        except Exception as e:
            return {"error": f"Could not click there ({type(e).__name__})."}
    if not str(target or "").strip():
        return {"error": "Say what to click: the text of a button, or an x and y."}
    ctrl = _find(auto, target)
    if not ctrl:
        return {"error": f"Nothing on screen says '{target}'. Read the screen first to see what's there."}
    c = _center(ctrl) or (None, None)
    try:
        if button == "right":
            ctrl.RightClick()
        elif double:
            ctrl.DoubleClick()
        else:
            ctrl.Click()
        return {"clicked": (ctrl.Name or "")[:60], "at": list(c) if c[0] is not None else None}
    except Exception as e:
        return {"error": f"Could not click '{target}' ({type(e).__name__})."}


def _escape_sendkeys(text):
    # uiautomation SendKeys treats { } ( ) + ^ % ~ # specially; wrap each literal in braces
    return re.sub(r"([{}()+^%~#])", r"{\1}", str(text or ""))


def type_text(user, text, enter=False):
    """Type text into whatever field is in focus. Set enter=True to press Enter afterwards."""
    _guard()
    auto = _auto()
    try:
        focus = auto.GetFocusedControl()
        if focus is not None and focus.ControlTypeName in _EDITABLE:
            try:
                vp = focus.GetValuePattern()
                vp.SetValue(str(text or ""))
                if enter:
                    auto.SendKeys("{Enter}")
                return {"typed": str(text)[:80], "into": (focus.Name or "")[:60]}
            except Exception:
                pass
        auto.SendKeys(_escape_sendkeys(text) + ("{Enter}" if enter else ""), waitTime=0.02)
        return {"typed": str(text)[:80]}
    except Exception as e:
        return {"error": f"Could not type ({type(e).__name__})."}


_KEYMAP = {"enter": "{Enter}", "return": "{Enter}", "tab": "{Tab}", "escape": "{Esc}", "esc": "{Esc}",
           "space": " ", "backspace": "{Back}", "delete": "{Delete}", "del": "{Delete}", "up": "{Up}",
           "down": "{Down}", "left": "{Left}", "right": "{Right}", "home": "{Home}", "end": "{End}",
           "pageup": "{PageUp}", "pagedown": "{PageDown}", "win": "{Win}",
           "copy": "{Ctrl}c", "paste": "{Ctrl}v", "cut": "{Ctrl}x", "selectall": "{Ctrl}a",
           "save": "{Ctrl}s", "find": "{Ctrl}f", "close": "{Alt}{F4}", "switch": "{Alt}{Tab}"}


def press(user, key):
    """Press a key or a named shortcut (enter, tab, esc, save, copy, paste, close, …)."""
    _guard()
    auto = _auto()
    k = str(key or "").strip().lower()
    seq = _KEYMAP.get(k)
    if not seq:
        # a plain combo like "ctrl+s" or a single key
        if "+" in k:
            parts = k.split("+")
            mods = "".join("{%s}" % p.capitalize() for p in parts[:-1] if p in ("ctrl", "alt", "shift", "win"))
            seq = mods + parts[-1]
        else:
            seq = _escape_sendkeys(k) if len(k) == 1 else "{%s}" % k.capitalize()
    try:
        auto.SendKeys(seq, waitTime=0.02)
        return {"pressed": key}
    except Exception as e:
        return {"error": f"Could not press '{key}' ({type(e).__name__})."}


def scroll(user, amount=-4, x=None, y=None):
    """Scroll the wheel: negative = down, positive = up (roughly in notches)."""
    _guard()
    auto = _auto()
    try:
        if x is not None and y is not None:
            auto.SetCursorPos(int(x), int(y))
        auto.WheelDown(abs(int(amount))) if int(amount) < 0 else auto.WheelUp(int(amount))
        return {"scrolled": int(amount)}
    except Exception as e:
        return {"error": f"Could not scroll ({type(e).__name__})."}


# ---------- apps and windows ----------
def open_app(user, app):
    """Open an app by name (notepad, excel, chrome…), or a file/URL path. Falls back to the Start menu search."""
    _guard()
    name = str(app or "").strip()
    if not name:
        return {"error": "Say which app or file to open."}
    # a path, exe or URL -> open it directly
    if re.search(r"[\\/]", name) or name.lower().endswith(".exe") or re.match(r"^[a-z]+://", name, re.I):
        try:
            os.startfile(name)
            return {"opened": name}
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
                return {"opened": name}
            except Exception as e:
                return {"error": f"Could not open {name} ({type(e).__name__})."}
    # otherwise try it as a command, then fall back to Start-menu search
    try:
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        time.sleep(1.0)
        return {"opened": name}
    except Exception:
        pass
    try:
        auto = _auto()
        auto.SendKeys("{Win}", waitTime=0.1)
        time.sleep(0.6)
        auto.SendKeys(_escape_sendkeys(name), waitTime=0.02)
        time.sleep(0.8)
        auto.SendKeys("{Enter}")
        return {"opened": name, "via": "start menu search"}
    except Exception as e:
        return {"error": f"Could not open {name} ({type(e).__name__})."}


def list_windows(user):
    """The titles of the open top-level windows."""
    _guard()
    auto = _auto()
    out = []
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                if w.ControlTypeName == "WindowControl" and (w.Name or "").strip() and not getattr(w, "IsOffscreen", False):
                    out.append((w.Name or "").strip()[:100])
            except Exception:
                continue
    except Exception as e:
        return {"error": f"Could not list windows ({type(e).__name__})."}
    # de-dup, keep order
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return {"windows": uniq[:40]}


def focus_window(user, title):
    """Bring a window to the front by (part of) its title."""
    _guard()
    auto = _auto()
    want = str(title or "").strip().lower()
    if not want:
        return {"error": "Say which window to focus."}
    try:
        root = auto.GetRootControl()
        best = None
        for w in root.GetChildren():
            try:
                if w.ControlTypeName != "WindowControl":
                    continue
                nm = (w.Name or "").strip()
                if not nm:
                    continue
                if nm.lower() == want:
                    best = w; break
                if want in nm.lower() and best is None:
                    best = w
            except Exception:
                continue
        if not best:
            return {"error": f"No open window matches '{title}'. Use list_windows to see what's open."}
        try:
            best.SetActive()
        except Exception:
            best.SetTopmost(True); best.SetTopmost(False)
        return {"focused": (best.Name or "")[:80]}
    except Exception as e:
        return {"error": f"Could not focus '{title}' ({type(e).__name__})."}
