# Repository guidance

## Managed collector job queue API

Managed collector job queue endpoints must use `/api/v2/collie-job-queue` in
public URL paths. Do not use `collector` in this endpoint path: ad blockers may
block URLs containing that term. This applies to all managed queue operations.
This URL naming requirement does not change internal terminology. Keep names
such as `CollectorJob`, collector-related Python identifiers, job keys, models,
and database concepts unchanged unless a separate requirement calls for it.

## BloodHound API contracts

When adding or changing a BloodHound or BloodHound Enterprise (BHE) API endpoint,
verify its path, HTTP method, parameters, request body, and response against the
applicable API contract. Refer to the [BloodHound repository](https://github.com/SpecterOps/BloodHound)
and [BloodHound Enterprise repository](https://github.com/SpecterOps/bloodhound-enterprise)
for their contracts and implementation details. If an endpoint is being
developed concurrently and is not yet in the published contract, check the
relevant repository's implementation or mock for the agreed behavior and
coordinate any contract changes with that work. Update existing OpenHound API
specifications or endpoint documentation when present; do not invent a new
specification if the repository has none.

## Extension version updates

When updating pinned `openhound-*` collector versions:

- Compare `extension/schema.json` (version and contents) between the old and new
  collector releases.
- If the schema changed, verify that the matching Enterprise schema in
  `SpecterOps/bhe-extensions` includes those changes and has been published.
  Its source is `extensions/<name>/enterprise-schema/bhe-<name>-schema.json`.
- Flag any required `*_EXTENSION_VERSION` updates in both BHE files:
  - `dockerfiles/bhe-deployment.Dockerfile`
  - `tools/docker-compose/api.Dockerfile`

BHE pins must match the published Enterprise artifact's `schema.version`, not
the Python collector version. Community and Enterprise schema versions may
differ; enrichment does not automatically increment the version.

These embedded schema pins are BHE-specific; no corresponding pin update is
needed in the `bhce` submodule. Standalone Community Edition users install the
matching Community schemas separately.
