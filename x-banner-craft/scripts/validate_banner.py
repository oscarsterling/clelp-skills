#!/usr/bin/env python3
"""validate_banner.py - Validate an X banner against measured mobile danger zones.

CLI:
    python3 validate_banner.py <banner_png> <regions_json> <out_dir> [--avatar-corner lower-left]

Exit codes:
    0  PASS (no region intersects any danger zone)
    1  FAIL (one or more intersections)
    2  input error (missing file, wrong canvas size, malformed JSON)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1500
CANVAS_H = 500

# Measured 2026-07-23 against a live X mobile profile screenshot (iOS, dark mode).
# X periodically changes its mobile profile layout; re-measure from a fresh
# screenshot if banners start failing validation for no visible reason.
DANGER_ZONES: dict[str, tuple[int, int, int, int]] = {
    "mobile_top_crop": (0, 0, 1500, 26),
    "mobile_bottom_crop": (0, 474, 1500, 500),
    "mobile_icon_cluster": (1180, 0, 1500, 110),
    "avatar_dead_zone": (0, 260, 320, 500),
}


def rects_intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """True if rectangles overlap with positive area (edge-touching does not count).

    Standard test: not (a.x1 <= b.x0 or b.x1 <= a.x0 or a.y1 <= b.y0 or b.y1 <= a.y0).
    """
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def load_small_font(size: int = 14) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def union_avatar_zone(
    declared: dict[str, int] | None,
) -> tuple[int, int, int, int]:
    """Union constant avatar_dead_zone with the regions JSON's declared avatar_zone."""
    const = DANGER_ZONES["avatar_dead_zone"]
    if not declared:
        return const
    dx0 = int(declared["x0"])
    dy0 = int(declared["y0"])
    dx1 = int(declared["x1"])
    dy1 = int(declared["y1"])
    return (
        min(const[0], dx0),
        min(const[1], dy0),
        max(const[2], dx1),
        max(const[3], dy1),
    )


def effective_danger_zones(
    regions_data: dict[str, Any],
) -> dict[str, tuple[int, int, int, int]]:
    zones = dict(DANGER_ZONES)
    declared = regions_data.get("avatar_zone")
    zones["avatar_dead_zone"] = union_avatar_zone(declared)
    return zones


def check_intersections(
    regions: list[dict[str, Any]],
    danger_zones: dict[str, tuple[int, int, int, int]],
) -> list[tuple[str, str]]:
    """Return list of (region_label, zone_name) pairs that truly overlap."""
    hits: list[tuple[str, str]] = []
    for reg in regions:
        label = str(reg.get("label", "?"))
        bbox = reg.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        rb = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        for zname, zb in danger_zones.items():
            if rects_intersect(rb, zb):
                hits.append((label, zname))
    return hits


def draw_desktop_composite(
    banner: Image.Image,
    danger_zones: dict[str, tuple[int, int, int, int]],
    out_path: str,
) -> None:
    base = banner.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_small_font(16)
    for name, (x0, y0, x1, y1) in danger_zones.items():
        draw.rectangle([x0, y0, x1, y1], fill=(255, 0, 0, 70), outline=(255, 40, 40, 200))
        draw.text((x0 + 4, y0 + 2), name, fill=(255, 220, 220, 255), font=font)
    out = Image.alpha_composite(base, overlay).convert("RGB")
    out.save(out_path, format="PNG")


def draw_phone_composite(
    banner: Image.Image,
    danger_zones: dict[str, tuple[int, int, int, int]],
    out_path: str,
) -> None:
    """Simulate mobile cover-fit crop, scale to 1126px wide, draw chrome + icons + avatar."""
    top = DANGER_ZONES["mobile_top_crop"][3]  # 26
    bottom = DANGER_ZONES["mobile_bottom_crop"][1]  # 474
    # Visible slice after removing top/bottom crop bands.
    cropped = banner.crop((0, top, CANVAS_W, bottom))
    phone_w = 1126
    scale = phone_w / CANVAS_W
    phone_h = max(1, int(round(cropped.height * scale)))
    phone = cropped.resize((phone_w, phone_h), Image.Resampling.LANCZOS).convert("RGBA")

    draw = ImageDraw.Draw(phone)
    # Solid dark status/header chrome bar across the top ~85px.
    chrome_h = 85
    draw.rectangle([0, 0, phone_w, chrome_h], fill=(12, 12, 16, 240))

    # Project mobile_icon_cluster into phone coords.
    # Source y is relative to full 1500x500; after crop, y' = y - top.
    ix0, iy0, ix1, iy1 = DANGER_ZONES["mobile_icon_cluster"]
    px0 = int(round(ix0 * scale))
    py0 = int(round((iy0 - top) * scale))
    px1 = int(round(ix1 * scale))
    py1 = int(round((iy1 - top) * scale))
    # Clamp into phone frame.
    py0 = max(0, py0)
    # Draw simple icon glyphs (circles + small rect labels).
    font = load_small_font(12)
    icon_y = max(8, (chrome_h // 2) - 10)
    icon_r = 10
    gap = 28
    right = phone_w - 18
    for i in range(3):
        cx = right - i * gap
        cy = icon_y + icon_r
        draw.ellipse([cx - icon_r, cy - icon_r, cx + icon_r, cy + icon_r], outline=(220, 220, 230, 255), width=2)
    draw.text((px0 + 4, max(py0, 2) + 2), "icons", fill=(200, 200, 210, 255), font=font)
    # Light outline of the projected icon cluster zone.
    draw.rectangle([px0, max(0, py0), min(phone_w - 1, px1), min(phone_h - 1, py1)], outline=(255, 80, 80, 180), width=1)

    # Avatar circle silhouette in lower-left (projected from avatar_dead_zone).
    ax0, ay0, ax1, ay1 = danger_zones.get("avatar_dead_zone", DANGER_ZONES["avatar_dead_zone"])
    # Avatar center sits near bottom of the zone, partly hanging off the banner on real X;
    # approximate with a circle in the lower-left of the phone composite.
    av_cx = int(round(((ax0 + ax1) / 2.0) * scale))
    # Map source y through crop.
    av_cy_src = (ay0 + ay1) / 2.0
    av_cy = int(round((av_cy_src - top) * scale))
    av_r = int(round(((ax1 - ax0) * 0.42) * scale))
    # Shift slightly down so it reads as overlapping the bottom of the banner.
    av_cy = min(phone_h - 4, av_cy + int(20 * scale))
    draw.ellipse(
        [av_cx - av_r, av_cy - av_r, av_cx + av_r, av_cy + av_r],
        fill=(40, 40, 48, 230),
        outline=(255, 255, 255, 255),
        width=3,
    )

    phone.convert("RGB").save(out_path, format="PNG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate X banner against mobile danger zones.")
    parser.add_argument("banner_png", help="1500x500 banner PNG")
    parser.add_argument("regions_json", help="Regions JSON from compose_banner.py")
    parser.add_argument("out_dir", help="Directory for desktop/phone composites")
    parser.add_argument(
        "--avatar-corner",
        default="lower-left",
        choices=["lower-left"],
        help="Avatar corner placement (only lower-left supported)",
    )
    args = parser.parse_args(argv)

    try:
        if not os.path.isfile(args.banner_png):
            print(f"error: banner not found: {args.banner_png}", file=sys.stderr)
            return 2
        if not os.path.isfile(args.regions_json):
            print(f"error: regions JSON not found: {args.regions_json}", file=sys.stderr)
            return 2

        banner = Image.open(args.banner_png)
        w, h = banner.size
        if w != CANVAS_W or h != CANVAS_H:
            print(
                f"error: banner must be exactly {CANVAS_W}x{CANVAS_H}, got {w}x{h}",
                file=sys.stderr,
            )
            return 2

        with open(args.regions_json, encoding="utf-8") as f:
            regions_data = json.load(f)

        if not isinstance(regions_data, dict):
            print("error: regions JSON must be an object", file=sys.stderr)
            return 2
        regions = regions_data.get("regions")
        if not isinstance(regions, list):
            print("error: regions JSON missing 'regions' list", file=sys.stderr)
            return 2

        os.makedirs(args.out_dir, exist_ok=True)
        danger = effective_danger_zones(regions_data)

        desktop_path = os.path.join(args.out_dir, "desktop_composite.png")
        phone_path = os.path.join(args.out_dir, "phone_composite.png")
        draw_desktop_composite(banner, danger, desktop_path)
        draw_phone_composite(banner, danger, phone_path)

        hits = check_intersections(regions, danger)

        if hits:
            print("FAIL")
            for label, zname in hits:
                print(f"FAIL: '{label}' intersects danger zone '{zname}'")
            print(f"wrote {desktop_path}")
            print(f"wrote {phone_path}")
            return 1

        print("PASS")
        print("No content regions intersect danger zones.")
        print(f"wrote {desktop_path}")
        print(f"wrote {phone_path}")
        return 0

    except json.JSONDecodeError as e:
        print(f"error: malformed JSON: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
