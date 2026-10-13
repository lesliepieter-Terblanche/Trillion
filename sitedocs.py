"""The files made from a website reading: a presentation (.pptx), a report (.docx) and a spreadsheet (.xlsx).
Laid out by hand so they look finished when opened, in the login's own accent colour."""
import datetime
import re
from pathlib import Path
from urllib.parse import urlparse

import core

NAVY, CARD, LINE = "0E1726", "17223A", "26324D"
WHITE, SOFT, MUTED = "FFFFFF", "C9D3E6", "8A96AE"
GOOD, BAD, WARN = "4ADE80", "F87171", "FBBF24"
LEVEL = {"high": BAD, "medium": WARN, "low": "60A5FA", "": MUTED}
FONT = "Segoe UI"


def accent(user):
    a = str(core.settings(user).get("accent") or "#ffb454").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", a):
        return "FFB454"
    r, g, b = (int(a[i:i + 2], 16) for i in (0, 2, 4))
    return a.upper() if (0.299 * r + 0.587 * g + 0.114 * b) > 110 else "FFB454"     # too dark to read on navy


def cut(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name).strip(" .")[:60] or "Website"


def make(kind, folder, result, checks, pages, info, user):
    stem = safe_name(result["name"]) + " - website " + {"pptx": "analysis", "docx": "report", "xlsx": "pages"}[kind]
    out = Path(folder) / f"{stem}.{kind}"
    {"pptx": deck, "docx": report, "xlsx": sheet}[kind](out, result, checks, pages, info, accent(user))
    return out


def shots(pages):
    return [p for p in pages if p.get("shot") and Path(p["shot"]).exists()]


# ---------------------------------------------------------------- presentation
def deck(out, R, C, pages, info, acc):
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    rgb = lambda h: RGBColor.from_string(h)
    n_slide = [0]

    def new(title=None, kicker=None):
        s = prs.slides.add_slide(blank)
        n_slide[0] += 1
        bg = s.background.fill
        bg.solid()
        bg.fore_color.rgb = rgb(NAVY)
        if title:
            box(s, 0.6, 0.42, 0.08, 0.62, acc)
            if kicker:
                text(s, 0.85, 0.3, 9, 0.35, kicker.upper(), 11, acc, bold=True, spacing=2)
            text(s, 0.85, 0.58 if kicker else 0.42, 11.5, 0.7, title, 28, WHITE, bold=True)
        text(s, 0.6, 7.0, 8, 0.3, f"{R['name']}  ·  {info['domain']}", 9, MUTED)
        text(s, 11.9, 7.0, 0.8, 0.3, str(n_slide[0]), 9, MUTED, align=PP_ALIGN.RIGHT)
        return s

    def box(s, x, y, w, h, fill, round_=False, line=None):
        sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if round_ else MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill)
        if line:
            sh.line.color.rgb = rgb(line)
            sh.line.width = Pt(1)
        else:
            sh.line.fill.background()
        if round_:
            sh.adjustments[0] = 0.08
        sh.shadow.inherit = False
        return sh

    def text(s, x, y, w, h, t, size, color, bold=False, align=PP_ALIGN.LEFT, spacing=0, anchor=MSO_ANCHOR.TOP, lines=None):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        items = lines if lines is not None else [t]
        for i, item in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            if lines is not None:
                p.space_after = Pt(8)
            for part in (item if isinstance(item, list) else [(item, size, color, bold)]):
                r = p.add_run()
                r.text = part[0]
                r.font.size = Pt(part[1])
                r.font.color.rgb = rgb(part[2])
                r.font.bold = part[3]
                r.font.name = FONT
                if spacing:
                    r.font._element.set("spc", str(spacing * 100))
        return tb

    def picture(s, path, x, y, w, h):
        """A screenshot in a browser frame, cropped to fill the space without stretching."""
        box(s, x - 0.06, y - 0.36, w + 0.12, h + 0.42, LINE, round_=True)
        for i, c in enumerate(("F87171", "FBBF24", "4ADE80")):
            dot = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.1 + i * 0.2), Inches(y - 0.24), Inches(0.11), Inches(0.11))
            dot.fill.solid()
            dot.fill.fore_color.rgb = rgb(c)
            dot.line.fill.background()
        try:
            from PIL import Image
            iw, ih = Image.open(path).size
        except Exception:
            iw, ih = 1366, 800
        pic = s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
        want = w / h
        have = iw / ih
        if have > want:
            pic.crop_right = 1 - want / have
        else:
            pic.crop_bottom = 1 - have / want
        return pic

    snaps = shots(pages)
    today = datetime.date.today().strftime("%d %B %Y").lstrip("0")

    # 1. cover
    s = new()
    box(s, 0, 0, 0.18, 7.5, acc)
    text(s, 0.75, 1.15, 6, 0.4, "WEBSITE ANALYSIS", 13, acc, bold=True, spacing=3)
    text(s, 0.75, 1.65, 6.2, 1.9, cut(R["name"], 48), 44 if len(R["name"]) < 22 else 34, WHITE, bold=True)
    text(s, 0.75, 3.75, 5.9, 1.4, cut(R["one_liner"], 170), 17, SOFT)
    text(s, 0.75, 5.75, 6, 0.35, info["domain"], 14, WHITE, bold=True)
    text(s, 0.75, 6.1, 6, 0.35, f"{len(pages)} pages read  ·  {today}", 12, MUTED)
    if snaps:
        picture(s, snaps[0]["shot"], 6.95, 2.05, 5.8, 3.4)

    # 2. at a glance
    s = new("At a glance", "Summary")
    text(s, 0.85, 1.55, 6.4, 5.2, cut(R["summary"], 900), 15, SOFT)
    stats = [(str(len(pages)), "pages read"), (f"{C['score']}", "health score / 100"),
             (str(C["counts"]["high"]), "serious issues"), (f"{C['avg_load']}s" if C["avg_load"] else "–", "average load time")]
    for i, (big, small) in enumerate(stats):
        x, y = 7.75 + (i % 2) * 2.62, 1.55 + (i // 2) * 2.45
        box(s, x, y, 2.42, 2.2, CARD, round_=True)
        col = acc if i != 2 else (BAD if C["counts"]["high"] else GOOD)
        if i == 1:
            col = GOOD if C["score"] >= 75 else (WARN if C["score"] >= 50 else BAD)
        text(s, x + 0.25, y + 0.35, 2, 0.9, big, 40, col, bold=True)
        text(s, x + 0.25, y + 1.4, 2, 0.6, small, 12, MUTED)

    # 3. what they offer
    offer = R["offering"][:6]
    if offer:
        s = new("What the site offers", "Offering")
        cols = 3 if len(offer) > 4 else 2
        w = (11.8 - 0.3 * (cols - 1)) / cols
        rows = (len(offer) + cols - 1) // cols
        h = min(2.45, (5.2 - 0.3 * (rows - 1)) / rows)
        for i, o in enumerate(offer):
            x, y = 0.75 + (i % cols) * (w + 0.3), 1.6 + (i // cols) * (h + 0.3)
            box(s, x, y, w, h, CARD, round_=True)
            box(s, x, y, 0.07, h, acc)
            text(s, x + 0.3, y + 0.25, w - 0.5, 0.5, cut(o["title"], 40), 16, WHITE, bold=True)
            text(s, x + 0.3, y + 0.8, w - 0.5, h - 0.95, cut(o["detail"], 150 if cols == 3 else 200), 12, SOFT)

    # 4. audience, tone, facts
    if R["audience"] or R["key_facts"]:
        s = new("Who it is for", "Audience")
        box(s, 0.75, 1.6, 5.7, 5.0, CARD, round_=True)
        text(s, 1.05, 1.85, 5.1, 0.4, "AUDIENCE", 11, acc, bold=True, spacing=2)
        text(s, 1.05, 2.3, 5.1, 2.0, cut(R["audience"], 240), 17, WHITE)
        if R["tone"]:
            text(s, 1.05, 4.45, 5.1, 0.4, "TONE AND LOOK", 11, acc, bold=True, spacing=2)
            text(s, 1.05, 4.9, 5.1, 1.5, cut(R["tone"], 200), 13, SOFT)
        text(s, 6.95, 1.65, 5.6, 0.4, "KEY FACTS", 11, acc, bold=True, spacing=2)
        facts = R["key_facts"][:6] or ["No specific facts were found."]
        text(s, 6.95, 2.15, 5.6, 4.5, "", 15, SOFT, lines=[[("●  ", 10, acc, False), (cut(f, 110), 15, SOFT, False)] for f in facts])

    # 5. the user's own question
    if R.get("answer"):
        s = new("Your question", "Answer")
        text(s, 0.85, 1.55, 11.6, 4.9, cut(R["answer"], 1100), 16, SOFT)

    # 6. strengths and weaknesses
    if R["strengths"] or R["weaknesses"]:
        s = new("Strengths and weaknesses", "Assessment")
        for col, (label, items, colour) in enumerate((("WORKS WELL", R["strengths"], GOOD), ("NEEDS WORK", R["weaknesses"], BAD))):
            x = 0.75 + col * 6.05
            box(s, x, 1.6, 5.75, 5.05, CARD, round_=True)
            box(s, x, 1.6, 5.75, 0.07, colour)
            text(s, x + 0.35, 1.9, 5, 0.4, label, 12, colour, bold=True, spacing=2)
            its = items[:5] or ["Nothing stood out."]
            fs = 16 if len(its) <= 4 else 14
            text(s, x + 0.35, 2.45, 5.1, 4.1, "", fs, SOFT,
                 lines=[[("●  ", 10, colour, False), (cut(t, 120), fs, SOFT, False)] for t in its])

    # 7. health
    s = new("Website health", "Technical checks")
    box(s, 0.75, 1.6, 3.4, 5.05, CARD, round_=True)
    sc = C["score"]
    col = GOOD if sc >= 75 else (WARN if sc >= 50 else BAD)
    text(s, 0.75, 1.95, 3.4, 1.3, str(sc), 66, col, bold=True, align=PP_ALIGN.CENTER)
    text(s, 0.75, 3.3, 3.4, 0.4, "out of 100", 12, MUTED, align=PP_ALIGN.CENTER)
    for i, lv in enumerate(("high", "medium", "low")):
        y = 4.05 + i * 0.75
        dot = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(1.15), Inches(y + 0.08), Inches(0.2), Inches(0.2))
        dot.fill.solid()
        dot.fill.fore_color.rgb = rgb(LEVEL[lv])
        dot.line.fill.background()
        text(s, 1.5, y, 2.4, 0.4, ("Serious", "Medium", "Small")[i] + f"   {C['counts'][lv]}", 14, WHITE)
    groups = {}
    for iss in C_issues(C):
        k = iss.get("kind") or iss["what"]
        g = groups.setdefault(k, {"what": k, "level": iss["level"], "n": 0, "fix": iss["fix"]})
        g["n"] += 1
    top = list(groups.values())[:6]
    text(s, 4.6, 1.65, 8, 0.4, "MOST COMMON", 11, acc, bold=True, spacing=2)
    for i, g in enumerate(top or [{"what": "No problems found on the pages read.", "level": "", "n": 0, "fix": ""}]):
        y = 2.15 + i * 0.75
        box(s, 4.6, y, 8.0, 0.62, CARD, round_=True)
        box(s, 4.6, y, 0.07, 0.62, LEVEL[g["level"]])
        text(s, 4.9, y + 0.08, 5.6, 0.5, cut(g["what"], 60), 13, WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
        if g["n"]:
            text(s, 10.6, y + 0.08, 1.8, 0.5, f"{g['n']} page{'s' if g['n'] != 1 else ''}", 12, MUTED, align=PP_ALIGN.RIGHT,
                 anchor=MSO_ANCHOR.MIDDLE)

    # 8. recommendations
    recs = R["recommendations"][:6]
    if recs:
        s = new("What to do next", "Recommendations")
        h = min(0.85, 5.1 / len(recs) - 0.12)
        for i, r in enumerate(recs):
            y = 1.6 + i * (h + 0.12)
            box(s, 0.75, y, 11.85, h, CARD, round_=True)
            text(s, 0.95, y, 0.6, h, str(i + 1), 22, acc, bold=True, anchor=MSO_ANCHOR.MIDDLE)
            text(s, 1.6, y + 0.1, 3.7, h - 0.2, cut(r["title"], 55), 14, WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
            text(s, 5.4, y + 0.08, 5.8, h - 0.16, cut(r["detail"], 150), 11.5, SOFT, anchor=MSO_ANCHOR.MIDDLE)
            if r["priority"]:
                pill = box(s, 11.35, y + h / 2 - 0.17, 1.05, 0.34, LEVEL[r["priority"]], round_=True)
                pill.adjustments[0] = 0.5
                text(s, 11.35, y + h / 2 - 0.17, 1.05, 0.34, r["priority"].upper(), 9, NAVY, bold=True, align=PP_ALIGN.CENTER,
                     anchor=MSO_ANCHOR.MIDDLE)

    # 9. pages looked at
    more = snaps[1:4]
    s = new("Pages looked at", "Site tour")
    if more:
        w = (11.8 - 0.45 * (len(more) - 1)) / len(more)
        for i, p in enumerate(more):
            x = 0.8 + i * (w + 0.45)
            picture(s, p["shot"], x, 2.0, w, w * 0.62)
            text(s, x, 2.15 + w * 0.62, w, 0.4, cut(p["title"] or urlparse(p["url"]).path, 45), 13, WHITE, bold=True)
            text(s, x, 2.55 + w * 0.62, w, 0.35, cut(urlparse(p["url"]).path or "/", 50), 10, MUTED)
    else:
        lines = [[(cut(p["title"] or "(no title)", 60), 13, WHITE, True), ("   " + cut(urlparse(p["url"]).path, 50), 11, MUTED, False)]
                 for p in pages[:10]]
        text(s, 0.85, 1.6, 11.6, 5, "", 13, WHITE, lines=lines)
    text(s, 0.85, 6.45, 11.5, 0.4, f"{len(pages)} pages read in all. The spreadsheet and report list every one.", 11, MUTED)

    # 10. next steps
    s = new()
    box(s, 0, 0, 0.18, 7.5, acc)
    text(s, 0.75, 1.3, 11, 0.5, "NEXT STEPS", 13, acc, bold=True, spacing=3)
    steps = R["next_steps"][:4] or [r["title"] for r in recs[:3]] or ["Fix the serious issues first."]
    for i, st in enumerate(steps):
        y = 2.1 + i * 1.1
        text(s, 0.75, y, 0.8, 0.8, f"0{i + 1}", 30, acc, bold=True)
        text(s, 1.75, y + 0.12, 10.5, 0.8, cut(st, 110), 20, WHITE)
    prs.save(str(out))


def C_issues(C):
    return C.get("issues") or []


# ---------------------------------------------------------------- report
def report(out, R, C, pages, info, acc):
    import docx
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    d = docx.Document()
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = Inches(0.9)
    sec.top_margin = sec.bottom_margin = Inches(0.8)
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    st.font.color.rgb = RGBColor.from_string("1F2937")
    st.paragraph_format.space_after = Pt(6)
    for lvl, size in ((1, 18), (2, 13.5)):
        h = d.styles[f"Heading {lvl}"]
        h.font.name, h.font.size, h.font.bold = "Calibri", Pt(size), True
        h.font.color.rgb = RGBColor.from_string(NAVY if lvl == 1 else "1E3A5F")
        h.paragraph_format.space_before = Pt(18 if lvl == 1 else 12)
        h.paragraph_format.space_after = Pt(6)
    dark_acc = "B45309"

    def shade(cell, hexcol):
        tcPr = cell._tc.get_or_add_tcPr()
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear")
        sh.set(qn("w:color"), "auto")
        sh.set(qn("w:fill"), hexcol)
        tcPr.append(sh)

    def table(head, rows, widths):
        t = d.add_table(rows=1, cols=len(head))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.style = "Table Grid"
        for i, h in enumerate(head):
            c = t.rows[0].cells[i]
            c.text = ""
            r = c.paragraphs[0].add_run(h)
            r.bold, r.font.size = True, Pt(10)
            r.font.color.rgb = RGBColor.from_string(WHITE)
            shade(c, NAVY)
        for n, row in enumerate(rows):
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                run = cells[i].paragraphs[0].add_run(str(v))
                run.font.size = Pt(9.5)
                if n % 2:
                    shade(cells[i], "F3F5F9")
        t.autofit = False
        for i, w in enumerate(widths):
            t.columns[i].width = Inches(w)
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
        d.add_paragraph()
        return t

    def bullets(items):
        for it in items:
            d.add_paragraph(it, style="List Bullet")

    # cover
    p = d.add_paragraph()
    r = p.add_run("WEBSITE REPORT")
    r.bold, r.font.size = True, Pt(11)
    r.font.color.rgb = RGBColor.from_string(dark_acc)
    p = d.add_paragraph()
    r = p.add_run(R["name"])
    r.bold, r.font.size = True, Pt(30)
    r.font.color.rgb = RGBColor.from_string(NAVY)
    if R["one_liner"]:
        p = d.add_paragraph()
        r = p.add_run(R["one_liner"])
        r.font.size = Pt(13)
        r.font.color.rgb = RGBColor.from_string("4B5563")
    p = d.add_paragraph()
    r = p.add_run(f"{info['start']}  ·  {len(pages)} pages read  ·  {datetime.date.today().strftime('%d %B %Y').lstrip('0')}")
    r.font.size, r.font.color.rgb = Pt(10), RGBColor.from_string("6B7280")
    snaps = shots(pages)
    if snaps:
        d.add_picture(snaps[0]["shot"], width=Inches(6.6))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    d.add_heading("Summary", 1)
    d.add_paragraph(R["summary"])
    t = table(["Pages read", "Health score", "Serious issues", "Average load"],
              [[len(pages), f"{C['score']} / 100", C["counts"]["high"], f"{C['avg_load']} s" if C["avg_load"] else "–"]], [1.65] * 4)
    if R.get("answer"):
        d.add_heading("Your question", 1)
        d.add_paragraph(R["answer"])
    if R["offering"]:
        d.add_heading("What the site offers", 1)
        table(["Offering", "Detail"], [[o["title"], o["detail"]] for o in R["offering"]], [2.0, 4.6])
    if R["audience"] or R["tone"]:
        d.add_heading("Audience and tone", 1)
        if R["audience"]:
            d.add_paragraph(R["audience"])
        if R["tone"]:
            d.add_paragraph(R["tone"])
    if R["key_facts"]:
        d.add_heading("Key facts", 1)
        bullets(R["key_facts"])
    if R["strengths"]:
        d.add_heading("What works well", 1)
        bullets(R["strengths"])
    if R["weaknesses"]:
        d.add_heading("What needs work", 1)
        bullets(R["weaknesses"])
    if R["recommendations"]:
        d.add_heading("Recommendations", 1)
        table(["#", "Action", "Why and how", "Priority"],
              [[i + 1, r["title"], r["detail"], r["priority"].capitalize()] for i, r in enumerate(R["recommendations"])], [0.35, 2.0, 3.5, 0.8])
    d.add_heading("Website health", 1)
    d.add_paragraph(f"Every page read was checked for titles, descriptions, headings, picture descriptions, speed, phone "
                    f"layout and security. {C['pages_ok']} of {len(pages)} pages had no issues. Found: {C['counts']['high']} serious, "
                    f"{C['counts']['medium']} medium and {C['counts']['low']} small issues.")
    iss = C_issues(C)[:60]
    if iss:
        table(["Level", "Page", "Issue", "Fix"],
              [[i["level"].capitalize(), cut(urlparse(i["url"]).path or "/", 40), i["what"], i["fix"]] for i in iss], [0.7, 1.5, 2.2, 2.2])
    ct = C["contacts"]
    if ct["emails"] or ct["phones"] or ct["socials"] or C["prices"]:
        d.add_heading("Contact details, social links and prices found", 1)
        if ct["emails"]:
            d.add_paragraph("Email: " + ", ".join(ct["emails"]))
        if ct["phones"]:
            d.add_paragraph("Phone: " + ", ".join(ct["phones"]))
        if ct["socials"]:
            d.add_paragraph("Social: " + ", ".join(ct["socials"]))
        if C["prices"]:
            d.add_paragraph("Prices seen: " + ", ".join(C["prices"]))
    if R["notes"]:
        d.add_heading("Page by page", 1)
        for n in R["notes"]:
            d.add_heading(cut(n["title"] or n["url"], 90), 2)
            p = d.add_paragraph()
            r = p.add_run(n["url"])
            r.font.size, r.font.color.rgb = Pt(9), RGBColor.from_string("6B7280")
            if n["summary"]:
                d.add_paragraph(n["summary"])
            bullets(n["facts"])
    d.add_heading("All pages read", 1)
    table(["Page", "Address", "Words", "Status"],
          [[cut(p["title"] or "(no title)", 50), cut(p["url"], 60), p["words"], p["status"]] for p in pages], [2.2, 3.2, 0.6, 0.6])
    # footer with the site name
    fp = sec.footer.paragraphs[0]
    fp.text = f"{R['name']} · website report by Trillion"
    fp.runs[0].font.size, fp.runs[0].font.color.rgb = Pt(8), RGBColor.from_string("9CA3AF")
    d.save(str(out))


# ---------------------------------------------------------------- spreadsheet
def sheet(out, R, C, pages, info, acc):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill, head_font = PatternFill("solid", fgColor=NAVY), Font(bold=True, color=WHITE)
    lv_fill = {"high": "FDE2E2", "medium": "FEF3C7", "low": "DBEAFE"}

    def write(ws, head, rows, widths):
        ws.append(head)
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(vertical="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = R["name"]
    ws["A1"].font = Font(bold=True, size=18, color=NAVY)
    ws["A2"] = R["one_liner"]
    ws["A2"].font = Font(italic=True, color="4B5563")
    rows = [("Website", info["start"]), ("Pages read", len(pages)), ("Health score", f"{C['score']} / 100"),
            ("Serious issues", C["counts"]["high"]), ("Medium issues", C["counts"]["medium"]), ("Small issues", C["counts"]["low"]),
            ("Words on the site", C["words"]), ("Pictures", C["images"]), ("Pictures without description", C["no_alt"]),
            ("Average load (seconds)", C["avg_load"] or ""), ("All pages on https", "Yes" if C["https"] else "No"),
            ("Emails", ", ".join(C["contacts"]["emails"])), ("Phones", ", ".join(C["contacts"]["phones"])),
            ("Social links", ", ".join(C["contacts"]["socials"])), ("Prices seen", ", ".join(C["prices"])),
            ("Read on", datetime.date.today().isoformat()), ("Summary", R["summary"])]
    for i, (k, v) in enumerate(rows, 4):
        ws.cell(i, 1, k).font = Font(bold=True)
        ws.cell(i, 2, v).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 30, 100

    issues_by = {}
    for i in C_issues(C):
        issues_by.setdefault(i["url"], []).append(i["what"])
    write(wb.create_sheet("Pages"),
          ["Title", "Address", "Status", "Words", "Main heading", "Description length", "Pictures", "No alt text",
           "Load (s)", "Links to own pages", "Prices", "Issues"],
          [[p["title"], p["url"], p["status"], p["words"], " | ".join(p["h1"]), len(p["description"]), p["images"], p["no_alt"],
            round(p["load_ms"] / 1000, 2) if p["load_ms"] else None, p.get("links_out", 0), ", ".join(p["prices"][:6]),
            "; ".join(issues_by.get(p["url"], []))] for p in pages],
          [36, 48, 8, 8, 34, 12, 10, 10, 9, 10, 24, 60])
    ws_i = wb.create_sheet("Issues")
    write(ws_i, ["Level", "Page", "Issue", "Fix"],
          [[i["level"], i["url"], i["what"], i["fix"]] for i in C_issues(C)], [9, 50, 46, 60])
    for row in ws_i.iter_rows(min_row=2):
        f = lv_fill.get(row[0].value)
        if f:
            row[0].fill = PatternFill("solid", fgColor=f)
    write(wb.create_sheet("Findings"), ["Kind", "Item", "Detail"],
          [["Offering", o["title"], o["detail"]] for o in R["offering"]] + [["Fact", f, ""] for f in R["key_facts"]]
          + [["Strength", s_, ""] for s_ in R["strengths"]] + [["Weakness", w, ""] for w in R["weaknesses"]]
          + [["Recommendation", r["title"], r["detail"]] for r in R["recommendations"]], [16, 50, 80])
    if info.get("documents"):
        write(wb.create_sheet("Documents"), ["Document linked from the site"], [[u] for u in info["documents"]], [100])
    wb.save(str(out))
