"""See what's on the home network and identify it — find a smart TV, a printer, a camera, a phone, the router.

Self-contained (standard library only): a concurrent ping sweep of the local subnet, the ARP table for MAC
addresses, reverse-DNS for hostnames, a small MAC-vendor (OUI) table, an SSDP/UPnP probe that smart TVs and
media players answer with a friendly name, and a quick port check that fingerprints common devices. Works for
wired and wireless devices alike. Nothing here needs a cloud account; if the TP-Link Deco is set up its client
list can enrich the names, but the scan stands on its own.
"""
import concurrent.futures as _fut
import re
import socket
import subprocess
import sys
from urllib.request import urlopen

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_WIN = sys.platform.startswith("win")

# A small table of common MAC prefixes -> vendor. Best-effort; a miss just leaves the vendor blank.
OUI = {
    "Samsung": ("E8508B", "FC03D6", "8CC8CD", "D0667B", "5CF6DC", "BC8CCD", "F4D9FB", "C0BDD1", "2C440B", "9401C2"),
    "LG": ("A816B2", "C4366C", "00E091", "10683F", "CC2D8C", "74A722", "AC0D1B", "3CBDD8", "B81DAA"),
    "Sony": ("FCF152", "A0E453", "3429EA", "D8D43C", "104FA8", "AC9B0A", "FC0FE6"),
    "Hisense": ("1C5A6B", "7CB1E1", "D46A91", "3CE1A1"),
    "TCL": ("DCF7A4", "C4120A", "9CD24B", "0CF019"),
    "Panasonic": ("8C73DC", "A4B1C1", "34C3AC", "C0D7AA"),
    "Philips": ("0046E6", "A0203D", "D40519", "1C5F2B"),
    "Vizio": ("00197C", "0019F8", "C838C2", "2C641F"),
    "Roku": ("B0A737", "CC6DA0", "D83134", "8C49B2", "DC3A5E"),
    "Amazon": ("FCA183", "68DBF5", "44650D", "F0272D", "0C47C9", "B0F8C2", "74C246"),
    "Apple": ("F0766F", "A85C2C", "DC2B2A", "8866A5", "F0DBF8", "6C4008", "3C0754", "A4D18C"),
    "Google": ("F4F5D8", "F88FCA", "A4775B", "D47756", "48D6D5", "6466B3", "1CF29A", "30FD38"),
    "TP-Link": ("50C7BF", "AC84C6", "9C5322", "0C8063", "1C3BF3", "60A4B7", "B04E26", "003192", "EC086B", "5CA6E6"),
    "Hikvision": ("44191C", "BC9B5E", "C05627", "4CBD8F", "ACB927"),
    "Dahua": ("3C1621", "9C8ECD", "08ED02", "E07C13"),
    "Raspberry Pi": ("B827EB", "DCA632", "E45F01", "2CCF67"),
    "Intel": ("3C9752", "94E979", "7CB27D", "8CF8C5", "A0A8CD"),
    "Microsoft": ("7C1E52", "C83F26", "4886E8", "0017FA", "502F9B"),
    "Xiaomi": ("286C07", "F8A45F", "64CC2E", "78118C", "D4970B"),
}
_OUI_LOOKUP = {p: v for v, ps in OUI.items() for p in ps}
TV_VENDORS = ("Samsung", "LG", "Sony", "Hisense", "TCL", "Panasonic", "Philips", "Vizio", "Roku")

# port -> (label, device hint)
PORTS = {
    8009: ("Google Cast", "tv"), 8008: ("Google Cast", "tv"), 7000: ("AirPlay", "tv"), 7100: ("AirPlay", "tv"),
    8001: ("Samsung TV", "tv"), 8002: ("Samsung TV", "tv"), 3000: ("LG webOS", "tv"), 3001: ("LG webOS", "tv"),
    1925: ("Philips TV", "tv"), 8060: ("Roku", "tv"), 55000: ("Panasonic VIERA", "tv"), 9197: ("DIAL", "tv"),
    554: ("RTSP", "camera"), 9100: ("Print (RAW)", "printer"), 631: ("IPP print", "printer"),
    445: ("SMB", "computer"), 22: ("SSH", "computer"), 3389: ("Remote Desktop", "computer"),
    80: ("Web", ""), 443: ("Web (TLS)", ""), 53: ("DNS", "router"),
}
_TV_PORTS = [8009, 8008, 8001, 3000, 1925, 8060, 55000, 7000]
_PROBE_PORTS = _TV_PORTS + [554, 9100, 631, 445, 22, 3389, 80, 443, 53]


def lan_ip():
    try:
        import cast
        ip = cast.lan_ip()
        if ip:
            return ip
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _subnet(ip=None):
    ip = ip or lan_ip()
    return ip.rsplit(".", 1)[0] + "."


def _ping(ip):
    cmd = ["ping", "-n", "1", "-w", "500", ip] if _WIN else ["ping", "-c", "1", "-W", "1", ip]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3, creationflags=NO_WINDOW)
        return ip if r.returncode == 0 else None
    except Exception:
        return None


def _arp_table():
    """Map IP -> MAC from the OS ARP cache (populated by the ping sweep)."""
    out = {}
    try:
        r = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
        for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}"
                             r"[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})", r.stdout or ""):
            out[m.group(1)] = m.group(2).upper().replace("-", ":")
    except Exception:
        pass
    return out


def _vendor(mac):
    if not mac:
        return ""
    pref = mac.replace(":", "").replace("-", "").upper()[:6]
    return _OUI_LOOKUP.get(pref, "")


def _hostname(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _port_open(ip, port, timeout=0.6):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def _open_ports(ip, ports=None):
    ports = ports or _PROBE_PORTS
    found = []
    with _fut.ThreadPoolExecutor(max_workers=min(len(ports), 20)) as ex:
        for port, ok in zip(ports, ex.map(lambda p: _port_open(ip, p), ports)):
            if ok:
                found.append(port)
    return found


def ssdp(timeout=3):
    """Ask the network (SSDP/UPnP) who's there. Smart TVs, media players and some speakers answer with a URL we
    can read a friendly name from. Returns {ip: {"name","model","kind","location"}}."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                break
            ip = addr[0]
            loc = re.search(rb"LOCATION:\s*(\S+)", data, re.I)
            srv = re.search(rb"SERVER:\s*(.+)", data, re.I)
            ent = found.setdefault(ip, {"name": "", "model": "", "kind": "", "location": ""})
            if loc and not ent["location"]:
                ent["location"] = loc.group(1).decode(errors="replace").strip()
            if srv and not ent["model"]:
                ent["model"] = srv.group(1).decode(errors="replace").strip()[:80]
    except Exception:
        pass
    finally:
        s.close()
    for ip, ent in found.items():          # read the device description for a friendly name
        if ent["location"]:
            try:
                xml = urlopen(ent["location"], timeout=2).read().decode(errors="replace")
                fn = re.search(r"<friendlyName>(.*?)</friendlyName>", xml, re.I | re.S)
                dt = re.search(r"<deviceType>(.*?)</deviceType>", xml, re.I | re.S)
                mf = re.search(r"<manufacturer>(.*?)</manufacturer>", xml, re.I | re.S)
                if fn:
                    ent["name"] = fn.group(1).strip()[:80]
                blob = ((dt.group(1) if dt else "") + " " + (mf.group(1) if mf else "")).lower()
                if "mediarenderer" in blob or "tv" in blob:
                    ent["kind"] = "tv"
                elif "printer" in blob:
                    ent["kind"] = "printer"
            except Exception:
                pass
    return found


def _classify(vendor, ports, ssdp_ent, host):
    """Best guess at what a device is, from the signals we have."""
    s = (ssdp_ent or {}).get("kind")
    if s == "tv" or any(p in ports for p in _TV_PORTS) or vendor in TV_VENDORS:
        label = "Smart TV"
        for p in _TV_PORTS:
            if p in ports:
                label = "Smart TV · " + PORTS[p][0]
                break
        return label
    if 554 in ports or vendor in ("Hikvision", "Dahua"):
        return "Camera"
    if 9100 in ports or 631 in ports:
        return "Printer"
    if 53 in ports or vendor == "TP-Link":
        return "Router / network"
    if 445 in ports or 3389 in ports or 22 in ports:
        return "Computer / NAS"
    if vendor in ("Apple", "Samsung", "Xiaomi") and not ports:
        return "Phone / tablet"
    if vendor == "Amazon":
        return "Amazon device"
    if vendor == "Google":
        return "Google device"
    return "Device"


def scan(timeout=3, deep=False, user=None):
    """Sweep the local subnet and return every device found, each with ip, mac, vendor, hostname, a friendly
    name (if it announced one over SSDP) and a best-guess type. `deep` also port-scans every host (slower)."""
    base = _subnet()
    gateway = base + "1"
    me = lan_ip()
    alive = []
    with _fut.ThreadPoolExecutor(max_workers=80) as ex:
        for ip in ex.map(_ping, [base + str(i) for i in range(1, 255)]):
            if ip:
                alive.append(ip)
    if me not in alive:
        alive.append(me)
    arp = _arp_table()
    sd = ssdp(timeout=timeout)
    devices = []
    for ip in sorted(alive, key=lambda x: int(x.rsplit(".", 1)[1])):
        mac = arp.get(ip, "")
        vendor = _vendor(mac)
        ent = sd.get(ip, {})
        host = _hostname(ip)
        ports = _open_ports(ip) if deep else (_open_ports(ip, _TV_PORTS + [554, 9100, 53]) if (vendor in TV_VENDORS or ent) else [])
        devices.append({
            "ip": ip, "mac": mac, "vendor": vendor, "hostname": host,
            "name": ent.get("name") or host or (vendor + " device" if vendor else ""),
            "type": _classify(vendor, ports, ent, host),
            "ports": ports, "is_you": ip == me, "is_router": ip == gateway,
        })
    return {"subnet": base + "0/24", "gateway": gateway, "count": len(devices), "devices": devices}


def lookup(target, user=None):
    """Identify one device by IP (e.g. 192.168.68.111) or by name. Pings it, reads its MAC/vendor/hostname,
    checks which service ports are open and asks it over SSDP, then says what it most likely is."""
    target = str(target or "").strip()
    ip = target
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", target):      # a name -> find its IP from a scan
        res = scan()
        t = target.lower()
        for d in res["devices"]:
            if t in (d["name"] or "").lower() or t in (d["hostname"] or "").lower() or t in (d["vendor"] or "").lower():
                ip = d["ip"]
                break
        else:
            return {"found": False, "error": f"I couldn't find a device called “{target}” on the network."}
    alive = _ping(ip) is not None
    # populate ARP for this ip
    arp = _arp_table()
    mac = arp.get(ip, "")
    vendor = _vendor(mac)
    host = _hostname(ip)
    ports = _open_ports(ip)
    sd = ssdp().get(ip, {})
    kind = _classify(vendor, ports, sd, host)
    how = _how_to_use(kind, ports, vendor, sd)
    return {"found": True, "ip": ip, "alive": alive, "mac": mac, "vendor": vendor, "hostname": host,
            "name": sd.get("name") or host or (vendor + " device" if vendor else ip), "model": sd.get("model", ""),
            "type": kind, "open_ports": [{"port": p, "service": PORTS.get(p, ("?", ""))[0]} for p in ports],
            "how": how}


def _how_to_use(kind, ports, vendor, sd):
    if kind.startswith("Smart TV"):
        if 8009 in ports or 8008 in ports:
            return ("This is a Google Cast TV (Chromecast built-in / Android TV). Trillion can cast to it and "
                    "control it with the cast tools — try “list cast devices” then “play … on the TV”.")
        return ("This looks like a smart TV. Trillion can wake it with Wake-on-LAN, and cast to it if it has "
                "Chromecast built-in. Volume/app control needs the TV's own protocol, which can be added next.")
    if kind == "Camera":
        return "An IP camera. Add it under Cameras with its RTSP URL to view and snapshot it."
    if kind == "Printer":
        return "A network printer."
    if kind == "Router / network":
        return "Your router / mesh node. If the TP-Link Deco is set up, Trillion can list all its clients."
    return "A device on your network."


def wake(target, mac=None, user=None):
    """Send a Wake-on-LAN magic packet to turn a device on (works if the device has WoL enabled). Give a MAC,
    or an IP/name and Trillion will look its MAC up first."""
    if not mac:
        info = lookup(target, user=user)
        if not info.get("found"):
            return info
        mac = info.get("mac")
    if not mac:
        return {"ok": False, "error": "No MAC address found for that device, so I can't send a wake packet."}
    hexmac = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hexmac) != 12:
        return {"ok": False, "error": f"That MAC ({mac}) doesn't look right."}
    packet = bytes.fromhex("FF" * 6 + hexmac * 16)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, ("255.255.255.255", 9))
        s.close()
        return {"ok": True, "mac": mac, "sent": "Wake-on-LAN packet sent."}
    except Exception as e:
        return {"ok": False, "error": f"Could not send the wake packet ({type(e).__name__})."}
