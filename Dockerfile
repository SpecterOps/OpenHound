# Integration-image branch only: see deployments/aws-extension-bundle.md.
FROM rust:1.96.0-slim-bookworm@sha256:4732ca96fd086cb9be682050c3f0176288eebaac2b80aa2bcefccfaf198e1950 AS rust

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential git \
 && rm -rf /var/lib/apt/lists/*

COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
ENV PATH="/usr/local/cargo/bin:${PATH}"

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

ARG OPENHOUND_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${OPENHOUND_VERSION}

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images.
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra all --frozen --no-install-project \
 && export SETUPTOOLS_SCM_PRETEND_VERSION="$(.venv/bin/python -c 'import os; from packaging.version import Version; print(Version(os.environ["SETUPTOOLS_SCM_PRETEND_VERSION"]))')" \
 && uv sync --extra all --frozen

# Fetch only this immutable source revision; no sibling checkout is required.
# The token is a BuildKit secret, never a build argument or an image layer.
ARG OPENHOUND_AWS_COMMIT=b5a5041367fa603c9b159479a3aa327fa3f0059f
RUN --mount=type=secret,id=OPENHOUND_AWS_GITHUB_TOKEN,required=true \
    .venv/bin/python scripts/prepare_aws_bundle.py "${OPENHOUND_AWS_COMMIT}"
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    export SETUPTOOLS_SCM_PRETEND_VERSION="$(.venv/bin/python -c 'from importlib.metadata import version; print(version("openhound"))')" \
 && uv pip freeze --exclude-editable > /tmp/openhound-constraints.txt \
 && uv pip install --python .venv --prerelease allow \
      --constraint /tmp/openhound-constraints.txt \
      --constraint deployments/aws-extension-constraints.txt "/app[all]" /tmp/openhound-aws \
 && uv pip check --python .venv


FROM gcr.io/distroless/python3:nonroot@sha256:727eccc5d83f11273c954652667b5b5a1c66120bd4cfb884e879ab77dcdf966a AS base

# This base supplies libgcc_s and libstdc++, needed by native dependencies.
# Set the python path + additional Python runtime config
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src:/app/.venv/lib/python3.13/site-packages"

COPY --from=builder --chown=nonroot:nonroot /app /app
WORKDIR /app

# Exercise entry-point discovery and the native module in the actual runtime.
RUN ["python", "scripts/smoke_aws_bundle.py"]

# Create an image that exposes the CLI as default
FROM base AS cli

ENTRYPOINT ["python", "-m", "openhound"]
CMD ["--help"]

# Create an image that runs the scheduler by default
FROM base AS enterprise

ENTRYPOINT ["python", "src/scheduler.py"]
