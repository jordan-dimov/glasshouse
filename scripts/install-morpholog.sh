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
# silently: bump VERSION and SHA256 together, regenerate the client and
# the view surface, and let the drift gate prove they agree.
#
# The release channel replaces a from-source cargo build. The published
# artefact is a static musl x86_64 binary, so there is no toolchain, no
# MSRV to track and no build cache to warm - and the checksum, not a
# mutable git tag, is what makes the pin immutable.
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

VERSION=v0.0.3
SHA256=2ee5e62ba57347b2b38593c6946b4c284d32726183fae1dccff91eab733c51e5

dest=${1:?usage: install-morpholog.sh <dest-dir> [main-latest]}
channel=${2:-pinned}

case "$channel" in
pinned) tag=$VERSION label=$VERSION ;;
main-latest) tag=main-latest label=main ;;
*)
    echo "unknown channel: $channel (expected 'main-latest' or nothing)" >&2
    exit 2
    ;;
esac

tarball="morpholog-${label}-x86_64-unknown-linux-musl.tar.gz"
base="https://github.com/jordan-dimov/morpholog/releases/download/${tag}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

curl -fsSL -o "$tarball" "${base}/${tarball}"

if [ "$channel" = pinned ]; then
    echo "${SHA256}  ${tarball}" > expected.sha256
else
    curl -fsSL -o expected.sha256 "${base}/${tarball}.sha256"
fi
sha256sum -c expected.sha256

tar xzf "$tarball"
mkdir -p "$dest"
install -m 0755 "morpholog-${label}-x86_64-unknown-linux-musl/morpholog" "${dest}/morpholog"
"${dest}/morpholog" --version
