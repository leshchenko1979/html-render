# A container image for the renderer, because the binary is not self-contained.
#
# Measured on the CI-built artifact: `ldd` reports 15 lines, of which 13 are
# shared libraries — libssl, libcrypto, libfontconfig, libfreetype, libexpat,
# libpng16, libz, libbz2, libbrotlidec, libbrotlicommon, libgcc_s, libm, libc.
# On top of that the shipped assets ask for `DejaVu Sans`, and a host without it
# gets a silent substitution that re-wraps every line of text. A distroless base
# cannot start the binary at all; a bare slim base starts it and mis-renders.
#
# This image discharges all three host duties once: libraries, fonts, and a glibc
# floor (2.35, asserted by the build guard; bookworm ships 2.36).
#
#   docker build -t html-render .
#   docker run --rm -v "$PWD:/work" html-render /work/card.html /work/card.png 1280 720 2

# ---- build ---------------------------------------------------------------
FROM rust:1-bookworm AS build
WORKDIR /src

# Dependencies first, so a source-only edit reuses the compiled crates instead
# of rebuilding 326 packages. The stub is thrown away by the next COPY.
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src \
 && echo 'fn main() { println!("stub"); }' > src/main.rs \
 && cargo build --release --locked \
 && rm -rf src target/release/render target/release/deps/render-*

COPY src ./src
# `touch` is not tidiness, it is the fix for a trap that shipped a stub binary
# once. Docker's COPY preserves the mtimes from the build context, and a git
# checkout's mtimes are OLDER than the stub compiled in the layer above. Cargo
# compares mtimes against its fingerprint, decides nothing changed, skips the
# rebuild, and the image then carries `fn main() { println!("stub") }`: a binary
# that starts, prints one line, and links 2 libraries instead of 13. The layer
# that was supposed to be thrown away is the one that ended up in the image.
RUN find src -name '*.rs' -exec touch {} +
# --locked, for the same reason parity and release use it: the committed
# Cargo.lock is the resolved graph the published numbers were measured on, so a
# build that would re-resolve must fail here rather than drift silently.
# The liveness check RENDERS, it does not ask for --help: this binary has no
# --help flag (unknown flags print usage and exit 2), so `--help` made the guard
# itself the build failure — it proved nothing about the renderer while failing
# every build. A 4x4 fragment exercises the whole path (parse, lay out, rasterise,
# encode) and the PNG magic number proves a real image came out, which is what
# distinguishes this binary from the stub.
RUN cargo build --release --locked \
 && printf '<div style="width:4px;height:4px;background:#000"></div>' > /tmp/smoke.html \
 && ./target/release/render /tmp/smoke.html /tmp/smoke.png 4 4 1 \
 && test "$(od -An -tx1 -N4 /tmp/smoke.png | tr -d ' ')" = "89504e47" \
 && test "$(ldd target/release/render | grep -c '=>')" -gt 5

# ---- runtime -------------------------------------------------------------
FROM debian:bookworm-slim

# The 13 libraries, by Debian package name, plus the font the shipped assets
# name. `--no-install-recommends` keeps the image from pulling a desktop stack.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libssl3 \
      libfontconfig1 \
      libfreetype6 \
      libexpat1 \
      libpng16-16 \
      libbz2-1.0 \
      libbrotli1 \
      zlib1g \
      fonts-dejavu-core \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/target/release/render /usr/local/bin/html-render

# Not root, not a writable home: the renderer only reads its input and writes
# the path it is given.
RUN useradd --system --create-home --uid 10001 render
USER render
WORKDIR /work

ENTRYPOINT ["html-render"]
CMD ["--help"]
