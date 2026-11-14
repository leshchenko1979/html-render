# Contributing

One binary, one maintainer, no API promise. The useful contribution here is a
measured one: a fixture that exposes a layout defect, a gate that fails on a real
regression, or a prose number that turns out to be wrong.

## Building

The build is a GitHub Actions job, because the host this was written on forbids
local Rust compilation. Five workflows:

| Workflow | Trigger | Output |
|---|---|---|
| `parity.yml` | push touching `src/`, `Cargo.*`, `assets/`, `fixtures/`, `tools/`, `.github/workflows/**` or `.github/dependabot.yml`; or manual | builds, runs the clip, exit-code, redirect, full-page, document-root, CSS-audit, corpus-gate and undefined-name tests, runs the exit-code contract against the **published tarball**, **measures the corpus and fails on a new divergence**, uploads results |
| `drain-probe.yml` | manual dispatch only | builds, measures the image drain loop under each `BLITZ_DRAIN` mode, uploads the report |
| `release.yml` | push a `v*` tag, or manual | builds, packages, publishes, **then verifies the published asset** |
| `container.yml` | push a `v*` tag, a push to `main` touching `src/`, `Cargo.*`, `Dockerfile`, `assets/` or the workflow (publishes the moving `latest` tag), or manual | builds the image, asserts the library closure is complete (`ldd` reports nothing `not found`, plus a floor on the count — a floor, not an exact number: the same source links 12 libraries on bookworm and 13 on jammy) and that the font resolves, renders an asset inside it, pushes to GHCR |
| `lockfile.yml` | push touching `Cargo.toml`, or manual | resolves `Cargo.lock` and commits it back if it moved |

Two hard requirements, both learned the hard way:

- **`runs-on: ubuntu-22.04`, never `ubuntu-latest`.** The `latest` image produced
  a `GLIBC_2.43` binary that would not start on a glibc 2.39 host. The runner is
  pinned, and a guard fails the build if any `GLIBC_*` symbol exceeds 2.35 —
  otherwise a future image bump silently raises the floor again.
- **`libfontconfig1-dev`** is needed to build Stylo.

Locally, where Rust is allowed:

```bash
cargo build --release
python3 tools/run_parity.py --blitz target/release/render
```

## Rules that bite

Each of these exists because the thing it prevents already happened once.

- **`assets/hero.png` and `assets/og.png` are command output, not images.**
  `tools/check_claims.py` re-renders both with the command the README quotes and
  asserts the committed bytes, and the README states their real dimensions.
  Hand-editing either one breaks the only byte-for-byte claim the project makes.

- **After touching any prose number, run `python3 tools/check_claims.py`.** It
  recomputes every derived figure from the inputs at the top of that file and
  fails when the page disagrees. `--selftest` proves the spelled-count guard
  fires; a guard nobody has seen fail is indistinguishable from one that cannot.

- **A number stated twice is a number that drifts.** The guard matches a computed
  value against the text, so a second sentence about the same fact passes while
  the first one is still correct — which is exactly how "pins the four known
  divergences" survived next to a baseline pinning eleven. State the fact once
  and link to it.

- **`--help` is the single source of truth for flags.** `help_text()` lives
  beside the parser in `src/main.rs` so a flag cannot be added to one and
  forgotten in the other, and `tools/test_exit_codes.py` asserts every flag it
  names is accepted. Do not add a flags table to any document.

- **Do not reflow the README.** One clause per line. That is what makes the diff
  reviewable and the 500-line file navigable.

## Where the detail lives

- `GATES.md` — the measured gates: determinism, device scale, element clip, the
  corpus verdict and its legs, fidelity against Chromium, the CSS audit, and what
  each published artefact actually did.
- `SECURITY.md` — the threat model, the measured egress proof, and how to report.
- `CHANGELOG.md` — what changed between releases.
- `AGENTS.md` — the same rules for a coding agent, in the form it reads.
