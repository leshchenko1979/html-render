#!/usr/bin/env python3
"""Recompute every derived number the docs quote, and fail when one drifts.

README.md, GATES.md and the release-body template in
`.github/workflows/release.yml` all quote ratios, percentages and pixel counts
derived from a handful of measurements. Prose ratios rot silently: the source
numbers get re-measured and the "2.49x" three paragraphs away keeps its old
value because nothing multiplies them again.

So the inputs live here as constants, every derived figure is computed from
them, and each computed figure is asserted to appear in the file that quotes
it. Change a measurement without changing the prose and this exits 1.

    python3 tools/check_claims.py
    python3 tools/check_claims.py --bin target/release/render   # also re-render assets

Exit 0 = every quoted number is the one the inputs produce.
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# The measurements. Each is the number as measured, with the instrument named
# beside it so a reader can tell a re-measurable figure from a remembered one.
# ---------------------------------------------------------------------------
CHROME_WALL_S = 13.36        # 12-slide 4.16 MB deck, Playwright, 12 shots, dsf=2
SCROLL_WALL_S = 5.37         # same deck, html-render --scroll
CLIP_WALL_S = 5.72           # same deck, html-render --clip
CHROME_RSS_MIB = 858.0       # largest single process (/usr/bin/time -f %M)
SCROLL_RSS_MIB = 93.4        # largest single process; html-render is 1 process
CLIP_RSS_MIB = 275.2         # largest single process

# assets/demo-deck.html, 2560x1440, scale 2, four runs each, tools/measure_rss.py
TREE_RUNS = (1703.5, 1826.2)
MAX_RUNS = (297.1, 299.2)

DIFF_PIXELS = 3551           # --scroll vs --clip, 12 slides
TOTAL_PIXELS = 44236800      # 12 x 2560 x 1440

DECK_MB = "4.16"             # the external deck the cost table was taken on
DECK_SLIDES = 12
FIDELITY_PCT = "4.13"        # whole deck, pixels off by >16, from tools/gates.py

ASSETS = {
    # html, width, height, scale, committed png
    "hero": ("assets/hero.html", 1600, 900, 2, "assets/hero.png"),
    "og": ("assets/og.html", 1280, 640, 2, "assets/og.png"),
}


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def png_size(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        head = fh.read(33)
    return struct.unpack(">II", head[16:24])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", default=None, help="also re-render the assets with this binary")
    args = ap.parse_args()

    readme = read("README.md")
    gates = read("GATES.md")
    release = read(".github/workflows/release.yml")

    fails = []
    checked = 0

    def claim(where, text, computed, note):
        """Assert `computed` — derived here from the inputs — appears in `text`.

        There is deliberately no "expected" argument: a hand-typed expectation
        is a second copy of the prose, and it agrees with the prose whether or
        not either is right. The only acceptable expectation is the arithmetic.
        """
        nonlocal checked
        checked += 1
        # Boundary-anchored, not `in`: a plain substring lets "2.4" pass on
        # "2.49" and "2.49" pass inside "12.491", which is how a drift check
        # reports green while the prose says something else. Digits and a
        # decimal point on either side disqualify the match.
        pattern = r"(?<![\d.,])" + re.escape(computed) + r"(?![\d])"
        if not re.search(pattern, text):
            fails.append("%s should quote %r (%s) and does not"
                         % (where, computed, note))

    def ratio(a, b, places=2):
        return "%.*f" % (places, a / b)

    # --- speed and memory ratios -------------------------------------------
    claim("README", readme, ratio(CHROME_WALL_S, CLIP_WALL_S), "13.36/5.72")
    claim("README", readme, ratio(CHROME_RSS_MIB, CLIP_RSS_MIB), "858/275.2")
    claim("README", readme, ratio(CHROME_WALL_S, SCROLL_WALL_S), "13.36/5.37")
    claim("README", readme, ratio(CHROME_RSS_MIB, SCROLL_RSS_MIB), "858/93.4")

    # The single-process undercount, quoted as a range by README, GATES and the
    # release body. Both ends come from the same four runs.
    lo = ratio(min(TREE_RUNS), max(MAX_RUNS), 1)
    hi = ratio(max(TREE_RUNS), min(MAX_RUNS), 1)
    claim("README", readme, "%s–%s" % (lo, hi), "tree/max range")
    claim("GATES", gates, "%s–%s" % (lo, hi), "tree/max range")
    claim("release body", release, "%s-%s" % (lo, hi), "tree/max range")

    # html-render against itself: the 3x is a per-process comparison and holds.
    claim("GATES", gates, "%d×" % round(CLIP_RSS_MIB / SCROLL_RSS_MIB),
          "275.2/93.4 rounds to 3")

    # --- pixel counts ------------------------------------------------------
    pct = "%.4f" % (DIFF_PIXELS * 100.0 / TOTAL_PIXELS)
    claim("README", readme, pct, "3,551/44,236,800")
    claim("GATES", gates, pct, "3,551/44,236,800")
    claim("README", readme, "{:,}".format(DIFF_PIXELS), "pixels differing")
    claim("README", readme, "{:,}".format(TOTAL_PIXELS), "12 x 2560 x 1440")
    # 12 slides x 2560 x 1440 must be the number the docs call the total. This
    # one has no prose to match, so it is a bare assertion rather than a claim.
    checked += 1
    if DECK_SLIDES * 2560 * 1440 != TOTAL_PIXELS:
        fails.append("TOTAL_PIXELS %d is not %d x 2560 x 1440 = %d"
                     % (TOTAL_PIXELS, DECK_SLIDES, DECK_SLIDES * 2560 * 1440))
    claim("README", readme, FIDELITY_PCT, "gates.py whole-deck fidelity")
    claim("GATES", gates, FIDELITY_PCT, "gates.py whole-deck fidelity")
    claim("release body", release, FIDELITY_PCT, "gates.py whole-deck fidelity")

    # --- the deck every cost figure was measured on -------------------------
    claim("README", readme, DECK_MB, "external deck size")
    claim("GATES", gates, DECK_MB, "external deck size")

    # --- assets: the committed PNG must be the command's output ------------
    for name, (html, w, h, scale, png) in ASSETS.items():
        checked += 1
        got = png_size(png)
        want = (w * scale, h * scale)
        if got != want:
            fails.append("%s is %dx%d but its documented command renders %dx%d"
                         % (png, got[0], got[1], want[0], want[1]))
        # The prose must state the size the file actually has.
        checked += 1
        literal = "%d×%d" % got
        if literal not in readme:
            fails.append("README never states %s's real size %s" % (png, literal))

    # The usage line quoted beside the hero image must be the one that produced it.
    checked += 1
    hero_cmd = "html-render assets/hero.html hero.png 1600 900 2 --document"
    if hero_cmd not in readme:
        fails.append("README does not quote the hero command verbatim: %r" % hero_cmd)

    # --- claims that must be GONE ------------------------------------------
    for retired in ("no runtime deps", "tree-summed"):
        checked += 1
        hits = [f for f, t in (("README.md", readme), ("GATES.md", gates),
                               (".github/workflows/release.yml", release))
                if retired in t]
        if hits:
            fails.append("retired claim %r survives in %s" % (retired, ", ".join(hits)))

    # --- optional: re-render the assets and compare -------------------------
    if args.bin:
        for name, (html, w, h, scale, png) in ASSETS.items():
            checked += 1
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "a.png")
                subprocess.run([args.bin, os.path.join(ROOT, html), out,
                                str(w), str(h), str(scale), "--document"],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                same = open(out, "rb").read() == open(os.path.join(ROOT, png), "rb").read()
            if not same:
                fails.append("%s no longer re-renders byte-identically: the "
                             "committed image is stale or the renderer changed" % png)

    print("checked %d claim(s) across README.md, GATES.md and the release template"
          % checked)
    if fails:
        print("\nFAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
