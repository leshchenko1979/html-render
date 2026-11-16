# pageplate

Turns HTML and CSS into a PNG without a browser in the loop. One Rust binary, one process.

`0.4.0`, one maintainer. Linux x86_64, glibc ≥ 2.35.

![The 3200×1800 hero render: a slide titled "HTML + CSS → PNG, with no browser", beside bar charts of peak memory and install size against Chromium](assets/hero.png)

```bash
printf '<h1>hello</h1>' > t.html   # the argument is a path, not a URL; a missing file panics (101)
pageplate t.html hello.png 1280 720 2                      # exit 0, hello.png written
pageplate assets/hero.html hero.png 1600 900 2 --document  # the PNG above; needs this repo cloned
```

[![parity](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/blob/main/LICENSE)

## Why it exists

Getting a page into an image usually means using a browser: a memory footprint of 2GB+, a cold start ahead of the first pixel, and a runtime to install before any of it.

Our CLI tool reads a file and writes a PNG. On an 8-slide deck its peak is **at least 31× less memory**
than headless Chromium ([the measurements](#performance)), and deck and probe both ship in this
repo, so `python3 tools/bench.py` re-derives every row.

Decks, social cards, report one-pagers, CI visual diffs: HTML plus CSS in, an image out.

## Install

Linux x86_64 with glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+, or a current Fedora. Anything else wants the container below.

```bash
V=v0.4.0
N=pageplate_0.4.0_linux_amd64
BASE="https://github.com/leshchenko1979/pageplate/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 pageplate /usr/local/bin/ && pageplate --version
```

Provenance is checkable without a key: each release asset is attested by the release workflow's own
OIDC identity, with GitHub as issuer. The checksum catches a damaged download; the attestation
names the repository, commit and workflow behind the build. Both need the `gh` CLI.

```bash
gh attestation verify "$N.tar.gz" --repo leshchenko1979/pageplate
gh attestation verify checksums.txt --repo leshchenko1979/pageplate
```

The release job attests both assets, publishes them, then downloads the published files back and runs these same two
commands on them; a release whose downloaded asset does not verify turns the job red. That check runs after publication,
so a red run there means a live release needs re-cutting, not that nothing was published.

The binary names six libraries directly — `libc`, `libssl`, `libcrypto`, `libfontconfig`, `libm`, `libgcc_s` — and
pulls freetype, expat, libpng, bzip2, brotli and zlib through fontconfig, so `ldd` lists 12 of them on bookworm and 13
on jammy. A slim Debian or Ubuntu host needs one extra package line:
`apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbz2-1.0 libbrotli1 ca-certificates`

The image carries those libraries and a font, but no network policy: a document's sub-resources are fetched at render time, so the container does not isolate egress ([#29](https://github.com/leshchenko1979/pageplate/issues/29)).

```bash
docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    -v "$PWD:/work" ghcr.io/leshchenko1979/pageplate:v0.4.0 \
    /work/card.html /work/card.png 1280 720 2
```

`--user` is not decorative: the image's own uid is 10001, and omitting the flag exits `101`. Two tags
exist, `v0.4.0` and `latest`. A moving tag lets two builds of one commit disagree, so pin the
version, or the digest when the image itself must be frozen. The tarball's attestation does not extend to the image,
so verify that one by digest ([#31](https://github.com/leshchenko1979/pageplate/issues/31)).
Building from source: [CONTRIBUTING.md](CONTRIBUTING.md#building).

The image is built on a tag push, a `workflow_dispatch`, or a commit touching `src/`, `Cargo.toml`, `Cargo.lock`,
`Dockerfile` or `assets/`; it has no scheduled rebuild, so pin the digest when base-image patches matter
([#31](https://github.com/leshchenko1979/pageplate/issues/31)). The tarball is the other way round: it links your
host's `libssl.so.3`, so your distro patches it, which makes the tarball the better artifact for TLS patch latency.

## Usage

```bash
pageplate page.html out.png 1280 720 2                            # one PNG
pageplate --clip deck.html slides/ 1280 720 2 'section.slide'    # one PNG per match
pageplate --clip deck.html slides/ 1280 720 2 'section.slide' --per-element   # element-sized canvases
```

The viewport is given in CSS pixels and `scale` multiplies the output; a single-image run may leave it out. A
`1280 640 2` run writes 2560×1280, the shape of `assets/og.png`. `--clip` writes `slides/clip_01.png`,
`clip_02.png` and so on, numbered from `01` in document order.

**Full reference: [docs/USAGE.md](docs/USAGE.md)** — every flag, all six environment variables, the exit-code
contract, the three modes and their memory shapes, and the two machine-readable streams.

Sub-resources are fetched at render time with no allowlist, so an untrusted document reaches the network;
[SECURITY.md](SECURITY.md#what-rendering-does-to-the-machine) covers what that means for the host.

## Performance

Nothing below is typed by hand: `bench/results.json` is a receipt that `tools/bench.py` wrote on a GitHub
runner, and the repo commits it unedited. The deck comes from a seed, so one command reproduces this document anywhere:

```bash
git clone https://github.com/leshchenko1979/pageplate && cd pageplate
python3 tools/bench.py --bin <path-to-render> --chrome <path-to-chrome> --out /tmp/mine.json
```

A `deck.html` digest of `0593ca9f047b4110432898a9cba23263` means the same document as the receipt. Any other
digest means the generator moved, and the rows below describe a different document.

Eight slides at 1280×5760 CSS px, scale 2, three renders per engine; peak RSS sums every process the engine spawned:

| measurement | Chromium | pageplate | ratio |
|---|---|---|---|
| peak RSS, `--full` | 2380.2 MiB | 37.8 MiB | **62.97×** |
| peak RSS, `--clip` | 2380.2 MiB | 75.4 MiB | **31.57×** |
| peak RSS, `--per-element` | 2380.2 MiB | 47.5 MiB | **50.11×** |

**Memory carries the claim.** At least 31× less in the least favourable mode, and 50× in the advertised one —
`--per-element`, the figure the hero quotes. The band repeats: six runs of the probe on one runner image
(43744837000, 43746767310, 43752965645, 43754284965, 43762042304, 43886554380) kept pageplate's `--full` peak inside
37.6 MiB to 37.9 MiB, a 0.8 % spread, while Chromium ranged over 2336 MiB to 2453 MiB. Four of the six
measured the previous deck (same canvas, different document — [GATES.md](GATES.md#cost) has the table); the
published figure is the per-run median of sampled peaks, and the widest single samples reached 38.1 and
2474.1 MiB.

**The headline is one deck, one render.** Peak memory follows canvas area, not page length, and there is no
daemon and no queue: N parallel renders multiply the peak by N. One 128 MP decode costs about 488 MiB of RGBA,
and the worst case in `tools/memory_baseline.json` is `clip_oversized_frame` at **809.5 MiB**, 21× the
37.8 MiB headline. Budget a container from that.

**Wall clock is measured, gated, and not advertised.** The receipt carries it for both engines, and the drift
check gates the wall *ratio* at a deliberately coarse 45 %: a 1.85× slowdown of this renderer trips it, a 1.80×
one does not. Absolute seconds stay ungated, because on one host, one deck and one binary Chromium read
3.186 s on one run and 3.774 s on the next. A ratio that mostly reports the other program's load is not a
property of this one.

Method, host and the run itself: [GATES.md](GATES.md#cost).

## Fidelity against Chromium

At the same viewport and scale, pixel for pixel against Chromium, the probe's deck differs on **4.7102 % of
pixels by more than 16 levels** across 29,491,200 pixels. The mean channel difference is 8.291; ink coverage
is 17.073 % (Blitz) against 16.6941 % (Chromium). This is the entire deck at full resolution rather than a
sample, and `tools/bench_compare.py` recomputes it from the two PNG sets.

Pixel divergence is what the deck measures. On this deck it is mostly antialiasing and glyph rasterisation;
how much of it is layout is not quantified here. The deck contains a `border-collapse` table, and table layout
is one of the known divergences below.

**The reproducible number is the corpus.** Every run measures geometry over 36 fixtures at one viewport,
against Chromium: **the corpus agrees on 42 of its 54 text-free rows**; 11 diverge and 1 asserts a shared
absence. `tools/parity_baseline.json` pins all 11 box by box, so a regression reddens the build while the
current defects stay visible. The reference engine is the runner image's Chrome, not a pinned build —
157.0.8103.78 when measured — so a runner refresh moves the baseline with it ([#34](https://github.com/leshchenko1979/pageplate/issues/34)).
The divergence is not spread: one table accounts for **7 rows** (#9), one
`<br>` run for **4** (#15).

## Scope

Linux x86_64 with glibc ≥ 2.35, and beta: flags and output names can move between minor versions, and
nothing here is a stable API. No JavaScript — the document renders as the HTML that arrived. PNG only, with
no PDF and no print model. Blitz's CSS is not Chromium's: an unknown property is dropped, not rejected, so
what a reader sees is a declaration with no effect.

## Known defects

Defects and gaps are tracked as issues, not restated here:
**[open limitations](https://github.com/leshchenko1979/pageplate/issues?q=is%3Aissue+is%3Aopen+label%3Alimitation)**.
That list is authoritative and current; this page describes what the tool does, not what is wrong with it.

## Where the detail lives

| file | holds |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | every flag, environment variable, exit code and output stream |
| [GATES.md](GATES.md) | determinism, device scale, element clip, cost, corpus, fidelity, CSS audit |
| [CONTRIBUTING.md](CONTRIBUTING.md) | build requirements, the CI workflows, the rules a change must pass |
| [SECURITY.md](SECURITY.md) | what rendering does to the machine, measured egress, reporting |
| [CHANGELOG.md](CHANGELOG.md) | what moved between releases |
| [bench/README.md](bench/README.md) | the probe: what it measures, how to re-run it, what the bands mean |
| [docs/why-html-first.md](docs/why-html-first.md) | what HTML-and-CSS-first rendering is good for, with sources |

## Contributing

A minimal HTML repro in a bug report is the most useful thing to send. Pull requests get read and reviewed, at
the pace of one person on a beta; [CONTRIBUTING.md](CONTRIBUTING.md#rules-that-bite) lists the rules a change has
to pass, and [CONTRIBUTING.md#building](CONTRIBUTING.md#building) has the build and the CI workflows.

## Licence

Apache-2.0, with an explicit patent grant. Full text in [LICENSE](LICENSE). If you redistribute a binary, ship
that file with it.
