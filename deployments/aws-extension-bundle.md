# Managed OpenHound + AWS beta images (BED-9518)

Use **Push Bundled OpenHound + AWS Beta Image to ECR** to publish updated managed
OpenHound/AWS beta collector images. This is a **manual-only** workflow: pushes,
merges, pull requests, and releases do not trigger it.

The workflow lives on `main` and defaults to building OpenHound from `main`.
During beta development, it can instead build the one explicitly approved source
branch configured in `MANAGED_AWS_BETA_SOURCE_BRANCH`. It resolves that branch to
an immutable commit before approval. The Dockerfile, AWS source pin, constraints,
and build scripts always come from the workflow's reviewed `main` revision,
independently of the selected source branch. Managed runtime functionality still
needs to exist in the source branch selected for the build.

## Image location and version

Images use the **same ECR registry and repository as regular OpenHound images**,
configured by the existing `ECR_REGISTRY` and `ECR_REPOSITORY` repository variables.
Only the `enterprise` target is published, with exactly one version tag:

```text
590372943691.dkr.ecr.us-east-1.amazonaws.com/bhe/openhound:ecr-enterprise0.4.1.dev9346005
```

Enter `0.4.1.dev9346005` as the workflow's `tag` input, without `v` or
`ecr-enterprise`. Use the team's existing development-version numbering convention
and a **new version for every image**, including AWS-pin-only updates. The
workflow accepts `MAJOR.MINOR.PATCH.devNUMBER` (PEP 440 development versions).
There are no `beta-bundle`, `latest`, or automatic SHA tags.

The workflow rejects an existing ECR tag and serializes its own runs for the same
version. This is a preflight check, not an atomic reservation against other
publishers: coordinate versions with the regular image workflow. ECR tag
immutability, if enabled by infrastructure, provides registry-side enforcement.

Image labels identify the managed AWS beta bundle and its OpenHound, AWS extension,
and build-tooling commits. The run summary records those revisions, the image tag,
and the published digest. Use the digest to identify the exact deployed image.

## Required GitHub setup before the first publish

Repository administrators must configure these settings; adding workflow YAML
alone does not create the approval or ownership protections.

1. Create the **`managed-aws-beta` environment**:
   - Required reviewers: **only `@SpecterOps/openhound`**, matching CODEOWNERS and
     the workflow's membership checks. This team must have write access to the
     repository to be a valid code owner.
   - Enable **Prevent self-review** and disable administrator bypass.
   - Under selected deployment branches/tags, allow **only the `main` branch**;
     do not add a tag rule. This restricts the workflow revision, not its selected
     source checkout.
   - The preflight job verifies these settings and fails if they are absent or
     weakened. Publishing requires a second maintainer's approval.
2. Configure these **environment secrets**:

   | Secret | Required access |
   | --- | --- |
   | `OPENHOUND_BETA_AUTHORIZATION_TOKEN` | Fine-grained token with SpecterOps organization **Members: read**, or an equivalent short-lived GitHub App token. No repository write permission is needed. Used only to verify active `openhound` team membership for both the original initiator and the rerun actor. |
   | `OPENHOUND_AWS_GITHUB_TOKEN` | Fine-grained token restricted to **Contents: read** on `SpecterOps/openhound-aws`, with organization approval/SSO authorization as required. |
   | `BHE_AWS_ACCESS_KEY_ID` / `BHE_AWS_SECRET_ACCESS_KEY` | Credentials authorized to inspect tags and push images to the existing OpenHound ECR repository. Keep their IAM scope limited to the required ECR operations/destination. |

   Missing credentials or membership API errors stop the workflow. The AWS source
   token is passed only as a BuildKit secret, never as a build argument or image
   layer. The ordinary workflow `GITHUB_TOKEN` has only `contents: read`; it does
   not grant cross-repository access to the internal AWS repository.
3. Retain the existing repository-level ECR variables without environment-level
   overrides. If needed, set repository variable `MANAGED_AWS_BETA_SOURCE_BRANCH`
   to the exact approved beta branch, initially `feat/managed-aws-source-role`.
   Leave it unset when only `main` should be built. Protect and review changes to
   any approved source branch; approving a build authorizes that source code.
4. Enable **required code-owner review** in the `main` ruleset. CODEOWNERS covers
   the new workflow, its authorization helper, Dockerfile, pin, and build scripts.
   A CODEOWNERS file by itself requests review but does not require approval.

Users with repository write access can submit a manual run request, but only
active members of the owning team can pass authorization, and a different team
member must approve publishing. All Actions dependencies are pinned to commits,
and checkout credentials are not persisted.

These controls govern this workflow. Existing repository/organization secrets and
other workflows with ECR write access remain separate publishing paths to the
shared repository; audit those before treating this as a repository-wide
owner-only publishing policy. In particular, environment-scoped copies do not
remove existing repository-scoped credentials.

## Publish or update beta collectors

1. Choose the OpenHound source branch. Select `main` once the needed managed work
   is merged; use the configured approved beta branch during the transition.
2. To include newer AWS extension changes, review `SpecterOps/openhound-aws` and
   update **`ARG OPENHOUND_AWS_COMMIT` in `Dockerfile.aws-bundle`** to the desired
   full 40-character commit SHA. This is the authoritative pin; the workflow
   reads it rather than following the extension's `main` automatically.
3. Review `scripts/prepare_aws_bundle.py` and
   `deployments/aws-extension-constraints.txt`. The current preparation patches
   the temporary AWS package's two `openhound==0.2.13` requirements to the installed
   local OpenHound version, raises its boto3 requirement to `>=1.43.90`, and places
   `extension.yaml` inside the Python package. Changed upstream packaging fails
   explicitly and may require updating these compatibility patches/constraints.
4. Verify both target architectures, then merge the pin/build-tooling change to
   `main` with owner review. The workflow's `main` revision supplies these files
   even when building an older feature branch.
5. In GitHub Actions select **Push Bundled OpenHound + AWS Beta Image to ECR**.
   Run the workflow **from `main`**, enter the next development version, and select
   the source branch. Review the resolved source SHAs in the preparation summary
   before another maintainer approves the publishing job.
6. Record the resulting image digest and update the managed beta collector
   deployment through the infrastructure team's existing process. Publishing an
   image does not itself update a running collector deployment.

For example, after the workflow is merged:

```sh
gh workflow run build-ecr-aws-beta-manual.yaml --ref main \
  -f tag=0.4.1.dev9346005 -f source_ref=main
```

After rebasing a beta feature branch on `main`, retain `main`'s standard
`build-ecr-manual.yaml` and `Dockerfile`, plus the new dedicated bundle files.
Remove the old in-place temporary bundle modifications. Rebasing is not required
to make the new Actions entry appear: registration comes from `main`.

## Local build and verification

Use Docker Buildx with native builders or QEMU for `linux/amd64` and `linux/arm64`.
The build needs registry, GitHub, PyPI, Debian mirror, and crates.io access.
From the repository root, with an authorized token available through GitHub CLI:

```sh
export OPENHOUND_AWS_GITHUB_TOKEN="$(gh auth token)"
docker buildx build --platform linux/arm64 --target enterprise \
  --file Dockerfile.aws-bundle --build-context bundle=. \
  --build-arg OPENHOUND_VERSION=0.4.1.dev9346005 \
  --secret id=OPENHOUND_AWS_GITHUB_TOKEN,env=OPENHOUND_AWS_GITHUB_TOKEN \
  --load -t openhound-aws-beta:arm64 .
docker run --rm --network none --platform linux/arm64 --entrypoint python \
  openhound-aws-beta:arm64 scripts/smoke_aws_bundle.py 0.4.1.dev9346005
unset OPENHOUND_AWS_GITHUB_TOKEN
```

Repeat with `linux/amd64` and `:amd64`. For a different OpenHound checkout, keep
`--build-context bundle=.` pointing to the reviewed build-tooling checkout and
replace the final `.` with the source checkout path.

The bundle-specific Docker ignore file excludes local Git metadata, virtual
environments, credentials/config directories, caches, logs, and output. Dependency
installation begins with the source's `uv.lock`, constrains its installed runtime
packages, and ends with `uv pip check`. Rust and runtime bases are digest-pinned.
The final nonroot image runs the offline smoke check on each target architecture:
it validates the selected OpenHound version/source, AWS entry-point discovery,
manifest loading, real AWS imports, and the native evaluator. It does not call
AWS or require collection credentials.

Run publishing-guard and packaging checks locally with:

```sh
node --test tests/workflows/aws-beta.test.cjs
uv run --group dev pytest tests/test_aws_bundle_preparation.py -q
```
