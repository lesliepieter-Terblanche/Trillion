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
import re
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


def _dispatch_claude(user, prompt, cwd):
    """Run Claude Code (Pro plan) once with this prompt in this folder. Returns {ok, text}."""
    cmd = _claude_cmd(user)
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)        # use the owner's Pro login, not pay-as-you-go API billing
    env["ANONYMIZED_TELEMETRY"] = "false"
    argv = shlex.split(cmd, posix=(os.name != "nt")) + [prompt]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=CLAUDE_TIMEOUT, cwd=cwd, env=env,
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
        return {"ok": False, "text": f"Claude Code reported an error. {(r.stderr or '').strip()[-400:]}".strip()}
    return {"ok": True, "text": out or "Claude Code finished but said nothing."}


def _report_back(user, label, res):
    import core
    report = res["text"][:6000]
    head = f"Claude Code finished the reverse-engineering job ({label})" if res["ok"] else \
           f"Claude Code could not finish the job ({label})"
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
           (user["id"], "assistant", f"{head}:\n\n{report}", time.time()))
    core.log(user["id"], head, "done" if res["ok"] else "waiting")
    try:
        import watch
        watch.alert(user, "job", "Trillion: REA job done" if res["ok"] else "Trillion: REA job failed", report[:300])
    except Exception:
        pass


def phone_app(user, app):
    """Reverse-engineer a phone app on the owner's Pro plan via Claude Code. Resolves the app quickly (so a bad name
    fails at once), then pulls the APK and runs Claude Code in the background. Returns at once; the report arrives in
    the chat and on the phone."""
    if not user["is_admin"]:
        return {"error": "REA is owner-only for now."}
    import core
    import phone
    try:
        pkg = phone.resolve_app(user, app)                 # quick: a wrong name fails straight away
    except Exception as e:
        return {"error": str(e) if type(e).__name__ == "PhoneError" else f"{type(e).__name__}: {e}"}
    uid = user["id"]
    dest = os.path.join(str(core.DATA), "rea_apks", re.sub(r"[^A-Za-z0-9._-]+", "_", pkg)[:60] or "app")

    def work():
        try:
            pull = phone.pull_apk(user, pkg, dest)         # the slow part (adb pull): off the request thread
        except Exception as e:
            msg = str(e) if type(e).__name__ == "PhoneError" else f"{type(e).__name__}: {e}"
            core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                   (uid, "assistant", f"I could not get {pkg} off your phone: {msg}", time.time()))
            core.log(uid, f"REA on {pkg}: could not pull the APK", "waiting")
            return
        core.log(uid, f"Handed to Claude Code (Pro plan): REA on {pkg}", "info")
        names = ", ".join(os.path.basename(p) for p in pull["all"])
        prompt = ("You are Claude Code, reverse-engineering an Android app on the owner's own machine. The owner approved "
                  "this through Trillion. The app's APK file(s) are in the current folder: " + names + f" (package {pkg}). "
                  "Decompile and inspect it with whatever is available (jadx or apktool if installed; otherwise unzip the APK and read "
                  "AndroidManifest.xml, the DEX strings, and resources). Explain how the app or the feature in question works and show the "
                  "evidence you based it on. Finish with a short plain-language summary the owner can read on a phone.")
        _report_back(user, pkg, _dispatch_claude(user, prompt, os.path.dirname(pull["apk"])))

    threading.Thread(target=work, daemon=True).start()
    return {"result": f"Pulling {pkg} off your phone and handing it to Claude Code on your Pro plan. "
            "It will do the work and report back here and on your phone when it is done.", "via": "Claude Code (Pro plan)"}


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


# ---------- what servers a phone app talks to (live traffic) ----------
TRAFFIC_PORT = int(os.environ.get("TRILLION_MITM_PORT", 8081))
TRAFFIC_MAX = 300


def _mitm_cmd():
    import shutil
    return os.environ.get("TRILLION_MITMDUMP") or shutil.which("mitmdump")


def _summarize_flows(path):
    """Read a mitmproxy capture file and return (hosts {host: count}, rows [{host,method,path,status,tls}])."""
    from mitmproxy import io as mio
    hosts, rows = {}, []
    with open(path, "rb") as f:
        for fl in mio.FlowReader(f).stream():
            req = getattr(fl, "request", None)
            if req is None:
                continue
            host = req.pretty_host
            hosts[host] = hosts.get(host, 0) + 1
            resp = getattr(fl, "response", None)
            rows.append({"host": host, "method": req.method, "path": (req.path or "")[:120],
                         "status": getattr(resp, "status_code", None), "tls": req.scheme == "https"})
    return hosts, rows


def phone_traffic(user, app, seconds, pc_ip):
    """Capture what servers a phone app talks to: route the phone through mitmproxy on the PC for `seconds`,
    then summarise the hosts and requests. Returns at once; the report arrives in the chat and on the phone."""
    if not user["is_admin"]:
        return {"error": "REA is owner-only for now."}
    import core
    import phone
    mitm = _mitm_cmd()
    if not mitm:
        return {"error": "mitmproxy is not installed on this PC. Install it (pip install mitmproxy) to capture app "
                "traffic, then try again."}
    if not pc_ip:
        return {"error": "I do not have this PC's Tailscale address, so the phone cannot reach the capture. "
                "Check Tailscale is on, then try again."}
    try:
        pkg = phone.resolve_app(user, app)
    except Exception as e:
        return {"error": str(e) if type(e).__name__ == "PhoneError" else f"{type(e).__name__}: {e}"}
    seconds = max(15, min(int(seconds or 60), TRAFFIC_MAX))
    folder = os.path.join(str(core.DATA), "rea_traffic")
    os.makedirs(folder, exist_ok=True)
    flow = os.path.join(folder, re.sub(r"[^A-Za-z0-9._-]+", "_", pkg) + ".flows")
    uid = user["id"]

    def work():
        import subprocess
        core.log(uid, f"Capturing {pkg} traffic for {seconds}s", "info")
        env = dict(os.environ)
        env["ANONYMIZED_TELEMETRY"] = "false"
        try:
            proc = subprocess.Popen([mitm, "-q", "-p", str(TRAFFIC_PORT), "-w", flow],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            _traffic_report(user, pkg, None, None, f"Could not start mitmproxy: {type(e).__name__}: {e}")
            return
        proxied = False
        try:
            time.sleep(2)                                   # let the proxy bind
            phone.set_http_proxy(user, pc_ip, TRAFFIC_PORT)
            proxied = True
            core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
                   (uid, "assistant", f"Capturing now - open and use {app} on your phone for about {seconds} seconds.", time.time()))
            time.sleep(seconds)
        except Exception as e:
            core.log(uid, f"Traffic capture problem: {type(e).__name__}: {e}", "info")
        finally:
            if proxied:
                try:
                    phone.clear_http_proxy(user)
                except Exception:
                    pass
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            hosts, rows = _summarize_flows(flow)
        except FileNotFoundError:
            _traffic_report(user, pkg, None, None, "Nothing was captured. Did the app send any traffic during the window?")
            return
        except Exception as e:
            _traffic_report(user, pkg, None, None, f"Could not read the capture: {type(e).__name__}: {e}")
            return
        _traffic_report(user, pkg, hosts, rows, None)

    threading.Thread(target=work, daemon=True).start()
    return {"result": f"Set your phone to route through this PC and started capturing. Open and use {app} now; "
            f"I will report the servers it talks to in about {seconds} seconds.", "via": "mitmproxy"}


def _traffic_report(user, pkg, hosts, rows, error):
    import core
    if error:
        text = f"Traffic capture for {pkg} did not work: {error}"
    elif not rows:
        text = (f"I routed {pkg} through the PC but saw no requests. The app may use something other than HTTP(S), "
                "or it sent nothing while capturing. Try again and use the app more during the window.")
    else:
        top = sorted(hosts.items(), key=lambda x: -x[1])[:15]
        lines = [f"{pkg} talked to {len(hosts)} server{'s' if len(hosts) != 1 else ''} in {len(rows)} request(s). Top servers:"]
        lines += [f"- {h} ({c})" for h, c in top]
        sample = [r for r in rows if r["status"]][:8] or rows[:8]
        if sample:
            lines.append("Some requests:")
            lines += [f"- {r['method']} {r['host']}{r['path']}" + (f" -> {r['status']}" if r['status'] else "") for r in sample]
        enc = sum(1 for r in rows if r["tls"])
        if enc:
            lines.append(f"{enc} request(s) were HTTPS; to read their contents (not just the server) install mitmproxy's "
                         "certificate on the phone and make sure the app does not pin its certificate.")
        text = "\n".join(lines)[:6000]
    core.x("INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)", (user["id"], "assistant", text, time.time()))
    core.log(user["id"], f"Traffic capture for {pkg}: {'failed' if error else 'done'}", "waiting" if error else "done")
    try:
        import watch
        watch.alert(user, "job", "Trillion: traffic capture done" if not error else "Trillion: traffic capture failed", text[:300])
    except Exception:
        pass
