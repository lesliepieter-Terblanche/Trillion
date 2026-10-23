---
name: Design from real sites
description: Before building or redesigning any website, landing page, dashboard or app screen, look up how real, professionally designed sites do it with the Inspo connector, so the result looks agency-made and not AI-made.
---

# Design from real sites

Use this whenever you are about to design something people will look at: a website for a business (Web Studio leads
included), a landing page, a section of a site, a dashboard layout, a flyer-style page, or a redesign of any of these.
Do the lookup FIRST, before writing any HTML or picking colours. The connector is **Design inspiration (Inspo)**.

## How to do it

1. **Start with one call: `recommend`.** Call `connector_call` with connector "Design inspiration (Inspo)", tool
   `recommend`, and a short brief in plain words, for example
   `{"brief": "premium one-page site for a plumber in Johannesburg, trustworthy, phone number prominent"}`.
   Add `"mode": "light"` or `"dark"` if you already know it. It returns a layout pick, five real example sites,
   matching ready-made sections, a palette and two rules (hero and spacing).
2. **Take the design system of the best example.** Call `get_design_system` with that example's slug. It gives the
   real fonts, colour roles, type sizes and spacing. Use these numbers; do not invent your own.
3. **Need a ready-made section?** Call `find_reference_components` (for example type "hero", "pricing", "footer"),
   then `get_reference_jsx` for the one you want, and adapt it.
4. **Unsure which filter words are allowed?** Call `get_filters` once.
5. Build the page, then tell the user in one line which real sites you drew from.

## Always follow these two rules (they come from the archive)

- **The hero fits the first screen** (about 1280x800, `100svh`). Never let it run below the fold.
- **Give sections real space**: 80-160px between sections, and keep all text inside a centred, padded column.
  Nothing touches the screen edge.

## Keep it honest

- Draw from the examples; never copy a site's logo, wording, photos or brand. The new site must be clearly its own.
- If the connector does not answer (no internet, or Node.js missing), say so in one line and carry on with your best
  judgement rather than stopping.
- Keep calls few and targeted: `recommend` first, then only what you actually need.
