#!/usr/bin/env python3
"""生成 macOS 应用图标：desktop/icon.png + desktop/Atlas.icns。

设计：深色圆角底 + 蓝紫环境光 + 渐变 A 字 + 顶部星芒。
只用 Pillow（开发依赖），产物已提交，构建脚本不需要重复生成。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT_PNG = HERE / "icon.png"
OUT_ICNS = HERE / "Atlas.icns"
SIZE = 1024
CANVAS = 2048
RADIUS = 430


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgrad(size: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        px[0, y] = _lerp(top, bottom, y / max(1, size - 1))
    return img.resize((size, size))


def _hgrad(size: int, left: tuple, right: tuple) -> Image.Image:
    img = Image.new("RGB", (size, 1))
    px = img.load()
    for x in range(size):
        px[x, 0] = _lerp(left, right, x / max(1, size - 1))
    return img.resize((size, size))


def _radial(size: int, cx: int, cy: int, radius: int, strength: float) -> Image.Image:
    img = Image.new("L", (size, size), 0)
    px = img.load()
    for y in range(size):
        dy = y - cy
        for x in range(size):
            d = ((x - cx) ** 2 + dy * dy) ** 0.5
            v = max(0.0, 1 - d / radius)
            px[x, y] = int(255 * (v**2) * strength)
    return img


def _k(value: int) -> int:
    return value * CANVAS // SIZE


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _a_mask() -> Image.Image:
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    width = _k(94)
    draw.line([(_k(512), _k(240)), (_k(300), _k(812))], fill=255, width=width, joint="curve")
    draw.line([(_k(512), _k(240)), (_k(724), _k(812))], fill=255, width=width, joint="curve")
    draw.line([(_k(382), _k(600)), (_k(642), _k(600))], fill=255, width=_k(70), joint="curve")
    for x, y, r in ((512, 240, 47), (300, 812, 47), (724, 812, 47),
                    (382, 600, 35), (642, 600, 35)):
        draw.ellipse((_k(x - r), _k(y - r), _k(x + r), _k(y + r)), fill=255)

    # 顶部星芒：Agent / 智能感。
    cx, cy, outer, inner = _k(512), _k(116), _k(52), _k(13)
    draw.polygon([
        (cx, cy - outer), (cx + inner, cy - inner), (cx + outer, cy),
        (cx + inner, cy + inner), (cx, cy + outer), (cx - inner, cy + inner),
        (cx - outer, cy), (cx - inner, cy - inner),
    ], fill=255)
    return mask


def build() -> None:
    base = _vgrad(CANVAS, (9, 14, 28), (20, 18, 46)).convert("RGBA")

    # 左上蓝光 / 右下紫光
    for cx, cy, radius, color, strength in (
        (330, 220, 1650, (52, 150, 255), 0.35),
        (1750, 1700, 1600, (126, 84, 255), 0.30),
    ):
        mask = _radial(CANVAS, cx, cy, radius, strength)
        layer = Image.new("RGBA", (CANVAS, CANVAS), color + (255,))
        base = Image.composite(layer, base, mask)

    # 暗角，让边缘更沉、中间更亮
    vignette = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(vignette).ellipse((80, 80, CANVAS - 80, CANVAS - 80), fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    base = Image.composite(base, Image.new("RGBA", (CANVAS, CANVAS), (5, 7, 14, 255)), vignette)

    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    icon.paste(base, (0, 0), _rounded_mask(CANVAS, RADIUS))

    mark = _a_mask()
    glow = mark.filter(ImageFilter.GaussianBlur(22))
    glow_layer = Image.new("RGBA", (CANVAS, CANVAS), (70, 170, 255, 0))
    glow_layer.putalpha(glow.point(lambda v: int(v * 0.42)))
    icon = Image.alpha_composite(icon, glow_layer)

    gradient = _hgrad(CANVAS, (78, 205, 255), (247, 250, 255)).convert("RGBA")
    icon.paste(gradient, (0, 0), mark)

    ImageDraw.Draw(icon).rounded_rectangle(
        (4, 4, CANVAS - 5, CANVAS - 5), radius=RADIUS - 4,
        outline=(130, 170, 240, 85), width=10)

    icon = icon.resize((SIZE, SIZE), Image.LANCZOS)
    icon.save(OUT_PNG, "PNG")

    iconset = HERE / "Atlas.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        for suffix, factor in (("", 1), ("@2x", 2)):
            target = size * factor
            resized = icon.resize((target, target), Image.LANCZOS)
            resized.save(iconset / f"icon_{size}x{size}{suffix}.png", "PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
                   check=True)
    print(f"wrote {OUT_PNG} and {OUT_ICNS}")


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"icon build failed: {e}", file=sys.stderr)
        raise
