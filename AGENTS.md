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
