"""Subprocess runner for the BEFORE / AFTER pitch card: screenshot the old live site and the new Trillion site,
each on desktop and phone, and composite them into one outreach image. Run as a short-lived subprocess so a
browser hiccup can never take down Trillion's web server.

  python beforeafter_run.py <old_url> <new_url> <out.jpg> "<Business name>" "#accent"

Compositing adapted from the MIT website-rebuilder by Jonny West
(github.com/pantheonmarketing/claude-website-rebuilder).
"""
import os
import sys


def _launch(pw):
    args = ["--no-sandbox", "--disable-dev-shm-usage", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"]
    exe = os.environ.get("BA_CHROME")
    if exe:
        try:
            return pw.chromium.launch(headless=True, executable_path=exe, args=args)
        except Exception:
            pass
    for ch in ("msedge", "chrome"):
        try:
            return pw.chromium.launch(headless=True, channel=ch, args=args)
        except Exception:
            pass
    return pw.chromium.launch(headless=True, args=args)


def _grab(browser, url, prefix):
    for name, vp, mob in (("d", {"width": 1440, "height": 900}, False),
                          ("m", {"width": 390, "height": 844}, True)):
        ctx = browser.new_context(viewport=vp, is_mobile=mob, device_scale_factor=2 if mob else 1)
        pg = ctx.new_page()
        try:
            pg.goto(url, wait_until="load", timeout=60000)
            pg.wait_for_timeout(3200)
        except Exception:
            # even a blank/half-loaded old site is still worth showing as the "before"
            pass
        pg.screenshot(path=f"{prefix}-{name}.png")
        ctx.close()


def _font(size):
    from PIL import ImageFont
    for f in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _framed(img, w, radius, border):
    from PIL import Image, ImageDraw
    img = img.convert("RGB").resize((w, int(img.height * w / img.width)), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = Image.new("RGBA", (img.width + border * 2, img.height + border * 2), (0, 0, 0, 0))
    if border:
        ImageDraw.Draw(out).rounded_rectangle((0, 0, out.width - 1, out.height - 1), radius + border,
                                              fill=(255, 255, 255, 255))
    out.paste(img, (border, border), mask)
    return out


def _shadow(canvas, box, radius):
    from PIL import Image, ImageDraw, ImageFilter
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((box[0] + 6, box[1] + 14, box[2] + 6, box[3] + 14), radius,
                                         fill=(0, 0, 0, 90))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))


def _hex(h):
    h = str(h or "#C9A84C").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return (201, 168, 76)


def build(name, accent, out):
    from PIL import Image, ImageDraw
    acc = _hex(accent)
    W, colw, gap, ytop = 1800, 790, 80, 200
    x0s = (70, 70 + colw + gap)
    c = Image.new("RGBA", (W, 1500), (17, 19, 26, 255))
    d = ImageDraw.Draw(c)
    d.rectangle((0, 0, W, 8), fill=acc)
    d.text((70, 44), name or "Your business", font=_font(54), fill="white")
    d.text((72, 124), "A new website preview, made for you  \u00b7  on a computer and on a phone",
           font=_font(26), fill=(150, 158, 172))
    bottom = 0
    for i, (tag, pre) in enumerate((("Your website today", "_old"), ("Your new website", "_new"))):
        x = x0s[i]
        pill = (70, 76, 90) if i == 0 else acc
        tw = d.textlength(tag, font=_font(28))
        d.rounded_rectangle((x, ytop, x + tw + 44, ytop + 50), 25, fill=pill)
        d.text((x + 22, ytop + 8), tag, font=_font(28), fill="white")
        desk = _framed(Image.open(pre + "-d.png"), colw, 14, 0)
        dy = ytop + 76
        _shadow(c, (x, dy, x + desk.width, dy + desk.height), 14)
        c.alpha_composite(desk, (x, dy))
        phone = _framed(Image.open(pre + "-m.png"), 240, 26, 8)
        phone = phone.crop((0, 0, phone.width, min(phone.height, 520)))
        px, py = x + colw - phone.width - 24, dy + desk.height - 190
        _shadow(c, (px, py, px + phone.width, py + phone.height), 30)
        c.alpha_composite(phone, (px, py))
        bottom = max(bottom, py + phone.height)
    c = c.crop((0, 0, W, bottom + 60))
    c.convert("RGB").save(out, quality=90)


def main():
    old, new, out, name = sys.argv[1:5]
    accent = sys.argv[5] if len(sys.argv) > 5 else "#C9A84C"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = _launch(pw)
        try:
            _grab(b, old, "_old")
            _grab(b, new, "_new")
        finally:
            b.close()
    build(name, accent, out)
    for f in ("_old-d.png", "_old-m.png", "_new-d.png", "_new-m.png"):
        try:
            os.remove(f)
        except Exception:
            pass
    print("BA_OK " + out)


if __name__ == "__main__":
    main()
