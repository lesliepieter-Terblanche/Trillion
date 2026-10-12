# Trillion: notes for any coding assistant working on this repository

Read this before changing anything. It is the handover from the assistant that built Trillion so far.

## What Trillion is

A personal assistant that runs on one Windows 10 PC at home (RTX 5060 Ti 16 GB, 32 GB RAM) and is used from that PC,
a laptop and Android phones over Tailscale. Three family logins share it: Pieter (owner, admin), Brandon and Lewellyn.
Each login has its own settings, keys, memory, files and history, and must never see another login's data.

The owner is not a developer and judges work by how it looks and behaves. Changes reach his PC when he tells Trillion
"update yourself" (or presses UPDATE NOW on the Family page): Trillion runs `git fetch` and fast-forwards to
`origin/main`, then restarts. So whatever is on `main` is what he gets. Never push something half-finished to `main`.

## How it is built

- Python 3.12, FastAPI on port 8787 (`app.py`), SQLite database `data/trillion.db`, one single-page dashboard
  `static/index.html` (plain JavaScript, no build step).
- Local brain through Ollama (`qwen2.5:14b`, fast model `qwen2.5:3b`). A login may switch on Gemini or Claude with its
  own key. `app.llm_chat(user, messages, ...)` is the one call for a model answer.
- Common requests are answered by regular-expression "shortcuts" in `app.py` before the model is asked
  (`phone_shortcut`, `store_shortcut`, `bible_shortcut` ...). The local model is not reliable at choosing tools, so
  anything that must work every time gets a shortcut.
- Tools the model can call are in `tools.py` (`SPECS`, `t_<name>` functions). Anything that sends money, deletes,
  runs commands or messages an unsaved number waits for approval (`NEEDS_APPROVAL`).
- Secrets (API keys, tokens, login codes) are stored encrypted per login with `core.set_secret` / `core.get_secret`.

| File | What it does |
| --- | --- |
| `app.py` | Web server, login, chat, shortcuts, all `/api/...` routes, startup migrations |
| `core.py` | Database, settings, users, encrypted secrets, logging, PC vitals |
| `tools.py` | Tools for the model, approvals |
| `extend.py` | Skills (SKILL.md playbooks in `skill_packs/`) and connectors (MCP servers) |
| `routines.py` | Morning / evening briefings and their sections |
| `phone.py` | Android phones over ADB: status, WhatsApp sending (with attachments), screen, unlock |
| `attach.py` | Files people add in the chat |
| `video.py`, `freevideo.py` | Edited videos with FFmpeg; AI clips through FreeVideo (a separate ComfyUI install) |
| `ebook.py` | Designed ebooks through Ebook Creator (a separate Next.js install on port 3217) |
| `webview.py` | A login's own web page (Brandon's health tracker) opened in a hidden browser and shown in the dashboard |
| `gumroad.py` | Brandon's Gumroad store: sales, products, codes, payouts, creating products |
| `bible.py`, `bibles/` | Lewellyn's Bible study: daily verses, reader, search, notes, study helper (King James Version) |
| `*.bat`, `setup.ps1` | Windows installers the owner double-clicks |

Programs installed next to Trillion (FreeVideo, EbookCreator) are never copied into this repository; their folders are
in `.gitignore`.

## Rules that must not be broken

1. **No secrets in the repository.** Brandon's health tracker login code, API keys and tokens are typed into the
   dashboard by the person and stored encrypted. Never write one into code, tests, commit messages or docs.
2. **Each login sees only its own things.** Every route that reads per-login data filters by `user["id"]`. The store
   page is Brandon's only and Bible study is Lewellyn's only (the owner can switch them on for others on the Family
   page); check `store_allowed` / `bible.allowed` on every route and shortcut you add for them.
3. **WhatsApp:** messages to saved contacts go straight out with no approval (the owner asked for this). Typed-in
   numbers that are not saved wait for approval. Do not change this.
4. **Licences:** before adding any outside program or data, check its licence. MIT, Apache and BSD are fine; AGPL
   programs stay separate; no licence means do not use. Bible translations other than the King James Version are
   under copyright unless proven otherwise.
5. **Plain language in everything the owner sees:** short sentences, no technical terms, say what happened and what
   to do next. Error messages must say what to do.
6. **Bump `VERSION`** (0.7.x) with every change to the program pushed to `main`, so the owner can see an update arrived.
7. **One-time changes to existing data** (new settings defaults, adding a connector) go in startup code guarded by a
   marker setting or marker file, so they run once and do not undo the owner's own choices later.

## Working on it

- Run locally: `pip install -r requirements.txt`, then `python -m uvicorn app:app --port 8787` with
  `TRILLION_DATA` set to an empty folder for a throwaway database. Log in as Pieter / Brandon / Lewellyn with any
  password on first use.
- There is no phone, Ollama or Gumroad in a test setup. Test against stand-ins: a fake `adb` on the `PATH`, a small
  fake HTTP server for Gumroad (`TRILLION_GUMROAD`), and patch `app.llm_chat` for model answers. Use FastAPI's
  `TestClient` for the API and Playwright to look at the dashboard at desktop (1440 wide) and phone (390 wide) sizes.
- Check the dashboard has no horizontal scrolling on a phone and the top menu stays on one row at 1366 wide.
- Keep the dashboard one file; follow the existing patterns (`go(view)`, `LOADERS`, `api()`, `twice()` for buttons
  that cost money or cannot be undone).

## Where things were left (12 October 2026, version 0.7.29)

- Everything recent is tested against stand-ins only, not yet on the owner's PC or phones.
- Offered but not built, waiting for the owner's decision: watch alerts for his Galaxy Watch 8 (post phone
  notifications through ADB so the watch mirrors them), a Wear OS app, a learning memory, HyperFrames video templates,
  OpenMontage (AGPL, must stay a separate program).
