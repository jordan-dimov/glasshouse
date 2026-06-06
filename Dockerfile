# Multi-stage build: Rust builder produces the morpholog binary; the
# Python runtime copies it in. Web and worker run this same image with
# different commands.
#
# NOTE (scaffold): the morpholog build stage pins a released ref once
# Morpholog publishes one; until then it builds from main.

FROM rust:1.85-slim AS morpholog-builder
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
ARG MORPHOLOG_REF=main
RUN git clone --depth 1 --branch "${MORPHOLOG_REF}" \
    https://github.com/jordan-dimov/morpholog /src/morpholog
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
