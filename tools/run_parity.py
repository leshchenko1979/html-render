"""Measure Blitz against Chromium on the geometry corpus.

For every fixture both engines render the same document at the same viewport,
and both PNGs are measured with the same instrument (`tools/measure.py`). A row
therefore reports a difference between the two engines, not between two rulers.

Usage: python3 tools/run_parity.py [--blitz path] [--chrome path]
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure  # noqa: E402
import measure_rss  # noqa: E402

# "tree" sums the whole process tree (default); "max" is the old
# RUSAGE_CHILDREN reading, kept so published figures stay reproducible.
RSS_MODE = "tree"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")

# Known divergences, pinned by fixture:colour with their measured delta. See
# tools/parity_baseline.json.
BASELINE = os.path.join(ROOT, "tools", "parity_baseline.json")

# The document wrapper is byte-identical to the one the original parity harness
# used, so both engines see the same reset and the same body.
WRAPPER = (
    "<html><head><style>html,body{{margin:0;padding:0;background:#ffffff;}}"
    "</style></head><body>{}</body></html>"
)

CHROME_FLAGS = [
    "--headless",
    "--incognito",  # without it a bare headless launch hangs on this box
    "--disable-gpu",
    "--no-sandbox",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
]

# A difference of one pixel is sub-pixel rounding of a fractional size, not a
# layout disagreement. Anything larger is reported.
TOLERANCE = 1


def find_chrome(explicit):
    if explicit:
        return explicit
    for name in ("google-chrome", "chromium-browser", "chromium"):
        path = shutil.which(name)
        if path:
            return path
    cache = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(cache):
        for entry in sorted(os.listdir(cache), reverse=True):
            for sub in ("chrome-linux64/chrome", "chrome-linux/chrome"):
                candidate = os.path.join(cache, entry, sub)
                if os.path.isfile(candidate):
                    return candidate
    raise SystemExit("no Chromium found: pass --chrome or install google-chrome")


def render_chromium(chrome, fixture, width, height, png):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(WRAPPER.format(open(fixture).read()))
        page = fh.name
    try:
        subprocess.run(
            [chrome]
            + CHROME_FLAGS
            + [f"--window-size={width},{height}", f"--screenshot={png}", f"file://{page}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    finally:
        os.unlink(page)
    if not os.path.exists(png) or os.path.getsize(png) == 0:
        raise SystemExit(f"chromium produced no screenshot for {fixture}")


def render_blitz(binary, fixture, width, height, png):
    proc = subprocess.run(
        [binary, fixture, png, str(width), str(height)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise SystemExit(f"blitz failed on {fixture}:\n{proc.stderr}")
    return proc.stderr


def timed(cmd):
    """Run a command and return (wall_seconds, peak_rss_kib).

    Peak RSS is the peak **sum over the command's whole process tree**, sampled
    every 25 ms by tools/measure_rss.py. It used to be `ru_maxrss` from
    `RUSAGE_CHILDREN`, which is one process's high-water mark: a browser that
    hands the page to a renderer was measured at its largest process, not at what
    the host holds. `--rss-mode max` reproduces the old reading.
    """
    rc, _out, err, wall, kib = measure_rss.run_measured(cmd, mode=RSS_MODE)
    if rc != 0:
        raise SystemExit(f"benchmark failed for {cmd[0]}:\n{err}")
    return wall, kib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blitz", default=os.path.join(ROOT, "target", "release", "render"))
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--bench-repeats", type=int, default=5)
    ap.add_argument(
        "--rss-mode",
        choices=("tree", "max"),
        default="tree",
        help="tree = peak sum over the process tree (default); max = the old "
             "single-process reading, for reproducing published figures",
    )
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite tools/parity_baseline.json from this run; read the diff "
             "before committing it",
    )
    args = ap.parse_args()

    global RSS_MODE
    RSS_MODE = args.rss_mode

    chrome = find_chrome(args.chrome)
    if not os.path.isfile(args.blitz):
        raise SystemExit(f"blitz binary not found at {args.blitz} (cargo build --release)")

    fixtures = json.load(open(os.path.join(ROOT, "fixtures.json")))
    for engine in ("blitz", "chromium"):
        os.makedirs(os.path.join(OUT, engine), exist_ok=True)

    rows = []
    counts = {"exact": 0, "within 1px": 0, "DIFF": 0, "MISSING": 0}

    print(f"chromium: {chrome}")
    print(f"blitz:    {args.blitz}")
    print()
    header = f"{'fixture':28s} {'probe':8s} {'chromium':22s} {'blitz':22s} {'verdict'}"
    print(header)
    print("-" * len(header))

    for fixture in fixtures:
        src = os.path.join(ROOT, "fixtures", fixture["file"])
        w, h = fixture["width"], fixture["height"]
        bpng = os.path.join(OUT, "blitz", fixture["name"] + ".png")
        cpng = os.path.join(OUT, "chromium", fixture["name"] + ".png")

        render_blitz(args.blitz, src, w, h, bpng)
        render_chromium(chrome, src, w, h, cpng)

        got = measure.measure(bpng)
        want = measure.measure(cpng)
        if not want:
            # Both engines paint nothing: the fixture asserts agreement on
            # absence (auto cross margins cancel the stretch), so there is no
            # box to compare and the absence itself is the assertion.
            print(f"{fixture['name']:28s} {'-':8s} {'no box in either engine':22s} "
                  f"{'ok (absence)'}")
            rows.append({"fixture": fixture["name"], "text": fixture.get("text", False),
                         "colour": None, "chromium": None, "blitz": None,
                         "delta": None, "verdict": "ok (absence)"})
            continue

        for colour in sorted(want):
            a = want[colour]
            b = got.get(colour)
            if b is None:
                verdict, d = "MISSING", None
            else:
                d = measure.delta(a, b)
                if d == 0:
                    verdict = "exact"
                elif d <= TOLERANCE:
                    verdict = f"ok ({d}px)"
                else:
                    verdict = f"DIFF {d}px"
            key = "exact" if verdict == "exact" else (
                "within 1px" if verdict.startswith("ok") else (
                    "MISSING" if verdict == "MISSING" else "DIFF"))
            counts[key] += 1
            rows.append(
                {
                    "fixture": fixture["name"],
                    "text": fixture.get("text", False),
                    "colour": colour,
                    "chromium": a,
                    "blitz": b,
                    "delta": d,
                    "verdict": verdict,
                }
            )
            print(
                f"{fixture['name']:28s} {colour:8s} "
                f"{fmt(a):22s} {fmt(b):22s} {verdict}"
            )

    # Two buckets, because only one of them can be judged. A text fixture's
    # probes are sized by glyph metrics, and neither engine is given the same
    # font here (an embedded data-URI @font-face would be needed; this harness
    # has none), so a text row measures font availability as much as layout. The
    # text-free bucket is the verdict.
    clean = [r for r in rows if not r["text"]]
    texty = [r for r in rows if r["text"]]

    def tally(subset):
        c = {"exact": 0, "within 1px": 0, "DIFF": 0, "MISSING": 0, "absence": 0}
        for r in subset:
            v = r["verdict"]
            if v == "exact":
                c["exact"] += 1
            elif v == "ok (absence)":
                c["absence"] += 1
            elif v.startswith("ok"):
                c["within 1px"] += 1
            elif v == "MISSING":
                c["MISSING"] += 1
            else:
                c["DIFF"] += 1
        return c

    def line(label, c, n):
        return (
            f"{label} ({n} boxes): {c['exact']} exact, {c['within 1px']} within 1px, "
            f"{c['DIFF']} divergent, {c['MISSING']} missing, {c['absence']} absence"
        )

    print()
    print(line("VERDICT  text-free", tally(clean), len(clean)))
    print(line("reported text-dependent", tally(texty), len(texty)))
    print(
        "  text-dependent rows are confounded by font availability, not layout: "
        "read them as a lower bound."
    )

    bench = benchmark(args, chrome, fixtures)
    json.dump(
        {
            "rows": rows,
            "counts": {"text_free": tally(clean), "text_dependent": tally(texty)},
            "bench": bench,
            "chrome": chrome,
        },
        open(os.path.join(OUT, "parity.json"), "w"),
        indent=2,
    )

    # The gate runs last so the report above is always written, even on a
    # failure: a red build has to leave the evidence behind.
    baseline = json.load(open(BASELINE)) if os.path.isfile(BASELINE) else None
    if args.update_baseline:
        problems, current = gate(clean, fixtures, baseline or {"divergent": {}})
        if baseline and baseline.get("note"):
            current["note"] = baseline["note"]
        with open(BASELINE, "w") as fh:
            json.dump(current, fh, indent=2)
            fh.write("\n")
        print(f"\nBASELINE rewritten: {BASELINE}")
        print(
            f"  {current['text_free_boxes']} boxes, "
            f"{len(current['divergent'])} divergent, "
            f"{len(current['absence'])} absence"
        )
        return result

    if baseline is None:
        # Deleting the file must not disarm the gate.
        print(f"\nGATE  FAIL  no baseline at {BASELINE}")
        print("  create one with --update-baseline and read the diff before committing")
        sys.exit(1)

    problems, current = gate(clean, fixtures, baseline)
    if problems:
        print("\nGATE  FAIL")
        for p in problems:
            print(f"  {p}")
        print(
            "  this corpus is the repo's central claim: a box that diverges and "
            "is not in the baseline is a regression, not a measurement"
        )
        sys.exit(1)
    print(
        f"\nGATE  PASS  {current['text_free_boxes']} boxes, "
        f"{len(current['divergent'])} known divergence(s) within baseline, "
        f"{len(current['absence'])} absence"
    )
    return result


def box_key(row):
    return "{}:{}".format(row["fixture"], row["colour"])


def gate(clean, fixtures, baseline):
    """Compare the text-free corpus against the pinned baseline.

    This harness used to be a report, not a gate: `main()` returned, and every
    `SystemExit` in the file was an *error* path (no Chromium, no binary, a
    fixture that crashed). No code path exited nonzero because a box diverged,
    so the four known divergences passed on every push and forty would have
    passed identically. A measurement that cannot say no is not evidence.

    A baseline entry that stops diverging is a failure too, not a bonus: left
    in place it would silently tolerate the same box regressing later, which is
    how a control disarms itself.
    """
    divergent, missing, absences = {}, [], []
    for r in clean:
        if r["verdict"] == "ok (absence)":
            absences.append(r["fixture"])
        elif r["verdict"] == "MISSING":
            missing.append(box_key(r))
        elif r["verdict"] != "exact" and not str(r["verdict"]).startswith("ok"):
            divergent[box_key(r)] = r["delta"]

    problems = []
    want = baseline.get("divergent", {})
    for key, delta in sorted(divergent.items()):
        if key not in want:
            problems.append(f"NEW divergence {key} at {delta}px")
        elif delta > want[key]:
            problems.append(f"WORSE divergence {key}: {want[key]}px -> {delta}px")
        elif delta < want[key]:
            problems.append(
                f"IMPROVED divergence {key}: {want[key]}px -> {delta}px "
                "(update tools/parity_baseline.json in the commit that fixes it)"
            )
    for key in sorted(set(want) - set(divergent)):
        problems.append(f"BASELINE STALE {key}: pinned at {want[key]}px, no longer divergent")
    for key in missing:
        problems.append(f"MISSING box {key}")
    if baseline.get("text_free_boxes") is not None and len(clean) != baseline["text_free_boxes"]:
        problems.append(f"BOX COUNT {len(clean)} != baseline {baseline['text_free_boxes']}")
    if baseline.get("fixtures") is not None and len(fixtures) != baseline["fixtures"]:
        problems.append(f"FIXTURE COUNT {len(fixtures)} != baseline {baseline['fixtures']}")
    if sorted(absences) != sorted(baseline.get("absence", [])):
        problems.append(
            f"ABSENCE SET {sorted(absences)} != baseline {sorted(baseline.get('absence', []))}"
        )
    return problems, {
        "fixtures": len(fixtures),
        "text_free_boxes": len(clean),
        "divergent": dict(sorted(divergent.items())),
        "absence": sorted(absences),
    }


def fmt(box):
    if box is None:
        return "-"
    return "x={} y={} w={} h={}".format(*box)


def benchmark(args, chrome, fixtures):
    """Wall time and peak RSS for both engines on one representative document."""
    fixture = next(f for f in fixtures if f["name"] == "content-box-flex")
    src = os.path.join(ROOT, "fixtures", fixture["file"])
    w, h = fixture["width"], fixture["height"]
    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, "page.html")
    open(page, "w").write(WRAPPER.format(open(src).read()))

    engines = {
        "blitz": [
            args.blitz, src, os.path.join(tmp, "b.png"), str(w), str(h)
        ],
        "chromium": [chrome]
        + CHROME_FLAGS
        + [f"--window-size={w},{h}", f"--screenshot={os.path.join(tmp, 'c.png')}", f"file://{page}"],
    }

    print()
    print(f"KPI ({fixture['name']} at {w}x{h}, {args.bench_repeats} runs, median)")
    print(f"{'engine':10s} {'wall s':>9s} {'peak RSS MiB':>14s}")
    result = {}
    for name, cmd in engines.items():
        walls, rss = [], []
        for _ in range(args.bench_repeats):
            wall, rss_kib = timed(cmd)
            walls.append(wall)
            rss.append(rss_kib / 1024.0)
        result[name] = {"wall_s": statistics.median(walls), "peak_rss_mib": statistics.median(rss)}
        print(f"{name:10s} {result[name]['wall_s']:9.3f} {result[name]['peak_rss_mib']:14.1f}")
    shutil.rmtree(tmp, ignore_errors=True)
    return result


if __name__ == "__main__":
    main()
