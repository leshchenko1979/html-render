"""Measure Blitz against Chromium on the geometry corpus.

For every fixture both engines render the same document at the same viewport,
and both PNGs are measured with the same instrument (`tools/measure.py`). A row
therefore reports a difference between the two engines, not between two rulers.

Usage: python3 tools/run_parity.py [--blitz path] [--chrome path]
"""

import argparse
import json
import os
import re
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

# The document wrapper carries a DOCTYPE, and that is load-bearing.
#
# Without it Chromium renders in QUIRKS mode while Blitz always lays out in
# standards mode, so every pinned delta was a mode mismatch rather than an
# engine difference: a cell's declared `height` resolves against the content
# box in quirks mode (padding lands on top of it) and against the border box in
# standards mode, which is 13 px on one probe of fixture 32; a `<br>` strut is
# 4 px in quirks mode and `line-height` in standards mode, which was all four
# of fixture 31's deltas. `tools/chrome_reference.txt` pins the Chrome VERSION
# and could not catch this: the same build renders both ways.
WRAPPER = (
    "<!DOCTYPE html>"
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

# The layout mode is MEASURED, not trusted.
#
# Blitz has no quirks mode: `blitz-html` drops the doctype (`html_sink.rs:111`)
# and all 29 `QuirksMode` sites in `blitz-dom` pass `NoQuirks`, so a pageplate
# render is standards-mode by construction. The gate is only meaningful if the
# Chromium side is in standards mode too, and the mode is selected by the
# doctype token ALONE. Losing it raises nothing: it silently moves Chromium's
# table arithmetic, and the gate then reports the difference as an engine
# divergence. That is what happened between 2026-11-03 and 2026-11-17 — four
# `<br>` rows off by up to 54 px and a 13 px "cell-height defect", all filed
# upstream as Blitz bugs, all of them Chromium's legacy rules.
#
# The probe below is the discriminator: a cell's declared `height` is a
# content-box size in standards mode (16 + 6 + 6 = 28 px painted) and a
# border-box size in quirks mode (16 px). Both figures are measured against the
# pinned Chrome build, not derived. The no-doctype half is the POSITIVE CONTROL:
# if the two halves stop differing, the probe can no longer see the mode and the
# guard has become decorative.
MODE_PROBE_BODY = (
    '<table style="border-collapse:separate"><tr>'
    '<td style="height:16px;padding:6px;background:#000000"></td>'
    "</tr></table>"
)
MODE_PROBE_W, MODE_PROBE_H = 200, 100
MODE_STANDARDS_PX = 28  # measured: Chrome 157.0.8103.78, standards mode
MODE_QUIRKS_PX = 16     # measured: same build, doctype token removed

# A difference of one pixel is sub-pixel rounding of a fractional size, not a
# layout disagreement. Anything larger is reported.
TOLERANCE = 1

# The corpus baseline is a property of (Blitz x ONE Chrome build). The runner
# image's Chrome moves on GitHub's schedule, and when it does the eleven pinned
# per-box deltas move with it — silently, because the natural remedy
# (`--update-baseline`) would re-pin Blitz's known defects to a new engine
# without anyone deciding that a Chrome-side change is ours (#34).
#
# So the engine that produced the committed baseline is recorded in this file,
# and a run against a different one stops with an instruction instead of
# quietly measuring against a reference nobody chose.
CHROME_REFERENCE = os.path.join(ROOT, "tools", "chrome_reference.txt")


def chrome_version(chrome):
    """`Google Chrome 157.0.8103.78` -> `157.0.8103.78`, or None."""
    try:
        out = subprocess.run(
            [chrome, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "") + (out.stderr or "")
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
    return m.group(1) if m else None


def assert_reference_engine(chrome, allow_drift):
    """Refuse to measure the corpus against an engine nobody chose.

    Skipped when the reference file is absent, so a fresh clone with no recorded
    reference still measures: this is a guard against SILENT drift, not a
    requirement that a reference exist.
    """
    if allow_drift:
        print(f"chromium: drift allowed, {chrome} is the reference for this run")
        return
    if not os.path.isfile(CHROME_REFERENCE):
        return
    want = open(CHROME_REFERENCE).read().strip()
    got = chrome_version(chrome)
    if got is None:
        raise SystemExit(
            f"cannot read a version out of {chrome}; the corpus baseline is "
            "pinned to a specific engine, so the run cannot be interpreted. "
            "Pass --allow-chrome-drift to measure anyway."
        )
    if got != want:
        raise SystemExit(
            f"reference engine changed: the committed baseline was measured "
            f"with Chrome {want}, this run has {got}.\n"
            f"Nothing is wrong with the build — the reference moved.\n"
            f"Re-measure deliberately: review the delta in "
            f"tools/parity_baseline.json, then either update "
            f"tools/chrome_reference.txt to {got} (if the new engine is now "
            f"the reference) or pin a Chrome build in parity.yml (if it is "
            f"not). To measure without deciding, pass --allow-chrome-drift."
        )
    print(f"chromium: {chrome} (version {got}, the recorded reference)")



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


def render_page(chrome, page, width, height, png):
    """Screenshot one already-wrapped HTML file."""
    subprocess.run(
        [chrome]
        + CHROME_FLAGS
        + [f"--window-size={width},{height}", f"--screenshot={png}", f"file://{page}"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )
    if not os.path.exists(png) or os.path.getsize(png) == 0:
        raise SystemExit(f"chromium produced no screenshot for {page}")

def render_chromium(chrome, fixture, width, height, png):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(WRAPPER.format(open(fixture).read()))
        page = fh.name
    try:
        render_page(chrome, page, width, height, png)
    finally:
        os.unlink(page)

def assert_standards_mode(chrome):
    """Refuse to measure the corpus unless Chromium is in standards mode.

    The mode is read off a rendered pixel rather than off `WRAPPER`, because a
    wrapper is a declaration and the mode is an outcome: this renders through
    the same wrapper, the same flags and the same instrument as the corpus, so
    it fails for the same reasons the corpus would be wrong.

    Two failures are distinguished on purpose. A standards half that reads the
    quirks value means the probe page lost its doctype token — the wrapper is
    the bug, and re-baselining would bury it. Two halves that agree mean the
    probe itself stopped discriminating, so the guard proves nothing.
    """
    tmp = tempfile.mkdtemp()
    try:
        wrapped = WRAPPER.format(MODE_PROBE_BODY)
        # The control differs from the probe by the doctype token and nothing
        # else, because that token is the only thing that selects the mode.
        pages = {
            "standards": wrapped,
            "quirks": wrapped.replace("<!DOCTYPE html>", "", 1),
        }
        heights = {}
        for label, html in pages.items():
            page = os.path.join(tmp, label + ".html")
            png = os.path.join(tmp, label + ".png")
            with open(page, "w") as fh:
                fh.write(html)
            render_page(chrome, page, MODE_PROBE_W, MODE_PROBE_H, png)
            boxes = measure.measure(png)
            if not boxes.get("black"):
                raise SystemExit(
                    "HARNESS FAULT: the layout-mode probe painted no measurable "
                    f"cell ({label}). Fix the probe before trusting any row this "
                    "run reports."
                )
            heights[label] = boxes["black"][3]  # (x, y, w, h) — the painted height
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if heights["standards"] != MODE_STANDARDS_PX:
        raise SystemExit(
            f"HARNESS FAULT: Chromium laid the probe cell out "
            f"{heights['standards']} px tall, not {MODE_STANDARDS_PX}.\n"
            f"  {MODE_QUIRKS_PX} px is the quirks-mode value: a cell's declared "
            "`height` resolved against the border box instead of the content "
            "box, which means the probe page carries no doctype token.\n"
            "  Blitz has no quirks mode, so every row this run reports would be "
            "a layout-mode mismatch attributed to the engine. Fix WRAPPER; do "
            "not re-baseline."
        )
    if heights["quirks"] == heights["standards"]:
        raise SystemExit(
            f"HARNESS FAULT: the mode probe reads {heights['standards']} px with "
            "and without a doctype, so it can no longer tell the two modes "
            "apart and this guard proves nothing.\n"
            f"  Expected {MODE_STANDARDS_PX} px with the doctype and "
            f"{MODE_QUIRKS_PX} px without. Fix the probe."
        )
    print(
        f"layout mode: standards, verified by probe "
        f"({heights['standards']} px cell; {heights['quirks']} px with the "
        f"doctype removed, so the probe sees the difference)"
    )


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
    ap.add_argument("--blitz", default=os.path.join(ROOT, "target", "release", "pageplate"))
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
    ap.add_argument(
        "--allow-chrome-drift",
        action="store_true",
        help="measure even when the Chromium version differs from "
             "tools/chrome_reference.txt; for a deliberate re-measurement only",
    )
    args = ap.parse_args()

    global RSS_MODE
    RSS_MODE = args.rss_mode

    chrome = find_chrome(args.chrome)
    assert_reference_engine(chrome, args.allow_chrome_drift)
    assert_standards_mode(chrome)
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
            f"{label} ({n} rows): {c['exact']} exact, {c['within 1px']} within 1px, "
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
            f"  {current['text_free_rows']} rows, "
            f"{len(current['divergent'])} divergent, "
            f"{len(current['absence'])} absence"
        )
        return 0

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
        f"\nGATE  PASS  {current['text_free_rows']} rows, "
        f"{len(current['divergent'])} known divergence(s) within baseline, "
        f"{len(current['absence'])} absence"
    )
    return 0


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
    # Keyed on text_free_rows, not text_free_boxes: `clean` is a bucket of
    # verdict ROWS, and one of them is an absence assertion that has no box
    # at all. A missing key is a STALE baseline, never a skip -- renaming the
    # field must not disarm the count check.
    if "text_free_rows" not in baseline:
        problems.append(
            "BASELINE STALE no text_free_rows key (regenerate with --update-baseline)"
        )
    elif len(clean) != baseline["text_free_rows"]:
        problems.append(f"ROW COUNT {len(clean)} != baseline {baseline['text_free_rows']}")
    if baseline.get("fixtures") is not None and len(fixtures) != baseline["fixtures"]:
        problems.append(f"FIXTURE COUNT {len(fixtures)} != baseline {baseline['fixtures']}")
    if sorted(absences) != sorted(baseline.get("absence", [])):
        problems.append(
            f"ABSENCE SET {sorted(absences)} != baseline {sorted(baseline.get('absence', []))}"
        )
    return problems, {
        "fixtures": len(fixtures),
        "text_free_rows": len(clean),
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
