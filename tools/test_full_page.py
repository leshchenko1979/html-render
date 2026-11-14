#!/usr/bin/env python3
"""Contract test: a tall document must be reachable, and the default must not lie.

Single-image mode paints exactly `<width> x <height>` device pixels. A document
taller than that is CUT -- the PNG is valid, the exit code is 0, and nothing on
stderr says a third of the page is missing. The README recommends that mode for
"one-pagers and dashboards", which are precisely the documents that are taller
than a viewport.

`--full` asks for the document's own scroll height as the canvas. The default
stays the viewport so existing goldens do not move, which is why leg (a) is a
test and not an assumption: it pins the trap so a future change that silently
"fixes" it is visible.

Three legs:

  a  default      2200px doc @ 600  -> 800x600, marker ABSENT   (the trap, pinned)
  b  --full       2200px doc @ 600  -> 800x2200, marker PRESENT (the escape hatch)
  c  --full       400px doc  @ 600  -> 800x600                  (never shrinks)

Usage:
    python3 tools/test_full_page.py --bin /path/to/html-render
    python3 tools/test_full_page.py --bin /path/to/old-binary   # a passes, b fails
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure import read_png  # noqa: E402

VIEW_W, VIEW_H = 800, 600
TALL = 2200
SHORT = 400


def tall_document(height):
    """A document of `height` CSS px, with a red marker as its last 200 px."""
    body = height - 200
    rows = []
    for y in range(0, body, 100):
        rows.append(f'<div style="height:100px;background:#eef"></div>')
    rows.append('<div style="height:200px;background:#c00"></div>')
    return (
        '<!doctype html><html><body style="margin:0">'
        + "".join(rows)
        + "</body></html>"
    )


def red_pixels(path):
    """Count strongly-red pixels; the marker is 800x200 of #c00."""
    _w, _h, channels, px = read_png(path)
    step = max(1, channels)
    if step < 3:
        return 0
    n = 0
    for i in range(0, len(px), step):
        if px[i] > 150 and px[i + 1] < 80 and px[i + 2] < 80:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the html-render binary")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir")
    args = ap.parse_args()

    if not os.path.exists(args.bin):
        print(f"FAIL: no such binary: {args.bin}")
        return 1

    tmp = tempfile.mkdtemp(prefix="fullpage-")
    legs = []

    def leg(name, doc_height, extra, want_dims, want_marker):
        html = os.path.join(tmp, f"{name}.html")
        out = os.path.join(tmp, f"{name}.png")
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(tall_document(doc_height))
        proc = subprocess.run(
            [args.bin, html, out, str(VIEW_W), str(VIEW_H), "1", "--document"] + extra,
            capture_output=True,
            text=True,
        )
        if not os.path.exists(out):
            legs.append((name, False, f"no output file (rc={proc.returncode})"))
            return
        w, h, _ch, _px = read_png(out)
        marker = red_pixels(out) > 1000
        ok = (w, h) == want_dims and marker == want_marker
        legs.append(
            (
                name,
                ok,
                f"{w}x{h} wanted {want_dims[0]}x{want_dims[1]} "
                f"marker={marker} wanted={want_marker} rc={proc.returncode}",
            )
        )

    leg("a-default", TALL, [], (VIEW_W, VIEW_H), False)
    leg("b-full", TALL, ["--full"], (VIEW_W, TALL), True)
    leg("c-short", SHORT, ["--full"], (VIEW_W, VIEW_H), True)

    failed = 0
    for name, ok, detail in legs:
        print(f"{'ok  ' if ok else 'FAIL'} {name:12s} {detail}")
        failed += 0 if ok else 1

    print()
    if failed:
        print(f"{failed} of {len(legs)} legs FAIL")
        if not args.keep:
            subprocess.run(["rm", "-rf", tmp], check=False)
        return 1
    print(f"all {len(legs)} legs hold")
    if args.keep:
        print(f"kept: {tmp}")
    else:
        subprocess.run(["rm", "-rf", tmp], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
