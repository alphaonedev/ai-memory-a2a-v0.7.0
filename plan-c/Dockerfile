# Plan C — ai-memory daemon container (autonomous tier, all tools loaded)
#
# Multi-stage build:
#   1. Rust builder: clones round-2-fixes (HEAD fda9e64+) and cargo-builds
#      with --features sal-postgres so --store-url postgres:// is available
#   2. Runtime: minimal Debian + binary + ca-certs + libpq
#
# Build context expects: ../../v07/v07-fixes/  OR clone fresh from origin

FROM rust:1.95-bookworm AS builder

ARG REPO_URL=https://github.com/alphaonedev/ai-memory-mcp.git
ARG REPO_REF=round-2-fixes
ARG CARGO_FEATURES=sal-postgres

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      build-essential pkg-config libssl-dev git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ai-memory-src
RUN git clone --depth 50 ${REPO_URL} . && \
    git fetch origin ${REPO_REF} && \
    git checkout ${REPO_REF} && \
    git log --oneline -3

# Build with sal-postgres feature so --store-url + autonomous tier wire up
RUN cargo build --release --features ${CARGO_FEATURES} 2>&1 | tail -10

# Smoke the binary
RUN ./target/release/ai-memory --version

#######################################################################

FROM debian:bookworm-slim AS runtime

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      ca-certificates libssl3 libpq5 curl jq postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Daemon binary
COPY --from=builder /opt/ai-memory-src/target/release/ai-memory /usr/local/bin/ai-memory

# Daemon dirs
RUN mkdir -p /var/lib/ai-memory /var/log/ai-memory/audit /etc/ai-memory \
             /root/.config/ai-memory/keys && \
    chmod 0700 /root/.config/ai-memory/keys

# Identity keypair generated at first start (entrypoint)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 19077
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
