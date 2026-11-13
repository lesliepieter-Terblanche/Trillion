"""Security watch: Trillion keeps an eye on THIS PC and your own home network for signs of hacking, warns
you, and can stop it. Defensive only - it watches your own machine and your own Wi-Fi, never anyone else's.

What it looks for (Windows):
  - Windows Defender threats (malware it has found)
  - brute-force sign-in attempts (bursts of failed logins, incl. remote/RDP)
  - a new administrator account appearing (a classic sign of a break-in)
  - new programs listening for connections, and risky ports open to the internet (RDP 3389, SMB 445)
  - unknown new devices joining your home network (someone on your Wi-Fi)

It compares against a "this is normal" baseline you set once, so it only flags real changes. When something
serious shows up it alerts you (phone notification + the chat) and, if you switch on auto-defend, it can block
the attacker's IP in the firewall or stop a rogue program. Every powerful action is owner-only.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import core

try:
    NO_WINDOW = subprocess.CREATE_NO_WINDOW            # hide the console flash on Windows
except AttributeError:
    NO_WINDOW = 0

DIR = core.DATA / "security"
BASELINE = DIR / "baseline.json"
LOGFILE = DIR / "events.json"
LAST = {"t": 0, "findings": [], "scanning": False}
_LOCK = threading.Lock()
RISKY_PORTS = {3389: "Remote Desktop (RDP)", 445: "Windows file sharing (SMB)", 135: "Windows RPC",
               139: "NetBIOS", 5900: "VNC remote control", 23: "Telnet"}


def available():
    return sys.platform.startswith("win")


# ---------- running PowerShell safely ----------
def _ps(script, timeout=40):
    """Run a PowerShell snippet, return its stdout (or '' on any failure). Never raises."""
    if not available():
        return ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout or ""
    except Exception:
        return ""


def _ps_json(script, timeout=40):
    """Run PowerShell that ends in ConvertTo-Json; return a list (one object becomes a 1-list)."""
    out = _ps(script, timeout).strip()
    if not out:
        return []
    try:
        v = json.loads(out)
    except Exception:
        return []
    return v if isinstance(v, list) else [v]


# ---------- the individual checks ----------
def _defender():
    rows = _ps_json("Get-MpThreat -ErrorAction SilentlyContinue | Select-Object ThreatName,Resources,IsActive | "
                    "ConvertTo-Json -Compress")
    out = []
    for t in rows:
        name = str(t.get("ThreatName") or "a threat")
        res = t.get("Resources")
        where = (", ".join(res) if isinstance(res, list) else str(res or ""))[:200]
        out.append({"sev": "high", "kind": "malware", "what": f"Windows Defender found: {name}",
                    "detail": where, "action": None,
                    "note": "Defender has already quarantined or blocked it; review it in Windows Security."})
    return out


def _failed_logins(minutes=60):
    rows = _ps_json(
        "$s=(Get-Date).AddMinutes(-%d);"
        "Get-WinEvent -FilterHashtable @{LogName='Security';Id=4625;StartTime=$s} -ErrorAction SilentlyContinue |"
        " ForEach-Object { $x=[xml]$_.ToXml();"
        " [pscustomobject]@{ User=($x.Event.EventData.Data | ?{$_.Name -eq 'TargetUserName'}).'#text';"
        " IP=($x.Event.EventData.Data | ?{$_.Name -eq 'IpAddress'}).'#text' } } |"
        " Group-Object IP | ForEach-Object { [pscustomobject]@{ IP=$_.Name; Count=$_.Count;"
        " Users=(($_.Group.User | Select-Object -Unique) -join ',') } } | ConvertTo-Json -Compress" % int(minutes))
    out = []
    for g in rows:
        ip = str(g.get("IP") or "").strip() or "-"
        n = int(g.get("Count") or 0)
        if n < 8:                                      # a few failures are normal mistyped passwords
            continue
        remote = ip not in ("", "-", "127.0.0.1", "::1", "::", "0.0.0.0")
        sev = "high" if (remote and n >= 10) else "med"
        where = f"from {ip}" if remote else "at this PC"
        out.append({"sev": sev, "kind": "bruteforce",
                    "what": f"{n} failed sign-in attempts in {minutes} min {where}",
                    "detail": f"tried user(s): {g.get('Users') or '?'}",
                    "action": {"block_ip": ip} if remote and ip not in ("-",) else None})
    return out


def _admins():
    rows = _ps_json("Get-LocalGroupMember -Group Administrators -ErrorAction SilentlyContinue | "
                    "Select-Object -ExpandProperty Name | ConvertTo-Json -Compress")
    return sorted({str(x).strip() for x in rows if str(x).strip()})


def _listeners():
    rows = _ps_json(
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {"
        " $p=(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue);"
        " [pscustomobject]@{ Port=$_.LocalPort; Addr=$_.LocalAddress; PID=$_.OwningProcess;"
        " Proc=$(if($p){$p.ProcessName}else{'?'}); Path=$(if($p){$p.Path}else{''}) } } |"
        " Sort-Object Port -Unique | ConvertTo-Json -Compress")
    out = []
    for r in rows:
        try:
            out.append({"port": int(r.get("Port")), "addr": str(r.get("Addr") or ""),
                        "pid": int(r.get("PID") or 0), "proc": str(r.get("Proc") or "?"),
                        "path": str(r.get("Path") or "")})
        except Exception:
            continue
    return out


def _new_lan_devices(user, known):
    try:
        import netscan
        devs = netscan.scan(timeout=2, user=user) or []
    except Exception:
        return [], known
    seen = dict(known)
    out = []
    for d in devs:
        mac = str(d.get("mac") or "").upper().strip()
        if not mac or mac in ("", "FF:FF:FF:FF:FF:FF"):
            continue
        name = d.get("name") or d.get("host") or d.get("ip") or mac
        if mac not in known:
            out.append({"sev": "med", "kind": "newdevice",
                        "what": f"New device on your network: {name}",
                        "detail": f"{d.get('ip','?')}  {mac}  {d.get('vendor','') or ''}".strip(),
                        "action": None})
        seen[mac] = name
    return out, seen


# ---------- baseline (what "normal" looks like) ----------
def _read_baseline():
    try:
        return json.loads(BASELINE.read_text())
    except Exception:
        return {}


def set_baseline(user):
    """Snapshot the current admins, listening ports and known network devices as 'normal'."""
    DIR.mkdir(parents=True, exist_ok=True)
    base = {"admins": _admins(), "ports": sorted({l["port"] for l in _listeners()}),
            "devices": {}, "set": time.time()}
    try:
        import netscan
        for d in (netscan.scan(timeout=2, user=user) or []):
            mac = str(d.get("mac") or "").upper().strip()
            if mac:
                base["devices"][mac] = d.get("name") or d.get("host") or d.get("ip") or mac
    except Exception:
        pass
    BASELINE.write_text(json.dumps(base))
    return {"ok": True, "admins": len(base["admins"]), "ports": len(base["ports"]), "devices": len(base["devices"])}


# ---------- the full scan ----------
def scan(user, minutes=60):
    """Run every check, compare to the baseline, return a list of findings (most serious first)."""
    if not available():
        return {"ok": False, "error": "Security watch runs on the Windows PC that hosts Trillion.", "findings": []}
    with _LOCK:
        LAST["scanning"] = True
    try:
        base = _read_baseline()
        findings = []
        findings += _defender()
        findings += _failed_logins(minutes)
        # new administrator accounts
        admins_now = _admins()
        base_admins = set(base.get("admins") or [])
        if base_admins:
            for a in admins_now:
                if a not in base_admins:
                    findings.append({"sev": "high", "kind": "newadmin",
                                     "what": f"A new administrator account appeared: {a}",
                                     "detail": "If you didn't create this, it's a strong sign of a break-in.",
                                     "action": None})
        # new / risky listening ports
        base_ports = set(base.get("ports") or [])
        for l in _listeners():
            internet_facing = l["addr"] in ("0.0.0.0", "::", "")
            if l["port"] in RISKY_PORTS and internet_facing:
                findings.append({"sev": "med", "kind": "openport",
                                 "what": f"{RISKY_PORTS[l['port']]} is open to the network (port {l['port']})",
                                 "detail": f"used by {l['proc']} (PID {l['pid']})",
                                 "action": {"kill": l["pid"], "proc": l["proc"]} if l["proc"] not in ("System", "svchost", "?") else None})
            elif base_ports and l["port"] not in base_ports and l["proc"] not in ("System", "svchost", "?"):
                findings.append({"sev": "low", "kind": "openport",
                                 "what": f"New program listening on port {l['port']}: {l['proc']}",
                                 "detail": l.get("path") or f"PID {l['pid']}",
                                 "action": {"kill": l["pid"], "proc": l["proc"]}})
        # new devices on the home network
        dev_findings, _seen = _new_lan_devices(user, base.get("devices") or {})
        findings += dev_findings

        order = {"high": 0, "med": 1, "low": 2}
        findings.sort(key=lambda f: order.get(f.get("sev"), 3))
        with _LOCK:
            LAST.update(t=time.time(), findings=findings, scanning=False)
        return {"ok": True, "findings": findings, "checked": time.time(), "baseline": bool(base)}
    finally:
        with _LOCK:
            LAST["scanning"] = False


# ---------- actions (owner-gated by the caller) ----------
def block_ip(ip):
    ip = str(ip or "").strip()
    if not available():
        return {"error": "Only works on the Windows PC."}
    if not ip or ip in ("127.0.0.1", "::1", "0.0.0.0", "-"):
        return {"error": "That isn't an IP I can block."}
    _ps(f"New-NetFirewallRule -DisplayName 'Trillion block {ip}' -Direction Inbound -RemoteAddress {ip} "
        f"-Action Block -ErrorAction SilentlyContinue | Out-Null")
    ok = _ps(f"(Get-NetFirewallRule -DisplayName 'Trillion block {ip}' -ErrorAction SilentlyContinue) -ne $null")
    return {"blocked": ip} if "True" in ok else {"error": f"Could not block {ip} (Trillion may need to run as administrator)."}


def unblock_ip(ip):
    ip = str(ip or "").strip()
    _ps(f"Remove-NetFirewallRule -DisplayName 'Trillion block {ip}' -ErrorAction SilentlyContinue | Out-Null")
    return {"unblocked": ip}


def kill_process(pid):
    try:
        pid = int(pid)
    except Exception:
        return {"error": "No such process."}
    if not available():
        return {"error": "Only works on the Windows PC."}
    _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
    still = _ps(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null")
    return {"error": f"Could not stop process {pid} (may need administrator)."} if "True" in still else {"stopped": pid}


# ---------- periodic watch (called from the background loop) ----------
def _log_event(uid, findings):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        rec = {"t": time.time(), "uid": uid, "findings": findings}
        hist = []
        try:
            hist = json.loads(LOGFILE.read_text())
        except Exception:
            hist = []
        hist.append(rec)
        LOGFILE.write_text(json.dumps(hist[-200:]))
    except Exception:
        pass


def watch_once(user):
    """One monitoring pass for the background loop: scan, alert on serious findings, optionally auto-defend."""
    s = core.settings(user)
    if not s.get("security_monitor") or not available():
        return
    if not _read_baseline():
        # first run: learn what's normal on this PC, then compare from the next pass on (so we don't flag
        # every existing device/port/admin as if it were new)
        try:
            set_baseline(user)
        except Exception:
            pass
        return
    res = scan(user)
    findings = [f for f in res.get("findings", []) if f.get("sev") in ("high", "med")]
    if not findings:
        return
    _log_event(user["id"], findings)
    autodef = bool(s.get("security_autodefend"))
    acted = []
    if autodef:
        for f in findings:
            act = f.get("action") or {}
            if f.get("kind") == "bruteforce" and act.get("block_ip"):
                if "blocked" in block_ip(act["block_ip"]):
                    acted.append(f"blocked {act['block_ip']}")
    top = findings[0]
    extra = f" Auto-defend: {', '.join(acted)}." if acted else ""
    body = top["what"] + (f" (+{len(findings) - 1} more)" if len(findings) > 1 else "") + extra
    try:
        import watch
        watch.alert(user, "security", "Security alert", body)
    except Exception:
        pass
    try:
        core.log(user["id"], "Security: " + body, "waiting")
    except Exception:
        pass


def state(user):
    with _LOCK:
        last = dict(LAST)
    s = core.settings(user)
    return {"available": available(), "monitor": bool(s.get("security_monitor")),
            "autodefend": bool(s.get("security_autodefend")), "baseline": bool(_read_baseline()),
            "last_scan": last["t"], "findings": last["findings"]}
