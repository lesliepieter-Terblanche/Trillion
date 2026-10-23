---
name: Web Studio pitch
description: Put together a full pitch for a Web Studio lead - audit their current site (if they have one), make the 30-second pitch video, then send it on WhatsApp with the preview link.
---

# Web Studio pitch

Use this when the user asks to pitch a business, "make the pitch video", "send the pitch", or to prepare a lead.

## Steps

1. **Find the site.** The business must already have a Web Studio site (its site number). If not, build one first.
2. **If the business has a website already**, audit it with the **Website audit (Lighthouse)** connector
   (`run_audit`, device "mobile"), and audit the new Web Studio site's preview link the same way. Keep only the scores
   Lighthouse actually returned.
3. **Make the video** with the `pitch_video` tool: `site_id`, plus `scores` when you have both audits, for example
   `{"Performance": [38, 99], "Mobile": [51, 96], "SEO": [60, 91]}` (old score first, new score second).
   Without an existing site, leave scores out: the video shows what is included instead.
4. The video renders in the background (2-4 minutes) and appears on the Web Studio page. The WhatsApp pitch for that
   lead then sends the video together with the preview link.
5. Tell the user in one line when it is ready, and ask before sending anything to the business.

## Rules

- Never invent scores or features. Only use Lighthouse's numbers and the features the site really has.
- The voice-over uses the free Kokoro voice when it is switched on; otherwise the video is silent, which is fine.
- Send nothing to a business without the user's go-ahead.
