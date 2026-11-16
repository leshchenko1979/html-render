#!/usr/bin/env python3
"""Generate the benchmark deck: a deterministic document every user can rebuild.

The deck is the fixture `tools/bench.py` measures, and it is also the argument
for why that measurement is worth taking: eight slides saying what a deck
actually is, what a browser is actually for, and what this project does and
does not claim. Given a seed it writes the same HTML and the same images, byte
for byte, on any host, so nobody has to take a figure on trust.

Three properties it must hold, and `tools/bench_selftest.py` checks them:

  * **Same seed, same bytes.** Every random draw comes from
    `random.Random(seed)` and every PNG is written with a fixed zlib level and
    no timestamp chunk, so the md5 of `deck.html` and of each image is a
    function of the seed alone.
  * **The seed still does something.** Accent colour, plate order and the pull
    quote are drawn from it, so two seeds cannot produce one document.
  * **Exactly `SLIDES * SLIDE_H` CSS pixels tall.** Each slide is a fixed-height
    box with `overflow: hidden`, and `html, body` have no margin. The probe
    asserts the rendered PNG's height equals `SLIDES * SLIDE_H * scale`, so a
    deck that grew a stray 4 px would fail loudly instead of being measured as a
    shorter document.

**No measured figure appears in this document, on purpose.** The deck's identity
is recorded in the receipt and the drift check refuses to compare receipts taken
on different documents. A figure quoted here would have to move whenever the
receipt was refreshed, which would make that check fire on every honest
re-measurement. The numbers live in the README, where `tools/check_claims.py`
holds them to the receipt; the deck argues, and points there.

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

# Seeded, so the seed is load-bearing without any of it being a claim.
ACCENTS = ["#1f4e79", "#7a2f2f", "#1d5c3a", "#5b3a72"]
QUOTES = [
    "The job is a picture of a document. Everything else is scaffolding.",
    "A browser is a general answer to a question nobody asked here.",
    "One document, rendered once, is not a workload that needs a process tree.",
    "If the input cannot vary, whatever varies is the renderer.",
]


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
                    # noise below lands on smooth and busy areas alike.
                    w = ((br * br - d) * 255) // (br * br * 4)
                    r = (r * (255 - w) + cr * w) // 255
                    g = (g * (255 - w) + cg * w) // 255
                    b = (b * (255 - w) + cb * w) // 255
            n = rng.randrange(-6, 7)
            row += bytes((max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n))))
        rows.append(row)
    return rows


STYLE = """
  html, body { margin: 0; padding: 0; background: #ffffff; }
  body { font-family: sans-serif; color: #111111; }
  .slide { width: %(w)dpx; height: %(h)dpx; overflow: hidden; padding: 52px;
           box-sizing: border-box; }
  .kicker { font-size: 14px; letter-spacing: 2px; text-transform: uppercase;
            color: %(accent)s; margin: 0 0 14px 0; }
  h1 { font-size: 54px; line-height: 62px; margin: 0 0 18px 0; }
  h2 { font-size: 36px; line-height: 44px; margin: 0 0 18px 0; }
  p { font-size: 20px; line-height: 31px; margin: 0 0 13px 0; }
  .lede { font-size: 24px; line-height: 37px; }
  .cols { display: flex; gap: 32px; }
  .cols > div { flex: 1; }
  table { border-collapse: collapse; width: 100%%; font-size: 15px; }
  th, td { border: 1px solid #cccccc; padding: 7px 9px; text-align: left; }
  th { background: #eeeeee; }
  .grid { display: flex; flex-wrap: wrap; gap: 16px; }
  .grid img { width: 284px; height: 160px; }
  .plate { width: 1184px; height: 333px; }
  .side { width: 540px; height: 304px; }
  .big { font-size: 76px; line-height: 86px; margin: 0 0 12px 0; color: %(accent)s; }
  .mono { font-family: monospace; font-size: 15px; line-height: 24px;
          background: #f5f5f5; border-left: 4px solid %(accent)s; padding: 14px 16px; }
  .pull { font-size: 30px; line-height: 41px; font-style: italic; color: %(accent)s;
          margin: 8px 0 0 0; }
"""


def _table(rows):
    head = "<tr><th>capability</th><th>needed to render a deck</th><th>what it brings with it</th></tr>"
    body = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (a, b, c) for a, b, c in rows
    )
    return "<table>%s%s</table>" % (head, body)


def _mono(lines):
    return '<div class="mono">%s</div>' % "<br>".join(
        line.replace("&", "&amp;").replace("<", "&lt;") for line in lines
    )


def deck_html(seed):
    rng = random.Random(seed)
    accent = rng.choice(ACCENTS)
    quote = rng.choice(QUOTES)
    order = list(PLATES)
    rng.shuffle(order)
    grid = "".join('<img src="%s" alt="plate">' % name for name, _ in order)

    body = []

    # 1 — what this document is
    body.append(
        '<div class="slide"><p class="kicker">benchmark fixture &middot; seed %d</p>'
        "<h1>Slides without a browser</h1>"
        '<p class="lede">A deck is a fixed-size document. Rendering one needs a '
        "layout engine and a rasteriser: not a scripting runtime, a network stack "
        "or a process tree.</p>"
        "<p>This document is the fixture <code>tools/bench.py</code> measures. It is "
        "generated from a seed, so every reader who clones the repository renders "
        "the identical bytes, and every figure in the README can be reproduced on "
        "their own machine.</p>"
        '<img class="plate" src="plate-a.png" alt="plate"></div>' % seed
    )

    # 2 — the shape of the problem
    body.append(
        '<div class="slide"><p class="kicker">the shape of the problem</p>'
        "<h2>A deck is a fixed-size document</h2>"
        '<div class="cols"><div>'
        "<p>Every slide has a known width and a known height, and a deck is a stack "
        "of them. The whole document therefore has a known size before the first "
        "pixel is painted.</p>"
        "<p>That is the entire contract: lay text and images out into boxes of fixed "
        "size, then rasterise. No layout depends on a user resizing a window, "
        "because there is no window.</p>"
        "<p>The hard parts are real. Line breaking, bidirectional text, font "
        "fallback and table layout all take serious work. They belong to the layout "
        "engine, not to the browser wrapped around it.</p>"
        '</div><div><img class="side" src="plate-b.png" alt="plate"></div></div></div>'
    )

    # 3 — capability audit
    body.append(
        '<div class="slide"><p class="kicker">capability audit</p>'
        "<h2>What a browser is actually for</h2>"
        + _table(
            [
                ("interactivity", "no", "an event loop and a scripting engine"),
                ("live navigation", "no", "session state, history, a back button"),
                ("remote content", "no", "a network stack and a certificate store"),
                ("sandboxing", "no", "a process per site, and IPC between them"),
                ("GPU compositing", "no", "a driver surface and its crash modes"),
                ("print fidelity", "no", "a second layout mode kept in step with the first"),
            ]
        )
        + "<p>None of these is the job. Each is a subsystem that has to be built, "
        "shipped, kept secure, and kept in step with the others.</p></div>"
    )

    # 4 — the cost
    body.append(
        '<div class="slide"><p class="kicker">the cost</p>'
        "<h2>The cost is the process tree</h2>"
        '<p class="big">one document, one process</p>'
        "<p>A browser is not one program. It is a browser process, plus a renderer "
        "per site, plus a GPU process, plus utility processes. What a host has to "
        "hold is the sum of them, not the largest.</p>"
        "<p>When the job is a single document rendered once, that structure is "
        "overhead with no counterpart in the work being done.</p>"
        '<p class="pull">%s</p></div>' % quote
    )

    # 5 — determinism
    body.append(
        '<div class="slide"><p class="kicker">determinism</p>'
        "<h2>The same document, the same bytes</h2>"
        "<p>A generated fixture makes the comparison about the renderer. Two hosts, "
        "two engines, one document: whatever differs is the engine, because the "
        "input cannot differ.</p>"
        + _mono(
            [
                "python3 tools/bench_deck.py bench/deck",
                "md5sum bench/deck/deck.html",
                "render bench/deck/deck.html out.png 1280 720 2 --document",
            ]
        )
        + "<p>The deck's own hash goes into the receipt, and the drift check refuses "
        "to compare two receipts taken on different documents: a changed deck would "
        "otherwise read as a faster or slower renderer.</p></div>"
    )

    # 6 — figures as code
    body.append(
        '<div class="slide"><p class="kicker">figures as code</p>'
        "<h2>A figure is a document, not a screenshot</h2>"
        "<p>Figures in papers and reports are usually drawn by hand and exported as "
        "images. Generated from code they become reviewable in a diff, rebuildable "
        "from the data, and identical on every machine that runs the generator.</p>"
        '<div class="grid">%s</div></div>' % grid
    )

    # 7 — limits
    body.append(
        '<div class="slide"><p class="kicker">limits</p>'
        "<h2>What this does not claim</h2>"
        "<p>It does not render interactive pages. There is no event loop, no "
        "scripting and no navigation.</p>"
        "<p>It is not a browser-compatibility guarantee. Where layout differs from "
        "Chromium, the difference is measured and published rather than hidden.</p>"
        "<p>It does not invent what it cannot find. A missing sub-resource, an "
        "oversized image or an uninstalled font is reported, not silently "
        "substituted.</p>"
        "<p>It does not replace a browser for browsing. It replaces one for "
        "producing a picture of a document.</p></div>"
    )

    # 8 — reproduce
    body.append(
        '<div class="slide"><p class="kicker">reproduce it</p>'
        "<h2>Run the probe yourself</h2>"
        "<p>Every figure the README prints comes from <code>tools/bench.py</code>, "
        "measured on a document this file generates. Clone the repository or "
        "download a release, and run it.</p>"
        + _mono(
            [
                "git clone https://github.com/leshchenko1979/pageplate",
                "python3 tools/bench.py --bin ./pageplate --repeats 3",
                "python3 tools/bench_selftest.py",
            ]
        )
        + "<p>Chromium is optional. With it, the probe measures both engines on the "
        "same document; without it, the probe records that the browser could not run "
        "and measures the renderer alone, saying so in the receipt.</p></div>"
    )

    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>bench deck</title>'
        "<style>%s</style></head><body>%s</body></html>\n"
        % (STYLE % {"w": SLIDE_W, "h": SLIDE_H, "accent": accent}, "".join(body))
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
