## Quick-start

   1. Copy the contents from `bloodhound-enterprise` folder to `${HOME}`.
   2. Rename `.dlt-example` to `.dlt` and fill in your credentials in the toml files.
   3. Place any required key files (github.pem, okta.json) in `${HOME}/.dlt`.
   4. Pull image from SpecterOps Docker Hub:  `docker pull specterops/openhound:0.1.1-enterprise`
      or run to pull from docker-compose.yml: `docker compose pull`
   5. Run all collectors:    `docker compose up -d`
      or run a single one:   `docker compose up -d scheduler-jamf`

Full configuration reference: https://bloodhound.specterops.io/openhound/enterprise

## WARNING: 
 All config and secret files referenced below MUST exist before running
 `docker compose up`. If they are missing, Docker will create them as directories,
 which will cause the collector to fail.