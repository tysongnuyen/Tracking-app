# Tracking branding assets

Created with the built-in `image_gen.imagegen` tool on 2026-10-18. The supplied
handwritten “Work” picture was a style reference only. The new app name remains
**Tracking**, with a separate handwritten **T** for the Windows icon.

Both selected PNG files were copied unchanged from the generated image directory
into this project. Transparency is preserved. `tracking.ico` is a format conversion
of the selected icon PNG using Pillow, with 16, 24, 32, 48, 64, 128 and 256 px
resolutions. No generated image content was changed in Python.

`branding.py` fits these assets proportionally for UI display with transparent
padding. A preview at the actual 150 × 58 px wordmark size was inspected before
integration.

## Selected wordmark

- File: `tracking-wordmark.png`
- Dimensions: 2172 × 724, RGBA with alpha range 0–255.
- Mode: new generation, built-in imagegen, `transparent_background=true`.
- Source: `C:/Users/Anh Nguyen/.codex/generated_images/01a14dfd-c67e-7831-8be1-268a210b6e4f/exec-7603e841-c5a4-4313-98be-35ea2a7140b7.png`
- Earlier trial and cleanup outputs were discarded; this selected wordmark was a
  fresh generation rather than an edit.

Exact final prompt:

> A clean flat logo wordmark, with EXACT text: "Tracking". Elegant flowing hand-lettered cursive signature written with a smooth fountain pen, solid white strokes on genuine transparent background. Titlecase T followed by r a c k i n g, spelled Tracking. Thin refined fluid lettering, simple legible lowercase loops and tasteful capital T swash. Clean digital vector-logo aesthetic, perfectly smooth WHITE filled calligraphic shapes. Every visible stroke is solid white with crisp antialiased edges. No drybrush texture, no distress, no roughness, no stipples, no specks, no particles, no scratches, no noise, no imperfections, no underlines, no extra text. Quiet sophisticated premium software brand. Compact horizontal composition 3:1, the lettering centered with small transparent margins. Do not include a backdrop. Not a sketch or photo; finished smooth calligraphic logo artwork.

## Selected icon

- Files: `tracking-icon.png`, `tracking.ico`
- PNG dimensions: 1254 × 1254, RGBA with alpha range 0–255.
- Mode: new generation, built-in imagegen, `transparent_background=true`.
- Source: `C:/Users/Anh Nguyen/.codex/generated_images/01a14dfd-c67e-7831-8be1-268a210b6e4f/exec-8df580a3-b12c-4c3a-a406-af7c7edd08eb.png`

Exact final prompt:

> Use case: logo-brand. Asset type: production Windows app icon PNG. Create one near-black rounded-square app tile (#121212, generous rounded corners) with a single large original WHITE handwritten capital "T", flowing confident brush pen calligraphy, elegant thin upstrokes and thicker downstrokes. White T centered and filling about 65 percent of tile, immediately recognizable and legible at 16 and 32 pixels; use a simple broad capital T silhouette with subtly sweeping top crossbar and graceful downstroke. Luxury minimal black and white visual style. No tiny writing, no full app name, no other letters, no slogan, no clock symbol, no shadows, no gradients, no glow, no extra objects. Square artwork; tile occupies 94 percent of square canvas. Genuinely transparent alpha outside the rounded-square tile, solid near-black tile inside. Flat icon production asset.
