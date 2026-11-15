#!/usr/bin/env python3
"""Compare a fresh benchmark receipt against the committed one.

`tools/bench.py` runs in CI on every push and writes a receipt, but until this
file existed nothing compared that fresh receipt to `bench/results.json`. CI
measured, uploaded the artifact, and passed — so a change that made the renderer
three times slower left every gate green and every advertised figure intact,
describing a binary nobody had. The measurement was real; the comparison that
gives it meaning was missing.

This is a **drift** check, not a regression test with tight bounds: it answers
"did something move so far that the published figures are now describing a
different program?", not "is this run faster than the last?".

It also refuses to compare two receipts that measured *different documents*.
Every quantity below is a property of (renderer x document), so replacing the
deck moves all of them for a reason that has nothing to do with the renderer —
and the move can go either way, which is worse than a one-directional error: a
cheaper deck would read as a performance win. Deck identity is therefore checked
first, and a mismatch is reported as "the deck moved", never as drift. The tolerances are
therefore set from the measured run-to-run spread of the probe itself, and they
are deliberately loose, because a gate that fires on a quiet machine's noise
gets disabled by whoever has to babysit it.

**Absolute wall clock is not compared at all.** Two runs on the same runner
(`runnervmy75tu`, 4 vCPU, 15,989 MiB, same deck `b77aac2d`, same binary) read
Chromium 1.604 s then 3.120 s and html-render 1.011 s then 1.689 s: both engines
roughly doubled while memory moved 0.2-0.4 % and the ratios moved 16 %. The
machine was busy; the program was not slower. Gating an absolute second on a
shared runner gates the neighbour's load, and a check that fails for reasons
outside the repo is a check that gets switched off.

So the gate compares quantities that are properties of (renderer x document):

| quantity | kind | tolerance | largest spread seen |
|---|---|---|---|
| peak tree RSS, four shapes | absolute | 20 % | 5.0 % |
| fidelity, pixels off by >16 | absolute | 15 % | 0.0 % |
| wall ratio, three shapes | ratio | 45 % | 30.3 % |
| memory ratio, three shapes | ratio | 20 % | 5.5 % |

A ratio survives a busy machine because both engines are measured in the same
window: a machine-wide slowdown cancels, a renderer-only regression does not.
The self-test pins that pair — `machine_slowdown_passes` and
`renderer_only_regression_fails` — because it is the distinction the gate exists
to draw.

Usage:

    python3 tools/bench_drift.py --committed bench/results.json --fresh /tmp/fresh.json
    python3 tools/bench_drift.py --self-test
"""

import argparse
import contextlib
import io
import json
import sys

# 45 %, not 35 %. Chromium's multi-process startup is far more
# contention-sensitive than a single process, so a busy machine moves the RATIO
# too, just less than it moves the seconds. Six runs on one runner image put the
# worst deviation from any single baseline at 30.3 %, so a 35 % band was a gate
# that failed for being unlucky. The ceiling is set by the smallest signal worth
# catching: a doubling of OUR renderer moves the ratio -50 %, so the band has to
# sit under that. 45 % is the window between the two. Measured detection
# threshold: a 1.85x slowdown of our renderer is caught, a 1.80x is not — so
# read this band as "somewhere near a doubling", not as a fine instrument. It is
# coarse on purpose, because speed is no longer advertised; the tight bands are
# memory (20 %) and fidelity (15 %), which is where the claims now live.
WALL_TOL = 0.45
MEM_TOL = 0.20
FIDELITY_TOL = 0.15


def load(path):
    with open(path) as f:
        return json.load(f)


def get(receipt, dotted):
    """Read a dotted path, or None when the receipt does not carry it."""
    node = receipt
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# What makes two receipts describe the same document. `plates_md5` is here
# because the HTML references its images by filename: re-generating a plate
# changes every painted pixel while `html_md5` does not move at all, so the
# document hash alone would call that "the same deck".
DECK_FIELDS = (
    ("html_md5", "the document"),
    ("plates_md5", "the embedded images"),
    ("slides", "the slide count"),
    ("seed", "the generator seed"),
    ("scale", "the device scale"),
    ("expected_px", "the pixel grid"),
)

def deck_delta(committed, fresh):
    """Return [(field, what_it_is, old, new)] for every deck identity mismatch.

    A missing block counts as a mismatch: a receipt that never recorded its deck
    cannot be shown to describe the same document, and "cannot be shown" is not
    "is the same".
    """
    old, new = committed.get("deck"), fresh.get("deck")
    if not isinstance(old, dict) or not isinstance(new, dict):
        return [("<deck block>", "the deck identity", old, new)]
    out = []
    for field, what in DECK_FIELDS:
        a, b = old.get(field), new.get(field)
        if a != b:
            out.append((field, what, a, b))
    return out

def compare(committed, fresh):
    """Return (rows, fails). Each row is (label, old, new, delta_pct, tol, ok)."""
    rows, fails = [], []

    # Deck identity first, because everything after it assumes the two receipts
    # measured the same document.
    moved = deck_delta(committed, fresh)
    if moved:
        detail = "; ".join("%s (%s): %r -> %r" % (f, what, a, b)
                           for f, what, a, b in moved)
        fails.append(
            "the deck moved: %s. The quantity rows above are two different "
            "documents, so they are not evidence about the renderer in either "
            "direction. Re-run tools/bench.py on the new deck and commit the "
            "fresh receipt as bench/results.json." % detail)

    # A receipt where the browser did not run cannot back a ratio, and a CI that
    # silently stopped being able to launch Chromium would otherwise read as
    # "no drift" forever.
    status = get(fresh, "engines.chromium.status")
    if status != "measured":
        fails.append("fresh receipt: chromium is %r, so no ratio can be compared"
                     % (status,))

    # Every figure the README prints, not just the `--full` pair. The first
    # version of this file checked only `engines.*`, while the docs advertise six
    # rows: a three-fold regression in the `--clip` or `--per-element` path would
    # have moved four published numbers and left this gate green.
    # Absolute RSS only. The matching `wall_median_s` rows were here until a
    # run on the same host, deck and binary read 1.604 s where the previous one
    # read 3.120 s for Chromium and 1.011 s where it read 1.689 s for ours:
    # both engines doubled, so that row was measuring the runner's load. Wall
    # clock stays in the receipt and stays gated as a RATIO, below.
    plan = [
        ("chromium peak tree RSS", "engines.chromium.peak_tree_mib", MEM_TOL),
        ("--full peak tree RSS", "engines.html-render.peak_tree_mib", MEM_TOL),
        ("--clip peak tree RSS", "shapes.slides.html-render-clip.peak_tree_mib", MEM_TOL),
        ("--per-element peak tree RSS",
         "shapes.slides.html-render-per-element.peak_tree_mib", MEM_TOL),
        ("fidelity, pixels off by >16", "fidelity.over16_pct", FIDELITY_TOL),
    ]
    for label, path, tol in plan:
        old, new = get(committed, path), get(fresh, path)
        if old is None or new is None:
            # Absent in one of them means the shape of the receipt changed, and a
            # comparator that skips what it cannot read reports health it never
            # checked.
            fails.append("%s: %r is missing (committed %r, fresh %r)"
                         % (label, path, old, new))
            continue
        if not old:
            fails.append("%s: committed value is %r, cannot form a ratio"
                         % (label, old))
            continue
        delta = (new - old) / abs(old)
        ok = abs(delta) <= tol
        rows.append((label, old, new, 100.0 * delta, 100.0 * tol, ok))
        if not ok:
            fails.append("%s moved %.1f%% (%r -> %r), past the %.0f%% band"
                         % (label, 100.0 * delta, old, new, 100.0 * tol))

    # `shapes.slides.chromium` is a copy of `engines.chromium` — one screenshot,
    # cut into slides, no extra render. They are one fact written twice, so a
    # receipt whose two copies disagree would make the ratio the README prints
    # depend on which copy the reader happened to pick.
    for field in ("wall_median_s", "peak_tree_mib"):
        c_eng, c_sli = get(committed, "engines.chromium." + field), \
            get(committed, "shapes.slides.chromium." + field)
        f_eng, f_sli = get(fresh, "engines.chromium." + field), \
            get(fresh, "shapes.slides.chromium." + field)
        if None in (c_eng, c_sli, f_eng, f_sli):
            fails.append("derived chromium copy: %r is missing (committed %r/%r, "
                         "fresh %r/%r)" % (field, c_eng, c_sli, f_eng, f_sli))
        elif c_eng != c_sli or f_eng != f_sli:
            fails.append("derived chromium copy disagrees on %s (committed "
                         "engines %r vs slides %r; fresh %r vs %r)"
                         % (field, c_eng, c_sli, f_eng, f_sli))

    # The ratios, one wall and one memory figure for each advertised shape.
    for shape, renderer in (
        ("--full", "engines.html-render"),
        ("--clip", "shapes.slides.html-render-clip"),
        ("--per-element", "shapes.slides.html-render-per-element"),
    ):
        for unit, field, tol in (
            ("wall ratio", "wall_median_s", WALL_TOL),
            ("memory ratio", "peak_tree_mib", MEM_TOL),
        ):
            label = "%s %s" % (shape, unit)
            c_num = get(committed, "engines.chromium." + field)
            c_den = get(committed, renderer + "." + field)
            f_num = get(fresh, "engines.chromium." + field)
            f_den = get(fresh, renderer + "." + field)
            if None in (c_num, c_den, f_num, f_den) or not c_den or not f_den:
                fails.append("%s: cannot form a ratio from the receipts" % label)
                continue
            old, new = c_num / c_den, f_num / f_den
            delta = (new - old) / abs(old)
            ok = abs(delta) <= tol
            rows.append((label, old, new, 100.0 * delta, 100.0 * tol, ok))
            if not ok:
                fails.append("%s moved %.1f%% (%.3f -> %.3f), past the %.0f%% band"
                             % (label, 100.0 * delta, old, new, 100.0 * tol))

    return rows, fails


def _md5(deck):
    """The document hash, shortened, or a word for "no deck recorded"."""
    if not isinstance(deck, dict):
        return "<none>"
    return str(deck.get("html_md5"))[:8]

def report(rows, fails, committed, fresh):
    # `deck_delta` reads `receipt["deck"]` itself, so it takes the RECEIPTS.
    # Handing it the two deck blocks made its own `get("deck")` return None on
    # both sides, and this line printed DIFFERENT on every run — a header that
    # contradicted the FAIL list underneath it, which is the fastest way to
    # teach a reader to skip the header.
    old, new = committed.get("deck"), fresh.get("deck")
    same = "same" if not deck_delta(committed, fresh) else "DIFFERENT"
    print("deck  committed %s  fresh %s  (%s)"
          % (_md5(old), _md5(new), same))
    print()
    print("%-30s %12s %12s %8s %7s" % ("quantity", "committed", "fresh", "delta", "band"))
    for label, old, new, delta, tol, ok in rows:
        print("%-30s %12.4g %12.4g %+7.1f%% %6.0f%% %s"
              % (label, old, new, delta, tol, "" if ok else "<-- OUT"))
    print()
    if fails:
        print("FAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("PASS: the fresh receipt describes the same program as the committed one")
    return 0


# --- self-test -------------------------------------------------------------
# The legs below are the reason this file is allowed to fail a build. A
# comparator nobody has watched fail is indistinguishable from one that always
# passes, and the shapes it has to get right (a doubling, a noise-sized move, a
# missing key) are exactly the ones a real receipt will present.


DECK_BASE = {
    "generator": "tools/bench_deck.py",
    "seed": 20261115,
    "slides": 8,
    "scale": 2,
    "css_px": [1280, 5760],
    "expected_px": [2560, 11520],
    "html_md5": "5c71602d1dd6c259ea3e3ba316da5233",
    "plates_md5": {"plate-a.png": "5dea3bb009d5e51fee1ce649575fd5b9"},
}

def _receipt(wall_c=3.774, wall_h=2.352, peak_c=2452.6, peak_h=37.7,
             fid=4.5462, status="measured", clip_wall=2.386, clip_peak=75.3,
             pe_wall=2.349, pe_peak=47.1, derived_wall=None, derived_peak=None,
             deck=None):
    """A receipt shaped like the real one, including the three advertised shapes.

    `derived_*` lets a leg break the `shapes.slides.chromium` copy away from
    `engines.chromium` on purpose — the one field pair that is a duplicate rather
    than a measurement. `deck` defaults to `DECK_BASE`; pass a dict to alter one
    identity field, or the string "absent" to model a receipt that never recorded
    one.
    """
    if deck is None:
        deck = json.loads(json.dumps(DECK_BASE))
    elif deck == "absent":
        deck = None
    return {
        "deck": deck,
        "engines": {
            "chromium": {"status": status, "wall_median_s": wall_c,
                         "peak_tree_mib": peak_c},
            "html-render": {"status": "measured", "wall_median_s": wall_h,
                            "peak_tree_mib": peak_h},
        },
        "shapes": {
            "slides": {
                "chromium": {
                    "status": status,
                    "wall_median_s": wall_c if derived_wall is None else derived_wall,
                    "peak_tree_mib": peak_c if derived_peak is None else derived_peak,
                },
                "html-render-clip": {
                    "status": "measured", "wall_median_s": clip_wall,
                    "peak_tree_mib": clip_peak,
                },
                "html-render-per-element": {
                    "status": "measured", "wall_median_s": pe_wall,
                    "peak_tree_mib": pe_peak,
                },
            }
        },
        "fidelity": {"over16_pct": fid},
    }


def self_test():
    base = _receipt()
    legs = []

    def leg(name, fresh, want_rc):
        rows, fails = compare(base, fresh)
        rc = 1 if fails else 0
        legs.append((name, rc == want_rc, rc, want_rc, fails))

    leg("identical_passes", _receipt(), 0)
    leg("chromium_wall_noise_passes", _receipt(wall_c=3.774 * 1.18), 0)
    leg("wall_ratio_12pct_passes", _receipt(wall_c=3.774 * 1.13), 0)
    # The observed natural span of the ratio across six runs is 30.3 %, so a
    # band that failed here would fail on an ordinary day.
    leg("wall_ratio_30pct_passes", _receipt(wall_c=3.774 * 1.30), 0)
    leg("wall_ratio_40pct_passes", _receipt(wall_c=3.774 * 1.40), 0)
    # The band's lower edge, measured rather than assumed: 1.85x is caught,
    # 1.80x is not. Pinned so a future widening has to justify itself.
    leg("renderer_180pct_passes", _receipt(wall_h=2.352 * 1.80), 0)
    leg("renderer_185pct_fails", _receipt(wall_h=2.352 * 1.85), 1)
    leg("memory_doubled_fails", _receipt(peak_h=37.7 * 2), 1)
    leg("renderer_only_regression_fails", _receipt(wall_h=2.352 * 2), 1)
    # The pair the gate exists to draw: a busy machine slows BOTH engines, so
    # every ratio holds and nothing has regressed. Before absolute wall clock
    # was dropped, this leg failed — which is what a noisy runner looked like.
    leg("machine_slowdown_passes",
        _receipt(wall_c=3.774 * 1.9, wall_h=2.352 * 1.9,
                 clip_wall=2.386 * 1.9, pe_wall=2.349 * 1.9), 0)
    leg("fidelity_doubled_fails", _receipt(fid=4.5462 * 2), 1)
    # The four rows the first version of this file did not read at all.
    leg("clip_wall_doubled_fails", _receipt(clip_wall=2.386 * 2), 1)
    leg("clip_memory_doubled_fails", _receipt(clip_peak=75.3 * 2), 1)
    leg("per_element_wall_doubled_fails", _receipt(pe_wall=2.349 * 2), 1)
    leg("per_element_memory_doubled_fails", _receipt(pe_peak=47.1 * 2), 1)
    # One fact written twice: the derived copy must agree with its source.
    leg("derived_copy_wall_disagrees_fails", _receipt(derived_wall=3.774 * 1.5), 1)
    leg("derived_copy_peak_disagrees_fails", _receipt(derived_peak=2452.6 * 1.5), 1)
    leg("chromium_unavailable_fails", _receipt(status="unavailable"), 1)
    # Deck identity. A different document makes every quantity incomparable, and
    # the failure has to say so rather than reporting it as renderer drift.
    leg("deck_identical_passes", _receipt(), 0)
    leg("deck_html_changed_fails",
        _receipt(deck={**DECK_BASE, "html_md5": "0" * 32}), 1)
    leg("deck_slides_changed_fails",
        _receipt(deck={**DECK_BASE, "slides": 12}), 1)
    leg("deck_plates_changed_fails",
        _receipt(deck={**DECK_BASE,
                       "plates_md5": {"plate-a.png": "f" * 32}}), 1)
    leg("deck_absent_fails", _receipt(deck="absent"), 1)
    # A receipt that lost the field is a schema change, not a clean bill.
    missing = _receipt()
    del missing["fidelity"]
    leg("missing_field_fails", missing, 1)
    # Zero in the denominator would divide by zero if it were trusted.
    zero = _receipt()
    zero["engines"]["html-render"]["peak_tree_mib"] = 0
    leg("zero_denominator_fails", zero, 1)

    # The header must agree with the verdict. It printed DIFFERENT on two
    # identical decks, because `deck_delta` reads `receipt["deck"]` and was
    # handed the blocks instead of the receipts.
    def report_leg(name, committed, fresh, needle):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report([], [], committed, fresh)
        text = buf.getvalue()
        ok = needle in text
        legs.append((name, ok, 0 if ok else 1, 0,
                     [] if ok else [text.strip()]))

    report_leg("report_header_same", _receipt(), _receipt(), "(same)")
    report_leg("report_header_different", _receipt(),
               _receipt(deck={**DECK_BASE, "html_md5": "0" * 32}), "(DIFFERENT)")

    bad = [l for l in legs if not l[1]]
    for name, ok, rc, want, fails in legs:
        print("%-4s %-28s rc=%d want=%d" % ("ok" if ok else "FAIL", name, rc, want))
        if not ok:
            for f in fails:
                print("       %s" % f)
    print()
    if bad:
        print("FAIL %d of %d leg(s)" % (len(bad), len(legs)))
        return 1
    print("PASS %d leg(s)" % len(legs))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--committed", help="the receipt in the repo")
    ap.add_argument("--fresh", help="the receipt this run just produced")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not (args.committed and args.fresh):
        ap.error("--committed and --fresh are both required (or --self-test)")
    committed, fresh = load(args.committed), load(args.fresh)
    rows, fails = compare(committed, fresh)
    return report(rows, fails, committed, fresh)


if __name__ == "__main__":
    sys.exit(main())
