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
import json as _json
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
# There are no measurement constants here any more, deliberately. Every figure
# the docs advertise is read from `bench/results.json`, which is the raw output
# of a CI run of `tools/bench.py`. Two reasons that matters more than it looks:
#
#   * a constant is a second copy of a measurement, and a second copy drifts —
#     this file used to carry 13.36 s and 858 MiB while the probe that produced
#     them lived nowhere a reader could run it;
#   * a constant cannot tell you whether the measurement still EXISTS. Reading
#     the receipt means a page that claims fidelity against Chromium fails the
#     guard when the receipt says Chromium was unavailable, instead of quoting a
#     figure from a run that never measured the other side.
#
# `bench/results.json` is never edited by hand: `bench/README.md` records the run
# id it came from, and `tools/bench.py` writes it.
RECEIPT = os.path.join("bench", "results.json")

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


def num(x):
    """858.0 -> '858', 93.4 -> '93.4': the docs drop a trailing .0."""
    text = ("%f" % x).rstrip("0").rstrip(".")
    return text


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

    # --- every figure below comes from the committed receipt ---------------
    # `bench/results.json` is a CI run of `tools/bench.py`: the same deck,
    # rendered once by each engine, wall clock a median of three, memory the
    # peak of the whole process tree. Nothing here is typed in by hand.
    receipt_path = os.path.join(ROOT, RECEIPT)
    try:
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = _json.load(fh)
    except OSError as exc:
        fails.append("cannot read %s (%s): every advertised figure comes from it"
                     % (RECEIPT, exc))
        receipt = None

    if receipt is not None:
        doc = receipt["engines"]["html-render"]
        chrome = receipt["engines"]["chromium"]
        shapes = receipt["shapes"]["slides"]
        clip = shapes["html-render-clip"]
        per_el = shapes["html-render-per-element"]

        # A figure about a comparison is only meaningful if the other side was
        # actually measured. This is the assertion the old constants could not
        # make: they quoted 4.13 % against Chromium whether or not any host had
        # managed to run Chromium.
        checked += 1
        if chrome.get("status") != "measured":
            fails.append(
                "the receipt says chromium is %r (%s), so no ratio or fidelity "
                "figure in the docs can be backed"
                % (chrome.get("status"), chrome.get("reason"))
            )
        else:
            # A receipt carries two copies of the same measurement: the raw
            # repeats in `wall_s` and the `wall_median_s` the docs quote. Only
            # the median was asserted, so a receipt whose repeats say 3.7 and
            # whose median says 1.0 would have backed every ratio in the docs
            # with a number no run produced. Perturbing `wall_s` alone proved it:
            # the guard stayed green. Assert the arithmetic instead of trusting
            # it, and hold the peak to its own maximum.
            for name, rec in receipt["engines"].items():
                reps = rec.get("wall_s")
                if rec.get("status") == "measured" and reps:
                    checked += 1
                    if sorted(reps)[len(reps) // 2] != rec.get("wall_median_s"):
                        fails.append(
                            "receipt engines.%s wall_median_s %r is not the "
                            "median of its own repeats %r"
                            % (name, rec.get("wall_median_s"), reps))
                peak, peak_max = rec.get("peak_tree_mib"), rec.get("peak_tree_mib_max")
                if peak is not None and peak_max is not None:
                    checked += 1
                    if peak > peak_max:
                        fails.append(
                            "receipt engines.%s peak_tree_mib %r exceeds its own "
                            "peak_tree_mib_max %r" % (name, peak, peak_max))
            for name, rec in shapes.items():
                peak, peak_max = rec.get("peak_tree_mib"), rec.get("peak_tree_mib_max")
                if peak is not None and peak_max is not None:
                    checked += 1
                    if peak > peak_max:
                        fails.append(
                            "receipt shapes.%s peak_tree_mib %r exceeds its own "
                            "peak_tree_mib_max %r" % (name, peak, peak_max))

            for label, rec in (
                ("document", doc),
                ("--clip", clip),
                ("--per-element", per_el),
            ):
                claim("README", readme, ratio(chrome["wall_median_s"],
                                              rec["wall_median_s"]),
                      "receipt wall ratio, %s" % label)
                claim("README", readme, ratio(chrome["peak_tree_mib"],
                                              rec["peak_tree_mib"]),
                      "receipt tree-RSS ratio, %s" % label)

            # The wall and RSS medians themselves, which the tables print.
            for label, rec in (("document", doc), ("--clip", clip),
                               ("--per-element", per_el)):
                claim("README", readme, num(rec["wall_median_s"]),
                      "receipt wall median, %s" % label)
                claim("README", readme, num(rec["peak_tree_mib"]),
                      "receipt peak tree RSS, %s" % label)
            claim("README", readme, num(chrome["wall_median_s"]), "receipt chromium wall")
            claim("README", readme, num(chrome["peak_tree_mib"]),
                  "receipt chromium peak tree RSS")

            fid = receipt["fidelity"]
            claim("README", readme, num(fid["over16_pct"]), "receipt fidelity")
            claim("GATES", gates, num(fid["over16_pct"]), "receipt fidelity")
            claim("release body", release, num(fid["over16_pct"]), "receipt fidelity")
            claim("GATES", gates, "{:,}".format(fid["pixels"]),
                  "receipt fidelity pixel count")
            claim("README", readme, "{:,}".format(fid["pixels"]),
                  "receipt fidelity pixel count")

            # The asset HTMLs bake the same two ratios by hand, as a tile
            # caption and as a bar width. Same rule: read from the receipt.
            hero = ASSETS["hero"][0]
            hero_text = read(hero)
            for name, (html, _w, _h, _scale, _png) in ASSETS.items():
                text = read(html)
                claim(html, text, ratio(chrome["wall_median_s"],
                                        per_el["wall_median_s"]),
                      "receipt wall ratio, faster tile")
                claim(html, text, "%s s vs %s s" % (num(per_el["wall_median_s"]),
                                                    num(chrome["wall_median_s"])),
                      "receipt wall medians, caption")
                claim(html, text, ratio(chrome["peak_tree_mib"],
                                        per_el["peak_tree_mib"]),
                      "receipt tree-RSS ratio, memory tile")
                claim(html, text, "%s vs %s MiB" % (num(per_el["peak_tree_mib"]),
                                                    num(chrome["peak_tree_mib"])),
                      "receipt tree-RSS medians, caption")
            claim(hero, hero_text,
                  "%.1f%%" % (100.0 * per_el["wall_median_s"] / chrome["wall_median_s"]),
                  "bar width, wall")
            claim(hero, hero_text,
                  "%.1f%%" % (100.0 * per_el["peak_tree_mib"] / chrome["peak_tree_mib"]),
                  "bar width, RSS")
            claim(hero, hero_text, num(fid["over16_pct"]), "receipt fidelity")

    # --- the deck the receipt was taken on ---------------------------------
    # These are properties of the generator, so they are asserted rather than
    # quoted: if the deck changes shape, the receipt describes a document that
    # no longer exists and every figure in it is about the wrong thing.
    checked += 1
    if receipt is not None:
        deck = receipt["deck"]
        want_px = [deck["css_px"][0] * deck["scale"], deck["css_px"][1] * deck["scale"]]
        if deck["expected_px"] != want_px:
            fails.append("receipt expected_px %r != css_px * scale %r"
                         % (deck["expected_px"], want_px))
        checked += 1
        if deck["slides"] != receipt["shapes"]["slides"]["html-render-clip"]["pngs"]:
            fails.append("receipt deck has %d slides but the --clip run wrote %d PNGs"
                         % (deck["slides"],
                            receipt["shapes"]["slides"]["html-render-clip"]["pngs"]))

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

        # --- the asset HTML's version kicker --------------------------------
        # The PNG checks above prove each committed image is its HTML's output,
        # and nothing more. The HTML also states a version by hand, and that had
        # already drifted: both kickers read v0.3.0-beta.6 while the tree was on
        # beta.10, with every gate green, because the PNG was compared to its
        # HTML and the HTML was compared to nothing. The measurement figures in
        # these files are asserted in the receipt block above.
        for name, (html, _w, _h, _scale, _png) in ASSETS.items():
            claim(html, read(html), "v%s" % version, "Cargo.toml version, kicker")

    # --- the corpus verdict ------------------------------------------------
    # These numbers drifted once already: README said "47 boxes ... 4 divergent"
    # while the baseline pinned 54 and 11, and both were "measured". They are
    # derived here from the same file the gate reads, so prose and gate cannot
    # disagree without this failing. The unit is a verdict ROW, not a box: the
    # absence assertion has no box, which is why 42 + 11 + 1 sums to the 54
    # rows the line quotes while only 53 of them are boxes.
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
