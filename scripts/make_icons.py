# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Generate the extension's PNG icons with the standard library only.

Produces a rounded-square "brand" tile (warm coral) at the sizes LibreOffice
wants: 16 & 26 px for the toolbar command, 42 px for the Extension Manager.
Anti-aliased via 3x supersampling. Run once (or from the build):

    python scripts/make_icons.py
"""

import os
import struct
import zlib

FILL = (0xD9, 0x77, 0x57)   # warm coral
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                       "ext", "icons")
SIZES = {"cmd16.png": 16, "cmd26.png": 26, "icon.png": 42}
SS = 3  # supersampling factor


def _in_rounded(x, y, w, h, r):
    nx = min(max(x, r), w - 1 - r)
    ny = min(max(y, r), h - 1 - r)
    dx, dy = x - nx, y - ny
    return dx * dx + dy * dy <= r * r


def _render(size):
    big = size * SS
    radius = big * 0.24
    # supersampled mask
    mask = bytearray(big * big)
    for y in range(big):
        for x in range(big):
            mask[y * big + x] = 1 if _in_rounded(x, y, big, big, radius) else 0
    # downsample to RGBA
    pixels = bytearray()
    n = SS * SS
    for y in range(size):
        for x in range(size):
            covered = 0
            for dy in range(SS):
                base = (y * SS + dy) * big + x * SS
                for dx in range(SS):
                    covered += mask[base + dx]
            alpha = int(covered * 255 / n)
            pixels += bytes((FILL[0], FILL[1], FILL[2], alpha))
    return pixels


def _write_png(path, size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                       # filter: none
        raw += rgba[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + \
        chunk(b"IDAT", comp) + chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(blob)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, size in SIZES.items():
        _write_png(os.path.join(OUT_DIR, name), size, _render(size))
        print("wrote", os.path.join("ext", "icons", name), "(%dx%d)" % (size, size))


if __name__ == "__main__":
    main()
