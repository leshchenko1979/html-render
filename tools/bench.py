#!/usr/bin/env python3
"""Measure this renderer against Chromium on a document anyone can rebuild.

    python3 tools/bench.py --bin ./html-render

Every figure the README advertises is supposed to be checkable by a reader. The
deck those figures came from was not in this repo, so nobody could check them;
this probe replaces it with a generated deck (`tools/bench_deck.py`, seeded, so
the same seed writes the same bytes anywhere) and measures **both** engines the
same way:

  * one process per engine, one tall PNG out;
  * wall clock = median of `--repeats` runs, spawn to exit;
  * peak memory = the whole **process tree**, sampled every 25 ms
    (`tools/measure_rss.py`), because a browser is a browser plus renderers plus
    helpers and per-process maxima understate it;
  * Chromium is driven exactly as `tools/run_parity.py` drives it, at the same
    device scale, so the two images are the same pixel grid.

Three honesty rules, because each one has already bitten this repo:

  1. **Assert the output is the document, not a viewport.** A screenshot of a
     5760 px document clipped to 720 px is a plausible picture of the wrong
     thing. The probe checks each PNG's IHDR against the expected size and
     fails if they differ.
  2. **Chromium unavailable is recorded, never skipped.** No binary, a crash or
     a wrong-sized screenshot all become `chromium.status = unavailable` with
     the measured reason. html-render is still measured, and the JSON says so
     in a field rather than in a comment.
  3. **Fidelity comes from the same two files.** `tools/bench_compare.py`
     compares them pixel by pixel; nothing is transcribed by hand.

Writes `bench/results.json` (host metadata, every figure, every command line)
and prints a markdown table. Exit 0 when html-render was measured, 1 when it
was not: a probe that cannot measure its own engine must not report success.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bench_compare  # noqa: E402
import bench_deck  # noqa: E402
import measure_rss  # noqa: E402
from run_parity import CHROME_FLAGS, find_chrome  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "bench", "results.json")
SCALE = 2
RUN_TIMEOUT = 900


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def expected_size(scale=SCALE):
    """The pixel size a correct render of the deck must have."""
    return bench_deck.SLIDE_W * scale, bench_deck.DOC_H * scale


def assert_size(path, want, engine):
    """Raise unless `path` is a PNG of exactly `want` pixels."""
    got = bench_compare.ihdr(path)
    if got != tuple(want):
        raise SystemExit(
            "error: %s wrote %dx%d, want %dx%d (%s)"
            % (engine, got[0], got[1], want[0], want[1], path)
        )
    return got


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def binary_version(path):
    try:
        import subprocess

        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or out.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return "unreadable: %s" % exc


def chrome_flags(scale):
    """`run_parity.py`'s flags, with the device scale the deck asks for."""
    flags = [f for f in CHROME_FLAGS if not f.startswith("--force-device-scale-factor")]
    return flags + ["--force-device-scale-factor=%d" % scale]


def run_engine(label, cmd, cwd, png, want, repeats, timeout=RUN_TIMEOUT, missing_is_unavailable=False):
    """Run `cmd` `repeats` times, returning the measured record or a refusal.

    `missing_is_unavailable` separates the two ways an engine can fail us: our
    own engine failing is a `failed` result (the probe could not do its job),
    while a browser that crashes or cannot launch on this host is `unavailable`
    with the measured reason. Collapsing those two into one status would let a
    broken Chromium read as a broken renderer.
    """
    walls = []
    peaks = []
    commands = []
    for i in range(repeats):
        if os.path.exists(png):
            os.unlink(png)
        rc, out, err, wall, peak_kib = measure_rss.run_measured(
            cmd, mode="tree", timeout=timeout, cwd=cwd
        )
        commands.append(" ".join(cmd))
        refusal = "failed" if not missing_is_unavailable else "unavailable"
        if rc != 0:
            return {
                "status": refusal,
                "reason": "run %d exited %d: %s" % (i + 1, rc, (err or out).strip()[-400:]),
                "command": " ".join(cmd),
            }
        if not os.path.exists(png):
            return {
                "status": refusal,
                "reason": "run %d wrote no PNG: %s" % (i + 1, (err or out).strip()[-400:]),
                "command": " ".join(cmd),
            }
        try:
            assert_size(png, want, label)
        except SystemExit as exc:
            return {"status": refusal, "reason": str(exc), "command": " ".join(cmd)}
        walls.append(round(wall, 3))
        peaks.append(round(peak_kib / 1024.0, 1))

    return {
        "status": "measured",
        "command": commands[0],
        "repeats": repeats,
        "wall_s": walls,
        "wall_median_s": round(statistics.median(walls), 3),
        "peak_tree_mib": round(statistics.median(peaks), 1),
        "peak_tree_mib_max": max(peaks),
        "png": png,
    }


def host_facts():
    facts = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    facts["mem_total_mib"] = round(int(line.split()[1]) / 1024.0)
                    break
    except OSError:
        pass
    try:
        import subprocess

        facts["glibc"] = (
            subprocess.run(["ldd", "--version"], capture_output=True, text=True, timeout=30)
            .stdout.strip()
            .splitlines()[0]
        )
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return facts


def markdown(results):
    lines = [
        "| engine | wall (s, median) | peak tree RSS (MiB) | PNG |",
        "|---|---|---|---|",
    ]
    for name, rec in results["engines"].items():
        if rec["status"] == "measured":
            lines.append(
                "| %s | %s | %s | %dx%d |"
                % (
                    name,
                    rec["wall_median_s"],
                    rec["peak_tree_mib"],
                    results["deck"]["expected_px"][0],
                    results["deck"]["expected_px"][1],
                )
            )
        else:
            # One line, not the whole stack trace: the table is a summary and the
            # full reason is in results.json.
            reason = (rec.get("reason") or "").strip().splitlines()
            lines.append(
                "| %s | %s | %s | - |"
                % (name, rec["status"], reason[0][:90] if reason else "")
            )
    fid = results.get("fidelity")
    if fid:
        lines.append("")
        lines.append(
            "fidelity: %.2f%% of %d px off by >16/255, mean diff %.2f, ink %.2f%% vs %.2f%%"
            % (
                fid["over16_pct"],
                fid["pixels"],
                fid["mean_diff"],
                fid["ink_ref_pct"],
                fid["ink_engine_pct"],
            )
        )
    return "\n".join(lines)


def probe(args):
    deckdir = args.deck
    if deckdir is None:
        deckdir = tempfile.mkdtemp(prefix="bench-deck-")
    html = bench_deck.generate(deckdir, args.seed)
    want = expected_size(args.scale)

    results = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deck": {
            "seed": args.seed,
            "slides": bench_deck.SLIDES,
            "css_px": [bench_deck.SLIDE_W, bench_deck.DOC_H],
            "scale": args.scale,
            "expected_px": list(want),
            "html_md5": md5(html),
            "plates_md5": {name: md5(os.path.join(deckdir, name)) for name, _ in bench_deck.PLATES},
            "generator": "tools/bench_deck.py",
        },
        "host": host_facts(),
        "engines": {},
    }

    blitz_png = os.path.join(deckdir, "blitz.png")
    blitz_cmd = [
        os.path.abspath(args.bin),
        os.path.basename(html),
        os.path.basename(blitz_png),
        str(bench_deck.SLIDE_W),
        str(bench_deck.DOC_H),
        str(args.scale),
        "--full",
    ]
    results["probe"] = {
        "path": os.path.abspath(args.bin),
        "sha256": sha256(args.bin) if os.path.exists(args.bin) else None,
        "version": binary_version(os.path.abspath(args.bin)),
    }
    results["engines"]["html-render"] = run_engine(
        "html-render", blitz_cmd, deckdir, blitz_png, want, args.repeats
    )
    if results["engines"]["html-render"]["status"] != "measured":
        results["engines"]["html-render"]["deck_dir"] = deckdir
        return results, deckdir, html

    chrome_png = os.path.join(deckdir, "chromium.png")
    try:
        chrome = find_chrome(args.chrome)
    except SystemExit as exc:
        results["engines"]["chromium"] = {"status": "unavailable", "reason": str(exc)}
        return results, deckdir, html

    chrome_cmd = (
        [chrome]
        + chrome_flags(args.scale)
        + [
            "--window-size=%d,%d" % (bench_deck.SLIDE_W, bench_deck.DOC_H),
            "--screenshot=%s" % os.path.basename(chrome_png),
            "file://%s" % os.path.abspath(html),
        ]
    )
    results["engines"]["chromium"] = run_engine(
        "chromium", chrome_cmd, deckdir, chrome_png, want, args.repeats,
        missing_is_unavailable=True,
    )
    if results["engines"]["chromium"]["status"] == "measured":
        results["fidelity"] = bench_compare.fidelity(chrome_png, blitz_png, decoder=args.decoder)
        results["engines"]["chromium"]["version"] = (
            __import__("subprocess")
            .run([chrome, "--version"], capture_output=True, text=True, timeout=60)
            .stdout.strip()
        )
    return results, deckdir, html


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", default="./html-render", help="the html-render binary to measure")
    ap.add_argument("--chrome", default=None, help="Chromium binary (default: discover)")
    ap.add_argument("--seed", type=int, default=bench_deck.SEED_DEFAULT)
    ap.add_argument("--scale", type=int, default=SCALE)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--decoder", default="auto", choices=("auto", "pillow", "stdlib"))
    ap.add_argument("--deck", default=None, help="keep the generated deck in this directory")
    ap.add_argument("--out", default=RESULTS)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        import bench_selftest

        return bench_selftest.main()

    if not os.path.exists(args.bin):
        raise SystemExit("error: no binary at %s (pass --bin)" % args.bin)

    results, deckdir, html = probe(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(markdown(results))
    print("\nwrote %s" % args.out)
    print("deck kept in %s" % deckdir)

    if results["engines"]["html-render"]["status"] != "measured":
        print("error: html-render was not measured: %s" % results["engines"]["html-render"]["reason"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
