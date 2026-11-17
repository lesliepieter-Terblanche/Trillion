"""Local image understanding - lets Trillion actually SEE (webcam, screenshots, pictures).

Sends an image to a local vision model through Ollama, so "what do you see" works on the webcam
with no tokens and no cloud. Picks whatever vision model is installed (moondream is the small default
pulled by Setup; qwen2.5vl / llava / llama3.2-vision are used if present and preferred for quality).
"""
import base64
import httpx
import core

# Vision models ranked by quality, best first. We use the best one that is actually installed.
VISION_RANK = ["qwen2.5vl", "llama3.2-vision", "llava", "minicpm-v", "moondream", "bakllava"]


def _installed_vision(pref=""):
    names = [m["name"] for m in core.models().get("list", [])]
    if pref:
        for n in names:                                   # honour a chosen model (exact or same family)
            if n == pref or n.split(":")[0] == pref.split(":")[0]:
                return n
    for fam in VISION_RANK:
        hit = next((n for n in names if n.split(":")[0] == fam or n.split(":")[0] == fam.replace("-", "")), None)
        if hit:
            return hit
    return None


def available(pref=""):
    return bool(_installed_vision(pref))


def describe(image, prompt="Describe what you see in this image in one or two short, natural sentences.",
             model="", timeout=120):
    """image: a file path or raw JPEG/PNG bytes. Returns the model's description, or raises RuntimeError."""
    name = _installed_vision(model)
    if not name:
        raise RuntimeError("No vision model is installed. Run Setup, or 'ollama pull moondream', so I can see images.")
    if isinstance(image, (bytes, bytearray)):
        data = bytes(image)
    else:
        with open(image, "rb") as f:
            data = f.read()
    b64 = base64.b64encode(data).decode()
    r = httpx.post(core.OLLAMA + "/api/generate",
                   json={"model": name, "prompt": prompt, "images": [b64], "stream": False},
                   timeout=timeout)
    r.raise_for_status()
    out = (r.json().get("response") or "").strip()
    if not out:
        raise RuntimeError("The vision model returned nothing.")
    return out
