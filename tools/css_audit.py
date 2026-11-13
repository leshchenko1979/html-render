#!/usr/bin/env python3
"""Report CSS declarations that change no pixel, by removing one at a time.

The question three reviewers asked is "which declarations did you drop?". A
hand-maintained list of supported properties cannot answer it: it rots the
moment Blitz updates, and a stale list reports a dropped declaration as fine —
the same silent-rot failure the rest of these tools exist to catch.

A declaration's effect is measurable instead. Render the file once, then
re-render once per declaration with exactly that declaration removed, and diff
the PNGs. A removal that changes no pixel means the declaration is INERT:
either the renderer dropped it, or something else already sets that value.

The report says "no pixel effect", never "unsupported". A declaration can be
honoured and still invisible — a `color` whose text all lives inside a child
that overrides it — so claiming "unsupported" from a pixel diff would be a
confident wrong cause, which is the error this repo keeps having to withdraw.

    python3 tools/css_audit.py --bin target/release/render assets/demo-deck.html

Cost is one render per declaration (~0.3 s each), so a 60-declaration card
takes about 20 s: cheaper than a human audit and it needs no knowledge of
Blitz internals. At-rules (`@media`, `@page`, `@font-face`, `@keyframes`) are
counted and skipped, not audited: their contents are conditional or non-visual,
so removing one declaration from inside them measures a state the file may
never render in.

Audit at the size you ship. A declaration can be inert at 1280x720 and
load-bearing at 794x1123, so the numbers below belong to `--width/--height`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

from measure import read_png

STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
STYLE_ATTR = re.compile(r"""style\s*=\s*"([^"]*)\"""", re.I)
TAG_NAME = re.compile(r"<\s*([a-zA-Z][a-zA-Z0-9-]*)")


class Decl:
    __slots__ = ("selector", "prop", "value", "line", "start", "end", "where")

    def __init__(self, selector, prop, value, line, start, end, where):
        self.selector = selector
        self.prop = prop
        self.value = value
        self.line = line
        self.start = start
        self.end = end
        self.where = where

    def as_dict(self):
        return {
            "line": self.line,
            "where": self.where,
            "selector": self.selector,
            "property": self.prop,
            "value": self.value,
        }


def line_of(src, offset):
    return src.count("\n", 0, offset) + 1


def _split_prop(body):
    """Split `prop: value` at the first colon outside parentheses."""
    paren = 0
    for i, ch in enumerate(body):
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif ch == ":" and paren == 0:
            return body[:i].strip(), body[i + 1 :].strip()
    return None, None


def _collect(css, start, stop, selector, base, src, where, out):
    """Collect declarations in css[start:stop], one per `;` at paren depth 0."""
    seg = start
    paren = 0
    i = start
    while i < stop:
        ch = css[i]
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == ";" and paren == 0:
            _add(css, seg, i, selector, base, src, where, out, semi=True)
            seg = i + 1
        i += 1
    _add(css, seg, stop, selector, base, src, where, out, semi=False)


def _add(css, s, e, selector, base, src, where, out, semi):
    raw = css[s:e]
    if not raw.strip():
        return
    lead = len(raw) - len(raw.lstrip())
    prop, value = _split_prop(raw.strip())
    if not prop or not value:
        return
    start = base + s + lead
    end = base + e + (1 if semi else 0)
    out.append(Decl(selector, prop, value, line_of(src, start), start, end, where))


def _mask_comments(css):
    """Blank out /* … */ in place, keeping the length and the newlines.

    Masking rather than stripping is what keeps every recorded offset and line
    number pointing at the original file. Without it a comment before a rule
    becomes part of that rule's SELECTOR, which is how the first run of this
    tool reported a `box-sizing` declaration as belonging to
    `/* Four slides, layout only… */`.
    """
    out = list(css)
    i, n = 0, len(css)
    while i < n - 1:
        if css[i] == "/" and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            end = n if end < 0 else end + 2
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out)


def _scan_block(css, base, src, out, skipped):
    """Walk one <style> block: rules are audited, at-rules are skipped."""
    css = _mask_comments(css)
    i, n = 0, len(css)
    prelude_start = 0
    while i < n:
        ch = css[i]
        if ch == "{":
            prelude = css[prelude_start:i].strip()
            depth, j = 1, i + 1
            while j < n and depth:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            if prelude.startswith("@"):
                # @media / @page / @font-face / @keyframes: conditional or
                # non-visual, so a declaration removed from inside one measures
                # a state the file may never render in.
                skipped.append(prelude.split("{")[0].strip())
            elif prelude:
                _collect(css, i + 1, j - 1, prelude, base, src, "style", out)
            i = j
            prelude_start = j
            continue
        if ch == "}":
            prelude_start = i + 1
        i += 1


def _attr_selector(src, offset):
    """Name the element a style="" attribute sits on: tag#id, tag.class or tag."""
    head = src.rfind("<", 0, offset)
    if head < 0:
        return "style="
    m = TAG_NAME.match(src, head)
    tag = m.group(1).lower() if m else "element"
    ident = re.search(r"""\bid\s*=\s*["']([^"']+)["']""", src[head:offset])
    if ident:
        return f"{tag}#{ident.group(1)}"
    cls = re.search(r"""\bclass\s*=\s*["']([^"']+)["']""", src[head:offset])
    if cls:
        return f"{tag}." + ".".join(cls.group(1).split())
    return tag


def parse(src):
    """Return (declarations, skipped at-rules) for the whole file."""
    decls, skipped = [], []
    for m in STYLE_BLOCK.finditer(src):
        _scan_block(m.group(1), m.start(1), src, decls, skipped)
    for m in STYLE_ATTR.finditer(src):
        sel = _attr_selector(src, m.start())
        _collect(m.group(1), 0, len(m.group(1)), sel, m.start(1), src, "attr", decls)
    return decls, skipped


def render(binary, html, out_png, width, height, scale):
    """Render with NO mode flag: auto-detection applies identically to the
    baseline and every mutant, since removing a declaration cannot change the
    file's first tag."""
    p = subprocess.run(
        [binary, html, out_png, str(width), str(height), str(scale)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if p.returncode != 0:
        raise SystemExit(
            f"render failed rc={p.returncode} for {html}: {p.stderr.strip()[:400]}"
        )


def compare(base_png, other_png):
    """Return (changed_pixels, max_channel_delta) or None if geometry moved."""
    wa, ha, ca, pa = read_png(base_png)
    wb, hb, cb, pb = read_png(other_png)
    if (wa, ha, ca) != (wb, hb, cb):
        return None
    if pa == pb:
        return 0, 0
    try:
        import numpy as np

        a = np.frombuffer(bytes(pa), dtype=np.uint8).reshape(-1, ca).astype(np.int16)
        b = np.frombuffer(bytes(pb), dtype=np.uint8).reshape(-1, cb).astype(np.int16)
        d = np.abs(a - b)
        return int((d.max(axis=1) > 0).sum()), int(d.max())
    except ImportError:
        pass
    changed, maxd = 0, 0
    for i in range(0, len(pa), ca):
        x, y = pa[i : i + ca], pb[i : i + ca]
        if x != y:
            changed += 1
            maxd = max(maxd, max(abs(x[k] - y[k]) for k in range(ca)))
    return changed, maxd


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", help="file to audit")
    ap.add_argument("--bin", required=True, help="path to the renderer binary")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=0, help="audit at most N declarations")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--fail-on-inert", action="store_true", help="exit 1 if any is inert")
    ap.add_argument("--keep", help="keep mutant files in this directory")
    args = ap.parse_args()

    binary = os.path.abspath(args.bin)
    if not os.access(binary, os.X_OK):
        raise SystemExit(f"{binary}: not executable")
    src = open(args.html, encoding="utf-8").read()
    decls, skipped = parse(src)
    if args.limit:
        decls = decls[: args.limit]
    if not decls:
        raise SystemExit(f"{args.html}: no declarations found")

    work = args.keep or tempfile.mkdtemp(prefix="css_audit_")
    os.makedirs(work, exist_ok=True)
    baseline = os.path.join(work, "baseline.png")
    render(binary, args.html, baseline, args.width, args.height, args.scale)

    rows = []
    for n, d in enumerate(decls, 1):
        mutant = os.path.join(work, f"m{n:04d}.html")
        out = os.path.join(work, f"m{n:04d}.png")
        with open(mutant, "w", encoding="utf-8") as fh:
            fh.write(src[: d.start] + src[d.end :])
        render(binary, mutant, out, args.width, args.height, args.scale)
        got = compare(baseline, out)
        row = d.as_dict()
        if got is None:
            # Geometry moved: the declaration changes layout, so it is
            # load-bearing by definition, but the pixel count is meaningless.
            row.update(changed=None, max_delta=None, verdict="changes layout")
        else:
            changed, maxd = got
            row.update(
                changed=changed,
                max_delta=maxd,
                verdict="no pixel effect" if changed == 0 else "load-bearing",
            )
        rows.append(row)
        if not args.keep:
            for p in (mutant, out):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if not args.json and n % 10 == 0:
            print(f"  … {n}/{len(decls)}", file=sys.stderr)

    inert = [r for r in rows if r["verdict"] == "no pixel effect"]
    if args.json:
        print(
            json.dumps(
                {
                    "file": args.html,
                    "viewport": [args.width, args.height, args.scale],
                    "declarations": len(rows),
                    "inert": len(inert),
                    "skipped_at_rules": skipped,
                    "rows": rows,
                },
                indent=2,
            )
        )
    else:
        print(
            f"{args.html}  {args.width}x{args.height} scale {args.scale}  "
            f"({len(rows)} declarations, {len(skipped)} at-rules skipped)"
        )
        print()
        print(f"{'line':>5}  {'selector':<30}  {'property':<24}  verdict")
        print(f"{'-'*5}  {'-'*30}  {'-'*24}  {'-'*26}")
        for r in rows:
            if r["verdict"] == "no pixel effect":
                verdict = "NO PIXEL EFFECT"
            elif r["changed"] is None:
                verdict = "changes layout"
            else:
                verdict = f"{r['changed']:,} px (max {r['max_delta']})"
            sel = r["selector"] if len(r["selector"]) <= 30 else r["selector"][:27] + "..."
            print(f"{r['line']:>5}  {sel:<30}  {r['property']:<24}  {verdict}")
        print()
        print(f"inert {len(inert)} / {len(rows)} declared")
        if skipped:
            print("skipped at-rules: " + ", ".join(sorted(set(skipped))))
        if inert:
            print()
            print("No pixel effect: either the renderer dropped the declaration, or")
            print("something else already sets that value. Re-render at the size you")
            print("ship before deleting one — inert here is not inert everywhere.")

    if args.fail_on_inert and inert:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
