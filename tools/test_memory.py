#!/usr/bin/env python3
"""Peak memory is quoted all over this repo and asserted nowhere — until now.

`tools/gates.py`, `tools/run_parity.py` and `tools/parity_baseline.json` all
record peak RSS and none of them compares it to anything, so a change that
doubled a shape's footprint passed every gate. `tools/measure_rss.py` said as
much in its own docstring: *"The gap is measured, not asserted"*. This is the
assertion.

Four shapes, chosen so that two are reached by banding and two structurally
cannot be:

  full_streamed           `--full` on a 20,000 px document. Streams row bands
                          into a `png` StreamWriter, so the peak is one band, not
                          the canvas.
  clip_oversized_frame    `--clip` on ONE 20,000 px element. `plan_bands` gives
                          an oversized frame its own band, so the band IS the
                          frame and banding is a no-op by construction. This is
                          the 2.07x-canvas shape nothing could see before.
  clip_multiband          `--clip` on a 20-slide deck, where banding does its job.
  clip_multiband_unbanded the same deck at `BLITZ_BAND_MIB=0`. The positive
                          control, and the proof that the instrument resolves
                          the difference banding makes.

WHY ABSOLUTE PINS. The dominant term is geometry — a canvas is `w * h * 4` — not
work done, so the numbers are stable rather than merely comparable: over seven to
eight runs of each shape the widest spread was 1.8 % (28.2–28.7 MiB, on the
smallest shape; the 809 MiB shape varied 0.7 %). `GROW_PCT` is 20 %, an order of
magnitude above that spread, so it absorbs a different host and allocator and
still catches the regression this gate exists for — a shape reading 2x its pin —
with a factor of five to spare.

THREE-WAY FAILURE, the same shape as the corpus gate: a shape with no pin fails,
a shape that grew fails, and a shape that dropped far below its pin fails as a
STALE pin. A baseline nobody updates rots in the silent direction, leaving a
tolerance that no longer describes the tree. This repo has already produced one
such drop: `--full` on the 20,000 px document read 422.6 MiB before it streamed
and 28.2 MiB after, so that fix had to move this baseline in its own commit.

    python3 tools/test_memory.py --bin target/release/pageplate
    python3 tools/test_memory.py --update-baseline --bin target/release/pageplate
    python3 tools/test_memory.py --self-test

Exit 0 = every shape is within its pin.
"""

import argparse
import json
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_rss  # noqa: E402  (after the path insert)

BASELINE = os.path.join(HERE, "memory_baseline.json")

# Tolerances live in the source, not in the baseline file: a knob inside the
# artifact under test is a knob the next `--update-baseline` can widen.
GROW_PCT = 20.0
SHRINK_PCT = 35.0

VIEW_W, VIEW_H, SCALE = 1280, 720, 2
DECK_SLIDES = 20
TALL_CSS_H = 20000


def tall_html():
    """One element 20,000 CSS px tall: taller than any single band, and taller
    than the viewport, so `--full` has to reach past the first screen."""
    return (
        '<html><head><style>html,body{margin:0;padding:0}'
        'div{background:#3366cc}</style></head>'
        '<body><div style="width:100%%;height:%dpx"></div></body></html>' % TALL_CSS_H
    )


def deck_html():
    """Many short frames: the shape banding was built for, because each band
    holds several frames instead of one."""
    slides = "".join(
        '<div class="s" style="width:100%%;height:%dpx;background:#%02x%02x%02x">'
        "slide %d</div>" % (VIEW_H, (i * 11) % 256, (i * 7) % 256, (i * 3) % 256, i)
        for i in range(DECK_SLIDES)
    )
    return (
        '<html><head><style>html,body{margin:0;padding:0}.s{display:block}'
        "</style></head><body>%s</body></html>" % slides
    )


def canvas_mib(rows_css, width_css):
    """What a single canvas would cost: the yardstick every ratio is stated
    against, so a pin can be read as a multiple of the thing it replaces."""
    return rows_css * SCALE * width_css * SCALE * 4 / (1024.0 * 1024.0)


def png_size(path):
    """Read width/height out of the IHDR chunk. `measure.read_png` decodes the
    whole image, which on a 2560x40000 render is 410 MB of Python pixels and
    half a second of unfiltering; this needs 24 bytes."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s: not a PNG" % path)
    return struct.unpack(">II", head[16:24])


def build_shapes(binary, work):
    """name -> {cmd, env, canvas, expect}. `expect` says what the run has to have
    produced, so a low number cannot be explained by a render that did nothing."""
    tall = os.path.join(work, "tall.html")
    with open(tall, "w") as fh:
        fh.write(tall_html())
    deck = os.path.join(work, "deck.html")
    with open(deck, "w") as fh:
        fh.write(deck_html())

    def outdir(name):
        path = os.path.join(work, name)
        os.makedirs(path, exist_ok=True)
        return path

    full_png = os.path.join(work, "full.png")
    clip_tall = outdir("clip_tall")
    clip_deck = outdir("clip_deck")
    clip_deck_off = outdir("clip_deck_off")

    geo = [str(VIEW_W), str(VIEW_H), str(SCALE)]
    return {
        "full_streamed": {
            "cmd": [binary, tall, full_png] + geo + ["--document", "--full"],
            "env": [],
            "canvas": canvas_mib(TALL_CSS_H, VIEW_W),
            "expect": ("png", full_png, (VIEW_W * SCALE, TALL_CSS_H * SCALE)),
        },
        "clip_oversized_frame": {
            "cmd": [binary, "--clip", tall, clip_tall] + geo + ["div", "--document"],
            "env": [],
            "canvas": canvas_mib(TALL_CSS_H, VIEW_W),
            "expect": ("clips", clip_tall, 1),
        },
        "clip_multiband": {
            "cmd": [binary, "--clip", deck, clip_deck] + geo + ["div.s", "--document"],
            "env": [],
            "canvas": canvas_mib(DECK_SLIDES * VIEW_H, VIEW_W),
            "expect": ("clips", clip_deck, DECK_SLIDES),
        },
        "clip_multiband_unbanded": {
            "cmd": [binary, "--clip", deck, clip_deck_off] + geo + ["div.s", "--document"],
            "env": ["BLITZ_BAND_MIB=0"],
            "canvas": canvas_mib(DECK_SLIDES * VIEW_H, VIEW_W),
            "expect": ("clips", clip_deck_off, DECK_SLIDES),
        },
    }


def check_expect(shape, name):
    """The work has to have happened, or the number is about nothing."""
    kind, path, want = shape["expect"]
    if kind == "png":
        if not os.path.isfile(path):
            return ["%s wrote no PNG at %s" % (name, path)]
        got = png_size(path)
        if got != want:
            return ["%s wrote %dx%d, want %dx%d" % ((name,) + got + want)]
        return []
    files = sorted(f for f in os.listdir(path) if f.startswith("clip_"))
    if len(files) != want:
        return ["%s wrote %d frame(s), want %d" % (name, len(files), want)]
    return []


def measure(binary, keep=False):
    """Run every shape; return (peak_mib by name, canvas by name, problems)."""
    work = tempfile.mkdtemp(prefix="mem-gate-")
    peaks, canvases, problems = {}, {}, []
    try:
        for name, shape in build_shapes(binary, work).items():
            cmd = (["env"] + shape["env"] + shape["cmd"]) if shape["env"] else shape["cmd"]
            rc, _out, err, _wall, kib = measure_rss.run_measured(cmd)
            if rc != 0:
                problems.append(
                    "%s exited %d (peak %.1f MiB is not a measurement of anything)\n%s"
                    % (name, rc, kib / 1024.0, err.strip()[:400])
                )
                continue
            peaks[name] = kib / 1024.0
            canvases[name] = shape["canvas"]
            problems.extend(check_expect(shape, name))
    finally:
        if keep:
            print("kept %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return peaks, canvases, problems


def gate(peaks, baseline):
    """Compare measured peaks against the pins. Three ways to fail, plus a
    control on the instrument itself."""
    problems = []
    pins = baseline.get("peak_mib", {})

    for name in sorted(peaks):
        if name not in pins:
            problems.append(
                "NEW shape %s at %.1f MiB is not pinned; add it with "
                "--update-baseline and read the diff" % (name, peaks[name])
            )

    for name in sorted(pins):
        if name not in peaks:
            problems.append(
                "BASELINE STALE %s: pinned at %.1f MiB but no longer measured"
                % (name, pins[name])
            )
            continue
        got, want = peaks[name], pins[name]
        if got > want * (1.0 + GROW_PCT / 100.0):
            problems.append(
                "GREW %s: %.1f -> %.1f MiB (+%.0f%%, tolerance %.0f%%)"
                % (name, want, got, 100.0 * (got - want) / want, GROW_PCT)
            )
        elif got < want * (1.0 - SHRINK_PCT / 100.0):
            problems.append(
                "IMPROVED %s: %.1f -> %.1f MiB (-%.0f%%, past the %.0f%% stale-pin "
                "band; update tools/memory_baseline.json in the commit that did it)"
                % (name, want, got, 100.0 * (want - got) / want, SHRINK_PCT)
            )

    # The instrument has to be able to tell the shapes apart. If `measure_rss`
    # ever returned one number for everything, every pin would fail at once and
    # the report would read as five regressions instead of one broken ruler.
    banded, unbanded = peaks.get("clip_multiband"), peaks.get("clip_multiband_unbanded")
    if banded and unbanded:
        ratio = unbanded / banded
        if ratio < 2.0:
            problems.append(
                "INSTRUMENT cannot resolve banding: unbanded/banded = %.2fx, want >= 2x"
                % ratio
            )
    return problems


def report(peaks, canvases, pins):
    print("%-26s %10s %10s %8s %10s" % ("shape", "peak MiB", "pin MiB", "vs pin", "canvas"))
    for name in sorted(peaks):
        pin = pins.get(name)
        print(
            "%-26s %10.1f %10s %8s %10.1f"
            % (
                name,
                peaks[name],
                ("%.1f" % pin) if pin is not None else "-",
                ("%.2fx" % (peaks[name] / pin)) if pin else "-",
                canvases.get(name, 0.0),
            )
        )


def self_test():
    """Prove the gate can say no without needing a regression to happen."""
    pins = {"a": 100.0, "b": 200.0, "c": 300.0}
    base = {"peak_mib": pins}

    def leg(name, peaks, want):
        got = gate(peaks, base)
        ok = (want is None and not got) or (want is not None and any(want in p for p in got))
        print("  %-46s %s" % (name, "PASS" if ok else "FAIL"))
        if not ok:
            print("     want %r, got %r" % (want, got))
        return ok

    at_pin = 100.0 * (1.0 + GROW_PCT / 100.0)
    ok = True
    ok &= leg("an exact match passes", dict(pins), None)
    ok &= leg("a shape at the tolerance edge still passes", {"a": at_pin, "b": 200.0, "c": 300.0}, None)
    ok &= leg("a 2x regression fails", {"a": 200.0, "b": 200.0, "c": 300.0}, "GREW a")
    ok &= leg("a shape just past the edge fails", {"a": at_pin + 1.0, "b": 200.0, "c": 300.0}, "GREW a")
    ok &= leg("a small improvement passes", {"a": 80.0, "b": 200.0, "c": 300.0}, None)
    ok &= leg("a large improvement fails as stale", {"a": 100.0, "b": 200.0, "c": 100.0}, "IMPROVED c")
    ok &= leg("an unpinned shape fails", dict(pins, d=50.0), "NEW shape d")
    ok &= leg("a shape that vanished fails as stale", {"a": 100.0, "b": 200.0}, "BASELINE STALE c")
    ok &= leg(
        "an instrument that cannot resolve banding fails",
        {"a": 100.0, "b": 200.0, "c": 300.0, "clip_multiband": 100.0,
         "clip_multiband_unbanded": 110.0},
        "INSTRUMENT",
    )
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", help="renderer binary (required unless --self-test)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite tools/memory_baseline.json from this run; read "
                         "the diff before committing it")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the gate logic can fail, without touching memory")
    ap.add_argument("--keep", action="store_true", help="leave the temp dir behind")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.bin:
        ap.error("--bin is required unless --self-test")
    binary = os.path.abspath(args.bin)

    baseline = json.load(open(BASELINE)) if os.path.isfile(BASELINE) else None
    peaks, canvases, problems = measure(binary, keep=args.keep)
    report(peaks, canvases, (baseline or {}).get("peak_mib", {}))

    if args.update_baseline:
        if problems:
            print("\nrefusing to write a baseline from a run that failed:")
            for p in problems:
                print("  " + p)
            return 1
        note = (baseline or {}).get("note", "")
        out = {"note": note, "peak_mib": {k: round(v, 1) for k, v in peaks.items()}}
        with open(BASELINE, "w") as fh:
            json.dump(out, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("\nwrote %s with %d pin(s)" % (BASELINE, len(peaks)))
        return 0

    if baseline is None:
        print("\nGATE  FAIL  no baseline at %s" % BASELINE)
        print("  create one with --update-baseline and read the diff before committing")
        return 1

    problems.extend(gate(peaks, baseline))
    if problems:
        print("\nGATE  FAIL  %d memory problem(s):" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("\nGATE  PASS  %d shape(s) within %.0f%% of their pins"
          % (len(peaks), GROW_PCT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
