#!/usr/bin/env python3
"""Contract test: a substituted font is reported (#12).

fontconfig always resolves something. A document that names a family the host
does not have is rendered in whatever the substitution picks, at exit `0` with
nothing on stderr -- a plausible PNG of the wrong face. Measured on
`0.3.0-beta.8`: `font-family:'Absolutely Nonexistent Font XYZ 9999'` painted
8238 non-white pixels of DejaVu Sans and said nothing.

`src/main.rs` now reads the computed `font-family` of every element and reports
each family that no installed font provides. Reporting is the whole fix, so the
exit code does not change.

Legs:

  a-default   no font-family at all      -> no warning
  b-missing   a family nothing provides  -> warns, names it, rc 0
  c-generic   `sans-serif`               -> no warning: a generic keyword names
                                            a class of face, so substituting is
                                            the request being honoured
  d-truth     every run above            -> the "no installed fonts were
                                            visible" line is ABSENT

Leg `d` is the control that makes the other three mean anything. A collection
with zero families cannot distinguish "this document is clean" from "this
instrument is blind", so the run says so instead -- and this leg fails if that
message ever appears in a run that is supposed to be measuring something.

What this does NOT cover: the `@font-face` exemption. Building a document that
embeds a real face needs a font file in the fixture set, and the exemption's
failure mode is a spurious warning on a document that renders correctly, not a
wrong render. Recorded here rather than claimed as tested.

Usage:
    python3 tools/test_fonts.py --bin /path/to/html-render
    python3 tools/test_fonts.py --bin /path/to/beta.8   # b fails
"""

import argparse
import os
import subprocess
import sys
import tempfile

BLIND = "no installed fonts were visible"
MISSING = "is not installed"
NONSENSE = "Absolutely Nonexistent Font XYZ 9999"

CASES = [
    (
        "a-default",
        "<div style='font-size:32px'>Hello World Test</div>",
        (),
        (MISSING,),
    ),
    (
        "b-missing",
        f"<div style='font-family:\"{NONSENSE}\";font-size:32px'>Hello World Test</div>",
        (NONSENSE, MISSING),
        (BLIND,),
    ),
    (
        "c-generic",
        "<div style='font-family:sans-serif;font-size:32px'>Hello World Test</div>",
        (),
        (MISSING,),
    ),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the html-render binary")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = ap.parse_args()

    if not os.access(args.bin, os.X_OK):
        print(f"not executable: {args.bin}")
        return 2

    tmp = tempfile.mkdtemp(prefix="fonts-")
    legs = []
    blind_seen = []

    for name, body, want, reject in CASES:
        html = os.path.join(tmp, f"{name}.html")
        png = os.path.join(tmp, f"{name}.png")
        with open(html, "w", encoding="utf-8") as handle:
            handle.write(f"<!doctype html><body>{body}</body>")
        proc = subprocess.run(
            [args.bin, html, png, "600", "200", "1", "--document"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        problems = []
        # The fix reports; it must not change the contract for a caller that
        # only looks at the exit code.
        if proc.returncode != 0:
            problems.append(f"rc={proc.returncode}, want 0")
        if not os.path.exists(png):
            problems.append("no PNG written")
        for needle in want:
            if needle not in proc.stderr:
                problems.append(f"stderr lacks {needle!r}")
        for needle in reject:
            if needle in proc.stderr:
                problems.append(f"stderr carries {needle!r}")
        if BLIND in proc.stderr:
            blind_seen.append(name)
        legs.append((name, not problems, "; ".join(problems) or f"rc={proc.returncode}"))

    # The control: a run that cannot see fonts must say so, and no run in this
    # set is allowed to be in that state.
    problems = [f"{name} reported a blind font check" for name in blind_seen]
    legs.append(("d-truth", not problems, "; ".join(problems) or "check had fonts to compare against"))

    failed = 0
    for name, ok, detail in legs:
        print(f"{'ok  ' if ok else 'FAIL'} {name:10s} {detail}")
        failed += 0 if ok else 1

    print()
    if failed:
        print(f"{failed} of {len(legs)} legs FAIL")
        if not args.keep:
            subprocess.run(["rm", "-rf", tmp], check=False)
        return 1
    print(f"all {len(legs)} legs hold")
    if args.keep:
        print(f"kept: {tmp}")
    else:
        subprocess.run(["rm", "-rf", tmp], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
