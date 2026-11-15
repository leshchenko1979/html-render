#!/usr/bin/env python3
"""Contract test: non-Latin shaping, gated rather than merely reported (#14).

`tools/run_parity.py` reports text-dependent rows separately and does not gate
them, because without an identical embedded font they measure font availability
as much as layout. That is the right call for *Chromium agreement*, and the wrong
one for the properties below, which are unambiguous by specification and need no
reference renderer:

  * text in an RTL script is ordered right-to-left, first word rightmost;
  * combining marks stack on their base and add no advance width.

What legs `a` and `b` do NOT test is the CSS `direction` property. Measured: with
`direction:rtl` removed from `arabic-joining.html`, or flipped to `ltr`, the
geometry is byte-identical -- red stays right of blue in all three cases. The
ordering comes from the Unicode bidi algorithm applied to the text itself, which
is a real property worth gating, but it is not the CSS property. An LTR word in
an RTL container is the isolating case, and it is a separate fixture
(`direction-rtl-latin`) reported by the parity harness rather than gated here,
because gating it would pin whichever behaviour this engine currently has.

  a-rtl-arabic    red word is RIGHT of the blue one   (bidi ordering)
  b-rtl-hebrew    red word is RIGHT of the blue one   (bidi ordering)
  c-combining     marks are TALLER, not WIDER         (stacked, not advanced)
  d-ltr-control   red span is LEFT of the blue one    (the control for a and b)

Leg `d` is what makes `a` and `b` mean anything. Asserting only "red is right of
blue" passes for an engine that puts the first span right in every case, so the
control renders the same two spans in Latin text under the default direction and
requires the opposite order. An engine that got RTL right by accident would fail
one of the two.

What this does NOT cover: CJK. No font on the runner covers it, and a character
with no covering font renders as a tofu box, so a CJK leg would measure the
absence of a font. Measured: `日`, `本` and `語` at 40 px produce byte-identical
PNGs. Recorded in GATES.md §Corpus rather than claimed as tested.

Usage:
    python3 tools/test_scripts.py --bin /path/to/html-render
"""

import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure import measure  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The two spans every leg compares, in the order they appear in the markup.
FIRST, SECOND = "red", "blue"

PLAIN_CONTROL = (
    '<div style="font-family:\'DejaVu Sans\';font-size:28px;line-height:40px;'
    'width:320px;height:60px">'
    '<span style="color:#ff0000">e</span>'
    '<span style="color:#0000ff">e</span></div>'
)

LTR_CONTROL = (
    '<div style="font-family:\'DejaVu Sans\';font-size:28px;line-height:40px;'
    'width:320px;height:60px">'
    '<span style="color:#ff0000">Alpha</span> '
    '<span style="color:#0000ff">Beta</span></div>'
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True, help="path to the html-render binary")
    ap.add_argument("--keep", action="store_true", help="keep the temp dir")
    args = ap.parse_args()

    if not os.path.exists(args.bin):
        print(f"FAIL: no such binary: {args.bin}")
        return 1

    tmp = tempfile.mkdtemp(prefix="scripts-")
    legs = []

    def render(src, name, w=320, h=60):
        out = os.path.join(tmp, f"{name}.png")
        proc = subprocess.run(
            [args.bin, src, out, str(w), str(h)],
            capture_output=True,
            text=True,
        )
        return proc, out

    def boxes(out):
        return measure(out) if os.path.exists(out) else {}

    def fixture_leg(name, fixture, check, want):
        """Render a corpus fixture and apply `check` to its two probe boxes."""
        src = os.path.join(ROOT, "fixtures", fixture)
        proc, out = render(src, name)
        b = boxes(out)
        if FIRST not in b or SECOND not in b:
            legs.append((name, False, f"probes missing: {sorted(b)} rc={proc.returncode}"))
            return
        first, second = b[FIRST], b[SECOND]
        got = check(first, second)
        legs.append((
            name,
            got == want,
            f"{FIRST}={first} {SECOND}={second} got={got} wanted={want}",
        ))

    # a, b — RTL order. The first word must be to the RIGHT of the second.
    def first_is_right(first, second):
        return first[0] > second[0]

    fixture_leg("a-rtl-arabic", "arabic-joining.html", first_is_right, True)
    fixture_leg("b-rtl-hebrew", "hebrew-rtl.html", first_is_right, True)

    # c — combining marks stack ABOVE the base and add no advance width, so the
    # marked glyph starts higher on the same width. The margin is not cosmetic:
    # two IDENTICAL `e` glyphs measure 1 px apart in height (14 vs 13) because
    # antialiased edges threshold differently, so a bare `>` comparison is
    # satisfied by noise. Measured with the marks: top edge 7 vs 15, width 13 vs
    # 13. Requiring 4 px of vertical separation rejects the artifact while
    # sitting well inside the 8 px the marks actually add.
    STACK_MARGIN = 4

    def stacked_not_advanced(first, second):
        starts_higher = first[1] <= second[1] - STACK_MARGIN
        same_width = first[2] == second[2]
        return starts_higher and same_width

    fixture_leg("c-combining", "combining-marks.html", stacked_not_advanced, True)

    # d — the control. Same two spans, default direction: the first must be LEFT.
    control = os.path.join(tmp, "d-ltr.html")
    with open(control, "w", encoding="utf-8") as fh:
        fh.write(LTR_CONTROL)
    proc, out = render(control, "d-ltr")
    b = boxes(out)
    if FIRST not in b or SECOND not in b:
        legs.append(("d-ltr-control", False, f"probes missing: {sorted(b)} rc={proc.returncode}"))
    else:
        first, second = b[FIRST], b[SECOND]
        ok = first[0] < second[0]
        legs.append((
            "d-ltr-control",
            ok,
            f"{FIRST}={first} {SECOND}={second} first_is_left={ok}",
        ))

    # e — the control for c. Two bare `e` glyphs are the same width and neither
    # starts higher, so the combining predicate must REJECT them. Without this
    # leg, c would pass on a document with no combining marks at all.
    plain = os.path.join(tmp, "e-plain.html")
    with open(plain, "w", encoding="utf-8") as fh:
        fh.write(PLAIN_CONTROL)
    proc, out = render(plain, "e-plain")
    b = boxes(out)
    if FIRST not in b or SECOND not in b:
        legs.append(("e-plain-control", False, f"probes missing: {sorted(b)} rc={proc.returncode}"))
    else:
        first, second = b[FIRST], b[SECOND]
        rejected = not stacked_not_advanced(first, second)
        legs.append((
            "e-plain-control",
            rejected,
            f"{FIRST}={first} {SECOND}={second} predicate_rejected={rejected}",
        ))

    failed = 0
    for name, ok, detail in legs:
        print(f"{'ok  ' if ok else 'FAIL'} {name:16s} {detail}")
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
