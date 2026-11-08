# syntax=docker/dockerfile:1.7
# Native builds cache the locked dependency stage by its content in the registry.
# The retained two-image publisher may still supply its digest-pinned dependency image.
ARG DINE_MCP_DEPS_IMAGE=runtime-deps
FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv

FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS runtime-deps

COPY runtime-installer.py /tmp/runtime-installer.py
RUN python /tmp/runtime-installer.py && rm /tmp/runtime-installer.py

COPY --from=uv /uv /usr/local/bin/uv

# The committed hash lock makes rebuilds independent of the package index's state.
COPY requirements-runtime.lock /tmp/requirements-runtime.lock
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --compile-bytecode --require-hashes -r /tmp/requirements-runtime.lock \
    && rm -f /tmp/requirements-runtime.lock \
    && python -c 'import httpx, fastmcp, tzdata, maps_mcp; assert fastmcp.__version__ == "4.1.4"'

FROM ${DINE_MCP_DEPS_IMAGE}

WORKDIR /app
ARG VERSION
ARG VCS_REF
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY scripts/embed-build-identity.py /tmp/embed-build-identity.py
RUN --mount=type=cache,target=/root/.cache/uv \
    python /tmp/embed-build-identity.py "$VERSION" "$VCS_REF" \
    && uv pip install --system --compile-bytecode --no-deps . \
    && rm -rf /app/src /tmp/embed-build-identity.py

# /state (venues, budgets, breakers, the roster, Google's consent cookies) is the only writable path; run the
# container with a read-only root and a /state volume or tmpfs owned by uid 1000.
RUN mkdir -p /state && chown 1000:1000 /state

ARG BUILD_DATE=unknown
ARG SOURCE_URL=unknown
LABEL org.opencontainers.image.title="dine-mcp" \
      org.opencontainers.image.description="Restaurant table availability across booking systems, as an MCP server" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONUNBUFFERED=1 \
    DINE_HOST=0.0.0.0 \
    DINE_STATE_DIR=/state
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s CMD ["dine-mcp", "health"]
ENTRYPOINT ["dine-mcp"]

