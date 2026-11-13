# html-render

![html-render: HTML + CSS to PNG with no browser](assets/hero.png)

**HTML + CSS → PNG, with no browser.**

**Linux x86_64, glibc ≥ 2.35.** No macOS, no Windows. A container image carries
the 13 shared libraries and the font for you — [skip the host
entirely](#or-skip-the-host-entirely).

```bash
# the image above is this command's output, byte for byte: 3200×1800
html-render assets/hero.html hero.png 1600 900 2 --document
```

A small Rust CLI that renders real documents — stylesheets, flexbox, grid, images,
web fonts — using [Blitz](https://github.com/DioxusLabs/blitz)'s Stylo + Taffy +
Vello pipeline. No Chromium, no JavaScript engine, one process.

## Status

`0.3.0-beta`, ten days old, one maintainer. No API promise: flags and output
names may change between minor versions. Linux only. One known layout defect —
four corpus boxes, consecutive `<br>` runs in a `font-size:0` inline-block
context (see [Corpus](#corpus)).

It exists to answer a concrete question: can a 25 MB binary replace a 500 MB
browser for server-side rendering? On a real 12-slide, 4 MB deck, yes
(what changed between releases: [CHANGELOG.md](CHANGELOG.md)):

| 12-slide deck, 2560×1440 | wall | peak RSS, largest process |
|---|---|---|
| headless Chromium (Playwright) | 13.36 s | 858 MiB |
| **html-render `--scroll`** | **5.37 s** | **93.4 MiB** |
| html-render `--clip` | 5.72 s | 275.2 MiB |

`--scroll` and `--clip` are the *clip* mode's two painting strategies; the
default mode is a single image (`html-render page.html out.png 1280 720`), which
is not in this table.

**2.34× faster and 3.12× lighter on the plain `--clip` recipe; 2.49× and 9.19×
with `--scroll`**, which paints one element-sized canvas per slide instead of one
full-height canvas that is cropped afterwards.

Those RSS figures are the largest single process, which is the wrong unit for one
of the two engines: Chromium is a tree of renderers and helpers. Measured on the
deck in this repo (`assets/demo-deck.html`, 2560×1440, scale 2), its tree peaks at
**1703.5–1826.2 MiB** while its largest process reads **297.1–299.2 MiB** — the
single-process reading undercounts it **5.7–6.1×**. `html-render` is one process,
so its two readings agree (81.2 vs 81.0 MiB). The memory ratios above therefore
*understate* the difference; the wall ratios do not depend on the method.
`python3 tools/measure_rss.py --rss-mode tree -- <command>` reproduces either.

Both encoders are lossless, and on the same document the two engines agree on all
but 3,551 of 44,236,800 pixels (0.0080 %, max channel delta 2/255) — antialiasing
at fractional offsets, not layout. Output is byte-for-byte deterministic,
which a browser is not: ten renders of the same input produce one checksum, so
output can be diffed in CI. That holds for a document whose resources are local
or `data:` URIs. A remote image is fetched at render time and painted as
**absent** if it misses `BLITZ_DEADLINE_SECS` (default 120 s), so a slow origin
changes the picture without changing the input.

The deck is not part of this repo (4.16 MB, embedded JPEGs, four `@font-face`
faces). Every figure here and in [GATES.md](GATES.md) is reproduced from your own
document with `python3 tools/gates.py --deck <path>` — a different deck gives
different numbers by construction.

[`assets/demo-deck.html`](assets/demo-deck.html) is a four-slide, 4 KB layout
fixture in the repo, so the two recipes are runnable from a fresh clone without
that deck:

```bash
# four slides out, one PNG per section.slide
html-render --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide' --document

# determinism: ten renders, one checksum
for i in $(seq 10); do html-render assets/demo-deck.html /tmp/r$i.png 1280 720 1 --document; done
sha256sum /tmp/r*.png | awk '{print $1}' | sort -u | wc -l   # → 1
```

## Use cases

Four things this is used for, each one a file you can name:

- **Slide images from HTML/CSS.** A deck authored as `<section class="slide">`
  becomes one PNG per slide at any scale, with no browser in the loop —
  `--clip` writes `clip_01.png`, `clip_02.png`, … in document order.
- **Open Graph and social cards.** [`assets/og.png`](assets/og.png) is
  2560×1280 — a 1280×640 card at scale 2 — rendered from
  [`assets/og.html`](assets/og.html) by
  `html-render assets/og.html og.png 1280 640 2 --document`.
- **CI visual diffs.** Deterministic output makes a changed pixel a changed
  document: commit the PNG, diff the PNG, fail the build.
- **Report and chart images.** One-pagers and dashboards where the layout is CSS
  and the alternative is a screenshot service you have to keep warm.

## Install

No package manager, no toolchain. A single 25 MB binary — but **not** a static
one: it links glibc and the font stack at runtime. From a
[release](https://github.com/leshchenko1979/html-render/releases):

```bash
V=v0.3.0-beta.4-r2
N=html-render_0.3.0-beta.4-r2_linux_amd64
BASE="https://github.com/leshchenko1979/html-render/releases/download/$V"

curl -fsSL -O "$BASE/$N.tar.gz"
curl -fsSL -O "$BASE/checksums.txt"
sha256sum -c checksums.txt
tar xzf "$N.tar.gz"
install -m755 html-render /usr/local/bin/html-render
```

Every release verifies its own asset: the workflow downloads the published
tarball back over HTTPS, checks `checksums.txt`, byte-compares it with the build,
renders a PNG and asserts its dimensions. A release serving the wrong file fails
its own run.

### What it needs on the host

`ldd` on the shipped binary lists **15** entries — **13** shared libraries plus
the loader and the vDSO — and a `scratch` or distroless image satisfies none of
them — the binary will not start:

| | |
|---|---|
| glibc | **2.35+** (`libc.so.6`, `libm.so.6`, `libgcc_s.so.1`) |
| TLS | `libssl.so.3`, `libcrypto.so.3` (Debian 12+ / Ubuntu 22.04+) |
| fonts | `libfontconfig.so.1`, `libfreetype.so.6`, `libexpat.so.1` |
| images | `libpng16.so.16`, `libbz2.so.1.0`, `libbrotlicommon.so.1`, `libbrotlidec.so.1` |
| misc | `libz.so.1`, `ld-linux-x86-64.so.2`, `linux-vdso.so.1` |

On Debian/Ubuntu the non-base half is one line:

```bash
apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbz2-1.0 libbrotli1
```

`libexpat.so.1`, `libz.so.1` and `libgcc_s.so.1` are absent from that line on
purpose: the six packages above depend on them, so apt pulls them in. `ldd` still
lists all thirteen, which is why the table and the command do not match line for
line — the table is the closure, the command is the shortest way to it.

### Fonts are a runtime dependency, and a silent one

The binary ships **no** font. `assets/hero.html` and `assets/og.html` name
`DejaVu Sans` explicitly, `assets/demo-deck.html` asks for plain `sans-serif`
(which fontconfig resolves to DejaVu on most hosts), and the deck this was
measured on names DejaVu too. If the host has it, nothing needs installing.
Check before you trust a PNG:

```bash
fc-match "DejaVu Sans"    # → DejaVuSans.ttf, or a substitute you did not ask for
```

The failure mode is the reason this is a section rather than a footnote:
fontconfig always resolves *something*, so a host without DejaVu renders every
line in a fallback face, re-wraps the text to different line breaks, and produces
a perfectly plausible PNG of the wrong thing. No warning, no non-zero exit. On a
minimal container:

```bash
apt-get install -y fonts-dejavu-core
```

### Or skip the host entirely

Everything above — the shared libraries, the glibc floor, the font — is
discharged once by an image:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD:/work" \
    ghcr.io/leshchenko1979/html-render:latest \
    /work/card.html /work/card.png 1280 720 2
```

`--user` is required, not optional. The image runs as uid 10001 so it cannot
touch anything outside the file it was told to write, which also means it cannot
write into a bind mount owned by you: without `--user` the render exits 101 with
`create png: Permission denied`. Passing your own uid:gid makes the PNG belong to
you, which is what mounting a directory implies.

`latest` is a **moving** tag: it follows the default branch and every release, so
the same name can mean a different binary next week. Pin a digest or a `v*` tag
for anything reproducible:

```bash
docker pull ghcr.io/leshchenko1979/html-render@sha256:<digest>
```

Rendering HTML you do not trust needs the network taken away, because remote
sub-resources are fetched with no allowlist (see [Security](#security)):

```bash
docker run --rm --user "$(id -u):$(id -g)" \
    --cap-drop ALL --security-opt no-new-privileges \
    --network none --read-only --tmpfs /tmp \
    -v "$PWD:/work" \
    ghcr.io/leshchenko1979/html-render:latest \
    /work/untrusted.html /work/out.png 1280 720 2
```

The image is built from the same commit as the release tarball, on the same tag
push, and its own CI proves it renders: `ldd` resolves the full closure with
none `not found`, `fc-match "DejaVu Sans"` returns DejaVu, and a shipped asset
renders inside it at the right dimensions with glyphs painted. The closure is
asserted as *no library missing* plus a floor on the count, not as an exact
number: the same source links 12 libraries on bookworm and 13 on jammy, because
those freetype builds differ, and a gate pinned to one distro's transitive graph
reports a defect every time the base image is rebuilt. The gate therefore asserts
the closure **of the image it just built**, not a number copied from a release
log.

## Usage

Two modes. Both take the **viewport in CSS pixels**; `scale` multiplies the
output resolution (scale 2 → a 1280×720 viewport produces 2560×1440).

```bash
# one image
html-render page.html out.png 1280 720 [scale] [--document] [--fragment]

# one PNG per element matching a selector
html-render --clip deck.html png/ 1280 720 2 'section.slide' [--document] [--fragment] [--scroll]
```

`--help` prints those two lines plus every flag and exit code and exits `0`;
`--version` prints the version and exits `0`. Both used to exit `2` with
`unknown flag`, which made `docker run image --help` and every `cmd --help | head`
pipeline read as a broken binary. The output directory is created if it does not
exist, in both modes — a missing `out/` used to panic `101` in single-image mode
while clip mode created it, so the same argument shape gave two answers.

**A file that declares its own document root is rendered as one, automatically.**
Detection reads the first tag after any BOM, whitespace and comments, and matches
it against `<!doctype>`, `<html>`, `<head>`, `<body>`; anything else — a `<div>`,
a `<style>`, bare text — is a fragment and is wrapped in a synthetic
`<html><body>` with a white-background reset.

The two flags override the guess: `--document` forces the document reading,
`--fragment` forces the wrapped one, and together they exit `2` rather than
silently preferring one. The resolved mode is on the `TIMING` line.

This matters more than it looks. Wrapping a real document hands the canvas to the
wrapper's `html{background:#ffffff}` reset, so a page that colours itself via
`body{background:…}` — the common idiom — renders **white**, with identical
geometry and nothing on stderr. Measured against the previous release, a
`body{background:…}` document differs from its `--document` render in **70.4 %**
of pixels, and a file that opens at `<body …>` in **89.9 %** — which is why the
rule matches all four opening tags, not just `<!doctype>`. Passing `--document`
was already the documented advice; the guess means forgetting it is no longer
silent.

`--scroll` (clip mode) paints one element-sized canvas per match instead of one
full-height canvas that is cropped afterwards — the same picture for a third of
the peak memory. Not byte-identical: on the 4-slide demo deck the two strategies
differ on 0 / 75 / 58 / 125 pixels of 3.69 M per slide, never by more than one
channel level, because a scrolled canvas rounds its origin differently.

```bash
# a 12-slide deck, 2560×1440 per slide, ~93 MiB peak
html-render --clip deck.html slides/ 1280 720 2 'section.slide' --document --scroll
```

Clip mode writes `clip_NN.png` into the output directory (one per match, in
document order) and prints each match's CSS box and physical size:

```
clip_01 css x=0.00 y=0.00 w=1280.00 h=720.00  physical 2560x1440 (scroll)
```

A matched element with no layout box at all (`br`, `wbr`, `script`, `style`, …)
is skipped, not fatal, and the skip is reported on stderr:

```
skipped 3 of 306 matched element(s): no layout box
```

That makes `'*'` usable on a real document. The skip is narrow on purpose:
`hr` **is** emitted, because it is a block box with a default border (measured
`w=400 h=2`), and so is anything else that has geometry — a skip that dropped
whatever it could not handle would pass a casual test and lose real boxes.
`clip_NN` numbering stays contiguous over the emitted boxes. A selector whose
matches are *all* boxless exits `3`, the same code as a selector that matched
nothing.

### Exit codes

Four codes, and every one is asserted by `tools/test_exit_codes.py`, which runs
in CI against the binary built from the same commit:

| Code | Means |
|---|---|
| `0` | the PNG, or the whole clip directory, was written; or `--help` / `--version` printed and exited |
| `2` | wrong argument count, an unknown flag, or `--clip` not first |
| `3` | the selector matched nothing, or every match had no layout box |
| `101` | a panic: an unparsable number, an invalid selector, an input file that cannot be read |

`101` is a panic, not a diagnosis: the message names a line in `src/main.rs`
rather than the argument you got wrong, so read the argument before the file.

**This table describes `main`, and the published `v0.3.0-beta.4-r2` predates
it.** Nine of the fifteen legs `tools/test_exit_codes.py` now asserts fail
against that binary: `--help`, `-h`, `--version` and `-V` exit `2` with
`unknown flag`; an unknown flag exits `0`; `--clip` in the wrong position exits
`0`; a selector whose matches are all boxless exits `101` instead of `3`; the
usage line still says `usage: render`; and single-image mode panics `101` when the
output directory does not exist. Everything else holds. A release cut from `main`
is what closes that gap; until then the tarball and this page disagree.

**The failure with no exit code is the one to watch.** Unsupported CSS is
ignored, not rejected: an unimplemented property or value falls back to its
initial value, the layout settles around it, and a plausible PNG comes out. No
warning, no non-zero exit. A clean run proves the document rendered, not that it
rendered as designed — check the properties you depend on against a browser
once, before trusting a deck to this.

## Fidelity

Measured against Chromium 151 on the same document, same viewport, same scale —
both PNGs read by one instrument, so a difference in the table is a difference
between two engines rather than between two rulers.

| Deck, scale 2, per slide vs `element.screenshot()` | |
|---|---|
| size | 12/12 at 2560×1440 |
| pixels off by >16 | **4.13 %** (per slide 0.88 – 11.67 %) |
| photo regions | 0.31 – 3.92 % |
| best integer shift on every photo region | **0 px** |

The residual is antialiasing, not layout: wrap points match, boxes sit at
Chromium's coordinates, and images are sampled at zero offset. Vello's
glyph AA is slightly softer than Skia's, so **you cannot diff html-render
against Chromium at 99 %** — and you don't need to, because against *itself* it
is exact.

What the corpus does **not** cover is worth naming before you rely on the table
above: **0 of 31 fixtures contain a table**, so `display: table` layout is
untested here, and no fixture exercises non-Latin text shaping or right-to-left
scripts. Both are Blitz surfaces rather than this CLI's, and both can move a
layout without any error. If your document is table-heavy or CJK, render one page
and look at it.

Determinism is a first-class property, not a side effect. Both rows are a
document with local resources only — the scope above:

| | |
|---|---|
| 10 renders, scale 2, one checksum | `1dffe1ae…` |
| two separate builds of one commit | different binary md5s, **identical pixels** (12/12 slides) |

[`tools/deck_check.py`](tools/deck_check.py) is the loop behind those numbers: it
renders one document with both engines at the same viewport, times each over
`--repeats` runs, and compares the two PNGs — ink coverage, mean channel
difference and the share of near-identical pixels.

```bash
python3 tools/deck_check.py --html your-deck.html --chrome "$(which google-chrome)"
```

It needs Chromium (`--chrome`, auto-detected if omitted) and a built binary
(`--blitz`, default `target/release/render`), so it is a local instrument rather
than a CI gate; the corpus gate that does run in CI is
[`tools/run_parity.py`](tools/run_parity.py), which compares per-element boxes
across 31 fixtures instead of whole pages.

### Which declarations did it drop?

[`tools/css_audit.py`](tools/css_audit.py) answers that by measurement, not from
a list of "supported properties": such a list rots the moment Blitz updates, and
a stale list reports a dropped declaration as fine. It renders the file once,
then re-renders once per declaration with exactly that declaration removed, and
diffs the PNGs.

```bash
python3 tools/css_audit.py --bin target/release/render your-card.html \
    --width 794 --height 1123 --scale 3.123
```

A declaration whose removal changes no pixel is **inert**: either the renderer
dropped it, or something else already sets that value. The report says "no pixel
effect" and never "unsupported", because a pixel diff cannot tell those two
apart — a `color` whose text all lives inside a child that overrides it is
honoured and still invisible.

Cost is one render plus one PNG decode per declaration, and both halves are
measured: a render is **0.23-0.28 s**, and decoding a 2560x1440 PNG is
**0.13-0.21 s** with Pillow or **3.0-3.7 s** through the stdlib reader
(`tools/measure.py`, which has no third-party imports). A declaration whose
removal produces a byte-identical PNG is never decoded, so inert ones are nearly
free: 48 of the 76 declarations in `assets/demo-deck.html` skip that step.

The whole audit, 76 declarations at 1280x720 scale 2 across 4 parallel renders
(`--jobs`, one per core by default):

| Decoder | Wall clock |
|---|---|
| Pillow | **11.4 s** |
| stdlib only | **100.5 s** |

Same verdicts either way, so Pillow is a speed knob, not a correctness one.
Install it before auditing anything large: an A4 card at scale 3.123 is 8.7
megapixels, where the stdlib reader costs about 7 s per changed declaration.
Audit at the size you ship, too — a declaration inert at 1280x720 can be
load-bearing at 794x1123.

## What it does

Cascade and selectors, flexbox, grid, inline formatting and line boxes,
`@font-face` (including data URIs), images over `http(s)://`, `file://` and
`data:`, backgrounds, gradients, borders, transforms, absolute and relative
positioning, CSS variables, `box-sizing`, `min`/`max` sizing, auto margins,
`aspect-ratio`, `overflow: clip`.

## What it doesn't

- **JavaScript.** No script engine is compiled in. That is the point: no
  script-execution surface, and no 200 MB of V8.
- **PDF.** PNG only. For paged output see [Alternatives](#alternatives).
- **`@page` and print layout.** No page boxes, no `@page` margins, no page
  counters. A document authored for print renders as one continuous page at the
  viewport you asked for. This is not the same gap as "exotic selectors": if you
  need pagination, no selector will get you there.
- **The long tail of CSS.** It is Blitz's CSS support, not Chromium's, so expect
  gaps in newer properties, colour spaces and selector forms. Unsupported CSS is
  **ignored, not rejected** — a gap shows up as a property that had no effect,
  never as an error. See [Exit codes](#exit-codes).

## Alternatives

Use one of these instead when the constraint is theirs, not ours:

- **`wkhtmltoimage`** — when the deliverable is **PDF** as well as images and you
  already ship a Qt stack. This renders PNG only. Conversely, pick this over
  `wkhtmltoimage` when a 25 MB binary with no Qt is the requirement.
- **WeasyPrint** — when the input is HTML *for print*: real `@page`, page
  counters, running headers, page breaks. It is a paged-media engine; this is a
  viewport rasteriser.
- **`resvg`** — when the input is already SVG. Blitz rasterises HTML and CSS, so
  SVG here means an `<img>` wrapper and a quality loss you do not need.
- **Chrome print-to-PDF, or Playwright** — when the page needs JavaScript, or
  when you must match a browser pixel for pixel. That is the 500 MB and the
  13.36 s this exists to avoid, and it is the right call when fidelity to Chrome
  *is* the requirement rather than an implementation detail.

## Security

No JavaScript engine is compiled in, so a document cannot execute code. **That is
not the same thing as a sandbox:** `http(s)://` sub-resources are fetched at
render time with no allowlist, and `file://` is readable, so rendering untrusted
HTML gives that HTML the network reach of the machine you render on. Details,
the measured egress proof, and how to report: [SECURITY.md](SECURITY.md).

## Corpus

`fixtures/` holds 31 documents that each paint elements in exact probe colours, so
geometry can be read out of a PNG without the instrument knowing anything about
the markup. `tools/run_parity.py` renders every fixture through Blitz and through
Chromium and reports per-box deltas:

```
VERDICT  text-free (47 boxes): 42 exact, 0 within 1px, 4 divergent, 0 missing, 1 absence
```

The 4 divergent boxes are one open defect: consecutive `<br>` runs in a
`font-size:0` inline-block context, where the deltas grow 16 px per extra break.
Six further boxes involve text and are reported separately, because without an
identical embedded font they measure font availability rather than layout.

**`absence` is a pass, not a gap.** One fixture (`margin-auto-stretch`) asserts
that *neither* engine paints a probe, because auto cross margins cancel the
stretch: there is no box to compare, and the absence itself is the assertion. So
`1 absence` counts one fixture that behaved correctly, and `0 missing` counts
boxes Chromium painted and Blitz did not.

**This is a gate, not a report.** `tools/parity_baseline.json` pins the four
known divergences by `fixture:colour` with their measured delta, and
`run_parity.py` exits `1` on a new divergence, on one that grew, on a missing
box, on a fixture that stopped running — and on a baseline entry that stopped
diverging, so a fix cannot leave a stale tolerance behind that later hides the
same box regressing. `python3 tools/test_parity_gate.py` proves each of those
legs fails, without needing a browser.

## Building

The build is a GitHub Actions job, because the host this was written on forbids
local Rust compilation. Five workflows:

| Workflow | Trigger | Output |
|---|---|---|
| `parity.yml` | push touching `src/`, `Cargo.*`, `fixtures/`, `tools/`, `assets/` or the workflow itself; or manual | builds, runs the clip, exit-code, document-root, CSS-audit and corpus-gate tests, **measures the corpus and fails on a new divergence**, uploads results |
| `drain-probe.yml` | manual dispatch only | builds, measures the image drain loop under each `BLITZ_DRAIN` mode, uploads the report |
| `release.yml` | push a `v*` tag, or manual | builds, packages, publishes, **then verifies the published asset** |
| `container.yml` | push a `v*` tag, or manual | builds the image, asserts the library closure is complete (`ldd` reports nothing `not found`, plus a floor on the count — a floor, not an exact number: the same source links 12 libraries on bookworm and 13 on jammy) and that the font resolves, renders an asset inside it, pushes to GHCR |
| `lockfile.yml` | push touching `Cargo.toml`, or manual | resolves `Cargo.lock` and commits it back if it moved |

Two hard requirements, both learned the hard way:

- **`runs-on: ubuntu-22.04`, never `ubuntu-latest`.** The `latest` image produced
  a `GLIBC_2.43` binary that would not start on a glibc 2.39 host. The runner is
  pinned, and a guard fails the build if any `GLIBC_*` symbol exceeds 2.35 —
  otherwise a future image bump silently raises the floor again.
- **`libfontconfig1-dev`** is needed to build Stylo.

Locally, where Rust is allowed:

```bash
cargo build --release
python3 tools/run_parity.py --blitz target/release/render
```

## Licence

Apache-2.0. Same licence as Blitz itself, and it carries an explicit patent grant.
