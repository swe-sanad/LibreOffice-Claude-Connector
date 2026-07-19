# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Generate mcpb/icon.png: a LibreOffice-style document page wearing Claude's
crab as a hat. Pure stdlib (zlib PNG writer), no PIL:

    python scripts/make_mcpb_icon.py
"""
import io
import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "mcpb", "icon.png")

S = 2            # supersample factor over the 256 design grid
W = H = 256 * S

# palette
CRAB = (0xDA, 0x77, 0x56, 255)       # Anthropic terracotta
CRAB_DARK = (0xB8, 0x5C, 0x3E, 255)
PAGE = (0xFF, 0xFF, 0xFF, 255)
PAGE_EDGE = (0x18, 0xA3, 0x03, 255)  # LibreOffice green
PAGE_FOLD = (0x92, 0xE2, 0x85, 255)
TEXTLINE = (0xC9, 0xE8, 0xC4, 255)
EYE_W = (0xFF, 0xFF, 0xFF, 255)
EYE_B = (0x2B, 0x1B, 0x14, 255)

buf = bytearray(W * H * 4)


def put(x, y, c):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 4
        buf[i:i + 4] = bytes(c)


def circle(cx, cy, r, c):
    cx, cy, r = cx * S, cy * S, r * S
    for y in range(int(cy - r), int(cy + r) + 1):
        for x in range(int(cx - r), int(cx + r) + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                put(x, y, c)


def rect(x0, y0, x1, y1, c):
    for y in range(y0 * S, y1 * S):
        for x in range(x0 * S, x1 * S):
            put(x, y, c)


def tri(p1, p2, p3, c):
    (x1, y1), (x2, y2), (x3, y3) = [(px * S, py * S) for px, py in (p1, p2, p3)]
    xmin, xmax = int(min(x1, x2, x3)), int(max(x1, x2, x3))
    ymin, ymax = int(min(y1, y2, y3)), int(max(y1, y2, y3))
    den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if den == 0:
        return
    for y in range(ymin, ymax + 1):
        for x in range(xmin, xmax + 1):
            a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / den
            b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / den
            g = 1 - a - b
            if a >= 0 and b >= 0 and g >= 0:
                put(x, y, c)


def arc(cx, cy, r, a0, a1, width, c):
    for t in range(int(a0 * 100), int(a1 * 100)):
        ang = t / 100.0
        for w in range(-width, width + 1):
            x = int((cx + (r + w / S) * math.cos(ang)) * S)
            y = int((cy + (r + w / S) * math.sin(ang)) * S)
            for dx in range(S):
                for dy in range(S):
                    put(x + dx, y + dy, c)


# ================= the crab hat (drawn first; page overlaps its underside) ===
# legs peeking out sideways from under the shell
for sx in (1, -1):
    for lx, ly, r in ((52, 78, 7), (44, 64, 8), (50, 50, 8)):
        x = 128 + sx * (128 - lx) if False else (lx if sx == 1 else 256 - lx)
        circle(x, ly, r, CRAB_DARK)
# claws resting on the page's top corners
circle(70, 84, 17, CRAB_DARK)
circle(186, 84, 17, CRAB_DARK)
circle(70, 84, 13, CRAB)
circle(186, 84, 13, CRAB)
tri((70, 64), (84, 76), (56, 78), (0, 0, 0, 0))     # claw notches
tri((186, 64), (200, 78), (172, 76), (0, 0, 0, 0))
# arms tucking under the shell
circle(92, 72, 9, CRAB_DARK)
circle(164, 72, 9, CRAB_DARK)
# crab body: a shell sitting hat-like
circle(128, 56, 40, CRAB_DARK)
circle(128, 54, 36, CRAB)
# eyes + smile on the shell
circle(114, 48, 9, EYE_W)
circle(142, 48, 9, EYE_W)
circle(114, 47, 4, EYE_B)
circle(142, 47, 4, EYE_B)
arc(128, 56, 11, 0.5, math.pi - 0.5, 2, EYE_B)

# ================= the document page (the face of the icon) =================
rect(60, 88, 196, 236, PAGE_EDGE)                  # green border
rect(68, 96, 188, 228, PAGE)                       # white page
tri((158, 96), (188, 96), (188, 126), PAGE_FOLD)   # folded corner
for i, ly in enumerate((124, 142, 160, 178, 196)):
    rect(84, ly, 148 if i in (0, 4) else 172, ly + 6, TEXTLINE)

# ---- encode PNG ----
rows = []
stride = W * 4
for y in range(H):
    rows.append(b"\x00" + bytes(buf[y * stride:(y + 1) * stride]))


def chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
       + chunk(b"IEND", b""))
io.open(OUT, "wb").write(png)
print("wrote %s (%dx%d, %.1f KB)" % (OUT, W, H, len(png) / 1024.0))
