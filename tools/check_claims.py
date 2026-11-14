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

# tools/gates.py pads the browser's side of the clock: one 1.5 s settle wait
# plus 120 ms per element screenshot. html-render is synchronous and waits for
# nothing, so a ratio computed on raw wall clock credits us with the harness's
# own sleep. 22 % of Chromium's 13.36 s is not rendering.
SETTLE_S = 1.5 + DECK_SLIDES * 0.120

ASSETS = {
    # html, width, height, scale, committed png
    "hero": ("assets/hero.html", 1600, 900, 2, "assets/hero.png"),
    "og": ("assets/og.html", 1280, 640, 2, "assets/og.png"),
}


# ---------------------------------------------------------------------------
# Spelled-out counts. A number written in letters is still a claim.
# ---------------------------------------------------------------------------
# `claim()` below looks for a computed value ANYWHERE in the text, which is the
# right test for a single figure and the wrong one for a fact stated twice: the
# README said "pins the four known divergences" while `parity_baseline.json`
# pinned eleven, and every claim passed because "11 divergent boxes" appears
# further up the same file. A digit-boundary regex cannot see a number spelled
# in letters at all, so the words are enumerated here and read against the same
# baseline the digits come from.
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# Only the "<n> [known] divergence(s)" shape is read, because only that shape
# states the PINNED TOTAL. The sentence that splits eleven into four and seven
# legitimately names both numbers, and a guard that fired on every spelled
# number near the word "divergence" would be red on a correct page — a guard
# that cries wolf gets muted, which is how this one would die.
PINNED_DIVERGENCES = re.compile(
    r"\b(%s|\d+)\s+(?:known\s+)?divergences?\b" % "|".join(WORD_NUMBERS),
    re.IGNORECASE,
)


def pinned_divergence_counts(text):
    """Every total stated as "<n> [known] divergence(s)", as integers."""
    out = []
    for match in PINNED_DIVERGENCES.finditer(text):
        token = match.group(1).lower()
        out.append(WORD_NUMBERS[token] if token in WORD_NUMBERS else int(token))
    return out


def selftest():
    """Prove the spelled-count guard fires on the defect it was written for.

    A guard nobody has seen fail is indistinguishable from a guard that cannot
    fail, so the historical text is kept here as the positive control.
    """
    cases = [
        ("historical defect (README L559)",
         "pins the four\nknown divergences by `fixture:colour`", [4]),
        ("corrected prose",
         "pins every divergent box by `fixture:colour`", []),
        ("the 4 + 7 split is not a pinned total",
         "The 11 divergent boxes are two open defects: four from consecutive "
         "`<br>` runs and seven from table layout", []),
        ("digit form is read too",
         "the 11 known divergences", [11]),
        ("singular and plural both read",
         "four divergences", [4]),
    ]
    bad = 0
    for name, text, want in cases:
        got = pinned_divergence_counts(text)
        ok = got == want
        bad += 0 if ok else 1
        print("%-44s %s  %r" % (name, "ok" if ok else "FAIL", got))
    print("\n%d/%d selftest case(s) hold" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


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
    ap.add_argument("--selftest", action="store_true",
                    help="prove the spelled-count guard fires, then exit")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

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
    # The same speed ratio with the harness's own settle sleep taken out, which
    # the README states beside the raw one. Memory is unaffected: sleeping
    # allocates nothing, so there is no equivalent correction on that row.
    claim("README", readme, ratio(CHROME_WALL_S - SETTLE_S, SCROLL_WALL_S),
          "(13.36 - settle)/5.37")

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
    claim("GATES", gates, pct, "3,551/44,236,800")
    claim("GATES", gates, "{:,}".format(DIFF_PIXELS), "pixels differing")
    claim("GATES", gates, "{:,}".format(TOTAL_PIXELS), "12 x 2560 x 1440")
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
    # "Static" was a lie the tree told twice: the release template called the CLI
    # "Static Blitz renderer CLI" and the README offered "a 25 MB static binary"
    # as a reason to pick it over wkhtmltoimage. ldd on the shipped asset lists 13
    # dynamic libraries, so both sentences are false. Guard the exact phrases: a
    # bare "static" is a CSS keyword and cannot be banned.
    for retired in ("no runtime deps", "tree-summed", "Static Blitz",
                    "static binary"):
        checked += 1
        hits = [f for f, t in (("README.md", readme), ("GATES.md", gates),
                               (".github/workflows/release.yml", release))
                if retired in t]
        if hits:
            fails.append("retired claim %r survives in %s" % (retired, ", ".join(hits)))

    # --- the version the page claims to document ----------------------------
    # The Status line and the install snippet name a release. Both are copies of
    # Cargo.toml, and both drifted: after beta.6 was cut the page still said
    # beta.5 in two places, so a reader following the install snippet would
    # download an artefact the page had just called stale. Derived from
    # Cargo.toml, so a bump that forgets the page fails here instead of shipping.
    version = ""
    for line in read("Cargo.toml").splitlines():
        if line.startswith("version = "):
            version = line.split('"')[1]
            break
    if not version:
        fails.append("Cargo.toml carries no version line")
    else:
        claim("README", readme, "`%s`, one maintainer" % version, "Cargo.toml version")
        claim("README", readme, "V=v%s" % version, "Cargo.toml version, install tag")
        claim("README", readme, "N=html-render_%s_linux_amd64" % version,
              "Cargo.toml version, asset name")

    # --- the corpus verdict ------------------------------------------------
    # These numbers drifted once already: README said "47 boxes ... 4 divergent"
    # while the baseline pinned 54 and 11, and both were "measured". They are
    # derived here from the same file the gate reads, so prose and gate cannot
    # disagree without this failing. The unit is a verdict ROW, not a box: the
    # absence assertion has no box, which is why 42 + 11 + 1 sums to the 54
    # rows the line quotes while only 53 of them are boxes.
    import json as _json
    baseline = _json.load(open(os.path.join(ROOT, "tools", "parity_baseline.json"),
                               encoding="utf-8"))
    rows = baseline["text_free_rows"]
    divergent = len(baseline["divergent"])
    absences = len(baseline["absence"])
    exact = rows - divergent - absences
    claim("GATES", gates, "text-free (%d rows): %d exact, 0 within 1px, "
          "%d divergent, 0 missing, %d absence" % (rows, exact, divergent, absences),
          "tools/parity_baseline.json")
    claim("GATES", gates, "%d divergent boxes are two open defects" % divergent,
          "baseline divergence count")
    # The baseline pins a fixture count too; a fixture added without the prose
    # following leaves a page describing a corpus it no longer runs.
    claim("README", readme, str(baseline["fixtures"]), "baseline fixture count")
    # A count written in letters is a claim too, and `claim()` above cannot see
    # one. Read every "N [known] divergence(s)" total in both files against the
    # baseline, so a second sentence about the same fact cannot drift while the
    # first one keeps the digit claim green.
    for where, text in (("README", readme), ("GATES", gates)):
        checked += 1
        for stated in pinned_divergence_counts(text):
            if stated != divergent:
                fails.append("%s states %d pinned divergence(s); the baseline "
                             "pins %d" % (where, stated, divergent))

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
