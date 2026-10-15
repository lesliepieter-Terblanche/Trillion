"""Trillion phones you, for free: an internet call from this PC to the Linphone app on your phone, over Tailscale.

No phone number, no server and no charges: Trillion sends the call (SIP) straight to the phone's Tailscale address,
the phone rings like a normal call, and the sound travels as ordinary phone-quality audio (G.711). Trillion speaks
with the PC's own voice (or ElevenLabs when that is set up) and hears you with the same speech recognition as the
dashboard. Anything you ask for goes through the normal chat, so whatever needs your OK still waits for it.
"""
import io
import os
import random
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid

import numpy as np

RATE = 8000
FRAME = 160                       # 20 ms at 8 kHz
RING_SECONDS = 40
MAX_TURNS = 4
LISTEN_MAX = 15                   # seconds to wait for an answer
END_SILENCE = 1.2                 # seconds of quiet that end what you said
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BUSY = {}                         # user id -> True while a call is on


class CallError(Exception):
    pass


# ---------- G.711 ----------
def ulaw_encode(pcm):
    """16-bit samples -> mu-law bytes."""
    x = np.clip(pcm.astype(np.int32), -32635, 32635)
    sign = (x < 0).astype(np.int32) << 7
    x = np.abs(x) + 132
    exp = np.floor(np.log2(np.maximum(x, 1))).astype(np.int32) - 7
    exp = np.clip(exp, 0, 7)
    mant = (x >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mant) & 0xFF).astype(np.uint8).tobytes()


def ulaw_decode(data):
    u = ~np.frombuffer(data, dtype=np.uint8).astype(np.int32) & 0xFF
    sign, exp, mant = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    x = (((mant << 3) + 132) << exp) - 132
    return np.where(sign != 0, -x, x).astype(np.int16)


def alaw_decode(data):
    a = np.frombuffer(data, dtype=np.uint8).astype(np.int32) ^ 0x55
    sign, exp, mant = a & 0x80, (a >> 4) & 0x07, a & 0x0F
    x = np.where(exp == 0, (mant << 4) + 8, ((mant << 4) + 0x108) << np.maximum(exp - 1, 0))
    return np.where(sign != 0, x, -x).astype(np.int16)


def alaw_encode(pcm):
    x = np.clip(pcm.astype(np.int32), -32767, 32767)
    sign = np.where(x >= 0, 0x80, 0)
    x = np.abs(x)
    exp = np.clip(np.floor(np.log2(np.maximum(x, 1))).astype(np.int32) - 7, 0, 7)
    mant = np.where(exp == 0, x >> 4, (x >> (exp + 3))) & 0x0F
    return ((sign | (exp << 4) | mant) ^ 0x55).astype(np.uint8).tobytes()


# ---------- the voice ----------
def synth(text, user=None):
    """Words -> 8 kHz 16-bit samples. ElevenLabs if that login uses it, otherwise Windows' own voice."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:900]
    if not text:
        return np.zeros(0, dtype=np.int16)
    if user is not None:
        try:
            import core, httpx
            s = core.settings(user)
            key = core.get_secret(user["id"], "elevenlabs") if s.get("voice") == "elevenlabs" else None
            if key:
                r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s.get('eleven_voice_id') or '21m00Tcm4TlvDq8ikWAM'}",
                               params={"output_format": "pcm_16000"}, headers={"xi-api-key": key}, timeout=60,
                               json={"text": text, "model_id": "eleven_turbo_v2_5"})
                if r.status_code == 200 and r.content:
                    return np.frombuffer(r.content, dtype=np.int16)[::2].copy()
        except Exception:
            pass
    if os.name == "nt":
        out = os.path.join(tempfile.mkdtemp(), "say.wav")
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(8000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
              "[System.Speech.AudioFormat.AudioChannel]::Mono); $s.Rate = 0; "
              f"$s.SetOutputToWaveFile('{out}', $f); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text, capture_output=True, text=True, timeout=60,
                       creationflags=NO_WINDOW)
        try:
            import wave
            with wave.open(out, "rb") as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).copy()
        except Exception:
            raise CallError("The PC's voice did not work.")
    raise CallError("No voice is available on this PC.")


def hear(samples):
    """8 kHz samples -> words, with the speech recognition the dashboard uses."""
    import app
    w = app.load_whisper()
    audio = np.repeat(samples.astype(np.float32) / 32768.0, 2)          # 8 kHz -> 16 kHz
    if audio.size < 8000:
        return ""
    segs, _ = w.transcribe(audio, vad_filter=True, language="en")
    return " ".join(s.text.strip() for s in segs).strip()


# ---------- one call ----------
class Call:
    def __init__(self, target, port=5060, my_ip=None, codec="auto"):
        self.target, self.port = target, int(port)
        self.my_ip = my_ip or self._route_ip(target)
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind(("0.0.0.0", 0))
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("0.0.0.0", 0))
        self.sip_port, self.rtp_port = self.sip.getsockname()[1], self.rtp.getsockname()[1]
        self.call_id = uuid.uuid4().hex + "@trillion"
        self.tag, self.branch = uuid.uuid4().hex[:10], "z9hG4bK" + uuid.uuid4().hex[:16]
        self.to_tag, self.remote_rtp, self.pt = "", None, 0
        self.inbox, self.ended, self.lock = [], threading.Event(), threading.Lock()
        self.seq, self.ts, self.ssrc = random.randint(0, 65535), random.randint(0, 2 ** 31), random.randint(0, 2 ** 31)
        self.out_q, self.state = [], "new"
        self.peer_bye = False

    @staticmethod
    def _route_ip(target):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 9))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def _sdp(self):
        return (f"v=0\r\no=trillion {int(time.time())} 1 IN IP4 {self.my_ip}\r\ns=Trillion\r\nc=IN IP4 {self.my_ip}\r\nt=0 0\r\n"
                f"m=audio {self.rtp_port} RTP/AVP 0 8 101\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n"
                f"a=rtpmap:101 telephone-event/8000\r\na=ptime:20\r\na=sendrecv\r\n")

    def _msg(self, method, cseq, body="", branch=None, to_tag=True):
        to = f"<sip:{self.target}:{self.port}>" + (f";tag={self.to_tag}" if to_tag and self.to_tag else "")
        lines = [f"{method} sip:{self.target}:{self.port} SIP/2.0",
                 f"Via: SIP/2.0/UDP {self.my_ip}:{self.sip_port};branch={branch or self.branch};rport",
                 "Max-Forwards: 70", f'From: "Trillion" <sip:trillion@{self.my_ip}>;tag={self.tag}', f"To: {to}",
                 f"Call-ID: {self.call_id}", f"CSeq: {cseq} {method}", f"Contact: <sip:trillion@{self.my_ip}:{self.sip_port}>",
                 "User-Agent: Trillion", "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS"]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()

    def _send_sip(self, data):
        self.sip.sendto(data, (self.target, self.port))

    def _parse(self, data):
        text = data.decode("utf-8", "replace")
        head, _, body = text.partition("\r\n\r\n")
        first, *hdrs = head.split("\r\n")
        h = {}
        for ln in hdrs:
            if ":" in ln:
                k, v = ln.split(":", 1)
                h.setdefault(k.strip().lower(), v.strip())
        return first, h, body

    def dial(self, ring_seconds=RING_SECONDS, on_ring=None):
        """Ring the phone. Returns 'answered', 'busy', 'declined', 'no answer' or 'unreachable'."""
        invite = self._msg("INVITE", 1, self._sdp(), to_tag=False)
        self._send_sip(invite)
        self.state = "calling"
        start, last_tx, gap, provisional = time.time(), time.time(), 0.5, False
        self.sip.settimeout(0.25)
        while time.time() - start < ring_seconds:
            try:
                data, addr = self.sip.recvfrom(65535)
            except socket.timeout:
                if not provisional and time.time() - last_tx > gap:
                    if time.time() - start > 8:
                        return "unreachable"
                    self._send_sip(invite)
                    last_tx, gap = time.time(), min(gap * 2, 4)
                continue
            except OSError:
                return "unreachable"
            first, h, body = self._parse(data)
            m = re.match(r"SIP/2\.0 (\d{3})", first)
            if not m:
                if first.startswith("BYE"):
                    self._reply_ok(data)
                    return "declined"
                continue
            code = int(m.group(1))
            if "INVITE" not in h.get("cseq", ""):
                continue
            tt = re.search(r";\s*tag=([^;>\s]+)", h.get("to", ""))
            if tt:
                self.to_tag = tt.group(1)
            if code < 200:
                provisional = True
                if code in (180, 183) and on_ring:
                    on_ring()
                continue
            if code >= 300:
                self._send_sip(self._ack(code))
                return {486: "busy", 600: "busy", 603: "declined", 480: "no answer", 408: "no answer", 487: "no answer"}.get(code, "declined")
            # 200 OK: where to send the sound, and which kind
            c = re.search(r"^c=IN IP4 (\S+)", body, re.M)
            mm = re.search(r"^m=audio (\d+) RTP/AVP ([\d ]+)", body, re.M)
            if not mm:
                self._send_sip(self._ack(200))
                self.hangup()
                raise CallError("The phone answered without sound.")
            pts = [int(x) for x in mm.group(2).split()]
            self.pt = 0 if 0 in pts else (8 if 8 in pts else -1)
            if self.pt < 0:
                self._send_sip(self._ack(200))
                self.hangup()
                raise CallError("The phone does not offer G.711 (PCMU/PCMA). Switch it on in Linphone's audio codecs.")
            self.remote_rtp = (c.group(1) if c else self.target, int(mm.group(1)))
            self._send_sip(self._ack(200))
            self.state = "talking"
            threading.Thread(target=self._rtp_loop, daemon=True).start()
            threading.Thread(target=self._sip_loop, daemon=True).start()
            return "answered"
        self._send_sip(self._msg("CANCEL", 1, branch=self.branch, to_tag=False))
        return "no answer"

    def _ack(self, code):
        branch = self.branch if code >= 300 else "z9hG4bK" + uuid.uuid4().hex[:16]
        return self._msg("ACK", 1, branch=branch)

    def _reply_ok(self, data):
        first, h, _ = self._parse(data)
        resp = ["SIP/2.0 200 OK"] + [f"{k}: {h[k]}" for k in ("via", "from", "to", "call-id", "cseq") if k in h] + ["Content-Length: 0"]
        self._send_sip(("\r\n".join(resp) + "\r\n\r\n").encode())

    def _sip_loop(self):
        while not self.ended.is_set():
            try:
                data, _ = self.sip.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            first, h, _ = self._parse(data)
            if first.startswith("BYE"):
                self._reply_ok(data)
                self.peer_bye = True
                self.ended.set()
            elif first.startswith(("OPTIONS", "INFO")):
                self._reply_ok(data)

    def _rtp_loop(self):
        self.rtp.settimeout(0.005)
        enc = ulaw_encode if self.pt == 0 else alaw_encode
        dec = ulaw_decode if self.pt == 0 else alaw_decode
        silence = enc(np.zeros(FRAME, dtype=np.int16))
        nxt = time.time()
        while not self.ended.is_set():
            with self.lock:
                chunk = self.out_q.pop(0) if self.out_q else None
            payload = enc(chunk) if chunk is not None else silence
            hdr = struct.pack("!BBHII", 0x80, self.pt, self.seq & 0xFFFF, self.ts & 0xFFFFFFFF, self.ssrc)
            try:
                self.rtp.sendto(hdr + payload, self.remote_rtp)
            except OSError:
                pass
            self.seq += 1
            self.ts += FRAME
            nxt += 0.02
            while time.time() < nxt:
                try:
                    data, _ = self.rtp.recvfrom(2048)
                    if len(data) > 12 and (data[1] & 0x7F) in (0, 8):
                        body = data[12 + 4 * (data[0] & 0x0F):]
                        with self.lock:
                            self.inbox.append(dec(body))
                except (socket.timeout, BlockingIOError):
                    pass
                except OSError:
                    break

    def say(self, samples):
        """Play samples to the phone and wait until they have been sent."""
        frames = [samples[i:i + FRAME] for i in range(0, len(samples), FRAME)]
        if frames and len(frames[-1]) < FRAME:
            frames[-1] = np.pad(frames[-1], (0, FRAME - len(frames[-1])))
        with self.lock:
            self.out_q.extend(frames)
        while not self.ended.is_set():
            with self.lock:
                if not self.out_q:
                    break
            time.sleep(0.05)
        with self.lock:
            self.inbox.clear()                                           # what came in while talking is our own echo

    def listen(self, max_seconds=LISTEN_MAX, end_silence=END_SILENCE):
        """Collect what the person says: from when they start speaking until they stop."""
        got, start, talking, quiet_since = [], time.time(), False, None
        floor = 600
        while not self.ended.is_set() and time.time() - start < max_seconds:
            time.sleep(0.05)
            with self.lock:
                frames, self.inbox = self.inbox, []
            for f in frames:
                level = float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) if len(f) else 0.0
                if level > floor:
                    talking, quiet_since = True, None
                elif talking and quiet_since is None:
                    quiet_since = time.time()
                if talking:
                    got.append(f)
            if talking and quiet_since and time.time() - quiet_since > end_silence:
                break
        return np.concatenate(got) if got else np.zeros(0, dtype=np.int16)

    def hangup(self):
        if self.state == "talking" and not self.peer_bye:
            try:
                self._send_sip(self._msg("BYE", 2, branch="z9hG4bK" + uuid.uuid4().hex[:16]))
            except OSError:
                pass
        self.ended.set()
        self.state = "ended"
        time.sleep(0.1)
        for s in (self.sip, self.rtp):
            try:
                s.close()
            except OSError:
                pass


# ---------- what a login sets up ----------
def prefs(user):
    import core
    s = core.settings(user).get("voicecall") or {}
    return {"on": bool(s.get("on")), "target": str(s.get("target") or ""), "port": int(s.get("port") or 5060),
            "name": str(s.get("name") or "")}


def save_prefs(user, data):
    import core
    row = core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    st = core.settings(row)
    old = prefs(row)
    target = str(data.get("target", old["target"]) or "").strip()
    if target and not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9.-]+", target):
        raise CallError("That does not look like a Tailscale address (100.x.x.x) or device name.")
    try:
        port = int(data.get("port", old["port"]) or 5060)
    except (TypeError, ValueError):
        port = 5060
    st["voicecall"] = {"on": bool(data.get("on", old["on"])), "target": target, "port": max(1, min(port, 65535)),
                       "name": str(data.get("name", old["name"]) or "")[:60]}
    core.save_settings(user["id"], st)
    return prefs(core.q("SELECT * FROM users WHERE id=?", (user["id"],), one=True))


def _resolve(target):
    try:
        return socket.gethostbyname(target)
    except OSError:
        raise CallError(f"I could not find {target} on Tailscale. Is Tailscale on, on the phone?")


def conversation(user, opening, answer=None, my_ip=None, log=None):
    """Ring the login's Linphone, say the opening, then take up to a few requests by voice.
    answer(text) -> what to say back (the normal chat). Returns how it went."""
    ip, port = where(user)
    if BUSY.get(user["id"]):
        raise CallError("I am already on a call with you.")
    BUSY[user["id"]] = True
    heard = []
    try:
        call = Call(ip, port, my_ip)
        first = synth(opening, user)                       # ready before the phone rings, so there is no pause
        result = call.dial()
        if result != "answered":
            return {"result": result, "heard": heard}
        try:
            time.sleep(0.6)
            call.say(first)
            for _ in range(MAX_TURNS):
                words = hear(call.listen()) if not call.ended.is_set() else ""
                if call.ended.is_set():
                    break
                if not words:
                    call.say(synth("I did not hear anything, so I will leave it on your phone. Bye.", user))
                    break
                heard.append(words)
                if log:
                    log(words)
                if re.search(r"\b(bye|goodbye|that'?s (all|it)|nothing|no thanks|no thank you|okay thanks|thanks,? bye|got it)\b", words, re.I) \
                        and len(words) < 40:
                    call.say(synth("Okay. Bye.", user))
                    break
                call.say(synth("One moment.", user))
                reply = answer(words) if answer else "Noted."
                reply = re.sub(r"https?://\S+", "the link on your dashboard", str(reply or "Done."))[:600]
                call.say(synth(reply + " Anything else?", user))
            return {"result": "answered", "heard": heard}
        finally:
            call.hangup()
    finally:
        BUSY.pop(user["id"], None)


# ---------- calls at set times ----------
def _plans_table():
    import core
    core.x("""CREATE TABLE IF NOT EXISTS call_plans(id INTEGER PRIMARY KEY, user_id INTEGER, at TEXT, days TEXT, topic TEXT,
              once_date TEXT DEFAULT '', last TEXT DEFAULT '', created REAL)""")


DAY_SETS = {"daily": "0123456", "weekdays": "01234", "weekends": "56"}
WEEKDAY = {"monday": "0", "tuesday": "1", "wednesday": "2", "thursday": "3", "friday": "4", "saturday": "5", "sunday": "6"}


def plans(user):
    import core
    _plans_table()
    return [dict(r) for r in core.q("SELECT * FROM call_plans WHERE user_id=? ORDER BY at", (user["id"],))]


def add_plan(user, at, days="daily", topic="projects", once_date=""):
    import core
    _plans_table()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", at or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise CallError("Give a time like 08:00.")
    days = days if (days in DAY_SETS or re.fullmatch(r"[0-6]{1,7}", days or "")) else "daily"
    pid = core.x("INSERT INTO call_plans(user_id,at,days,topic,once_date,created) VALUES(?,?,?,?,?,?)",
                 (user["id"], f"{int(m.group(1)):02d}:{m.group(2)}", days, (topic or "projects")[:200], once_date, time.time()))
    return pid


def delete_plan(user, pid):
    import core
    _plans_table()
    core.x("DELETE FROM call_plans WHERE id=? AND user_id=?", (int(pid), user["id"]))


def due_plans(user, now):
    """Plans whose time is now (within 20 minutes, in case the PC was busy) and that have not run today."""
    import core
    _plans_table()
    out = []
    today, hm = now.date().isoformat(), now.strftime("%H:%M")
    for p in plans(user):
        if p["last"] == today:
            continue
        if p["once_date"] and p["once_date"] != today:
            continue
        days = DAY_SETS.get(p["days"], p["days"])
        if str(now.weekday()) not in days:
            continue
        h, m = map(int, p["at"].split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if 0 <= (now - start).total_seconds() < 1200:
            out.append(p)
    return out


def mark_ran(plan_id, today, once=False):
    import core
    if once:
        core.x("DELETE FROM call_plans WHERE id=?", (plan_id,))
    else:
        core.x("UPDATE call_plans SET last=? WHERE id=?", (today, plan_id))


CALL_ME = re.compile(r"\b(?:call|phone|ring) me\b", re.I)
AT = re.compile(r"\b(?:at|@)\s*(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?\b", re.I)


def parse(text):
    """'call me at 8am every weekday and give me a breakdown of current projects' -> plan; 'call me' -> now."""
    if len(text) > 200:
        return None
    if re.search(r"\b(don'?t|do not|stop|never)\b[^.?!]{0,10}\b(call|ring|phon)(ing|e)?\b[^.?!]{0,6}\bme\b", text, re.I):
        return {"stop": True}
    if not CALL_ME.search(text):
        return None
    a = AT.search(text)
    topic = "projects"
    t = re.search(r"\b(?:and|to|with)\s+(?:give me |tell me |talk (?:me )?through |go through |update me on |remind me (?:about |of |to )?)?"
                  r"(?:a |the )?(.+)$", text[CALL_ME.search(text).end():], re.I)
    if t:
        words = t.group(1).strip(" .?!")
        if not re.search(r"\b(projects?|breakdown|update|status|briefing|summary|what'?s outstanding|everything)\b", words, re.I):
            topic = words[:200]
    if not a:
        return {"now": True, "topic": topic}
    h, mi, ap = int(a.group(1)), int(a.group(2) or 0), (a.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    days, once = "daily", ""
    low = text.lower()
    if re.search(r"\bweek ?days?\b|\bmonday to friday\b|\bworkdays?\b", low):
        days = "weekdays"
    elif re.search(r"\bweekends?\b", low):
        days = "weekends"
    elif re.search(r"\b(tomorrow|today|tonight|once)\b", low) and not re.search(r"\bevery\b|\bdaily\b|\beach\b", low):
        import datetime
        d = datetime.date.today() + (datetime.timedelta(days=1) if "tomorrow" in low else datetime.timedelta(0))
        once = d.isoformat()
    else:
        named = [WEEKDAY[w] for w in WEEKDAY if re.search(rf"\b{w}s?\b", low)]
        if named:
            days = "".join(sorted(set(named)))
    return {"at": f"{h:02d}:{mi:02d}", "days": days, "once": once, "topic": topic}


# ---------- the login point: Linphone signs in to Trillion, so Trillion always knows where the phone is ----------
SIP_PORT = int(os.environ.get("TRILLION_SIP_PORT", 5060))
REG = {}                                   # user id -> {"ip", "port", "expires", "agent", "seen", "user"}
REGISTRAR = {"running": False, "error": "", "last": None}       # last: the latest sign-in attempt seen, and what happened


def _allowed_source(ip):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_loopback or a in ipaddress.ip_network("100.64.0.0/10")        # this PC, or your own Tailscale devices


def _user_for(name):
    import core
    name = (name or "").strip().lower()
    for u in core.q("SELECT * FROM users"):
        if u["name"].lower() == name:
            return u
    return None


def _seen(addr, what):
    REGISTRAR["last"] = {"t": time.time(), "from": addr[0], "what": what}


def _handle_sip(sock, data, addr):
    text = data.decode("utf-8", "replace")
    head, _, _ = text.partition("\r\n\r\n")
    first, *lines = head.split("\r\n")
    method = first.split(" ", 1)[0]
    if method not in ("REGISTER", "OPTIONS"):
        return
    h = {}
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            k = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact"}.get(k.strip().lower(), k.strip().lower())
            h.setdefault(k, v.strip())
    to = h.get("to", "")
    if ";tag=" not in to:
        to += ";tag=" + uuid.uuid4().hex[:8]
    via = h.get("via", "")
    if "rport" in via and "rport=" not in via:
        via = via.replace("rport", f"rport={addr[1]};received={addr[0]}", 1)
    base = [f"Via: {via}", f"From: {h.get('from', '')}", f"To: {to}", f"Call-ID: {h.get('call-id', '')}", f"CSeq: {h.get('cseq', '')}",
            "Server: Trillion"]

    def reply(code, reason, extra=()):
        sock.sendto(("\r\n".join([f"SIP/2.0 {code} {reason}"] + base + list(extra) + ["Content-Length: 0"]) + "\r\n\r\n").encode(), addr)

    if method == "OPTIONS":
        reply(200, "OK", ["Allow: INVITE, ACK, BYE, CANCEL, OPTIONS, REGISTER"])
        return
    if not _allowed_source(addr[0]):
        _seen(addr, f"refused: {addr[0]} is not a Tailscale address. Use the PC's 100.x address as the domain.")
        reply(403, "Forbidden")
        return
    m = re.search(r"sip:([^@;>\s]+)@", h.get("to", ""))
    user = _user_for(m.group(1) if m else "")
    if not user:
        _seen(addr, f"refused: there is no Trillion login called '{m.group(1) if m else ''}'. Use your login name, e.g. pieter.")
        reply(404, "Not Found")                     # the user name must be the Trillion login name, e.g. pieter
        return
    contact = h.get("contact", "")
    exp = re.search(r"expires=(\d+)", contact) or re.search(r"(\d+)", h.get("expires", ""))
    expires = int(exp.group(1)) if exp else 3600
    if contact.strip() == "*" or expires == 0:
        REG.pop(user["id"], None)
        reply(200, "OK")
        return
    cm = re.search(r"sip:(?:[^@;>]+@)?([^:;>]+)(?::(\d+))?", contact)
    ip = addr[0]                                   # where the phone really is (it may sit behind a router)
    port = addr[1] if "rport" in h.get("via", "") or not cm else int(cm.group(2) or 5060)
    expires = max(60, min(expires, 3600))
    _seen(addr, f"signed in as {user['name'].lower()}")
    REG[user["id"]] = {"ip": ip, "port": port, "expires": time.time() + expires, "agent": h.get("user-agent", "")[:60],
                       "seen": time.time(), "user": user["name"]}
    reply(200, "OK", [f"Contact: {contact.split(';expires')[0] if contact else '<sip:' + ip + '>'};expires={expires}", f"Expires: {expires}"])


def run_registrar():
    """Listen for Linphone signing in. Runs for as long as Trillion runs."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", SIP_PORT))
    except OSError as e:
        REGISTRAR.update(running=False, error=f"Port {SIP_PORT} is in use by another program ({e.strerror}).")
        return
    REGISTRAR.update(running=True, error="", port=SIP_PORT)
    while True:
        try:
            data, addr = sock.recvfrom(65535)
            _handle_sip(sock, data, addr)
        except Exception:
            time.sleep(0.05)


def registered(user):
    r = REG.get(user["id"])
    return r if r and r["expires"] > time.time() else None


def where(user):
    """Where to ring: the phone's own sign-in if it is signed in, otherwise the address saved on the Phone page."""
    r = registered(user)
    if r:
        return r["ip"], r["port"]
    p = prefs(user)
    if not p["target"]:
        raise CallError("Your phone is not signed in to Trillion yet. In Linphone use user name "
                        f"{user['name'].lower()}, domain this PC's Tailscale address, transport UDP.")
    return _resolve(p["target"]), p["port"]
