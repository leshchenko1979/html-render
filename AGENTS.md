---
name: html-render
description: Project procedure for the html-render repo — CI-only build gate, the Python contract suite, the prose-number guard, and the commit/release conventions. Load before touching README.md, GATES.md, src/, tools/ or any workflow.
---

# AGENTS.md

Instructions for an agent working in this repository.
Everything here is a fact about the tree, not a preference.

## Build and test

- **Do not run `cargo`, `rustc` or `clippy` locally.**
  CI is the only build and test gate.
  Dispatch a workflow or push, then read the run's own conclusion from the API —
  never infer it from an exit code.
- `tools/` is Python with no third-party imports except an optional Pillow.
  `python3 tools/check_claims.py` runs offline and needs no binary.

## The contract suite

Every claim in the prose has a script behind it.
Run the ones your change can move; run all of them before a push touching `src/**`.

| Script | Proves |
|---|---|
| `check_claims.py` | every derived figure in README, GATES and release.yml still matches the constants at the top of the file |
| `check_claims.py --selftest` | the spelled-count guard still fires — 5 cases, one of them the historical defect |
| `test_parity_gate.py` | the parity harness reports what it actually measured |
| `test_exit_codes.py` | the documented exit codes, against a real binary |
| `test_clip_void.py` | `--clip` skips boxless elements and keeps output numbering contiguous |
| `test_bands.py` | `--clip` banding keeps a multi-band deck's measured geometry identical (delta ≤ 1, glyph-edge rounding, and NO seam: a glyph cut by a band boundary reassembles with 0 differing pixels), lifts the total-page ceiling, and never reaches the single-PNG path (leg 1 is a negative control there, not proof about Gate C) |
| `test_document_detect.py` | document-root detection, 6 legs |
| `test_measure_rss.py` | RSS is tree-summed, not one process |
| `test_css_audit.py` | the CSS audit's inert / load-bearing verdicts |
| `test_full_page.py` | `--full` |
| `test_redirect.py` | redirect handling |
| `test_undefined_names.py` | no undefined names across `tools/` |

`python3 tools/run_parity.py` re-renders the 32 fixtures and diffs them against
`tools/parity_baseline.json`.
That baseline is the record of what the corpus currently does — read it there,
do not restate its counts in prose here.
Changing a fixture moves those counts, and the prose that quotes them is guarded,
so the baseline and that prose move in the same commit.

## Workflows — five, and what each one costs

| Workflow | Fires on | Cost |
|---|---|---|
| `parity.yml` | push to any branch touching `src/**`, `Cargo.toml`, `Cargo.lock`, `assets/**`, `fixtures/**`, `tools/**`, `.github/workflows/**`, `.github/dependabot.yml` | a full Rust build |
| `container.yml` | tags `v*`, `main` when the image can move, or a dispatch from `lockfile.yml` | 326-crate Docker build |
| `release.yml` | tags `v*`, or a dispatched tag | builds from that tag, publishes |
| `lockfile.yml` | `main` when `Cargo.toml` moves | commits `Cargo.lock` back, then dispatches the builds that commit cannot start |
| `drain-probe.yml` | **dispatch only** | 60-minute budget, answers one question |

A docs-only commit fires **nothing**.
That is the allowlist working, not a broken trigger — do not widen it to "make CI run".

## Files that are generated — do not hand-edit

| File | Produced by | Guarded by |
|---|---|---|
| `assets/hero.png` | `html-render assets/hero.html hero.png 1600 900 2 --document` | `check_claims.py` re-renders it and compares bytes; README states its real 3200×1800 |
| `assets/og.png` | `html-render assets/og.html og.png 1280 640 2 --document` | same |
| `Cargo.lock` | resolved by `lockfile.yml`, committed back when it moves | that workflow |

## Prose numbers

`README.md`, `GATES.md` and `.github/workflows/release.yml` quote figures derived
from the constants at the top of `tools/check_claims.py`.
After touching any of them:

```bash
python3 tools/check_claims.py            # every derived figure must still match
python3 tools/check_claims.py --selftest # the spelled-count guard must fire
```

`claim()` deliberately takes no "expected" argument — a hand-typed expectation is
a second copy of the prose and agrees with it whether or not either is right.
If a measurement changes, change the constant and let the guard tell you which
sentences went stale.

**State each fact once.** The guard asserts that a computed value appears
*somewhere*, so a second sentence about the same fact can contradict the first
while every claim stays green. That is not hypothetical: the README said "pins
the four known divergences" while `tools/parity_baseline.json` pinned eleven.

## Commits

- Conventional prefix in English: `fix(claims):`, `docs:`, `ci(parity):`, `feat(clip):`.
- Subject and body in **English**; the body numbered, each item carrying what was measured.
  English because this repo is public and `git log` is the only record of *why* a change was
  made: 54 of the 129 commits written under the previous rule are unreadable to the audience
  the README, `GATES.md` and the issues are written for (issue #23). History is not rewritten.
- A body that quotes a number quotes the command that produced it.
- **Author email is `leshchenko@gmail.com`** — the address linked to the GitHub
  account, so those commits read *Verified*. `leshchenko1979@gmail.com` is linked
  to no account (`verification.reason: no_user`) and every commit through
  `96bdfd4` carries it; history is not rewritten, new commits use the linked
  address. The global config already holds it — check `git config user.email`
  before a batch of commits, because a clone inherits whatever the global holds:

```bash
git -c user.name="Alexey Leshchenko" \
    -c user.email="leshchenko@gmail.com" \
    commit -m "docs: ..."
```

## Releases

- A `v*` tag fires `release.yml` and `container.yml`; both publish.
- The asset is `html-render_<version>_linux_amd64.tar.gz` — **no `v` prefix** on the
  version — and `parity.yml` asserts the README's install snippet names exactly that file.
- Never retag a published version. Publish a new one; a downloaded asset cannot be recalled.
- **A release RUNS THE PROBE, then lands its output on every surface that states
  a figure, in one commit.** The probe is `tools/bench.py`: it generates a seeded
  deck, measures both engines on it, and writes `bench/results.json`, which is
  committed **unedited**. Any user can rebuild the same deck and check the same
  numbers; `deck.html_md5` in the receipt is how they know they are measuring the
  same document.
- **No figure is typed by hand anywhere.** `tools/check_claims.py` loads
  `bench/results.json` and reads every number out of README `## Performance`,
  `## Fidelity against Chromium`, `GATES.md`, the release-notes template and both
  asset HTMLs. A sentence quoting a figure the receipt does not contain fails the
  build, so a forgotten surface is caught instead of shipped. When a measurement
  changes, the receipt moves first and the prose follows it.
- **The receipt is the authority, and it must be internally consistent.** It
  carries each measurement twice (the `wall_s` repeats and the `wall_median_s`
  the docs quote); the guard asserts the median is the median of its own repeats
  and that `peak_tree_mib` never exceeds `peak_tree_mib_max`. Both legs exist
  because perturbing `wall_s` alone once left the guard green.
- **A stale receipt fails the build, by design.** `tools/bench_drift.py` compares
  every CI run's fresh receipt against the committed one and fails past 35 % on
  wall time, 20 % on memory or 15 % on fidelity. So a change that makes the
  renderer slower cannot land while the README still advertises the old speed:
  either the code goes back or the receipt and the prose move together in one
  commit. A FAIL there is never "the runner was slow" — the bands are two to
  three times the measured run-to-run spread.
- **A receipt produced on a dev box is not a receipt.** `bench/results.json` must
  come from CI, where Chromium runs; a local run on a host with no usable GPU
  records `chromium: unavailable` and the guard then refuses to back a single
  ratio. Re-run the probe in CI rather than committing a local file.
- **The assets carry the same facts and are regenerated, never edited.** After
  the prose moves: `html-render assets/hero.html hero.png 1600 900 2 --document`
  and the same for `og.html` at `1280 640`. `check_claims.py --bin` re-renders
  both and compares bytes, so a stale PNG fails. The version kickers inside the
  HTMLs are asserted too: they once read `v0.3.0-beta.6` for four releases with
  every gate green, because the guard compared each PNG to its HTML and read no
  figure inside the HTML.

## Surfaces outside the tree

The prose guard reads files. Anything a reader sees that is **not** a file in the
tree is unguarded, and it goes stale silently:

| Surface | Read it with | Set it with |
|---|---|---|
| GitHub repo description | `gh repo view leshchenko1979/html-render --json description` | `gh repo edit leshchenko1979/html-render --description "…"` |
| Repo topics | `gh repo view … --json repositoryTopics` | `gh repo edit --add-topic …` |
| Release notes | the release body | the `release.yml` template |

**After any change to the README's one-liner, its headline figure, or what the
tool claims, re-read the description and update it in the same turn.** Read the
pre-state first: it is a live surface, not a default to overwrite. Quote the old
description in the commit or the report so the change is auditable.

Measured 2026-11-16: the description read *"2.34x faster and 3.12x lighter than
Chromium on the plain --clip recipe"* while the page had retired speed as a claim
and the receipt reads **31.14×** on `--clip`. A retired claim and a figure off by
an order of magnitude, on the most-read surface of the project, with every gate
green. Same failure class as the version kickers that read `v0.3.0-beta.6` for
four releases: the guard checks what it can read, and it cannot read GitHub.

## Issues

This repository's own tracker is `leshchenko1979/html-render`.
The OpenCrabs fork and factory trackers govern a different project and are not this one.
File a defect with the measurement that exposes it, not with an adjective.

## Where things are

| Path | Holds |
|---|---|
| `README.md` | the first five minutes: what it is, install, usage, performance, fidelity, scope, where the detail lives — one screen per section. **It carries no defect list**: defects are issues under the `limitation` label (owner directive 2026-11-16). Budget 220 lines, hard ceiling 235 (a 2026-11-16 rewrite cut it from 282); the budget is a subtraction exercise, not a target to fill |
| `docs/USAGE.md` | the reference: every flag, all six environment variables, the exit-code contract, both machine-readable streams, and the behaviour a caller must plan for. The flags table here is pinned against the parser by `tools/test_exit_codes.py` |
| `GATES.md` | the measurements — determinism, scale, clip, corpus, fidelity, CSS audit, published artefacts |
| `CONTRIBUTING.md` | build requirements, the five workflows, the rules that bite |
| `SECURITY.md` | threat model, measured egress proof, reporting |
| `src/main.rs` | the renderer; `help_text()` prints the flags, `docs/USAGE.md` is the documented reference, and `tools/test_exit_codes.py` pins the two together |
| `tools/` | the gates and the instruments that measure them |
| `fixtures/` | 36 documents that paint probes in exact colours so geometry can be read from a PNG |

## Conventions

- **Semantic line breaks** — one clause per line, never reflowed. Diffs stay
  reviewable that way.
- **One flags table, and it is guarded.** `docs/USAGE.md` is the only document
  that lists flags, environment variables and exit codes; `tools/test_exit_codes.py`
  asserts that every flag the parser accepts is documented there, that the table
  invents none, and that `--help` names nothing it omits. Do not add a second
  copy elsewhere — an unguarded third copy is how `--scroll` stayed invisible.
- **Defects live in issues, not in prose.** A limitation the README used to
  enumerate is filed under the `limitation` label and linked from a single
  pointer, so the page describes what the tool does and the tracker says what is
  wrong with it. File first, then remove the sentence — never the reverse, or a
  known limitation becomes a hidden one.
- **Never soften a negative statement.** A limitation moves to an issue with its
  measurement attached, never out of the record: the layout divergences, the
  silent-failure modes and "verify the PNG, not the exit code" are the product,
  wherever they are written down.
- **Never invent a number.** A figure that was not produced by a command in the
  same turn does not go in a file.
