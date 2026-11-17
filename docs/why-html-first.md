# Why author HTML/CSS first and render it to PNG

Most people who need a picture of a document reach for a browser: drive
Chromium, screenshot the page, ship the PNG. This repository takes the other
route — parse HTML, compute style, lay out, paint, encode — and the obvious
question is what that is *for*.

It is not for every case. A browser is the correct tool when you need the web
platform: JavaScript, video, `position: fixed` against a real viewport, a page
that fetches its own data. This document is about the cases where it is the
wrong tool, and why authoring the document as HTML/CSS first is worth it even
when you do not need a browser to paint it.

The through-line: **HTML/CSS is a text format that describes a picture.** If the
picture must be reviewed, versioned, diffed, regenerated, or produced by
something that only emits text, then the source should be text — and the
renderer is a build step, not a service.

## 1. Reproducible publication figures

The reproducibility argument in scientific publishing is usually made about
data and code, and it stops one step short of the figure. A figure assembled in
a vector editor is not reproducible: the file records the result, not the
process, and re-running the analysis means re-editing the figure by hand.

Gerum's `pylustrator` paper states the problem directly — image-editing and
vector-drawing tools "cannot provide a reproducible way of generating figures
and bear the danger of accidentally changing data points", and any change to the
analysis forces the figure to be re-edited
([arXiv:1910.00279](https://arxiv.org/abs/1910.00279)). Its answer is to record
the interactive styling as *code*, so the figure regenerates from the data
([github.com/rgerum/pylustrator](https://github.com/rgerum/pylustrator)).

That is the argument for a markup-first renderer in one sentence: the figure's
source is a document you can commit, review as a diff, and regenerate on every
data refresh. A PNG that was drawn once is an artefact; a `.html` file that
produces that PNG is a recipe.

Where this repo adds something `pylustrator` does not: the recipe is not Python
calling a plotting library, it is HTML and CSS, so the same document can also be
opened in a browser for a human to inspect — and the renderer becomes an
optional accelerator rather than the only way to see the figure.

## 2. Figure-as-code: text-native output for LLM pipelines

A model that emits a PNG cannot be diffed, reviewed, or patched. It can only be
regenerated. The `FigureCode` benchmark makes this the centre of its design: it
asks for scientific architecture diagrams as **pure HTML/SVG source**, not as
images, on the grounds that image outputs are "inherently difficult to edit,
verify, and integrate into text-native LLM workflows"
([aclanthology.org/2026.findings-emnlp.2060.pdf](https://aclanthology.org/2026.findings-emnlp.2060.pdf)).

Their reported numbers, which are theirs and not this repository's: on 1,369
figures from ACL-venue papers, code-based generation reached 84.3 % edit-request
success against 54.3 % for the image-based baseline, 52.7 F1@5 retrieval against
27.4, and 66.1 % WCAG/ARIA coverage — properties that have no analogue when the
output is a bitmap
([same paper](https://aclanthology.org/2026.findings-emnlp.2060.pdf)).

The earlier `Plot2Code` benchmark is the converse direction — recover executable
code from a plot — and is useful for the same reason: it treats the code as the
thing worth evaluating, not the pixels
([aclanthology.org/2025.findings-naacl.164.pdf](https://aclanthology.org/2025.findings-naacl.164.pdf)).

**Which text format** is a separate question, and it also has an answer:
[`html-vs-svg.md`](html-vs-svg.md) records the benchmark where HTML beats SVG by
44.81 points at the median across 12 models, with none preferring SVG.

**The practical consequence.** If an agent writes a figure, the only useful
output is text. Rendering it to PNG is a presentation step that should be
cheap, deterministic, and available offline. Spawning a browser per figure
turns a text-generation loop into a process-management problem.

## 3. Visual regression testing

Here the browser is not merely inconvenient; it is the main source of false
positives.

A screenshot is not a property of your page. It is a property of your page
rendered by a particular browser build, on a particular OS, with particular
fonts, on particular hardware — and headless and headed Chromium have
historically been *different renderers*, which makes a baseline captured one way
and compared the other a category error that presents as flakiness
([qualflare.com](https://qualflare.com/blog/visual-regression-flaky-tests/)).

Pei, Sohn and Papadakis, *An Empirical Study of Web Visual Flakiness* (Journal of
Systems and Software, 2026), analysed 262 cases and report — as summarised in
that write-up, so treat the citation chain accordingly — that visual flakiness
accounted for a mean 37.2 % (median 33.3 %) of a project's flaky instances, and
that visual commits were flaky at 3.7 ‰ against 0.8 ‰ for non-visual ones
([qualflare.com](https://qualflare.com/blog/visual-regression-flaky-tests/)).

Two properties of a non-browser renderer follow directly:

- **The font stack is the document's, and a substitution is reported.** Fonts are
  the first cause anyone names for cross-platform screenshot drift. This renderer
  resolves the document's stack against the host's fontconfig, so on its own it
  pins nothing — but a family it could not find is named on stderr instead of
  being swapped in silently, and a container with a fixed font set removes the
  variable rather than hiding it.
- **The renderer is a binary, not a service.** No GPU process to fail, no
  browser download in CI, no sandbox to configure.

What it costs: it will not match Chromium pixel for pixel. That is a real
trade — this repository's own corpus gate measures the divergence rather than
claiming parity, and the divergent rows are pinned and explained.

## 4. Social cards and Open Graph images

Social cards are the case where "HTML/CSS to PNG without a browser" is already
mainstream. Vercel's `@vercel/og` documents itself as generating images "from
HTML/CSS without a browser", built on Satori (HTML/CSS → SVG) and Resvg
(SVG → PNG), with a recommended size of 1200×630
([vercel.com/docs/og-image-generation](https://vercel.com/docs/og-image-generation),
[github.com/vercel/satori](https://github.com/vercel/satori)).

Two lessons from that stack are worth carrying over:

1. **Subsetting CSS is a legitimate design.** Satori supports flexbox and a
   subset of properties; grid does not work
   ([same docs](https://vercel.com/docs/og-image-generation)). A renderer that
   honestly declares its CSS surface is more useful than one that silently
   approximates.
2. **The bundle budget is the product.** Cards are generated per-request at the
   edge, where a browser process is not an option at all. Cost per image is the
   whole design constraint.

For a static site the same reasoning applies at build time: a card per page,
regenerated in the same commit as the page, reviewable as a diff.

## 5. Print and email previews

Paged output is where CSS has a formal answer and browsers implement it
partially. The model is specified — page boxes, margin boxes, running headers,
`counter(page)`/`counter(pages)`, break control — in
[CSS Paged Media Module Level 3](https://www.w3.org/TR/css-page-3/), and
implementations exist outside the browser: WeasyPrint documents its pipeline as
parse → style → box tree → layout onto pages → paint
([doc.courtbouillon.org](https://doc.courtbouillon.org/weasyprint/stable/going_further.html)).

The engineering point is that a paged-media engine is a *layout* problem with a
known specification, not a rendering-engine problem that requires Blink. A
survey of HTML-to-PDF engines reaches the same conclusion — every one of them is
"a layout engine with a PDF writer behind it", and the output quality is decided
by how well the engine implements CSS Paged Media
([llion.net](https://www.llion.net/developers/html-to-pdf.html)).

Email preview is the same problem with a worse toolchain: clients differ wildly,
and being able to render a template to a PNG in a test — offline, at a fixed
width, with a font stack you control — turns "does it look right in Outlook"
into a check.

## 6. Slide decks

A deck is the case this repository exists for, and it is a good stress test
because it combines everything above: a fixed pixel grid, images, text shaping
across scripts, and a need for the result to be reproducible from source.

A deck authored as HTML is a deck you can diff. A deck exported from presentation
software is a binary blob whose rendering is at the mercy of whatever opens it.
Rendering the HTML to one tall PNG makes the deck a single reviewable artefact,
and — usefully — makes it measurable: same document in, same bytes out.

## What this does not solve

Stated plainly, because the value proposition is narrower than "faster browser":

- **JavaScript.** No scripting, no dynamic data fetching at render time. The
  document must be complete before rendering.
- **Full CSS.** Not every property is implemented, and the gaps are tracked
  rather than hidden.
- **Pixel parity with Chromium.** There is a residual difference on glyph edges
  from coordinate rounding. It is bounded and measured; it is not zero.
- **The long tail of web platform behaviour** — `position: fixed` against a real
  scrolling viewport, video, web fonts loaded by URL at paint time, and so on.

The honest framing: this is a renderer for **documents that are already
finished**. If the document needs a browser to become itself, use a browser. If
it is finished HTML and you need a PNG of it — deterministically, in CI, without
a GPU, from text — a browser is a large dependency for a small job.

## Sources

| # | Source | Used for |
|---|---|---|
| 1 | [arXiv:1910.00279 — pylustrator](https://arxiv.org/abs/1910.00279) | reproducibility argument, figure-as-code |
| 2 | [github.com/rgerum/pylustrator](https://github.com/rgerum/pylustrator) | styling recorded as code |
| 3 | [FigureCode benchmark (EMNLP Findings)](https://aclanthology.org/2026.findings-emnlp.2060.pdf) | figure-as-code, its 84.3/54.3 %, 52.7 F1@5, 66.1 % WCAG figures |
| 4 | [Plot2Code (NAACL Findings)](https://aclanthology.org/2025.findings-naacl.164.pdf) | code as the evaluated object |
| 5 | [qualflare — why visual regression tests flake](https://qualflare.com/blog/visual-regression-flaky-tests/) | headed vs headless renderers; Pei et al. 2026 figures as summarised there |
| 6 | [Vercel — OG image generation](https://vercel.com/docs/og-image-generation) | Satori+Resvg, 1200×630, CSS subset |
| 7 | [github.com/vercel/satori](https://github.com/vercel/satori) | HTML/CSS → SVG |
| 8 | [CSS Paged Media Level 3 (W3C)](https://www.w3.org/TR/css-page-3/) | page model, margin boxes, counters |
| 9 | [WeasyPrint — going further](https://doc.courtbouillon.org/weasyprint/stable/going_further.html) | paged rendering pipeline |
| 10 | [llion.net — HTML to PDF](https://www.llion.net/developers/html-to-pdf.html) | every engine is a layout engine plus a writer |
| 11 | [Chromium — Memory Usage Backgrounder](https://www.chromium.org/developers/memory-usage-backgrounder/) | multi-process model and its memory cost |
| 12 | [Chromium — Multi-process Architecture](https://www.chromium.org/developers/design-documents/multi-process-architecture/) | renderer process per site instance |

Every figure above attributed to a third party is theirs, quoted from the linked
source. This document states no measurement of its own; the repository's own
measurements live in `bench/results.json`, taken by `tools/bench.py`, and are
guarded by `tools/check_claims.py`.
