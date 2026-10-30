# syntax=docker/dockerfile:1.7
# dine-mcp: source on top of dine-mcp-deps (Dockerfile.deps). The release workflow builds
# both stages as one chain.
ARG DINE_MCP_DEPS_IMAGE=dine-mcp-deps:latest
FROM ${DINE_MCP_DEPS_IMAGE}

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --compile-bytecode --no-deps . \
    && rm -rf /app/src

# /state (venues, budgets, breakers, the roster, Google's consent cookies) is the only writable path; run the
# container with a read-only root and a /state volume or tmpfs owned by uid 1000.
RUN mkdir -p /state && chown 1000:1000 /state

ARG VERSION=0.2.3
ARG VCS_REF=unknown
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

