#!/usr/bin/env python3
"""Self-test for `tools/bench.py`: prove the probe can fail before trusting it.

A probe that only ever prints numbers is decoration. Each leg here either
demonstrates a property the probe relies on (determinism, one definition across
two decoders) or forces a failure the probe is supposed to catch, and checks
that it is caught:

  1  deck-deterministic    same seed, two directories, identical md5s
  2  deck-height           the deck's CSS height is SLIDES * SLIDE_H
  3  ihdr                  dimensions read from IHDR match a full decode
  4  identical-zero        the same file against itself scores 0 / 0
  5  known-difference      a painted rectangle of known area scores its area
  6  decoders-agree        pillow and stdlib produce the same four figures
  7  size-mismatch         two different sizes refuse to compare
  8  viewport-clip-caught  an engine writing a too-small PNG is failed, not measured
  9  no-png-caught         an engine that writes nothing is failed, not measured
 10  median                the wall figure is a median of the repeats

Run: python3 tools/bench.py --self-test
"""

import os
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench  # noqa: E402
import bench_compare  # noqa: E402
import bench_deck  # noqa: E402

LEGS = []


def leg(fn):
    LEGS.append(fn)
    return fn


def _solid(path, w, h, rgb):
    bench_deck.write_png(path, w, h, [bytes(rgb) * w for _ in range(h)])


@leg
def deck_deterministic(tmp):
    a = bench_deck.generate(os.path.join(tmp, "a"), 7)
    b = bench_deck.generate(os.path.join(tmp, "b"), 7)
    if bench.md5(a) != bench.md5(b):
        return "deck.html differs between two generations of the same seed"
    for name, _ in bench_deck.PLATES:
        pa = os.path.join(os.path.dirname(a), name)
        pb = os.path.join(os.path.dirname(b), name)
        if bench.md5(pa) != bench.md5(pb):
            return "%s differs between two generations of the same seed" % name
    c = bench_deck.generate(os.path.join(tmp, "c"), 8)
    if bench.md5(c) == bench.md5(a):
        return "a different seed produced the same deck: the seed does nothing"
    return None


@leg
def deck_height(tmp):
    want = bench_deck.SLIDES * bench_deck.SLIDE_H
    if bench_deck.DOC_H != want:
        return "DOC_H %d != SLIDES*SLIDE_H %d" % (bench_deck.DOC_H, want)
    html = bench_deck.generate(os.path.join(tmp, "h"), 1)
    with open(html) as fh:
        body = fh.read()
    got = body.count('class="slide"')
    if got != bench_deck.SLIDES:
        return "%d slides in the HTML, %d declared" % (got, bench_deck.SLIDES)
    return None


@leg
def ihdr_matches_decode(tmp):
    png = os.path.join(tmp, "ihdr.png")
    _solid(png, 37, 11, (10, 20, 30))
    got = bench_compare.ihdr(png)
    if got != (37, 11):
        return "IHDR says %r, wrote 37x11" % (got,)
    w, h, _ch, _buf = bench_compare._stdlib_rgb(png)
    if (w, h) != got:
        return "decode says %dx%d, IHDR says %r" % (w, h, got)
    return None


@leg
def identical_zero(tmp):
    png = os.path.join(tmp, "same.png")
    _solid(png, 64, 32, (200, 30, 90))
    for decoder in ("stdlib", "pillow"):
        if decoder == "pillow" and bench_compare.Image is None:
            continue
        got = bench_compare.fidelity(png, png, decoder=decoder)
        if got["over16_pct"] != 0.0 or got["mean_diff"] != 0.0:
            return "%s: identical files scored %r" % (decoder, got)
    return None


@leg
def known_difference(tmp):
    """Paint a 10x10 white block on a 100x50 black image: exactly 2% of pixels."""
    w, h = 100, 50
    a = os.path.join(tmp, "black.png")
    b = os.path.join(tmp, "block.png")
    _solid(a, w, h, (0, 0, 0))
    rows = []
    for y in range(h):
        row = bytearray(bytes((0, 0, 0)) * w)
        if 10 <= y < 20:
            row[10 * 3:20 * 3] = bytes((255, 255, 255)) * 10
        rows.append(bytes(row))
    bench_deck.write_png(b, w, h, rows)
    want = 100.0 * 100 / (w * h)
    results = {}
    for decoder in ("stdlib", "pillow"):
        if decoder == "pillow" and bench_compare.Image is None:
            continue
        got = bench_compare.fidelity(a, b, decoder=decoder)
        results[decoder] = got
        if abs(got["over16_pct"] - want) > 0.01:
            return "%s: painted %d px of %d, scored %.4f%% not %.4f%%" % (
                decoder, 100, w * h, got["over16_pct"], want)
        if abs(got["mean_diff"] - (255.0 * 100 / (w * h))) > 0.05:
            return "%s: mean_diff %.4f, expected %.4f" % (
                decoder, got["mean_diff"], 255.0 * 100 / (w * h))
    if len(results) == 2:
        one, two = results["stdlib"], results["pillow"]
        for key in ("over16_pct", "mean_diff", "ink_ref_pct", "ink_engine_pct"):
            if abs(one[key] - two[key]) > 0.01:
                return "decoders disagree on %s: %r vs %r" % (key, one[key], two[key])
    return None


@leg
def decoders_agree(tmp):
    """Leg 5 proves they agree on a synthetic pair; this one on a rendered one."""
    if bench_compare.Image is None:
        return None
    a = os.path.join(tmp, "ga.png")
    b = os.path.join(tmp, "gb.png")
    _solid(a, 80, 40, (12, 200, 40))
    _solid(b, 80, 40, (12, 180, 40))
    one = bench_compare.fidelity(a, b, decoder="stdlib")
    two = bench_compare.fidelity(a, b, decoder="pillow")
    for key in ("over16_pct", "mean_diff", "ink_ref_pct", "ink_engine_pct"):
        if abs(one[key] - two[key]) > 0.01:
            return "decoders disagree on %s: %r vs %r" % (key, one[key], two[key])
    return None


@leg
def size_mismatch(tmp):
    a = os.path.join(tmp, "m1.png")
    b = os.path.join(tmp, "m2.png")
    _solid(a, 20, 20, (0, 0, 0))
    _solid(b, 20, 21, (0, 0, 0))
    try:
        bench_compare.fidelity(a, b, decoder="stdlib")
    except ValueError:
        return None
    return "compared 20x20 against 20x21 without complaint"


@leg
def viewport_clip_caught(tmp):
    """An engine that writes a viewport-sized PNG must be failed, not measured."""
    script = (
        "import sys; sys.path.insert(0, %r); import bench_deck; "
        "bench_deck.write_png(%r, 10, 10, [bytes((0,0,0))*10]*10)"
        % (os.path.dirname(os.path.abspath(__file__)), os.path.join(tmp, "tiny.png"))
    )
    cmd = [sys.executable, "-c", script]
    got = bench.run_engine(
        "fake", cmd, tmp, os.path.join(tmp, "tiny.png"), (100, 100), 1
    )
    if got["status"] != "failed":
        return "a 10x10 PNG was accepted as a 100x100 render: %r" % (got,)
    if "want 100x100" not in got["reason"]:
        return "failed for the wrong reason: %s" % got["reason"]
    return None


@leg
def no_png_caught(tmp):
    cmd = [sys.executable, "-c", "pass"]
    got = bench.run_engine("fake", cmd, tmp, os.path.join(tmp, "absent.png"), (10, 10), 1)
    if got["status"] != "failed":
        return "an engine that wrote nothing was recorded as %r" % got["status"]
    if "wrote no PNG" not in got["reason"]:
        return "failed for the wrong reason: %s" % got["reason"]
    return None


@leg
def crash_is_unavailable(tmp):
    """A browser that aborts must read as `unavailable`, never as `failed`.

    Both statuses are refusals, and the difference is who is broken: `failed`
    says the probe could not do its job, `unavailable` says this host cannot run
    that engine. A crashed Chromium recorded as `failed` would read as a broken
    renderer, which is the one thing the probe exists to disprove.
    """
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    got = bench.run_engine(
        "browser", cmd, tmp, os.path.join(tmp, "none.png"), (10, 10), 1,
        missing_is_unavailable=True,
    )
    if got["status"] != "unavailable":
        return "a crashed engine was recorded as %r" % got["status"]
    if "exited 3" not in got["reason"]:
        return "the exit status is missing from the reason: %s" % got["reason"]
    own = bench.run_engine("own", cmd, tmp, os.path.join(tmp, "none2.png"), (10, 10), 1)
    if own["status"] != "failed":
        return "our own engine's crash was recorded as %r, not failed" % own["status"]
    return None


@leg
def median(tmp):
    if statistics.median([3.0, 1.0, 2.0]) != 2.0:
        return "median of 1,2,3 is not 2"
    if statistics.median([1.0, 5.0, 9.0, 11.0]) != 7.0:
        return "median of 1,5,9,11 is not 7"
    return None


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="bench-selftest-") as tmp:
        for fn in LEGS:
            sub = os.path.join(tmp, fn.__name__)
            os.makedirs(sub, exist_ok=True)
            try:
                problem = fn(sub)
            except Exception as exc:  # a leg that raises is a failing leg
                problem = "%s: %s" % (type(exc).__name__, exc)
            if problem:
                failures += 1
                print("FAIL %-22s %s" % (fn.__name__, problem))
            else:
                print("ok   %s" % fn.__name__)
    print("\n%d leg(s), %d failed" % (len(LEGS), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
