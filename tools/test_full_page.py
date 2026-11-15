#!/usr/bin/env python3
"""Contract test: a tall document must be reachable, and the default must not lie.

Single-image mode paints exactly `<width> x <height>` device pixels. A document
taller than that is CUT -- the PNG is valid, the exit code is 0, and before the
`content_h` field nothing on stderr said a third of the page was missing. The
README recommends that mode for "one-pagers and dashboards", which are precisely
the documents that are taller than a viewport.

`--full` asks for the document's own scroll height as the canvas. The default
stays the viewport so existing goldens do not move, which is why leg (a) is a
test and not an assumption: it pins the trap so a future change that silently
"fixes" it is visible.

Seven legs:

  a  default      2200px doc @ 600  -> 800x600, marker ABSENT   (the trap, pinned)
  b  --full       2200px doc @ 600  -> 800x2200, marker PRESENT (the escape hatch)
  c  --full       400px doc  @ 600  -> 800x600                  (never shrinks)
  d  --full       2200px doc        -> banded == single canvas  (streaming is not lossy)
  e  --full       position:fixed    -> warns, falls back        (cannot be banded)
  f  default      2200px doc @ 600  -> content_h=2200 named     (the cut is loud)
  g  default      400px doc  @ 600  -> content_h ABSENT         (the control for f)

Leg (g) is what makes (f) mean something: a substring check on a line the binary
always prints would pass on any document, so the shorter-than-canvas case proves
the field is conditional rather than constant.

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

    def render(html, out, extra, env=None):
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        return subprocess.run(
            [args.bin, html, out, str(VIEW_W), str(VIEW_H), "1", "--document"] + extra,
            capture_output=True,
            text=True,
            env=full_env,
        )

    def same_bytes(a, b):
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()

    leg("a-default", TALL, [], (VIEW_W, VIEW_H), False)
    leg("b-full", TALL, ["--full"], (VIEW_W, TALL), True)
    leg("c-short", SHORT, ["--full"], (VIEW_W, VIEW_H), True)

    # d — the streamed path must be the same image as the single canvas it
    # replaced. TALL is 2200 rows against a 1310-row band, so banding really
    # runs here (two bands); a leg on a one-band document would pass without
    # reaching the mechanism, which is this suite's own recorded vice.
    d_html = os.path.join(tmp, "d.html")
    with open(d_html, "w", encoding="utf-8") as fh:
        fh.write(tall_document(TALL))
    d_banded = os.path.join(tmp, "d-banded.png")
    d_single = os.path.join(tmp, "d-single.png")
    pd = render(d_html, d_banded, ["--full"])
    ps = render(d_html, d_single, ["--full"], env={"BLITZ_BAND_MIB": "0"})
    ok = (
        pd.returncode == 0
        and ps.returncode == 0
        and os.path.exists(d_banded)
        and os.path.exists(d_single)
        and same_bytes(d_banded, d_single)
    )
    legs.append((
        "d-banded",
        ok,
        f"banded rc={pd.returncode} single rc={ps.returncode} "
        f"identical={os.path.exists(d_banded) and os.path.exists(d_single) and same_bytes(d_banded, d_single)}",
    ))

    # e — a position:fixed element cannot be banded: blitz repaints it at every
    # band's top, which would stamp the header down the image. The renderer must
    # say so and fall back to exactly the single-canvas image.
    e_html = os.path.join(tmp, "e.html")
    with open(e_html, "w", encoding="utf-8") as fh:
        fh.write(
            '<!doctype html><html><body style="margin:0">'
            '<div style="position:fixed;top:0;left:0;width:100%;height:60px;background:#c00"></div>'
            '<div style="height:2200px;background:#eef"></div>'
            "</body></html>"
        )
    e_auto = os.path.join(tmp, "e-auto.png")
    e_off = os.path.join(tmp, "e-off.png")
    pa = render(e_html, e_auto, ["--full"])
    po = render(e_html, e_off, ["--full"], env={"BLITZ_BAND_MIB": "0"})
    warned = "position:fixed" in pa.stderr
    fell_back = (
        os.path.exists(e_auto) and os.path.exists(e_off) and same_bytes(e_auto, e_off)
    )
    legs.append((
        "e-fixed",
        pa.returncode == 0 and po.returncode == 0 and warned and fell_back,
        f"rc={pa.returncode} warned={warned} fell_back={fell_back}",
    ))

    # f — the cut must be LOUD. A tall document at the default canvas is still cut
    # and still exits 0 (leg a pins that), but the timing line now carries the
    # document's own height and stderr names the missing rows. Before this leg,
    # the only observable difference between a whole render and the top third of
    # one was the PNG's dimensions.
    f_html = os.path.join(tmp, "f.html")
    with open(f_html, "w", encoding="utf-8") as fh:
        fh.write(tall_document(TALL))
    f_out = os.path.join(tmp, "f.png")
    pf = render(f_html, f_out, [])
    f_has_h = f"content_h={TALL}" in pf.stderr
    f_has_c = f"canvas_h={VIEW_H}" in pf.stderr
    f_has_rows = str(TALL - VIEW_H) in pf.stderr
    f_ok = (
        pf.returncode == 0
        and f_has_h
        and f_has_c
        and "were cut" in pf.stderr
        and f_has_rows
    )
    legs.append((
        "f-signal",
        f_ok,
        f"rc={pf.returncode} content_h={f_has_h} canvas_h={f_has_c} "
        f"rows={f_has_rows}",
    ))

    # g — the control for f. A document SHORTER than the canvas is not cut, so the
    # same strings must be absent. Without this leg, `in stderr` on a string that
    # is always printed would pass and f would be decoration.
    g_html = os.path.join(tmp, "g.html")
    with open(g_html, "w", encoding="utf-8") as fh:
        fh.write(tall_document(SHORT))
    g_out = os.path.join(tmp, "g.png")
    pg = render(g_html, g_out, [])
    g_ok = (
        pg.returncode == 0
        and "content_h=" not in pg.stderr
        and "were cut" not in pg.stderr
    )
    legs.append((
        "g-nosignal",
        g_ok,
        f"rc={pg.returncode} content_h={'content_h=' in pg.stderr} "
        f"warned={'were cut' in pg.stderr}",
    ))

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
