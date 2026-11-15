# bench/results.json — the receipt behind the numbers on the front page

`results.json` is the **raw output** of one CI run of `tools/bench.py`. It is not
edited by hand: every figure the README advertises is read from it by
`tools/check_claims.py`, and the guard fails if the prose and this file disagree.

## Provenance

| | |
|---|---|
| Workflow run | [43762042304](https://github.com/leshchenko1979/html-render/actions/runs/43762042304) (`parity`, `4b22e6a`) |
| Artefact | `bench-results`, step *Benchmark against Chromium* |
| Measured | 2026-11-15T21:10:29Z |
| Host | `runnervmy75tu`, Ubuntu 24.04, glibc 2.35, 4 vCPU, 15,989 MiB |
| Probe | `html-render 0.3.0-beta.10`, `sha256 de69ad53…` |
| Browser | Google Chrome 157.0.8103.78 |

The run id, commit, *Measured* instant, host and probe version above are
asserted against this file by `tools/check_claims.py`, so they cannot drift from
the receipt they describe. `tools/bench.py` writes `run.id`, `run.sha` and
`run.ref` into the receipt itself, so this table renders fields the JSON already
holds rather than being the only copy of them.

## Reproduce it

```sh
git clone https://github.com/leshchenko1979/html-render
cd html-render
cargo build --release          # or download a release tarball
python3 -m pip install -r tools/requirements.txt
python3 tools/bench.py --bin ./target/release/render --repeats 3
```

Chromium must be on `PATH` (or pass `--chrome`) for the comparison half. On a
host where it cannot launch, the probe records `chromium: unavailable` with the
measured reason and still benches the renderer — it never silently skips, and it
never reports success without saying which engines it actually measured.

## How drift is caught

CI runs the probe on every push, but a measurement nobody compares is a
measurement nobody reads. `tools/bench_drift.py` compares the fresh receipt
against this committed one and fails the build when a figure moves past a band
set from the probe's own run-to-run spread — **35 % on wall time, 20 % on
memory, 15 % on fidelity**, each roughly two to three times the largest spread
observed between two CI runs on the same runner image.

Every figure the README prints is covered, which is six ratios across three
shapes: a regression confined to `--clip` or `--per-element` moves four
published numbers and nothing in the `--full` pair, so checking only `--full`
would have reported green. The self-test proves that shape of failure on purpose
(`--self-test`, 15 legs).

A FAIL here is not "this run was slow". It means the published figures now
describe a different program, and either the code regressed or the receipt needs
refreshing in the same commit as the prose that quotes it.

## What the deck is

`tools/bench_deck.py` generates it from seed `20261115`: 8 slides of 1280×720
CSS px, rendered at scale 2, so the document is 1280×5760 CSS px → a
**2560×11520** PNG. Same seed writes the same bytes anywhere, which is why
`deck.html_md5` here equals the md5 on a developer's machine — the fixture is
arithmetic, not a checked-in blob.

That property is what makes a cross-host comparison meaningful: every reader
measures the identical document.

The eight slides also make the argument for the probe: what a deck actually is,
what a browser is actually for, the cost of the process tree, determinism,
figures as code, what this project does not claim, and how to reproduce it.
**No measured figure appears in the deck, deliberately.** Its identity is
recorded in the receipt and the drift check refuses to compare receipts taken on
different documents, so a figure quoted here would have to move on every honest
re-measurement and would make that check fire on its own refresh. The numbers
live in the README, where `tools/check_claims.py` holds them to the receipt; the
deck argues, and points there.

## How the two engines are measured

Identically, deliberately:

- one process each, one tall PNG out;
- wall clock = median of 3 runs, spawn to exit;
- peak memory = the **whole process tree**, sampled every 25 ms
  (`tools/measure_rss.py`), because a browser is a browser plus renderers plus
  helpers, and a per-process maximum understates what the host must hold;
- Chromium is driven with `tools/run_parity.py`'s own flags at the same device
  scale, so both images are the same pixel grid.

## Read the numbers honestly

- **Memory is the claim.** It held inside 0.8 % across five runs of this probe on
  one runner image, so the README states it as a floor: at least 31×, taking the
  least favourable of the three modes.
- **Speed is measured, gated, and not advertised.** The receipt carries wall
  clock for both engines and `tools/bench_drift.py` fails if it moves past 35 %,
  so a regression is caught. The ratio is not published, because across those
  same four runs it ranged 1.42× to 1.60× while html-render's own wall held to
  15.2 % and Chromium's moved 21.7 % — most of the movement was the browser
  having a different day. A number that mostly measures the other program is not
  a property of this one.
- The **memory** ratio is larger than figures published before this probe, for a
  method reason: earlier rows compared html-render against Chromium's *largest
  single process*, which flatters a multi-process browser. Tree RSS is what a
  host has to hold.
- **Fidelity** is a share of pixels off by more than 16/255 against Chromium's
  own screenshot of the same document. It is not a statement about any other
  page, and the divergent pixels are concentrated in a few known layout defects
  (`tools/parity_baseline.json`).
