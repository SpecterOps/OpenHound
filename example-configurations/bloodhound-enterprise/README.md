## Quick-start

   1. Copy `.dlt-example` to `${HOME}/.dlt`. From the repository root, run `cd example-configurations/bloodhound-enterprise` before the Compose commands below so the configured build context can access the root `Dockerfile`.
   2. Fill in your credentials in the toml files.
   3. Place any required key files (github.pem, okta.json) in `${HOME}/.dlt`.
   4. Pull image from SpecterOps Docker Hub:  `docker pull specterops/openhound:latest-enterprise`
      or run to pull from docker-compose.yml: `docker compose pull`
   5. Run all collectors:    `docker compose up -d`
      or run a single one:   `docker compose up -d scheduler-jamf`

Full configuration reference: https://bloodhound.specterops.io/openhound/enterprise

## Saved runs inside the container

Images built with saved-run support default to `/var/lib/openhound/runs`, writable
by the image's non-root user (UID/GID `65532`). No data volume is required. Runs
survive a stop/start or restart of the same container, but are deleted when that
container is removed or replaced. A normal image update using Compose can replace
the container, so copy needed data first or enable the persistent storage below.

The image sets `OPENHOUND_DEFAULT_RUNS_DIRECTORY` as a fallback. An explicit
`[openhound].runs_directory` in TOML overrides it; an explicit
`OPENHOUND__RUNS_DIRECTORY` environment variable takes precedence over TOML.
Ensure any chosen directory is writable and mounted on persistent storage when
needed. Setting `OPENHOUND__RUNS_DIRECTORY` to an empty string disables saved
runs, including the image fallback. Local Python development keeps its existing
opt-in `runs_directory` setting.

Inspect and recover a finished run, then create a package for manual upload:

```sh
docker compose exec scheduler-okta python -m openhound runs inspect /var/lib/openhound/runs/<run-id>
docker compose exec scheduler-okta python -m openhound runs resume /var/lib/openhound/runs/<run-id> --workers 1
docker compose exec scheduler-okta python -m openhound runs package /var/lib/openhound/runs/<run-id> --output /var/lib/openhound/recovered-okta.zip
docker compose cp scheduler-okta:/var/lib/openhound/recovered-okta.zip ./recovered-okta.zip
docker compose cp scheduler-okta:/var/lib/openhound/recovered-okta.zip.manifest.json ./recovered-okta.zip.manifest.json
```

Upload only the ZIP through BloodHound's file upload workflow. The manifest is a
local audit record. To preserve a complete workspace before replacing a container,
copy `/var/lib/openhound/runs/<run-id>` instead. Copy after the run is idle; do not
copy or alter pipeline files while collection/recovery is writing them.

## Optional persistent storage

To keep saved runs and packages across container removal or replacement, add the
optional Compose file on every invocation:

```sh
docker compose -f docker-compose.yml -f docker-compose.persist.yml up -d scheduler-okta
docker compose -f docker-compose.yml -f docker-compose.persist.yml logs -f scheduler-okta
```

It mounts a separate named volume at `/var/lib/openhound` for each collector. The
image prepares that directory's ownership; a new empty Docker volume inherits
the writable directory contents and ownership. Existing volumes with different
permissions may need ownership corrected to UID/GID `65532` before starting.
Keep using the same Compose project name and file pair to reuse the volumes.
Normal `down` preserves named volumes; `down --volumes` deletes them and their data.

Choose the storage mode before starting a collection. Adding a volume later hides
existing files at that path in the previous container; Compose replacement does
not migrate those files. Copy any existing workspaces/packages out first.

A host bind mount, such as `./collector-data/okta:/var/lib/openhound`, can replace a
named volume if direct host access is preferred. Prepare the host directory for
UID/GID `65532`; a bind mount hides the image directory's ownership and does not
inherit it. Saved run directories and packages have restrictive permissions, so
`docker compose cp` is also useful with mounted storage.

## WARNING: 
 All config and secret files referenced below MUST exist before running
 `docker compose up`. If they are missing, Docker will create them as directories,
 which will cause the collector to fail.

Cloud collection beta examples for internal use are in
[cloud-collection-beta-examples/](cloud-collection-beta-examples/).
