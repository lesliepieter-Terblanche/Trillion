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
FRAME = 160                       # 20 ms at 8 kHz (the old telephone sound)
VOICE_RATE = 16000                # Trillion's voice is made at 16 kHz; wideband calls (G.722) carry it as it is
CALL_MODEL = os.environ.get("TRILLION_CALL_WHISPER", "large-v3")   # full model: better with accents on call audio
_CALL_W = {"m": None, "tried": False}
RING_SECONDS = 40
MAX_TURNS = 4
LISTEN_MAX = 15                   # seconds to wait for an answer
END_SILENCE = 1.5                 # seconds of quiet that end what you said
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BUSY = {}                         # user id -> {"t": when it started, "call": the call} while a call is on
MAX_CALL = 600                    # a call never lasts longer than ten minutes
IN_TURNS = 12                     # when you call Trillion, you can ask for up to twelve things in one call
ON_INCOMING = None                # set by the app: what to do when you phone Trillion (user, call) -> None


def busy(uid):
    """True only while a call is really on: a call that ended, or one stuck for too long, does not count."""
    b = BUSY.get(uid)
    if not b:
        return False
    c = b.get("call")
    if (c is not None and c.ended.is_set()) or time.time() - b["t"] > MAX_CALL + 60:
        BUSY.pop(uid, None)
        return False
    return True


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
            if s.get("voice") == "kokoro":
                try:
                    import localvoice
                    samples, sr = localvoice.synth(text, s.get("kokoro_voice"))
                    if len(samples):
                        return localvoice.pcm16k(samples, sr)
                except Exception:
                    pass
            key = core.get_secret(user["id"], "elevenlabs") if s.get("voice") == "elevenlabs" else None
            if key:
                r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s.get('eleven_voice_id') or '21m00Tcm4TlvDq8ikWAM'}",
                               params={"output_format": "pcm_16000"}, headers={"xi-api-key": key}, timeout=60,
                               json={"text": text, "model_id": "eleven_turbo_v2_5"})
                if r.status_code == 200 and r.content:
                    return np.frombuffer(r.content, dtype=np.int16).copy()
        except Exception:
            pass
    if os.name == "nt":
        out = os.path.join(tempfile.mkdtemp(), "say.wav")
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
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


def resample(x, src, dst):
    if src == dst or not len(x):
        return x.astype(np.int16)
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


def call_model():
    """A stronger speech model for phone calls (better with accents and phone sound); the dashboard's one if it cannot load."""
    import app
    if _CALL_W["m"] is not None:
        return _CALL_W["m"]
    if not _CALL_W["tried"]:                      # never wait for it during a call: load it in the background
        threading.Thread(target=warm_call_model, daemon=True).start()
    return app.load_whisper()


def warm_call_model():
    """Load (the first time: download, about 1.6 GB) the stronger call model. Run in the background at start-up."""
    if _CALL_W["tried"]:
        return
    _CALL_W["tried"] = True
    try:
        import app, core
        from faster_whisper import WhisperModel
        if core.gpu():
            app._cuda_dlls()
            _CALL_W["m"] = WhisperModel(CALL_MODEL, device="cuda", compute_type="int8_float16")
    except Exception:
        _CALL_W["m"] = None


def hints(user):
    """Names and words the speech model should expect, so they are heard right."""
    words = ["Trillion", "Pieter", "Brandon", "Lewellyn", "TrueHome", "TackleTrail", "VapeEssence", "Gumroad", "Payhip",
             "WhatsApp", "Linphone", "invoice", "approve", "remind me", "call back", "outstanding",
             # South African places and work vocabulary, so the call hears local names right (narrowband audio
             # otherwise turns "Roodepoort" into "Roydepoort").
             "Roodepoort", "Johannesburg", "Joburg", "Pretoria", "Centurion", "Midrand", "Sandton", "Randburg",
             "Krugersdorp", "Gauteng", "Cape Town", "Durban", "South Africa", "SADC", "rand",
             "Westcon-Comstor", "Juniper", "SonarSource", "Avaya", "Nokia"]
    try:
        import core
        st = core.settings(user) if user else {}
        words = [user["name"], st.get("brand") or "", st.get("assistant_name") or ""] + words
    except Exception:
        pass
    return "Phone call with " + ", ".join(dict.fromkeys(w for w in words if w)) + "."


def hear(samples, rate=RATE, user=None):
    """What was said on the call, as words."""
    audio = resample(samples, rate, 16000).astype(np.float32) / 32768.0
    if audio.size < 8000:
        return ""
    w = call_model()
    segs, _ = w.transcribe(audio, language="en", beam_size=5, vad_filter=True, initial_prompt=hints(user),
                           condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    if not text:
        segs, _ = w.transcribe(audio, language="en", beam_size=5, vad_filter=False, initial_prompt=hints(user))
        text = " ".join(s.text.strip() for s in segs).strip()
    return "" if text.lower().startswith("phone call with") else text


def spoken(text, limit=320):
    """A chat answer made fit to be heard: no lists or symbols, two or three sentences."""
    t = re.sub(r"https?://\S+", "the link on your dashboard", str(text or ""))
    t = re.sub(r"^\s*[-*\u2022]\s*", "", t, flags=re.M)
    t = re.sub(r"[*_#`>|]", "", t)
    t = re.sub(r"\s*\n+\s*", ". ", t).replace("..", ".")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + ".") + " The rest is in the chat."


G722 = 9


def g722_codec():
    """Wideband (16 kHz) sound in and out, through the audio library Trillion already has."""
    import av
    e = av.CodecContext.create("g722", "w")
    e.sample_rate, e.format, e.layout = 16000, "s16", "mono"
    d = av.CodecContext.create("g722", "r")
    d.sample_rate, d.layout = 16000, "mono"

    def enc(pcm):
        f = av.AudioFrame.from_ndarray(pcm.astype(np.int16).reshape(1, -1), format="s16", layout="mono")
        f.sample_rate = 16000
        return b"".join(bytes(p) for p in e.encode(f))

    def dec(payload):
        out = [fr.to_ndarray().reshape(-1) for fr in d.decode(av.Packet(payload))]
        return np.concatenate(out).astype(np.int16) if out else np.zeros(0, dtype=np.int16)
    return enc, dec


# ---------- one call ----------
class Call:
    def __init__(self, target, port=5060, my_ip=None, codec="auto", shared=None, user_part=""):
        """shared: the socket the phone signed in on (the call goes out the way the phone already trusts)."""
        import queue
        self.target, self.port, self.user_part = target, int(port), user_part
        self.my_ip = my_ip or self._route_ip(target)
        self.call_id = uuid.uuid4().hex + "@trillion"
        self.shared = shared
        if shared is not None:
            self.sip = shared
            self.q = queue.Queue()
            CALLQ[self.call_id] = self.q
        else:
            self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sip.bind(("0.0.0.0", 0))
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind(("0.0.0.0", 0))
        self.sip_port, self.rtp_port = self.sip.getsockname()[1], self.rtp.getsockname()[1]
        self.tag, self.branch = uuid.uuid4().hex[:10], "z9hG4bK" + uuid.uuid4().hex[:16]
        self.to_tag, self.remote_rtp, self.pt, self.rate = "", None, 0, 8000
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
                f"m=audio {self.rtp_port} RTP/AVP 9 0 8 101\r\na=rtpmap:9 G722/8000\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n"
                f"a=rtpmap:101 telephone-event/8000\r\na=ptime:20\r\na=sendrecv\r\n")

    def _msg(self, method, cseq, body="", branch=None, to_tag=True):
        who = f"{self.user_part}@" if self.user_part else ""
        to = f"<sip:{who}{self.target}:{self.port}>" + (f";tag={self.to_tag}" if to_tag and self.to_tag else "")
        lines = [f"{method} sip:{who}{self.target}:{self.port};transport=udp SIP/2.0",
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

    def _recv(self, timeout=0.25):
        """The next SIP message for this call. Raises socket.timeout when there is none yet."""
        if self.shared is not None:
            import queue
            try:
                return self.q.get(timeout=timeout)
            except queue.Empty:
                raise socket.timeout()
        self.sip.settimeout(timeout)
        return self.sip.recvfrom(65535)

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
        while time.time() - start < ring_seconds:
            try:
                data, addr = self._recv(0.25)
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
            self.pt = next((p for p in pts if p in (G722, 0, 8)), -1)
            if self.pt == G722:
                try:
                    import av  # noqa: F401  (wideband needs the audio library; telephone sound otherwise)
                except ImportError:
                    self.pt = 0 if 0 in pts else (8 if 8 in pts else -1)
            self.rate = 16000 if self.pt == G722 else 8000
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
        names = {"via": "Via", "from": "From", "to": "To", "call-id": "Call-ID", "cseq": "CSeq"}
        resp = ["SIP/2.0 200 OK"] + [f"{names[k]}: {h[k]}" for k in names if k in h] + ["Content-Length: 0"]
        self._send_sip(("\r\n".join(resp) + "\r\n\r\n").encode())

    def _sip_loop(self):
        while not self.ended.is_set():
            try:
                data, _ = self._recv(0.5)
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
        frame = self.rate // 50
        if self.pt == G722:
            enc, dec = g722_codec()
        else:
            enc = ulaw_encode if self.pt == 0 else alaw_encode
            dec = ulaw_decode if self.pt == 0 else alaw_decode
        silence = enc(np.zeros(frame, dtype=np.int16))
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
            self.ts += FRAME                      # the clock runs at 8 kHz for G.722 too, as the standard says
            nxt += 0.02
            while time.time() < nxt:
                try:
                    data, _ = self.rtp.recvfrom(2048)
                    if len(data) > 12 and (data[1] & 0x7F) == self.pt:
                        body = data[12 + 4 * (data[0] & 0x0F):]
                        with self.lock:
                            self.inbox.append(dec(body))
                except (socket.timeout, BlockingIOError):
                    pass
                except OSError:
                    break

    def say(self, samples, rate=VOICE_RATE):
        """Play samples (made at `rate`) to the phone and wait until they have been sent."""
        samples = resample(samples, rate, self.rate)
        fr = self.rate // 50
        frames = [samples[i:i + fr] for i in range(0, len(samples), fr)]
        if frames and len(frames[-1]) < fr:
            frames[-1] = np.pad(frames[-1], (0, fr - len(frames[-1])))
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
        before, noise = [], []                    # the moment before speech starts is kept, so the first word is not cut
        floor = 500
        while not self.ended.is_set() and time.time() - start < max_seconds:
            time.sleep(0.05)
            with self.lock:
                frames, self.inbox = self.inbox, []
            for f in frames:
                level = float(np.sqrt(np.mean(f.astype(np.float32) ** 2))) if len(f) else 0.0
                if not talking and len(noise) < 15 and level < 1500:          # quiet frames only: speech is never "noise"
                    noise.append(level)
                    floor = min(1500.0, max(500.0, 3.0 * float(np.median(noise))))   # set by the line's own background hiss
                if level > floor:
                    if not talking:
                        got.extend(before)
                    talking, quiet_since = True, None
                elif talking and quiet_since is None:
                    quiet_since = time.time()
                elif talking and level > floor * 0.6:
                    quiet_since = None
                if talking:
                    got.append(f)
                else:
                    before = (before + [f])[-15:]
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
        CALLQ.pop(self.call_id, None)
        for s in ((self.rtp,) if self.shared is not None else (self.sip, self.rtp)):
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
    reg = registered(user)
    if busy(user["id"]):
        raise CallError("I am already on a call with you.")
    BUSY[user["id"]] = {"t": time.time(), "call": None}
    heard = []
    try:
        if reg and SOCK["s"] is not None:
            call = Call(ip, port, my_ip, shared=SOCK["s"], user_part=reg.get("user", "").lower())
        else:
            call = Call(ip, port, my_ip)
        BUSY[user["id"]]["call"] = call
        first = synth(opening, user)                       # ready before the phone rings, so there is no pause
        result = call.dial()
        REGISTRAR["call"] = {"t": time.time(), "to": f"{ip}:{port}", "result": result, "via": "sign-in" if call.shared is not None else "direct"}
        if result != "answered":
            return {"result": result, "heard": heard}
        try:
            time.sleep(0.6)
            heard = talk(call, user, first, answer, log)
            return {"result": "answered", "heard": heard}
        finally:
            call.hangup()
    finally:
        BUSY.pop(user["id"], None)


BYE_WORDS = re.compile(r"\b(bye|goodbye|that'?s (all|it)|nothing|no thanks|no thank you|okay thanks|thanks,? bye|got it)\b", re.I)


def talk(call, user, first, answer=None, log=None, turns=MAX_TURNS, caller=False):
    """The back-and-forth once a call is up: say `first`, then listen, repeat back, answer, until bye or hang-up."""
    heard, began, missed = [], time.time(), 0
    call.say(first)
    for _ in range(turns):
        if call.ended.is_set() or time.time() - began > MAX_CALL:
            break
        words = hear(call.listen(), call.rate, user) if not call.ended.is_set() else ""
        if call.ended.is_set():                    # you hung up: stop at once
            break
        if not words:
            if caller and not missed:              # you phoned in: one more chance before giving up
                missed = 1
                call.say(synth("Sorry, I did not catch that. Say it again?", user))
                continue
            call.say(synth("I did not hear anything. Call me again any time. Bye." if caller else
                           "I did not hear anything, so I will leave it on your phone. Bye.", user))
            break
        missed = 0
        heard.append(words)
        if log:
            log(words)
        if BYE_WORDS.search(words) and len(words) < 40:
            call.say(synth("Okay. Bye.", user))
            break
        call.say(synth(f"Got it: {words[:160]}. One moment.", user))      # so a word heard wrong is caught at once
        reply = answer(words) if answer else "Noted."
        if call.ended.is_set():
            break
        call.say(synth(spoken(reply or "Done.") + " Anything else?", user))
    else:
        if not call.ended.is_set() and caller:
            call.say(synth("That is all I can take in one call. Call me again for more. Bye.", user))
    return heard


# ---------- you phone Trillion ----------
def _headers(text):
    """All header lines of a SIP message, long names, in order (a message can carry several Via lines)."""
    head = text.partition("\r\n\r\n")[0]
    first, *lines = head.split("\r\n")
    out = []
    for ln in lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            k = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length"}.get(k.strip().lower(), k.strip().lower())
            out.append((k, v.strip()))
    return first, out


class IncomingCall(Call):
    """A call Linphone makes to Trillion. It arrives on the sign-in socket and is answered there."""

    def __init__(self, sock, data, addr, user):
        super().__init__(addr[0], addr[1], None, shared=sock)
        CALLQ.pop(self.call_id, None)
        text = data.decode("utf-8", "replace")
        self.first_line, self.hdrs = _headers(text)
        h = dict(reversed(self.hdrs))                  # first value of each
        self.h, self.user = h, user
        self.call_id = h.get("call-id", self.call_id)
        CALLQ[self.call_id] = self.q
        self.my_ip = self._route_ip(addr[0])
        self.their_side = h.get("from", "")
        self.our_side = h.get("to", "") + ("" if ";tag=" in h.get("to", "") else f";tag={self.tag}")
        cm = re.search(r"<([^>]+)>", h.get("contact", "")) or re.search(r"(sip:\S+)", h.get("contact", ""))
        self.remote_uri = cm.group(1) if cm else f"sip:{addr[0]}:{addr[1]}"
        self.offer = text.partition("\r\n\r\n")[2]
        self.answer_sdp, self.ok = "", b""

    def _respond(self, code, reason, body="", tag=True):
        lines = [f"SIP/2.0 {code} {reason}"]
        for k, v in self.hdrs:
            if k == "via":
                if "rport" in v and "rport=" not in v:
                    v = v.replace("rport", f"rport={self.port};received={self.target}", 1)
                lines.append(f"Via: {v}")
        lines += [f"From: {self.their_side}", f"To: {self.our_side if tag else self.h.get('to', '')}",
                  f"Call-ID: {self.call_id}", f"CSeq: {self.h.get('cseq', '')}",
                  f"Contact: <sip:trillion@{self.my_ip}:{self.sip_port}>", "Server: Trillion",
                  "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS"]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        data = ("\r\n".join(lines) + "\r\n\r\n" + body).encode()
        self._send_sip(data)
        return data

    def _msg(self, method, cseq, body="", branch=None, to_tag=True):
        """A request from Trillion inside this call (hanging up): the two sides swap round."""
        lines = [f"{method} {self.remote_uri} SIP/2.0",
                 f"Via: SIP/2.0/UDP {self.my_ip}:{self.sip_port};branch={branch or self.branch};rport", "Max-Forwards: 70",
                 f"From: {self.our_side}", f"To: {self.their_side}", f"Call-ID: {self.call_id}", f"CSeq: {cseq} {method}",
                 f"Contact: <sip:trillion@{self.my_ip}:{self.sip_port}>", "User-Agent: Trillion", "Content-Length: 0"]
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def choose(self):
        """Pick the sound format from what the phone offers. False if there is nothing Trillion can use."""
        mm = re.search(r"^m=audio (\d+) RTP/AVP ([\d ]+)", self.offer, re.M)
        if not mm:
            return False
        pts = [int(x) for x in mm.group(2).split()]
        wide = G722 in pts
        if wide:
            try:
                import av  # noqa: F401
            except ImportError:
                wide = False
        self.pt = G722 if wide else next((p for p in pts if p in (0, 8)), -1)
        if self.pt < 0:
            return False
        self.rate = 16000 if self.pt == G722 else 8000
        c = re.search(r"^c=IN IP4 (\S+)", self.offer, re.M)
        self.remote_rtp = (c.group(1) if c else self.target, int(mm.group(1)))
        name = {G722: "G722", 0: "PCMU", 8: "PCMA"}[self.pt]
        media = [f"m=audio {self.rtp_port} RTP/AVP {self.pt}" + (" 101" if 101 in pts else ""),
                 f"a=rtpmap:{self.pt} {name}/8000"] + (["a=rtpmap:101 telephone-event/8000"] if 101 in pts else []) + \
                ["a=ptime:20", "a=sendrecv"]
        for other in re.findall(r"^m=(?!audio)(\w+) \d+ (\S+) (\S+)", self.offer, re.M):   # video and the like: declined
            media.append(f"m={other[0]} 0 {other[1]} {other[2]}")
        self.answer_sdp = (f"v=0\r\no=trillion {int(time.time())} 1 IN IP4 {self.my_ip}\r\ns=Trillion\r\n"
                           f"c=IN IP4 {self.my_ip}\r\nt=0 0\r\n" + "\r\n".join(media) + "\r\n")
        return True

    def ringing(self):
        self._respond(100, "Trying", tag=False)
        self._respond(180, "Ringing")

    def _drain(self, wait):
        """Look at what the phone sent while it rings. 'cancel' if you hung up first."""
        end = time.time() + wait
        while time.time() < end:
            try:
                data, _ = self._recv(min(0.1, max(0.01, end - time.time())))
            except socket.timeout:
                continue
            first = data.decode("utf-8", "replace").split("\r\n", 1)[0]
            if first.startswith(("CANCEL", "BYE")):
                self._reply_ok(data)
                self._respond(487, "Request Terminated")
                return "cancel"
            if first.startswith("INVITE"):            # the phone asking again: still ringing
                self._respond(180, "Ringing")
        return ""

    def pick_up(self, min_ring=0.8):
        """Answer: send OK with the sound details, wait for the phone's ACK, start the sound."""
        if self._drain(min_ring) == "cancel":
            self.ended.set()
            return False
        self.ok = self._respond(200, "OK", self.answer_sdp)
        start, gap, last = time.time(), 0.5, time.time()
        self.state = "talking"
        while time.time() - start < 8:
            try:
                data, _ = self._recv(0.1)
            except socket.timeout:
                if time.time() - last > gap:
                    self._send_sip(self.ok)
                    last, gap = time.time(), min(gap * 2, 2)
                continue
            first = data.decode("utf-8", "replace").split("\r\n", 1)[0]
            if first.startswith("ACK"):
                break
            if first.startswith("INVITE"):
                self._send_sip(self.ok)
            elif first.startswith(("BYE", "CANCEL")):
                self._reply_ok(data)
                self.peer_bye = True
                self.ended.set()
                return False
        threading.Thread(target=self._rtp_loop, daemon=True).start()
        threading.Thread(target=self._sip_loop, daemon=True).start()
        return True

    def _sip_loop(self):
        while not self.ended.is_set():
            try:
                data, _ = self._recv(0.5)
            except socket.timeout:
                continue
            except OSError:
                break
            first = data.decode("utf-8", "replace").split("\r\n", 1)[0]
            if first.startswith("BYE"):
                self._reply_ok(data)
                self.peer_bye = True
                self.ended.set()
            elif first.startswith("INVITE") and self.ok:      # our OK got lost: send it again
                self._send_sip(self.ok)
            elif first.startswith(("OPTIONS", "INFO", "UPDATE")):
                self._reply_ok(data)


def take_call(user, call, opening, answer=None, log=None):
    """Run a call you made to Trillion, from ringing to hang-up. The line is already marked busy."""
    heard = []
    try:
        BUSY[user["id"]]["call"] = call
        first = synth(opening, user)                   # made while the phone rings, so there is no pause
        if not call.pick_up():
            return {"result": "cancelled", "heard": heard}
        time.sleep(0.4)
        heard = talk(call, user, first, answer, log, turns=IN_TURNS, caller=True)
        return {"result": "answered", "heard": heard}
    except Exception as e:
        return {"result": "failed", "error": f"{type(e).__name__}: {e}", "heard": heard}
    finally:
        call.hangup()
        BUSY.pop(user["id"], None)
        REGISTRAR["incoming"] = {"t": time.time(), "user": user["name"], "heard": len(heard)}


def _incoming(sock, data, addr, reply):
    """Linphone dialled Trillion. Only your own signed-in phone gets through, and it gets your own Trillion."""
    if not _allowed_source(addr[0]):
        reply(403, "Forbidden")
        return
    _, hdrs = _headers(data.decode("utf-8", "replace"))
    frm = dict(reversed(hdrs)).get("from", "")
    m = re.search(r"sip:([^@;>\s]+)@", frm)
    user = _user_for(m.group(1) if m else "")
    reg = registered(user) if user else None
    if not user or not reg or reg["ip"] != addr[0]:
        _seen(addr, "a call was refused: only a phone signed in to Trillion can call it.")
        reply(403, "Forbidden")
        return
    if ON_INCOMING is None:
        reply(480, "Temporarily Unavailable")
        return
    if busy(user["id"]):
        reply(486, "Busy Here")
        return
    BUSY[user["id"]] = {"t": time.time(), "call": None}
    try:
        call = IncomingCall(sock, data, addr, user)
    except Exception:
        BUSY.pop(user["id"], None)
        reply(500, "Server Error")
        return
    if not call.choose():
        _seen(addr, "a call was refused: switch on G722 or PCMU in Linphone's audio codecs, and media encryption to None.")
        call._respond(488, "Not Acceptable Here")
        call.hangup()
        BUSY.pop(user["id"], None)
        return
    call.ringing()
    _seen(addr, f"{user['name'].lower()} is calling Trillion")
    threading.Thread(target=ON_INCOMING, args=(user, call), daemon=True).start()


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
        if not re.search(r"\b(projects?|breakdown|update|status|briefing|summary|what'?s outstanding|everything|let me know)\b", words, re.I):
            topic = words[:200]
    if not a:
        # "call me WHEN the project is done" - defer the call until the work finishes, don't ring now
        if re.search(r"\b(when|once|after|as soon as|the moment)\b", text, re.I) and \
           re.search(r"\b(done|finish\w*|complet\w*|ready|over|wrapped?\s*up|wraps?\s*up|ends?)\b", text, re.I):
            return {"on_done": True, "topic": topic}
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
CALLQ = {}                                 # call id -> queue: answers to calls that went out on the sign-in socket
SOCK = {"s": None}
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
    cid = re.search(r"^(?:Call-ID|i)\s*:\s*(\S+)", text, re.M | re.I)
    if cid and cid.group(1) in CALLQ:               # part of a call Trillion is making: hand it to that call
        CALLQ[cid.group(1)].put((data, addr))
        return
    head, _, _ = text.partition("\r\n\r\n")
    first, *lines = head.split("\r\n")
    method = first.split(" ", 1)[0]
    if method not in ("REGISTER", "OPTIONS", "INVITE", "CANCEL", "BYE", "ACK"):
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
    if method == "INVITE":
        _incoming(sock, data, addr, reply)
        return
    if method == "ACK":
        return
    if method in ("CANCEL", "BYE"):                 # for a call that is already over
        reply(481, "Call/Transaction Does Not Exist")
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
    SOCK["s"] = sock
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
