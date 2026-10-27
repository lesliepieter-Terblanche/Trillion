"""Android-TV control over the network with ADB — connect to a smart TV, see its apps, sideload a new launcher
(the brand shut down, the launcher can be replaced), and set it as the default home. Reuses the same ADB that
drives the phone. Needs the TV to have ADB/network debugging on (Developer options); until then connect() says so.

Known launchers are open-source and fetched from F-Droid (direct, trusted APKs); you can also install any APK by
local path or URL.
"""
import os
import re
import tempfile

import core
import phone      # reuse phone.find_adb() and phone._run()

# open-source launchers we can fetch straight from F-Droid, with their HOME activity for setting as default
LAUNCHERS = {
    "flauncher": {"pkg": "me.efesser.flauncher", "home": "me.efesser.flauncher/.MainActivity",
                  "name": "FLauncher", "note": "light, open-source, made for Android TV"},
}


def _ip(target):
    t = str(target or "").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+(:\d+)?", t):
        return t.split(":")[0]
    import netscan
    info = netscan.lookup(t)
    if info.get("found"):
        return info["ip"]
    raise ValueError(f"I couldn't find a device called “{target}” on the network.")


def _addr(target, port=5555):
    ip = _ip(target)
    return ip if ":" in str(target) and str(target).count(".") == 3 else f"{ip}:{port}"


def _state(devices_out, addr):
    for line in (devices_out or "").splitlines():
        if line.startswith(addr):
            return line.split()[-1] if len(line.split()) > 1 else "device"
    return ""


def connect(target, port=5555):
    """Open an ADB connection to the TV over the network. Reports clearly if ADB debugging isn't enabled yet."""
    addr = _addr(target, port)
    code, out = phone._run(["connect", addr], timeout=12)
    low = out.lower()
    if "connected to" in low or "already connected" in low:
        _, dout = phone._run(["devices"], timeout=10)
        st = _state(dout, addr)
        if st == "unauthorized":
            return {"ok": False, "addr": addr, "state": "unauthorized",
                    "note": "The TV is asking to allow this computer. Accept the prompt on the TV screen (tick "
                            "'always allow'), then try again."}
        return {"ok": True, "addr": addr, "state": st or "device", "note": "Connected to the TV."}
    return {"ok": False, "addr": addr, "error": out.strip()[:200],
            "note": "ADB over the network isn't open on the TV. Turn on Developer options → USB debugging / "
                    "Network debugging on the TV first (a USB mouse helps reach the hidden settings), then try again."}


def status(target, port=5555):
    addr = _addr(target, port)
    _, dout = phone._run(["devices"], timeout=10)
    st = _state(dout, addr)
    return {"addr": addr, "connected": bool(st and st != "offline"), "state": st or "not connected"}


def _ensure(target, port=5555):
    addr = _addr(target, port)
    st = status(target, port)
    if not st["connected"] or st["state"] == "unauthorized":
        c = connect(target, port)
        if not c.get("ok"):
            return None, c
    return addr, None


def apps(target, port=5555):
    """List the TV's installed apps and, separately, which of them are launchers (HOME apps)."""
    addr, err = _ensure(target, port)
    if err:
        return err
    _, out = phone._run(["-s", addr, "shell", "pm", "list", "packages"], timeout=25)
    pkgs = sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("package:"))
    _, hout = phone._run(["-s", addr, "shell", "cmd", "package", "query-activities",
                          "--brief", "-c", "android.intent.category.HOME"], timeout=20)
    launchers = sorted(set(re.findall(r"([\w.]+/[\w.$]+)", hout or "")))
    return {"addr": addr, "count": len(pkgs), "launchers": launchers, "packages": pkgs[:300]}


def _fdroid_apk_url(pkg):
    import httpx
    r = httpx.get(f"https://f-droid.org/api/v1/packages/{pkg}", timeout=20)
    r.raise_for_status()
    data = r.json()
    code = data.get("suggestedVersionCode") or (data.get("packages") or [{}])[0].get("versionCode")
    if not code:
        raise RuntimeError("No version found on F-Droid.")
    return f"https://f-droid.org/repo/{pkg}_{code}.apk"


def _download(url):
    import httpx
    fd, path = tempfile.mkstemp(suffix=".apk")
    os.close(fd)
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return path


def install(target, apk, port=5555):
    """Sideload an APK onto the TV. apk is a local file path on the PC or an http(s) URL (downloaded first)."""
    addr, err = _ensure(target, port)
    if err:
        return err
    path = str(apk)
    tmp = None
    try:
        if path.lower().startswith("http"):
            tmp = path = _download(path)
        elif not os.path.exists(path):
            return {"ok": False, "error": f"No APK found at {path}."}
        code, out = phone._run(["-s", addr, "install", "-r", path], timeout=240)
        ok = "success" in (out or "").lower()
        return {"ok": ok, "addr": addr, "output": out.strip()[-200:],
                "note": "Installed." if ok else "Install failed — check the APK and that the TV accepted the connection."}
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def set_launcher(target, package, activity="", port=5555):
    """Make a launcher the TV's default home screen. Give the package (e.g. me.efesser.flauncher)."""
    addr, err = _ensure(target, port)
    if err:
        return err
    comp = activity
    if not comp:
        known = next((v["home"] for v in LAUNCHERS.values() if v["pkg"] == package), "")
        comp = known
    if not comp:                       # resolve the HOME activity from the device
        _, out = phone._run(["-s", addr, "shell", "cmd", "package", "resolve-activity", "--brief",
                             "-c", "android.intent.category.HOME", package], timeout=20)
        m = re.search(r"([\w.]+/[\w.$]+)", out or "")
        comp = m.group(1) if m else ""
    if not comp:
        return {"ok": False, "note": f"Couldn't find {package}'s home screen. On the TV press the Home button and "
                                     "pick the new launcher when asked."}
    code, out = phone._run(["-s", addr, "shell", "cmd", "package", "set-home-activity", comp], timeout=20)
    ok = "success" in (out or "").lower() or not (out or "").strip()
    return {"ok": ok, "addr": addr, "component": comp, "output": out.strip()[-160:],
            "note": "Set as the default launcher. Press Home on the TV to see it." if ok else
                    "Could not set it automatically — press Home on the TV and choose the launcher manually."}


def install_launcher(target, which="flauncher", port=5555):
    """Download a known open-source launcher from F-Droid and install it on the TV, then set it as the default."""
    which = (which or "flauncher").lower().replace(" ", "")
    spec = LAUNCHERS.get(which) or LAUNCHERS["flauncher"]
    try:
        url = _fdroid_apk_url(spec["pkg"])
    except Exception as e:
        return {"ok": False, "error": f"Could not reach F-Droid for {spec['name']} ({type(e).__name__}). "
                "You can still install any launcher APK with the install tool by giving its path or URL."}
    res = install(target, url, port)
    if not res.get("ok"):
        return res
    sl = set_launcher(target, spec["pkg"], spec.get("home", ""), port)
    return {"ok": True, "launcher": spec["name"], "installed": True, "set_default": sl.get("ok"),
            "note": f"{spec['name']} installed" + (" and set as default." if sl.get("ok") else
                    " — press Home on the TV and choose it as the launcher.")}


def tv_shell(target, command, port=5555):
    """Run a raw ADB shell command on the TV. Owner only, needs approval."""
    addr, err = _ensure(target, port)
    if err:
        return err
    code, out = phone._run(["-s", addr, "shell", command], timeout=60)
    return {"addr": addr, "exit": code, "output": (out or "").strip()[-1500:]}
