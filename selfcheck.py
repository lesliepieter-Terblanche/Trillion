"""Self-check: Trillion tests each of her own parts live on this PC and reports what is working.

Owner only. Read-only and safe: it looks and reports, it never sends a message, makes a call, deletes anything
or restarts. Each check comes back as ok (green), warn (amber: optional or not set up) or fail (red: broken).
"""
import os
import shutil
import time

import core

OK, WARN, FAIL = "ok", "warn", "fail"


def _c(name, status, detail, fix=""):
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _brain():
    m = core.models()
    if not m.get("ok"):
        return _c("Local brain", FAIL, "Ollama is not answering on this PC.",
                  "Make sure Ollama is installed and running, then restart Trillion.")
    names = [x["name"] for x in m.get("list", [])]
    if not names:
        return _c("Local brain", WARN, "Ollama is running but no models are installed.",
                  "Install a model (for example qwen2.5:14b) with Ollama.")
    loaded = [x["name"] for x in m["list"] if x.get("loaded")]
    return _c("Local brain", OK, f"{len(names)} model(s) installed" + (f", {loaded[0]} warm" if loaded else "") + ".")


def _hearing(hearing_state):
    st = (hearing_state or {}).get("state", "unknown")
    if st == "ready":
        return _c("Hearing (speech)", OK, "The speech model is loaded.")
    if st in ("loading", "not loaded"):
        return _c("Hearing (speech)", WARN, f"The speech model is {st}. It loads on first use.")
    return _c("Hearing (speech)", WARN, f"Hearing state: {st}.")


def _tailscale(ts):
    v = (ts or {}).get("v") or {}
    if v.get("ip"):
        return _c("Tailscale", OK, f"Connected as {v.get('name') or 'this PC'} ({v['ip']}).")
    return _c("Tailscale", FAIL, "Tailscale is not connected, so your phone and other devices cannot reach Trillion.",
              "Open Tailscale on this PC and sign in.")


def _phone(user):
    if not core.settings(user).get("phone_serial"):
        return _c("Phone link", WARN, "No phone linked to this login.",
                  "Pair your phone on the Phone page if you want WhatsApp, calls and REA.")
    try:
        import phone
        serial = core.settings(user)["phone_serial"]
        on = [d for d in phone.devices() if d["serial"] == serial and d["state"] == "device"]
        if on:
            return _c("Phone link", OK, f"Phone {serial} is connected.")
        seen = [d for d in phone.devices() if d["serial"] == serial]
        if seen:
            return _c("Phone link", FAIL, f"Phone {serial} is {seen[0]['state']} (not ready).",
                      "Unlock the phone and tap Allow on the USB-debugging prompt, or re-pair over Wi-Fi.")
        return _c("Phone link", FAIL, f"Phone {serial} is not reachable right now.",
                  "Check the phone is on, unlocked and on the same Tailscale network, then re-pair if needed.")
    except Exception as e:
        return _c("Phone link", FAIL, f"Could not check the phone: {type(e).__name__}.")


def _website(user, sites):
    site = core.settings(user).get("site")
    if not site:
        return _c("Your website", WARN, "No website set for this login.")
    r = (sites or {}).get(site)
    if r is None:
        return _c("Your website", WARN, f"{site} has not been checked yet.")
    if r.get("ok"):
        return _c("Your website", OK, f"{site} is up.")
    return _c("Your website", FAIL, f"{site} is not answering.", "Check the site and its host.")


def _docker():
    try:
        import apps
        st = apps.docker_state()
    except Exception as e:
        return _c("Docker (business apps)", WARN, f"Could not check Docker: {type(e).__name__}.")
    if not st.get("installed"):
        return _c("Docker (business apps)", WARN, "Docker is not installed. Only needed for the business apps.",
                  "Install Docker Desktop from the Apps page if you want SerpBear, n8n or Activepieces.")
    if not st.get("running"):
        return _c("Docker (business apps)", WARN, "Docker is installed but not running.",
                  "Start Docker Desktop if you want to use the business apps.")
    try:
        import apps
        running = [k for k, v in apps.running_projects().items() if "running" in v]
        return _c("Docker (business apps)", OK, f"Docker is running ({len(running)} app project(s) up).")
    except Exception:
        return _c("Docker (business apps)", OK, "Docker is running.")


def _backup():
    try:
        import backup
        st = backup.owner_settings()
        target = st.get("backup_target")
        backups = backup.find_backups()
    except Exception as e:
        return _c("Backup", WARN, f"Could not check backups: {type(e).__name__}.")
    if backups:
        newest = backups[0]
        when = time.strftime("%d %b %H:%M", time.localtime(newest.get("when", 0))) if newest.get("when") else "unknown time"
        return _c("Backup", OK, f"{len(backups)} backup(s) found; newest {when}.")
    if target:
        writable = False
        try:
            os.makedirs(target, exist_ok=True)
            probe = os.path.join(target, ".trillion_write_test")
            open(probe, "w").close()
            os.remove(probe)
            writable = True
        except Exception:
            pass
        return _c("Backup", WARN if writable else FAIL,
                  f"A backup folder is set ({target}) but no backup has been made yet."
                  if writable else f"The backup folder ({target}) cannot be written to.",
                  "Press BACK UP NOW on the Settings page." if writable else "Choose a folder on a drive that stays plugged in.")
    return _c("Backup", WARN, "No backup has been made and no folder is set.",
              "Settings, Backup and restore: choose a folder on another drive and press BACK UP NOW.")


def _calls(user):
    try:
        import voicecall
        reg = voicecall.REGISTRAR
        if not reg.get("running"):
            return _c("Calls (Linphone)", WARN, "The call sign-in point is not running." + (f" {reg.get('error')}" if reg.get("error") else ""),
                      "Restart Trillion; if it keeps failing, port 5060 may be in use.")
        if voicecall.registered(user):
            return _c("Calls (Linphone)", OK, "Linphone is signed in; Trillion can call you and you can call it.")
        if voicecall.prefs(user).get("target"):
            return _c("Calls (Linphone)", WARN, "A phone address is saved but Linphone is not signed in right now.",
                      "Open Linphone on the phone so it signs in.")
        return _c("Calls (Linphone)", WARN, "Linphone is not set up yet.",
                  "Set up Linphone on the Phone page if you want phone calls with Trillion.")
    except Exception as e:
        return _c("Calls (Linphone)", WARN, f"Could not check calls: {type(e).__name__}.")


def _claude_code(user):
    import shutil
    cmd = (os.environ.get("TRILLION_CLAUDE_CMD") or "").split(" ")[0] or "claude"
    found = shutil.which(cmd) or (os.path.exists(cmd) and cmd)
    if found:
        return _c("Claude Code (REA)", OK, "Claude Code is installed (used for native/APK reverse-engineering).")
    return _c("Claude Code (REA)", WARN, "Claude Code is not installed on this PC.",
              "Install Claude Code and sign in to your Pro plan so REA can analyse apps and native programs.")


def _mitmproxy():
    found = os.environ.get("TRILLION_MITMDUMP") or shutil.which("mitmdump")
    if found:
        return _c("mitmproxy (app traffic)", OK, "mitmproxy is installed (used to capture an app's network traffic).")
    return _c("mitmproxy (app traffic)", WARN, "mitmproxy is not installed on this PC.",
              "Run 'pip install mitmproxy' if you want to capture what servers a phone app talks to.")


def _heavy_model(user):
    hv = core.settings(user).get("heavy_model") or "mistral-small:24b"
    try:
        if core.has_model(hv):
            return _c("Smart model", OK, f"The bigger brain ({hv}) is installed; used for coding and hard reasoning.")
    except Exception:
        pass
    return _c("Smart model", WARN, f"The bigger brain ({hv}) is not installed yet. Everyday chat still works on the fast model.",
              "Tell Trillion 'install the smart model', or run Setup again, to download it (about 14 GB).")


def _search():
    try:
        import apps
        r = core.q("SELECT status FROM apps WHERE key='searxng'", one=True)
        if r and r["status"] in ("installed", "running"):
            return _c("Private web search", OK, "SearXNG is installed; Trillion can search the web with no API key.")
    except Exception:
        pass
    return _c("Private web search", WARN, "SearXNG is not installed; web search is off.",
              "Install 'Private search (SearXNG)' on the Apps page (needs Docker).")


def _knowledge():
    try:
        import vstore
        if vstore.available():
            return _c("Knowledge store", OK, "The smart memory store (Chroma) is active for the second brain.")
    except Exception:
        pass
    return _c("Knowledge store", WARN, "The smart memory store is not installed; the built-in memory search is used instead.",
              "Run 'pip install chromadb' (Setup does this automatically) for faster, sharper recall.")


def _cast():
    try:
        import cast
        if cast.available():
            return _c("Cast (Google Home / Chromecast)", OK, "pychromecast is installed; Trillion can announce and cast.")
    except Exception:
        pass
    return _c("Cast (Google Home / Chromecast)", WARN, "Casting is not set up (pychromecast not installed).",
              "Run Setup again (or 'pip install pychromecast gTTS') to announce on speakers and cast media.")


def _deco(user):
    try:
        import extend
        d = extend.deco_status(user)
    except Exception:
        return _c("TP-Link Deco", WARN, "Could not check the Deco connector.")
    if d.get("configured") and d.get("has_password"):
        extra = " (changes on)" if d.get("allow_write") else " (read-only)"
        return _c("TP-Link Deco", OK, f"Set up for {d.get('host')}{extra}.")
    if d.get("configured"):
        return _c("TP-Link Deco", WARN, "Deco address saved but no admin password yet.",
                  "Add your Deco password on the Skills page (Connectors > TP-Link Deco).")
    return _c("TP-Link Deco", WARN, "Deco mesh not connected.",
              "Set it up on the Skills page (Connectors > TP-Link Deco) if you want to manage your Wi-Fi.")


def _disk():
    try:
        v = core.vitals().get("disk")
        if not v:
            return _c("Disk space", WARN, "Could not read disk space.")
        free = v["total"] - v["used"]
        if free < 5:
            return _c("Disk space", FAIL, f"Only {free:.1f} GB free on the system drive.",
                      "Free up space on the Files page (Free up C:).")
        if free < 20:
            return _c("Disk space", WARN, f"{free:.1f} GB free on the system drive.")
        return _c("Disk space", OK, f"{free:.0f} GB free on the system drive.")
    except Exception:
        return _c("Disk space", WARN, "Could not read disk space.")


def _secrets():
    try:
        p = core.DATA / "secret.key"
        return _c("Saved keys", OK, "The key that protects your saved passwords is in place.") if p.exists() \
            else _c("Saved keys", FAIL, "The encryption key for saved passwords is missing.",
                    "Restore from a backup, or re-enter your keys on the Settings page.")
    except Exception:
        return _c("Saved keys", WARN, "Could not check the saved-keys file.")


def run(user, sites=None, hearing=None, tailscale=None):
    """Run every check and return a summary. Owner only."""
    if not user["is_admin"]:
        return {"error": "The self-check is for the owner login only."}
    checks = [
        _brain(),
        _heavy_model(user),
        _hearing(hearing),
        _tailscale(tailscale),
        _phone(user),
        _calls(user),
        _website(user, sites),
        _docker(),
        _search(),
        _knowledge(),
        _cast(),
        _deco(user),
        _backup(),
        _disk(),
        _secrets(),
        _claude_code(user),
        _mitmproxy(),
    ]
    counts = {OK: 0, WARN: 0, FAIL: 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    overall = FAIL if counts[FAIL] else (WARN if counts[WARN] else OK)
    return {"checks": checks, "counts": counts, "overall": overall, "when": time.time()}


def spoken(result):
    """A short line for a voice/chat reply."""
    if "error" in result:
        return result["error"]
    c = result["counts"]
    bad = [x["name"] for x in result["checks"] if x["status"] == FAIL]
    warn = [x["name"] for x in result["checks"] if x["status"] == WARN]
    if not c[FAIL] and not c[WARN]:
        return "Everything checks out: all green."
    parts = [f"{c[OK]} green"]
    if c[WARN]:
        parts.append(f"{c[WARN]} amber ({', '.join(warn)})")
    if c[FAIL]:
        parts.append(f"{c[FAIL]} red ({', '.join(bad)})")
    return "Self-check: " + ", ".join(parts) + "."
