"""Trillion tools: what the assistant can actually do on the PC."""
import os, re, json, math, shutil, subprocess, threading, time, fnmatch
from pathlib import Path

import httpx

import core
import watch


def fn(name, desc, props, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required if required is not None else list(props)}}}


S = {"type": "string"}
ARR = {"type": "array", "items": {"type": "string"}}
SPECS = [
    fn("list_folder", "List the files and folders inside a folder.", {"path": S}),
    fn("find_files", "Search a folder (and its subfolders) for files matching a pattern like *.pdf or *invoice*.",
       {"folder": S, "pattern": S}),
    fn("read_file", "Read a text file.", {"path": S}),
    fn("write_file", "Create or overwrite a text file (notes, .txt, .md, .csv, code).", {"path": S, "content": S}),
    fn("make_folder", "Create a folder.", {"path": S}),
    fn("copy_path", "Copy a file or folder.", {"source": S, "destination": S}),
    fn("move_path", "Move or rename a file or folder.", {"source": S, "destination": S}),
    fn("delete_path", "Delete a file or folder. Always needs the user's approval on the dashboard.", {"path": S}),
    fn("open_path", "Open a file, folder or program on the PC screen.", {"path": S}),
    fn("create_word", "Create a Word document (.docx).",
       {"path": S, "title": S, "paragraphs": {"type": "array", "items": S}}),
    fn("create_excel", "Create an Excel workbook (.xlsx). rows is a list of rows; the first row is the header.",
       {"path": S, "sheet_name": S, "rows": {"type": "array", "items": {"type": "array", "items": S}}}),
    fn("create_powerpoint", "Create a PowerPoint deck (.pptx).",
       {"path": S, "title": S, "slides": {"type": "array", "items": {"type": "object", "properties": {
           "title": S, "bullets": {"type": "array", "items": S}}}}}),
    fn("save_note", "Save something worth remembering to the second brain. domain is PERSONAL or WORK.",
       {"title": S, "body": S, "domain": S}, ["title", "body"]),
    fn("search_memory", "Search the second brain for things saved earlier.", {"query": S}),
    fn("web_search", "Search the web privately through the local SearXNG engine (no API key, nothing sent to Google). "
       "Returns titles, links and short snippets. Use this to look things up online.", {"query": S, "count": {"type": "integer"}}, ["query"]),
    fn("list_cast_devices", "List the Google Home / Nest speakers and Chromecasts found on the local network.", {}),
    fn("announce", "Say a short message out loud on a Google Home / cast speaker. device is the speaker's name "
       "(optional - leave empty for the only/first one).", {"text": S, "device": S}, ["text"]),
    fn("cast_media", "Play an audio or video URL on a Chromecast or speaker. device is optional.",
       {"url": S, "device": S, "content_type": S}, ["url"]),
    fn("cast_control", "Control a cast device: action is play, pause, stop or volume. For volume give level 0-100. "
       "device is optional.", {"action": S, "device": S, "level": {"type": "integer"}}, ["action"]),
    fn("list_cameras", "List the cameras set up on this PC (front door, Wyze, etc.).", {}),
    fn("camera_snapshot", "Take a still picture from a camera right now and show it. camera is the camera's name. "
       "If display is given (a cast screen name), also put the picture on that screen.", {"camera": S, "display": S}, ["camera"]),
    fn("camera_move", "Move a PTZ camera: action is left, right, up, down, zoom_in, zoom_out or stop. camera is its name. "
       "Only works on cameras that support ONVIF.", {"camera": S, "action": S}, ["camera", "action"]),
    fn("seo_rankings", "Google rankings of the user's websites for their tracked search words, from SerpBear.", {"domain": S}, []),
    fn("competitor_watch", "Watch competitor websites for changes (prices, specials, new products and pages) and report "
       "what changed. action: add (url, optional name and hours between checks), list, remove (url or name), "
       "changes (what changed in the last days, default 7). Each login has its own list.",
       {"action": S, "url": S, "name": S, "hours": {"type": "integer"}, "days": {"type": "integer"}}, ["action"]),
    fn("automations", "List the user's n8n automation workflows and whether each is on.", {}),
    fn("automation_switch", "Switch an n8n workflow on or off by its name.", {"name": S, "on": {"type": "boolean"}}),
    fn("read_website", "Open a website in a browser on the PC, read the whole site page by page, analyse it, and make files from "
       "it: outputs can include pptx (presentation), docx (report document) and xlsx (spreadsheet of pages). focus is what the "
       "user wants to learn or the purpose of the files. Runs in the background; tell the user it has started.",
       {"url": S, "outputs": {"type": "array", "items": S}, "focus": S, "max_pages": {"type": "integer"}}, ["url"]),
    fn("clone_website", "Clone a website into website files and folders. mode copy = a full working copy (only the user's own "
       "site, or one whose owner gave permission: set permission true only if the user said so). mode rebuild = a new site for "
       "the user's own business in the layout and style of url, with new words; brand is the business name and about what it does.",
       {"url": S, "mode": S, "brand": S, "about": S, "permission": {"type": "boolean"}, "max_pages": {"type": "integer"}}, ["url", "mode"]),
    fn("check_website", "Check whether a website is up and how fast it answers.", {"url": S}),
    fn("pitch_video", "Make a 30-second cinematic walkthrough video (MP4) of a website Web Studio built, to send with the "
       "pitch: title card, desktop and phone walkthrough, features or scores, call to action, with a free voice-over when "
       "the Kokoro voice is on. site_id is the Web Studio site number. Optional scores compare the prospect's current site "
       "with the new one, e.g. {\"Performance\": [38, 99], \"SEO\": [60, 92]} - only use numbers a Lighthouse audit returned. "
       "Runs in the background (about 2-4 minutes); the WhatsApp pitch then sends the video with the link.",
       {"site_id": {"type": "integer"}, "scores": {"type": "object"}}, ["site_id"]),
    fn("system_status", "Get the PC's current processor, memory, disk and graphics card readings.", {}),
    fn("use_skill", "Load the full instructions of one of the user's installed skills. Call this before doing "
       "a job that one of the listed skills covers, then follow what it says.", {"name": S}),
    fn("draft_skill", "Write down a reusable playbook for a job the user does repeatedly. It is saved as a draft "
       "and only becomes usable after the owner approves it on the Skills page.",
       {"name": S, "description": S, "instructions": S}),
    fn("connector_tools", "List what one of the user's connectors can do (its tools and their arguments). "
       "Call this before connector_call. A connector with many tools first returns only their names: call again with "
       "find set to a few words (for example: javascript, website, decompile) to get the matching tools in full.",
       {"connector": S, "find": S}, ["connector"]),
    fn("connector_call", "Use one tool of a connector. Get the exact tool name and arguments from connector_tools first.",
       {"connector": S, "tool": S, "arguments": {"type": "object"}}, ["connector", "tool"]),
    fn("rea_phone_app", "Reverse-engineer an app installed on the user's phone: pulls the app's APK off the phone and hands it to "
       "Claude Code for analysis. Use this for 'use REA on <app> on my phone' instead of taking screenshots. app is the app's name "
       "or package. Owner only; runs without asking for approval.", {"app": S}, ["app"]),
    fn("rea_phone_traffic", "Capture what servers an app on the phone talks to: routes the phone through a proxy on the PC for a "
       "short while, then reports the hosts and requests. Use for 'what does <app> connect to / its network traffic'. app is the "
       "app's name; seconds is how long to capture (default 60). Owner only; runs without asking for approval.",
       {"app": S, "seconds": {"type": "integer"}}, ["app"]),
    fn("phone_status", "Get the user's own mobile phone status: battery, charging, storage, Wi-Fi, model, screen on or off.", {}),
    fn("phone_open_app", "Open an app on the user's own phone, for example WhatsApp, Chrome or Maps. The phone must be unlocked.",
       {"app": S}),
    fn("phone_find_contact", "Look up a contact's phone numbers on the user's phone by name.", {"name": S}),
    fn("phone_send_whatsapp", "Send a WhatsApp message from the user's own phone. contact is a contact name or a phone "
       "number. Always needs the user's approval on the dashboard before it is sent.", {"contact": S, "message": S}),
    fn("phone_locate", "Find where the user's own phone is: its last known position with a map link.", {}),
    fn("phone_ring", "Make the user's own phone play a loud sound so it can be found nearby.", {}),
    fn("watch_alert", "Show a short alert on the user's watch (it arrives as a notification on their phone, which the watch "
       "mirrors). Use when they ask to be told something on their watch.", {"title": S, "text": S}, ["text"]),
    fn("phone_read_screen", "Read what is on the user's phone screen right now: the visible texts, buttons and fields. "
       "Do this before tapping so you know what is there.", {}),
    fn("phone_tap", "Tap something on the user's phone screen. Give the visible text of the button (target), or x and y.",
       {"target": S, "x": {"type": "integer"}, "y": {"type": "integer"}}, []),
    fn("phone_type", "Type text into the field that is selected on the user's phone. Tap the field first.", {"text": S}),
    fn("phone_press", "Press a phone key: back, home, recents, enter, delete, volume up, volume down, play, pause, next, lock, wake.",
       {"key": S}),
    fn("phone_swipe", "Swipe the phone screen up, down, left or right (swipe up to scroll down a page).", {"direction": S}),
    fn("phone_shell", "Run an Android shell command on the user's phone. Owner only, and always needs approval.", {"command": S}),
    fn("phone_screenshot", "Take a screenshot of the user's phone screen and save it to their folder.", {}),
    fn("create_image", "Create a picture (PNG) from a description with Google's image model, using this login's own Gemini key. "
       "shape is square, tall or wide.", {"prompt": S, "shape": S}, ["prompt"]),
    fn("create_video", "Create a finished cinematic social media video (MP4) from a plain description. No media is "
       "needed: she writes the script, finds stock footage, adds voice-over, music, titles and captions. "
       "format is vertical, square or landscape. Takes a few minutes.",
       {"prompt": S, "seconds": {"type": "integer"}, "format": S, "voiceover": {"type": "boolean"}}, ["prompt"]),
    fn("revise_video", "Change an existing video the user already made: remake it applying a requested change - a different "
       "length, format (vertical/square/landscape), voice-over on or off, new footage, or new wording/subject. Needs the "
       "video's id (the number shown in the Videos list) and a plain description of the change.",
       {"video_id": {"type": "integer"}, "change": S}, ["video_id", "change"]),
    fn("create_ai_clip", "Generate a short AI-made video clip (2 to 15 seconds, with sound) on this PC's own graphics card with "
       "FreeVideo. Use only when the user asks for an AI video, AI clip or generated clip. Takes several minutes. "
       "format is vertical, square or landscape.",
       {"prompt": S, "seconds": {"type": "integer"}, "format": S}, ["prompt"]),
    fn("create_ebook", "Write and lay out a designed ebook (PDF with cover, contents page and chapters) from a plain description. "
       "Takes several minutes.", {"prompt": S, "chapters": {"type": "integer"}}, ["prompt"]),
    fn("gumroad_sales", "The user's Gumroad store sales and earnings for a period. period is one of: today, week, days30, month, all.",
       {"period": S}),
    fn("gumroad_publish", "Publish or unpublish one product in the user's Gumroad store. product is its name.",
       {"product": S, "published": {"type": "boolean"}}),
    fn("gumroad_discount_code", "Create a percentage discount code for one product in the user's Gumroad store.",
       {"product": S, "code": S, "percent": {"type": "integer"}}),
    fn("gumroad_set_price", "Change the price of one product in the user's Gumroad store. price is in the product's currency, "
       "like 9.99. Needs the user's approval.", {"product": S, "price": {"type": "number"}}),
    fn("gumroad_refund", "Refund one Gumroad sale in full. sale is the buyer's email address or the sale number. "
       "Needs the user's approval.", {"sale": S}),
    fn("network_devices", "See what's on the user's home network — find a smart TV, printer, camera, phone, the "
       "router. Use this for ANY question about devices on the network, Wi-Fi, LAN, or 'what's connected'. Returns "
       "each device's IP, MAC, vendor, name and a best-guess type. This is NOT the car manual — use it whenever the "
       "user mentions their network, an IP address, a TV, or a device at home. deep=true also port-scans (slower).",
       {"deep": {"type": "boolean"}}),
    fn("find_device", "Identify ONE device on the home network by its IP address (e.g. 192.168.68.111) or by name "
       "(e.g. 'the TV'). Pings it, reads its MAC/vendor/hostname, checks open ports and asks it over SSDP, then says "
       "what it most likely is and how to use it. Use this when the user gives an IP or asks 'what is this device' / "
       "'can you see my TV'.", {"target": S}, ["target"]),
    fn("wake_device", "Send a Wake-on-LAN packet to turn on a device (TV, PC) that supports it. Give an IP, name or "
       "MAC address.", {"target": S}, ["target"]),
    fn("tv_connect", "Connect to an Android smart TV over the network with ADB (target is its IP or name). Needed "
       "before the other tv_ tools. Tells you clearly if the TV's Developer-options / network debugging isn't on yet.",
       {"target": S}, ["target"]),
    fn("tv_apps", "List the apps on a connected Android TV, and which of them are launchers (home screens).",
       {"target": S}, ["target"]),
    fn("tv_install_launcher", "Install a new home-screen launcher on an Android TV and set it as default — use when "
       "the TV's own launcher is dead or unwanted. which is 'flauncher' (light & open-source, the default). The APK "
       "is fetched from F-Droid. For any other launcher, use tv_install_app with its APK.", {"target": S, "which": S}, ["target"]),
    fn("tv_install_app", "Sideload an APK onto an Android TV. apk is a file path on the PC or an http(s) URL.",
       {"target": S, "apk": S}, ["target", "apk"]),
    fn("tv_set_launcher", "Set an installed launcher as the Android TV's default home. package e.g. "
       "me.efesser.flauncher.", {"target": S, "package": S}, ["target", "package"]),
    fn("tv_press", "Press a remote button on the Android TV: up, down, left, right, ok, back, home, menu, vol_up, "
       "vol_down, mute, power, play_pause, next, prev, search, input (switch HDMI source, e.g. to a Chromecast), "
       "settings. Use when the user says to navigate or control the TV.",
       {"target": S, "button": S}, ["target", "button"]),
    fn("tv_type", "Type text into whatever is focused on the Android TV (e.g. a search box).",
       {"target": S, "text": S}, ["target", "text"]),
    fn("tv_open_app", "Open an app on the Android TV by its package name (find it with tv_apps).",
       {"target": S, "package": S}, ["target", "package"]),
    fn("tv_shell", "Run a raw ADB shell command on an Android TV. Owner only, needs approval.",
       {"target": S, "command": S}, ["target", "command"]),
    fn("search_manual", "Search the user's uploaded vehicle workshop/repair manuals (e.g. the XUV500 manual) for a "
       "question ABOUT A VEHICLE only: where a car part is, how to remove or fix it, torque values, fuses, car wiring, "
       "fault codes. Do NOT use this for the home network, Wi-Fi, IP addresses, TVs or any non-vehicle question. "
       "Returns the best pages with section, page number, an extract and a link. manual is optional.",
       {"query": S, "manual": S}, ["query"]),
    fn("read_manual_page", "Read every word on one page of a manual, after search_manual found it. page is the PDF page number.",
       {"manual": S, "page": {"type": "integer"}}, ["page"]),
    fn("list_manuals", "List the workshop manuals that have been read in: where each PDF is, its pages and its sections.", {}),
    fn("add_manual", "Read a PDF workshop or repair manual into the manual library so it can be searched page by page. "
       "path is the PDF on the PC. A big manual takes a few minutes; it carries on by itself.", {"path": S, "name": S}, ["path"]),
    fn("split_manual", "Save each section of a manual (Engine, Brakes, Wiring ...) as its own PDF in a folder next to it.",
       {"manual": S}, []),
    fn("vendors", "List the vendors in the user's channel portfolio and their stored context (target, partners, "
       "QBR date, status, notes).", {}),
    fn("vendor_add", "Add or update a vendor in the user's channel portfolio. Fields (all optional except name): "
       "category, target, partners, qbr (next QBR date), status, notes.",
       {"name": S, "category": S, "target": S, "partners": S, "qbr": S, "status": S, "notes": S}, ["name"]),
    fn("vendor_brief", "Build a QBR-ready brief for one vendor: pulls the stored context, the user's own documents "
       "and notes about that vendor, and fresh web news, then writes a brief with suggested next actions. Use for "
       "QBR prep, 'brief me on <vendor>', vendor updates. Set fresh=false to skip the web news (faster).",
       {"name": S, "fresh": {"type": "boolean"}}, ["name"]),
    fn("vendor_remove", "Remove a vendor from the portfolio.", {"name": S}, ["name"]),
    fn("boardroom", "Convene the Boardroom: a council of AI advisors (Bezos, Buffett, Hormozi, Paul Graham, "
       "Gary V, Naval and more) argues out a hard business decision on the local brain, then hands back a scored "
       "verdict (GO / GO WITH CONDITIONS / RESHAPE / DON'T DO IT) with a scorecard and next steps, saved to "
       "notes. Use for 'should I...', pricing calls, vendor bets, hiring, big roadmap choices, 'run this past "
       "the board'. decision = the call plus the user's numbers and constraints. options = the 2-4 choices (if "
       "omitted it's a yes/no). mode 'quick' seats 4 for one round (lighter), 'full' seats ~7 for a debate. "
       "seats = optional explicit list of advisor keys to seat.",
       {"decision": S, "options": ARR, "mode": S, "seats": ARR}, ["decision"]),
    fn("boardroom_history", "List the user's past Boardroom verdicts (decision, verdict, date).", {}),
    fn("boardroom_add_advisor", "Add a new advisor to the user's Boardroom, built from how that real person "
       "thinks (like the built-in Bezos/Buffett/Hormozi seats). name is the person. pillar (optional) is the "
       "business area they own, e.g. 'brand and storytelling'; focus (optional) is extra context. Use for 'add "
       "<name> to the board', 'put <name> on my board'.", {"name": S, "pillar": S, "focus": S}, ["name"]),
    fn("boardroom_advisors", "List the advisors on the user's Boardroom (built-in and their own added ones).", {}),
    fn("boardroom_ask", "Ask ONE advisor a question and get their answer in depth (lighter than convening the "
       "whole board). seat is the advisor (e.g. 'buffett', 'hormozi', or a name). Use for 'ask Hormozi …', "
       "'what would Buffett say about …', a quick single opinion.", {"seat": S, "question": S}, ["seat", "question"]),
    fn("inbox_triage", "Read the user's Gmail inbox (last few days) and sort what needs them from the noise: each "
       "with a one-line summary, priority and whether it needs action, highest first. Use for 'check my inbox', "
       "'what needs my attention', 'any important emails'.", {}),
    fn("draft_email_reply", "Draft a reply to an inbox email in the user's voice (nothing is sent — a draft for them "
       "to review and send). uid is the email's id from inbox_triage. instruction is optional guidance on what to "
       "say.", {"uid": S, "instruction": S}, ["uid"]),
    fn("deep_research", "Research a question on the web properly: plan several searches, read the top pages via the "
       "private SearXNG engine, and write a short sourced brief with [n] citations. Use for 'research …', vendor or "
       "competitor briefings, QBR prep, 'find out everything about …'. Takes up to a minute; the brief is saved to "
       "notes.", {"question": S}, ["question"]),
    fn("ask_my_stuff", "Answer a question from the user's OWN documents and notes (their private document brain). "
       "Use whenever they ask about something that would be in their files, reports, contracts, decks or notes — "
       "'what did we agree', 'find in my docs', 'according to my files'. Returns the matching passages and their "
       "source files for you to answer from.", {"query": S}, ["query"]),
    fn("index_documents", "Add a folder (or a single file) to the user's document brain so ask_my_stuff can search "
       "it. path is a folder or file on the PC. Reads PDF, Word, text, markdown, CSV and HTML. Runs in the background.",
       {"path": S}, ["path"]),
    fn("list_documents", "List the folders/files already in the user's document brain.", {}),
    fn("forget_documents", "Remove a document or folder from the document brain by its name or path.",
       {"source": S}, ["source"]),
    fn("self_heal", "Fix Trillion's own common breakages and report what was done: start Docker if it's asleep, "
       "restart a business app that fell over, reconnect a dropped connector. Safe and reversible; heavier fixes are "
       "reported for approval. Owner only. Use when the user says fix yourself, self-heal, or something's broken.", {}),
    fn("update_self", "Update Trillion herself to the newest version from GitHub, then restart. Owner only. "
       "Use when the user says update yourself, get the latest version, or pull the update.", {}),
    fn("run_command", "Run a Windows command. Owner only, and always needs approval on the dashboard.",
       {"command": S}),
]
NEEDS_APPROVAL = {"delete_path", "run_command", "phone_send_whatsapp", "phone_shell", "gumroad_set_price", "gumroad_refund",
                  "tv_install_app", "tv_install_launcher", "tv_set_launcher", "tv_shell",
                  }
MONEY = {"gumroad_set_price", "gumroad_refund"}      # payment matters: these always wait for your OK, even on a call
_CTX = threading.local()


class on_call:
    """While you are on the phone with Trillion, what you ask for is done at once (you said it out loud and heard it
    repeated back). Only payment matters (MONEY) still wait for your OK on the dashboard."""

    def __enter__(self):
        _CTX.call = True

    def __exit__(self, *a):
        _CTX.call = False


def calling():
    return bool(getattr(_CTX, "call", False))


FULL_PHONE = {"phone_read_screen", "phone_tap", "phone_type", "phone_press", "phone_swipe", "phone_shell"}


def resolve(user, path):
    raw = str(path or "").strip().strip('"')
    home = core.user_home(user)
    if raw in ("", ".", "~"):
        p = home
    elif raw.startswith("~"):
        p = home / raw[1:].lstrip("/\\")
    else:
        p = Path(os.path.expandvars(raw))
        if not p.is_absolute():
            p = home / p
    p = p.resolve()
    roots = core.user_roots(user)
    if "*" in roots:
        return p
    for r in roots:
        R = Path(r).resolve()
        if p == R or R in p.parents:
            return p
    raise PermissionError(f"{user['name']} is not allowed to use {p}")


def describe(name, a):
    g = lambda k: str(a.get(k, ""))[:70]
    return {
        "list_folder": f"Looking in {g('path')}", "find_files": f"Searching for {g('pattern')}",
        "read_file": f"Reading {g('path')}", "write_file": f"Writing {g('path')}",
        "make_folder": f"Creating folder {g('path')}", "copy_path": f"Copying {g('source')}",
        "move_path": f"Moving {g('source')}", "delete_path": f"Delete {g('path')}",
        "open_path": f"Opening {g('path')}", "create_word": f"Creating Word document {g('path')}",
        "create_excel": f"Creating Excel workbook {g('path')}",
        "create_image": f"Creating a picture: {g('prompt')[:80]}",
        "create_powerpoint": f"Creating PowerPoint {g('path')}", "save_note": f"Remembering: {g('title')}",
        "search_memory": f"Searching memory for {g('query')}", "check_website": f"Checking {g('url')}",
        "web_search": f"Searching the web for {g('query')}",
        "announce": f"Announcing on the speaker: {g('text')[:60]}", "cast_media": f"Casting {g('url')[:60]}",
        "cast_control": f"Cast {g('action')}", "list_cast_devices": "Looking for cast devices",
        "list_cameras": "Listing cameras", "camera_snapshot": f"Taking a picture from {g('camera')}",
        "camera_move": f"Moving {g('camera')} {g('action')}",
        "use_skill": f"Reading the skill {g('name')}", "draft_skill": f"Drafting a playbook: {g('name')}",
        "connector_tools": f"Checking what {g('connector')} can do",
        "connector_call": f"Using {g('connector')}: {g('tool')}",
        "pitch_video": "Making a pitch video",
        "rea_phone_app": f"Reverse-engineering the phone app {g('app')} (pulling its APK and handing it to Claude Code)",
        "rea_phone_traffic": f"Capturing the network traffic of the phone app {g('app')}",
        "gumroad_sales": "Reading Gumroad sales", "gumroad_publish": f"{'Publish' if a.get('published') else 'Unpublish'} {g('product')} on Gumroad",
        "gumroad_discount_code": f"Gumroad code {g('code')}: {g('percent')}% off {g('product')}",
        "gumroad_set_price": f"Change the Gumroad price of {a.get('_name') or g('product')} to {g('price')}",
        "gumroad_refund": f"Refund the Gumroad sale of {a.get('_name') or g('sale')}",
        "phone_status": "Checking the phone", "phone_open_app": f"Opening {g('app')} on the phone",
        "phone_find_contact": f"Looking up {g('name')} in contacts",
        "phone_send_whatsapp": f"Send WhatsApp to {a.get('_name') or g('contact')}"
                               + (f" (+{a.get('contact')})" if a.get('_name') and a.get('contact') and not str(a.get('contact', '')).startswith('chat:') else "") + (f": \"{str(a.get('message', ''))[:120]}\"" if str(a.get('message', '')).strip() else "")
                               + (f" with {len(a['files'])} file{'s' if len(a['files']) != 1 else ''}" if a.get('files') else ""),
        "phone_read_screen": "Reading the phone screen", "phone_tap": f"Tapping {g('target') or 'the screen'}",
        "phone_type": "Typing on the phone", "phone_press": f"Pressing {g('key')}", "phone_swipe": f"Swiping {g('direction')}",
        "phone_shell": f"Run on the phone: {g('command')}",
        "phone_locate": "Locating the phone", "phone_ring": "Ringing the phone",
        "clone_website": f"{'Copying' if g('mode') == 'copy' else 'Rebuilding the style of'} {g('url')}",
        "seo_rankings": "Checking Google rankings",
        "competitor_watch": "Checking competitor websites",
        "automations": "Checking automations", "automation_switch": f"Switching {g('name')} {'on' if a.get('on') else 'off'}",
        "read_website": f"Reading the website {g('url')}",
        "watch_alert": f"Alert on the watch: {g('text')[:80]}",
        "phone_screenshot": "Taking a phone screenshot",
        "create_video": "Creating a video",
        "revise_video": "Changing the video",
        "create_ai_clip": "Generating an AI clip",
        "create_ebook": "Writing an ebook",
        "search_manual": f"Searching the manual for {g('query')}", "read_manual_page": f"Reading manual page {g('page')}",
        "list_manuals": "Listing the manuals", "add_manual": f"Reading in the manual {g('path')}",
        "split_manual": "Splitting the manual into sections",
        "network_devices": "Scanning the home network", "find_device": f"Looking up {g('target')} on the network",
        "wake_device": f"Sending a wake signal to {g('target')}",
        "tv_connect": f"Connecting to the TV ({g('target')})", "tv_apps": "Listing the TV's apps",
        "tv_install_launcher": f"Installing {g('which') or 'a launcher'} on the TV", "tv_install_app": "Installing the app on the TV",
        "tv_set_launcher": "Setting the TV's default launcher", "tv_shell": f"Running on the TV: {g('command')}",
        "tv_press": f"TV: pressing {g('button')}", "tv_type": "TV: typing", "tv_open_app": f"TV: opening {g('package')}",
        "vendors": "Listing your vendors", "vendor_add": f"Saving {g('name')}", "vendor_brief": f"Briefing on {g('name')}",
        "vendor_remove": f"Removing {g('name')}",
        "boardroom": "Convening the Boardroom", "boardroom_history": "Your past Boardroom verdicts",
        "boardroom_add_advisor": f"Building the {g('name')} seat", "boardroom_advisors": "Your board of advisors",
        "boardroom_ask": f"Asking {g('seat')}",
        "inbox_triage": "Going through your inbox", "draft_email_reply": "Drafting a reply",
        "deep_research": f"Researching: {g('question')}",
        "ask_my_stuff": f"Searching your documents for {g('query')}", "index_documents": f"Indexing {g('path')}",
        "list_documents": "Listing your documents", "forget_documents": f"Removing {g('source')} from your documents",
        "system_status": "Checking the PC", "self_heal": "Fixing myself", "update_self": "Updating myself from GitHub", "run_command": f"Run command: {g('command')}",
    }.get(name, name)


def execute(user, name, args, task_id=None, approved=False, direct=False):
    """Run one tool. Returns a JSON-safe dict. Never raises."""
    uid = user["id"]
    try:
        if name not in {s["function"]["name"] for s in SPECS}:
            return {"error": f"Unknown tool {name}"}
        if task_id:                                   # record this stop on the job's path for the workflow view
            if name == "use_skill":
                core.add_step(task_id, "skill", str(args.get("name") or args.get("skill") or "skill"), "reading skill")
            elif name in ("connector_tools", "connector_call"):
                core.add_step(task_id, "connector", str(args.get("connector") or "connector"),
                              f"using {args.get('tool')}" if args.get("tool") else "checking")
            else:
                core.add_step(task_id, "tool", name, describe(name, args)[:120])
        if name == "run_command" and not user["is_admin"]:
            return {"error": "Only the owner login can run commands."}
        if name == "phone_shell" and not user["is_admin"]:
            return {"error": "Only the owner login can run commands on a phone."}
        if name in FULL_PHONE and not core.settings(user).get("phone_full_control"):
            return {"error": "Full phone control is switched off. Switch it on on the Phone page."}
        if name == "update_self" and not user["is_admin"]:
            return {"error": "Only the owner login can update Trillion."}
        if name in NEEDS_APPROVAL and not approved:
            if name == "delete_path":
                resolve(user, args.get("path"))  # permission check before asking
            if name == "gumroad_set_price":       # settle exactly which product before asking
                import gumroad
                p = gumroad.find_product(user, args.get("product"))
                args = {"product": p["id"], "price": float(args.get("price")), "_name": f"{p['name']} (now {p['shown']})"}
            if name == "gumroad_refund":          # settle exactly which sale before asking
                import gumroad
                s_ = gumroad.find_sale(user, args.get("sale"))
                if s_["refunded"]:
                    return {"error": "That sale is already refunded."}
                args = {"sale": s_["id"], "_name": f"{s_['product']} to {s_['email']} ({s_['shown']})"}
            if name == "phone_send_whatsapp":  # settle exactly who it goes to before asking
                import phone
                who, number = phone.resolve_contact(user, str(args.get("contact", "")))
                ids = [int(i) for i in (args.get("files") or []) if str(i).isdigit()][:10]
                args = {"contact": number, "message": str(args.get("message") or ""), "_name": who}
                if ids:
                    if len(whatsapp_files(user, ids)) != len(set(ids)):
                        return {"error": "One of the attached files is no longer on the PC. Attach it again."}
                    args["files"] = ids
                if not args["message"].strip() and not ids:
                    return {"error": "There is no message to send."}
                st = core.settings(user)
                own = phone.normalise(st["phone_own_number"], str(st.get("phone_country_code", "27"))) if st.get("phone_own_number") else None
                in_contacts = who != number and not re.fullmatch(r"[\d\s+()-]{7,}", who)   # a saved name, not a typed number
                # the user's standing choice: saved contacts get the message at once, however the request was worded
                if st.get("whatsapp_auto_contacts") and (in_contacts or number == own):
                    out = t_phone_send_whatsapp(user, **args)
                    core.log(uid, describe(name, args), "done")
                    return out
            if calling() and name not in MONEY:           # asked for on a call: no approval needed (but never payments)
                out = globals()["t_" + name](user, **args)
                core.log(uid, describe(name, args) + " (asked for on a call)", "done")
                return out
            summary = describe(name, args)
            core.x("INSERT INTO approvals(user_id,task_id,tool,args,summary,created) VALUES(?,?,?,?,?,?)",
                   (uid, task_id, name, json.dumps(args), summary, time.time()))
            core.log(uid, f"Waiting on you: {summary}", "waiting")
            watch.alert(user, "approval", "Trillion needs your OK", summary)
            return {"status": "waiting_for_approval",
                    "note": "Not done yet. The user must approve this on the dashboard."}
        if name in ("create_video", "revise_video", "create_ai_clip", "create_ebook", "boardroom",
                    "boardroom_ask"):
            args = dict(args, _task=task_id)
        out = globals()["t_" + name](user, **args)
        core.log(uid, describe(name, args), "done")
        return out
    except PermissionError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"Wrong details for {name}: {e}"}
    except Exception as e:
        if type(e).__name__ == "PhoneError":
            return {"error": str(e)}
        return {"error": f"{type(e).__name__}: {e}"}


# ---------- files ----------
def t_list_folder(user, path="."):
    p = resolve(user, path)
    items = []
    for e in sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))[:300]:
        try:
            items.append({"name": e.name, "type": "folder" if e.is_dir() else "file",
                          "size": e.stat().st_size if e.is_file() else None})
        except OSError:
            pass
    return {"folder": str(p), "items": items}


def t_find_files(user, folder=".", pattern="*"):
    p = resolve(user, folder)
    hits = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d not in ("node_modules", "AppData")]
        for f in files:
            if fnmatch.fnmatch(f.lower(), pattern.lower()):
                hits.append(str(Path(root) / f))
                if len(hits) >= 100:
                    return {"matches": hits, "note": "Stopped at 100 matches."}
    return {"matches": hits}


def t_read_file(user, path):
    p = resolve(user, path)
    if p.stat().st_size > 2_000_000:
        return {"error": "File is too large to read as text."}
    return {"path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")[:20000]}


def t_write_file(user, path, content):
    p = resolve(user, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"saved": str(p)}


def t_make_folder(user, path):
    p = resolve(user, path)
    p.mkdir(parents=True, exist_ok=True)
    return {"created": str(p)}


def t_copy_path(user, source, destination):
    s, d = resolve(user, source), resolve(user, destination)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return {"error": f"{d} already exists. Choose another name."}
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s, d) if s.is_dir() else shutil.copy2(s, d)
    return {"copied_to": str(d)}


def t_move_path(user, source, destination):
    s, d = resolve(user, source), resolve(user, destination)
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return {"error": f"{d} already exists. Choose another name."}
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"moved_to": str(d)}


def t_delete_path(user, path):
    p = resolve(user, path)
    if p == core.user_home(user) or len(p.parts) <= 2:
        return {"error": "Refusing to delete a top-level folder."}
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    return {"deleted": str(p)}


def t_open_path(user, path):
    p = resolve(user, path)
    if hasattr(os, "startfile"):
        os.startfile(str(p))  # Windows only
        return {"opened": str(p)}
    return {"error": "Opening things on screen only works on the Windows PC."}


# ---------- documents ----------
def _with_ext(user, path, ext):
    p = resolve(user, path)
    if p.suffix.lower() != ext:
        p = p.with_suffix(ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def t_create_word(user, path, title="", paragraphs=None):
    from docx import Document
    p = _with_ext(user, path, ".docx")
    d = Document()
    if title:
        d.add_heading(title, 0)
    for para in paragraphs or []:
        d.add_paragraph(str(para))
    d.save(p)
    return {"saved": str(p)}


def t_create_excel(user, path, sheet_name="Sheet1", rows=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    p = _with_ext(user, path, ".xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Sheet1")[:31]
    for r in rows or []:
        out = []
        for v in r:
            try:
                out.append(float(v) if str(v).replace(".", "", 1).replace("-", "", 1).isdigit() else v)
            except Exception:
                out.append(v)
        ws.append(out)
    for c in ws[1] if ws.max_row else []:
        c.font = Font(bold=True)
    wb.save(p)
    return {"saved": str(p)}


def t_create_powerpoint(user, path, title="", slides=None):
    from pptx import Presentation
    p = _with_ext(user, path, ".pptx")
    prs = Presentation()
    if title:
        s = prs.slides.add_slide(prs.slide_layouts[0])
        s.shapes.title.text = title
    for sl in slides or []:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = str(sl.get("title", ""))
        tf = s.placeholders[1].text_frame
        bullets = [str(b) for b in sl.get("bullets", [])]
        if bullets:
            tf.text = bullets[0]
            for b in bullets[1:]:
                tf.add_paragraph().text = b
    prs.save(p)
    return {"saved": str(p)}


# ---------- memory ----------
def add_note(uid, title, body, domain="PERSONAL"):
    import vstore
    domain = "WORK" if str(domain).upper().startswith("W") else "PERSONAL"
    emb = core.embed(f"{title}\n{body}")
    nid = core.x("INSERT INTO notes(user_id,title,body,domain,ts,emb) VALUES(?,?,?,?,?,?)",
                 (uid, title[:200], body[:8000], domain, time.time(), json.dumps(emb) if emb else None))
    vstore.add(uid, nid, f"{title}\n{body}", emb, {"d": domain})   # best-effort; falls back to SQLite search
    return nid


def search_notes(uid, query, k=5):
    import vstore
    qe = core.embed(query) if query else None
    # When Chroma is in play, let it pick the strongest candidates (it scales past a flat SQLite scan);
    # otherwise fall back to the most recent notes. Either way the scoring below has the final say.
    rows = []
    if query and vstore.available():
        ids = vstore.search(uid, query, qe, k=max(40, k * 8))
        if ids:
            marks = ",".join("?" * len(ids))
            rows = core.q(f"SELECT * FROM notes WHERE user_id=? AND id IN ({marks})", (uid, *ids))
    if not rows:
        rows = core.q("SELECT * FROM notes WHERE user_id=? ORDER BY ts DESC LIMIT 2000", (uid,))
    if not rows:
        return []
    scored = []
    words = [w for w in query.lower().split() if len(w) > 2]
    for r in rows:
        cos = 0.0
        if qe and r["emb"]:
            e = json.loads(r["emb"])
            dot = sum(a * b for a, b in zip(qe, e))
            na, nb = math.sqrt(sum(a * a for a in qe)), math.sqrt(sum(b * b for b in e))
            cos = dot / (na * nb) if na and nb else 0.0
        text = (r["title"] + " " + r["body"]).lower()
        hits = sum(1 for w in words if w in text)
        if hits or cos > 0.55 or not query:
            scored.append((cos + 0.15 * hits, r))
    scored.sort(key=lambda s: -s[0])
    return [{"id": r["id"], "title": r["title"], "body": r["body"], "domain": r["domain"], "ts": r["ts"]}
            for s, r in scored[:k]]


def t_save_note(user, title, body, domain="PERSONAL"):
    return {"saved_note": add_note(user["id"], title, body, domain)}


def t_search_memory(user, query):
    return {"results": [{"title": n["title"], "body": n["body"][:1500]} for n in search_notes(user["id"], query)]}


def t_pitch_video(user, site_id, scores=None):
    import pitchvideo
    clean = {}
    for k, v in (scores or {}).items():
        try:
            clean[str(k)[:20]] = [max(0, min(100, int(v[0]))), max(0, min(100, int(v[1])))]
        except Exception:
            pass
    r = pitchvideo.start(user, int(site_id), clean or None)
    return dict(r, note="Rendering in the background, about 2-4 minutes. It appears on the Web Studio page, and the "
                        "WhatsApp pitch for this lead will send it with the link.")


def t_web_search(user, query, count=6):
    """Query the private local SearXNG engine (installed on the Apps page) and return clean results."""
    import apps
    try:
        base = apps.base("searxng")
    except Exception:
        base = "http://127.0.0.1:8899"
    try:
        r = httpx.get(base + "/search", params={"q": query, "format": "json", "safesearch": 1},
                      timeout=25, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {"error": "Private web search (SearXNG) isn't installed or running yet. Install it on the Apps page, "
                         "then try again."}
    out = []
    for x in (data.get("results") or [])[:max(1, min(int(count or 6), 12))]:
        out.append({"title": x.get("title"), "url": x.get("url"), "snippet": (x.get("content") or "")[:300]})
    return {"results": out} if out else {"results": [], "note": "No results found."}


# ---------- cast: Google Home / Chromecast ----------
# ---------- workshop manuals ----------
def _manual(fn_):
    import manuals
    try:
        return fn_(manuals)
    except manuals.ManualError as e:
        return {"error": str(e)}


def t_network_devices(user, deep=False):
    import netscan
    try:
        return netscan.scan(deep=bool(deep), user=user)
    except Exception as e:
        return {"error": f"Could not scan the network ({type(e).__name__})."}


def t_find_device(user, target):
    import netscan
    try:
        return netscan.lookup(target, user=user)
    except Exception as e:
        return {"error": f"Could not look up {target} ({type(e).__name__})."}


def t_wake_device(user, target):
    import netscan
    try:
        return netscan.wake(target, user=user)
    except Exception as e:
        return {"error": f"Could not wake {target} ({type(e).__name__})."}


def t_tv_connect(user, target):
    import tvadb
    try:
        return tvadb.connect(target)
    except Exception as e:
        return {"error": f"Could not connect to the TV ({type(e).__name__}): {str(e)[:120]}"}


def t_tv_apps(user, target):
    import tvadb
    try:
        return tvadb.apps(target)
    except Exception as e:
        return {"error": f"Could not read the TV's apps ({type(e).__name__})."}


def t_tv_install_launcher(user, target, which="flauncher"):
    import tvadb
    try:
        return tvadb.install_launcher(target, which)
    except Exception as e:
        return {"error": f"Could not install the launcher ({type(e).__name__}): {str(e)[:120]}"}


def t_tv_install_app(user, target, apk):
    import tvadb
    try:
        return tvadb.install(target, apk)
    except Exception as e:
        return {"error": f"Could not install the app ({type(e).__name__}): {str(e)[:120]}"}


def t_tv_set_launcher(user, target, package):
    import tvadb
    try:
        return tvadb.set_launcher(target, package)
    except Exception as e:
        return {"error": f"Could not set the launcher ({type(e).__name__})."}


def t_tv_press(user, target, button):
    import tvadb
    try:
        return tvadb.key(target, button)
    except Exception as e:
        return {"error": f"Could not press that on the TV ({type(e).__name__})."}


def t_tv_type(user, target, text):
    import tvadb
    try:
        return tvadb.text(target, text)
    except Exception as e:
        return {"error": f"Could not type on the TV ({type(e).__name__})."}


def t_tv_open_app(user, target, package):
    import tvadb
    try:
        return tvadb.launch(target, package)
    except Exception as e:
        return {"error": f"Could not open that app on the TV ({type(e).__name__})."}


def t_tv_shell(user, target, command):
    import tvadb
    try:
        return tvadb.tv_shell(target, command)
    except Exception as e:
        return {"error": f"Could not run that on the TV ({type(e).__name__})."}


def t_search_manual(user, query, manual=""):
    def go(manuals):
        hits = manuals.search(user, query, manual or None)
        if not hits:
            return {"hits": [], "note": "Nothing in the manuals matches that. Try other words, or the part's name."}
        return {"hits": hits, "note": "Give the user the link of the page that answers it so they can see the picture."}
    return _manual(go)


def t_read_manual_page(user, page, manual=""):
    return _manual(lambda manuals: manuals.page_text(user, manuals.find(user, manual)["id"], page))


def t_list_manuals(user):
    return _manual(lambda manuals: {"manuals": manuals.listing(user)})


def t_add_manual(user, path, name=""):
    p = resolve(user, path)
    return _manual(lambda manuals: {"manual": manuals.add(user, p, name or None),
                                    "note": "It is being read now; search_manual works once it says ready."})


def t_split_manual(user, manual=""):
    return _manual(lambda manuals: manuals.split(user, manuals.find(user, manual)["id"]))


def t_list_cast_devices(user):
    import cast
    if not cast.available():
        return {"error": "Casting isn't set up yet (pychromecast not installed). Run Setup again to add it."}
    try:
        devs = cast.discover(force=True)
    except Exception as e:
        return {"error": f"Could not look for cast devices: {type(e).__name__}."}
    return {"devices": devs} if devs else {"devices": [], "note": "No Google Home or Chromecast found on the network."}


def t_announce(user, text, device=""):
    import cast
    try:
        where = cast.announce(text, device or None)
        return {"announced_on": where, "said": text[:200]}
    except cast.CastError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not announce: {type(e).__name__}."}


def t_cast_media(user, url, device="", content_type=""):
    import cast
    if not re.match(r"https?://", url or ""):
        return {"error": "Give a full http(s) link to the audio or video to play."}
    ctype = content_type or ("video/mp4" if re.search(r"\.(mp4|m3u8|webm)(\?|$)", url, re.I) else "audio/mpeg")
    try:
        return {"casting_on": cast.cast_url(url, device or None, ctype)}
    except cast.CastError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not cast: {type(e).__name__}."}


def t_cast_control(user, action, device="", level=None):
    import cast
    try:
        return {"device": cast.control(action, device or None, level), "action": action}
    except cast.CastError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not control the cast device: {type(e).__name__}."}


# ---------- cameras ----------
def t_list_cameras(user):
    import cameras
    cams = cameras.listing()
    return {"cameras": cams} if cams else {"cameras": [], "note": "No cameras set up yet. Add one on the Cameras page."}


def t_camera_snapshot(user, camera, display=""):
    import cameras, cast
    cam = cameras.find(camera)
    if not cam:
        return {"error": f"No camera called {camera}. Add it on the Cameras page.",
                "cameras": [c["name"] for c in cameras.listing()]}
    try:
        fn = cameras.snapshot(cam)
    except cameras.CameraError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not take the picture: {type(e).__name__}."}
    url = f"http://{cast.lan_ip()}:{cast.PORT}/cam/snap/{fn}"
    out = {"camera": cam["name"], "image_url": url, "show": {"image": url}}
    if display:
        try:
            out["shown_on"] = cast.cast_url(url, display, "image/jpeg", title=cam["name"])
        except Exception as e:
            out["display_error"] = f"Took the picture but could not put it on {display}: {type(e).__name__}."
    return out


def t_camera_move(user, camera, action):
    import cameras
    cam = cameras.find(camera)
    if not cam:
        return {"error": f"No camera called {camera}."}
    try:
        return {"camera": cam["name"], "moved": cameras.ptz(cam, action)}
    except cameras.CameraError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Could not move the camera: {type(e).__name__}."}


# ---------- web / system ----------
def site_check(url):
    if not url.startswith("http"):
        url = "https://" + url
    t = time.time()
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Trillion/0.1"})
        return {"url": url, "ok": r.status_code < 400, "status": r.status_code,
                "ms": int((time.time() - t) * 1000), "checked": time.time()}
    except Exception as e:
        return {"url": url, "ok": False, "status": 0, "error": type(e).__name__, "checked": time.time()}


def t_check_website(user, url):
    return site_check(url)


def t_system_status(user):
    return core.vitals()


def t_run_command(user, command):
    r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120,
                       cwd=str(core.user_home(user)))
    return {"exit_code": r.returncode, "output": (r.stdout + r.stderr)[-4000:]}


# ---------- self-update ----------
def _git(*args, timeout=120):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", *args], cwd=str(core.BASE), capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def version():
    try:
        v = (core.BASE / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        v = "0.1"
    try:
        code, out = _git("rev-parse", "--short", "HEAD", timeout=5)
        if code == 0:
            v += " (" + out + ")"
    except Exception:
        pass
    return v


RUNNING = None  # the version this running copy was started with (set at start-up)


def t_vendors(user):
    import vendors
    return {"vendors": vendors.listing(user)}


def t_vendor_add(user, name, category="", target="", partners="", qbr="", status="", notes=""):
    import vendors
    f = {k: v for k, v in dict(category=category, target=target, partners=partners, qbr=qbr,
                               status=status, notes=notes).items() if v}
    return vendors.add(user, name, **f)


def t_vendor_brief(user, name, fresh=True):
    import vendors
    return vendors.brief(user, name, fresh=bool(fresh))


def t_vendor_remove(user, name):
    import vendors
    return vendors.remove(user, name)


def t_boardroom(user, decision, options=None, mode="full", seats=None, _task=None):
    import boardroom
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    m = str(mode or "full").lower()
    m = "quick" if m.startswith("q") else "full"
    return boardroom.run(user, decision, options=options, mode=m, seats=seats, progress=say)


def t_boardroom_history(user):
    import boardroom
    return {"verdicts": boardroom.history(user)}


def t_boardroom_add_advisor(user, name, pillar="", focus=""):
    import boardroom
    return boardroom.add_advisor(user, name, pillar=pillar, focus=focus)


def t_boardroom_advisors(user):
    import boardroom
    return {"advisors": boardroom.seats_list(user)}


def t_boardroom_ask(user, seat, question, _task=None):
    import boardroom
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return boardroom.consult(user, seat, question, progress=say)


def t_inbox_triage(user):
    import inbox
    return inbox.triage(user)


def t_draft_email_reply(user, uid, instruction=""):
    import inbox
    return inbox.draft_reply(user, uid, instruction)


def t_deep_research(user, question):
    import research
    return research.research(user, question)


def t_ask_my_stuff(user, query):
    import askstuff
    return askstuff.ask(user, query)


def t_index_documents(user, path):
    import askstuff
    p = resolve(user, path)
    if not os.path.exists(p):
        return {"error": f"I can't find {path} on the PC."}
    return askstuff.start_ingest(user, str(p))


def t_list_documents(user):
    import askstuff
    return {"documents": askstuff.listing(user)}


def t_forget_documents(user, source):
    import askstuff
    return askstuff.forget(user, source)


def t_self_heal(user):
    import selfheal
    return selfheal.heal(user)


def t_update_self(user):
    if not (core.BASE / ".git").exists():
        return {"error": "Trillion is not connected to GitHub yet. Run '3 - Connect to GitHub.bat' once."}
    was = RUNNING or version()
    try:
        _, before = _git("rev-parse", "HEAD", timeout=10)
        code, out = _git("fetch", "origin")
    except FileNotFoundError:
        return {"error": "Git is not installed. Run Setup again."}
    except subprocess.TimeoutExpired:
        return {"error": "GitHub did not answer in time. Check the internet connection and try again."}
    if code != 0:
        return {"error": "Could not reach GitHub: " + out[-400:]}
    code, target = _git("rev-parse", "--abbrev-ref", "@{u}", timeout=10)
    if code != 0 or not target.strip():
        target = "origin/main"
    target = target.strip().splitlines()[-1]
    code, out = _git("merge", "--ff-only", target)
    if code != 0:
        # something on this PC is in the way of a normal update (an edited or half-written file):
        # take GitHub's copy exactly. Logins, keys, memory and files live in data/, which git never touches.
        code, out = _git("reset", "--hard", target)
        if code != 0:
            return {"error": "Could not apply the update: " + out[-400:]}
    _, after = _git("rev-parse", "HEAD", timeout=10)
    now = version()
    if before == after and now == was:
        return {"status": "already_up_to_date", "version": now}
    changes, files = "", ""
    if before != after:
        _, changes = _git("log", "--pretty=%s", f"{before}..{after}", timeout=10)
        _, files = _git("diff", "--name-only", before, after, timeout=10)
    if "requirements.txt" in files:
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
                       cwd=str(core.BASE), timeout=900)
    import threading
    threading.Timer(4, lambda: os._exit(42)).start()  # the start script restarts on code 42
    return {"status": "updated", "version": now, "was": was, "what_changed": changes.splitlines()[:10],
            "note": "Restarting in a few seconds. Tell the user the dashboard will reconnect by itself."}


# ---------- skills and connectors ----------
def t_use_skill(user, name):
    import extend
    sk = extend.find_skill(user["id"], name)
    if not sk:
        return {"error": f"No switched-on skill called {name}.",
                "available": [x["name"] for x in extend.enabled_skills(user["id"])]}
    extend.mark_used("skill", sk["slug"])
    return {"skill": sk["name"], "instructions": (extend.skill_body(sk["slug"]) or "")[:14000],
            "note": "Follow these instructions with your own tools. You cannot run scripts the skill refers to."}


def t_draft_skill(user, name, description, instructions):
    import extend
    name = name[:70]
    if core.q("SELECT 1 FROM skills WHERE slug=?", (extend.slugify(name),)):  # never overwrite an existing skill
        name = f"{name} (draft {time.strftime('%d %b %H%M')})"
    slug = extend.save_skill(name, description[:400], instructions[:20000], f"drafted for {user['name']}",
                             status="draft")
    core.log(user["id"], f"Waiting on you: new playbook '{name[:60]}' needs approval on the Skills page", "waiting")
    return {"status": "saved_as_draft", "name": name, "slug": slug,
            "note": "Tell the user it is waiting for the owner's approval on the Skills page."}


MANY_TOOLS = 30


def t_connector_tools(user, connector, find=""):
    import extend
    row = extend.find_connector(user["id"], connector)
    if not row:
        return {"error": f"No switched-on connector called {connector}.",
                "available": [r["name"] for r in extend.enabled_connectors(user["id"])]}
    extend.mark_used("connector", row["name"])
    tools = extend.client(row).tools()
    words = [w for w in re.findall(r"[a-z0-9]{3,}", str(find or "").lower())]
    if words:      # a long list narrowed down: tools whose name or description has the words, best matches first
        hay = lambda t: (t.get("name", "") + " " + (t.get("description") or "")).lower()
        scored = sorted(((sum(w in hay(t) for w in words) + sum(w in t.get("name", "").lower() for w in words), i, t)
                         for i, t in enumerate(tools)), key=lambda x: (-x[0], x[1]))
        hits = [t for n, _, t in scored if n][:12]
        return {"connector": row["name"], "find": find, "tools": extend.brief_tools(hits),
                "note": "" if hits else "No tool matched those words. Try other words, or leave find empty for the list of names."}
    if len(tools) > MANY_TOOLS:   # too many to describe at once for a local brain: names first
        return {"connector": row["name"], "count": len(tools), "names": [t.get("name") for t in tools],
                "note": "This connector has many tools. Call connector_tools again with find set to a few words "
                        "to get the matching ones with their arguments."}
    return {"connector": row["name"], "tools": extend.brief_tools(tools)}


def t_connector_call(user, connector, tool, arguments=None):
    import extend
    row = extend.find_connector(user["id"], connector)
    if not row:
        return {"error": f"No switched-on connector called {connector}."}
    extend.mark_used("connector", row["name"])
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    import rea
    if rea.is_rea(row):
        if not user["is_admin"]:
            return {"error": "REA is owner-only for now."}
        if rea.is_native_job(tool, arguments or {}):
            return rea.handoff(user, row, tool, arguments or {})
    return extend.client(row).call(tool, arguments or {})


def t_rea_phone_app(user, app):
    import rea
    return rea.phone_app(user, app)


def t_rea_phone_traffic(user, app, seconds=60):
    import rea
    if not user["is_admin"]:
        return {"error": "REA is owner-only for now."}
    import app as _app
    pc_ip = (_app.TAILSCALE.get("v") or {}).get("ip") or ""
    return rea.phone_traffic(user, app, seconds, pc_ip)


# ---------- the user's own phone ----------
def t_phone_status(user):
    import phone
    return phone.status(user)


def t_phone_open_app(user, app):
    import phone
    return phone.open_app(user, app)


def t_phone_find_contact(user, name):
    import phone
    found = phone.contacts(user, name)
    return {"contacts": [{"name": k, "numbers": v} for k, v in list(found.items())[:10]]} if found \
        else {"contacts": [], "note": f"Nobody called {name} in the contact list I can read. phone_send_whatsapp can still be "
                                      "called with the name: it looks the person up inside WhatsApp itself."}


def whatsapp_files(user, ids):
    """The files a login attached, by their upload numbers. Only that login's own files; anything else is left out."""
    import attach
    out = []
    for i in (ids or [])[:10]:
        f = attach.get(user, i) if str(i).isdigit() else None
        if f and f["path"] not in [o["path"] for o in out]:
            out.append(f)
    return out


def t_phone_send_whatsapp(user, contact, message="", _name=None, files=None):
    import phone
    found = whatsapp_files(user, files)
    if files and len(found) != len(set(str(i) for i in files[:10])):
        return {"error": "One of the attached files is no longer on the PC. Attach it again."}
    res = phone.send_whatsapp(user, contact, message, [f["path"] for f in found])
    if _name and not str(contact).startswith("chat:"):
        res["to"] = _name
    return res


def _gum(fn, user=None):
    import gumroad
    try:
        return fn(gumroad)
    except gumroad.GumroadError as e:
        return {"error": str(e)}


def t_gumroad_sales(user, period="today"):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    period = {"7": "week", "30": "days30", "total": "all"}.get(str(period).lower(), str(period).lower())
    def go(g):
        try:
            g.sync(user)
        except g.GumroadError:
            pass
        return {"answer": g.say_sales(user, period if period in ("today", "week", "days30", "month", "all") else "today")}
    return _gum(go)


def t_gumroad_publish(user, product, published=True):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        g.set_published(user, p["id"], bool(published))
        return {"ok": True, "product": p["name"], "published": bool(published)}
    return _gum(go)


def t_gumroad_discount_code(user, product, code="", percent=10):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        return {"ok": True, "product": p["name"], "code": g.create_code(user, p["id"], code or f"SAVE{int(percent)}", percent), "percent": percent}
    return _gum(go)


def t_gumroad_set_price(user, product, price, _name=None):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        p = g.find_product(user, product)
        g.update_product(user, p["id"], price_cents=int(round(float(price) * (1 if p["currency"] == "jpy" else 100))))
        return {"ok": True, "product": p["name"], "price": price}
    return _gum(go)


def t_gumroad_refund(user, sale, _name=None):
    if not core.settings(user).get("store_on"):
        return {"error": "The Gumroad store is not switched on for this login."}
    def go(g):
        s_ = g.find_sale(user, sale)
        g.refund(user, s_["id"])
        return {"ok": True, "refunded": s_["product"], "to": s_["email"]}
    return _gum(go)


def t_phone_locate(user):
    import phone
    return phone.locate(user)


def t_phone_ring(user):
    import phone
    return phone.ring(user)


def t_clone_website(user, url, mode="copy", brand="", about="", permission=False, max_pages=None):
    import siteclone, siteread
    try:
        j = siteclone.start(user, mode, url, {"brand": brand, "about": about},
                            max_pages or (siteclone.DEFAULT_COPY_PAGES if mode == "copy" else 4), bool(permission))
    except siteread.SiteError as e:
        return {"error": str(e)}
    return {"started": True, "note": siteclone.started_reply(j)}


def _app(fn, *a, **k):
    import apps
    try:
        return fn(*a, **k)
    except apps.AppError as e:
        return {"error": str(e)}


def t_competitor_watch(user, action, url="", name="", hours=6, days=7):
    import apps
    a = str(action or "").lower()
    if a.startswith("add") or a == "watch":
        return _app(apps.competitor_add, user, url, name, hours)
    if a.startswith("remove") or a.startswith("stop") or a.startswith("delete"):
        return _app(apps.competitor_remove, user, url or name)
    if a.startswith("change") or a.startswith("report") or a == "what changed":
        r = _app(apps.competitor_changes, user, days)
        return r if isinstance(r, dict) else {"changes": r, "note": "Nothing changed in that time." if not r else ""}
    r = _app(apps.competitor_list, user)
    return r if isinstance(r, dict) else {"watching": r}


def t_seo_rankings(user, domain=""):
    import apps
    return _app(apps.rankings, user, domain)


def t_automations(user):
    import apps
    return _app(apps.workflows, user)


def t_automation_switch(user, name, on=True):
    import apps
    return _app(apps.workflow_switch, user, name, on)


def t_read_website(user, url, outputs=None, focus="", max_pages=40):
    import siteread
    try:
        j = siteread.start(user, url, outputs or siteread.wants(focus or ""), focus, max_pages)
    except siteread.SiteError as e:
        return {"error": str(e)}
    return {"started": True, "note": siteread.started_reply(j)}


def t_watch_alert(user, text, title="Trillion"):
    r = watch.send(user, title or "Trillion", text)
    return {"ok": True, "note": "Posted on the phone; the watch shows it if phone notifications are mirrored."} if r["ok"] else {"error": r["error"]}


def t_phone_screenshot(user):
    import phone
    return phone.screenshot(user)


# ---------- pictures ----------
IMAGE_API = os.environ.get("TRILLION_IMAGE_API", "https://generativelanguage.googleapis.com/v1beta/models")
IMAGE_MODELS = ["gemini-3-pro-image-preview", "gemini-2.5-flash-image"]


def t_create_image(user, prompt, shape="square"):
    """Google's image model, called with the login's own key. Tries the newest model first, then the older one."""
    import base64
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"error": "Say what the picture should show."}
    key = core.get_secret(user["id"], "gemini")
    if not key:
        return {"error": "Making pictures needs a Google Gemini key. Add one under Settings, Keys (Google Gemini), tick it on and click SAVE."}
    shape = str(shape or "square").lower()
    ratio = "9:16" if shape in ("tall", "vertical", "portrait") else ("16:9" if shape in ("wide", "landscape", "horizontal") else "1:1")
    wanted = [m for m in [core.settings(user).get("image_model")] + IMAGE_MODELS if m]
    last = "no answer"
    for model in dict.fromkeys(wanted):
        for cfg in ({"responseModalities": ["IMAGE", "TEXT"], "imageConfig": {"aspectRatio": ratio}}, {"responseModalities": ["IMAGE", "TEXT"]}):
            try:
                r = httpx.post(f"{IMAGE_API}/{model}:generateContent", params={"key": key}, timeout=180,
                               json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg})
            except Exception as e:
                last = f"could not reach Google ({type(e).__name__})"
                break
            if r.status_code == 200:
                data = None
                for cand in r.json().get("candidates", []):
                    for part in (cand.get("content") or {}).get("parts", []):
                        blob = part.get("inlineData") or part.get("inline_data")
                        if blob and blob.get("data"):
                            data = base64.b64decode(blob["data"])
                            break
                    if data:
                        break
                if not data:
                    last = "Google answered without a picture (the request may have been refused)"
                    break
                folder = core.out_dir(user, "Trillion Images")
                folder.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "picture"
                path = folder / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.png"
                path.write_bytes(data)
                core.bump_usage(user["id"], "gemini")
                return {"saved": str(path), "file": path.name, "model": model, "shape": ratio}
            try:
                msg = (r.json().get("error") or {}).get("message", "")[:200]
            except Exception:
                msg = r.text[:200]
            last = {429: "your Gemini key has used up its allowance for now", 403: "Google refused the key for picture making",
                    401: "Google did not accept the key"}.get(r.status_code, f"code {r.status_code}") + (f": {msg}" if msg else "")
            if r.status_code == 400 and "imageConfig" in cfg:
                continue                       # this model does not take a shape: ask again without it
            break
        if last.startswith(("your Gemini key", "Google did not accept", "could not reach")):
            break
    return {"error": "I could not make the picture: " + last + "."}


# ---------- video ----------
def t_create_video(user, prompt, seconds=20, format="vertical", voiceover=True, _task=None, media=None):
    import video
    fmt = str(format or "vertical").lower()
    fmt = "landscape" if fmt in ("landscape", "horizontal", "wide", "youtube") else ("square" if fmt == "square" else "vertical")
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    # 'a video for TrueHome' (brand named, no address) -> add the brand's site so it is read and kept on-brand
    prompt = video.with_brand_site(prompt)
    # prefer the cinematic engine (MoneyPrinterTurbo) when it's installed and no personal media was attached;
    # fall back to the classic engine on any problem so a video always comes out while the new one is unproven.
    try:
        import mpt
        # the classic engine can place real photos pulled from a named website; MPT can't, so a URL request goes classic
        if mpt.ready() and not media and not video._URL_IN.search(prompt or ""):
            return mpt.create(user, prompt, seconds, fmt, bool(voiceover), progress=say)
    except Exception as e:
        if say:
            say(f"Cinematic engine hit a snag ({type(e).__name__}); using the classic engine")
        core.log(user["id"], f"MoneyPrinterTurbo failed, used classic engine: {type(e).__name__}: {e}"[:300], "waiting")
    return video.create(core.LLM, user, prompt, seconds, fmt, bool(voiceover), progress=say, media=media)


def t_revise_video(user, video_id, change, _task=None):
    """Remake a video the user already has, applying one plain-language change (length, format, voice,
    footage, or wording). Reuses the original request and settings, then overrides only what changed."""
    import json as _json, os, re as _re
    row = core.q("SELECT * FROM videos WHERE id=? AND user_id=?", (int(video_id), user["id"]), one=True)
    if not row:
        return {"error": f"I couldn't find video #{video_id} to change. Check the number in the Videos list."}
    try:
        d = _json.loads(row["detail"] or "{}")
    except Exception:
        d = {}
    base = (d.get("request") or row["title"] or "").strip()
    seconds = int(row["seconds"] or 20)
    fmt = d.get("format") or "vertical"
    voiceover = bool(d.get("voiceover", True))
    media = [m for m in (d.get("media") or []) if isinstance(m, str) and os.path.exists(m)] or None
    c = (change or "").strip()
    low = c.lower()
    P_LEN = r"(\d{1,2})\s*(seconds?|secs?|s)\b"
    P_FMT = r"landscape|horizontal|wide|youtube|square|vertical|portrait|reel|tiktok|story|shorts"
    P_VOICE = (r"no voice|without (a )?voice|remove (the )?voice|mute|no narration|silent|no voice-?over|"
               r"add (a )?voice|with (a )?voice|narrat|voice-?over")
    P_FOOT = (r"new footage|different (footage|clips?|videos?|photos?|pictures?|background)|re-?roll|other footage|"
              r"fresh (footage|clips?|photos?)|change the (footage|clips?|background|photos?|video)|re-?pick")
    m = _re.search(P_LEN, low)
    if m:
        seconds = max(8, min(60, int(m.group(1))))
    elif _re.search(r"\b(shorter|quicker|snappier|briefer)\b", low):
        seconds = max(8, seconds - 5)
    elif _re.search(r"\b(longer|more time)\b", low):
        seconds = min(60, seconds + 5)
    if _re.search(r"landscape|horizontal|wide|youtube", low):
        fmt = "landscape"
    elif _re.search(r"\bsquare\b", low):
        fmt = "square"
    elif _re.search(r"vertical|portrait|reel|tiktok|story|shorts", low):
        fmt = "vertical"
    if _re.search(r"no voice|without (a )?voice|remove (the )?voice|mute|no narration|silent|no voice-?over", low):
        voiceover = False
    elif _re.search(r"add (a )?voice|with (a )?voice|narrat|voice-?over", low):
        voiceover = True
    if _re.search(P_FOOT, low):
        media = None                        # re-pick footage instead of reusing the stored photos
    # does anything remain beyond the parameter words? if so it's a wording/subject change and should steer the script
    stripped = low
    for pat in (P_LEN, P_FMT, P_VOICE, P_FOOT, r"\b(shorter|quicker|snappier|briefer|longer|more time)\b"):
        stripped = _re.sub(pat, " ", stripped)
    STOP = {"make", "makeit", "it", "the", "a", "an", "to", "into", "and", "no", "of", "this", "that", "please",
            "change", "changeit", "fix", "redo", "remake", "update", "edit", "video", "clip", "version", "now",
            "over", "voice", "footage", "second", "seconds", "bit", "more", "less", "instead", "but", "for", "with",
            "its", "be", "little", "much", "same", "keep", "use"}
    words = [w for w in _re.sub(r"[^a-z0-9]+", " ", stripped).split() if len(w) >= 3 and w not in STOP]
    prompt = base
    if words:                               # a real wording/subject change -> steer the script for this new version
        prompt = f"{base}\n\nChange for this new version: {c}"
    return t_create_video(user, prompt, seconds, fmt, bool(voiceover), _task=_task, media=media)


def t_create_ai_clip(user, prompt, seconds=5, format="vertical", _task=None):
    import freevideo
    fmt = str(format or "vertical").lower()
    fmt = "landscape" if fmt in ("landscape", "horizontal", "wide", "youtube") else ("square" if fmt == "square" else "vertical")
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return freevideo.create(core.LLM, user, prompt, seconds, fmt, progress=say)


def t_create_ebook(user, prompt, chapters=5, _task=None, sources=None, images=None, pictures=False):
    import ebook
    say = (lambda text: core.set_task(_task, detail=text)) if _task else None
    return ebook.create(core.LLM, user, prompt, chapters, progress=say, sources=sources, images=images, pictures=bool(pictures))


def t_phone_read_screen(user):
    import phone
    return phone.screen(user)


def t_phone_tap(user, target="", x=None, y=None):
    import phone
    return phone.tap(user, target, x, y)


def t_phone_type(user, text):
    import phone
    return phone.type_text(user, text)


def t_phone_press(user, key):
    import phone
    return phone.press(user, key)


def t_phone_swipe(user, direction):
    import phone
    return phone.swipe(user, direction)


def t_phone_shell(user, command):
    import phone
    return phone.shell(user, command)
