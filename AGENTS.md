# AGENTS.md

Instructions for a coding agent working in this repository. Everything here is a
fact about the tree, not a preference.

## Build and test

- **Do not run `cargo`, `rustc` or `clippy` locally.** Local Rust compilation is
  forbidden on the host this project is developed on; CI is the only build and
  test gate. Dispatch `.github/workflows/parity.yml` (or push) and read the run's
  conclusion from the API — never infer it from an exit code.
- `tools/` is Python with no third-party imports except an optional Pillow.
  `python3 tools/check_claims.py` runs offline and needs no binary.

## Files that are generated — do not hand-edit

| File | Produced by | Guarded by |
|---|---|---|
| `assets/hero.png` | `html-render assets/hero.html hero.png 1600 900 2 --document` | `check_claims.py` re-renders it and compares bytes; README states its real 3200×1800 |
| `assets/og.png` | `html-render assets/og.html og.png 1280 640 2 --document` | same |
| `Cargo.lock` | resolved by `lockfile.yml`, committed back when it moves | that workflow |

## Prose numbers

`README.md`, `GATES.md` and `.github/workflows/release.yml` quote figures derived
from the constants at the top of `tools/check_claims.py`. After touching any of
them:

```bash
python3 tools/check_claims.py            # every derived figure must still match
python3 tools/check_claims.py --selftest # the spelled-count guard must fire
```

`claim()` deliberately takes no "expected" argument — a hand-typed expectation is
a second copy of the prose and agrees with it whether or not either is right. If
a measurement changes, change the constant and let the guard tell you which
sentences went stale.

**State each fact once.** The guard asserts that a computed value appears
*somewhere*, so a second sentence about the same fact can contradict the first
while every claim stays green. That is not hypothetical: the README said "pins
the four known divergences" while `tools/parity_baseline.json` pinned eleven.

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
