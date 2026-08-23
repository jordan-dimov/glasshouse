# Multi-stage build: a fetch stage installs the pinned morpholog release
# binary; the Python runtime copies it in. Web and worker run this same
# image with different commands.
#
# The pin (version and checksum) lives in scripts/install-morpholog.sh -
# the single place this image and CI both read it from, so the deployed
# binary is byte-identical to the one the committed client and view
# surface are drift-checked against. It used to be duplicated here and in
# ci.yml, and a re-pin once updated only one of them.
#
# The release is a static musl binary, so this stage needs neither a Rust
# toolchain nor an MSRV to track: a checksummed ~10 MB download replaces
# a multi-minute cargo build on every image build. The install script
# selects the artefact by `uname`, so building this image on an arm64
# host fetches the arm64 musl release rather than failing.
FROM alpine:3.22 AS morpholog-fetch
RUN apk add --no-cache curl
COPY scripts/install-morpholog.sh /tmp/install-morpholog.sh
RUN sh /tmp/install-morpholog.sh /out

FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=morpholog-fetch /out/morpholog /usr/local/bin/morpholog

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

ENV GLASSHOUSE_MORPHOLOG_BIN=/usr/local/bin/morpholog
# The environment is complete at build time, so no `uv run` in a
# container may touch it: a bare one re-syncs on the spot and pulls the
# dev group back in at container start (the nightly reset job was quietly
# downloading ruff and mypy before each run). An absent venv now fails
# loudly instead of being silently rebuilt over the network.
ENV UV_NO_SYNC=1
EXPOSE 8000
# Demo profile: web with background-thread projector. The worker command
# (outbox + projector) is the production-like profile.
CMD ["uv", "run", "uvicorn", "glasshouse.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
