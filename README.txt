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

SKILLS AND CONNECTORS (Skills page)
- Skill: a written playbook. The owner pastes a GitHub link, previews it and installs it; each
  login switches it on or off. Playbooks Trillion drafts herself wait there for approval.
- Connector: a helper program (MCP server) that adds abilities, such as browser control. Only the
  owner adds one and chooses which logins may use it. Connectors need Node.js: run Setup again
  once if "Cannot find the program npx" appears.

MOVING TO THE NEW SSD
1. Family page > DOWNLOAD BACKUP. Save the zip off this drive.
2. On the fresh Windows: unzip Trillion, run Setup, then unzip the backup into the "data" folder.

NOT IN THIS VERSION YET
"Hey Trillion" wake word, phone control, network devices, Google Workspace, mailboxes,
WhatsApp, Emergent website management, competitor tracking, video creation, social posting.
