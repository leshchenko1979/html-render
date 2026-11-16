#!/usr/bin/env python3
"""Recompute every derived number the docs quote, and fail when one drifts.

README.md, GATES.md and the release-body template in
`.github/workflows/release.yml` all quote ratios, percentages and pixel counts
derived from a handful of measurements. Prose ratios rot silently: the source
numbers get re-measured and the "2.49x" three paragraphs away keeps its old
value because nothing multiplies them again.

So the inputs live in `bench/results.json` — a CI run of `tools/bench.py`,
committed unedited — every derived figure is computed from them, and each
computed figure is asserted to appear in the file that quotes it. Change a
measurement without changing the prose and this exits 1. Nothing here is typed
in by hand, so prose and gate cannot agree on a figure the receipt does not
contain.

    python3 tools/check_claims.py
    python3 tools/check_claims.py --bin target/release/render   # also re-render assets

Exit 0 = every quoted number is the one the inputs produce.
"""

import argparse
import json as _json
import math
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

    def claim_prose(where, text, computed, note):
        """Assert `computed` appears in `text`, ignoring how the prose wraps.

        Prose in this repo is reflowed by hand, so a claim that encodes where a
        line happens to break fails for a reason unrelated to the figure — and
        the temptation, once it does, is to weaken the claim rather than fix the
        wrap. Collapsing whitespace on both sides removes the question: the
        claim is about words and digits, not about layout.
        """
        claim(where, " ".join(text.split()), " ".join(computed.split()), note)

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

        # The provenance table in `bench/README.md` is a set of claims about
        # this receipt, and it was hand-typed: it named run 43744837000 measured
        # at 18:08:34Z while the committed receipt was taken at 18:28:59Z by a
        # later run. A reader following it would have downloaded a different
        # measurement, which is the one thing a provenance table must never do.
        bench_readme = read(os.path.join("bench", "README.md"))
        claim("bench/README.md", bench_readme, receipt["generated_at"],
              "the instant this receipt was measured")
        claim("bench/README.md", bench_readme, receipt["probe"]["version"],
              "the binary that was measured")
        claim("bench/README.md", bench_readme,
              receipt["probe"]["sha256"][:8] + "\u2026",
              "the probe's own digest")
        claim("bench/README.md", bench_readme, receipt["host"]["hostname"],
              "the host that measured it")
        claim("bench/README.md", bench_readme,
              "%d vCPU" % receipt["host"]["cpu_count"],
              "the vCPU count the probe saw")
        claim("bench/README.md", bench_readme,
              "{:,} MiB".format(receipt["host"]["mem_total_mib"]),
              "the RAM the probe saw")
        claim("bench/README.md", bench_readme,
              receipt["engines"]["chromium"]["version"],
              "the browser that was measured against")
        if "run" in receipt:
            claim("bench/README.md", bench_readme, receipt["run"]["id"],
                  "the workflow run that produced this receipt")
            claim("bench/README.md", bench_readme, receipt["run"]["sha"][:7],
                  "the commit that run measured")

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

            # Both docs print the same six ratios and six medians. GATES.md
            # printed them unchecked: its §Cost table could have drifted for a
            # whole release with every gate green, which is the failure this
            # file exists to prevent.
            #
            # Assert whole ROWS, never bare numbers. A refreshed receipt can
            # give two shapes the same ratio — this one reads 1.42x for both
            # `--full` and `--per-element` — and a bare `claim("1.42")` is then
            # satisfied by either row. Perturbing one row of the GATES table
            # proved it: the guard stayed green while the table disagreed with
            # itself. A row string is unique per shape, so a drift cannot hide
            # behind its twin.
            def readme_rss_row(label, rec):
                return "| peak RSS, `%s` | %s MiB | %s MiB | **%s\u00d7** |" % (
                    label, num(chrome["peak_tree_mib"]), num(rec["peak_tree_mib"]),
                    ratio(chrome["peak_tree_mib"], rec["peak_tree_mib"]))

            for label, rec in (("--full", doc), ("--clip", clip),
                               ("--per-element", per_el)):
                claim("README", readme, readme_rss_row(label, rec),
                      "cost row, %s tree RSS" % label)

            # Peak RSS leads the GATES table; wall clock is the last column and
            # carries no ratio, because it is measured and gated but no longer
            # advertised (see GATES.md §Withdrawn claims).
            claim("GATES", gates,
                  "| Chromium, one tall screenshot cut into eight slides | %s MiB | %s s |"
                  % (num(chrome["peak_tree_mib"]), num(chrome["wall_median_s"])),
                  "cost row, chromium")
            claim("GATES", gates,
                  "| `--full` | **%s MiB** | %s s |"
                  % (num(doc["peak_tree_mib"]), num(doc["wall_median_s"])),
                  "cost row, --full")
            claim("GATES", gates,
                  "| `--clip` (banded, one PNG per frame) | %s MiB | %s s |"
                  % (num(clip["peak_tree_mib"]), num(clip["wall_median_s"])),
                  "cost row, --clip")
            claim("GATES", gates,
                  "| `--clip --per-element` | %s MiB | %s s |"
                  % (num(per_el["peak_tree_mib"]), num(per_el["wall_median_s"])),
                  "cost row, --per-element")

            # The history table quotes earlier runs, which this receipt cannot
            # verify. Its LAST row is this run, and that row must agree with the
            # receipt — it is the row a refresh forgets, and the one that read
            # 1.61x for a measurement of 1.6046.
            # The row carries the deck it was measured on, because the table
            # spans a deck change: four rows on one document, the last on
            # another. A refresh that moved the figures but left the deck cell
            # behind would make the table read as a like-for-like comparison of
            # two different documents, which is the one thing it must not do.
            claim("GATES", gates,
                  "| %s | `%s` | %s MiB | %s MiB | %s s | %s s |"
                  % (receipt["run"]["id"],
                     (receipt["deck"].get("html_md5") or "")[:8],
                     num(chrome["peak_tree_mib"]), num(doc["peak_tree_mib"]),
                     num(chrome["wall_median_s"]), num(doc["wall_median_s"])),
                  "history table, this run")


            # Memory is advertised as a FLOOR, and the floor is derived, so the
            # prose cannot round a measurement up: it prints the integer below
            # the measured ratio, which every run of the probe matches or beats.
            # TWO floors, because the modes differ by 1.6x: quoting the best
            # mode as "the claim" would overstate the banded one, which is the
            # least favourable and therefore the honest headline.
            mem_ratio = chrome["peak_tree_mib"] / per_el["peak_tree_mib"]
            mem_floor = int(math.floor(mem_ratio))
            worst_ratio = chrome["peak_tree_mib"] / clip["peak_tree_mib"]
            worst_floor = int(math.floor(worst_ratio))
            if worst_floor > mem_floor:
                fails.append("the advertised mode is not the least favourable: "
                             "%d\u00d7 against %d\u00d7" % (mem_floor, worst_floor))
            claim("README", readme, "At least %d\u00d7 less in the least favourable mode"
                  % worst_floor, "floor of the least favourable mode")
            claim("README", readme, "%d\u00d7 in the advertised one" % mem_floor,
                  "floor of the advertised mode")
            # The floor is quoted a SECOND time on each of three surfaces, in
            # lowercase prose rather than as the headline claim. Those copies
            # went stale in exactly the refresh that moved 51x to 31x: the
            # guarded sentence said 31x while the summary three paragraphs
            # earlier still promised 32x. Claim every copy, not the headline.
            claim_prose("README", readme,
                        "**at least %d\u00d7 less memory**" % worst_floor,
                        "floor, restated in the README intro")
            claim_prose("GATES", gates,
                        "at least %d\u00d7 \u2014 which held to" % worst_floor,
                        "floor, restated in the withdrawn-claims section")
            claim_prose("bench/README", bench_readme,
                        "at least %d\u00d7, taking the" % worst_floor,
                        "floor, restated in the receipt README")

            fid = receipt["fidelity"]
            claim("README", readme, num(fid["over16_pct"]), "receipt fidelity")
            claim("GATES", gates, num(fid["over16_pct"]), "receipt fidelity")
            claim("release body", release, num(fid["over16_pct"]), "receipt fidelity")
            # The release body quotes the cost figures too, and it is the copy
            # nobody re-reads: it advertised 1.60x / 65x from an older run long
            # after the README had moved. Speed is gone from it; memory is
            # claimed here so a stale release note fails the build.
            claim("release body", release,
                  "**at least %dx** smaller" % worst_floor,
                  "release body, memory floor")
            claim("release body", release,
                  "%s MiB against %s MiB for" % (num(clip["peak_tree_mib"]),
                                                 num(chrome["peak_tree_mib"])),
                  "release body, --clip against Chromium")
            claim("release body", release,
                  "%s MiB for \\`--clip --per-element\\`, %s MiB for" % (
                      num(per_el["peak_tree_mib"]), num(doc["peak_tree_mib"])),
                  "release body, per-element and --full")
            claim("GATES", gates, "{:,}".format(fid["pixels"]),
                  "receipt fidelity pixel count")
            claim("README", readme, "{:,}".format(fid["pixels"]),
                  "receipt fidelity pixel count")

            # The fidelity paragraph is FIVE numbers, and for two releases only
            # over16_pct and pixels were gated. The other three drifted back to
            # the PREVIOUS receipt's values -- mean_diff 7.115 against 8.2889,
            # ink 19.1-19.4 % against 17.07/16.69 -- while the gate read 91
            # claims green. A guard that covers the paragraph's first and last
            # number proves nothing about the middle of it: gate all five.
            claim("README", readme, num(fid["mean_diff"]),
                  "receipt fidelity, mean channel difference")
            claim("GATES", gates, num(fid["mean_diff"]),
                  "receipt fidelity, mean channel difference")
            claim("README", readme, num(fid["ink_engine_pct"]),
                  "receipt fidelity, engine ink coverage")
            claim("GATES", gates, num(fid["ink_engine_pct"]),
                  "receipt fidelity, engine ink coverage")
            claim("README", readme, num(fid["ink_ref_pct"]),
                  "receipt fidelity, Chromium ink coverage")
            claim("GATES", gates, num(fid["ink_ref_pct"]),
                  "receipt fidelity, Chromium ink coverage")

            # The asset HTMLs bake the same two ratios by hand, as a tile and
            # as a bar width. Same rule: derive from the receipt — but print the
            # FLOOR, not the ratio. The tile is the one surface with no room to
            # say "1.4-1.6x", and a tile showing whichever ratio the receipt
            # caught would advertise the top of a range that moves 18.5% run to
            # run. The floor is matched or beaten by every run, the reader's own
            # included.
            hero = ASSETS["hero"][0]
            hero_text = read(hero)
            for name, (html, _w, _h, _scale, _png) in ASSETS.items():
                text = read(html)
                claim(html, text,
                      '<div class="v">%d<em>\u00d7</em></div>' % mem_floor,
                      "floor of the tree-RSS ratio, memory tile")
                claim(html, text,
                      '<div class="v">%d<em>\u00d7</em></div>' % worst_floor,
                      "floor of the least favourable mode, worst-case tile")
                claim(html, text, "%s vs %s MiB" % (num(per_el["peak_tree_mib"]),
                                                    num(chrome["peak_tree_mib"])),
                      "receipt tree-RSS medians, caption")
            claim(hero, hero_text,
                  "%.1f%%" % (100.0 * per_el["peak_tree_mib"] / chrome["peak_tree_mib"]),
                  "bar width, RSS")
            # The right panel and the terminal footer are SECOND copies of the
            # same measurements, written in different markup. A duplicate no
            # guard reads is how the kicker outlived four releases, so each copy
            # is claimed by the tag that distinguishes it.
            claim(hero, hero_text,
                  '<span class="bv">%s MiB</span>' % num(chrome["peak_tree_mib"]),
                  "right-panel Chromium tree RSS")
            claim(hero, hero_text,
                  '<span class="bv win">%s MiB</span>' % num(per_el["peak_tree_mib"]),
                  "right-panel html-render tree RSS")
            claim(hero, hero_text,
                  "<b>%s MiB</b> peak." % num(per_el["peak_tree_mib"]),
                  "terminal footer summary")
            claim(hero, hero_text, num(fid["over16_pct"]), "receipt fidelity")

            # The hero names the browser it measured against, and that name is
            # in the receipt: "Chromium 151" outlived the browser it described.
            ver = re.search(r"Chrome (\d+)", chrome.get("version", ""))
            if ver is None:
                fails.append("receipt engines.chromium.version %r names no "
                             "Chrome major version" % chrome.get("version"))
            else:
                claim(hero, hero_text, "Chromium %s" % ver.group(1),
                      "the browser the receipt measured")

    # --- the withdrawn speed claim ------------------------------------------
    # This file used to assert a wall-clock multiple in three places: README's
    # cost table, GATES's ratio sentence, and a tile on each asset. All three
    # are withdrawn (GATES.md §Withdrawn claims), and a positive claim cannot
    # enforce an absence — so the surfaces that carried them are checked for
    # the strings a re-add would produce.
    for name, text, needle in (
            ("README.md", readme, "| wall clock, `"),
            ("GATES.md", gates, "\u00d7** wall and"),
            ("assets/hero.html", read(ASSETS["hero"][0]), "faster"),
            ("assets/og.html", read(ASSETS["og"][0]), "faster"),
    ):
        checked += 1
        if needle in text:
            fails.append("%s still advertises speed (%r): the ratio is "
                         "withdrawn — see GATES.md \u00a7Withdrawn claims"
                         % (name, needle))

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

    # --- a derived record must actually be derived --------------------------
    # shapes.slides.chromium says derived_from engines.chromium: the browser
    # renders once and cuts, so there is no second measurement to disagree
    # with. That makes the two copies one fact, and a copy nobody compares is
    # free to drift -- the same hole as wall_s vs wall_median_s.
    checked += 1
    if receipt is not None:
        der = receipt["shapes"]["slides"].get("chromium") or {}
        src = receipt["engines"].get("chromium") or {}
        if der.get("status") == "measured" and src.get("status") == "measured":
            for field in ("wall_median_s", "peak_tree_mib"):
                checked += 1
                if der.get(field) != src.get(field):
                    fails.append("receipt shapes.slides.chromium.%s is %r but it "
                                 "claims derived_from engines.chromium, which "
                                 "holds %r" % (field, der.get(field), src.get(field)))

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

    # --- the footer must credit the probe that produced these figures -------
    # tools/gates.py is the pre-probe harness: it needs --deck, a client deck
    # that is not in this repo, so "reproducible with gates.py" is a claim no
    # reader can act on. The probe replaced it.
    hero_html = read("assets/hero.html")
    # The determinism tile advertises two numbers: how many renders were
    # compared, and how many distinct digests came out. Both live in the
    # receipt's top-level `determinism` block, which the probe writes only
    # after hashing every repeat -- so a receipt without that block cannot
    # back the tile at all, and saying nothing would be the same defect the
    # tile exists to prevent. `1 md5` is the claim that N runs of the same
    # input produce the same bytes; it is worth exactly as much as the
    # measurement behind it.
    checked += 1
    if receipt is not None:
        det = receipt.get("determinism")
        if not det:
            fails.append("assets/hero.html advertises byte-identical output but "
                         "bench/results.json carries no determinism block: "
                         "re-run tools/bench.py so the tile is measured")
        else:
            claim("assets/hero.html", hero_html,
                  "<div class=\"v\">%d md5</div>" % det["distinct_md5"],
                  "the receipt's distinct-digest count, determinism tile")
            claim("assets/hero.html", hero_html,
                  "<div class=\"l\">%d renders</div>" % det["renders"],
                  "the probe's repeat count, determinism tile")
            # The release note states the same property in words, and it is
            # the copy nobody re-reads: it said "ten renders" for a probe that
            # runs three, and no guard noticed because the string had no claim.
            _words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                      6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
            claim("release body", release,
                  "%s renders of the same input are byte-identical"
                  % _words.get(det["renders"], str(det["renders"])),
                  "the probe's repeat count, release note")
    checked += 1
    if "reproducible with <b>tools/bench.py</b>" not in hero_html:
        fails.append("hero footer does not credit tools/bench.py as the reproducer")
    checked += 1
    if "tools/gates.py" in hero_html:
        fails.append("assets/hero.html credits tools/gates.py, which needs --deck "
                     "(a client deck this repo does not ship); the probe is "
                     "tools/bench.py")

    # The footer's glibc must be the one the receipt was measured on.
    checked += 1
    if receipt is not None:
        m = re.search(r"GLIBC (\d+\.\d+)", receipt["host"].get("glibc", ""))
        if not m:
            fails.append("receipt host.glibc names no GLIBC version: %r"
                         % (receipt["host"].get("glibc"),))
        else:
            want_glibc = "glibc %s" % m.group(1)
            if want_glibc not in hero_html:
                fails.append("hero footer does not state %r (receipt host glibc %r)"
                             % (want_glibc, m.group(1)))

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
    # The clip-vs-per-element pixel count quoted a 44,236,800-px document that
    # this repo does not generate, so no reader could reproduce it. Retired as
    # a string: a denominator nobody can regenerate is not a measurement.
    for retired in ("no runtime deps", "tree-summed", "Static Blitz",
                    "static binary", "44,236,800"):
        checked += 1
        hits = [f for f, t in (("README.md", readme), ("GATES.md", gates),
                               (".github/workflows/release.yml", release))
                if retired in t]
        if hits:
            fails.append("retired claim %r survives in %s" % (retired, ", ".join(hits)))

    # --- every doc the README points at has to exist ------------------------
    # A page that links a file nobody wrote is a broken promise, and this repo
    # grew two docs (the probe's, and the research note) that nothing pointed
    # at. Both directions are checked: the target exists, and the README says
    # where it is.
    for target in ("bench/README.md", "docs/why-html-first.md",
                   "docs/USAGE.md"):
        checked += 1
        if not os.path.exists(os.path.join(ROOT, target)):
            fails.append("README.md links %s, which does not exist" % target)
        elif "](%s)" % target not in readme:
            fails.append("README.md no longer links %s: the doc is unreachable "
                         "from the page" % target)

    # --- the drift bands, which live in code and are quoted in prose --------
    # Three files state the tolerances and `tools/bench_drift.py` enforces them.
    # Two copies of one fact drift apart, and the prose copy is the one a reader
    # trusts — so the numbers are read out of the module that applies them.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bench_drift  # noqa: E402  (local tool, imported for its constants)
    wall_pct = "%d %%" % round(bench_drift.WALL_TOL * 100)
    mem_pct = "%d %%" % round(bench_drift.MEM_TOL * 100)
    fid_pct = "%d %%" % round(bench_drift.FIDELITY_TOL * 100)
    claim_prose("README.md", readme,
                "gates the wall *ratio* at a\ndeliberately coarse %s" % wall_pct,
                "the wall band, in the paragraph that disclaims speed")
    claim_prose("GATES.md", gates, "past %s (wall ratio)" % wall_pct,
                "the wall band, where the gate is described")
    claim_prose("GATES.md", gates,
                "%s (memory) or %s (fidelity)" % (mem_pct, fid_pct),
                "the memory and fidelity bands, where the gate is described")
    claim_prose("bench/README.md", bench_readme,
                "**%s on the wall ratio, %s on\nmemory, %s on fidelity**"
                % (wall_pct, mem_pct, fid_pct),
                "the bands, as the probe documents them")

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
