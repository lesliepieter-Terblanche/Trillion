"""Self-heal: Trillion fixes her own common breakages, then reports what she did.

Owner only. Only SAFE, reversible repairs run on their own — start Docker if it's installed but asleep,
restart a business app that has fallen over, and reconnect a dropped connector. Anything heavier or
destructive (re-downloading a 14 GB model, touching your files, signing in to Tailscale) is never done
automatically: it's reported for you to approve. Everything attempted is logged.
"""
import time

import core


def _log(uid, msg):
    try:
        core.log(uid, msg, "done")
    except Exception:
        pass


def _heal_docker():
    """Start Docker Desktop if it's installed but not running."""
    try:
        import apps
        st = apps.docker_state()
        if not st.get("installed"):
            return None
        if st.get("running"):
            return None
        apps.start_docker_desktop()
        return {"item": "Docker", "action": "started Docker Desktop (it was asleep)"}
    except Exception as e:
        return {"item": "Docker", "action": f"could not start Docker ({type(e).__name__})", "failed": True}


def _heal_apps():
    """Restart any business app that should be up but isn't."""
    done = []
    try:
        import apps
        st = apps.docker_state()
        if not (st.get("installed") and st.get("running")):
            return done
        running = apps.running_projects()                      # {key: status text}
        rows = core.q("SELECT key, status FROM apps WHERE status IN ('installed','running','stopped')")
        for r in rows:
            key = r["key"]
            up = "running" in (running.get(key, "") or "")
            if r["status"] in ("installed", "running") and not up and key in apps.APPS:
                try:
                    code, out = apps.compose(key, "up", "-d", timeout=600)
                    if code == 0:
                        done.append({"item": apps.APPS[key]["name"], "action": "restarted (it had stopped)"})
                    else:
                        done.append({"item": apps.APPS[key]["name"], "action": "restart failed", "failed": True})
                except Exception as e:
                    done.append({"item": apps.APPS[key]["name"], "action": f"restart error ({type(e).__name__})", "failed": True})
    except Exception:
        pass
    return done


def _heal_connectors(user):
    """Re-establish any enabled connector whose MCP process has died."""
    done = []
    try:
        import extend
        for r in extend.enabled_connectors(user["id"]):
            row = core.q("SELECT * FROM connectors WHERE id=?", (r["id"],), one=True)
            if not row:
                continue
            try:
                live = extend.LIVE.get(row["id"])
                alive = bool(live and live.alive())
                if not alive:
                    extend.client(row)                         # (re)spawns the MCP, stopping any stale one
                    done.append({"item": row["name"], "action": "reconnected"})
            except Exception as e:
                done.append({"item": row["name"], "action": f"could not reconnect ({type(e).__name__})", "failed": True})
    except Exception:
        pass
    return done


def _needs_you(user):
    """Things that are broken but only the owner can fix — reported, never auto-fixed."""
    out = []
    try:
        if not core.models().get("ok"):
            out.append("The local brain (Ollama) isn't answering — start Ollama, then restart Trillion.")
    except Exception:
        pass
    try:
        v = core.vitals().get("disk")
        if v and (v["total"] - v["used"]) < 5:
            out.append(f"Low disk: under 5 GB free — free up space on the Files page.")
    except Exception:
        pass
    return out


def heal(user, progress=None):
    """Run the safe repairs and report. Owner only."""
    if not user.get("is_admin"):
        return {"error": "Self-heal is for the owner login only."}
    say = progress or (lambda t: None)
    fixed, failed = [], []

    say("Checking Docker")
    d = _heal_docker()
    if d:
        (failed if d.get("failed") else fixed).append(d)
        if not d.get("failed"):
            time.sleep(6)                                      # give the engine a moment before touching apps

    say("Checking the business apps")
    for a in _heal_apps():
        (failed if a.get("failed") else fixed).append(a)

    say("Checking the connectors")
    for c in _heal_connectors(user):
        (failed if c.get("failed") else fixed).append(c)

    need = _needs_you(user)
    for f in fixed:
        _log(user["id"], f"Self-heal: {f['item']} — {f['action']}")
    return {"ok": True, "fixed": [f"{x['item']}: {x['action']}" for x in fixed],
            "failed": [f"{x['item']}: {x['action']}" for x in failed],
            "needs_you": need, "when": time.time(),
            "summary": _summary(fixed, failed, need)}


def _summary(fixed, failed, need):
    if not fixed and not failed and not need:
        return "Everything was already healthy — nothing to fix."
    parts = []
    if fixed:
        parts.append(f"fixed {len(fixed)} thing(s): " + "; ".join(f"{x['item']} ({x['action']})" for x in fixed))
    if failed:
        parts.append(f"couldn't fix {len(failed)}: " + "; ".join(x["item"] for x in failed))
    if need:
        parts.append(f"{len(need)} need(s) you: " + " ".join(need))
    return "Self-heal — " + ". ".join(parts) + "."
