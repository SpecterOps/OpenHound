# First, build the application in the `/app` directory.
# See `Dockerfile` for details.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

ARG OPENHOUND_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${OPENHOUND_VERSION}

ENV UV_INDEX='ARTIFACTS=https://pkgs.dev.azure.com/SpecterDev/openhound/_packaging/private/pypi/simple/'
ENV UV_INDEX_ARTIFACTS_USERNAME='dummy'

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images.
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=ARTIFACT_TOKEN \
    export UV_INDEX_ARTIFACTS_PASSWORD="$(cat /run/secrets/ARTIFACT_TOKEN)" && \
    uv sync --extra all --frozen


FROM gcr.io/distroless/python3:nonroot AS base

# Set the python path + additional Python runtime config
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src:/app/.venv/lib/python3.13/site-packages"

COPY --from=builder --chown=nonroot:nonroot /app /app
WORKDIR /app

# Create an image that exposes the CLI as default
FROM base AS cli

ENTRYPOINT ["python", "-m", "openhound"]
CMD ["--help"]

# Create an image that runs the scheduler by default
FROM base AS enterprise

ENTRYPOINT ["python", "src/scheduler.py"]
