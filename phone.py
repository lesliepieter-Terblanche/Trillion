"""Trillion phone control: each login links its own Android phone over ADB (USB debugging).

Works over the USB cable, or wirelessly (for example over Tailscale) once switched to wireless mode.
Nothing here can get past the phone's lock screen: screen actions need the phone unlocked.
"""
import glob, os, re, shlex, shutil, subprocess, time
from urllib.parse import quote

import core

APPS = {
    "whatsapp": "com.whatsapp", "whatsapp business": "com.whatsapp.w4b", "chrome": "com.android.chrome",
    "youtube": "com.google.android.youtube", "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps", "google maps": "com.google.android.apps.maps",
    "settings": "com.android.settings", "facebook": "com.facebook.katana", "instagram": "com.instagram.android",
    "tiktok": "com.zhiliaoapp.musically", "telegram": "org.telegram.messenger", "spotify": "com.spotify.music",
    "camera": "com.sec.android.app.camera", "gallery": "com.sec.android.gallery3d",
    "messages": "com.samsung.android.messaging", "phone": "com.samsung.android.dialer",
    "calculator": "com.sec.android.app.popupcalculator", "play store": "com.android.vending",
    "tailscale": "com.tailscale.ipn", "calendar": "com.samsung.android.calendar", "clock": "com.sec.android.app.clockpackage",
}


class PhoneError(Exception):
    pass


def find_adb():
    p = shutil.which("adb")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    for pat in (r"Microsoft\WinGet\Links\adb.exe", r"Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe",
                r"Android\Sdk\platform-tools\adb.exe"):
        hits = glob.glob(os.path.join(local, pat))
        if hits:
            return hits[0]
    raise PhoneError("The phone tool (ADB) is not installed. Run '1 - Setup.bat' again, then restart Trillion.")


def _run(args, timeout=25):
    try:
        r = subprocess.run([find_adb()] + list(args), capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise PhoneError("The phone did not answer in time.")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def devices():
    """Phones this PC can see right now."""
    _, out = _run(["devices", "-l"], timeout=15)
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "offline"):
            m = re.search(r"model:(\S+)", line)
            found.append({"serial": parts[0], "state": parts[1], "model": (m.group(1) if m else "").replace("_", " ")})
    return found


def linked_serial(user):
    s = core.settings(user).get("phone_serial")
    if not s:
        raise PhoneError("No phone is linked to this login yet. Link one on the Phone page.")
    return s


def owner_of(serial):
    for u in core.q("SELECT * FROM users"):
        if core.settings(u).get("phone_serial") == serial:
            return u
    return None


_ADB_ERR = re.compile(r"^(?:adb(?:\.exe)?|error)\s*:\s*(.*)$", re.I | re.M)
UNREACHABLE = ("The phone is not reachable. Plug it in, or check it is on the same Tailscale network with wireless debugging on.")


def _ready(serial, seen):
    return any(d["serial"] == serial and d["state"] == "device" for d in seen)


def working_serial(user):
    """The address this login's phone answers on right now. The same phone shows up under a different name on
    the cable and over the network, so if the saved one has gone quiet the other one is tried before giving up."""
    s = core.settings(user)
    serial = s.get("phone_serial")
    if not serial:
        raise PhoneError("No phone is linked to this login yet. Link one on the Phone page.")
    seen = devices()
    if _ready(serial, seen):
        return serial
    known = [x for x in dict.fromkeys([serial, s.get("phone_wifi"), s.get("phone_usb")]) if x]
    for x in known:
        if ":" in x:
            _run(["connect", x], timeout=12)
    seen = devices()
    for x in known:
        if _ready(x, seen):
            return x
    # an older link only remembers one name: accept the same model if it is the only free phone in sight
    model = (s.get("phone_model") or "").lower()
    free = [d for d in seen if d["state"] == "device" and model and d["model"].lower() == model
            and (owner_of(d["serial"]) or user)["id"] == user["id"]]
    if len(free) == 1:
        return free[0]["serial"]
    if any(d["serial"] in known and d["state"] == "unauthorized" for d in seen):
        raise PhoneError("The phone has not allowed this PC. Unlock it and tap Allow on the USB debugging prompt.")
    raise PhoneError(UNREACHABLE)


def sh(user, command, timeout=25):
    """Run a command on this login's own phone."""
    serial = working_serial(user)
    code, out = _run(["-s", serial, "shell", command], timeout=timeout)
    if code != 0:
        # only adb's own complaint counts; the same words inside the phone's answer are just text
        for m in _ADB_ERR.finditer(out):
            low = m.group(1).lower()
            if "unauthorized" in low:
                raise PhoneError("The phone has not allowed this PC. Unlock it and tap Allow on the USB debugging prompt.")
            if re.search(r"device .*not found|offline|no devices|failed to connect|^closed|connection reset", low):
                raise PhoneError(UNREACHABLE)
    return out


_LOCK_RE = re.compile(r"(mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen)=true")


def is_locked(user):
    out = sh(user, "dumpsys power | grep -E 'mWakefulness=' ; dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen'")
    if "mWakefulness=Awake" not in out:
        sh(user, "input keyevent KEYCODE_WAKEUP")
        time.sleep(0.8)
        out = sh(user, "dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen'")
    return bool(_LOCK_RE.search(out))


def unlock(user, explicit=False):
    """Unlock with the code this login saved. One try only: a wrong code is never repeated, so the phone cannot be
    locked out by her. After a failed try the automatic unlock stays off until the code is saved again."""
    if not is_locked(user):
        return {"unlocked": True, "was_locked": False}
    st = core.settings(user)
    saved = core.get_secret(user["id"], "phone_unlock")
    if not saved:
        raise PhoneError("The phone is locked" + (" and no unlock code is saved. Save one on the Phone page, or unlock it yourself."
                                                  if explicit else ". Unlock it and ask me again, or save an unlock code on the Phone page."))
    if st.get("phone_unlock_failed"):
        raise PhoneError("The phone is locked. The saved unlock code did not work last time, so I will not try it again. "
                         "Unlock it yourself, then save the code again on the Phone page.")
    if not explicit and not st.get("phone_auto_unlock"):
        raise PhoneError("The phone is locked. Say 'unlock my phone', or tick 'Unlock by herself' on the Phone page.")
    kind, _, code = saved.partition(":")
    m = re.search(r"(\d+)x(\d+)", sh(user, "wm size"))
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 2340)
    sh(user, f"input swipe {w // 2} {int(h * 0.80)} {w // 2} {int(h * 0.30)} 220")     # lift the lock screen to the code pad
    time.sleep(1.0)
    if kind == "pattern":
        grid = next((n for n in _nodes(user) if "pattern" in n["id"].lower() and n.get("box")), None)
        if not grid:
            raise PhoneError("I could not find the pattern grid on the lock screen. Unlock it yourself this time.")
        x1, y1, x2, y2 = grid["box"]
        cw, ch = (x2 - x1) / 3, (y2 - y1) / 3
        pts = [(int(x1 + cw * ((int(d) - 1) % 3 + 0.5)), int(y1 + ch * ((int(d) - 1) // 3 + 0.5))) for d in code]
        cmds = [f"input motionevent DOWN {pts[0][0]} {pts[0][1]}"]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            for i in range(1, 5):          # move in small steps so every dot on the way is touched
                cmds.append(f"input motionevent MOVE {int(ax + (bx - ax) * i / 4)} {int(ay + (by - ay) * i / 4)}")
        cmds.append(f"input motionevent UP {pts[-1][0]} {pts[-1][1]}")
        sh(user, "; ".join(cmds), timeout=40)
    else:
        quoted = code.replace("'", "'\\''").replace(" ", "%s")
        sh(user, f"input text '{quoted}'")
        time.sleep(0.4)
        sh(user, "input keyevent KEYCODE_ENTER")
    time.sleep(1.4)
    out = sh(user, "dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen'")
    if _LOCK_RE.search(out):
        st["phone_unlock_failed"] = True
        core.save_settings(user["id"], st)
        raise PhoneError("The saved unlock code did not open the phone. I tried once and will not try again, so the phone cannot "
                         "lock itself out. Unlock it yourself, then save the code again on the Phone page.")
    return {"unlocked": True, "was_locked": True}


def _awake_and_unlocked(user):
    unlock(user)


def status(user):
    out = sh(user, "getprop ro.product.model; echo @@; getprop ro.build.version.release; echo @@; dumpsys battery; echo @@; "
                   "df /data | tail -1; echo @@; dumpsys power | grep mWakefulness=; echo @@; dumpsys wifi | grep -m1 'mWifiInfo'")
    p = [x.strip() for x in out.split("@@")] + [""] * 6
    res = {"model": p[0], "android": p[1]}
    lvl, st, temp = (re.search(r"level: (\d+)", p[2]), re.search(r"status: (\d+)", p[2]), re.search(r"temperature: (\d+)", p[2]))
    if lvl:
        res["battery_percent"] = int(lvl.group(1))
        res["charging"] = bool(st and st.group(1) in ("2", "5"))
    if temp:
        res["battery_temp_c"] = int(temp.group(1)) / 10
    d = p[3].split()
    if len(d) >= 4 and d[1].isdigit():
        res["storage_free_gb"] = round(int(d[3]) / 1048576, 1)
        res["storage_total_gb"] = round(int(d[1]) / 1048576, 1)
    res["screen"] = "on" if "Awake" in p[4] else "off"
    w = re.search(r'SSID: "?([^",]+)"?,', p[5])
    if w and "unknown" not in w.group(1).lower():
        res["wifi"] = w.group(1)
    return res


def packages(user):
    return [ln.split(":", 1)[1].strip() for ln in sh(user, "pm list packages").splitlines() if ln.startswith("package:")]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def third_party(user):
    """Packages the user installed (not the built-in Android ones)."""
    out = sh(user, "pm list packages -3")
    return [ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:")]


def foreground_app(user):
    """The package of the app currently open on the phone's screen."""
    for cmd in ("dumpsys activity activities | grep -E 'mResumedActivity|ResumedActivity' | head -n 3",
                "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp' | head -n 3",
                "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' | head -n 3"):
        try:
            out = sh(user, cmd, timeout=25)
        except PhoneError:
            out = ""
        for m in re.finditer(r"\b([a-zA-Z][\w.]+)/[\w.$]+", out):
            pkg = m.group(1)
            if "." in pkg and not pkg.startswith(("com.android.systemui", "com.android.launcher")):
                return pkg
    raise PhoneError("I could not tell which app is open. Open the app you want and try again.")


def resolve_app(user, name):
    key = re.sub(r"\s+", " ", str(name).lower().strip())
    pkgs = packages(user)
    if key in pkgs:                                  # an exact package name was given
        return key
    if APPS.get(key) in pkgs:
        return APPS[key]
    q = _norm(key)
    if not q:
        raise PhoneError("Say which app to use.")
    hits = [p for p in pkgs if q in _norm(p)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        best = [p for p in hits if _norm(p).endswith(q) or p.lower().rsplit(".", 1)[-1] == key.replace(" ", "")]
        if len(best) == 1:
            return best[0]
        raise PhoneError(f"More than one app matches '{name}': {', '.join(hits[:6])}. "
                         "Say which one, or give the exact package name.")
    toks = [t for t in re.split(r"[^a-z0-9]+", key) if len(t) >= 2]
    try:
        pool = third_party(user) or pkgs
    except PhoneError:
        pool = pkgs
    loose = [p for p in pool if any(t in _norm(p) for t in toks)][:10]
    hint = (" Closest installed: " + ", ".join(loose) + "." if loose else "")
    raise PhoneError(f"I could not find an app called '{name}' on the phone. Open the app and say "
                     f"'reverse engineer the app on my screen', or give the exact package name.{hint}")


def open_app(user, name):
    pkg = resolve_app(user, name)
    _awake_and_unlocked(user)
    out = sh(user, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    if "No activities found" in out:
        raise PhoneError(f"{name} is installed but cannot be opened this way.")
    return {"opened": name, "package": pkg}


CONTACT_QUERIES = (
    "content query --uri content://com.android.contacts/data/phones --projection display_name:data1",
    "content query --uri content://com.android.contacts/data --projection display_name:data1:mimetype "
    "--where \"mimetype='vnd.android.cursor.item/phone_v2'\"",
    "content query --uri content://com.android.contacts/data/phones",
)
LAST_CONTACTS = {"rows": 0, "raw": ""}


def all_contacts(user):
    """Every saved contact with its numbers. Phones differ in how they answer, so a few ways of asking are tried."""
    raw = ""
    for cmd in CONTACT_QUERIES:
        try:
            out = sh(user, cmd, timeout=60)
        except PhoneError:
            continue
        raw = raw or out
        found = {}
        for ln in out.splitlines():
            n = re.search(r"\bdisplay_name=(.*?)(?:, \w+=|$)", ln)
            d = re.search(r"\bdata1=(.*?)(?:, \w+=|$)", ln)
            if not n or not d:
                continue
            name, num = n.group(1).strip(), d.group(1).strip()
            if not name or name == "NULL" or not re.search(r"\d{5}", re.sub(r"\D", "", num)):
                continue
            found.setdefault(name, [])
            if num not in found[name]:
                found[name].append(num)
        if found:
            LAST_CONTACTS.update(rows=len(found), raw="")
            return found
    LAST_CONTACTS.update(rows=0, raw=" ".join(raw.split())[:220])
    return {}


def contacts(user, name):
    want = name.lower().strip()
    return {k: v for k, v in all_contacts(user).items() if want in k.lower()}


def normalise(number, country="27"):
    n = re.sub(r"[^\d+]", "", number)
    if n.startswith("+"):
        return n[1:]
    if n.startswith("00"):
        return n[2:]
    if n.startswith("0"):
        return country + n[1:]
    if len(n) == 9:                      # saved without the leading 0
        return country + n
    return n


ALTERNATES = {}   # first-choice number -> the contact's other numbers, tried if WhatsApp does not know the first


def _best_first(nums, country):
    """Cell numbers before landlines: WhatsApp lives on the cell number."""
    seen, out = set(), []
    for x in nums:
        n = normalise(x, country)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    cell = re.compile(r"^%s[678]" % re.escape(country)) if country == "27" else re.compile(r"^")
    return sorted(out, key=lambda n: not cell.match(n))


def resolve_contact(user, contact):
    """A phone number, or exactly one contact by name. Never guesses between several people."""
    country = str(core.settings(user).get("phone_country_code", "27"))
    if re.fullmatch(r"[\d\s+()-]{7,}", contact.strip()):
        return contact.strip(), normalise(contact, country)
    if contact.startswith("chat:"):
        return contact[5:], contact
    import difflib
    everyone = all_contacts(user)
    want = re.sub(r"\s+", " ", contact.lower().strip())
    found = {k: v for k, v in everyone.items() if want in k.lower()}
    exact = {k: v for k, v in found.items() if k.lower() == want}
    if exact:
        found = exact
    if not found:
        # spoken and typed names are often spelled a little differently from the contact (Llewellen / Llewellyn)
        scored = sorted(((difflib.SequenceMatcher(None, want, k.lower()).ratio(), k) for k in everyone), reverse=True)
        close = [k for r, k in scored if r >= 0.8]
        if not close and " " not in want:      # one name given: compare with each word of the contact names
            close = [k for k in everyone if any(difflib.SequenceMatcher(None, want, w).ratio() >= 0.8 for w in k.lower().split())]
        if close and (len(close) == 1 or scored[0][1] == close[0] and scored[0][0] - scored[1][0] >= 0.1):
            found = {close[0]: everyone[close[0]]}
        elif len(close) > 1:
            raise PhoneError("I am not sure which contact you mean: " + "; ".join(close[:8]) + ". Say exactly which one.")
    if not found:
        # not in the contact list I can read: look the name up inside WhatsApp itself when it is time to send
        return contact.strip(), "chat:" + contact.strip()
    if len(found) > 1:
        raise PhoneError("More than one contact matches: " + "; ".join(list(found)[:8]) + ". Say exactly which one.")
    name, nums = next(iter(found.items()))
    order = _best_first(nums, country)
    ALTERNATES[order[0]] = (name, order[1:])
    return name, order[0]


NOT_ON_WA = re.compile(r"(isn\'t|is not|isn&apos;t|isn’t) on WhatsApp|Invite to WhatsApp|text=\"Invite\"|SMS invite|not on WhatsApp", re.I)


def _tap_send(user, pkg):
    """True when Send was pressed, "invite" when WhatsApp says the number has no WhatsApp, False otherwise."""
    for _ in range(5):
        time.sleep(1.6)
        xml = sh(user, "uiautomator dump /sdcard/trillion_ui.xml >/dev/null 2>&1; cat /sdcard/trillion_ui.xml", timeout=30)
        m = re.search(r'resource-id="%s:id/send"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(pkg), xml) or \
            re.search(r'content-desc="Send"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            sh(user, f"input tap {(x1 + x2) // 2} {(y1 + y2) // 2}")
            return True
        if NOT_ON_WA.search(xml):
            sh(user, "input keyevent KEYCODE_BACK")
            return "invite"
    return False


def _find(nodes, ids=(), label=None):
    for n in nodes:
        if n["id"] in ids or (label and re.search(label, n["label"], re.I)):
            return n
    return None


def _open_chat_by_search(user, pkg, name):
    """Open a WhatsApp chat by looking the name up in WhatsApp's own search. Returns the chat's real name."""
    import difflib
    want = re.sub(r"\s+", " ", name.lower().strip())
    if any(ord(c) > 126 for c in want):
        raise PhoneError("I can only search WhatsApp for names in plain letters.")
    sh(user, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    box = None
    for _ in range(4):
        time.sleep(1.5)
        nodes = _nodes(user)
        box = _find(nodes, ("my_search_bar", "search_bar", "menuitem_search", "search_fragment", "search_src_text", "search_input"),
                    r"^(search|ask meta ai or search)\b")
        if box:
            break
        sh(user, "input keyevent KEYCODE_BACK")     # WhatsApp was left inside a chat: step back to the list
    if not box:
        raise PhoneError("I opened WhatsApp but could not find its search box.")
    sh(user, f"input tap {box['x']} {box['y']}")
    time.sleep(1.0)
    typed = want.split()[0][:4]                    # a few letters, so a spelling slip later in the name still finds it
    sh(user, f"input text '{typed}'")
    best = []
    for _ in range(3):
        time.sleep(1.6)
        rows = [n for n in _nodes(user) if n["label"] and not n["field"]
                and n["id"] in ("conversations_row_contact_name", "contactpicker_row_name", "chat_able_contacts_row_name", "name")]
        scored = {}
        for n in rows:
            lab = n["label"].lower()
            r = difflib.SequenceMatcher(None, want, lab).ratio()
            if " " not in want:
                r = max([r] + [difflib.SequenceMatcher(None, want, w).ratio() for w in lab.split()])
            if r >= 0.8:
                scored.setdefault(n["label"], (r, n))
        best = sorted(scored.values(), key=lambda t: -t[0])
        if best:
            break
    if not best:
        hint = (" I also could not read the phone's contact list (it answered: " + LAST_CONTACTS["raw"][:120] + ")."
                if not LAST_CONTACTS["rows"] and LAST_CONTACTS["raw"] else "")
        raise PhoneError(f"I could not find '{name}' in your contacts or in WhatsApp." + hint)
    if len(best) > 1 and best[0][0] - best[1][0] < 0.1:
        raise PhoneError("More than one WhatsApp chat matches: " + "; ".join(t[1]["label"] for t in best[:6]) + ". Say exactly which one.")
    n = best[0][1]
    sh(user, f"input tap {n['x']} {n['y']}")
    return n["label"]


def _send_in_chat(user, pkg, name, message):
    """Find the person in WhatsApp's own search, type the message there and press Send."""
    if any(ord(c) > 126 for c in message):
        raise PhoneError("For this contact I can only type plain letters, numbers and common symbols. Leave out emoji and accents.")
    name = _open_chat_by_search(user, pkg, name)
    entry = None
    for _ in range(4):
        time.sleep(1.5)
        entry = _find(_nodes(user), ("entry",))
        if entry:
            break
    if not entry:
        raise PhoneError(f"I opened WhatsApp for {name} but could not find the message box.")
    sh(user, f"input tap {entry['x']} {entry['y']}")
    time.sleep(0.6)
    quoted = message.replace("'", "'\\''").replace(" ", "%s")
    sh(user, f"input text '{quoted}'")
    return name, _tap_send(user, pkg)


# ---------- sending files with a WhatsApp ----------
PHONE_DROP = "/storage/emulated/0/Download/Trillion"     # where a file is put on the phone before WhatsApp picks it up
WA_FILE_MAX = 2 * 1024 ** 3                              # WhatsApp's own ceiling for a document


def _phone_name(path, taken):
    """A file name the phone's command line cannot trip over: plain letters, numbers, dot, dash, underscore."""
    stem, ext = os.path.splitext(os.path.basename(str(path)))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "file"
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10]
    name, n = stem[:80] + ext, 2
    while name.lower() in taken:
        name = f"{stem[:80]}_{n}{ext}"
        n += 1
    taken.add(name.lower())
    return name


def _mime(path):
    import mimetypes
    ext = os.path.splitext(str(path))[1].lower()
    known = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
             ".mp4": "video/mp4", ".mov": "video/quicktime", ".3gp": "video/3gpp", ".mkv": "video/x-matroska", ".webm": "video/webm",
             ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/x-wav",
             ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv", ".zip": "application/zip",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
             ".doc": "application/msword", ".xls": "application/vnd.ms-excel", ".ppt": "application/vnd.ms-powerpoint"}
    return known.get(ext) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _media_id(user, remote):
    out = sh(user, f"content query --uri content://media/external/file --projection _id --where \"_data='{remote}'\"")
    m = re.search(r"\b_id=(\d+)", out)
    return m.group(1) if m else None


def push_file(user, path, taken=None):
    """Copy one file from this PC to the phone and return (the address WhatsApp can open it by, its type, its name)."""
    path = str(path)
    if not os.path.isfile(path):
        raise PhoneError(f"I cannot find the file {os.path.basename(path)} on the PC any more.")
    size = os.path.getsize(path)
    if size > WA_FILE_MAX:
        raise PhoneError(f"{os.path.basename(path)} is bigger than 2 GB, which is the most WhatsApp accepts.")
    name = _phone_name(path, taken if taken is not None else set())
    remote = f"{PHONE_DROP}/{name}"
    serial = working_serial(user)
    sh(user, f"mkdir -p {PHONE_DROP}")
    code, out = _run(["-s", serial, "push", path, remote], timeout=90 + size // 400_000)     # allows for a slow wireless link
    if code != 0 or re.search(r"^adb: error|failed to copy|No such file|Permission denied", out, re.I | re.M):
        raise PhoneError(f"I could not copy {os.path.basename(path)} to the phone: " + out.strip()[-160:])
    # the phone's media index must know the file before another app is allowed to open it
    sh(user, f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://{remote}' >/dev/null 2>&1")
    mid = None
    for attempt in range(6):
        mid = _media_id(user, remote)
        if mid:
            break
        if attempt == 1:
            sh(user, f"content call --uri content://media --method scan_file --arg '{remote}' >/dev/null 2>&1")
        if attempt == 3:
            sh(user, "content call --uri content://media --method scan_volume --arg external_primary >/dev/null 2>&1", timeout=60)
        time.sleep(1.2)
    if not mid:
        raise PhoneError(f"{os.path.basename(path)} is on the phone (Download > Trillion) but the phone has not listed it yet, "
                         "so WhatsApp cannot be handed it. Try again in a moment.")
    return f"content://media/external/file/{mid}", _mime(path), name


_BOUNDS = r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'


def _tap_send_file(user, pkg):
    """Press Send on WhatsApp's file screen. True when pressed, "picker" when WhatsApp is asking who to send to,
    "invite" when the number has no WhatsApp, False when no Send button showed up."""
    for _ in range(6):
        time.sleep(1.7)
        xml = sh(user, "uiautomator dump /sdcard/trillion_ui.xml >/dev/null 2>&1; cat /sdcard/trillion_ui.xml", timeout=30)
        m = (re.search(r'resource-id="%s:id/send"[^>]*%s' % (re.escape(pkg), _BOUNDS), xml)
             or re.search(r'content-desc="Send"[^>]*' + _BOUNDS, xml)
             or re.search(r'text="(?:Send|SEND)"[^>]*resource-id="android:id/button1"[^>]*' + _BOUNDS, xml)
             or re.search(r'<node[^>]*text="(?:Send|SEND)"[^>]*class="android\.widget\.Button"[^>]*' + _BOUNDS, xml))
        if m:
            x1, y1, x2, y2 = map(int, m.groups()[-4:])
            sh(user, f"input tap {(x1 + x2) // 2} {(y1 + y2) // 2}")
            return True
        if NOT_ON_WA.search(xml):
            sh(user, "input keyevent KEYCODE_BACK")
            return "invite"
        if re.search(r'resource-id="%s:id/contactpicker[^"]*"|text="Send to(?:\.\.\.|…)?"' % re.escape(pkg), xml):
            return "picker"
    return False


def _pick_in_share_list(user, pkg, name):
    """WhatsApp is showing its "Send to" list with the file waiting: find the person there and choose them."""
    import difflib
    want = re.sub(r"\s+", " ", name.lower().strip())
    if not want or any(ord(c) > 126 for c in want):
        raise PhoneError("I can only search WhatsApp for names in plain letters.")
    box = _find(_nodes(user), ("menuitem_search", "search_src_text", "search_input", "my_search_bar", "search_bar"), r"^search\b")
    if not box:
        raise PhoneError("WhatsApp is asking who to send the file to, and I could not find its search box. Pick the person on the phone.")
    sh(user, f"input tap {box['x']} {box['y']}")
    time.sleep(1.0)
    sh(user, f"input text '{want.split()[0][:4]}'")
    best = []
    for _ in range(3):
        time.sleep(1.6)
        scored = {}
        for n in _nodes(user):
            if not n["label"] or n["field"] or n["id"] not in ("contactpicker_row_name", "chat_able_contacts_row_name", "conversations_row_contact_name", "name"):
                continue
            lab = n["label"].lower()
            r = difflib.SequenceMatcher(None, want, lab).ratio()
            if " " not in want:
                r = max([r] + [difflib.SequenceMatcher(None, want, w).ratio() for w in lab.split()])
            if r >= 0.8:
                scored.setdefault(n["label"], (r, n))
        best = sorted(scored.values(), key=lambda t: -t[0])
        if best:
            break
    if not best:
        raise PhoneError(f"I could not find '{name}' in WhatsApp's send-to list. The file is waiting on the phone: pick the person there.")
    if len(best) > 1 and best[0][0] - best[1][0] < 0.1:
        raise PhoneError("More than one WhatsApp chat matches: " + "; ".join(t[1]["label"] for t in best[:6]) + ". Say exactly which one.")
    n = best[0][1]
    sh(user, f"input tap {n['x']} {n['y']}")
    time.sleep(0.8)
    arrow = _find(_nodes(user), ("send",), r"^send$")          # the green arrow that confirms the chosen person
    if not arrow:
        raise PhoneError(f"I chose {n['label']} in WhatsApp but could not find the arrow to go on. Finish it on the phone.")
    sh(user, f"input tap {arrow['x']} {arrow['y']}")
    return n["label"]


def _send_file(user, pkg, uri, mime, number, name):
    """Hand one file on the phone to WhatsApp for one person and press Send. Returns (pressed, the name it went to)."""
    jid = "" if number.startswith("chat:") else f" --es jid '{number}@s.whatsapp.net'"
    out = sh(user, f"am start -a android.intent.action.SEND -t '{mime}' --eu android.intent.extra.STREAM '{uri}'{jid} "
                   f"--grant-read-uri-permission -p {pkg}")
    if "Error" in out:
        raise PhoneError("WhatsApp would not take the file: " + out.strip()[-160:])
    pressed = _tap_send_file(user, pkg)
    if pressed == "picker":
        if not name or re.fullmatch(r"[\d\s+()-]{7,}", name):
            raise PhoneError("WhatsApp is asking who to send the file to and I only have a number, not a name to look for. "
                             "The file is waiting on the phone: pick the person there.")
        name = _pick_in_share_list(user, pkg, name)
        pressed = _tap_send_file(user, pkg)
        if pressed == "picker":
            pressed = False
    return pressed, name


def send_whatsapp(user, contact, message, files=None):
    """Send a WhatsApp. files is a list of file paths on this PC that go along: the message first, then each file."""
    files = [str(f) for f in (files or [])]
    message = (message or "").strip()
    if not message and not files:
        raise PhoneError("There is no message to send.")
    for f in files:
        if not os.path.isfile(f):
            raise PhoneError(f"I cannot find the file {os.path.basename(f)} on the PC any more.")
        if os.path.getsize(f) > WA_FILE_MAX:
            raise PhoneError(f"{os.path.basename(f)} is bigger than 2 GB, which is the most WhatsApp accepts.")
    name, number = resolve_contact(user, contact)
    pkgs = packages(user)
    pkg = "com.whatsapp" if "com.whatsapp" in pkgs else ("com.whatsapp.w4b" if "com.whatsapp.w4b" in pkgs else None)
    if not pkg:
        raise PhoneError("WhatsApp is not installed on the phone.")
    _awake_and_unlocked(user)
    res = {"status": "send_pressed", "to": name, "number": "found in WhatsApp" if number.startswith("chat:") else "+" + number,
           "note": "I pressed Send. I cannot confirm delivery from here."}
    if message:
        res = _send_text(user, pkg, name, number, message)
        if res["status"] != "send_pressed" and files:
            res["files_sent"], res["files_left"] = [], [os.path.basename(f) for f in files]
            res["note"] += " The file" + ("s were" if len(files) > 1 else " was") + " not sent."
            return res
    if not files:
        return res
    if not number.startswith("chat:") and res.get("number", "").startswith("+"):
        number = res["number"][1:]                 # the number WhatsApp accepted for the message
    elif not number.startswith("chat:") and message:
        number = "chat:" + str(res.get("to") or name)      # WhatsApp did not know the number: the chat was found by name
    who = ALTERNATES.get(number, (None, []))[0] or res.get("to") or name
    taken, sent = set(), []
    for i, f in enumerate(files):
        uri, mime, _ = push_file(user, f, taken)
        pressed, who2 = _send_file(user, pkg, uri, mime, number, who)
        if pressed == "invite":
            raise PhoneError(f"WhatsApp says {who} is not on WhatsApp. " + ("The message went, the file did not." if message else "Nothing was sent."))
        if not pressed:
            res.update(status="typed_not_sent", files_sent=sent, files_left=[os.path.basename(x) for x in files[i:]],
                       note=f"WhatsApp is open with {os.path.basename(f)} ready, but I could not press Send. Press it on the phone.")
            return res
        who = who2 or who
        sent.append(os.path.basename(f))
        time.sleep(1.5)                            # let WhatsApp return to the chat before the next file
    res.update(status="send_pressed", files_sent=sent, files_left=[],
               note="I pressed Send for the message and each file. I cannot confirm delivery from here." if message
               else "I pressed Send for each file. I cannot confirm delivery from here.")
    if number.startswith("chat:"):
        res["to"] = who
    return res


def _send_text(user, pkg, name, number, message):
    if number.startswith("chat:"):
        name, pressed = _send_in_chat(user, pkg, name, message)
        shown = "found in WhatsApp"
    else:
        saved_name, others = ALTERNATES.get(number, (None, []))
        pressed, shown = False, "+" + number
        for num in [number] + list(others):
            url = f"https://wa.me/{num}?text={quote(message, safe='')}"
            out = sh(user, f"am start -a android.intent.action.VIEW -d '{url}' {pkg}")
            if "Error" in out:
                raise PhoneError("WhatsApp would not open that chat: " + out.strip()[-160:])
            pressed, shown = _tap_send(user, pkg), "+" + num
            if pressed != "invite":
                break
        if pressed == "invite":
            # WhatsApp does not know the saved number(s) in that form: open the existing chat by name instead
            if not saved_name:
                raise PhoneError(f"WhatsApp says {shown} is not on WhatsApp. Nothing was sent.")
            name, pressed = _send_in_chat(user, pkg, saved_name, message)
            shown = "found in WhatsApp"
    if pressed == "invite":
        raise PhoneError(f"WhatsApp says {name} is not on WhatsApp. Nothing was sent.")
    if not pressed:
        return {"status": "typed_not_sent", "to": name, "number": shown,
                "note": "The chat is open with the message typed, but I could not press Send. Press it on the phone."}
    return {"status": "send_pressed", "to": name, "number": shown,
            "note": "I pressed Send. I cannot confirm delivery from here."}


def locate(user):
    out = sh(user, "dumpsys location | grep -E 'Location\\[' | head -40", timeout=30)
    best = None
    for prov in ("fused", "gps", "network"):
        m = re.search(r"Location\[%s (-?\d+\.\d+),(-?\d+\.\d+)(?:.*?hAcc=([\d.]+))?" % prov, out)
        if m:
            best = (prov, m)
            break
    if not best:
        raise PhoneError("The phone has no recent location. Check that Location is switched on.")
    prov, m = best
    lat, lon = float(m.group(1)), float(m.group(2))
    res = {"latitude": lat, "longitude": lon, "source": prov, "map": f"https://www.google.com/maps?q={lat},{lon}",
           "note": "This is the phone's last known position, not necessarily where it is this second."}
    if m.group(3):
        res["accuracy_m"] = round(float(m.group(3)))
    return res


def ring(user, message="Trillion: find my phone"):
    """Ring through the phone's own clock: alarm volume to full, then a one-second timer.
    The timer alarm sounds until someone dismisses it, and does not depend on a music app."""
    sh(user, "cmd media_session volume --stream 4 --set 15; cmd media_session volume --stream 3 --set 15; input keyevent KEYCODE_WAKEUP")
    out = sh(user, "am start -a android.intent.action.SET_TIMER --ei android.intent.extra.alarm.LENGTH 1 "
                   "--ez android.intent.extra.alarm.SKIP_UI true --es android.intent.extra.alarm.MESSAGE "
                   + shlex.quote(re.sub(r"\s+", " ", message)[:60]))
    if "Error" in out or "unable to resolve" in out.lower():
        sh(user, "cmd notification post -S bigtext -t 'Trillion' find_phone 'Find my phone: here I am'")
        return {"status": "notification_only", "note": "The phone's clock would not take a timer, so I sent a notification "
                "sound instead. It is quieter than an alarm."}
    return {"status": "ringing", "note": "Your phone's timer alarm is ringing at full volume. Dismiss it on the phone to stop it."}


# ---------- full control: read the screen, tap, type, press, swipe ----------
KEYS = {"back": "KEYCODE_BACK", "home": "KEYCODE_HOME", "recents": "KEYCODE_APP_SWITCH", "recent apps": "KEYCODE_APP_SWITCH",
        "enter": "KEYCODE_ENTER", "delete": "KEYCODE_DEL", "backspace": "KEYCODE_DEL", "tab": "KEYCODE_TAB",
        "search": "KEYCODE_SEARCH", "menu": "KEYCODE_MENU", "volume up": "KEYCODE_VOLUME_UP", "volume down": "KEYCODE_VOLUME_DOWN",
        "mute": "KEYCODE_VOLUME_MUTE", "power": "KEYCODE_POWER", "lock": "KEYCODE_SLEEP", "wake": "KEYCODE_WAKEUP",
        "play": "KEYCODE_MEDIA_PLAY_PAUSE", "pause": "KEYCODE_MEDIA_PLAY_PAUSE", "next": "KEYCODE_MEDIA_NEXT",
        "previous": "KEYCODE_MEDIA_PREVIOUS", "camera": "KEYCODE_CAMERA", "escape": "KEYCODE_ESCAPE"}


def _nodes(user):
    xml = sh(user, "uiautomator dump /sdcard/trillion_ui.xml >/dev/null 2>&1; cat /sdcard/trillion_ui.xml", timeout=30)
    out = []
    for m in re.finditer(r"<node\b([^>]*)/?>", xml):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        label = (a.get("text") or a.get("content-desc") or "").strip()
        b = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        label = label.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&#10;", " ").replace("&quot;", '"')
        out.append({"label": label, "x": (x1 + x2) // 2, "y": (y1 + y2) // 2, "box": (x1, y1, x2, y2), "tap": a.get("clickable") == "true",
                    "field": "EditText" in a.get("class", ""), "id": a.get("resource-id", "").split("/")[-1]})
    return out


def screen(user):
    """What is on the phone's screen right now, as a list of the things that can be read or tapped."""
    _awake_and_unlocked(user)
    focus = sh(user, "dumpsys window | grep -E 'mCurrentFocus' | tail -1")
    app = re.search(r"\s([\w.]+)/", focus)
    items, seen = [], set()
    for n in _nodes(user):
        if not n["label"] and not n["field"]:
            continue
        key = (n["label"], n["x"] // 20, n["y"] // 20)
        if key in seen:
            continue
        seen.add(key)
        items.append({"text": n["label"][:80] or f"(empty field {n['id']})", "x": n["x"], "y": n["y"],
                      **({"button": True} if n["tap"] else {}), **({"field": True} if n["field"] else {})})
    return {"app": app.group(1) if app else "", "items": items[:70],
            "note": "Use phone_tap with the text of an item (or its x and y) to press it."}


def tap(user, target="", x=None, y=None):
    _awake_and_unlocked(user)
    if x is not None and y is not None and str(x).lstrip("-").isdigit() and str(y).lstrip("-").isdigit():
        sh(user, f"input tap {int(x)} {int(y)}")
        return {"tapped": [int(x), int(y)]}
    want = str(target or "").strip().lower()
    if not want:
        raise PhoneError("Say what to tap: the text of a button, or an x and y position.")
    nodes = [n for n in _nodes(user) if n["label"]]
    exact = [n for n in nodes if n["label"].lower() == want]
    part = [n for n in nodes if want in n["label"].lower()]
    pool = exact or part
    if not pool:
        raise PhoneError(f"Nothing on the screen says '{target}'. Read the screen first to see what is there.")
    pool.sort(key=lambda n: (not n["tap"], len(n["label"])))
    n = pool[0]
    sh(user, f"input tap {n['x']} {n['y']}")
    return {"tapped": n["label"][:60], "at": [n["x"], n["y"]]}


def type_text(user, text):
    _awake_and_unlocked(user)
    t = str(text)
    if not t:
        raise PhoneError("There is nothing to type.")
    if any(ord(c) > 126 for c in t):
        raise PhoneError("I can only type plain English letters, numbers and common symbols on the phone.")
    quoted = t.replace("'", "'\\''").replace(" ", "%s")
    sh(user, f"input text '{quoted}'")
    return {"typed": t[:80]}


def press(user, key):
    k = str(key).strip().lower().replace("_", " ")
    code = KEYS.get(k) or KEYS.get(k.replace(" button", "").replace(" key", ""))
    if not code:
        raise PhoneError(f"I do not know the key '{key}'. Try: " + ", ".join(sorted(KEYS)[:14]) + ".")
    sh(user, f"input keyevent {code}")
    return {"pressed": k}


def swipe(user, direction):
    _awake_and_unlocked(user)
    m = re.search(r"(\d+)x(\d+)", sh(user, "wm size"))
    w, h = (int(m.group(1)), int(m.group(2))) if m else (1080, 2340)
    d = str(direction).strip().lower()
    cx, cy = w // 2, h // 2
    moves = {"up": (cx, int(h * .72), cx, int(h * .28)), "down": (cx, int(h * .28), cx, int(h * .72)),
             "left": (int(w * .85), cy, int(w * .15), cy), "right": (int(w * .15), cy, int(w * .85), cy)}
    if d not in moves:
        raise PhoneError("Swipe up, down, left or right.")
    x1, y1, x2, y2 = moves[d]
    sh(user, f"input swipe {x1} {y1} {x2} {y2} 260")
    return {"swiped": d}


def shell(user, command):
    return {"output": sh(user, str(command), timeout=45)[-4000:]}


def screenshot(user):
    serial = working_serial(user)
    dest = core.user_home(user) / "Phone screenshots"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / time.strftime("phone-%Y%m%d-%H%M%S.png")
    sh(user, "screencap -p /sdcard/trillion_shot.png")
    code, out = _run(["-s", serial, "pull", "/sdcard/trillion_shot.png", str(path)], timeout=40)
    if not path.exists():
        raise PhoneError("Could not copy the screenshot off the phone: " + out.strip()[-160:])
    return {"saved": str(path)}


def simple(user, what):
    """Small everyday switches that need no special permission: sleep, wake, sound, do not disturb, music keys."""
    if what == "sleep":
        sh(user, "input keyevent KEYCODE_SLEEP")
        return "Your phone's screen is off and it is locked."
    if what == "wake":
        sh(user, "input keyevent KEYCODE_WAKEUP")
        return "Your phone's screen is on."
    if what in ("dnd_on", "dnd_off"):
        out = sh(user, "cmd notification set_dnd " + ("on" if what == "dnd_on" else "off"))
        if re.search(r"unknown command|exception|error", out, re.I):
            raise PhoneError("This phone would not let me change Do not disturb.")
        return "Do not disturb is " + ("on." if what == "dnd_on" else "off.")
    if what == "mute":
        sh(user, "cmd audio set-ringer-mode SILENT; cmd media_session volume --stream 2 --set 0; cmd media_session volume --stream 5 --set 0")
        return "Your phone is on silent."
    if what == "unmute":
        sh(user, "cmd audio set-ringer-mode NORMAL; cmd media_session volume --stream 2 --set 11; cmd media_session volume --stream 5 --set 11")
        return "Your phone's ringer is back on."
    keys = {"playpause": ("KEYCODE_MEDIA_PLAY_PAUSE", "Play or pause pressed on your phone."),
            "next": ("KEYCODE_MEDIA_NEXT", "Skipped to the next track."), "previous": ("KEYCODE_MEDIA_PREVIOUS", "Went back a track.")}
    if what in keys:
        sh(user, "input keyevent " + keys[what][0])
        return keys[what][1]
    raise PhoneError("I do not know that phone switch.")


WA_PKGS = ("com.whatsapp", "com.whatsapp.w4b")
_WA_SKIP = re.compile(r"^(\d+ (new )?messages?( from \d+ chats?)?|checking for new messages?|backup (in progress|paused|complete).*|"
                      r"whatsapp web is currently active|.*is active on.*|you may have new messages|tap for more info.*)$", re.I)


def whatsapp_notifications(user):
    """New WhatsApp messages as the phone is showing them in its notification shade. Only what is still unread there
    can be seen: this reads notifications, not the WhatsApp chats themselves."""
    out = sh(user, "dumpsys notification --noredact", timeout=40)
    found = []
    for rec in re.split(r"\n\s*NotificationRecord\(", out)[1:]:
        m = re.search(r"\bpkg=(\S+)", rec)
        if not m or m.group(1) not in WA_PKGS:
            continue

        def ext(name):
            mm = re.search(r"android\.%s=[\w.$]+ \((.*)\)\s*$" % name, rec, re.M)
            return mm.group(1).strip() if mm else ""
        title, text = ext("title"), (ext("bigText") or ext("text"))
        if not title or not text or title in ("WhatsApp", "WhatsApp Business") or _WA_SKIP.match(text):
            continue
        title = re.sub(r"\s*\(\d+ (?:new )?messages?\)\s*", "", title).strip()
        chat, sender = title, title
        if " @ " in title:                      # "Brandon @ Family"
            sender, chat = [x.strip() for x in title.split(" @ ", 1)]
        elif ": " in title:                     # "Family: Brandon"
            chat, sender = [x.strip() for x in title.split(": ", 1)]
        kind = "call" if re.search(r"\b(voice|video) call\b", text, re.I) else (
            "voice note" if re.search(r"\bvoice message\b|\U0001F3A4", text, re.I) else "message")
        when = re.search(r"\bwhen=(\d{10,13})\b", rec)
        key = re.search(r"\bkey=(\S+)", rec)
        found.append({"chat": chat, "sender": sender, "text": text[:2000], "kind": kind, "group": chat != sender,
                      "when": int(when.group(1)[:10]) if when else 0, "key": key.group(1) if key else ""})
    return found


def screen_png(user):
    """The phone's screen right now as PNG bytes, for the dashboard's live picture. Nothing is saved."""
    serial = working_serial(user)
    try:
        r = subprocess.run([find_adb(), "-s", serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=25)
    except subprocess.TimeoutExpired:
        raise PhoneError("The phone did not answer in time.")
    if not r.stdout.startswith(b"\x89PNG"):
        raise PhoneError("The phone would not give me a picture of its screen.")
    return r.stdout


def phone_addresses(serial):
    """The phone's own network addresses: Tailscale (100.x) first, then Wi-Fi."""
    _, out = _run(["-s", serial, "shell", "ip -4 -o addr show"], timeout=15)
    ts, wifi = [], []
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", out, re.M):
        name, ip = m.group(1), m.group(2)
        if ip.startswith("127."):
            continue
        first, second = int(ip.split(".")[0]), int(ip.split(".")[1])
        (ts if first == 100 and 64 <= second <= 127 else wifi if name.startswith("wlan") else []).append(ip)
    return ts, wifi


def go_wireless(user, address=""):
    """Phone must be on the USB cable for this. Afterwards it is reachable at <address>:5555."""
    serial = linked_serial(user)
    address = (address or "").strip()
    if address and not re.fullmatch(r"[\w.-]+", address):
        raise PhoneError("That does not look like an address. Use the phone's Tailscale address, such as 100.x.x.x.")
    usb = core.settings(user).get("phone_usb")
    if ":" in serial and usb and _ready(usb, devices()):
        serial = usb                     # wireless link went quiet but the cable is in: set it up again from the cable
    if ":" in serial:
        raise PhoneError("This phone is already linked wirelessly. To set it up again, unlink it, plug in the cable and link it first.")
    ts, wifi = phone_addresses(serial)
    candidates = [a for a in dict.fromkeys(([address] if address else []) + ts + wifi)]
    if not candidates:
        raise PhoneError("The phone has no network address. Switch on Wi-Fi or Tailscale on the phone and try again.")
    code, out = _run(["-s", serial, "tcpip", "5555"], timeout=25)
    if "error" in out.lower() or code != 0:
        raise PhoneError("The phone would not switch to wireless mode: " + out.strip()[-160:])
    last = ""
    for attempt in range(10):          # the phone restarts its debugging service; give it time
        time.sleep(2.0)
        for addr in candidates:
            target = addr + ":5555"
            _, res = _run(["connect", target], timeout=12)
            low = res.lower()
            last = res.strip()
            if "failed to authenticate" in low or "unauthorized" in low:
                raise PhoneError("Unlock the phone and tap Allow on the debugging prompt, then click the button again.")
            if "connected to" in low:
                time.sleep(1.0)
                if any(d["serial"] == target and d["state"] == "device" for d in devices()):
                    return target, ("tailscale" if addr in ts or addr.startswith("100.") else "wifi")
                if any(d["serial"] == target and d["state"] == "unauthorized" for d in devices()):
                    raise PhoneError("Unlock the phone and tap Allow on the debugging prompt, then click the button again.")
    hint = ("The phone answered but would not accept the connection. Keep the cable plugged in, make sure Tailscale is "
            "switched on on the phone, and try again.") if "refused" in last.lower() else \
           "The phone could not be reached on the network. Check that Tailscale is switched on on the phone."
    raise PhoneError(f"Could not connect wirelessly. {hint} (Last answer: {last[-140:]})")


def pull_apk(user, name, dest):
    """Copy an installed app's APK file(s) off this login's phone to the folder `dest`.
    Returns {package, apk (the main base.apk), all (every file pulled), splits}."""
    pkg = resolve_app(user, name)
    out = sh(user, f"pm path {pkg}", timeout=30)
    paths = [ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:")]
    if not paths:
        raise PhoneError(f"Could not find the installed files for {name} ({pkg}).")
    serial = working_serial(user)
    os.makedirs(dest, exist_ok=True)
    pulled = []
    for i, p in enumerate(paths):
        fname = "base.apk" if p.endswith("base.apk") or len(paths) == 1 else f"split_{i}_{os.path.basename(p)}"
        local = os.path.join(dest, fname)
        code, _o = _run(["-s", serial, "pull", p, local], timeout=180)
        if code == 0 and os.path.exists(local):
            pulled.append(local)
    if not pulled:
        raise PhoneError("Could not copy the app's files off the phone. Is it still connected and unlocked?")
    base = next((l for l in pulled if l.endswith("base.apk")), pulled[0])
    return {"package": pkg, "apk": base, "all": pulled, "splits": len(pulled)}


def set_http_proxy(user, host, port):
    """Route the phone's web traffic through host:port (a proxy running on the PC). Over ADB, no root."""
    sh(user, f"settings put global http_proxy {host}:{int(port)}", timeout=20)
    return True


def clear_http_proxy(user):
    """Stop routing the phone through the PC proxy (back to normal)."""
    sh(user, "settings put global http_proxy :0", timeout=20)
    return True
