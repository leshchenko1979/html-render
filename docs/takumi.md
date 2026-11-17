# What takumi does differently

[`why-html-first.md`](why-html-first.md) argues for text-as-source rendering
without a browser. [`html-vs-svg.md`](html-vs-svg.md) settles which text format.
Both leave a third question open: if this approach is right, what do the *other*
implementations of it look like, and is one of them a better base than Blitz?

The most serious alternative is [takumi](https://github.com/kane50613/takumi):
Rust, no browser, Blink-derived layout, and a PDF backend. This document records
what was read in its source, because two of its findings change what we know
about our own engine — and because the obvious conclusion, "it is smaller and
faster, switch to it", does not survive contact with what it cannot do.

Every figure below was read from the source at the pinned commit, not from its
README. The line counts, test counts and reference counts were re-measured
before publication; the first pass had five of them wrong, and the corrected
values are the ones here.

## 1. It is not a small renderer. It is a Blink port.

| crate | lines | role |
|---|---:|---|
| `takumi-core` | **191,752** (405 files) | layout, style, fonts, svg, HTML parsing |
| `takumi-pdf` | 43,542 | paged PDF writer |
| `takumi-raster` | 17,450 | tiny-skia raster backend |
| `takumi-cli` | 4,859 | the `takumi` command |
| `takumi-svg` | 3,758 | vector SVG backend |

For scale, this repository's entire `src/` is **2,079 lines in one file** and
delegates layout to Blitz. `takumi-core` is **92×** that, and it contains the
layout engine itself: 72,015 lines, of which inline is 21,125, table 7,790,
grid 6,927 and flex 4,149, plus block fragmentation, paged layout, exclusions
(floats), line clamping and block break tokens.

The provenance is explicit rather than inferred: **3,106 references to Blink**
across 269 files, and tests named after the Blink source they port —
`fragmentation_tests.rs` opens with *"after Blink's `fragmentation_test.cc`"*.

## 2. No Stylo, and that is the architectural difference

`grep stylo` over the manifests returns **zero**. Where Blitz inherits Servo's
Stylo for the cascade and computed values, takumi hand-writes them: 76 property
modules totalling 52,763 lines, built on Servo's `cssparser` and `selectors`
(the parsing and matching primitives, not the cascade engine). There is also a
5,066-line **Tailwind utility parser inside the engine**, which has no
equivalent here — a Tailwind deck must be pre-built before pageplate sees it.

That hand-written CSS stack is the single largest liability in the codebase: it
re-implements every property's computed-value logic and inherits none of Stylo's
conformance. Blitz's Stylo inheritance is an advantage of this project, not a
gap in it.

## 3. Four backends, and the PDF one is the surprise

One layout tree feeds raster (PNG, JPEG, WebP, ICO, animated WebP/APNG/GIF),
vector SVG, a **paint tree as data**, and PDF.

`takumi-pdf` is not a raster wrapper. From its own crate documentation: content
flows across pages as Chromium lays out print, table rows break between pages,
`break-before`/`break-after`/`break-inside` are honoured, `widows` and `orphans`
default to 2 as Chromium does, **a table repeats its `<thead>`/`<tfoot>` on every
page it crosses**, header and footer bands expose `pageNumber`/`totalPages`,
links become annotations, the outline is built from `h1`–`h6`, and **tagged PDF
is on by default** with **PDF/A conformance and validation**.

That is more than WeasyPrint offers on tagging and PDF/A, and it is exactly what
this tool structurally cannot do: `--full` paints one tall canvas in bands, not
pages. The two are different products. A picture is not a document, and pageplate
is not a PDF renderer.

## 4. Content height, and a correction

`ViewportSize.height` is an `Option<u32>`. `None` maps to
`AvailableSpace::MaxContent`, and `Scene::lay_out` folds the content size into
the canvas size, so omitting the height yields a canvas as tall as the content.
The CLI says so in its own help: *"Without it, the image is as tall as its
content."*

An earlier research note of ours claimed takumi had no content-height mode. That
was wrong: it was true of a third-party wrapper binary, not of the library or the
official CLI. The general lesson is recorded here because it cost real time — a
measurement of a wrapper is not a measurement of the project.

Genuinely absent: **element-scoped rendering**. `RenderOptions` has no selector
field and no CLI path renders a subtree, so there is no `--clip` analogue.

## 5. Three APIs worth taking

| API | what it gives |
|---|---|
| `needs()` | pre-flight: the image URLs and `@font-face` files a tree will load, before rendering |
| `uncovered_characters()` | every character no registered font can draw |
| paint tree dump | per node: `offscreen`, `empty`, `draws nothing`, and a `not painted` section |

`needs()` is the architectural one. Layout declares what it needs, the host
fetches it, the host hands back decoded images: the engine never opens a socket.
That is why its sandbox can say the network is simply not reachable — SSRF is not
mitigated there, it is structurally impossible. Our `--no-network` and
`--allow-host` are policies over a fetcher that exists, which is a weaker shape.

`uncovered_characters()` is the honest version of what this tool currently has to
say on a fontless host: that substitution cannot be detected. Enumerating the
undrawable characters answers the question instead of declining it.

The paint-tree markers are the exact diagnostic for the defect class where a
resource silently paints nothing.

## 6. Security model

Stronger than ours. Each granted root is an open directory handle and every read
resolves through `cap-std`, so `..`, a symlink, or a symlink swapped between
check and read cannot escape. Budgets are 32 MiB per resource, 256 MiB per
render, 1024 resources. The network is never reachable.

## 7. Correctness: it ports the reference, it does not agree with it

**1,898 `#[test]` functions in `takumi-core`** (2,200 across all crates). What it
does not have is any visual-parity corpus: its `e2e/` directory is bundler
integration (nitro, next, cloudflare, wrangler), with no screenshots and no
browser comparison.

So its correctness claim is *"we ported the reference implementation's tests"*,
not *"we agree with Chrome on a measured corpus"*. This repository's parity gate
is the stronger instrument for "does this match the browser"; takumi's is
stronger for "did we port Blink faithfully". They answer different questions,
which is why comparing them by test count would be meaningless.

## 8. What it proves about our open table defect

Measured with the same probe ruler this repository uses, on the table fixture
that carries our open table defect: takumi lands **on Chromium's values** for
four of six probes and within 2 px for the rest, where Blitz is off by 2–16 px on
every one.

The mechanism is visible in its source. Its cell block size honours `box-sizing`
and the border-padding floor before the row's maximum is taken, which is Blink's
`ComputeMinimumRowBlockSize` semantics, ported. That is what makes the finding
useful rather than merely interesting: a second browser-free engine reaching the
same numbers as Chromium localises the defect to Blitz's arithmetic, and refutes
the explanation that a non-browser engine cannot get tables right.

On the other open divergence, takumi is **pixel-identical to Blitz**: two
independent engines agree and Chromium is the outlier. That divergence is
Chromium's, not a defect in either.

## 9. Verdict: do not switch

A swap would buy a smaller binary and less memory, and cost the CLI contract, the
parity gate and every gated claim in this tree. More to the point, it would trade
a renderer whose job is *a picture* for one whose centre of gravity is *a paged
document* — a different product with a different failure surface.

What is worth watching is the PDF backend, because it solves a problem this tool
does not attempt. What is worth adopting is `needs()` and the paint-tree
diagnostics. What is worth avoiding is the hand-written CSS stack.

## Sources

| Source | What it establishes |
|---|---|
| [kane50613/takumi](https://github.com/kane50613/takumi) @ `8a55f0b` | every figure in this document, read from source |
| [Blink's `ComputeMinimumRowBlockSize`](https://source.chromium.org/chromium/chromium/src/+/main:third_party/blink/renderer/core/layout/table/) | the semantics takumi ports and Blitz does not yet match |

The takumi figures are read from a pinned third-party commit and cannot be
re-verified by this repository's guard; the commit above is the provenance. This
repository's own measurements live in `bench/results.json` and are guarded by
`tools/check_claims.py`.
