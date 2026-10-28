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
3. Set **`tag`** to a new development version such as `0.4.1.dev9346005`, without
   `v` or `ecr-enterprise`. Coordinate versions with other publishers to the shared
   ECR repository.
4. Set **`aws_commit`** to the full **40-character SHA** of the AWS extension
   revision to bundle. Copy it from the desired commit in
   [openhound-aws history](https://github.com/SpecterOps/openhound-aws/commits/main/).
   This is required; the build rejects branches, tags, and abbreviated SHAs.
5. Leave **`verify_access_only`** unchecked and run the workflow.

The workflow builds the `enterprise` image for AMD64 and ARM64 and publishes:

```text
${ECR_REGISTRY}/${ECR_REPOSITORY}:ecr-enterprise0.4.1.dev9346005
```

Publishing does not update a running deployment. Update the managed beta
collector deployment through the infrastructure team's existing process.

To check GitHub App access without building or publishing, enable
**`verify_access_only`**. Supply the required `tag` and `aws_commit` inputs; this
mode only checks out the requested AWS revision.

## Run with the GitHub CLI

Set `AWS_COMMIT_SHA` to the desired full AWS source commit SHA, then run:

```sh
gh workflow run build-ecr-aws-beta-manual.yaml --ref main \
  -f tag=0.4.1.dev9346005 -f aws_commit="$AWS_COMMIT_SHA"
```

Replace `main` with the OpenHound branch to build. Add
`-f verify_access_only=true` for an access-only check. To include newer AWS
extension changes, run again with the desired SHA and a new development version.
