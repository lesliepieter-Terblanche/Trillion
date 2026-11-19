"""Cartoon stickman videos - the 'big round head over a real-looking world' style.

Characters are DRAWN BY CODE (round white head, dot eyes, hats, outfits, stick arms and legs), so the same character
looks exactly the same in every shot - no AI drift. Backgrounds are detailed, realistic, EMPTY sets made by the free
image service (one picture per location, reused, so places stay consistent too). Every shot gets a camera move, the
characters talk, point, walk and react, the narration is read by Kokoro, and the captions pop one word at a time.

Each shot is rendered to its own file and kept, so a long video resumes from the last finished shot, never from scratch.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageOps

import core
import video

FPS = 30
JOBS = core.DATA / "toon"

HATS = ("top", "beanie", "ranger", "bowler", "cap", "cowboy", "hardhat", "none")
OUTFITS = ("shirt", "suit", "vest", "jacket", "dress", "hoodie")
POSES = ("stand", "talk", "point", "wave", "hold", "shrug", "celebrate", "walk", "run", "think", "sit", "facepalm")
FACES = ("normal", "happy", "laugh", "surprised", "angry", "sad", "smug", "worried", "sleepy")
SIZES = ("wide", "medium", "close")
PROPS = ("none", "box", "moneybag", "money", "phone", "key", "paper", "gold", "pizza", "coffee", "sign", "pickaxe", "pan")
CAMS = ("in", "out", "left", "right")

INK = (22, 20, 24)
SKIN = (250, 248, 242)
BG_STYLE = (", highly detailed realistic 3D rendered environment, Pixar-like set design, cinematic warm lighting, "
            "rich textures, depth, completely EMPTY scene with NO people, NO characters, NO animals, NO text, NO logos")


def _rgb(c, default=(60, 120, 170)):
    try:
        c = str(c).strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return default


def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


# ---------------------------------------------------------------- the character, drawn by code

def _limb(d, a, b, w):
    d.line([a, b], fill=INK, width=max(2, int(w)))
    r = w / 2
    for p in (a, b):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=INK)


def _arm(d, shoulder, ang1, ang2, L, w, side):
    """ang from straight down, positive = away from the body (side -1 left, +1 right)."""
    a1 = math.radians(ang1)
    elbow = (shoulder[0] + side * math.sin(a1) * L, shoulder[1] + math.cos(a1) * L)
    a2 = math.radians(ang1 + ang2)
    hand = (elbow[0] + side * math.sin(a2) * L * 0.95, elbow[1] + math.cos(a2) * L * 0.95)
    _limb(d, shoulder, elbow, w)
    _limb(d, elbow, hand, w)
    r = w * 1.15
    d.ellipse([hand[0] - r, hand[1] - r, hand[0] + r, hand[1] + r], fill=SKIN, outline=INK, width=max(2, int(w * 0.45)))
    return hand


def _pose_angles(pose, ph):
    """(left arm (shoulder, elbow), right arm, leg swing in degrees, body bob) for a pose at phase ph (0..1 loop)."""
    s = math.sin(ph * 2 * math.pi)
    if pose == "talk":
        return (12, 8), (48 + 14 * s, -55 - 12 * s), 0, 0.02 * s
    if pose == "point":
        return (10, 6), (92, -4), 0, 0
    if pose == "wave":
        return (10, 6), (135, 20 + 25 * s), 0, 0
    if pose == "hold":
        return (40, -95), (40, -95), 0, 0.01 * s
    if pose == "shrug":
        return (55, -115), (55, -115), 0, 0.03 * abs(s)
    if pose == "celebrate":
        return (138 + 8 * s, 20), (138 - 8 * s, 20), 0, 0.05 * abs(s)
    if pose == "walk":
        return (14 + 22 * s, -12), (14 - 22 * s, -12), 26 * s, 0.03 * abs(s)
    if pose == "run":
        return (35 + 45 * s, -70), (35 - 45 * s, -70), 45 * s, 0.06 * abs(s)
    if pose == "think":
        return (25, -60), (30, -150), 0, 0
    if pose == "facepalm":
        return (12, 8), (35, -160), 0, 0
    if pose == "sit":
        return (25, -40), (25, -40), 0, 0
    return (10, 6), (10, 6), 0, 0.01 * s                     # stand


def _hat(d, kind, cx, top, R, col, w):
    o = max(2, int(w))
    if kind == "top":
        d.rounded_rectangle([cx - R * 0.95, top - R * 1.25, cx + R * 0.95, top + R * 0.1], R * 0.08, fill=col, outline=INK, width=o)
        d.rectangle([cx - R * 0.95, top - R * 0.15, cx + R * 0.95, top + R * 0.05], fill=_shade(col, 0.55) if sum(col) > 300 else (150, 30, 40))
        d.rounded_rectangle([cx - R * 1.45, top - R * 0.02, cx + R * 1.45, top + R * 0.22], R * 0.1, fill=col, outline=INK, width=o)
    elif kind == "beanie":
        d.chord([cx - R * 1.02, top - R * 0.85, cx + R * 1.02, top + R * 1.15], 180, 360, fill=col, outline=INK, width=o)
        d.rounded_rectangle([cx - R * 1.06, top + R * 0.05, cx + R * 1.06, top + R * 0.42], R * 0.12, fill=_shade(col, 0.85), outline=INK, width=o)
        for i in range(-4, 5):
            x = cx + i * R * 0.22
            d.line([(x, top + R * 0.1), (x, top + R * 0.37)], fill=_shade(col, 0.6), width=max(1, o // 2))
    elif kind in ("ranger", "cowboy"):
        d.chord([cx - R * 0.8, top - R * 0.95, cx + R * 0.8, top + R * 0.7], 180, 360, fill=col, outline=INK, width=o)
        d.ellipse([cx - R * 1.6, top - R * 0.12, cx + R * 1.6, top + R * 0.35], fill=_shade(col, 0.9), outline=INK, width=o)
        d.rectangle([cx - R * 0.8, top - R * 0.2, cx + R * 0.8, top + R * 0.02], fill=_shade(col, 0.55))
        if kind == "ranger":
            d.ellipse([cx - R * 0.17, top - R * 0.5, cx + R * 0.17, top - R * 0.16], fill=(232, 190, 70), outline=INK, width=max(1, o // 2))
    elif kind == "bowler":
        d.chord([cx - R * 0.85, top - R * 0.95, cx + R * 0.85, top + R * 0.75], 180, 360, fill=col, outline=INK, width=o)
        d.rounded_rectangle([cx - R * 1.25, top - R * 0.04, cx + R * 1.25, top + R * 0.18], R * 0.09, fill=col, outline=INK, width=o)
    elif kind == "cap":
        d.chord([cx - R * 0.98, top - R * 0.8, cx + R * 0.98, top + R * 1.0], 180, 360, fill=col, outline=INK, width=o)
        d.rounded_rectangle([cx + R * 0.2, top + R * 0.02, cx + R * 1.55, top + R * 0.22], R * 0.1, fill=_shade(col, 0.8), outline=INK, width=o)
    elif kind == "hardhat":
        d.chord([cx - R * 1.0, top - R * 0.9, cx + R * 1.0, top + R * 0.9], 180, 360, fill=col, outline=INK, width=o)
        d.rounded_rectangle([cx - R * 1.3, top - R * 0.02, cx + R * 1.3, top + R * 0.16], R * 0.08, fill=col, outline=INK, width=o)


def _face(d, cx, cy, R, face, mouth_open, ch, w, blink=False):
    o = max(2, int(w))
    ex = R * 0.34
    ey = cy - R * 0.05
    er = R * 0.11
    # brows
    if face in ("angry",):
        d.line([(cx - ex - er * 1.8, ey - R * 0.32), (cx - ex + er * 1.6, ey - R * 0.18)], fill=INK, width=o)
        d.line([(cx + ex + er * 1.8, ey - R * 0.32), (cx + ex - er * 1.6, ey - R * 0.18)], fill=INK, width=o)
    elif face in ("sad", "worried"):
        d.line([(cx - ex - er * 1.8, ey - R * 0.2), (cx - ex + er * 1.6, ey - R * 0.33)], fill=INK, width=o)
        d.line([(cx + ex + er * 1.8, ey - R * 0.2), (cx + ex - er * 1.6, ey - R * 0.33)], fill=INK, width=o)
    elif face in ("surprised",):
        for sx in (-1, 1):
            d.arc([cx + sx * ex - er * 2, ey - R * 0.5, cx + sx * ex + er * 2, ey - R * 0.22], 200, 340, fill=INK, width=o)
    elif face == "smug":
        d.line([(cx - ex - er * 1.6, ey - R * 0.26), (cx - ex + er * 1.6, ey - R * 0.26)], fill=INK, width=o)
        d.line([(cx + ex - er * 1.6, ey - R * 0.33), (cx + ex + er * 1.8, ey - R * 0.22)], fill=INK, width=o)
    # eyes
    for sx in (-1, 1):
        x = cx + sx * ex
        if blink or face == "sleepy":
            d.line([(x - er * 1.2, ey), (x + er * 1.2, ey)], fill=INK, width=o)
        elif face in ("happy", "laugh"):
            d.arc([x - er * 1.5, ey - er * 1.2, x + er * 1.5, ey + er * 1.6], 200, 340, fill=INK, width=o + 1)
        elif face == "surprised":
            d.ellipse([x - er * 1.9, ey - er * 2.1, x + er * 1.9, ey + er * 1.7], fill=(255, 255, 255), outline=INK, width=o)
            d.ellipse([x - er * 0.7, ey - er * 0.6, x + er * 0.7, ey + er * 0.8], fill=INK)
        elif face == "smug":
            d.chord([x - er * 1.3, ey - er * 1.1, x + er * 1.3, ey + er * 1.2], 0, 180, fill=INK)
        else:
            d.ellipse([x - er, ey - er * 1.25, x + er, ey + er * 1.25], fill=INK)
    # glasses
    if ch.get("glasses"):
        for sx in (-1, 1):
            x = cx + sx * ex
            d.ellipse([x - er * 2.4, ey - er * 2.3, x + er * 2.4, ey + er * 2.3], outline=INK, width=o)
        d.line([(cx - ex + er * 2.4, ey), (cx + ex - er * 2.4, ey)], fill=INK, width=o)
    # mouth
    my = cy + R * 0.42
    mw = R * 0.34
    if mouth_open or face in ("laugh",):
        h = R * (0.3 if face in ("laugh", "surprised") else 0.2)
        d.chord([cx - mw, my - h * 0.6, cx + mw, my + h * 1.4], 0, 180, fill=(70, 20, 25), outline=INK, width=o)
        d.chord([cx - mw * 0.5, my + h * 0.35, cx + mw * 0.5, my + h * 1.3], 180, 360, fill=(215, 80, 90))
    elif face in ("happy", "smug"):
        d.arc([cx - mw, my - R * 0.25, cx + mw, my + R * 0.15], 15, 165, fill=INK, width=o + 1)
    elif face in ("sad", "worried"):
        d.arc([cx - mw * 0.8, my - R * 0.02, cx + mw * 0.8, my + R * 0.3], 200, 340, fill=INK, width=o + 1)
    elif face == "surprised":
        d.ellipse([cx - R * 0.11, my - R * 0.05, cx + R * 0.11, my + R * 0.2], fill=(70, 20, 25), outline=INK, width=o)
    elif face == "angry":
        d.line([(cx - mw * 0.8, my + R * 0.08), (cx + mw * 0.8, my + R * 0.02)], fill=INK, width=o + 1)
    else:
        d.line([(cx - mw * 0.6, my + R * 0.04), (cx + mw * 0.6, my + R * 0.04)], fill=INK, width=o + 1)
    # moustache
    if ch.get("mustache"):
        mx, mzy = cx, cy + R * 0.28
        for sx in (-1, 1):
            d.chord([mx - R * 0.42 if sx < 0 else mx - R * 0.02, mzy - R * 0.12, mx + R * 0.02 if sx < 0 else mx + R * 0.42,
                     mzy + R * 0.16], 180 if sx < 0 else 180, 360, fill=INK)
            d.ellipse([mx + sx * R * 0.42 - R * 0.07, mzy - R * 0.1, mx + sx * R * 0.42 + R * 0.07, mzy + R * 0.04], fill=INK)


def _torso(d, ch, cx, top, bot, R, w):
    o = max(2, int(w))
    col = _rgb(ch.get("color"))
    kind = ch.get("outfit", "shirt")
    tw = R * (0.78 if kind != "dress" else 0.7)
    bw = R * (0.72 if kind != "dress" else 1.05)
    poly = [(cx - tw, top), (cx + tw, top), (cx + bw, bot), (cx - bw, bot)]
    d.polygon(poly, fill=col, outline=INK)
    d.line(poly + [poly[0]], fill=INK, width=o, joint="curve")
    if kind == "suit":
        d.polygon([(cx - tw * 0.45, top), (cx + tw * 0.45, top), (cx, top + (bot - top) * 0.45)], fill=(250, 250, 250), outline=INK)
        for i in range(3):
            y = top + (bot - top) * (0.55 + i * 0.15)
            d.ellipse([cx - R * 0.06, y - R * 0.06, cx + R * 0.06, y + R * 0.06], fill=INK)
        d.ellipse([cx - tw * 0.75, top + R * 0.25, cx - tw * 0.45, top + R * 0.55], fill=(200, 30, 40))     # buttonhole rose
    elif kind == "vest":
        d.polygon([(cx - tw * 0.4, top), (cx + tw * 0.4, top), (cx + R * 0.1, bot), (cx - R * 0.1, bot)], fill=(240, 236, 226))
        for i in range(3):
            y = top + (bot - top) * (0.35 + i * 0.2)
            d.ellipse([cx - R * 0.05, y - R * 0.05, cx + R * 0.05, y + R * 0.05], fill=INK)
    elif kind == "jacket":
        d.line([(cx, top + R * 0.1), (cx, bot)], fill=_shade(col, 0.6), width=o)
        d.line([(cx - tw * 0.5, top), (cx, top + R * 0.45), (cx + tw * 0.5, top)], fill=INK, width=o)
    elif kind == "hoodie":
        d.chord([cx - tw * 0.8, top - R * 0.25, cx + tw * 0.8, top + R * 0.35], 0, 180, fill=_shade(col, 0.8), outline=INK, width=o)
        d.rounded_rectangle([cx - R * 0.4, top + (bot - top) * 0.55, cx + R * 0.4, top + (bot - top) * 0.82], R * 0.08,
                            outline=_shade(col, 0.6), width=o)
    else:
        d.polygon([(cx - R * 0.22, top), (cx + R * 0.22, top), (cx, top + R * 0.3)], fill=_shade(col, 0.8), outline=INK)


def _prop(d, kind, hx, hy, R, w):
    o = max(2, int(w))
    s = R
    if kind == "box":
        d.rectangle([hx - s * 0.75, hy - s * 0.55, hx + s * 0.75, hy + s * 0.45], fill=(196, 150, 96), outline=INK, width=o)
        d.line([(hx - s * 0.2, hy - s * 0.55), (hx - s * 0.2, hy + s * 0.45)], fill=(232, 200, 140), width=o * 2)
    elif kind == "moneybag":
        d.ellipse([hx - s * 0.6, hy - s * 0.35, hx + s * 0.6, hy + s * 0.7], fill=(186, 160, 110), outline=INK, width=o)
        d.polygon([(hx - s * 0.2, hy - s * 0.35), (hx + s * 0.2, hy - s * 0.35), (hx + s * 0.32, hy - s * 0.62), (hx - s * 0.32, hy - s * 0.62)],
                  fill=(186, 160, 110), outline=INK)
        d.text((hx - s * 0.15, hy - s * 0.05), "$", fill=(40, 120, 50), font=video._font(int(s * 0.55)))
    elif kind == "money":
        d.rectangle([hx - s * 0.6, hy - s * 0.3, hx + s * 0.6, hy + s * 0.3], fill=(120, 180, 110), outline=INK, width=o)
        d.ellipse([hx - s * 0.15, hy - s * 0.15, hx + s * 0.15, hy + s * 0.15], outline=(40, 100, 50), width=o)
    elif kind == "phone":
        d.rounded_rectangle([hx - s * 0.22, hy - s * 0.42, hx + s * 0.22, hy + s * 0.42], s * 0.06, fill=(30, 30, 36), outline=INK, width=o)
        d.rectangle([hx - s * 0.17, hy - s * 0.34, hx + s * 0.17, hy + s * 0.3], fill=(110, 190, 240))
    elif kind == "key":
        d.ellipse([hx - s * 0.25, hy - s * 0.25, hx + s * 0.05, hy + s * 0.05], outline=(220, 180, 60), width=o * 2)
        d.line([(hx + s * 0.03, hy - s * 0.1), (hx + s * 0.5, hy + s * 0.25)], fill=(220, 180, 60), width=o * 2)
    elif kind == "paper":
        d.rectangle([hx - s * 0.55, hy - s * 0.7, hx + s * 0.55, hy + s * 0.5], fill=(238, 232, 214), outline=INK, width=o)
        for i in range(5):
            y = hy - s * 0.5 + i * s * 0.2
            d.line([(hx - s * 0.4, y), (hx + s * 0.4, y)], fill=(120, 116, 104), width=max(1, o // 2))
    elif kind == "gold":
        d.polygon([(hx - s * 0.4, hy), (hx - s * 0.15, hy - s * 0.32), (hx + s * 0.3, hy - s * 0.25), (hx + s * 0.42, hy + s * 0.1),
                   (hx + s * 0.05, hy + s * 0.3), (hx - s * 0.3, hy + s * 0.22)], fill=(240, 196, 60), outline=INK)
    elif kind == "pizza":
        d.polygon([(hx - s * 0.45, hy - s * 0.3), (hx + s * 0.45, hy - s * 0.3), (hx, hy + s * 0.55)], fill=(244, 200, 100), outline=INK)
        for p in ((-0.12, -0.1), (0.15, -0.05), (0, 0.18)):
            d.ellipse([hx + p[0] * s - s * 0.07, hy + p[1] * s - s * 0.07, hx + p[0] * s + s * 0.07, hy + p[1] * s + s * 0.07], fill=(190, 50, 40))
    elif kind == "coffee":
        d.rectangle([hx - s * 0.22, hy - s * 0.3, hx + s * 0.22, hy + s * 0.3], fill=(250, 250, 250), outline=INK, width=o)
        d.rectangle([hx - s * 0.22, hy - s * 0.05, hx + s * 0.22, hy + s * 0.12], fill=(140, 90, 60))
    elif kind == "sign":
        d.line([(hx, hy), (hx, hy + s * 1.2)], fill=(120, 80, 50), width=o * 2)
        d.rectangle([hx - s * 0.8, hy - s * 0.7, hx + s * 0.8, hy], fill=(250, 250, 250), outline=INK, width=o)
    elif kind == "pickaxe":
        d.line([(hx, hy + s * 0.6), (hx, hy - s * 0.6)], fill=(130, 90, 55), width=o * 2)
        d.arc([hx - s * 0.7, hy - s * 0.9, hx + s * 0.7, hy - s * 0.3], 200, 340, fill=(120, 120, 130), width=o * 3)
    elif kind == "pan":
        d.ellipse([hx - s * 0.7, hy - s * 0.25, hx + s * 0.7, hy + s * 0.25], fill=(90, 90, 96), outline=INK, width=o)
        d.ellipse([hx - s * 0.5, hy - s * 0.15, hx + s * 0.5, hy + s * 0.15], fill=(140, 120, 80))


def draw_character(ch, R, pose="stand", face="normal", ph=0.0, talking=False, prop="none", flip=False):
    """One frame of a character as an RGBA image. The feet stand at (W/2, H - pad). Drawn at 2x and scaled down
    so the lines stay smooth."""
    SS = 2
    r = R * SS
    W, H = int(r * 5.4), int(r * 6.6)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = r * 0.095
    (la, ra, leg, bob) = _pose_angles(pose, ph)
    cx = W / 2
    ground = H - r * 0.25
    lift = bob * r * 2
    sit = pose == "sit"
    hip = ground - r * (1.45 if not sit else 0.9) - lift
    # legs
    for side in (-1, 1):
        a = math.radians(leg * side)
        hx = cx + side * r * 0.28
        if sit:
            knee = (hx + r * 0.75, hip + r * 0.05)
            foot = (knee[0] + r * 0.05, ground)
            _limb(d, (hx, hip), knee, w)
            _limb(d, knee, foot, w)
        else:
            foot = (hx + math.sin(a) * r * 1.45, hip + math.cos(a) * r * 1.45)
            _limb(d, (hx, hip), foot, w)
        d.ellipse([foot[0] - r * 0.26, foot[1] - r * 0.12, foot[0] + r * 0.3, foot[1] + r * 0.12], fill=INK)
    # torso
    ttop = hip - r * 1.45
    _torso(d, ch, cx, ttop, hip + r * 0.05, r, w)
    # arms (behind/in front simplified: both in front)
    sh_y = ttop + r * 0.25
    raised = (la[0] > 120, ra[0] > 120)
    arms = {}

    def arms_now(which):
        for side, (ang, el), up in ((-1, la, raised[0]), (1, ra, raised[1])):
            if up == which:
                arms[side] = _arm(d, (cx + side * r * 0.62, sh_y), ang, el, r * 0.72, w, side)

    arms_now(False)                                   # arms at the side go under the head ...
    # head
    hc = (cx, ttop - r * 0.88)
    d.ellipse([hc[0] - r, hc[1] - r, hc[0] + r, hc[1] + r], fill=SKIN, outline=INK, width=max(2, int(w * 0.9)))
    blink = (ph * 7.3) % 1 < 0.04 and face not in ("surprised",)
    _face(d, hc[0], hc[1], r, face, talking, ch, w * 0.75, blink)
    _hat(d, ch.get("hat", "none"), hc[0], hc[1] - r * 0.62, r, _rgb(ch.get("hat_color"), (40, 40, 40)), w * 0.8)
    arms_now(True)                                    # ... raised arms go over it, beside the head
    lh, rh = arms[-1], arms[1]
    # prop in the hands (held in front)
    if prop and prop != "none":
        if pose == "hold":
            _prop(d, prop, (lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2 + r * 0.1, r, w * 0.7)
        else:
            _prop(d, prop, rh[0], rh[1], r * 0.8, w * 0.7)
    img = img.resize((W // SS, H // SS), Image.LANCZOS)
    if flip:
        img = ImageOps.mirror(img)
    return img


# ---------------------------------------------------------------- planning

def _target_seconds(brief):
    m = re.search(r"\b(\d{1,3})\s*(?:-|to)?\s*(?:\d{1,3}\s*)?(seconds?|secs?|minutes?|mins?)\b", brief or "", re.I)
    if not m:
        return 45
    n = int(m.group(1))
    return max(15, min(180, n * 60 if m.group(2).lower().startswith("min") else n))


def plan(user, brief, fmt="vertical"):
    secs = _target_seconds(brief)
    shots_n = max(6, min(40, round(secs / 2.8)))
    words = int(secs * 2.5)
    sys_msg = (
        "You direct short cartoon videos in this exact style: flat 2D cartoon characters with BIG round white heads, dot "
        "eyes, simple coloured outfits, stick arms and legs and signature hats, placed into detailed realistic 3D "
        "environments. Fast cuts (one shot every 2-3 seconds), lots of expressions and close-ups, a storyteller narrator.\n"
        "Reply with ONLY valid JSON:\n"
        '{"title":"short title","cast":[{"id":"a","name":"short name","hat":"' + "|".join(HATS) + '","hat_color":"#hex",'
        '"outfit":"' + "|".join(OUTFITS) + '","color":"#hex","mustache":false,"glasses":false}],'
        '"places":[{"id":"p1","look":"the empty location in rich visual detail, no people"}],'
        '"shots":[{"line":"the narration words spoken during this shot","place":"p1","chars":[{"id":"a","pose":"'
        + "|".join(POSES) + '","face":"' + "|".join(FACES) + '","x":"left|center|right","size":"' + "|".join(SIZES)
        + '","flip":false}],"speaker":"a or none","prop":"' + "|".join(PROPS) + '","cam":"' + "|".join(CAMS) + '"}]}\n'
        "Rules: 1-3 cast members, each with a distinct hat and outfit colour, kept the same all video. 2-5 places. "
        f"About {shots_n} shots. Each line 3-12 words; the lines read in order are the full narration (about {words} "
        "words), with a strong hook first and a payoff at the end. If the user gave a script, use their words exactly, "
        "split across the shots. Vary sizes: establishing wide shots, medium shots, and close-ups for reactions. "
        "1-2 characters per shot. speaker = the character whose mouth moves (their dialogue), or none for narration.")
    p = {}
    if core.LLM:
        try:
            p = _loads(core.LLM(user, [{"role": "system", "content": sys_msg},
                                       {"role": "user", "content": f"Make the video. Script or topic:\n{brief}"}]).get("content") or "")
        except Exception:
            p = {}
    return _clean(p, brief)


def _loads(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except Exception:
            return {}


def _pick(v, allowed, default):
    v = str(v or "").lower().strip()
    return v if v in allowed else default


def _clean(p, brief):
    palette = ["#2f8f7f", "#c0392b", "#3a6ea5", "#8e44ad", "#d68910", "#566573"]
    cast = []
    for i, c in enumerate((p.get("cast") or [])[:3]):
        cast.append({"id": str(c.get("id") or chr(97 + i)), "name": str(c.get("name") or f"Char {i + 1}")[:20],
                     "hat": _pick(c.get("hat"), HATS, HATS[i % 6]), "hat_color": c.get("hat_color") or "#2b2b2b",
                     "outfit": _pick(c.get("outfit"), OUTFITS, "shirt"), "color": c.get("color") or palette[i],
                     "mustache": bool(c.get("mustache")), "glasses": bool(c.get("glasses"))})
    if not cast:
        cast = [{"id": "a", "name": "Sam", "hat": "beanie", "hat_color": "#e0b030", "outfit": "shirt", "color": "#2f8f7f",
                 "mustache": False, "glasses": False}]
    ids = {c["id"] for c in cast}
    places = [{"id": str(x.get("id") or f"p{i}"), "look": str(x.get("look") or "a cosy room")[:300]}
              for i, x in enumerate((p.get("places") or [])[:6])] or [{"id": "p1", "look": "a warm cosy living room"}]
    pids = {x["id"] for x in places}
    shots = []
    for s in (p.get("shots") or [])[:60]:
        line = re.sub(r"\s+", " ", str(s.get("line") or "")).strip()
        if not line:
            continue
        chars = []
        for c in (s.get("chars") or [])[:3]:
            if str(c.get("id")) in ids:
                chars.append({"id": str(c["id"]), "pose": _pick(c.get("pose"), POSES, "talk"),
                              "face": _pick(c.get("face"), FACES, "normal"), "x": _pick(c.get("x"), ("left", "center", "right"), "center"),
                              "size": _pick(c.get("size"), SIZES, "medium"), "flip": bool(c.get("flip"))})
        sp = str(s.get("speaker") or "none")
        shots.append({"line": line[:200], "place": s.get("place") if s.get("place") in pids else places[0]["id"],
                      "chars": chars, "speaker": sp if sp in ids else "none",
                      "prop": _pick(s.get("prop"), PROPS, "none"), "cam": _pick(s.get("cam"), CAMS, "in")})
    if not shots:                                      # no brain: one shot per sentence of the brief
        for i, sent in enumerate([x for x in re.split(r"(?<=[.!?])\s+", brief or "") if x.strip()][:20]):
            shots.append({"line": sent.strip()[:200], "place": places[0]["id"],
                          "chars": [{"id": cast[0]["id"], "pose": ("talk", "point", "think", "shrug")[i % 4],
                                     "face": ("normal", "happy", "surprised", "smug")[i % 4], "x": "center",
                                     "size": SIZES[i % 3], "flip": False}],
                          "speaker": "none", "prop": "none", "cam": CAMS[i % 4]})
    return {"title": str(p.get("title") or "Stickman story")[:60], "cast": cast, "places": places, "shots": shots}


# ---------------------------------------------------------------- pictures

def _background(look, w, h, seed, tint=""):
    """The location picture, made once and reused. Falls back to a soft painted room if the image service fails."""
    path = video.ai_image(look + BG_STYLE, w, h, seed) or video.ai_image(look + BG_STYLE, w, h, seed + 7)   # one retry
    if path:
        img = Image.open(path).convert("RGB")
        img = img.crop((0, 0, img.width, int(img.height * 0.95)))          # trim the service's corner mark
    else:
        img = Image.new("RGB", (w, h), (70, 52, 40))
        d = ImageDraw.Draw(img)
        for y in range(h):
            f = y / h
            d.line([(0, y), (w, y)], fill=(int(120 - 60 * f), int(92 - 40 * f), int(70 - 30 * f)))
        d.rectangle([0, int(h * 0.72), w, h], fill=(96, 66, 44))
    img = ImageOps.fit(img, (int(w * 1.18), int(h * 1.18)), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(1.2))                     # a touch soft, so the characters pop
    img = ImageEnhance.Contrast(img).enhance(1.05)
    if tint == "noir":
        img = ImageOps.colorize(ImageOps.grayscale(img), (10, 12, 18), (200, 205, 215)).convert("RGB")
    return img


def _cam_frame(bg, w, h, t, cam):
    """Crop the oversized background with a slow move."""
    W, H = bg.size
    z = {"in": 1 + 0.06 * t, "out": 1.06 - 0.06 * t}.get(cam, 1.03)
    cw, chh = int(W / z / 1.0), int(H / z / 1.0)
    cw, chh = min(W, int(w * 1.18 / z)), min(H, int(h * 1.18 / z))
    dx = {"left": -0.5 + t, "right": 0.5 - t}.get(cam, 0.0)
    x0 = int((W - cw) / 2 + dx * (W - cw) * 0.9)
    y0 = int((H - chh) / 2)
    x0 = max(0, min(W - cw, x0))
    return bg.crop((x0, y0, x0 + cw, y0 + chh)).resize((w, h), Image.BILINEAR)


SIZE_GEOM = {"wide": (0.07, 0.88), "medium": (0.12, 1.04), "close": (0.2, 1.42)}   # head radius (x frame h), feet y
XPOS = {"left": 0.3, "center": 0.5, "right": 0.7}


def _caption(frame, word, w, h, scale=1.0, at=0.72):
    if not word:
        return
    d = ImageDraw.Draw(frame)
    size = int(w * 0.095 * scale)
    f = video._font(size)
    tw = d.textlength(word, font=f)
    while tw > w * 0.88 and size > 20:
        size -= 4
        f = video._font(size)
        tw = d.textlength(word, font=f)
    x = (w - tw) / 2
    y = h * at
    d.text((x, y), word, font=f, fill=(255, 255, 255), stroke_width=max(4, size // 9), stroke_fill=(0, 0, 0))


def _word_times(line, dur, lead):
    words = line.split()
    weights = [max(2, len(re.sub(r"\W", "", x))) + (3 if re.search(r"[.,!?;:]$", x) else 0) for x in words]
    tot = float(sum(weights)) or 1.0
    out, t = [], lead
    for x, wt in zip(words, weights):
        d = dur * wt / tot
        out.append((t, t + d, x))
        t += d
    return out


def _render_shot(path, shot, cast, bg, w, h, dur, words, talk_span, tint=""):
    """Render one shot to its own silent MP4."""
    ff = video.find_ffmpeg()
    proc = subprocess.Popen([ff, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS),
                             "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(path)],
                            stdin=subprocess.PIPE)
    by_id = {c["id"]: c for c in cast}
    n = max(1, int(round(dur * FPS)))
    cache = {}
    chars = shot["chars"]
    xs = [XPOS[c["x"]] for c in chars]
    if len(chars) == 2:                                   # two in a shot: spread out, a little smaller, never overlapping
        xs = [0.27, 0.73] if xs[0] <= xs[1] else [0.73, 0.27]
    elif len(chars) == 3:
        xs = [0.2, 0.5, 0.8]
    shrink = {1: 1.0, 2: 0.82, 3: 0.68}.get(len(chars), 1.0)
    cap_y = 0.8 if any(c["size"] == "close" for c in chars) else 0.72
    for i in range(n):
        t = i / FPS
        frame = _cam_frame(bg, w, h, t / max(dur, 0.1), shot["cam"])
        if tint == "noir":
            pass
        for c, xp in zip(chars, xs):
            ch = by_id[c["id"]]
            hr, fy = SIZE_GEOM[c["size"]]
            R = int(h * hr * shrink)
            talking = (shot["speaker"] == c["id"]) and talk_span[0] <= t <= talk_span[1] and int(t * 9) % 2 == 0
            ph = round(((t * 0.9) % 1.0) * 12) / 12                          # 12 poses per loop, cached
            pose = c["pose"]
            walk_dx = 0.0
            if pose in ("walk", "run"):
                ph = round(((t * (1.6 if pose == "walk" else 2.6)) % 1.0) * 12) / 12
                walk_dx = (t / max(dur, 0.1) - 0.5) * (0.18 if pose == "walk" else 0.3) * (-1 if c["flip"] else 1)
            key = (c["id"], R, pose, c["face"], ph, talking, shot["prop"] if c is chars[0] else "none", c["flip"])
            spr = cache.get(key)
            if spr is None:
                spr = draw_character(ch, R, pose, c["face"], ph, talking, key[6], c["flip"])
                if len(cache) > 400:
                    cache.clear()
                cache[key] = spr
            px = int(w * (xp + walk_dx) - spr.width / 2)
            py = int(h * fy - spr.height + R * 0.25)
            if c["size"] != "close":                                        # contact shadow on the floor
                sh = Image.new("RGBA", (int(R * 2.6), int(R * 0.5)), (0, 0, 0, 0))
                ImageDraw.Draw(sh).ellipse([0, 0, sh.width, sh.height], fill=(0, 0, 0, 90))
                frame.paste(sh, (int(w * (xp + walk_dx) - sh.width / 2), int(h * fy - sh.height / 2)), sh)
            frame.paste(spr, (px, py), spr)
        word = next((x for a, b, x in words if a <= t < b), "")
        pop = 1.0
        hit = next((a for a, b, x in words if a <= t < b), None)
        if hit is not None and t - hit < 0.08:
            pop = 1.12
        _caption(frame, word, w, h, pop, cap_y)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0 or not Path(path).exists():
        raise RuntimeError(f"rendering a shot failed (ffmpeg {proc.returncode})")


# ---------------------------------------------------------------- the whole job

def make(user, brief, fmt="vertical", look="cartoon", progress=None, job_id=None):
    """Brief/script -> planned shots -> Kokoro voice per line -> each shot rendered and saved -> stitched with music."""
    import numpy as np
    import localvoice
    say = progress or (lambda t: None)
    video.init()
    s = core.settings(user)
    fmt = fmt if fmt in video.SIZES else "vertical"
    w, h = video.SIZES[fmt]
    tint = "noir" if look == "noir" else ""
    job_id = job_id or ("toon-" + hashlib.sha1(f"{brief}|{fmt}|{look}".encode()).hexdigest()[:10])
    jdir = JOBS / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    pf = jdir / "plan.json"
    if pf.exists():
        p = json.loads(pf.read_text())
        say("Reusing the plan from the last run")
    else:
        say("Directing the shots and the cast")
        p = plan(user, brief, fmt)
        pf.write_text(json.dumps(p))
    shots = p["shots"]

    # 1. the voice: each shot's line, so every shot is exactly as long as its words
    voice = s.get("kokoro_voice") or "bm_george"
    speed = float(s.get("kokoro_speed") or 1.0)
    sr = localvoice.SR
    lines = []
    for i, sh in enumerate(shots):
        lp = jdir / f"line_{i:02d}.npy"
        if lp.exists():
            a = np.load(lp)
        else:
            say(f"Recording line {i + 1} of {len(shots)} (Kokoro, {voice})")
            a = video.kokoro_line(sh["line"], voice, speed)          # raises with the reason: no silent video
            np.save(lp, a)
        lines.append(a)
    lead, tail = 0.12, 0.18
    durs = [round(len(a) / sr + lead + tail, 3) for a in lines]

    # 2. the places: one picture each, reused in every shot set there
    bgs = {}
    for i, pl in enumerate(p["places"]):
        say(f"Building set {i + 1} of {len(p['places'])}")
        seed = int(hashlib.sha1((job_id + pl["id"]).encode()).hexdigest()[:6], 16)
        bgs[pl["id"]] = _background(pl["look"], w, h, seed, tint)

    # 3. every shot rendered to its own file (resumable)
    seg_paths = []
    for i, (sh, d) in enumerate(zip(shots, durs)):
        seg = jdir / f"shot_{i:02d}_{fmt}.mp4"
        seg_paths.append(seg)
        if seg.exists() and seg.stat().st_size > 1000:
            say(f"Shot {i + 1} of {len(shots)} already made - reusing it")
            continue
        say(f"Animating shot {i + 1} of {len(shots)}")
        words = _word_times(sh["line"], len(lines[i]) / sr, lead)
        tmp = seg.with_suffix(".part.mp4")
        _render_shot(tmp, sh, p["cast"], bgs[sh["place"]], w, h, d, words, (lead, lead + len(lines[i]) / sr), tint)
        os.replace(tmp, seg)

    # 4. the soundtrack: the lines laid end to end at each shot's start, music underneath
    say("Mixing the voice and music")
    total = sum(durs)
    track = np.zeros(int((total + 0.5) * sr), dtype="float32")
    t = 0.0
    for a, d in zip(lines, durs):
        at = int((t + lead) * sr)
        track[at: at + len(a)] += a[: max(0, len(track) - at)]
        t += d
    vo = jdir / "voice.wav"
    vo.write_bytes(localvoice.wav_bytes(track, sr))
    lst = jdir / f"shots_{fmt}.txt"
    lst.write_text("".join(f"file '{x.as_posix()}'\n" for x in seg_paths))
    ff = video.find_ffmpeg()
    folder = core.out_dir(user, "Trillion Videos")
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower()).strip("-")[:40] or "stickman"
    out = folder / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    music, music_name = video.pick_music()
    cmd = [ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(vo)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music), "-filter_complex",
                f"[2:a]volume=0.12,afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("Joining the shots failed: " + (r.stderr or "")[-300:])
    thumb = str(out)[:-4] + ".jpg"
    subprocess.run([ff, "-y", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2", thumb],
                   capture_output=True)
    detail = {"style": "cartoon stickman", "cast": [c["name"] for c in p["cast"]], "shots": len(shots),
              "narration": " ".join(x["line"] for x in shots), "voice": f"Kokoro ({voice})", "music": music_name,
              "format": fmt, "request": brief, "job_id": job_id}
    vid = core.x("INSERT INTO videos(user_id,title,path,thumb,seconds,detail,created) VALUES(?,?,?,?,?,?,?)",
                 (user["id"], p["title"], str(out), thumb if os.path.exists(thumb) else "", total, json.dumps(detail), time.time()))
    return {"video_id": vid, "title": p["title"], "saved": str(out), "seconds": round(total, 1), "shots": len(shots),
            "scenes": len(shots),
            "cast": [c["name"] for c in p["cast"]], "voice_over": True, "job_id": job_id,
            "notes": [f"Voice: Kokoro ({voice}).", "Every shot is saved, so asking again with the same words resumes."]}
