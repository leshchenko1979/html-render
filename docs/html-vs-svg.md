# Why the model writes HTML, not SVG

[`why-html-first.md`](why-html-first.md) argues that the *source* of a picture
should be text rather than a browser session. It leaves a second question open:
which text format? HTML/CSS, or SVG?

For a document that a model generates — a card, a slide, a figure — the answer
is HTML, and it is not close. On the one benchmark that scores both formats with
the same models and the same metric, HTML beats SVG by **+44.81 points at the
median** across 12 models, and **not one of the 12 prefers SVG**. The
slide-generation literature has separately converged on HTML and does not treat
SVG as a candidate at all.

This document records that evidence, because the alternative keeps coming up:
"why not have the model emit SVG and rasterise it?" It is a reasonable question
and the answer is a benchmark, not a preference.

## 1. The direct comparison

*StructEval: Benchmarking LLMs' Capabilities to Generate Structural Outputs* —
Yang, Jiang et al., University of Waterloo
([arXiv:2505.20139](https://arxiv.org/abs/2505.20139),
[leaderboard](https://tiger-ai-lab.github.io/StructEval/)).

1,085 visually-rendered examples across 25 tasks. Both HTML and SVG are rendered
with a headless renderer and judged by a VLM on the resulting image, so the
metric is *what the output looks like*, not what the code looks like. Same
models, same protocol, same judge.

Per-format generation scores, from Tables 9–10 of the paper:

| Model | Text→HTML | Text→SVG | gap |
|---|---|---|---|
| o1-mini | 99.44 | 58.12 | +41.32 |
| GPT-4.1-mini | 99.30 | 58.74 | +40.56 |
| Gemini-1.5-pro | 99.30 | 54.72 | +44.58 |
| Gemini-2.0-flash | 99.26 | 56.72 | +42.54 |
| GPT-4o | 99.22 | 56.78 | +42.44 |
| GPT-4o-mini | 99.02 | 53.98 | +45.04 |
| Qwen-3-4B | 98.80 | 53.44 | +45.36 |
| Phi-4-mini | 97.24 | 39.72 | +57.52 |
| Llama-3.1-8B | 95.96 | 44.18 | +51.78 |
| Qwen-2.5-7B | 92.92 | 47.30 | +45.62 |
| Phi-3-mini | 92.10 | 35.78 | +56.32 |
| Meta-Llama-3-8B | 72.52 | 40.16 | +32.36 |

| | Text→HTML | Text→SVG |
|---|---|---|
| mean | **95.42** | **49.97** |
| median | 98.91 | 53.71 |
| min | 72.52 | 35.78 |
| max | 99.44 | 58.74 |

Mean ratio **1.91×**. SVG is the *second-hardest* visual format in the whole
benchmark — only Mermaid, Typst and Vega score lower — while HTML is the
*easiest* by a wide margin. The paper places Text→HTML among tasks "effectively
solved by all LLMs with scores exceeding 0.95", and Text→SVG among tasks "where
all models struggle significantly".

### The honest caveat

Part of that 45-point gap is **error tolerance, not layout quality**. A
malformed SVG renders nothing and scores zero; malformed HTML usually still
renders *something*, because browsers and Blitz are forgiving of unclosed tags
and bad CSS. The benchmark counts a render failure against you — correctly,
since a card pipeline cannot use a blank frame either — but the number is not
purely a measure of "which format models understand better". It also measures
"which format degrades gracefully". For production that is not a confound, it is
the point.

## 2. The slide literature picked HTML and never looked at SVG

*Building Presentation Intelligence for High-Quality Slide Generation* — Peking
University / Alibaba ([arXiv:2610.30888](https://arxiv.org/html/2610.30888)).

Compares four paradigms on two slide benchmarks: Single-HTML, Multi-HTML,
Single-Python, Multi-Python.

| Dataset | Model | Single-HTML | Multi-HTML | Single-Python | Multi-Python |
|---|---|---|---|---|---|
| PresentBench | Qwen-3.6-27B | **70.45** | 63.39 | 59.56 | 60.80 |
| PresentBench | Qwen-3.6-35B-A3B | 64.93 | **64.28** | 64.10 | 64.91 |
| GDPVal (PPT) | Qwen-3.6-27B | **74.49** | 74.12 | 66.77 | 68.31 |
| GDPVal (PPT) | Qwen-3.6-35B-A3B | **74.01** | 71.58 | 70.03 | 71.57 |

HTML wins 3 of 4 rows and the authors adopt **Single-HTML** as their training
setting. Their stated reasons: *"HTML exhibits clear advantages in layout
composition, color design, visual diversity, and mathematical rendering"* — and
specifically that `python-pptx` has no maths typesetting engine, so formulas
degrade to plain text.

Their second finding matters to a card pipeline: single-page generation is more
robust than multi-page, because *"independent slide-wise generation naturally
facilitates fault isolation, precise error localization, and incremental
refinement"*.

SVG does not appear as a paradigm at all. The field's candidate set is HTML vs
Python.

*AutoPresent / SlidesBench* — Ge et al., CVPR 2025
([paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Ge_AutoPresent_Designing_Structured_Visuals_from_Scratch_CVPR_2025_paper.pdf),
[code](https://github.com/para-lost/autopresent)).

Same conclusion from a different angle: *"programmatic methods produce
higher-quality slides in user-interactable formats"* than end-to-end image
generation. And one number here is the whole argument for CSS over raw
coordinates:

> Generating the slide as a raw program averages **170 lines**, which models fail
> to get right. Wrapping the same operations in a small library (SlidesLib) drops
> it to **13 lines** and gains up to **34 points**.

CSS *is* that library. It is the abstraction that saves the model from emitting
coordinates — which is exactly what the renderer already does for every other
document it paints.

## 3. Why SVG loses, mechanically

Three reinforcing causes, not one.

**Training data imbalance.** *PrismaCoder* ([arXiv:2610.20538](https://arxiv.org/abs/2610.20538))
is the largest open multimodal-code corpus. Its task mix:

| Task family | Samples | Share |
|---|---|---|
| WebUI Generation | 357.1K | 17.8 % |
| SVG Generation | 53.3K | 2.7 % |

**6.70× more WebUI samples than SVG.** Models are better at what they saw more
of.

**SVG is buried inside HTML in the pretraining corpus.** *LLM4SVG* — Xing et al.,
CVPR 2025
([PDF](https://openaccess.thecvf.com/content/CVPR2025/papers/Xing_Empowering_LLMs_to_Understand_and_Generate_Complex_Vector_Graphics_CVPR_2025_paper.pdf))
names the mechanism: *"SVG data from the internet is often embedded within web
page code, requiring LLMs to parse layered languages, which renders SVG data
less accessible amidst lengthy XML tags. These limitations manifest as confusion
or hallucinations in vector path sequences."*

**Text is the weak point — and cards are text.** *VecGlypher* (CVPR 2026,
[PDF](https://xk-huang.github.io/VecGlypher/assets/VecGlypher.pdf)): *"Many
general LLMs that can generate generic SVG graphics (for example icons or simple
drawings) still struggle to draw valid glyphs and to follow typography style
instructions."* In SVG every glyph run is either an absolute `<text>` placement
the model has to compute, or a path it has to draw. In HTML the layout engine
does line-breaking, wrapping, hyphenation, vertical centring and font fallback
for free.

A card is a text container with a picture on it. That is precisely the axis where
SVG generation is weakest.

## 4. Where SVG is the right answer

Not a strawman — it wins on these:

- **Geometry is the content.** Icons, logos, line art, diagrams, anything where
  the path *is* the message. Resolution-independent, diffable, tiny.
- **Deterministic templates.** If a chart library (matplotlib, Vega, ECharts)
  emits the SVG from data, no model is generating coordinates and the failure
  modes above vanish. The *model* writes the spec; the *library* writes the SVG.
  That is a different question from "the model emits SVG".
- **Downstream editing.** SVG stays editable as vectors in Illustrator or Figma;
  a rendered PNG does not.

The case that fails is specifically **an LLM authoring SVG directly for a
text-heavy layout**.

## 5. What this means for this repository

1. **No SVG generation path for cards.** It would be a bet against a ~45-point
   benchmark gap and a 6.70× data imbalance, on the exact axis — text layout —
   where the format is weakest.
2. **HTML→PNG is the pipeline**, and the renderer already owns it: the engine
   does the layout maths the model would otherwise have to emit as coordinates.
3. **If SVG ever enters, it should enter as an input format** — a deterministic
   template or a chart library's output embedded as `<img src="x.svg">` or
   inline `<svg>`, rasterised by the same engine. The model keeps authoring
   HTML; the vector art is authored by code.
4. **This is a format-choice argument, not a fidelity one.** It says nothing
   about how closely the output matches Chromium. That divergence is measured
   separately in [`GATES.md`](../GATES.md).

## Sources

| Source | What it establishes |
|---|---|
| [StructEval, arXiv:2505.20139](https://arxiv.org/abs/2505.20139) | T→HTML 95.42 mean vs T→SVG 49.97, 12 models, none prefer SVG |
| [Presentation Intelligence, arXiv:2610.30888](https://arxiv.org/html/2610.30888) | Single-HTML beats Python paradigms on 2 slide benchmarks; adopted as the training setting |
| [AutoPresent, CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Ge_AutoPresent_Designing_Structured_Visuals_from_Scratch_CVPR_2025_paper.pdf) | programmatic > end-to-end image; 170 lines → 13 with an abstraction library |
| [Design2Code, NAACL 2025](https://arxiv.org/abs/2403.03163) | screenshot→HTML is a mature, benchmarked task |
| [LLM4SVG, CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/papers/Xing_Empowering_LLMs_to_Understand_and_Generate_Complex_Vector_Graphics_CVPR_2025_paper.pdf) | why SVG is underrepresented: buried inside HTML, path hallucination |
| [VecGlypher, CVPR 2026](https://xk-huang.github.io/VecGlypher/assets/VecGlypher.pdf) | general LLMs still fail at glyphs and typography in SVG |
| [SVGenius, arXiv:2506.03139](https://arxiv.org/html/2506.03139) | 22 models on SVG; all degrade sharply with complexity, style transfer worst |
| [PrismaCoder, arXiv:2610.20538](https://arxiv.org/abs/2610.20538) | WebUI 357.1K samples vs SVG 53.3K — 6.70× |

Every figure above attributed to a third party is theirs, quoted from the linked
source. This document states no measurement of its own; the repository's own
measurements live in `bench/results.json` and are guarded by
`tools/check_claims.py`.
