---
name: x-banner-craft
description: Design X (Twitter) profile header/banner images that make the avatar and
  display name pop and land one idea, then validate the result against how X actually
  renders it on desktop and mobile before anyone ships it.
---

# X Banner Craft

Actionable playbook for designing X (Twitter) profile header banners that survive real
desktop and mobile rendering, not just a full-bleed canvas preview.

## Canvas and geometry

- Canvas is always **1500x500px** (3:1 source aspect).
- A center safe band of roughly **x:150-1350, y:85-415** holds all copy and focal
  content. Outer margins crop first on mobile and under header chrome.
- **Lower-left is the avatar dead zone.** The circular avatar overlaps this region
  (roughly x:0-320, y:260-500). Put nothing essential there.
- Make the avatar pocket the **darkest / richest** part of the frame so the white
  avatar ring and white display name snap forward instead of dissolving into the
  background.

## One idea, feed-thumbnail legibility

- One focal idea per banner. Never a collage of products, logos, and slogans.
- If the idea does not read at thumbnail size while scrolling a feed, it fails.
- Treat the banner as a billboard seen in one glance, not a brochure.

## Canva-derived craft checklist

These eight cues are distilled from banner-design best practice:

1. **Limited color palette** - two to three intentional tones, not a rainbow.
2. **Generous negative space** - let the idea breathe; do not fill every pixel.
3. **One clear message** - a single setup/payoff beat, not a paragraph.
4. **Deliberate contrast** - dark against light (or reverse) by design, not accident.
5. **Subtle texture or grain** - never a flat-digital wash; a little noise adds depth.
6. **Blocks of color over busy detail** - broad fields beat dense illustration.
7. **Do not complicate** - cut elements until the frame feels inevitable.
8. **Know when enough is enough** - stop before the last "clever" extra.

Also: **pair design with photography rather than competing with it.** The profile
owner's avatar photo *is* the photography half of that pairing. The banner is the
designed stage; the face is the subject.

## Contrast-against-the-avatar rule

Before picking a palette, look at the actual avatar photo's value range and hue.

- The banner's darkest, richest tone must sit **directly behind and around** the
  avatar position.
- Cool, light tones near the avatar are the failure mode: they match a fair-skinned
  or light-clothed subject and the person dissolves into the background.
- Gradients carry depth. Flat single-color washes and network-mesh / wireframe /
  connected-dots-globe cliches read as generic AI-tech slop and are **banned**.

## Type hierarchy

- Split copy into a **setup line** (smaller, cooler, secondary) and a **payoff line**
  (larger, brighter, the landing beat).
- Both lines and any logo lockup share **one left margin** so the eye has a single
  anchor column.
- Add a soft, blurred dark **scrim** behind copy whenever it sits over a light or
  warm region, so white text holds real contrast (roughly AA/AAA-grade, not just
  "looks fine at full opacity").

## What to ban outright (and why)

Counter-positioning / disclosure doctrine, generalized to any profile:

- **No raw engagement-bait** - "Follow me", arrows pointing at the avatar, fake
  buttons. It reads as desperate and fights the product UI.
- **No vanity-metric numbers baked into the image** - follower counts invite a
  numbers contest with competitors and go stale immediately.
- **No green "verified/secure" checkmark or shield motif** - trust theater, not
  earned credibility.
- **No literal product screenshots or UI chrome** pasted into the banner - they
  blur into the real app chrome and age poorly.

## THE mobile lesson

A banner that is correct by margin math on desktop can still fail on the phone.

X's mobile profile view does **not** just scale the banner. It crops it to a wider
aspect ratio (roughly **3.34:1** vs the source **3:1**), which trims slices off the
top and bottom of the source image. On top of that, mobile header chrome (back
button / name / a small icon cluster of actions) occupies real screen space at the
top of the screen and can float over the top of the banner during scroll
interactions.

Margin math against the 1500x500 canvas alone will not catch this. The only
reliable check is rendering an actual simulated phone composite from a zone map
that was measured against a real X screenshot, and confirming no copy or lockup
bounding box intersects a danger zone.

**Always run the validator's phone composite before calling a banner done.** A
desktop-only check is not sufficient.

## Zone map is living data

Danger-zone coordinates are measured against a specific X app snapshot and will
drift when X changes its mobile layout. Re-measure from a fresh screenshot
periodically. The validator script keeps this map in one versioned, dated constant
for exactly this reason.

## Workflow

1. Pick a palette against the avatar's actual value range (darkest pocket at the
   avatar position).
2. Write one setup / payoff copy pair.
3. Render with `compose_banner.py` from a declarative JSON spec.
4. Run `validate_banner.py` against the render and both a desktop and
   simulated-phone composite.
5. Only ship if the validator exits 0.

## Scripts

### compose_banner.py

```
python3 scripts/compose_banner.py <spec.json> <out_png> [--regions-out <path>]
```

Renders a 1500x500 PNG from a declarative JSON spec (gradient, grain, scrim, copy
lines, lockup). Writes a sidecar `.regions.json` of content bounding boxes for the
validator (default: same path as the PNG with `.regions.json` suffix).

### validate_banner.py

```
python3 scripts/validate_banner.py <banner_png> <regions_json> <out_dir> [--avatar-corner lower-left]
```

Loads the banner and regions, draws desktop and phone composites with danger-zone
overlays into `<out_dir>`, checks every content region against danger zones, prints
PASS or FAIL, exits 0 / 1 / 2 accordingly.

## examples/

`examples/` holds the smoke-test output of running both scripts end to end: a good
demo banner (expected PASS) and a bad sample deliberately overlapping
`mobile_icon_cluster` (expected FAIL). That pass/fail pair is what makes this skill
tested, not just listed. See `examples/README.md`.
