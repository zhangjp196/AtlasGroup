#!/usr/bin/env python3
"""生成 macOS 应用图标：desktop/icon.png + desktop/Atlas.icns。

设计沿用前端 favicon：深色圆角底 + 白色/青色 A 字标记。
只用 Pillow（开发依赖），产物已提交，构建脚本不需要重复生成。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT_PNG = HERE / "icon.png"
OUT_ICNS = HERE / "Atlas.icns"
SIZE = 1024


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((size, size))


def _draw_mark(draw: ImageDraw.ImageDraw, scale: float, offset: tuple[int, int]) -> None:
    """A 字 + 上方短竖线（与 favicon 一致），带一点青色。"""
    ox, oy = offset
    white = (245, 247, 255, 255)
    cyan = (96, 210, 255, 255)
    w = max(2, int(34 * scale))
    # 两条斜边
    left = [(ox + int(150 * scale), oy + int(790 * scale)),
            (ox + int(440 * scale), oy + int(190 * scale))]
    right = [(ox + int(584 * scale), oy + int(190 * scale)),
             (ox + int(874 * scale), oy + int(790 * scale))]
    draw.line(left, fill=cyan, width=w, joint="curve")
    draw.line(right, fill=white, width=w, joint="curve")
    # 横杠
    draw.line([(ox + int(330 * scale), oy + int(520 * scale)),
               (ox + int(694 * scale), oy + int(520 * scale))],
              fill=white, width=w, joint="curve")
    # 顶部短竖
    draw.line([(ox + int(512 * scale), oy + int(96 * scale)),
               (ox + int(512 * scale), oy + int(158 * scale))],
              fill=cyan, width=max(2, int(w * 0.7)), joint="curve")


def build() -> None:
    base = _gradient(SIZE, (13, 18, 32), (24, 38, 66)).convert("RGBA")
    # 细边框 + 内阴影感
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=220,
                           outline=(64, 92, 140, 160), width=6)
    _draw_mark(draw, 1.0, (0, 0))
    base.putalpha(_rounded_mask(SIZE, 220))
    base.save(OUT_PNG, "PNG")

    iconset = HERE / "Atlas.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        for suffix, factor in (("", 1), ("@2x", 2)):
            target = size * factor
            img = base.resize((target, target), Image.LANCZOS)
            img.save(iconset / f"icon_{size}x{size}{suffix}.png", "PNG")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT_ICNS)],
                   check=True)
    print(f"wrote {OUT_PNG} and {OUT_ICNS}")


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"icon build failed: {e}", file=sys.stderr)
        raise
