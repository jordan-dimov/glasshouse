#!/bin/sh
# Install the pinned morpholog binary from an upstream release.
#
# THIS FILE IS THE ONLY PLACE THE SUBSTRATE PIN LIVES. The Docker image,
# CI's integration leg and any developer machine all run this script, so
# the deployed binary, the binary CI drift-checks the generated client
# against, and the one on a laptop are byte-identical by construction.
# (They were not, before: the pin was duplicated in ci.yml and the
# Dockerfile, and a re-pin once updated only the first.)
#
# Re-pin in the same PR that adopts a new upstream surface, never
# silently: bump VERSION and every SHA256_* together, regenerate the
# client and the view surface, and let the drift gate prove they agree.
#
# The release channel replaces a from-source cargo build. The published
# artefacts are static musl binaries for linux and a native build for
# Apple Silicon, each built and smoke-tested on a runner of its own
# architecture, so there is no toolchain, no MSRV to track and no build
# cache to warm - and the checksum, not a mutable git tag, is what makes
# the pin immutable. Each target carries its own checksum: a pin is
# per-artefact, so there is no single "the" hash to record.
#
# THREE TARGETS, ONE PER LINE BELOW. Upstream published linux arm64 and
# Apple Silicon in v0.0.9 (morpholog#249, forced by this script having
# to refuse a developer's own machine by name), so the no-toolchain
# promise now holds wherever Glasshouse is developed. Intel Macs remain
# the source build: GitHub retired the free Intel runner, and upstream
# will not publish a binary no runner of that architecture has executed.
#
# Usage: install-morpholog.sh <dest-dir> [main-latest]
#
# With no channel argument the pinned release below is installed and
# verified against the checksum recorded here. `main-latest` installs
# upstream's rolling prerelease instead and can only verify against the
# checksum published beside it (a weaker guarantee: it proves the
# download intact, not that anyone reviewed what moved). That channel is
# for the substrate canary, which is deliberately never a merge gate.
set -eu

VERSION=v0.0.11
SHA256_LINUX_X86_64=b69eb30f10c29b572ef33beb925ea16a569c4008449b84fdf70104a202dfe8de
SHA256_LINUX_ARM64=e25c0c56477b70607aa1ff168fc41997c09ee3e8a93c1b008093ee1a3415e2af
SHA256_MACOS_ARM64=d291872b94cb1f121f4c1b22487659fa777a00d2d1689a69fe2776a65b0a90cf

dest=${1:?usage: install-morpholog.sh <dest-dir> [main-latest]}
channel=${2:-pinned}

# Resolve the destination BEFORE the working directory moves: everything
# below runs inside a temp directory the exit trap deletes, so a relative
# destination would be installed into it and vanish on the way out.
case "$dest" in
/*) ;;
*) dest="$(pwd)/$dest" ;;
esac

# Select the artefact by machine, and refuse an unpublished platform by
# name rather than download a binary that cannot run here. Target and
# checksum are chosen in the same breath, so a target can never be
# downloaded with no pin of its own to check it against.
case "$(uname -s)/$(uname -m)" in
Linux/x86_64) target=x86_64-unknown-linux-musl expected=$SHA256_LINUX_X86_64 ;;
Linux/aarch64 | Linux/arm64) target=aarch64-unknown-linux-musl expected=$SHA256_LINUX_ARM64 ;;
Darwin/arm64) target=aarch64-apple-darwin expected=$SHA256_MACOS_ARM64 ;;
*)
    echo "no prebuilt morpholog for $(uname -s)/$(uname -m) - upstream publishes" \
        "linux x86_64, linux arm64 and macOS on Apple Silicon; build from source" \
        "and point GLASSHOUSE_MORPHOLOG_BIN at it" >&2
    exit 2
    ;;
esac

case "$channel" in
pinned) tag=$VERSION label=$VERSION ;;
main-latest) tag=main-latest label=main ;;
*)
    echo "unknown channel: $channel (expected 'main-latest' or nothing)" >&2
    exit 2
    ;;
esac

tarball="morpholog-${label}-${target}.tar.gz"
base="https://github.com/jordan-dimov/morpholog/releases/download/${tag}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

curl -fsSL -o "$tarball" "${base}/${tarball}"

if [ "$channel" = pinned ]; then
    echo "${expected}  ${tarball}" > expected.sha256
else
    curl -fsSL -o expected.sha256 "${base}/${tarball}.sha256"
fi

# macOS ships shasum, not sha256sum. Choose before checking rather than
# falling back after a failure, so a genuine mismatch reports as one.
if command -v sha256sum > /dev/null 2>&1; then
    sha256sum -c expected.sha256
else
    shasum -a 256 -c expected.sha256
fi

tar xzf "$tarball"
mkdir -p "$dest"
install -m 0755 "morpholog-${label}-${target}/morpholog" "${dest}/morpholog"
"${dest}/morpholog" --version
