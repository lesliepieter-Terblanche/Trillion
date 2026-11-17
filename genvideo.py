"""One door for making an AI video clip, whichever engine the user chose.

video_engine setting:
  - "local"   : FreeVideo on this PC's own graphics card (free, token-free).
  - "cloud"   : a paid cloud model via Replicate (top quality).
  - "handoff" : don't generate here - hand the user a ready prompt to run in the Claude app, then they
                drop the clip back in. clip() returns {"handoff": True, "prompt": ...} so callers can tell them.
"""
import core


def engine(user):
    e = str(core.settings(user).get("video_engine") or "local").lower()
    return e if e in ("local", "cloud", "handoff") else "local"


def clip(user, prompt, seconds=5, fmt="vertical", progress=None):
    """Make one clip with the chosen engine. Returns freevideo/cloudvideo's {saved,...} or {error}/{handoff}."""
    e = engine(user)
    if e == "cloud":
        import cloudvideo
        return cloudvideo.create(core.LLM, user, prompt, seconds=seconds, fmt=fmt, progress=progress)
    if e == "handoff":
        return {"handoff": True, "prompt": str(prompt or "").strip(),
                "note": "Video engine is set to Claude hand-off. Generate this shot in the Claude app, then add the "
                        "clip back to Trillion. Switch the engine to Local or Cloud in Settings to generate automatically."}
    import freevideo
    return freevideo.create(core.LLM, user, prompt, seconds=seconds, fmt=fmt, progress=progress)


def ready(user):
    """Is the chosen engine usable right now? Returns (ok, message)."""
    e = engine(user)
    if e == "cloud":
        import cloudvideo
        return (cloudvideo.ready(user), "" if cloudvideo.ready(user)
                else "Add your Replicate API key in Settings, or switch the video engine to Local.")
    if e == "handoff":
        return (True, "")
    import freevideo
    st = freevideo.status(user)
    return (bool(st.get("ready")), "" if st.get("ready") else st.get("note") or freevideo.NOT_RUNNING)
