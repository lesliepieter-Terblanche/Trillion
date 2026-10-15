"""REA (Reverse Engineer Anything) helpers.

REA is a connector (npm package rea-agents, github.com/morluto/rea, MIT) that looks inside apps and
websites without their source. Two rules the owner asked for live here:

1. Every REA job needs the owner's approval first — even on a phone call, where other jobs run straight
   away. The approval is enforced in tools.execute; this module only says which calls are REA.
2. Heavy native-binary jobs (a .exe, .dll, a Ghidra-level disassembly) are not run on the local brain.
   They are handed to Claude Code on the owner's Pro plan, which does the work and reports back here.

For now REA is owner-only. The connector's own access list already enforces that; the extra guard here
is a belt-and-braces check so a future change cannot quietly open it to the other logins.
"""
import os
import shlex
import subprocess
import threading
import time

import core

# npm package that backs the REA connector; used to recognise it whatever the owner renamed it to
REA_PACKAGE = "rea-agents"

# a native program: the heavy path that goes to Claude Code
NATIVE_EXT = {".exe", ".dll", ".sys", ".so", ".dylib", ".o", ".a", ".elf", ".bin", ".msi",
              ".node", ".lib", ".ko", ".com", ".scr", ".drv", ".out", ".macho", ".obj"}
NATIVE_WORDS = ("native", "ghidra", "binary", "disasm", "disassembl", "machine code", "pe header",
                "assembly", "decompile native")

CLAUDE_TIMEOUT = 1200            # a heavy reverse-engineering job may take a while


def is_rea(row):
    """True if this connector row is REA (by its command, so a rename does not fool it)."""
    if not row:
        return False
    try:
        cmd = row["command"]
    except (KeyError, IndexError, TypeError):
        cmd = ""
    return REA_PACKAGE in str(cmd or "")


def is_rea_call(user, connector_name):
    """True if a connector_call names the REA connector (used to force approval, even on a call)."""
    try:
        import extend
        row = extend.find_connector(user["id"], connector_name)
        return is_rea(row)
    except Exception:
        return False


def _arg_strings(arguments):
    out = []
    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(arguments)
    return out


def is_native_job(tool, arguments):
    """A heavy native-binary job: a native program by its file name, or a tool that means machine code."""
    name = str(tool or "").lower()
    if any(w in name for w in NATIVE_WORDS):
        return True
    for s in _arg_strings(arguments):
        low = s.lower().strip().strip('"').strip("'")
        head = low.split("?", 1)[0].split("#", 1)[0]
        if any(head.endswith(e) for e in NATIVE_EXT):
            return True
    return False


def _claude_cmd(user):
    s = (core.settings(user).get("rea") or {})
    return str(s.get("claude_cmd") or os.environ.get("TRILLION_CLAUDE_CMD") or "claude -p").strip()


def _target_dir(user, arguments):
    """Run Claude Code in the folder of the binary, when the argument is a file the owner may use."""
    try:
        import tools
        for s in _arg_strings(arguments):
            low = s.lower().split("?", 1)[0]
            if any(low.endswith(e) for e in NATIVE_EXT):
                try:
                    p = tools.resolve(user, s)
                except Exception:
                    continue
                if p.exists():
                    return str(p.parent)
    except Exception:
        pass
    return str(core.DATA)


def _prompt(tool, arguments):
    import json
    return ("You are Claude Code, acting as a reverse-engineering assistant on the owner's own machine. "
            "This job was approved by the machine's owner through Trillion. "
            f"Carry out this REA (reverse-engineer-anything) request on a native binary.\n\n"
            f"REA tool asked for: {tool}\n"
            f"Arguments: {json.dumps(arguments, ensure_ascii=False)[:1500]}\n\n"
            "Work only inside the current folder. Explain how the feature or binary works and show the evidence "
            "(strings, imports, disassembly, decompiled snippets) you based it on. "
            "Finish with a short plain-language summary the owner can read on a phone.")


def _run_claude(user, tool, arguments):
    cmd = _claude_cmd(user)
    prompt = _prompt(tool, arguments)
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)        # use the owner's Pro login, not pay-as-you-go API billing
    env["ANONYMIZED_TELEMETRY"] = "false"
    argv = shlex.split(cmd, posix=(os.name != "nt")) + [prompt]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=CLAUDE_TIMEOUT, cwd=_target_dir(user, arguments), env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        return {"ok": False, "text": "Claude Code is not installed on this PC, or the command is wrong. "
                "Install Claude Code and sign in to your Pro plan, then set the command on the Connectors page "
                f"(now '{cmd}')."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": f"Claude Code did not finish within {CLAUDE_TIMEOUT // 60} minutes."}
    except Exception as e:
        return {"ok": False, "text": f"Could not run Claude Code: {type(e).__name__}: {e}"}
    out = (r.stdout or "").strip()
    if not out and r.returncode != 0:
        tail = (r.stderr or "").strip()[-400:]
        return {"ok": False, "text": f"Claude Code reported an error. {tail}".strip()}
    return {"ok": True, "text": out or "Claude Code finished but said nothing."}


def handoff(user, row, tool, arguments):
    """Hand a heavy native-binary job to Claude Code on the owner's Pro plan, in the background, and report back.
    Returns at once; the finished report arrives in the chat and on the phone."""
    if not user["is_admin"]:
        return {"error": "REA is owner-only for now."}
    uid = user["id"]

    def work():
        core.log(uid, f"Handed to Claude Code (Pro plan): REA {tool}", "info")
        res = _run_claude(user, tool, arguments)
        report = res["text"][:6000]
        head = "Claude Code finished the reverse-engineering job" if res["ok"] else "Claude Code could not finish the job"
        core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
               (uid, "assistant", f"{head} ({tool}):\n\n{report}", time.time()))
        core.log(uid, f"{head}: {tool}", "done" if res["ok"] else "waiting")
        try:
            import watch
            watch.alert(user, "job", "Trillion: REA job done" if res["ok"] else "Trillion: REA job failed",
                        report[:300])
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()
    return {"result": "This is a native-binary job, so I handed it to Claude Code on your Pro plan. "
            "It will do the work and report back here and on your phone when it is done.",
            "via": "Claude Code (Pro plan)"}
