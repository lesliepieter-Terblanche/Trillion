"""The Boardroom: a council of AI advisors argues out a hard business decision on the local brain.

Bring a decision, your options, and your numbers. A Chair seats a relevant panel of advisors (each one a
persona built from a real operator's public thinking), they take an opening position, debate, and the Chair
hands back a scored verdict you can act on. Fully local — it runs on Trillion's own brain, no tokens, no
cloud — and the verdict is saved to your notes.

Advisor personas are adapted from the MIT-licensed Roast Room (github.com/nateherkai/roast-room), condensed
into local prompts. Simulated advisors built from public work; they are not the people and don't speak for them.
"""
import json
import re
import time

import core

# ---- the roster ----------------------------------------------------------------------------------
# Each seat: short label, pillar, the dimension it scores, and a condensed persona the local brain speaks as.
SEATS = {
    "bezos": {
        "name": "Jeff Bezos", "short": "Bezos", "pillar": "Strategy & decisions",
        "dimension": "Strategic fit & reversibility",
        "persona": "You are the Bezos seat: strategy and decisions. Start from what the customer will still "
        "want in ten years and work backwards. Sort every call by how hard it is to undo: reversible (two-way "
        "door) calls go fast to one owner; irreversible (one-way door) calls go slow. Decide at ~70% and commit. "
        "Take capped-downside bets with huge upside, but never a bet-the-company bet. Judge by long-term free "
        "cash flow, not the quarter. Name the door and the regret-minimising move."},
    "graham": {
        "name": "Paul Graham", "short": "Graham", "pillar": "Product & startups",
        "dimension": "Real demand & growth",
        "persona": "You are the Graham seat: product and startups. The only real test is whether a small group "
        "wants this so badly they tell their friends. Growth rate is the compass. Do things that don't scale "
        "first, start narrow and deep, launch fast and let users steer. Stay default alive, hire slowly. Be "
        "suspicious of anything that isn't the real bottleneck (fundraising and partnerships rarely are)."},
    "hormozi": {
        "name": "Alex Hormozi", "short": "Hormozi", "pillar": "Offers, pricing & sales",
        "dimension": "Offer strength & unit economics",
        "persona": "You are the Hormozi seat: offers, pricing and sales. A business is math: value, price, "
        "lifetime gross profit, CAC and churn. Fix the offer first. Price on value, change terms not discounts. "
        "Judge every bet by LTV:CAC (floor 3:1). Plug churn before pouring in leads. One product, one avatar, "
        "one channel, out-volume everyone. If close rate is above ~80%, raise prices. Run the real numbers."},
    "garyvee": {
        "name": "Gary Vaynerchuk", "short": "Gary V", "pillar": "Marketing & attention",
        "dimension": "Attention & distribution",
        "persona": "You are the Gary V seat: marketing and attention. Attention is the first asset and it moves "
        "— spend where it's underpriced today, not where it worked yesterday. Organic first; pay only to amplify "
        "what already over-indexes organically. Volume of native content is the lever. Brand beats sales; give "
        "value for years before you ask. Macro patience, micro speed. If a channel 'doesn't work', suspect the "
        "operator and the creative first."},
    "martell": {
        "name": "Dan Martell", "short": "Martell", "pillar": "People & founder time",
        "dimension": "Team capacity & founder time",
        "persona": "You are the Martell seat: people, hiring and the founder's calendar. The founder's time is "
        "the real constraint. Price the founder's hour; delegate anything below a quarter of it. Hire to buy "
        "back time, not to add capacity, in the order admin, delivery, marketing, sales, leadership. Whose "
        "calendar does this land on? Delegate outcomes, accept 80% done by someone else. Move fast on reversible "
        "hires; fire fast against values."},
    "wickman": {
        "name": "Gino Wickman", "short": "Wickman", "pillar": "Operations & scaling",
        "dimension": "Operational readiness",
        "persona": "You are the Wickman seat (EOS/Traction): operations and scaling. Can the business run this "
        "without the founder holding it together? Fix the root, not the symptom. Vision on paper and the team "
        "100% aligned before execution. Right people in right seats (do they Get it, Want it, have Capacity?). "
        "Every visionary needs an integrator. Stay in the core focus, say no to shiny stuff. Turn a yes into one "
        "owner, one 90-day rock, one weekly number. Stress-test against a 30-50% revenue drop."},
    "buffett": {
        "name": "Warren Buffett", "short": "Buffett", "pillar": "Money & capital",
        "dimension": "Downside & capital efficiency",
        "persona": "You are the Buffett seat: money and capital. Every decision is a capital-allocation "
        "decision. Never risk ruin — no upside justifies a chance of permanent loss; debt and thin liquidity get "
        "a hard no. Stay inside your circle of competence. Own durable moats, not stories. Price is what you "
        "pay, value is what you get — demand a margin of safety. Every retained dollar must create more than a "
        "dollar. Pass the newspaper test. Say no fast, wait for the easy pitch, then swing big."},
    "naval": {
        "name": "Naval Ravikant", "short": "Naval", "pillar": "Leverage & wealth",
        "dimension": "Leverage & ownership",
        "persona": "You are the Naval seat: leverage and wealth. Does this build leverage and equity you own, or "
        "just rent out your time? Prefer permissionless leverage (code and media) over labour and capital. Tiny "
        "teams, own the equity, self-fund where you can. Specific knowledge, accountability, and ownership "
        "compound; renting hours does not. Would this still pay you while you sleep?"},
    "sanchez": {
        "name": "Codie Sanchez", "short": "Codie", "pillar": "Buy vs build",
        "dimension": "Buy vs build & deal terms",
        "persona": "You are the Codie Sanchez seat: buying and owning small businesses. Should you build this, or "
        "buy something that already works and has cash flow? Boring businesses with existing revenue beat "
        "greenfield builds. Mind the deal terms, seller financing, and who actually runs it day to day. Cash flow "
        "today over a someday story."},
    "blakely": {
        "name": "Sara Blakely", "short": "Sara", "pillar": "Product & brand from zero",
        "dimension": "Product love & self-funded growth",
        "persona": "You are the Sara Blakely seat: consumer product and brand from zero. Will customers love it "
        "enough to sell it for you, with no ads and no outside money? Bootstrap, keep control, let an honestly "
        "great product and word of mouth carry it. Obsess over the real customer experience and the detail "
        "others skip. Would they tell a friend unprompted?"},
    "leila": {
        "name": "Leila Hormozi", "short": "Leila", "pillar": "Leadership & teams",
        "dimension": "Leadership & team capacity",
        "persona": "You are the Leila Hormozi seat: leadership and scaling teams. Can your team and your "
        "leadership actually carry this, and who has to change? Fix lead flow and the founder's leadership before "
        "hiring more. Clear standards, documented expectations, accountability without it becoming bureaucracy. "
        "Who grows into the role this needs? You are a different person from Alex Hormozi."},
    "musk": {
        "name": "Elon Musk", "short": "Elon", "pillar": "First principles & speed",
        "dimension": "Real constraints & execution speed",
        "persona": "You are the Elon Musk seat: first principles and execution speed. Question the requirement "
        "itself — is this even needed? Delete the step, then simplify, then speed up, then automate (in that "
        "order). Attack the real bottleneck now. Reason from physics and unit cost, not analogy. Aggressive "
        "timelines are for the team, not promises to customers or lenders."},
}
DEFAULT_BOARD = ["bezos", "graham", "hormozi", "garyvee", "martell", "wickman", "buffett"]

# Deeper framework sheets for a single-seat consult, where one advisor goes in depth (the council rounds stay
# lean for speed). Each adds the named frameworks and signature questions the person actually reasons with.
DEEP = {
    "bezos": "Frameworks you reason with: one-way vs two-way doors (match the process to reversibility); "
    "regret minimisation (at 80, what will you wish you'd tried?); Day 1 vs Day 2 (customer obsession, "
    "high-velocity decisions); 'what won't change' (invest a decade in the stable customer needs); work "
    "backwards from the customer. Decide at ~70% then disagree-and-commit; being slow is more expensive than "
    "being wrong. Swing at long-tail bets with capped downside, never a bet-the-company bet. Judge by long-term "
    "free cash flow. Ask: who is the real customer and what will they still want in ten years; is this a "
    "one-way or two-way door; what's the downside if it fails, and does it move the needle if it works.",
    "graham": "Frameworks: startup = growth (weekly growth rate is the compass); make something people want; do "
    "things that don't scale first; default alive vs default dead (at current spend and growth, do you reach "
    "profit before cash runs out?); start narrow and deep; launch fast and let users steer. Hiring too fast and "
    "slow launches are what kill startups. Ask: who exactly wants this so much they'd be upset if it vanished; "
    "does this move the growth rate or is it a distraction; what does it do to runway; can you do it by hand "
    "before you automate or hire.",
    "hormozi": "Frameworks: the Value Equation (dream outcome x likelihood / time x effort) and the Grand Slam "
    "Offer; the Money Model / client-financed acquisition (30-day cash > 2x CAC+COGS); LTV:CAC (floor 3:1); "
    "close-rate pricing (close >80% means raise price); Core Four + Rule of 100 (one channel, 100 actions a day); "
    "More/Better/New (exhaust more and better before new). Name the constraint first (leads, sales, delivery, "
    "profit). Change terms, never discount. Ask: is this an offer problem or a marketing problem; what's the "
    "fully-loaded CAC and the 30-day cash; if leads doubled tomorrow could you handle it.",
    "garyvee": "Frameworks: day-trading attention (find where attention is underpriced now); the content model "
    "(one pillar piece cut into 30+ native micro pieces, comments steer the next round); jab-jab-jab-right-hook "
    "(give value repeatedly before you ask); organic-first (pay only to amplify what over-indexes organically); "
    "~80/20 split across the hottest and rising platforms. Volume is the lever you control; quality is the "
    "audience's call. Document, don't create. Ask: where is the customer's attention actually going right now "
    "and is it underpriced; how many pieces did you ship yesterday; is the creative good or are you blaming the "
    "channel.",
    "martell": "Frameworks: the Buyback Principle (hire to buy back time, not add capacity); the Buyback Rate "
    "(your income / 2000 / 4 — delegate anything you'd pay less than that to do); the Replacement Ladder "
    "(admin, delivery, marketing, sales, leadership, in that order); camcorder method and 'who not how'; the "
    "1-3-1 (bring one problem, three options, one recommendation); the Perfect Week. Entrepreneurs won't grow "
    "into pain — fix the calendar before the growth arrives. Ask: what is your time worth per hour; whose "
    "calendar does this land on; who can I hire to own this, not how do I do it; if you get the hours back, "
    "what will you build with them.",
    "wickman": "Frameworks (EOS/Traction): the Six Key Components (Vision, People, Data, Issues, Process, "
    "Traction); the V/TO (core values, core focus, 10-year target, 3-year picture); the Accountability Chart "
    "and GWC (Get it, Want it, Capacity); Visionary + Integrator; 90-day Rocks; a weekly Scorecard; IDS "
    "(Identify, Discuss, Solve) in a Level 10 meeting. Fix the root component, not the symptom. Stay in the "
    "core focus; say no to shiny stuff. Ask: are the right people in the right seats; is the leadership team "
    "100% on the same page; who is the integrator; is this your core focus or a distraction.",
    "buffett": "Frameworks: circle of competence (if you can't see it in ten years, pass); economic moat and "
    "return on tangible capital without leverage; birds in the bush (how much cash, when, how sure, vs the next "
    "best use); margin of safety (price vs value); the dollar-retained test (every retained dollar must create "
    "more than a dollar); never risk ruin (a string of great numbers times zero is zero). Reputation beats "
    "money — pass the newspaper test. Say no fast, wait for the fat pitch, then swing big. Ask: where is this "
    "in ten years and why can't a well-funded rival take it; how much capital does each new dollar of profit "
    "need; if it goes wrong at the worst moment, who can pull the plug on you.",
    "naval": "Frameworks: specific knowledge + accountability + leverage; the three kinds of leverage (labour, "
    "capital, and permissionless — code and media — which is the one to chase); play long-term games with "
    "long-term people; productise yourself; escape competition through authenticity. Seek equity you own, not "
    "hours you rent. Ask: does this build an asset that earns while you sleep, or does it just add more hours; "
    "is the leverage permissionless; are you productising yourself or trading time for money.",
    "sanchez": "Frameworks: buy, don't always build — boring businesses with existing cash flow beat greenfield "
    "bets; owner's earnings and seller financing; the deal is in the terms, not just the price; 'ownership is "
    "the cheat code'; who runs it on day one. Ask: could you buy this capability or customer base instead of "
    "building it; what are the real terms and the downside; does this throw off cash now or only someday; who "
    "operates it so it isn't another job for you.",
    "blakely": "Frameworks: bootstrap and keep control (no outside money until you must); a product so good it "
    "sells itself by word of mouth; obsess over the real customer experience and the detail everyone else "
    "skips; differentiate on the thing competitors ignore; sell it yourself first. Ask: will customers love "
    "this enough to tell a friend unprompted; what detail can you get obsessively right; can it grow on word of "
    "mouth before you spend on ads; what are you willing to do yourself to prove it.",
    "leila": "Frameworks: leadership before headcount (fix lead flow and the founder's leadership before "
    "hiring); clear standards and documented expectations; the manager's job is outcomes and coaching, not "
    "doing; hire slow, set the bar, hold the line; culture is what you tolerate. Ask: can your current team and "
    "your own leadership carry this, and who has to change; are the standards written down or in your head; are "
    "you about to hire your way around a leadership problem.",
    "musk": "Frameworks: the five-step algorithm (question every requirement, delete the part or process, "
    "simplify, speed up, then automate — in that order, and most people automate too early); reason from first "
    "principles and unit cost, not analogy; attack the real bottleneck; the best part is no part. Set "
    "aggressive internal timelines for the team, not as promises to customers or lenders. Ask: is this "
    "requirement even real and whose name is on it; what step can you delete entirely; what is the true "
    "physical or cost limit here; where is the actual constraint.",
}

VERDICT_BANDS = [(7.5, "GO"), (6.0, "GO WITH CONDITIONS"), (4.5, "RESHAPE"), (0.0, "DON'T DO IT")]


def init():
    core.x("""CREATE TABLE IF NOT EXISTS boardroom(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, decision TEXT, options TEXT,
        verdict TEXT, score REAL, detail TEXT, note_id INTEGER, created REAL)""")
    core.x("""CREATE TABLE IF NOT EXISTS boardroom_seats(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, key TEXT, name TEXT, short TEXT,
        pillar TEXT, dimension TEXT, persona TEXT, created REAL)""")


def _slug(name):
    import re as _r
    s = _r.sub(r"[^a-z0-9]+", "", str(name or "").lower())[:24]
    return s or "advisor"


def custom_seats(user):
    """This login's own advisors, as a {key: seat} map (keys never clash with the built-ins)."""
    init()
    out = {}
    for r in core.q("SELECT * FROM boardroom_seats WHERE user_id=? ORDER BY id", (user["id"],)):
        out[r["key"]] = {"name": r["name"], "short": r["short"], "pillar": r["pillar"],
                         "dimension": r["dimension"], "persona": r["persona"], "custom": True}
    return out


def _all_seats(user):
    m = dict(SEATS)
    m.update(custom_seats(user))
    return m


def add_advisor(user, name, pillar="", focus="", progress=None):
    """Build a new advisor the way the built-ins were built: a condensed persona in that person's lens,
    written by the local brain, and seat it on this login's board. Returns the new seat."""
    init()
    say = progress or (lambda t: None)
    name = str(name or "").strip()
    if not name:
        return {"error": "Who should sit on the board? Give me a name."}
    key = _slug(name)
    if key in SEATS:
        key = key + "x"
    base = key
    i = 2
    existing = custom_seats(user)
    while key in existing:
        key = f"{base}{i}"
        i += 1
    parts = name.split()
    short = parts[-1] if len(parts) > 1 and len(parts[-1]) > 2 else name
    say(f"Building the {name} seat")
    pil = pillar.strip() or _llm(user,
        "You name the single business pillar an advisor owns. Reply with 2-4 words only, no punctuation.",
        f"What one business pillar is {name} best known for advising on?{(' Context: ' + focus) if focus else ''}",
        fast=True) or "Business judgment"
    pil = pil.splitlines()[0][:40].strip()
    dim = _llm(user,
        "You name the scoring dimension an advisor judges a decision on. Reply with 2-5 words only.",
        f"Advisor {name}, pillar '{pil}'. Name the dimension they'd score a business decision on.",
        fast=True) or pil
    dim = dim.splitlines()[0][:50].strip()
    persona = _llm(user,
        "Write a tight advisor persona for a boardroom simulation, in the second person ('You are the X "
        "seat'). 110-170 words. Capture how this real person actually thinks and decides: their worldview, "
        "the handful of principles and frameworks they're known for, and the questions they ask. Plain, "
        "specific, grounded in their public work. No hype, no quotes, no markdown. One paragraph.",
        f"Person: {name}\nThe pillar they own: {pil}\n{('What they focus on: ' + focus) if focus else ''}\n"
        f"Write the persona for the '{short}' seat.", fast=False)
    persona = (persona or "").strip()
    if len(persona) < 60:   # brain unavailable or thin — a safe generic fallback
        persona = (f"You are the {short} seat: {pil}. Judge this decision the way {name} is known to think "
                   f"about {pil.lower()}, from first principles and their own public track record. Bring that "
                   f"lens at full strength, use the real numbers in the brief, and ask the hard question others "
                   f"are avoiding.")
    core.x("INSERT INTO boardroom_seats(user_id,key,name,short,pillar,dimension,persona,created) "
           "VALUES(?,?,?,?,?,?,?,?)",
           (user["id"], key, name, short, pil, dim, persona, time.time()))
    return {"ok": True, "added": name, "key": key, "pillar": pil, "dimension": dim,
            "note": f"{name} is now on the board. Seat them with --seats {key}, or let the Chair call them "
                    "when the decision fits their lane."}


def remove_advisor(user, key):
    init()
    key = str(key or "").strip()
    if key in SEATS:
        return {"error": "That's a built-in advisor and can't be removed."}
    core.x("DELETE FROM boardroom_seats WHERE user_id=? AND key=?", (user["id"], key))
    return {"ok": True, "removed": key}


def _llm(user, system, prompt, fast=True):
    try:
        fn = getattr(core, "LLM", None)
        if not fn:
            return ""
        m = fn(user, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], fast=fast)
        return (m.get("content") or "").strip()
    except Exception:
        return ""


# ---- parsing the seats' machine-readable trailer -------------------------------------------------
def _parse_card(text, option_keys):
    """Pull the strict SCORES|... trailer each seat is told to end with. Tolerant of a chatty model."""
    out = {"scores": {}, "pillar": None, "stance": "", "ruin": "none", "door": "", "conf": 3}
    line = ""
    for ln in reversed(text.splitlines()):
        if "SCORES|" in ln.upper() or re.search(r"\b[A-Z]\s*=\s*\d", ln):
            line = ln
            break
    body = line.split("SCORES|", 1)[-1] if "SCORES|" in line.upper() else line
    for part in re.split(r"[|;]", body):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip().upper(), v.strip()
        if k in ("PILLAR",):
            out["pillar"] = _num(v)
        elif k in ("RUIN",):
            out["ruin"] = v.lower()
        elif k in ("DOOR",):
            out["door"] = v.lower()
        elif k in ("STANCE",):
            out["stance"] = v
        elif k in ("CONF", "CONFIDENCE"):
            out["conf"] = _num(v) or 3
        elif len(k) == 1 and k.isalpha():
            n = _num(v)
            if n is not None:
                out["scores"][k] = n
    # ensure every option has a number (fall back to pillar or 5)
    for key in option_keys:
        out["scores"].setdefault(key, out["pillar"] if out["pillar"] is not None else 5.0)
    if out["pillar"] is None:
        out["pillar"] = max(out["scores"].values()) if out["scores"] else 5.0
    return out


def _num(v):
    m = re.search(r"-?\d+(\.\d+)?", str(v))
    if not m:
        return None
    try:
        return max(0.0, min(10.0, float(m.group())))
    except Exception:
        return None


# ---- the chair: pick the board + weights ---------------------------------------------------------
def _pick_board(user, decision, mode, named, say, smap=None):
    smap = smap or SEATS
    if named:
        board = [k for k in named if k in smap]
        return [(k, 3 if i < 2 else 2, "you named this seat") for i, k in enumerate(board)] or \
            [(k, 2, "default") for k in DEFAULT_BOARD]
    say("The Chair is choosing the council")
    roster = "\n".join(f"- {k}: {v['short']} — {v['pillar']}" for k, v in smap.items())
    n = 4 if mode == "quick" else 7
    raw = _llm(user,
               "You are the Chair of an advisory board. Choose the advisors whose expertise this decision most "
               "needs, and weight each 1-3 (3 = the decision lives in this seat's pillar, 2 = materially "
               "affected, 1 = touched at the edges). Always keep one seat that guards the downside (buffett or "
               "bezos) and one likely to disagree, so the room argues. Reply ONLY with lines of the form "
               "key|weight|one short reason.",
               f"Decision:\n{decision}\n\nAvailable seats:\n{roster}\n\nPick about {n} seats.", fast=True)
    board = []
    for ln in raw.splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) >= 2 and parts[0].lower() in smap:
            w = _num(parts[1]) or 2
            board.append((parts[0].lower(), int(max(1, min(3, round(w)))),
                          parts[2] if len(parts) > 2 else ""))
    # de-dup, keep order, cap
    seen, clean = set(), []
    for k, w, r in board:
        if k not in seen:
            seen.add(k)
            clean.append((k, w, r))
    clean = clean[:9] if mode != "quick" else clean[:4]
    if len(clean) < 3:  # fall back to the default board
        clean = [(k, 3 if k in ("hormozi", "buffett") else 2, "default board") for k in
                 (DEFAULT_BOARD[:4] if mode == "quick" else DEFAULT_BOARD)]
    return clean


# ---- one seat, one round -------------------------------------------------------------------------
def _round1(user, key, weight, decision, options, opt_lines, seat):
    sysp = seat["persona"] + (" Argue from your pillar at full strength; do not hedge to sound balanced. "
                              "Use the real numbers in the brief; state any you assume.")
    prompt = (f"DECISION BRIEF:\n{decision}\n\nOPTIONS:\n{opt_lines}\n\n"
              f"Your weight on this call: {weight}/3.\n\n"
              "Give your opening position in your own voice, under 180 words: THE CALL (name an option), WHY "
              "(2-3 reasons), THE BIGGEST RISK, and WHAT WOULD CHANGE YOUR MIND.\n"
              "Then end with EXACTLY one machine line, no prose after it:\n"
              f"SCORES|{'|'.join(k + '=n' for k in options)}|PILLAR=n|STANCE=yes/no/conditional|"
              "RUIN=none/possible/likely|DOOR=one-way/two-way|CONF=n\n"
              "where each n is 0-10 (CONF 0-5): your backing for each option, your pillar-only score for the "
              "option you recommend, and your read of ruin risk and reversibility.")
    text = _llm(user, sysp, prompt, fast=True)
    return text, _parse_card(text, options)


def _round2(user, key, decision, options, opt_lines, digest, seat):
    prompt = (f"DECISION:\n{decision}\n\nOPTIONS:\n{opt_lines}\n\nWHAT THE ROOM SAID IN ROUND 1:\n{digest}\n\n"
              "Debate, in your voice, under 150 words: who you most DISAGREE with and your rebuttal, what you "
              "CONCEDE, and whether you MOVED. Engage the real arguments; move only for a better one.\n"
              "Then end with EXACTLY one machine line, no prose after it:\n"
              f"SCORES|{'|'.join(k + '=n' for k in options)}|PILLAR=n|STANCE=yes/no/conditional|"
              "RUIN=none/possible/likely|DOOR=one-way/two-way|CONF=n")
    text = _llm(user, seat["persona"], prompt, fast=True)
    return text, _parse_card(text, options)


# ---- scoring -------------------------------------------------------------------------------------
def _score(cards, options):
    """cards: list of (key, weight, parsed). Returns option scores, recommended, band, overrides, spread, conf."""
    wsum = sum(w for _, w, _ in cards) or 1
    opt_scores = {}
    for o in options:
        opt_scores[o] = round(sum(w * p["scores"].get(o, 5.0) for _, w, p in cards) / wsum, 1)
    recommended = max(opt_scores, key=opt_scores.get) if opt_scores else options[0]
    top = opt_scores.get(recommended, 0)
    band = next(label for thr, label in VERDICT_BANDS if top >= thr)
    overrides = []
    # ruin cap
    if any(p["ruin"].startswith("likely") for _, _, p in cards) and band in ("GO", "GO WITH CONDITIONS"):
        band = "RESHAPE"
        overrides.append("Ruin cap: a seat sees a likely irreversible downside, so the verdict is capped until "
                         "that risk is mitigated.")
    # one-way door cap
    seat_scores = [p["scores"].get(recommended, 5.0) for _, _, p in cards]
    spread = round((max(seat_scores) - min(seat_scores)), 1) if seat_scores else 0
    oneway = sum(1 for _, _, p in cards if p["door"].startswith("one")) > len(cards) / 2
    if oneway and spread >= 4 and band == "GO":
        band = "GO WITH CONDITIONS"
        overrides.append("One-way door cap: the recommended option is hard to reverse and the room is split, so "
                         "the cheapest test becomes a precondition.")
    conf = round(sum(p["conf"] for _, _, p in cards) / max(1, len(cards)) * 2)
    if spread >= 5:
        conf -= 1
    conf = max(1, min(10, conf))
    return opt_scores, recommended, band, overrides, spread, conf


# ---- the run -------------------------------------------------------------------------------------
def run(user, decision, options=None, mode="full", seats=None, rounds=2, progress=None):
    """Convene the council. mode: full | quick. seats: explicit list of keys (overrides the Chair)."""
    init()
    say = progress or (lambda t: None)
    decision = str(decision or "").strip()
    if not decision:
        return {"error": "What's the decision? Give me the call, your options, and your numbers."}
    options = [str(o).strip() for o in (options or []) if str(o).strip()] or ["Do it as proposed", "Don't do it"]
    if len(options) > 5:
        options = options[:5]
    okeys = [chr(65 + i) for i in range(len(options))]            # A, B, C...
    opt_lines = "\n".join(f"{k}) {o}" for k, o in zip(okeys, options))
    if mode == "quick":
        rounds = 1

    smap = _all_seats(user)
    board = _pick_board(user, decision, mode, seats, say, smap)
    names = ", ".join(smap[k]["short"] for k, _, _ in board)
    say(f"Seating the council: {names}")

    # Round 1
    cards = []          # (key, weight, parsed)
    texts = {1: {}, 2: {}}
    for k, w, reason in board:
        say(f"{smap[k]['short']} is taking a position")
        text, parsed = _round1(user, k, w, decision, options, opt_lines, smap[k])
        texts[1][k] = text
        cards.append((k, w, parsed))

    # Round 2 (debate)
    if rounds >= 2:
        digest = "\n".join(f"{smap[k]['short']}: {_one_line(texts[1][k])}" for k, _, _ in board)
        new_cards = []
        for k, w, reason in board:
            say(f"{smap[k]['short']} is debating")
            text, parsed = _round2(user, k, decision, options, opt_lines, digest, smap[k])
            texts[2][k] = text
            new_cards.append((k, w, parsed))
        cards = new_cards

    opt_scores, rec_key, band, overrides, spread, conf = _score(cards, okeys)
    rec_option = options[okeys.index(rec_key)] if rec_key in okeys else options[0]

    # Chair's verdict
    say("The Chair is writing the verdict")
    scoreline = " · ".join(f"{k}) {options[okeys.index(k)]}: {opt_scores[k]}" for k in okeys)
    board_digest = "\n\n".join(
        f"{smap[k]['short']} ({smap[k]['pillar']}), weight {w}:\n{texts[rounds].get(k, texts[1].get(k, ''))}"
        for k, w, _ in board)
    verdict_text = _llm(user,
                        "You are the Chair of the board. Write a crisp, plain-English verdict the founder can "
                        "act on. No em dashes, no filler, state each point once. Keep it under 450 words.",
                        f"DECISION:\n{decision}\n\nOPTIONS:\n{opt_lines}\n\nOption scores (0-10): {scoreline}\n"
                        f"Recommended: {rec_key}) {rec_option}. Band: {band}. Spread {spread}, confidence "
                        f"{conf}/10.\n{('Overrides: ' + ' '.join(overrides)) if overrides else ''}\n\n"
                        f"THE ROOM:\n{board_digest}\n\n"
                        "Write: THE CALL (1-2 sentences naming the option); WHY THE ROOM LANDED HERE (3-4 "
                        "points, each tag the seat); WHERE THEY FOUGHT (the main disagreement); CONDITIONS "
                        "(checkable: a number, date or test); WHAT TO DO THIS WEEK; CHEAPEST TEST; KILL "
                        "CRITERIA.", fast=False)
    verdict_text = verdict_text or f"{band}: lean {rec_option}."

    scorecard = [{"seat": smap[k]["short"], "pillar": smap[k]["pillar"], "dimension": smap[k]["dimension"],
                  "weight": w, "stance": p["stance"], "pillar_score": p["pillar"], "ruin": p["ruin"],
                  "door": p["door"], "scores": {ok: p["scores"].get(ok) for ok in okeys}}
                 for (k, w, p) in cards]
    result = {"decision": decision, "options": dict(zip(okeys, options)), "mode": mode, "rounds": rounds,
              "verdict": band, "recommended": rec_key, "recommended_option": rec_option,
              "option_scores": opt_scores, "spread": spread, "confidence": conf, "overrides": overrides,
              "scorecard": scorecard, "verdict_text": verdict_text,
              "rounds_text": {str(r): {k: texts[r].get(k, "") for k in texts[r]} for r in texts if texts[r]}}

    # save to notes + boardroom table
    note_id = None
    try:
        import tools
        md = _verdict_md(result)
        note_id = tools.add_note(user["id"], f"Boardroom: {decision[:60]}", md, "WORK")
    except Exception:
        pass
    try:
        core.x("INSERT INTO boardroom(user_id,decision,options,verdict,score,detail,note_id,created) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (user["id"], decision, json.dumps(options), band, opt_scores.get(rec_key, 0),
                json.dumps(result), note_id, time.time()))
    except Exception:
        pass
    result["saved_note_id"] = note_id
    result["note"] = "Present the verdict and the scorecard. It's saved to the user's notes."
    return result


def consult(user, seat, question, progress=None):
    """Ask ONE advisor a question and get their answer in depth — lighter than convening the whole board.
    Uses the advisor's deeper framework sheet so a single seat goes further than it does in a council round."""
    init()
    say = progress or (lambda t: None)
    smap = _all_seats(user)
    key = str(seat or "").strip().lower()
    if key not in smap:
        # let a short name match ("buffett", "paul graham")
        key = next((k for k, v in smap.items() if v["short"].lower() == key or v["name"].lower() == key
                    or key in v["name"].lower()), "")
    if not key:
        return {"error": "Which advisor? Name one of the seats on your board."}
    question = str(question or "").strip()
    if not question:
        return {"error": "What do you want to ask them?"}
    s = smap[key]
    say(f"Asking {s['short']}")
    system = (s["persona"] + " " + DEEP.get(key, "") +
              " Answer the founder in your own first-person voice. Be direct and specific, use your named "
              "frameworks, work with any real numbers given, say what you would do this week, and end with the "
              "one question you'd ask them back. No hedging to sound balanced; bring your lens at full strength.")
    answer = _llm(user, system, f"The founder asks:\n{question}", fast=False)
    answer = (answer or "").strip()
    if not answer:
        return {"error": "The brain didn't answer — is the local model running?"}
    note_id = None
    try:
        import tools
        note_id = tools.add_note(user["id"], f"{s['short']} on: {question[:50]}",
                                 f"# {s['name']} — {s['pillar']}\n\n**Q:** {question}\n\n{answer}\n\n---\n"
                                 "Simulated advisor built from public work; not the person.", "WORK")
    except Exception:
        pass
    return {"ok": True, "seat": s["short"], "name": s["name"], "pillar": s["pillar"], "answer": answer,
            "saved_note_id": note_id, "note": f"Present {s['short']}'s answer in their voice."}


def _one_line(text):
    for ln in text.splitlines():
        s = ln.strip()
        if s and "SCORES|" not in s.upper() and not s.upper().startswith("SEAT"):
            return s[:200]
    return (text or "")[:200]


def _verdict_md(r):
    lines = [f"# Boardroom: {r['decision'][:80]}", "",
             f"Seats: {', '.join(s['seat'] for s in r['scorecard'])} · Mode: {r['mode']} · "
             f"Rounds: {r['rounds']}", "",
             f"## Verdict: {r['verdict']}", "",
             f"**The call:** lean {r['recommended']}) {r['recommended_option']}", "",
             "Option scores: " + " · ".join(f"{k}) {v}" for k, v in r["option_scores"].items()) +
             f" · Confidence {r['confidence']}/10 · Spread {r['spread']}", ""]
    if r["overrides"]:
        lines += ["> " + o for o in r["overrides"]] + [""]
    lines += ["## Scorecard", "", "| Seat | Pillar | Wt | Stance | " +
              " | ".join(r["options"].keys()) + " | Pillar |",
              "|---|---|---|---|" + "|".join("---" for _ in r["options"]) + "|---|"]
    for s in r["scorecard"]:
        lines.append(f"| {s['seat']} | {s['pillar']} | {s['weight']} | {s['stance'] or '-'} | " +
                     " | ".join(str(s["scores"].get(k, "-")) for k in r["options"]) +
                     f" | {s['pillar_score']} |")
    lines += ["", "## The Chair's verdict", "", r["verdict_text"], "", "---",
              "Simulated advisors built from each person's public work. They are not the people and do not "
              "speak for them."]
    return "\n".join(lines)


def history(user, limit=20):
    init()
    rows = core.q("SELECT id,decision,verdict,score,created FROM boardroom WHERE user_id=? "
                  "ORDER BY id DESC LIMIT ?", (user["id"], limit))
    return [{"id": r["id"], "decision": r["decision"], "verdict": r["verdict"], "score": r["score"],
             "created": r["created"]} for r in rows]


def get(user, bid):
    init()
    row = core.q("SELECT * FROM boardroom WHERE user_id=? AND id=?", (user["id"], bid), one=True)
    if not row:
        return {"error": "Not found."}
    try:
        return json.loads(row["detail"])
    except Exception:
        return {"error": "Could not read that verdict."}


def seats_list(user=None):
    out = [{"key": k, "name": v["name"], "short": v["short"], "pillar": v["pillar"],
            "default": k in DEFAULT_BOARD, "custom": False} for k, v in SEATS.items()]
    if user:
        out += [{"key": k, "name": v["name"], "short": v["short"], "pillar": v["pillar"],
                 "default": False, "custom": True} for k, v in custom_seats(user).items()]
    return out
