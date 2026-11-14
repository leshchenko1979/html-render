# Gates

The three gates that decide whether `html-render` can stand in for a browser
for static slide rendering. All numbers below were measured on one 4 vCPU Linux
host (glibc 2.39) with Blitz `0.3.0-beta.4` and Chromium 151, and all of them
are reproducible with `tools/gates.py` in this repo.

The document under test is not part of this repo: a 12-slide, 4.16 MB deck
(embedded JPEGs, four `@font-face` faces, 78 CSS rules). Pass your own with
`--deck`.

```
python3 tools/gates.py --binary ./render --deck deck.html --chromium $(which google-chrome)
```

## A · determinism

Ten renders of the same input at `--scale 2` must be byte-identical.

| | |
|---|---|
| result | **PASS** |
| md5 of all ten | `1dffe1ae…` (one distinct hash) |
| median wall | 2.51 s |
| peak RSS | 86.7 MiB (single process: tree sum = process max) |

Determinism is the gate that makes the rest useful: without it, output cannot be
diffed in CI, and a pixel comparison is noise.

## B · device scale

`--scale 2` must produce exactly twice the physical output of `--scale 1`, and
agree with Chromium at both scales.

| scale | output | mean channel delta | pixels off by >16 |
|---|---|---|---|
| 1 | 1280×720 | 5.31 | 3.15 % |
| 2 | 2560×1440 | 5.53 | 2.66 % |

**PASS**: the 2× invariant holds exactly and fidelity does not degrade with
scale.

## C · element clip

Every `section.slide` clipped to its own PNG must match the dimensions Chromium
produces for the same element.

| | |
|---|---|
| result | **PASS** |
| slides | 12/12 at 2560×1440 |
| whole deck | 4.13 % of pixels off by >16 |
| per slide | 0.88–11.67 % |

Two structural checks, because a gate that passes on a wrong mechanism is worse
than no gate:

- `--clip` output is **byte-identical to cropping the `--document` render**
  (0 differing pixels, 12/12), so the clip branch cannot hide a defect the
  full-document measurement missed.
- clip mode is deterministic too: 3 runs, one md5.

## What the residual is

The 4.13 % is antialiasing and image resampling, not layout:

- best integer shift on every photo region is **0 px**, so nothing is displaced;
- 8× downsampling collapses the difference (mean 20.4 → 5.73), which is the
  signature of edge noise rather than misplaced geometry;
- photos are pixel-exact in the region mask at scale 1 (0.00 % off by >16);
  at scale 2 they read 0.3-3.9 % off, which is the 2x upscale filter rather
  than a displacement, since the shift stays 0 px.

So `html-render` will never match Chromium at 99 % of pixels, and that is not
the useful target: determinism means it can be diffed against **itself**
byte-exactly, which is what a CI gate needs.

## Cost

Same host, same job. **All three figures below are per-process maxima**, taken
with `/usr/bin/time -f %M`, which reports the largest single process in the tree
rather than what the host has to hold. The deck they were measured on — 12
slides, 4 MB — is not in this repo, so these rows are history, not a gate:

| engine | wall | peak RSS (largest process) |
|---|---|---|
| Chromium 151, Playwright, 12 element screenshots at dsf=2 (includes 2.94 s of settle sleeps) | 13.36 s | 858.4 MiB |
| `--scroll` | **5.37 s** | **93.4 MiB** |
| `--clip` (one full-height canvas, cropped) | 5.72 s | 275.2 MiB |

That method is not comparable between the two engines, and the size of the error
is measured: on `assets/demo-deck.html` at 2560×1440, scale 2, Chromium's process
tree peaks at **1703.5–1826.2 MiB** across four runs while its largest single
process reads **297.1–299.2 MiB** — a **5.7–6.1×** undercount. `html-render` is one
process, so the two methods agree for it (tree 81.2 MiB, max 81.0 MiB). The
memory ratios above are ratios of the wrong quantity and understate the tree
difference; the wall times are unaffected. Re-measuring the external deck is one
command once it is at hand:

    python3 tools/measure_rss.py --rss-mode tree -- <engine command>

`--scroll` paints one element-sized canvas per region instead of one
full-height canvas, which is where the 3× memory difference comes from — that
comparison is `html-render` against itself, so both sides are single-process and
the per-process method is the right one there. It is
lossless: `--scroll` and `--clip` differ on 3,551 of 44,236,800 pixels
(0.0080 %), max channel delta 2/255, which is antialiasing at fractional
offsets.

## Traps

Each of these cost real time, and none of them raises an error.

1. **The drain loop can exit before the image is ingested.** Upstream Blitz's
   `examples/screenshot.rs` breaks on `net.is_empty()`, which reports the
   *provider's* waker count — but the loaded asset arrives on the *document's*
   message channel, and `resolve()` is the only thing that drains it. So the loop
   can break with the image fetched and still queued: measured **0/8** images on
   both runtime flavours. One trailing `resolve(0.0)` after the loop gives
   **8/8** (that is why upstream's `examples/paint_bench.rs` survives — it has one
   and `screenshot.rs` does not; neither file is vendored here, the line numbers
   are `screenshot.rs:98` and `paint_bench.rs:111` upstream). `yield_now()` is
   *not* the fix: it rescues the current-thread case only and reads 0/8 on
   multi-thread. `is_empty()` itself is not racy: `fetch()` clones the waker
   before `spawn()`. Upstream #1419. Reproduce both shapes on one binary with
   `BLITZ_DRAIN=naive|naive2|yield|quiet` (see `src/main.rs`), which is what
   `tools/drain_probe.py` sweeps.
2. **`--clip` mixes two coordinate systems.** `get_client_bounding_rect()` and
   painter node coordinates are **CSS** px; the canvas and the PNG are
   **physical** px. Feed CSS px to the painter and the offsets are right but the
   output is half resolution; feed physical px and everything is culled to a
   blank slide. Neither case errors.
3. **Never raise the viewport to document height.** A real deck resolves `vh`
   units and `height: 100%` against the viewport, so a taller viewport relayouts
   the page and the comparison becomes meaningless. Paint one tall canvas and
   crop, or use `--scroll`.
4. **Know the document height.** A 12-slide deck with 24 px gaps is 8904 CSS px,
   not 8640. Guessing short makes the last slide read as 37.6 % wrong when the
   real figure is 1.41 %: the crop, not the renderer.
5. **`--help` used to exit 2.** Usage went to stderr, so `set -euo pipefail` failed on a
   successful invocation. Use `|| true` in scripts.

## Withdrawn claims

**"Blitz makes N line boxes where Chromium makes N−1."** Filed as
[DioxusLabs/blitz#1391](https://github.com/DioxusLabs/blitz/pull/1391) and
withdrawn. With the fix reverted, unpatched Blitz renders `x<br>`, `x<br><br>`
and `x<br><br><br>` as 1, 2 and 3 lines: already correct. The real signature in
the `br-run-blocks` fixture is deltas of 6/22/38/54 px, growing by 16 per extra
`<br>`; a wrong line *count* gives a constant offset and cannot grow. The cause
is a line-box *height* computation in a `font-size: 0` + inline-block context,
still unidentified and unfiled.

**"`Provider::is_empty()` races the first poll."** Also wrong: see trap 1.

## Corpus

`tools/run_parity.py` renders every fixture in `fixtures/` through Blitz and
through Chromium and compares per-box geometry, each fixture painting its probes
in exact colours so the PNG can be read without the instrument knowing anything
about the markup. The current verdict, and the detail that does not belong on a
landing page, live here.

```
text-free (54 rows): 42 exact, 0 within 1px, 11 divergent, 0 missing, 1 absence
```

All 11 divergent boxes are two open defects: four from consecutive `<br>` runs
inside a `font-size:0` context
([#15](https://github.com/leshchenko1979/html-render/issues/15)), seven from
table layout, up to 18 px out
([#9](https://github.com/leshchenko1979/html-render/issues/9)). The remaining 42
rows land where Chromium puts them.

Six further boxes involve text and are reported separately, because without an
identical embedded font they measure font availability rather than layout.

**This is a gate, not a report.** `tools/parity_baseline.json` pins every
divergent box by `fixture:colour` with its measured delta, and `run_parity.py`
exits `1` on a new divergence, on one that grew, on a missing box, on a corpus
that lost rows, on a fixture that stopped running, on a baseline that predates a
field it now checks — and on a baseline entry that stopped diverging, so a fix
cannot leave a stale tolerance behind that later hides the same box regressing.
`python3 tools/test_parity_gate.py` proves each of those legs fails, without
needing a browser.

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

`display: table` used to be the biggest hole here, and it is now measured rather
than absent. Fixture 32 (`table-collapse`) puts `border-collapse`, `colgroup`,
`rowspan`, `colspan` and `border-spacing` in front of both engines, and **all
seven of its boxes diverge**: the table comes out **18px shorter** than Chromium
paints it, one cell is **12px tall where Chromium paints 28**, and the other six are
off by 2-16px. Those seven are pinned in `tools/parity_baseline.json` with that
reason written down, so a *further* regression still fails the build while the
current defect stays visible instead of being forgotten.

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
across 32 fixtures instead of whole pages.

## CSS audit

**Which declarations did the renderer drop?**
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

## Published artefacts

`tools/test_exit_codes.py` runs the exit-code contract against a binary and
prints what it found. These are those runs against each published artefact, not
a hand-written summary:

| Published asset | Contract | Because |
|---|---|---|
| `v0.3.0-beta.6` | **all 21 legs hold** | cut from the commit the README documents |
| `v0.3.0-beta.5` | 8 failed assertions | `--help` only at `argv[1]`; `-V` panics (`rc=101`); no code `4`; `--scroll` silently ignored; stale frames kept; `--full` unknown |
| `v0.3.0-beta.4-r2` | 22 failed assertions | all of the above plus `--help` exits `2`, `--version` exits `2`, and a single image into a missing directory panics |

A release older than the page is not a scandal — the page moves and artefacts
don't. What would be a scandal is the page claiming otherwise, which is why the
published-artefact step in CI is a **hard gate**: when `Cargo.toml` and the
release tag name the same version, a failing leg fails the build. A tag that
predates `main` only warns, so red still means something.
