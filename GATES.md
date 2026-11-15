# Gates

The three gates that decide whether `html-render` can stand in for a browser
for static slide rendering, plus the receipts behind every number this repo
advertises.

**Every advertised figure comes from `bench/results.json`** — a receipt written
by `tools/bench.py` on a GitHub runner and committed unedited. `tools/check_claims.py`
fails the build if a sentence in this file, the README or the release notes
quotes a number the receipt does not contain, so prose and measurement cannot
drift apart.

The benchmark document is **generated, not shipped**: `tools/bench_deck.py`
builds an eight-slide deck from a fixed seed, so the same command renders the
same bytes anywhere.

```bash
git clone https://github.com/leshchenko1979/html-render && cd html-render
python3 tools/bench.py --bin <render> --chrome <chrome> --out /tmp/mine.json
```

Compare `/tmp/mine.json` to `bench/results.json`; if `deck.html_md5` matches
`5c71602d1dd6c259ea3e3ba316da5233`, both describe the same document.

Sections A–C below are the older `tools/gates.py` runs on a private deck. They
established the invariants (determinism, the 2× scale law, clip-equals-crop) and
those invariants are still gated; their absolute figures are history.

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
| whole deck | 4.5462 % of pixels off by >16, over 29,491,200 pixels |

Two structural checks, because a gate that passes on a wrong mechanism is worse
than no gate:

- `--clip` output matches **cropping the `--document` render** at every frame's
  own rectangle, to within one level per channel, so the clip branch cannot
  hide a defect the full-document measurement missed.
  It used to be byte-identical, and this document said so; row banding ended
  that. Measured on a 12-slide deck spanning 11 bands: 1 of 12 frames identical,
  11 differing by max channel delta **1**, and the count grows with band index
  (0 → 28 px) because the shift is the antialiasing phase of a non-zero
  `set_viewport_scroll`, not a layout difference. Asserted, not assumed:
  `tools/test_bands.py` leg 4 compares every clip frame against its crop of the
  `--full` render and fails above delta 1.
- clip mode is deterministic too: 3 runs, one md5.
- the shared canvas is **as wide as the widest match**, not as wide as the
  viewport (#17). An element wider than the viewport is laid out and painted past
  it, so a canvas at the viewport's width cut the frame — measured before the fix:
  a 1600 px element in an 800 px viewport wrote an 800×100 PNG at `rc=0` with half
  its content missing. Widening is a paint-surface change, not a layout one:
  `paint_scene` builds its root clip from the surface size it is given, while the
  document's viewport — and therefore every `vh` unit — is untouched. The row
  budget is derived from the widened width, so peak memory is one band at the
  widest frame's width. `tools/test_clip_wide.py` pins the width and, separately,
  that the overflow is painted rather than left white.

## What the residual is

The residual is antialiasing and image resampling, not layout:

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

From `bench/results.json` (schema 1), measured on the runner that produced it —
4 vCPU, glibc 2.35, Chromium driven headless at the same device scale factor.
Three repeats per engine, wall clock as the median, and peak RSS as the **sum of
every process in the engine's tree**, which is what a container limit counts:

| engine | wall | peak tree RSS |
|---|---|---|
| Chromium, one tall screenshot cut into eight slides | 3.774 s | 2452.6 MiB |
| `--full` | **2.352 s** | **37.7 MiB** |
| `--clip` (banded, one PNG per frame) | 2.386 s | 75.3 MiB |
| `--clip --per-element` | 2.349 s | 47.1 MiB |

Ratios against Chromium: **1.60×** wall and **65.06×** memory for `--full`,
**1.58×** / **32.57×** for `--clip`, **1.61×** / **52.07×** for `--per-element`.

Two things this table does not claim.

**Speed is a modest win, and the earlier 2.49× was not.** That figure came from a
private deck where the Chromium harness also paid 2.94 s of settle sleeps;
removing them left 1.94×. The probe drives Chromium as cheaply as it can be
driven — one tall screenshot, no sleeps — so 1.6× is the honest race.

**The memory ratio is larger than what was published before, for a method
reason, not a code change.** Earlier rows compared html-render's footprint with
Chromium's *largest single process* (858 MiB). Summing the tree gives 2452.6 MiB
on the same engine, because the browser, GPU and renderer processes all hold
memory at once. The undercount was measured before the probe existed: on
`assets/demo-deck.html` Chromium's tree peaked at 1703.5–1826.2 MiB while its
largest process read 297.1–299.2 MiB.

`--clip` costs more than `--per-element` because it keeps one full-height canvas
per band alive; both are `html-render` against itself, so neither is a
cross-engine claim. They differ on 3,551 of 44,236,800 pixels (0.0080 %), max
channel delta 2/255 — antialiasing at fractional offsets.

### What is actually gated

The rows above are history. What is a gate is `tools/test_memory.py`, which pins
absolute peak RSS for four shapes against `tools/memory_baseline.json`:

| shape | why it is in the set |
|---|---|
| `full_streamed` | `--full` on a 20,000 px document: peak is one band, not the canvas |
| `clip_oversized_frame` | one 20,000 px element: `plan_bands` gives it its own band, so banding is a no-op by construction and the band IS the frame |
| `clip_multiband` | a 20-slide deck, where banding does its job |
| `clip_multiband_unbanded` | the same deck at `BLITZ_BAND_MIB=0`: the control that proves the instrument still resolves the difference banding makes |

It fails three ways, the same three the corpus gate uses: a shape with no pin, a
shape that grew past its pin, and a shape that dropped far below its pin — a
baseline nobody updates rots in the silent direction, leaving a tolerance that no
longer describes the tree.

The pins are that file's numbers and are not repeated here: a second copy is a
copy that drifts. This section carries its own proof of that — its rows were
labelled history while `README.md` still advertised the same figures as current.

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
   the page and the comparison becomes meaningless. `--clip` paints the document
   in row bands and scrolls between them, so the tall canvas is never
   materialised; `--per-element` scrolls to each match instead.
4. **Know the document height.** A 12-slide deck with 24 px gaps is 8904 CSS px,
   not 8640. Guessing short makes the last slide read as 37.6 % wrong when the
   real figure is 1.41 %: the crop, not the renderer.
5. **`--help` used to exit 2.** Usage went to stderr, so `set -euo pipefail` failed on a
   successful invocation. Use `|| true` in scripts.
6. **A single canvas past 65,532 px tall or 65,408 px wide panics, and leaves a
   dump behind.** The renderer truncates the dimension through a `u16` and then indexes
   past the buffer it actually got: a 72,000 px document produced a 6,464-row
   buffer and died on `range end index 20688000 out of range for slice of length
   20684800`, `rc=101`, with 34 of 80 frames already written. Both ceilings are
   measured on a released binary, one pixel either side — 65,532 renders and
   65,533 panics, 65,408 renders and 65,410 panics — and neither is derivable
   from the other. `refuse_oversized_canvas` refuses with `rc=5` before painting,
   so nothing is written.

   **No mode is bounded by the total page any more.** `--clip` bands the
   document (`BLITZ_BAND_MIB`, default 4), `--per-element` gives each frame its
   own canvas, and `--full` bands and streams rows into the PNG encoder without
   ever materialising `width × height × 4`. So the wall applies to a single
   *canvas*: one band, one element, or a whole page when banding is switched off
   (`BLITZ_BAND_MIB=0`) or when a `position:fixed` element forces the
   single-canvas fallback. 45 slides at 1440p 1×, **22 at 2×, 15 at 3×** is what
   a single canvas still cannot hold; `--full` renders all of them.

   **Banding costs one level of antialiasing, and only where it bands.** The
   banded path scrolls the viewport (`set_viewport_scroll`) instead of offsetting
   the paint origin: on a 48,000-row deck 32 of 40 frames differed from the
   single canvas by **max channel delta 1**, two to ten pixels each, inside the
   glyphs. That is not clipping — a clipped frame loses whole regions, not
   single levels — and it is two orders below the gate's tolerance of 8.

   **It is rounding, not a seam**, and "moves the subpixel AA phase" — this
   file's earlier wording, and the `beta.7` changelog entry's — named the wrong
   mechanism. Measured on a glyph-dense document (40 lines of 64 px bold) at
   scales 1, 2 and 3:

   - a glyph cut exactly by a band boundary reassembles with **0 differing
     pixels**. That is the direct test of the seam question, and it passes;
   - **100 %** of differing pixels sit on rows that carry ink;
   - their proximity to boundaries is what the boundaries' own row share
     predicts: at scale 3 with a 1 MiB budget, **37 observed against 37.7
     expected** if the same pixels were spread uniformly;
   - banded renders agree with *each other* far better than with the single
     canvas — scale 2: **12-64 px** between two band budgets, **426-433 px**
     against the single canvas. The reference is the outlier, which locates the
     cause in painting at a non-zero offset rather than in band layout.

   So overlapping the bands would not reduce the residual: there is nothing at
   the seam to repair, and a fifth grid only re-rolls which glyph edges round.

   What protects the 54-row corpus verdict is not pixel identity
   but that **banding never runs on that path**: `run_parity.py` renders fixtures
   with no `--clip`, and `--clip` is the only mode that bands, so the verdict
   cannot see the change at all. The banded path's real check is Gate C, which
   does render the deck with `--clip` and requires every slide at Chromium's
   dimensions. `tools/test_bands.py` covers the rest: leg 1 is a **negative
   control** — the single-PNG path renders identically at 1 MiB and 0, which
   catches banding leaking into a mode that must not have it — and leg 2 asserts
   the geometry identity on a multi-band deck, while leg 5 pins the no-seam
   property above so this paragraph cannot rot back into the phase story. Three
   earlier claims were wrong: byte-identity on *every* frame was false,
   describing leg 1 as the leg that protects the corpus verdict was wrong too
   (it passes because banding does not run there), and the AA-phase mechanism
   was wrong in the way set out above.

7. **A failed sub-resource is invisible, and a tiny file can demand gigabytes.**
   Two failures with no exit code between them, both closed by the provider
   wrapper in `src/main.rs`.

   `blitz_net::Provider::fetch` reports a failed fetch through `tracing`, which
   is compiled out here, and never calls the handler: the node stays pending, the
   box paints blank, and the run exits 0 with a valid PNG of the wrong thing.
   Measured on `0.3.0-beta.8`: a `404` produced a **795-byte all-white PNG** at
   `rc=0` with nothing on stderr. Fetching through `fetch_with_callback` — the
   public path that hands back the `Result` — makes it reportable, and the run
   now names the url and the status and exits `4`.

   The same wrapper caps one decoded image at **128 MP**, read from the header
   before the decoder runs, so the refusal costs no allocation. The file that
   motivated it is **60 bytes** and claims `30000x30000`: 3.6 GB of RGBA, which
   on a host without the headroom aborts with `rc=134` — a signal, not a
   message, naming no file. `BLITZ_MAX_IMAGE_MP` moves the cap and `0` disables
   it; a real `9000x9000` PNG (81 MP) still paints in **2.36 s** at the default,
   so the cap is above what a document plausibly embeds.

   The header sniffer recognises **PNG, JPEG, GIF and WebP** — measured, not
   assumed: all four paint on `0.3.0-beta.8`, while **BMP and TIFF paint
   nothing**, so those two cannot allocate however large their header claims.
   Anything unrecognised passes through uncapped, because a cap that guesses
   would refuse documents that render today. `tools/test_assets.py` pins all of
   it in 10 legs, including the control that proves leg `c`'s refusal is the cap
   and not the fixture.

   **The in-flight counter is ours for the same reason.** `Provider::count`
   derives it from the waker `Arc` that `fetch` clones for the life of a
   request; `fetch_with_callback` does not clone it, so forwarding that count
   would report an empty queue while fetches were still running and the drain
   loop would stop early. The `fetch()` path in trap 1 is still the reason
   `is_empty()` is not racy *upstream*; this CLI no longer uses that path.

8. **Single-image mode cuts a taller document, and used to say nothing.** The
   canvas is exactly `<width> x <height>`, so a 2200 px document at 800x600 is a
   valid PNG of the top 600 px at `rc=0`, and before the `content_h` field its
   `TIMING` line was identical to a complete render's. Measured on
   `0.3.0-beta.8`: that stderr carries the `TIMING` line and no height at all.
   `--full` is the escape, and the default stays the viewport because every
   golden depends on it, so the fix is the signal rather than the geometry:
   `TIMING` carries `content_h=<document px> canvas_h=<canvas px>` **only** when
   the document exceeds the canvas, and stderr names the lost rows. The exit code
   stays `0`, because whether a knowingly partial render should be fatal is
   caller policy, not a renderer decision. `tools/test_full_page.py` pins both
   directions: leg `f` asserts the field and the row count on a cut render, leg
   `g` asserts both are absent on a document shorter than the canvas — without
   `g`, a substring check on a line the binary always prints would pass on any
   document and `f` would be decoration.
9. **The shared `--clip` canvas was the viewport's width, so a wide element was cut
   in half and called success.** An element wider than the viewport is laid out and
   painted past it, but the canvas was `phys_w` wide, so the frame was cropped at the
   viewport's edge: a 1600 px element in an 800 px viewport produced an 800×100 PNG at
   `rc=0`, half its content missing, and the numbers that would have revealed it
   (`w=1600.00` beside `physical 800x100`) were on the line a caller does not read.
   The canvas is now as wide as the widest match, which is a paint-surface change and
   not a layout one: `paint_scene` takes the surface size as an argument and builds its
   root clip from it (`blitz-paint` `render.rs`, `viewport_clip_rect`), while the
   document's own viewport is untouched, so `vh` units still resolve against the
   requested viewport. The row budget is recomputed from the widened width, so peak
   memory stays one band at that width rather than one band at the viewport's.
   `tools/test_clip_wide.py` pins it, and its `b` leg is the control that stops a
   widened canvas from passing while blank: the frame's right half must carry the ink
   of a child that sits entirely past the viewport edge.

## Withdrawn claims

**"Scrolling the viewport shifts the subpixel AA phase."** Wrong as a mechanism,
though the delta it was attached to is real. It implies a phase discontinuity at
the band edges — exactly the thing a reader would try to fix by overlapping the
bands — and there is none. A glyph cut exactly by a band boundary reassembles
with **0 differing pixels** at scales 1, 2 and 3; **100 %** of differing pixels
sit on ink rows; and their proximity to boundaries matches the boundaries' own
row share (scale 3, 1 MiB: **37 observed, 37.7 expected**). What is happening is
8-bit coverage rounding at glyph edges when a glyph is painted at a different
offset: bounded at **1 level**, never more, at every scale measured. Banded
renders agree with each other to 12-64 px while all sit 426-433 px from the
single canvas at scale 2, so the reference is the outlier and a fifth grid would
not be closer.

A second explanation is also withdrawn: that the `<=1` tolerance in
`test_bands.py` leg 4 was merely an artefact of comparing a 1 MiB `--clip` render
against a default-budget `--full` render, so that "the same budget gives 0 px".
It does not hold in general — the same budget gave **0 px at 1 MiB / scale 1**
but **3, 4 and 10 px** at the other three combinations measured. The tolerance
is right; the reason first offered for it was not.

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

Fourteen further boxes involve text and are reported separately, because without an
identical embedded font they measure font availability rather than layout. Four
of those fixtures were added because the corpus was otherwise 32 of 32
ASCII-only and could say nothing about shaping. The census that motivated it was
a byte grep, so three carry literal non-ASCII bytes — **3 of 36 fixtures** —
rather than HTML entities that would render the same text while leaving that
grep reading zero; the fourth is Latin words in an RTL container, which is
ASCII and still isolates a property no other fixture reaches.

| fixture | what its geometry reveals |
|---|---|
| `arabic-joining` | two coloured Arabic words in `direction: rtl`: joining changes each run's width, and the first word must land **right** of the second |
| `hebrew-rtl` | the same shape in Hebrew, so an engine that loses bidi ordering puts the runs in the wrong order |
| `combining-marks` | `e` + U+0301 + U+0300 against a bare `e`: the marks must stack **above** (taller box) without adding advance width |
| `direction-rtl-latin` | LTR words inside `direction: rtl`: isolates the **CSS property** from bidi, which the two fixtures above cannot — measured, removing `direction:rtl` from `arabic-joining` changes no pixel |

The last row is the one that separates two things the corpus previously
conflated. Arabic and Hebrew are ordered right-to-left by the Unicode bidi
algorithm because of the characters themselves, so their geometry is unchanged
when `direction` is removed or flipped. Only LTR text in an RTL container can
show whether the CSS property is honoured, and on `0.3.0-beta.8` the line
right-aligns while the span order stays LTR: `Alpha` at x=168, `Beta` at x=259.

**Measured against Chromium, all four land where it puts them.** Per-box deltas
from the run that added them: `arabic-joining` **0 px on both boxes**,
`hebrew-rtl` 0 and 1 px, `direction-rtl-latin` 1 px on both, `combining-marks`
1 px on the marked glyph and **2 px on the plain one** — the only new divergence,
and the reason the text-dependent bucket reads 6 exact / 4 within 1px / 4
divergent across its 14 rows. So the RTL ordering is agreement rather than a
defect: Chromium also leaves `Alpha` left of `Beta` there, which is why that
fixture is reported and not gated on a hand-written expectation. The one thing
the corpus now says is wrong is a 2 px advance on a bare `e` beside combining
marks, and 2 px is not worth a defect of its own until something else points at
the same code.

**CJK is deliberately absent.** No font on the runner covers it, and a character
with no covering font renders as a tofu box: measured here, `日`, `本` and `語` at
40 px produce **byte-identical PNGs**, so a CJK fixture would measure the absence
of a font rather than line breaking or full-width punctuation. Making it
measurable needs a CJK font in CI, and a CJK font also covers Latin, which would
move font resolution for every existing fixture and perturb the pinned corpus
verdict. That is a separate change with its own baseline move, not a fixture.

**This is a gate, not a report.** `tools/parity_baseline.json` pins every
divergent box by `fixture:colour` with its measured delta, and `run_parity.py`
exits `1` on a new divergence, on one that grew, on a missing box, on a corpus
that lost rows, on a fixture that stopped running, on a baseline that predates a
field it now checks — and on a baseline entry that stopped diverging, so a fix
cannot leave a stale tolerance behind that later hides the same box regressing.
`python3 tools/test_parity_gate.py` proves each of those legs fails, without
needing a browser.

## Fidelity

Measured against Chromium on the same document, same viewport, same scale —
both PNG sets read by one instrument (`tools/bench_compare.py`), so a difference
in the table is a difference between two engines rather than between two rulers.
Whole-deck, every pixel, no sampling:

| probe deck, scale 2, 2560×11520 | |
|---|---|
| pixels compared | **29,491,200** |
| pixels off by >16 | **4.5462 %** |
| mean channel difference | 7.115 |
| ink coverage | 19.4399 % engine vs 19.1282 % Chromium |

The residual is antialiasing, not layout: wrap points match, boxes sit at
Chromium's coordinates, and images are sampled at zero offset. Vello's
glyph AA is slightly softer than Skia's, so **you cannot diff html-render
against Chromium at 99 %** — and you don't need to, because against *itself* it
is exact.

`display: table` used to be the biggest hole here, and it is now measured rather
than absent. Fixture 32 (`table-collapse`) puts `border-collapse`, `colgroup`,
`rowspan`, `colspan` and `border-spacing` in front of both engines, and **all
seven of its boxes diverge**: the table comes out **18px taller** than Chromium
paints it (104 px against 86), one cell is **28 px tall where Chromium paints 12**, and
the other six are off by 2-16px. Both directions were stated backwards here until
2026-11-15: the run's `chromium` column is printed first and the `blitz` column
second (`tools/run_parity.py:157`), so the divergence is Blitz over-tall, not under. Those seven are pinned in `tools/parity_baseline.json` with that
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
