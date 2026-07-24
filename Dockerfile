# Multi-stage build: Rust builder produces the morpholog binary; the
# Python runtime copies it in. Web and worker run this same image with
# different commands.
#
# MORPHOLOG_REF must match the pin CI validates (.github/workflows/ci.yml:
# the same MORPHOLOG_REF) so the deployed binary is the one the committed
# client and view surface were generated and drift-checked against; a
# moving `main` could ship a binary whose wire has diverged. Re-pin both
# in the same PR. A full SHA is needed because `--branch` also accepts
# tags and `git clone --depth 1` needs an exact ref.

# Rust >= the pinned morpholog's rust-version (1.95, edition 2024); too
# old a toolchain fails the build with an MSRV error. Bump alongside
# MORPHOLOG_REF if a future pin raises it.
FROM rust:1.95-slim AS morpholog-builder
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
ARG MORPHOLOG_REF=35322d9765fbc5e6c50f0c460e8618712bfc80ba
RUN git clone https://github.com/jordan-dimov/morpholog /src/morpholog \
    && git -C /src/morpholog checkout "${MORPHOLOG_REF}"
WORKDIR /src/morpholog
RUN cargo build --release

FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=morpholog-builder /src/morpholog/target/release/morpholog /usr/local/bin/morpholog

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

ENV GLASSHOUSE_MORPHOLOG_BIN=/usr/local/bin/morpholog
EXPOSE 8000
# Demo profile: web with background-thread projector. The worker command
# (outbox + projector) is the production-like profile.
CMD ["uv", "run", "uvicorn", "glasshouse.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
