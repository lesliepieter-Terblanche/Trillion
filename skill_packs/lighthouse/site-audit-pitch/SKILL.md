---
name: Website audit for pitches
description: Audit any website with Lighthouse (speed, mobile, SEO, accessibility) and turn the scores into plain-language selling points for a Web Studio pitch, or check a site Trillion built before it is sent.
---

# Website audit for pitches

Use this when the user asks how good a website is, wants a prospect's site audited, wants a pitch for a business
that HAS a website (just a weak one), or before sending a site Trillion built. The connector is
**Website audit (Lighthouse)**.

## How to do it

1. Call `connector_call` with connector "Website audit (Lighthouse)", tool `run_audit`, arguments
   `{"url": "<full https:// address>", "device": "mobile"}`. Most customers find a small business on their phone, so
   audit **mobile first**. An audit takes 20-60 seconds.
2. For a pitch, also call `get_core_web_vitals` with the same URL if load speed looks poor.
3. Report the four scores (Performance, Accessibility, Best practices, SEO, each out of 100) and the three worst
   problems in plain words a business owner understands, for example:
   - "Your site takes 9 seconds to show on a phone. Most visitors leave after 3."
   - "Google can't read your page title, so you rank lower when people search for you."
   - "The text is too small to read on a phone without zooming."
4. **For a Web Studio pitch:** put the prospect's scores next to the scores of the site Trillion built for them
   (audit that one too, from its preview link). Lead with the difference in speed and mobile.

## Rules

- Audit only public website addresses. Never audit login pages, admin pages or anything behind a password.
- Never invent or round up scores. Quote what Lighthouse returned.
- Scores move a little between runs; if a result looks odd, run it once more before using it in a pitch.
- If the audit fails (no Chrome, site down, site blocks robots), say so in one line and carry on without it.
