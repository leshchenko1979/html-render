#!/usr/bin/env python3
"""Generate the benchmark deck: a deterministic document every user can rebuild.

The point of this file is that nobody has to take a figure on trust. Given a
seed, it writes the same HTML and the same images, byte for byte, on any host:
the deck is generated from arithmetic, not shipped as a 4 MB blob, so it can be
rebuilt, diffed and re-measured from a checkout.

Two properties it must hold, and `tools/bench.py --self-test` checks both:

  * **Same seed, same bytes.** Every random draw comes from `random.Random(seed)`
    and every PNG is written with a fixed zlib level and no timestamp chunk, so
    the md5 of `deck.html` and of each image is a function of the seed alone.
  * **Exactly `SLIDES * SLIDE_H` CSS pixels tall.** Each slide is a fixed-height
    box with `overflow: hidden`, and `html, body` have no margin. The probe
    asserts the rendered PNG's height equals `SLIDES * SLIDE_H * scale`, so a
    deck that grew a stray 4 px would fail loudly instead of being measured as a
    shorter document.

Stdlib only. The PNG writer below is 40 lines of zlib and struct, which is
cheaper than making every user install Pillow to regenerate a fixture.
"""

import os
import random
import struct
import zlib

SLIDES = 8
SLIDE_W = 1280
SLIDE_H = 720
DOC_H = SLIDES * SLIDE_H
SEED_DEFAULT = 20261115

# Photo-like plates, generated once and reused across slides.
PLATES = [("plate-a.png", 0), ("plate-b.png", 1), ("plate-c.png", 2), ("plate-d.png", 3)]
PLATE_W, PLATE_H = 640, 360


def write_png(path, width, height, rows):
    """Write an 8-bit RGB PNG from `rows` (an iterable of bytes, 3*width each)."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(chunk(b"IEND", b""))


def plate(seed, index):
    """A deterministic 640x360 plate: two gradients, three blobs, mild noise."""
    rng = random.Random(seed * 31 + index)
    blobs = [
        (
            rng.randrange(0, PLATE_W),
            rng.randrange(0, PLATE_H),
            rng.randrange(80, 200),
            rng.randrange(40, 220),
            rng.randrange(40, 220),
            rng.randrange(40, 220),
        )
        for _ in range(3)
    ]
    rows = []
    for y in range(PLATE_H):
        row = bytearray()
        for x in range(PLATE_W):
            r = 40 + (x * 120) // PLATE_W
            g = 30 + (y * 140) // PLATE_H
            b = 60 + ((x + y) * 90) // (PLATE_W + PLATE_H)
            for bx, by, br, cr, cg, cb in blobs:
                dx, dy = x - bx, y - by
                d = dx * dx + dy * dy
                if d < br * br:
                    # Quadratic falloff, so the edge is a soft gradient and the
                    # JPEG-like noise below lands on smooth and busy areas alike.
                    w = ((br * br - d) * 255) // (br * br * 4)
                    r = (r * (255 - w) + cr * w) // 255
                    g = (g * (255 - w) + cg * w) // 255
                    b = (b * (255 - w) + cb * w) // 255
            n = rng.randrange(-6, 7)
            row += bytes((max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n))))
        rows.append(row)
    return rows


def paragraph(rng, words, sentences):
    """`sentences` sentences of `words` words, drawn from a fixed word list."""
    vocab = (
        "render layout column margin glyph baseline kerning band canvas viewport "
        "stream row paint probe measure memory budget frame document stylesheet "
        "cascade inherit compute layout tree fragment clip scroll height width"
    ).split()
    out = []
    for _ in range(sentences):
        s = [rng.choice(vocab) for _ in range(words)]
        s[0] = s[0].capitalize()
        out.append(" ".join(s) + ".")
    return " ".join(out)


STYLE = """
  html, body { margin: 0; padding: 0; background: #ffffff; }
  body { font-family: sans-serif; color: #111111; }
  .slide { width: %(w)dpx; height: %(h)dpx; overflow: hidden; padding: 40px;
           box-sizing: border-box; }
  h1 { font-size: 46px; margin: 0 0 18px 0; }
  h2 { font-size: 26px; margin: 0 0 12px 0; }
  p { font-size: 16px; line-height: 24px; margin: 0 0 12px 0; }
  .cols { display: flex; gap: 28px; }
  .cols > div { flex: 1; }
  table { border-collapse: collapse; width: 100%%; font-size: 15px; }
  th, td { border: 1px solid #cccccc; padding: 5px 8px; text-align: left; }
  th { background: #eeeeee; }
  .grid { display: flex; flex-wrap: wrap; gap: 14px; }
  .grid img { width: 380px; height: 214px; }
  .plate { width: 1200px; height: 400px; }
  .big { font-size: 64px; margin: 0; }
  .mono { font-family: monospace; font-size: 13px; line-height: 18px; }
"""


def deck_html(seed):
    rng = random.Random(seed)
    body = []

    body.append(
        '<div class="slide"><h1>Benchmark deck, seed %d</h1>'
        "<p>%s</p><p>%s</p><p>%s</p></div>"
        % (seed, paragraph(rng, 14, 4), paragraph(rng, 14, 4), paragraph(rng, 14, 3))
    )
    body.append(
        '<div class="slide"><img class="plate" src="plate-a.png" alt="plate">'
        "<p>%s</p></div>" % paragraph(rng, 16, 2)
    )
    body.append(
        '<div class="slide"><h2>Two columns</h2><div class="cols">'
        "<div><p>%s</p><p>%s</p></div><div><p>%s</p><p>%s</p></div>"
        "</div></div>"
        % (paragraph(rng, 12, 5), paragraph(rng, 12, 5), paragraph(rng, 12, 5), paragraph(rng, 12, 5))
    )
    rows = []
    for i in range(16):
        rows.append(
            "<tr><td>row %02d</td><td>%s</td><td>%d.%02d</td><td>%d</td></tr>"
            % (i, rng.choice(("alpha", "beta", "gamma", "delta", "epsilon")), rng.randrange(1, 90),
               rng.randrange(0, 100), rng.randrange(100, 9999))
        )
    body.append(
        '<div class="slide"><h2>Table</h2><table><tr><th>key</th><th>group</th>'
        "<th>ratio</th><th>count</th></tr>%s</table></div>" % "".join(rows)
    )
    body.append(
        '<div class="slide"><div class="cols"><div><img src="plate-b.png" '
        'style="width:560px;height:315px" alt="plate"></div><div><h2>Caption</h2>'
        "<p>%s</p><p>%s</p></div></div></div>"
        % (paragraph(rng, 11, 4), paragraph(rng, 11, 4))
    )
    body.append(
        '<div class="slide"><h2>Grid</h2><div class="grid">'
        '<img src="plate-c.png" alt="plate"><img src="plate-d.png" alt="plate">'
        '<img src="plate-a.png" alt="plate"><img src="plate-b.png" alt="plate">'
        "</div></div>"
    )
    body.append(
        '<div class="slide"><h2>Monospace block</h2><div class="mono">%s</div></div>'
        % "<br>".join(
            "  %-14s %-10s %s" % (rng.choice(("band", "frame", "clip", "row")),
                                  rng.randrange(0, 9999), paragraph(rng, 6, 1))
            for _ in range(14)
        )
    )
    body.append(
        '<div class="slide"><h2>Totals</h2><p class="big">%d px</p>'
        "<p>%s</p><div class=\"cols\"><div><img src=\"plate-d.png\" "
        'style="width:560px;height:315px" alt="plate"></div><div><p>%s</p>'
        "<p>%s</p></div></div></div>"
        % (DOC_H, paragraph(rng, 13, 2), paragraph(rng, 12, 4), paragraph(rng, 12, 3))
    )

    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>bench deck</title>"
        "<style>%s</style></head><body>%s</body></html>\n"
        % (STYLE % {"w": SLIDE_W, "h": SLIDE_H}, "".join(body))
    )


def generate(outdir, seed=SEED_DEFAULT):
    """Write deck.html and its plates into `outdir`; return the HTML path."""
    os.makedirs(outdir, exist_ok=True)
    for name, index in PLATES:
        write_png(os.path.join(outdir, name), PLATE_W, PLATE_H, plate(seed, index))
    path = os.path.join(outdir, "deck.html")
    with open(path, "w") as fh:
        fh.write(deck_html(seed))
    return path


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "bench/deck"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else SEED_DEFAULT
    print(generate(target, seed))
