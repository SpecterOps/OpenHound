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

The Dockerfile, constraints, and preparation/smoke scripts come from the selected
**workflow revision**, independently of `source_ref`. This lets
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
`scripts/prepare_aws_bundle.py` and `deployments/aws-extension-constraints.txt`.
The preparation script patches the temporary extension's OpenHound requirement
to the installed local version,
raises its boto3 requirement to `>=1.43.90`, and places `extension.yaml` inside the
Python package. Changed upstream packaging may require updating those patches.

The build runs `uv pip check` and an offline smoke check inside the final nonroot
image on each architecture. These checks verify extension discovery, metadata,
and native imports only when the bundle is manually built. No extra automatic
workflow test suite is added.

When bringing `main` into older beta branches, retain its standard
`build-ecr-manual.yaml` and `Dockerfile`, plus the separate bundle files. Replace
the old temporary in-place bundle modifications. Rebasing is not required just
to make the new workflow appear in Actions.
