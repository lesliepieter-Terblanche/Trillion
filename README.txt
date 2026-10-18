TRILLION v0.1 - the core
========================

INSTALL (once)
1. Unzip this folder somewhere permanent, for example C:\Trillion
2. Double-click "1 - Setup.bat"
   It installs Python, Ollama, FFmpeg and Git if they are missing, installs Trillion's own
   parts, and downloads the local models (about 15 GB, so it takes a while).
3. Double-click the "Trillion" shortcut on the desktop (or "2 - Start Trillion.bat").
   The dashboard opens at http://localhost:8787
4. Pick your name and create your password. Brandon and Lewellyn do the same the first
   time they sign in.

WHAT WORKS IN THIS VERSION
- Three logins (Pieter = owner, Brandon, Lewellyn), each with their own dashboard, branding,
  settings, keys, memory and history
- Local brain through Ollama; type or hold the MIC button and speak
- Files and folders: list, find, read, write, copy, move, rename, open
- Deleting files and running commands always wait for approval on the dashboard
- Creates Word, Excel and PowerPoint files, and notes
- Second brain: she saves lasting facts, searches them, and reviews each day at 23:30
- Live PC vitals, loaded models, task board, today's timeline, activity log
- Website up/down watch for each login's site
- Settings: per-login keys for Gemini, Claude (API key) and ElevenLabs, encrypted on this PC
- Family page (owner only): who has what switched on, folder permissions, password reset,
  one-click backup

WHO CAN TOUCH WHAT
- Pieter: the whole PC
- Brandon and Lewellyn: only their own Trillion folder (data\users\<name>\files) until the
  owner adds more folders for them on the Family page

ON YOUR PHONE
Install Tailscale on the PC and the phone, signed in to the same account.
Typing only:  open http://<the PC's Tailscale address>:8787 in the phone's browser.
With the microphone: double-click "4 - Phone secure link.bat" once on the PC and open the
https address it shows on the phone instead. Phones only allow the microphone on https.

UPDATING
Tell Trillion "update yourself", or use UPDATE NOW on the Family page.

VIDEOS (Videos page, or just ask her)
"Create a 20 second cinematic video about ..." She writes the script, fetches real footage from
Pixabay, records an ElevenLabs voice-over, adds music, titles and captions, and saves the MP4 to
your "Trillion Videos" folder. Keys go under Settings: Pixabay (free, pixabay.com/api/docs) for footage,
ElevenLabs for the voice. Without them the video still renders, with light backdrops and no voice.
Your own music: drop mp3 files into data\music and she uses those instead of the built-in track.

YOUR PHONE (the Phone page)
Each login links its own Android phone. On the phone switch on USB debugging (Developer options),
plug it into the PC, tap Allow, then LINK TO ME. Optional: SWITCH TO WIRELESS with the phone's
Tailscale address. She can then report the phone's status, open apps, look up contacts, send a
WhatsApp (always after your approval), locate it, ring it and take a screenshot.
Needs the Android phone tools: run Setup again once if it says ADB is not installed.

ROUTINES (Routines page, or just say the words)
Say "morning" and she gives you the weather, news, financials, the latest jobs she finished, what is
outstanding, your website and the PC's health, aloud and as a panel on the home screen. Every login
has its own routines: Morning, Evening wrap-up ("good night") and Status check ("status") to start
with. On the Routines page each person changes the words, the town, the news sources, the list of
currencies, metals and crypto, the order, adds a reminder of their own, or adds new routines.
All of it is free and needs no keys.

SKILLS AND CONNECTORS (Skills page)
- Skill: a written playbook. The owner pastes a GitHub link, previews it and installs it; each
  login switches it on or off. Playbooks Trillion drafts herself wait there for approval.
- Connector: a helper program (MCP server) that adds abilities, such as browser control. Only the
  owner adds one and chooses which logins may use it. Connectors need Node.js: run Setup again
  once if "Cannot find the program npx" appears.
- CHECK FOR DUPLICATES (button on the Skills page): scans your connectors and skills and flags any
  two that run the same program or cover the same ground, so you can remove the extra one.
- WHAT'S BEING USED (button on the Skills page): shows how often each skill and connector has actually
  been used, and flags the ones never used, each with an UNINSTALL button. You can also just ask her
  "which skills aren't being used". ("Not used yet" counts from when tracking began, so fresh installs
  start blank.)

BUSINESS APPS (Apps page)
- The apps are SerpBear (Google rankings), n8n and Activepieces (automations), and under Knowledge:
  SearXNG (a private search engine) and AnythingLLM (chat with your own documents). Postiz, Listmonk,
  Umami, Invoice Ninja and Chatwoot were removed on request; if any were installed, Trillion erases
  their containers, data, folders and saved keys the next time it starts with Docker running.
- SearXNG lets Trillion search the web herself with no API key and nothing sent to Google: install it,
  then just ask her to look something up. AnythingLLM is your own private document library you can chat
  with; it uses your local Ollama brain, so it costs no tokens and nothing leaves the PC.
- Reverse engineering (REA): a connector that looks inside apps, websites and programs without
  their source (github.com/morluto/rea, MIT). Owner only, and it runs without asking for approval.
  To reverse-engineer an app on your phone, just say "use REA on <app> on my phone": Trillion pulls
  the app's APK off the phone and hands it to Claude Code (no screenshots). To see what servers an
  app talks to, say "what does <app> connect to": Trillion routes the phone through a proxy on the PC
  (mitmproxy) for a short while and reports the hosts and requests. That needs mitmproxy installed on
  the PC (pip install mitmproxy); reading HTTPS contents (not just the server names) also needs
  mitmproxy's certificate installed on the phone, and the app must not pin its certificate. Heavy native-binary jobs
  (a .exe/.dll, an APK, a Ghidra-level disassembly) are handed to Claude Code on your Pro plan, which
  does the work and reports back in the chat and on your phone. For that handoff, Claude Code must
  be installed and signed in to your Pro plan on this PC; adjust its command under the REA
  connector if needed (default "claude -p").

YOUR WI-FI (TP-Link Deco)
Trillion can manage your TP-Link Deco mesh over the local network - no TP-Link cloud account, nothing
leaves the PC. Set it up on the Skills page under Connectors > "TP-Link Deco": enter the Deco address
(usually 192.168.68.1) and your Deco admin password (the one from the Deco app). The password is kept
encrypted on this PC. Then just ask her, for example "who's on my wifi" or "show my Deco network".
By default she can only READ (who's connected, the nodes, guest Wi-Fi, reservations, a speed test).
Blocking a device and rebooting a node are off until you tick the "Allow changes" boxes - and on some
Deco firmware (yours is an X55) blocking and reboot can be app-only, so they may not work even then.
Owner login only. (Built on the open deco-mcp project, MIT licence.)

THE SMART (BIGGER) BRAIN
Trillion runs two local brains: a fast one for everyday chat and the full one for jobs. There is now an
optional third - a bigger "smart" model (Mistral Small 24B, about 14 GB) that she uses automatically for
coding and hard reasoning, while everyday chat stays on the fast model. It fits your 16 GB graphics card.
Setup downloads it (skip with -SkipModelsHeavy); you can also get it any time by telling her "install the
smart model". If it is not installed she simply uses the normal local model, so nothing breaks. Turn the
routing off under Settings (heavy_answers) if you ever want everything on one model. All of this is local
and free - no tokens, nothing leaves the PC.

SMARTER MEMORY
Her second brain (the facts she saves and searches) gets a proper local vector store when the optional
"chromadb" part is installed - Setup does this automatically, and it falls back to the built-in search if
it is missing, so it is safe either way. This keeps recall fast and sharp as the number of saved notes grows.

SELF-CHECK (is everything working?)
Trillion can test all her own parts live on this PC and show what is working.
- Settings page: press RUN SELF-CHECK. Or in the chat: "run a self check".
- Green = working. Amber = optional or not set up yet. Red = broken; the line says how to fix it.
- She also runs it once when she starts and writes any red items into the activity log, so you
  find out about a problem without having to ask.

GO-LIVE CHECKLIST (do this once, then you KNOW it is solid)
Run the self-check first and clear every red item. Then confirm these by hand, because they only
truly count when done on your real PC:
1. Backup round-trip: Settings > BACK UP NOW to a D: folder. Open the backup folder and check a
   manifest.json and a data folder are there.
2. One business app: Apps page > install SerpBear (or n8n). Wait for "running", open it, sign in.
3. REA on an app: open an app on the phone, then say "reverse engineer the app on my screen". You
   should get a report back (needs Claude Code signed in to your Pro plan on this PC).
4. App traffic: "what does <app> connect to" - you should get a list of servers (needs mitmproxy,
   which Setup now installs).
5. A call: Phone page > TEST CALL, answer it, say a request, check it acts on it. Before every call
   Trillion wakes and unlocks the phone and opens Linphone so it rings - save an unlock code on the
   Phone page so she can unlock it (without one, the call still comes but may sit behind the lock screen).
6. Meeting mode: turn it on, send yourself a WhatsApp, check Trillion replies. The reply wording is
   set on the Phone page under Meeting mode - either a short phrase ("in a meeting") that slots into
   a sentence, or your own full message (use {time} for when you're back and {name} for your name).
7. A reminder call: "call me in 2 minutes with my project update", confirm the phone rings.
When all seven are done and the self-check is green, Trillion is ready to rely on day to day.

MOVING TO A NEW DRIVE OR A CLEAN WINDOWS INSTALL
Before you wipe C:
1. Settings > Backup and restore > BACK UP NOW, to a folder on another drive (for example D:\Trillion Backup).
   It holds every login, password, setting, note and chat, your finished files on C:, the phone pairing,
   and the business apps' data. Files already on D: are left where they are.
2. Write down nothing: the backup itself records the PC name, the Tailscale name and address, the
   Downloads location and every folder that was moved to D:.
After the clean install:
1. Install Trillion again (1 - Setup.bat, 3 - Connect to GitHub.bat) and sign in as the owner.
2. Settings > Backup and restore: the backup on D: is listed. Press RESTORE (tap twice). Trillion restarts,
   puts everything back and changes every saved path to the new Windows user folder.
3. Install Tailscale and sign in. In the Tailscale admin page remove the old PC, give this PC the old name,
   and set its address to the old 100.x address (both are shown after the restore). Every link on the
   phones and laptop then keeps working.
4. If you use the business apps: Apps page > Install Docker, Start Docker, then Settings > PUT THE
   BUSINESS APPS BACK. They return with their data.
5. Keep the drive letter of the second drive the same (D:), so the moved folders link up again.
