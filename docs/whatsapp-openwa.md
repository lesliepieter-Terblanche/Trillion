# WhatsApp for Trillion (via OpenWA)

Trillion can send **and receive** WhatsApp messages — text, files, and spoken **voice notes** — through a free,
self-hosted gateway called **OpenWA** that runs in Docker on the same PC. You link your WhatsApp once by scanning
a QR code; after that everything goes over a local API, with no phone tethered by USB.

**What this does and doesn't do**
- ✅ Send/receive WhatsApp **messages** (text, media, voice notes).
- ✅ **Auto-reply** to incoming messages (optional, off by default).
- ❌ Live WhatsApp **voice/video calls** — not possible through any WhatsApp API. Live calls stay on Linphone.
  "Talking" on WhatsApp happens as **voice notes**, not a ringing call.

**Important — ban risk.** OpenWA is *unofficial* (it drives WhatsApp Web). WhatsApp can restrict or ban a number
that uses it. **Use a dedicated business SIM, not your personal number**, and warm new numbers up slowly.

---

## 1. Run OpenWA (Docker)

Install Docker Desktop, then save this as `docker-compose.whatsapp.yml` and run
`docker compose -f docker-compose.whatsapp.yml up -d`:

```yaml
services:
  openwa:
    image: ghcr.io/rmyndharis/openwa:latest
    container_name: openwa
    restart: unless-stopped
    ports:
      - "2785:2785"            # dashboard + API: http://localhost:2785
    volumes:
      - ./openwa-data:/app/data
    extra_hosts:
      - "host.docker.internal:host-gateway"   # lets OpenWA reach Trillion on this PC
```

First boot prints an **admin API key** (starts `owa_k1_...`) in the logs: `docker logs openwa`.
Open the dashboard at **http://localhost:2785** and sign in with that key.

## 2. Link your WhatsApp

In the OpenWA dashboard, create a session with the id **`trillion`**, open it, and **scan the QR code** with the
WhatsApp you want Trillion to use (Settings → Linked devices → Link a device). It should show **CONNECTED**.

## 3. Tell Trillion about it

In Trillion → **Settings**:
- **OpenWA API key** (secret `wa`): paste the `owa_k1_...` key. Click **Test** — it should say *Connected and
  linked to WhatsApp*.
- **Webhook signing secret** (secret `wa_hmac`): make up a random string. Use the **same** value when you add the
  webhook in OpenWA (next step). Leave both blank only if you don't want signature checking.
- **wa_public_base**: the URL OpenWA can reach Trillion on. Best is your **Tailscale Funnel** address
  (`https://desktop-xxxx.tailxxxx.ts.net`). If you leave it blank, Trillion uses `host.docker.internal:8787`,
  which works when OpenWA runs in Docker on the same PC.

Then either click **Link WhatsApp** in Trillion (it creates the session + webhook for you), **or** in the OpenWA
dashboard add a webhook pointing to:

```
<wa_public_base>/api/wa/hook        e.g.  https://desktop-xxxx.tailxxxx.ts.net/api/wa/hook
events: message.received, session.status
secret: <the same wa_hmac value>
```

## 4. Turn on what you want

- **wa_auto_reply** (off by default): when ON, Trillion answers incoming WhatsApps automatically in your business
  voice. Start OFF, watch the inbox, turn ON when you're happy.
- **wa_voice_notes** (off by default): when ON, replies are sent as spoken **voice notes** instead of text.

---

## Using it

- **Send a WhatsApp:** `POST /api/wa/send` `{ "to": "0821234567", "message": "Hi", "voice": false }`
  (`voice: true` sends it as a voice note). Numbers can be local (`082…`), international (`+2782…`) or a JID.
- **Incoming messages** land in the dashboard inbox (domain `whatsapp`). With auto-reply on, Trillion answers.
- **Incoming voice notes** are transcribed with the local call model; with voice-notes on, Trillion replies with
  a voice note of its own.

## Notes & limits

- Outgoing **voice notes** are made in Trillion's local voice, encoded to OGG/Opus, and served to OpenWA from
  `/api/wa/media/…` (random filename, auto-deleted within the hour).
- The **first** incoming voice note may not transcribe until the local speech model has finished warming up.
- The **ADB phone** method still exists as a fallback for sending.
- For regulated/high-volume use, consider Meta's official WhatsApp Cloud API instead of OpenWA.
