"""WhatsApp through a self-hosted OpenWA gateway (https://github.com/rmyndharis/OpenWA).

OpenWA is a free, self-hosted WhatsApp API gateway you run next to Trillion (Docker on the same PC). You link
your WhatsApp ONCE by scanning a QR code; after that Trillion can send and receive WhatsApp messages over a
plain REST API - no phone tethered over ADB, and it can RECEIVE messages (via a webhook) so it can auto-reply.

This module is the Trillion side: it calls OpenWA's API to send text / files / voice notes, reads a session's
status and QR, and verifies the HMAC signature on incoming webhooks. The webhook handling + auto-reply lives in
app.py (/api/wa/hook).

What it can and cannot do:
  - Messages (text, media, voice notes): YES - both directions.
  - Live WhatsApp voice/video CALLS: NO. OpenWA (whatsapp-web.js / Baileys) cannot carry call audio; live calls
    stay on Linphone (voicecall.py). "Talking" on WhatsApp happens as voice notes, not a ringing call.

Honest note: OpenWA is UNOFFICIAL (reverse-engineered WhatsApp), so the linked number carries a ban risk. Use a
dedicated business number, not a personal one.

API shape (OpenWA v0.2x, confirmed against the official docs + MCP tools):
  base            http://127.0.0.1:2785/api
  auth header     X-API-Key: owa_k1_...
  send text       POST /sessions/{id}/messages/send-text   {chatId, text}
  send audio      POST /sessions/{id}/messages/send-audio   {chatId, url}      (audio fetched from a public URL)
  send media      POST /sessions/{id}/messages/send-media   {chatId, url, caption}
  sessions        GET  /sessions            GET /sessions/{id}  (-> status)
  start session   POST /sessions            {id, name, config:{autoReconnect, webhookUrl}}
  qr              GET  /sessions/{id}/qr?format=image
  webhook sig     header X-OpenWA-Signature: sha256=<hex of HMAC-SHA256(raw_body, secret)>
"""
import hashlib
import hmac
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx

import core

DEFAULT_URL = "http://127.0.0.1:2785"
MEDIA = core.DATA / "wa"                 # voice notes / outgoing media are written here and served by Trillion
TIMEOUT = 30.0


def _s(user):
    return core.settings(user) if isinstance(user, dict) else core.settings({"id": user})


def base_url(user):
    u = (_s(user).get("wa_url") or DEFAULT_URL).strip().rstrip("/")
    return u if u.endswith("/api") else u + "/api"


def key(user):
    try:
        return core.get_secret(user["id"], "wa", only_enabled=False) or ""
    except Exception:
        return ""


def hmac_secret(user):
    try:
        return core.get_secret(user["id"], "wa_hmac", only_enabled=False) or ""
    except Exception:
        return ""


def session_id(user):
    return (_s(user).get("wa_session") or "trillion").strip() or "trillion"


def configured(user):
    return bool(key(user))


def _headers(user):
    return {"X-API-Key": key(user), "Content-Type": "application/json"}


def public_base(user):
    """A base URL the OpenWA container can reach Trillion on, to fetch outgoing audio and to POST webhooks.
    Prefer an explicit setting; then the Tailscale funnel (reachable anywhere); then Docker's host alias."""
    b = (_s(user).get("wa_public_base") or "").strip().rstrip("/")
    if b:
        return b
    dns = (_s(user).get("funnel_dns") or os.environ.get("TRILLION_FUNNEL_DNS") or "").strip()
    if dns:
        return f"https://{dns}"
    # OpenWA usually runs in Docker; host.docker.internal reaches the host (Trillion) on Windows/Mac Docker.
    return "http://host.docker.internal:8787"


# ---------- low-level calls ----------
def _post(user, path, json=None, timeout=TIMEOUT):
    r = httpx.post(base_url(user) + path, headers=_headers(user), json=json or {}, timeout=timeout)
    if r.status_code >= 400:
        raise WAError(f"OpenWA {path} -> {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return {"ok": True, "raw": r.text}


def _get(user, path, params=None, timeout=TIMEOUT):
    r = httpx.get(base_url(user) + path, headers=_headers(user), params=params or {}, timeout=timeout)
    if r.status_code >= 400:
        raise WAError(f"OpenWA {path} -> {r.status_code}: {r.text[:300]}")
    return r


class WAError(Exception):
    pass


# ---------- chat id helpers ----------
def to_chat_id(who, country_code="27"):
    """Turn a number/JID the user typed into a WhatsApp chat id. Groups (…@g.us) and full JIDs pass through."""
    w = str(who or "").strip()
    if "@" in w:                              # already a JID
        return w
    digits = re.sub(r"\D", "", w)
    if not digits:
        raise WAError("That doesn't look like a phone number.")
    cc = re.sub(r"\D", "", str(country_code or "27")) or "27"
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):              # local number -> international
        digits = cc + digits[1:]
    return digits + "@c.us"


# ---------- status / linking ----------
def status(user):
    """Return the session's state: {configured, linked, status, session}. Never raises."""
    out = {"configured": configured(user), "linked": False, "status": "unknown", "session": session_id(user)}
    if not configured(user):
        out["status"] = "no_key"
        return out
    try:
        r = _get(user, f"/sessions/{session_id(user)}", timeout=8)
        j = r.json()
        st = str((j.get("status") or j.get("state") or "")).upper()
        out["status"] = st or "unknown"
        out["linked"] = st in ("CONNECTED", "AUTHENTICATED", "READY", "WORKING")
    except WAError as e:
        out["status"] = "no_session" if "404" in str(e) else "error"
    except Exception:
        out["status"] = "unreachable"
    return out


def start_session(user, webhook=True):
    """Create/ensure the session and (optionally) point its webhook back at Trillion. Returns the API response."""
    cfg = {"autoReconnect": True}
    if webhook:
        cfg["webhookUrl"] = public_base(user) + "/api/wa/hook"
        sec = hmac_secret(user)
        if sec:
            cfg["webhookSecret"] = sec
    body = {"id": session_id(user), "name": "Trillion", "config": cfg}
    try:
        res = _post(user, "/sessions", body)
    except WAError as e:
        if "409" in str(e) or "exist" in str(e).lower():     # already there -> just restart it
            res = _post(user, f"/sessions/{session_id(user)}/restart")
        else:
            raise
    if webhook:
        register_webhook(user)                                # also register via the dedicated endpoint (version-safe)
    return res


def register_webhook(user):
    """Point an OpenWA webhook at Trillion's /api/wa/hook for incoming messages. Best-effort; ignored if the
    gateway version doesn't expose this endpoint (the session's config.webhookUrl already covers it)."""
    body = {"url": public_base(user) + "/api/wa/hook",
            "events": ["message.received", "session.status"]}
    sec = hmac_secret(user)
    if sec:
        body["secret"] = sec
    try:
        return _post(user, f"/sessions/{session_id(user)}/webhooks", body)
    except Exception:
        return None


def qr(user, fmt="image"):
    """Return the QR to scan to link WhatsApp. fmt: 'image' (data URL/png), 'base64' or 'raw'."""
    r = _get(user, f"/sessions/{session_id(user)}/qr", params={"format": fmt}, timeout=12)
    ct = r.headers.get("content-type", "")
    if "application/json" in ct:
        j = r.json()
        return j.get("qr") or j.get("data") or j.get("base64") or ""
    if ct.startswith("image/"):
        import base64
        return "data:" + ct + ";base64," + base64.b64encode(r.content).decode()
    return r.text


# ---------- sending ----------
def send_text(user, to, text):
    cid = to_chat_id(to, _s(user).get("phone_country_code") or "27")
    _post(user, f"/sessions/{session_id(user)}/messages/send-text", {"chatId": cid, "text": str(text or "")[:4000]})
    return cid


def send_media(user, to, url, caption="", kind="media"):
    cid = to_chat_id(to, _s(user).get("phone_country_code") or "27")
    body = {"chatId": cid, "url": url}
    if caption:
        body["caption"] = str(caption)[:1000]
    # try the typed endpoint, then the generic one
    for path in (f"/sessions/{session_id(user)}/messages/send-{kind}",
                 f"/sessions/{session_id(user)}/messages/send-media"):
        try:
            _post(user, path, body)
            return cid
        except WAError as e:
            if "404" not in str(e):
                raise
    raise WAError("OpenWA has no send-media endpoint on this version.")


def send_audio_url(user, to, url):
    cid = to_chat_id(to, _s(user).get("phone_country_code") or "27")
    _post(user, f"/sessions/{session_id(user)}/messages/send-audio", {"chatId": cid, "url": url})
    return cid


def send_voice(user, to, text):
    """Speak `text` in Trillion's local voice and send it as a WhatsApp voice note."""
    ogg = synth_voice_note(str(text or ""))
    if not ogg:
        return send_text(user, to, text)         # no voice available -> fall back to text so nothing is lost
    url = public_base(user) + "/api/wa/media/" + ogg.name
    return send_audio_url(user, to, url)


def synth_voice_note(text):
    """Make an OGG/Opus voice note from text (the format WhatsApp shows as a real voice note). Returns the Path,
    or None if no local voice is available. Files live under data/wa and are served by /api/wa/media/."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        import localvoice
        import video
        samples, sr = localvoice.synth_wait(text)
        if samples is None or len(samples) == 0:
            return None
        MEDIA.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:12]
        wav = MEDIA / f"{tag}.wav"
        wav.write_bytes(localvoice.wav_bytes(samples, sr))
        ogg = MEDIA / f"{tag}.ogg"
        subprocess.run([video.find_ffmpeg(), "-y", "-v", "error", "-i", str(wav),
                        "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1", str(ogg)],
                       check=True, timeout=120)
        wav.unlink(missing_ok=True)
        _sweep_media()
        return ogg if ogg.exists() else None
    except Exception:
        return None


def _sweep_media(max_age=3600):
    """Keep data/wa tidy: drop served audio older than an hour (OpenWA fetches it within seconds)."""
    try:
        now = time.time()
        for f in MEDIA.glob("*.*"):
            if now - f.stat().st_mtime > max_age:
                f.unlink(missing_ok=True)
    except Exception:
        pass


# ---------- incoming webhook ----------
def verify_signature(user, raw_body, header):
    """True if the webhook's X-OpenWA-Signature matches HMAC-SHA256(raw_body, secret). If no secret is set,
    verification is treated as passed (the user chose not to sign)."""
    sec = hmac_secret(user)
    if not sec:
        return True
    if not header:
        return False
    got = header.strip()
    if got.lower().startswith("sha256="):
        got = got[7:]
    want = hmac.new(sec.encode(), raw_body if isinstance(raw_body, bytes) else raw_body.encode(),
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(got, want)


def parse_incoming(payload):
    """Pull the useful bits out of an OpenWA webhook body. Returns a dict or None for events we ignore.
    Tolerant of field-name drift between OpenWA versions."""
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") or payload.get("type") or ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if "message" not in str(event).lower() and not (data.get("body") or data.get("from") or data.get("chatId")):
        return None
    frm = data.get("from") or data.get("chatId") or data.get("sender") or ""
    from_me = bool(data.get("fromMe") or data.get("from_me"))
    mtype = (data.get("type") or data.get("messageType") or ("audio" if data.get("isVoice") else "text")).lower()
    media_url = ""
    for k in ("mediaUrl", "media_url", "url", "downloadUrl"):
        if data.get(k):
            media_url = data[k]
            break
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    if not media_url and media:
        media_url = media.get("url") or ""
    return {
        "event": event,
        "from": frm,
        "from_me": from_me,
        "type": mtype,
        "is_group": str(frm).endswith("@g.us"),
        "text": (data.get("body") or data.get("text") or data.get("caption") or "").strip(),
        "media_url": media_url,
        "media_base64": (media.get("base64") if media else "") or data.get("base64") or "",
        "message_id": data.get("id") or data.get("messageId") or "",
    }
