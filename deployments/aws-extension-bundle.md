# Managed AWS integration image

This is an integration-image-only branch:
`feat/BED-9346_Managed-OpenHound-with-aws-extension-bundle`.
Do not merge it into the managed feature branch or `main`.

## Sources and packaging

The Dockerfile fetches `SpecterOps/openhound-aws` at
`b5a5041367fa603c9b159479a3aa327fa3f0059f` during the build. Its Cargo package
version (also the Python distribution version) is `0.1.0`. No sibling checkout
or change to the AWS repository is required.

The temporary extension's `pyproject.toml` receives two compatibility patches:

- Both `openhound==0.2.13` requirements become an exact requirement on the
  **installed local OpenHound package version**, read using `importlib.metadata`.
  This handles arbitrary development versions without deriving compatibility
  from the BloodHound user agent.
- `boto3==1.43.68` becomes `boto3>=1.43.90`, matching the feature branch.
- Other requirements, including boto3-stubs, remain unchanged.

The root `extension.yaml` is also copied into the temporary Python package so
maturin includes it where `CollectorManager` expects to find it. The original
wheel omitted this package resource; no extension runtime code is patched.

OpenHound and its extras are initially installed from `uv.lock`. Their installed
versions constrain normal dependency resolution when installing the local
OpenHound package and extension together. The additional extension packages are
pinned in `deployments/aws-extension-constraints.txt`; all three build/runtime
image references are pinned to multi-architecture digests. Dependencies are
installed normally;
`uv pip check` rejects an inconsistent environment. The extension's checked-in
`Cargo.lock` controls Rust dependencies. Rust 1.96.0 and a C/C++ toolchain build
the native module for each target architecture. The distroless Python runtime
includes libgcc_s and libstdc++.

`OPENHOUND_VERSION` remains the workflow's build argument. For example,
`v0.4.1dev95230003` produces package version `0.4.1.dev95230003` and the existing
BloodHound user agent `openhound/v0.4.1`.
The build normalizes the argument with `packaging.version.Version` before
passing it to hatch-vcs/setuptools-scm, which otherwise preserves the raw
pretend-version string in wheel metadata.
Supply this build argument when building locally as well.

## Prerequisites

- Docker with BuildKit/buildx, access to the image registries, Debian package
  mirrors, PyPI, GitHub, and crates.io.
- Emulation (Docker Desktop or QEMU/binfmt) or native builders for both
  `linux/amd64` and `linux/arm64`.
- A GitHub token with read access to the **internal**
  `SpecterOps/openhound-aws` repository (and organization SSO authorization if
  required). Locally, `gh auth token` can supply an appropriately authorized
  token. The token is mounted as a BuildKit secret, never passed as a build arg.
- For **Build image for ECR**, configure the Actions secret
  `OPENHOUND_AWS_GITHUB_TOKEN` with that access. The checkout's ordinary
  `GITHUB_TOKEN` generally cannot read another internal repository.

## Local build and verification

From this branch's repository root:

```sh
export OPENHOUND_AWS_GITHUB_TOKEN="$(gh auth token)"
docker buildx build --platform linux/arm64 --target enterprise \
  --build-arg OPENHOUND_VERSION=v0.4.1dev95230003 \
  --secret id=OPENHOUND_AWS_GITHUB_TOKEN,env=OPENHOUND_AWS_GITHUB_TOKEN \
  --load -t openhound-aws-bundle:arm64 .
docker run --rm --platform linux/arm64 --entrypoint python \
  openhound-aws-bundle:arm64 scripts/smoke_aws_bundle.py v0.4.1dev95230003

docker buildx build --platform linux/amd64 --target enterprise \
  --build-arg OPENHOUND_VERSION=v0.4.1dev95230003 \
  --secret id=OPENHOUND_AWS_GITHUB_TOKEN,env=OPENHOUND_AWS_GITHUB_TOKEN \
  --load -t openhound-aws-bundle:amd64 .
docker run --rm --platform linux/amd64 --entrypoint python \
  openhound-aws-bundle:amd64 scripts/smoke_aws_bundle.py v0.4.1dev95230003
unset OPENHOUND_AWS_GITHUB_TOKEN
```

The offline smoke script also runs during each image build, in the final
distroless runtime as its nonroot user. It verifies the source location and
package version, AWS entry point, collector-manager discovery/loading, real AWS
source imports, native evaluator import, and BloodHound user agent.

Run existing managed-AWS tests against the installed extension in the image:

```sh
docker run --rm --platform linux/arm64 --entrypoint python \
  openhound-aws-bundle:arm64 -m pytest -p no:cacheprovider \
  tests/test_aws_secrets_manager.py tests/test_managed_configuration.py \
  tests/test_aws_profile_adapter.py \
  tests/test_bhe_version.py tests/test_extensions_format.py -q
```

These checks require no AWS credentials or infrastructure collection.

The scheduling tests use FastAPI/httpx from OpenHound's development dependencies,
which are not installed in the runtime image. Run those in the existing project
development environment:

```sh
.venv/bin/pytest tests/test_bhe_job_scheduling.py -q
```

## Local verification results

Verified with `OPENHOUND_VERSION=v0.4.1dev95230003`:

| Check | linux/arm64 (native) | linux/amd64 (Docker Desktop emulation) |
| --- | --- | --- |
| `enterprise` build and final-image smoke check | Passed | Passed |
| Installed OpenHound | `0.4.1.dev95230003` | `0.4.1.dev95230003` |
| Installed AWS extension | `0.1.0` | `0.1.0` |
| boto3 | `1.43.90` | `1.43.90` |
| BloodHound user agent | `openhound/v0.4.1` | `openhound/v0.4.1` |
| AWS entry point, collector-manager loading, native evaluator | Passed | Passed |
| Image-based tests listed above | 134 tests + 173 subtests passed | 134 tests + 173 subtests passed |

The broader managed-AWS suite also passed on the host development environment
(macOS ARM64, Python 3.14.4): **426 passed** across
`test_aws_secrets_manager.py`, `test_managed_configuration.py`,
`test_aws_profile_adapter.py`, `test_bhe_job_scheduling.py`, and
`test_bhe_version.py`. For this run, `OPENHOUND_AWS_SOURCE` pointed to an isolated
temporary checkout fetched at the pinned commit, rather than a sibling checkout.
The image-based model tests used the installed extension directly.

No unresolved dependency, extension runtime-import, or architecture failures
were found. An initial attempt to run the scheduling tests inside the runtime
image failed because its development-only FastAPI harness is absent; those
tests passed in the development environment. Existing deprecation warnings
were emitted by the host test suite.
