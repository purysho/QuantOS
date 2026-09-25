# First Current Quant OS — container image (Apache-2.0).
#
#   docker build -t first-current -f Containerfile .
#   docker compose run --rm quantos setup        # first run
#   docker compose run --rm quantos daily
#   docker compose up terminal                   # http://127.0.0.1:8765/
#
# Behind a TLS-intercepting proxy, pass its CA for the build only (never
# stored in the image):  docker build --secret id=ca,src=/path/ca.pem ...
#
# BASE_IMAGE may point at a mirror of the same official image, e.g.
# mirror.gcr.io/library/python:3.12-slim-trixie or
# public.ecr.aws/docker/library/python:3.12-slim-trixie.
ARG BASE_IMAGE=docker.io/library/python:3.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.17

FROM ${UV_IMAGE} AS uv

FROM ${BASE_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/quantos/venv \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /src
# Dependencies first (cached layer), exactly as locked, with the ORE extra on x86-64.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    extra="$( [ "$(uname -m)" = x86_64 ] && echo --extra ore )"; \
    uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3 $extra
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    extra="$( [ "$(uname -m)" = x86_64 ] && echo --extra ore )"; \
    uv sync --locked --no-dev --no-editable --python /usr/local/bin/python3 $extra
# Offline terminal: Perspective, integrity-verified against the frozen npm hashes.
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export REQUESTS_CA_BUNDLE=/run/secrets/ca; fi; \
    QUANTOS_VENDOR_DIR=/opt/quantos/vendor QUANTOS_HOME=/tmp/vendor-home \
    /opt/quantos/venv/bin/quantos terminal vendor

FROM ${BASE_IMAGE}
ARG VERSION=0.20.0
LABEL org.opencontainers.image.title="First Current Quant OS" \
      org.opencontainers.image.description="Free, open-source, point-in-time investment research OS (no live-capital path)" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/purysho/First-Current-Quant-OS-prototype"
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin quantos \
 && mkdir -p /quantos && chown quantos:quantos /quantos
COPY --from=build /opt/quantos /opt/quantos
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/first-current-quant-os/
ENV PATH=/opt/quantos/venv/bin:$PATH \
    QUANTOS_HOME=/quantos \
    QUANTOS_VENDOR_DIR=/opt/quantos/vendor \
    QUANTOS_CONTAINER=1 \
    PYTHONUNBUFFERED=1
USER quantos
WORKDIR /quantos
VOLUME ["/quantos"]
EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--", "quantos"]
CMD ["doctor"]
