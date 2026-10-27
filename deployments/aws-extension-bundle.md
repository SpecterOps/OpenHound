# Managed OpenHound + AWS beta images (BED-9518)

Use **Push Bundled OpenHound + AWS Beta Image to ECR** to publish updated managed
OpenHound/AWS beta collector images. This workflow is **manual-only**: pushes,
merges, pull requests, and releases do not trigger it.

It uses the same repository-access rules, ECR variables, and AWS publishing
credentials as **Build image for ECR**. Repository writers can run it; it adds no
code-owner restriction, environment approval, or team-membership requirement.

## Required configuration

Keep the existing `ECR_REGISTRY` and `ECR_REPOSITORY` repository variables and
`BHE_AWS_ACCESS_KEY_ID` / `BHE_AWS_SECRET_ACCESS_KEY` secrets. Bundled images go to
the same ECR location as regular OpenHound images.

The additional secret **`OPENHOUND_AWS_GITHUB_TOKEN`** needs **Contents: read** on
the internal `SpecterOps/openhound-aws` repository, with organization approval/SSO
authorization if required. The ordinary workflow `GITHUB_TOKEN` cannot normally
read that repository. The source token is mounted as a BuildKit secret, never
passed as a build argument or saved in an image layer.

## Publish a beta image

Commit references for the workflow inputs:

- **`aws_commit`:** [Recent openhound-aws commits](https://github.com/SpecterOps/openhound-aws/commits/main/).
  Open the desired commit and copy its **full 40-character SHA**, not the
  abbreviated value shown in the history. Repository access is required.
- **`source_ref`:** [Recent OpenHound commits](https://github.com/SpecterOps/OpenHound/commits/main/).
  Use the branch selector to view feature-branch history when needed.

1. In GitHub Actions, select **Push Bundled OpenHound + AWS Beta Image to ECR**.
2. Use the workflow from **`main`**. Set `source_ref` to `main`, or to the beta
   branch/commit containing the managed functionality you want to build.
3. Set `tag` to a development version such as `0.4.1.dev9346005`, without `v` or
   `ecr-enterprise`. Use the existing numbering convention and coordinate versions
   with other publishers to the shared repository.
4. Set **`aws_commit`** to the full 40-character source commit SHA from
   `SpecterOps/openhound-aws` that you want to bundle. This input is required and
   has no default; branches, tags, and abbreviated SHAs are rejected by the build.
5. Run the workflow. It builds the `enterprise` image for AMD64 and ARM64 and
   publishes only this version tag:

   ```text
   ${ECR_REGISTRY}/${ECR_REPOSITORY}:ecr-enterprise0.4.1.dev9346005
   ```

6. Update the managed beta collector deployment through the infrastructure team's
   existing process. Publishing an image does not update a running deployment.

The Dockerfile and preparation/smoke scripts come from the selected **workflow
revision**, independently of `source_ref`. This lets
the workflow on `main` build a feature branch that predates these bundle files.
The AWS source revision is selected separately through `aws_commit`. The selected
OpenHound source still needs to contain the desired managed runtime features.

After the workflow merges to `main`, it can also be started with the GitHub CLI.
Set `AWS_COMMIT_SHA` to the desired full AWS source commit SHA first:

```sh
gh workflow run build-ecr-aws-beta-manual.yaml --ref main \
  -f tag=0.4.1.dev9346005 -f source_ref=main -f aws_commit="$AWS_COMMIT_SHA"
```

## Include newer AWS extension changes

Run the workflow with the desired **`aws_commit`** and a new development version.
Selecting a newer extension revision does not require editing files or merging a
SHA update to `main`. Each build fetches exactly the supplied commit instead of
automatically following the AWS repository's latest commit. Local Docker builds
must likewise pass `--build-arg OPENHOUND_AWS_COMMIT=<full-commit-sha>`.

When selecting a newer commit, consider compatibility with
`scripts/prepare_aws_bundle.py`.
The preparation script patches the temporary extension's OpenHound requirement
to the installed local version, raises its boto3 requirement to `>=1.43.90`, and
places `extension.yaml` inside the Python package. Changed upstream packaging may
require updating those patches.

The build preserves OpenHound's installed, locked dependencies while resolving
the extension's declared requirements. It runs `uv pip check` and an offline smoke
check inside the final nonroot image on each architecture. These checks verify
extension discovery, metadata, and native imports only when the bundle is
manually built. No extra automatic workflow test suite is added.

When bringing `main` into older beta branches, retain its standard
`build-ecr-manual.yaml` and `Dockerfile`, plus the separate bundle files. Replace
the old temporary in-place bundle modifications. Rebasing is not required just
to make the new workflow appear in Actions.

## Local managed profile and collection check

OpenHound transports nested DLT configuration and bridges BHE's raw keys to an
isolated generated boto3 profile. It does not validate AWS field names, key
formats, role syntax, or organization combinations. See
[configuration resolution and worker logging](managed-aws-role-profiles.md) for
the accepted JSON layout, generated-profile injection, role-field translation,
and the current extension's union fallback and ignored-field behavior.

For initial collection-role assumption using job `params`, see
[Managed AWS role profiles](managed-aws-role-profiles.md). Those checks also mount
the updated adapter when using an image built before role-profile support.

Use `scripts/check_managed_aws.py` to supply an access-key pair instead of an
existing AWS profile. This works with the already-built image: mount the script
from this checkout, so an image rebuild is unnecessary.

The harness exercises this path:

1. Create a local claimed-job model containing the Access Key ID and secret name.
2. Mock Secrets Manager's SDK response with BHE's `SecretBinary` payload shape.
3. Run the real secret parser and `prepare_aws_credentials` adapter.
4. Call the real `_subprocess_collect` worker entry point, which generates the
   temporary profile and injects its name into job-scoped DLT configuration.
5. Observe the real AWS extension's `AWSAuth` constructor, without replacing its
   implementation. Observe the resolved model. When DLT selects the generated
   profile, verify boto3 used the shared-credentials-file provider, the key pair
   matches, and files are mode 0600.
6. Remove the generated profile workspace after the run.

The script runs one worker directly in the container process. Queue claim/start,
heartbeats, BHE uploads, and multiprocessing log forwarding are outside this
direct-call check. Collected OpenGraph JSON is saved locally; the real scheduler
routes worker logs through its parent-owned listener and configured destinations.

### 1. Offline profile check (about 5–15 seconds)

From this repository root, run:

```sh
docker run --rm --network none --platform linux/arm64 --entrypoint python \
  --mount "type=bind,src=$PWD/src,dst=/app/src,readonly" \
  --mount "type=bind,src=$PWD/scripts/check_managed_aws.py,dst=/app/check_managed_aws.py,readonly" \
  openhound-aws-bundle:arm64 /app/check_managed_aws.py --offline
```

This uses example keys and stubs STS. The actual extension source, DLT resolution,
profile creation, and SDK credential-file loading run with networking disabled.
Expected output includes:

```text
PASS: job secret loaded through the real Secrets Manager payload parser
PASS: AWSProfileCredentials resolved openhound-job-... using shared-credentials-file; supplied pair matches; files are 0600
PASS: extension STS identity is account 123456789012
PASS: temporary AWS profile workspace removed
```

### 2. Authenticate with your generated profile (typically 5–30 seconds)

Create a JSON file outside the checkout, for example
`$HOME/.config/openhound-aws-local/credentials.json`, with your long-lived IAM key
pair (OpenHound transports these strings without AWS format validation):

```json
{
  "access_key_id": "AKIA...",
  "secret_access_key": "<raw Secret Access Key>"
}
```

The JSON is supplied over stdin; its contents are not put in Docker arguments or
environment variables. No existing AWS profile or Secrets Manager permissions
are needed. The script never prints the key pair.

```sh
docker run --rm -i --platform linux/arm64 --entrypoint python \
  --mount "type=bind,src=$PWD/src,dst=/app/src,readonly" \
  --mount "type=bind,src=$PWD/scripts/check_managed_aws.py,dst=/app/check_managed_aws.py,readonly" \
  --mount "type=bind,src=$PWD/deployments/managed-aws-local-params.json,dst=/app/local-params.json,readonly" \
  openhound-aws-bundle:arm64 /app/check_managed_aws.py \
  --auth-only --credentials-file /dev/stdin --params-file /app/local-params.json \
  < "$HOME/.config/openhound-aws-local/credentials.json"
```

Secrets Manager is still mocked, but the extension calls **live STS** with the
generated source profile. Check that the printed account ID is the expected
account. For organization settings, this quick check authenticates the source
identity; the full collection also exercises role assumption and service access.

### 3. Run a real collection

The checked-in parameters collect one account/region. Edit `region_name` as
needed. To collect an organization, add `org_role_name` and your other organization
settings under `sources.source.aws.credentials`. The worker replaces canonical
`profile_name` at runtime. For this generated-profile check, omit `profile_names`:
the current extension may otherwise select multi-profile credentials and leave
the generated profile unused. Such settings are preserved, not rejected.

```sh
mkdir -p output/managed-aws
docker run --rm -i --platform linux/arm64 --entrypoint python \
  --user "$(id -u):$(id -g)" --workdir /tmp -e HOME=/tmp \
  --mount "type=bind,src=$PWD/src,dst=/app/src,readonly" \
  --mount "type=bind,src=$PWD/scripts/check_managed_aws.py,dst=/app/check_managed_aws.py,readonly" \
  --mount "type=bind,src=$PWD/deployments/managed-aws-local-params.json,dst=/app/local-params.json,readonly" \
  --mount "type=bind,src=$PWD/output/managed-aws,dst=/output" \
  openhound-aws-bundle:arm64 /app/check_managed_aws.py \
  --credentials-file /dev/stdin --params-file /app/local-params.json \
  --output-dir /output \
  < "$HOME/.config/openhound-aws-local/credentials.json"
```

Using your host UID makes the output mount writable; `/tmp` supplies writable
working and home directories for intermediate collection/lookup data. Each run
saves JSON under `output/managed-aws/<job UUID>/`. Collection duration depends on
account/organization size and AWS API throttling.

For AMD64, change both `linux/arm64` and `:arm64` to their AMD64 equivalents.

Diagnostic verification completed locally:

- Offline generated-profile check passed in both ARM64 and AMD64 images with
  networking disabled.
- The stdin credential and parameters-file path passed with STS stubbed.
- An accounts-only collection with STS stubbed passed the real collect,
  preprocess, and convert stages and exported an AWS account node on ARM64.
- Existing profile-adapter and Secrets Manager tests: 71 passed.

Live AWS authentication and full infrastructure collection require your key pair
and have not been exercised by these offline checks.
