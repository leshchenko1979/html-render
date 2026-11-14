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
| `container.yml` | tags `v*`, and `main` when the image can move | 326-crate Docker build |
| `release.yml` | tags `v*`, or a dispatched tag | builds from that tag, publishes |
| `lockfile.yml` | `main` when `Cargo.toml` moves | commits `Cargo.lock` back |
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
- Subject and body in **Russian**; the body numbered, each item carrying what was measured.
- A body that quotes a number quotes the command that produced it.
- **The repo config and the history disagree on the author email:** `git config
  user.email` reads `leshchenko@gmail.com` while every commit is authored
  `leshchenko1979@gmail.com`. Pass it explicitly:

```bash
git -c user.name="Alexey Leshchenko" \
    -c user.email="leshchenko1979@gmail.com" \
    commit -m "docs: ..."
```

## Releases

- A `v*` tag fires `release.yml` and `container.yml`; both publish.
- The asset is `html-render_<version>_linux_amd64.tar.gz` — **no `v` prefix** on the
  version — and `parity.yml` asserts the README's install snippet names exactly that file.
- Never retag a published version. Publish a new one; a downloaded asset cannot be recalled.

## Issues

This repository's own tracker is `leshchenko1979/html-render`.
The OpenCrabs fork and factory trackers govern a different project and are not this one.
File a defect with the measurement that exposes it, not with an adjective.

## Where things are

| Path | Holds |
|---|---|
| `README.md` | the first five minutes: what it is, install, usage, defects |
| `GATES.md` | the measurements — determinism, scale, clip, corpus, fidelity, CSS audit, published artefacts |
| `CONTRIBUTING.md` | build requirements, the five workflows, the rules that bite |
| `SECURITY.md` | threat model, measured egress proof, reporting |
| `src/main.rs` | the renderer; `help_text()` is the single source of truth for flags |
| `tools/` | the gates and the instruments that measure them |
| `fixtures/` | 32 documents that paint probes in exact colours so geometry can be read from a PNG |

## Conventions

- **Semantic line breaks** — one clause per line, never reflowed. Diffs stay
  reviewable that way.
- **Do not add a flags table** to any document. `--help` is tested against the
  parser by `tools/test_exit_codes.py`; a third copy would be unguarded.
- **Never soften a negative statement.** The named layout defects, the five
  silent-failure modes and "verify the PNG, not the exit code" are the product.
- **Never invent a number.** A figure that was not produced by a command in the
  same turn does not go in a file.
