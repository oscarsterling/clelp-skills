#!/usr/bin/env python3
"""compose_banner.py - Render a 1500x500 X profile banner from a declarative JSON spec.

CLI:
    python3 compose_banner.py <spec.json> <out_png> [--regions-out <path>]

Spec schema (example):
{
  "background": {
    "type": "linear_gradient",
    "angle_deg": 135,
    "stops": [{"pos": 0.0, "color": "#0b0f1a"}, {"pos": 1.0, "color": "#c96a2e"}]
  },
  "grain": {"enabled": true, "opacity": 0.05, "seed": 7},
  "avatar_zone": {"x0": 0, "y0": 260, "x1": 320, "y1": 500},
  "copy_lines": [
    {"text": "It exists.", "role": "setup", "size_px": 50, "color": "#9fb3c8"},
    {"text": "But does it work?", "role": "payoff", "size_px": 64, "color": "#ffffff"}
  ],
  "copy_left_margin": 662,
  "copy_top": 190,
  "scrim": {"enabled": true, "opacity": 0.35, "padding": 24},
  "lockup": {"text": "clelp", "size_px": 28, "color": "#ffffff"}
}

Writes <out_png> plus a regions sidecar JSON of content bounding boxes in 1500x500
source coordinates for validate_banner.py.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS_W = 1500
CANVAS_H = 500


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"expected #RRGGBB color, got {color!r}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rects_intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """True if rectangles overlap with positive area (edge-touching does not count)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def load_font(size_px: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Prefer a real system TTF on Darwin; fall back to load_default cleanly."""
    candidates: list[str] = []
    if bold:
        candidates.append("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
        candidates.append("/System/Library/Fonts/Supplemental/Arial.ttf")
    else:
        candidates.append("/System/Library/Fonts/Supplemental/Arial.ttf")
        candidates.append("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    # Common Linux fallbacks (harmless if missing).
    candidates.extend(
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size_px)
            except OSError:
                continue
    return ImageFont.load_default()


def interpolate_stops(stops: list[dict[str, Any]], t: float) -> tuple[int, int, int]:
    """Multi-stop color interpolation for t in [0, 1]."""
    ordered = sorted(stops, key=lambda s: float(s["pos"]))
    if t <= float(ordered[0]["pos"]):
        return hex_to_rgb(ordered[0]["color"])
    if t >= float(ordered[-1]["pos"]):
        return hex_to_rgb(ordered[-1]["color"])
    for i in range(len(ordered) - 1):
        p0 = float(ordered[i]["pos"])
        p1 = float(ordered[i + 1]["pos"])
        if p0 <= t <= p1:
            local = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            c0 = hex_to_rgb(ordered[i]["color"])
            c1 = hex_to_rgb(ordered[i + 1]["color"])
            return tuple(int(round(c0[j] + (c1[j] - c0[j]) * local)) for j in range(3))  # type: ignore[return-value]
    return hex_to_rgb(ordered[-1]["color"])


def render_linear_gradient(
    width: int,
    height: int,
    angle_deg: float,
    stops: list[dict[str, Any]],
) -> Image.Image:
    """Genuine diagonal multi-stop gradient across the full canvas."""
    # Direction vector for the gradient axis.
    rad = math.radians(angle_deg)
    dx = math.cos(rad)
    dy = math.sin(rad)
    # Project corners to find min/max along the axis so stops map full-frame.
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    projs = [x * dx + y * dy for x, y in corners]
    pmin, pmax = min(projs), max(projs)
    span = pmax - pmin if pmax != pmin else 1.0

    # Sample a 1D strip then map via projection for speed.
    strip_len = max(width, height) * 2
    strip_colors = [interpolate_stops(stops, i / max(strip_len - 1, 1)) for i in range(strip_len)]

    pixels: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            p = x * dx + y * dy
            t = (p - pmin) / span
            idx = int(round(t * (strip_len - 1)))
            idx = max(0, min(strip_len - 1, idx))
            pixels.append(strip_colors[idx])
    img = Image.new("RGB", (width, height))
    img.putdata(pixels)
    return img


def apply_grain(
    base: Image.Image,
    opacity: float,
    seed: int,
    avatar_zone: dict[str, int],
) -> Image.Image:
    """Blend luminance noise; mute grain inside avatar_zone (factor ~0.15)."""
    if opacity <= 0:
        return base
    rng = random.Random(seed)
    w, h = base.size
    ax0 = int(avatar_zone["x0"])
    ay0 = int(avatar_zone["y0"])
    ax1 = int(avatar_zone["x1"])
    ay1 = int(avatar_zone["y1"])

    noise_pixels: list[tuple[int, int, int]] = []
    for y in range(h):
        for x in range(w):
            v = rng.randint(0, 255)
            noise_pixels.append((v, v, v))
    noise = Image.new("RGB", (w, h))
    noise.putdata(noise_pixels)

    base_rgb = base.convert("RGB")
    out_pixels: list[tuple[int, int, int]] = []
    bp = list(base_rgb.getdata())
    np_ = noise_pixels
    for y in range(h):
        for x in range(w):
            i = y * w + x
            b = bp[i]
            n = np_[i]
            local_op = opacity
            if ax0 <= x < ax1 and ay0 <= y < ay1:
                local_op = opacity * 0.15
            r = int(round(b[0] * (1.0 - local_op) + n[0] * local_op))
            g = int(round(b[1] * (1.0 - local_op) + n[1] * local_op))
            bl = int(round(b[2] * (1.0 - local_op) + n[2] * local_op))
            out_pixels.append((r, g, bl))
    out = Image.new("RGB", (w, h))
    out.putdata(out_pixels)
    return out


def text_bbox(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> tuple[int, int, int, int]:
    """Return integer (x0, y0, x1, y1) for rendered text at xy (left, top)."""
    x, y = xy
    # textbbox is relative to the given origin when using anchor default (lt-like).
    try:
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
    except TypeError:
        # Very old Pillow: fall back to textsize-ish.
        try:
            tw, th = draw.textsize(text, font=font)  # type: ignore[attr-defined]
        except Exception:
            tw, th = font.getsize(text)  # type: ignore[attr-defined]
        left, top, right, bottom = x, y, x + tw, y + th
    return int(left), int(top), int(right), int(bottom)


def compose(spec: dict[str, Any], out_png: str, regions_out: str) -> None:
    bg = spec.get("background") or {}
    if bg.get("type") != "linear_gradient":
        raise ValueError("background.type must be 'linear_gradient'")
    stops = bg.get("stops") or []
    if len(stops) < 2:
        raise ValueError("background.stops needs at least two stops")
    angle = float(bg.get("angle_deg", 0))

    img = render_linear_gradient(CANVAS_W, CANVAS_H, angle, stops)

    avatar_zone = spec.get("avatar_zone") or {"x0": 0, "y0": 260, "x1": 320, "y1": 500}
    grain = spec.get("grain") or {}
    if grain.get("enabled"):
        img = apply_grain(
            img,
            float(grain.get("opacity", 0.05)),
            int(grain.get("seed", 0)),
            avatar_zone,
        )

    draw = ImageDraw.Draw(img)
    copy_left = int(spec.get("copy_left_margin", 662))
    copy_top = int(spec.get("copy_top", 190))
    copy_lines = spec.get("copy_lines") or []

    # Measure all text first so we can draw scrim, then text.
    measured: list[dict[str, Any]] = []
    y_cursor = copy_top
    line_gap = 16
    for line in copy_lines:
        size_px = int(line.get("size_px", 48))
        bold = line.get("role") == "payoff"
        font = load_font(size_px, bold=bold)
        text = str(line.get("text", ""))
        bbox = text_bbox(draw, (copy_left, y_cursor), text, font)
        # If font metrics put top above y_cursor, still use actual bbox.
        measured.append(
            {
                "label": f"copy:{line.get('role', 'line')}",
                "text": text,
                "color": line.get("color", "#ffffff"),
                "font": font,
                "xy": (copy_left, y_cursor),
                "bbox": bbox,
            }
        )
        y_cursor = bbox[3] + line_gap

    lockup_spec = spec.get("lockup")
    lockup_meas: dict[str, Any] | None = None
    if lockup_spec:
        lockup_gap = 28
        size_px = int(lockup_spec.get("size_px", 28))
        font = load_font(size_px, bold=False)
        text = str(lockup_spec.get("text", ""))
        xy = (copy_left, y_cursor + lockup_gap - line_gap)
        # If no copy lines, place lockup at copy_top.
        if not measured:
            xy = (copy_left, copy_top)
        bbox = text_bbox(draw, xy, text, font)
        lockup_meas = {
            "label": "lockup",
            "text": text,
            "color": lockup_spec.get("color", "#ffffff"),
            "font": font,
            "xy": xy,
            "bbox": bbox,
        }

    all_bboxes = [m["bbox"] for m in measured]
    if lockup_meas:
        all_bboxes.append(lockup_meas["bbox"])

    az = (
        int(avatar_zone["x0"]),
        int(avatar_zone["y0"]),
        int(avatar_zone["x1"]),
        int(avatar_zone["y1"]),
    )
    for bb in all_bboxes:
        if rects_intersect(bb, az):
            raise ValueError(
                f"content bounding box {bb} would overlap avatar_zone {az}; "
                "adjust copy_left_margin / copy_top / sizes before rendering"
            )

    # Scrim: soft dark rect under all copy, blurred.
    scrim = spec.get("scrim") or {}
    if scrim.get("enabled") and all_bboxes:
        pad = int(scrim.get("padding", 24))
        opacity = float(scrim.get("opacity", 0.35))
        sx0 = min(b[0] for b in all_bboxes) - pad
        sy0 = min(b[1] for b in all_bboxes) - pad
        sx1 = max(b[2] for b in all_bboxes) + pad
        sy1 = max(b[3] for b in all_bboxes) + pad
        # Draw on RGBA overlay then composite.
        overlay = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        alpha = max(0, min(255, int(round(opacity * 255))))
        # Rounded-ish soft block: rect then gaussian blur.
        odraw.rounded_rectangle([sx0, sy0, sx1, sy1], radius=18, fill=(0, 0, 0, alpha))
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=12))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    # Draw text on top of scrim.
    for m in measured:
        draw.text(m["xy"], m["text"], fill=hex_to_rgb(m["color"]), font=m["font"])
    if lockup_meas:
        draw.text(
            lockup_meas["xy"],
            lockup_meas["text"],
            fill=hex_to_rgb(lockup_meas["color"]),
            font=lockup_meas["font"],
        )

    # Re-measure bboxes after final draw context (same fonts/xy; keep measured).
    regions = [{"label": m["label"], "bbox": list(m["bbox"])} for m in measured]
    if lockup_meas:
        regions.append({"label": "lockup", "bbox": list(lockup_meas["bbox"])})

    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    img.save(out_png, format="PNG")

    sidecar = {
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "avatar_zone": {
            "x0": int(avatar_zone["x0"]),
            "y0": int(avatar_zone["y0"]),
            "x1": int(avatar_zone["x1"]),
            "y1": int(avatar_zone["y1"]),
        },
        "regions": regions,
    }
    os.makedirs(os.path.dirname(os.path.abspath(regions_out)) or ".", exist_ok=True)
    with open(regions_out, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose a 1500x500 X banner from JSON.")
    parser.add_argument("spec_json", help="Path to declarative banner spec JSON")
    parser.add_argument("out_png", help="Output PNG path")
    parser.add_argument(
        "--regions-out",
        default=None,
        help="Sidecar regions JSON path (default: <out_png> with .regions.json)",
    )
    args = parser.parse_args(argv)

    regions_out = args.regions_out
    if not regions_out:
        if args.out_png.lower().endswith(".png"):
            regions_out = args.out_png[:-4] + ".regions.json"
        else:
            regions_out = args.out_png + ".regions.json"

    try:
        with open(args.spec_json, encoding="utf-8") as f:
            spec = json.load(f)
        compose(spec, args.out_png, regions_out)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError, KeyError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"wrote {args.out_png}")
    print(f"wrote {regions_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
