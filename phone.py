"""Trillion phone control: each login links its own Android phone over ADB (USB debugging).

Works over the USB cable, or wirelessly (for example over Tailscale) once switched to wireless mode.
Nothing here can get past the phone's lock screen: screen actions need the phone unlocked.
"""
import glob, os, re, shutil, subprocess, time
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
        raise PhoneError("No phone is linked to this login yet. Link one under Settings, My phone.")
    return s


def owner_of(serial):
    for u in core.q("SELECT * FROM users"):
        if core.settings(u).get("phone_serial") == serial:
            return u
    return None


def sh(user, command, timeout=25):
    """Run a command on this login's own phone."""
    serial = linked_serial(user)
    if ":" in serial and not any(d["serial"] == serial and d["state"] == "device" for d in devices()):
        _run(["connect", serial], timeout=12)
    code, out = _run(["-s", serial, "shell", command], timeout=timeout)
    low = out.lower()
    if "unauthorized" in low:
        raise PhoneError("The phone has not allowed this PC. Unlock it and tap Allow on the USB debugging prompt.")
    if "not found" in low and "device" in low or "offline" in low or "no devices" in low or "failed to connect" in low:
        raise PhoneError("The phone is not reachable. Plug it in, or check it is on the same Tailscale network with wireless debugging on.")
    return out


def _awake_and_unlocked(user):
    out = sh(user, "dumpsys power | grep -E 'mWakefulness=' ; dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen'")
    if "mWakefulness=Awake" not in out:
        sh(user, "input keyevent KEYCODE_WAKEUP")
        time.sleep(0.8)
        out = sh(user, "dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen'")
    if re.search(r"(mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen)=true", out):
        raise PhoneError("The phone is locked. Unlock it and ask me again: I cannot get past the lock screen.")


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


def resolve_app(user, name):
    key = re.sub(r"\s+", " ", name.lower().strip())
    pkgs = packages(user)
    if APPS.get(key) in pkgs:
        return APPS[key]
    squashed = key.replace(" ", "")
    hits = [p for p in pkgs if squashed in p.replace(".", "").lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        best = [p for p in hits if p.lower().endswith("." + squashed)]
        if len(best) == 1:
            return best[0]
        raise PhoneError(f"More than one app matches '{name}': {', '.join(hits[:6])}. Say which one.")
    raise PhoneError(f"I could not find an app called '{name}' on the phone.")


def open_app(user, name):
    pkg = resolve_app(user, name)
    _awake_and_unlocked(user)
    out = sh(user, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    if "No activities found" in out:
        raise PhoneError(f"{name} is installed but cannot be opened this way.")
    return {"opened": name, "package": pkg}


def contacts(user, name):
    out = sh(user, "content query --uri content://com.android.contacts/data/phones --projection display_name:data1", timeout=40)
    want = name.lower().strip()
    found = {}
    for ln in out.splitlines():
        m = re.search(r"display_name=(.*?), data1=(.*)$", ln)
        if m and want in m.group(1).lower():
            found.setdefault(m.group(1).strip(), [])
            num = m.group(2).strip()
            if num not in found[m.group(1).strip()]:
                found[m.group(1).strip()].append(num)
    if not found and "Permission" in out:
        raise PhoneError("The phone would not let me read contacts. Give me the number instead.")
    return found


def normalise(number, country="27"):
    n = re.sub(r"[^\d+]", "", number)
    if n.startswith("+"):
        return n[1:]
    if n.startswith("00"):
        return n[2:]
    if n.startswith("0"):
        return country + n[1:]
    return n


def resolve_contact(user, contact):
    """A phone number, or exactly one contact by name. Never guesses between several people."""
    country = str(core.settings(user).get("phone_country_code", "27"))
    if re.fullmatch(r"[\d\s+()-]{7,}", contact.strip()):
        return contact.strip(), normalise(contact, country)
    found = contacts(user, contact)
    exact = {k: v for k, v in found.items() if k.lower() == contact.lower().strip()}
    if exact:
        found = exact
    if not found:
        raise PhoneError(f"No contact called '{contact}' on the phone.")
    if len(found) > 1:
        raise PhoneError("More than one contact matches: " + "; ".join(list(found)[:8]) + ". Say exactly which one.")
    name, nums = next(iter(found.items()))
    return name, normalise(nums[0], country)


def _tap_send(user, pkg):
    for _ in range(5):
        time.sleep(1.6)
        xml = sh(user, "uiautomator dump /sdcard/trillion_ui.xml >/dev/null 2>&1; cat /sdcard/trillion_ui.xml", timeout=30)
        m = re.search(r'resource-id="%s:id/send"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % re.escape(pkg), xml) or \
            re.search(r'content-desc="Send"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            sh(user, f"input tap {(x1 + x2) // 2} {(y1 + y2) // 2}")
            return True
    return False


def send_whatsapp(user, contact, message):
    name, number = resolve_contact(user, contact)
    pkgs = packages(user)
    pkg = "com.whatsapp" if "com.whatsapp" in pkgs else ("com.whatsapp.w4b" if "com.whatsapp.w4b" in pkgs else None)
    if not pkg:
        raise PhoneError("WhatsApp is not installed on the phone.")
    _awake_and_unlocked(user)
    url = f"https://wa.me/{number}?text={quote(message, safe='')}"
    out = sh(user, f"am start -a android.intent.action.VIEW -d '{url}' {pkg}")
    if "Error" in out:
        raise PhoneError("WhatsApp would not open that chat: " + out.strip()[-160:])
    if not _tap_send(user, pkg):
        return {"status": "typed_not_sent", "to": name, "number": "+" + number,
                "note": "The chat is open with the message typed, but I could not press Send. Press it on the phone."}
    return {"status": "send_pressed", "to": name, "number": "+" + number,
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


def ring(user):
    tones = sh(user, "ls /system/media/audio/ringtones 2>/dev/null | head -3; ls /product/media/audio/ringtones 2>/dev/null | head -3").split()
    sh(user, "cmd media_session volume --stream 3 --set 15; input keyevent KEYCODE_WAKEUP")
    if not tones:
        return {"status": "volume_raised", "note": "I turned the volume up but found no ringtone to play."}
    folder = "/system/media/audio/ringtones" if "No such" not in sh(user, f"ls /system/media/audio/ringtones/{tones[0]}") else "/product/media/audio/ringtones"
    sh(user, f"am start -a android.intent.action.VIEW -d file://{folder}/{tones[0]} -t audio/ogg")
    return {"status": "ringing_attempted", "note": "I turned the volume up and started a ringtone. On some phones a "
            "music app has to be picked on screen first. If the phone is truly lost, use Google's Find My Device."}


def screenshot(user):
    serial = linked_serial(user)
    dest = core.user_home(user) / "Phone screenshots"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / time.strftime("phone-%Y%m%d-%H%M%S.png")
    sh(user, "screencap -p /sdcard/trillion_shot.png")
    code, out = _run(["-s", serial, "pull", "/sdcard/trillion_shot.png", str(path)], timeout=40)
    if not path.exists():
        raise PhoneError("Could not copy the screenshot off the phone: " + out.strip()[-160:])
    return {"saved": str(path)}


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
