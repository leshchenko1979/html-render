# pageplate

Turns HTML and CSS into a PNG without a browser in the loop.
`0.5.0`, one maintainer, one Rust binary.
Linux x86_64, glibc ≥ 2.35.

![The 3200×1800 hero render: a slide titled "HTML + CSS → PNG, with no browser", beside bar charts of peak memory and install size against Chromium](assets/hero.png)

```bash
printf '<h1>hello</h1>' > t.html                           # the argument is a path, not a URL
pageplate t.html hello.png 1280 720 2                       # exit 0, hello.png written
pageplate assets/hero.html hero.png 1600 900 2 --document   # the PNG above; needs this repo cloned
```

[![parity](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml/badge.svg)](https://github.com/leshchenko1979/pageplate/actions/workflows/parity.yml)
[![release](https://img.shields.io/github/v/release/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/releases)
[![licence](https://img.shields.io/github/license/leshchenko1979/pageplate)](https://github.com/leshchenko1979/pageplate/blob/main/LICENSE)

## Why it exists

A browser to turn a page into an image costs 2GB+ of memory, a cold start, and a runtime to install.
This reads a file and writes a PNG, with a peak **at least 31× less memory** than headless Chromium
on an 8-slide deck.
Decks, social cards, report one-pagers and CI visual diffs are the shapes it fits.

## Install

```bash
V=v0.5.0
N=pageplate_0.5.0_linux_amd64
BASE="https://github.com/leshchenko1979/pageplate/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 pageplate /usr/local/bin/ && pageplate --version
```

Each release asset is attested by the release workflow's own OIDC identity, so provenance is checkable without a key; [docs/INSTALL.md](docs/INSTALL.md) has the verification commands.

Ubuntu 22.04+, Debian 12+ or a current Fedora cover the tarball; anything else wants the container image.
The tarball's attestation does not extend to it, and the image applies no network policy of its own:
a document's sub-resources are fetched at render time unless you pass `--no-network`.
Invocation, digest pinning and the rest: [docs/INSTALL.md](docs/INSTALL.md).

## Usage

```bash
pageplate page.html out.png 1280 720 2
pageplate --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide'
pageplate --clip assets/demo-deck.html slides/ 1280 720 2 'section.slide' --per-element
```

`page.html` is your own file; the `--clip` recipes use the deck this repo ships and run from a fresh clone.

The viewport is CSS pixels and the trailing number is the scale: `1600 900 2` writes 3200×1800, the size of
`assets/hero.png`, and `1280 640 2` writes 2560×1280, the shape of `assets/og.png`.
The canvas is the viewport, so a 770-px card at a 640-px viewport loses its bottom 130 px, at exit 0.
Add `--full` when the height is unknown.
An output directory is single-writer, so two `--clip` runs into one interleave frames and both exit 0.
Every flag and stream: [docs/USAGE.md](docs/USAGE.md).
`--json` writes one object per PNG on stdout, and its schema is stable.

## Performance

At least 31× less in the least favourable mode, 50× in the advertised one: peak RSS of the whole process
tree over an 8-slide deck, against headless Chromium at the same canvas.
The advertised mode is `--per-element`, one PNG per matched element at its own size.
In `--full` and `--clip` memory follows a fixed row budget, not the canvas: one band at a time.
No daemon and no queue: each render is one process that exits, so N parallel renders multiply the peak by N.
Receipt, method and run history: [GATES.md](GATES.md#cost).

## Fidelity against Chromium

At the same viewport and scale, an 8-slide deck rendered by both differs on **4.7102 % of pixels by more than
16 levels** against Chromium, over every pixel with no sampling.
The reference is the runner image's Chrome, which is not pinned, so a runner refresh moves the baseline;
how to re-run it is in [bench/README.md](bench/README.md).

## Scope

Beta: flags and output names can move between minor versions.
No JavaScript, so a document renders as the HTML that arrived.
PNG only, no PDF.
The engine inside it, Blitz, is not Chromium: an unknown property is dropped with no error.
Defects and gaps are tracked as [open limitations](https://github.com/leshchenko1979/pageplate/issues?q=is%3Aissue+is%3Aopen+label%3Alimitation),
and that list is authoritative and current.

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

A minimal HTML repro in a bug report is the most useful thing to send; [CONTRIBUTING.md](CONTRIBUTING.md)
has the rules a change must pass and the build.

## Licence

Apache-2.0 with an explicit patent grant, in [LICENSE](LICENSE).
