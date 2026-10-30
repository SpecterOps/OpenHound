# Bundled OpenHound + AWS beta images

Use **Push Bundled OpenHound + AWS Beta Image to ECR** to publish a bundled
OpenHound/AWS beta collector image. The workflow runs manually and uses the
configured GitHub App to access `SpecterOps/openhound-aws`.

## Run in GitHub Actions

1. Select **Push Bundled OpenHound + AWS Beta Image to ECR**, then **Run workflow**.
2. Under **Use workflow from**, select the OpenHound branch to build: `main` or
   the feature branch containing the changes you want. This selects both the
   OpenHound source and the bundle build tooling; the branch must include this
   version of the workflow.
3. Set **`tag`** to a new development version such as `0.5.0.dev9346005`, without
   `v` or `ecr-enterprise`. Coordinate versions with other publishers to the shared
   ECR repository.
4. Set **`aws_commit`** to the full **40-character SHA** of the AWS extension
   revision to bundle. Copy it from the desired commit in
   [openhound-aws history](https://github.com/SpecterOps/openhound-aws/commits/main/).
   This is required; the build rejects branches, tags, and abbreviated SHAs.
5. Leave **`verify_access_only`** unchecked and run the workflow.

### Beta version convention

Use development versions to distinguish beta builds from stable releases. For
example, `0.5.0.dev9346005` consists of:

- `0.5.0`: the OpenHound base version.
- `.dev`: the Python development-release marker used for these beta builds.
- `9346`: the beta project's tracking identifier.
- `005`: the release counter, starting at `000`; `005` is the sixth beta image
  in this sequence.

Increment the counter for each release and use a new version for each published
build. This is a project naming convention; Python interprets the entire
`9346005` suffix as one development-release number.

The workflow builds the `enterprise` image for AMD64 and ARM64 and publishes:

```text
${ECR_REGISTRY}/${ECR_REPOSITORY}:ecr-enterprise0.5.0.dev9346005
${ECR_REGISTRY}/${ECR_REPOSITORY}:candidate
```

Both tags point to the same multi-architecture image. Each successful publish
moves `candidate` to the newly published beta image. The version tag remains
available for that build.
The ECR repository must allow the `candidate` tag to be overwritten.

Publishing does not update a running deployment. See the deployment guidance
below.

To check GitHub App access without building or publishing, enable
**`verify_access_only`**. Supply the required `tag` and `aws_commit` inputs; this
mode only checks out the requested AWS revision.

## Run with the GitHub CLI

Set `AWS_COMMIT_SHA` to the desired full AWS source commit SHA, then run:

```sh
gh workflow run build-ecr-aws-beta-manual.yaml --ref main \
  -f tag=0.5.0.dev9346005 -f aws_commit="$AWS_COMMIT_SHA"
```

Replace `main` with the OpenHound branch to build. Add
`-f verify_access_only=true` for an access-only check. To include newer AWS
extension changes, run again with the desired SHA and a new development version.

Bundle preparation updates the AWS extension's OpenHound requirements to the
selected OpenHound build version, regardless of the original version pins. It
also pins the extension's boto3 requirements to the SDK version already installed
by OpenHound's frozen dependency sync, regardless of the original AWS bounds.
The helper reads the installed version rather than hard-coding an SDK version.
Other requirements are preserved. Dependency resolution and `uv pip check` still
run before publication; unresolved conflicts fail the build.

## Deployment guidance

> [!WARNING]
> The linked guide includes examples specific to BHE. For OpenHound collectors,
> always explicitly pass `-p bhe-collector` to Commute commands. Verify the product
> and destination in the confirmation prompt before approving a deployment.

For managed deployments, authorized operators should follow the internal
[Commute documentation](https://specterops.atlassian.net/wiki/spaces/BE/pages/356679809/Commute)
and the infrastructure team's current collector release guidance for setup,
target selection, promotion, deployment, and rollback procedures.
