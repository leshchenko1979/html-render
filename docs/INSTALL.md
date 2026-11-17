# Install reference

## Requirements

Linux x86_64, glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+, or a current Fedora. Anything else wants the container
image below.

A slim Debian or Ubuntu host needs one extra package line:

```bash
apt-get install -y libssl3 libfontconfig1 libfreetype6 libpng16-16 libbrotli1 ca-certificates fonts-dejavu-core
```

## Install from the tarball

Download the tarball and the checksum file, verify the checksum, extract, then install:

```bash
V=v0.5.0
N=pageplate_0.5.0_linux_amd64
BASE="https://github.com/leshchenko1979/pageplate/releases/download/$V"
curl -fsSL -O "$BASE/$N.tar.gz" -O "$BASE/checksums.txt"
sha256sum -c checksums.txt && tar xzf "$N.tar.gz"
install -m755 pageplate /usr/local/bin/ && pageplate --version
```

`install -m755` puts the binary at `/usr/local/bin/pageplate`, and `--version` confirms the installed copy runs.

## Verify provenance

Each release asset is attested by the release workflow's own OIDC identity, with GitHub as issuer, so provenance
is checkable without a key. The checksum catches a damaged download; the attestation names the repository, commit
and workflow behind the build. Both checks need the `gh` CLI.

```bash
N=pageplate_0.5.0_linux_amd64
gh attestation verify "$N.tar.gz" --repo leshchenko1979/pageplate \
    --signer-workflow leshchenko1979/pageplate/.github/workflows/release.yml \
    --source-ref refs/tags/v0.5.0
gh attestation verify checksums.txt --repo leshchenko1979/pageplate \
    --signer-workflow leshchenko1979/pageplate/.github/workflows/release.yml \
    --source-ref refs/tags/v0.5.0
```

`--repo` alone accepts an attestation from *any* workflow in the repository; the two extra flags pin the check to
this workflow and this tag.

The release job attests both assets, publishes them, downloads them back and verifies them with the same pair, so
a release whose downloaded asset does not verify turns the job red. A red run after publication means a live
release needs re-cutting.

## Container image

The same binary is published as an image, for hosts outside the list above or where the host libraries should not
be involved:

```bash
docker run --rm --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
    --memory 1g \
    -v "$PWD:/work" ghcr.io/leshchenko1979/pageplate:v0.5.0 \
    /work/card.html /work/card.png 1280 720 2
```

The image's uid is 10001, so omitting `--user` leaves it unable to write your mounted directory, and it panics
(`101`).

Two tags exist, `v0.5.0` and `latest`. A moving tag lets two builds of one commit disagree, so pin the version, or
the digest when the image must be frozen.

The tarball's attestation does not extend to the image, so verify that one by digest
([#31](https://github.com/leshchenko1979/pageplate/issues/31)).

## Fonts and shared libraries

The binary names seven objects — the loader plus `libc`, `libssl`, `libcrypto`, `libfontconfig`, `libm`,
`libgcc_s` — and pulls freetype, expat, libpng, brotli and zlib through fontconfig.

The closure varies by distro: 12 objects on bookworm and 13 on jammy or noble, the extra one `libbz2.so.1.0`,
which their freetype links and bookworm's does not. The container gate runs `ldd` inside the runtime image and
prints the count, so a drift shows up in a run log rather than in this sentence.

A host with no font package still renders, with a substituted face, but the warning says substitution cannot be
detected there: with nothing visible to compare against, it cannot name the family it wanted. When *some* fonts
are visible and the requested one is not, it names each missing family.

The image carries those libraries and a font, but applies no network policy of its own: a document's
sub-resources are fetched at render time unless you pass `--no-network`, so the container still does not
isolate egress. Both flags are in [USAGE.md](USAGE.md#flags).

The tarball links your host's `libssl.so.3`, so your distro patches it: better TLS patch latency.

## When the image rebuilds

The image is built on a tag push, a `workflow_dispatch`, or a commit touching `src/`, `Cargo.toml`, `Cargo.lock`,
`Dockerfile`, `assets/` or `container.yml`. It has no scheduled rebuild, so pin the digest when base-image patches
matter ([#31](https://github.com/leshchenko1979/pageplate/issues/31)).

## Building from source

See [CONTRIBUTING.md](../CONTRIBUTING.md#building).
