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
