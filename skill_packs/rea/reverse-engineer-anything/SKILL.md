---
name: Reverse engineer anything
description: How to look inside an app, website or program you have no source code for, using the REA connector — and the owner's rules for it (owner only, runs without approval, phone apps and native binaries go to Claude Code).
---

# Reverse engineer anything

Use this when the owner asks you to work out how some software does something — "how does this app check the licence key", "what does this website call when I click pay", "what is this .exe actually doing" — and there is no source code to read. The work is done through the **Reverse engineering (REA)** connector.

## The owner's rules (do not skip these)

- **REA is owner-only.** Only the owner login may use it. If anyone else asks, say it is owner-only for now.
- **REA runs without asking for approval.** Do not ask "ready to proceed?" and wait — just call the tool and let it run.
- **To reverse-engineer an app on the phone, call `rea_phone_app` with the app's name.** It pulls the app's APK off the phone and hands it to Claude Code. Do NOT take screenshots or try to drive the live app for this.
- **Heavy native-binary jobs go to Claude Code.** A job on a native program (a `.exe`, `.dll`, `.so`, an APK, a Ghidra-level disassembly) is handed to Claude Code on the owner's Pro plan automatically. You will get back a short "handed off" note; the real report arrives later in the chat and on the phone. Do not try to disassemble native binaries on the local brain.

## How to do a job

1. **Find the connector's tools.** Call `connector_tools` with connector "Reverse engineering (REA)" (a few words in `find`, like "javascript" or "network" or "dotnet", narrows a long list).
2. **Pick the right tool for the target:**
   - JavaScript / Electron app, or a bundled web app → the JavaScript/Electron tools.
   - .NET program (`.dll`/`.exe` that is managed .NET) → the .NET tools.
   - A live website or its network calls → the web tools.
   - A native program (unmanaged `.exe`/`.dll`/`.so`, needs Ghidra) → just ask REA for it; the native path is handed to Claude Code for you.
3. **Call the tool** with `connector_call` (or `rea_phone_app` for a phone app). It runs straight away, or is handed to Claude Code for native work.
4. **Explain the finding in plain language, with the evidence.** Say how the feature works and point to what shows it — the function, the strings, the network call, the decompiled snippet. Never guess past what the evidence shows.

## Staying honest and safe

- Report only what the tools actually found. If the evidence is thin, say so.
- This is for understanding software (interoperability, security review, debugging the owner's own tools), not for stripping protection or copying someone else's product. If a request is about defeating a licence or piracy, say that is not something to help with.
