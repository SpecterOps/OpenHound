## Quick-start

   1. Copy `.dlt-example` to `${HOME}/.dlt`and `docker-compose.yml` to `${HOME}`.
   2. Fill in your credentials in the toml files.
   3. Place any required key files (github.pem, okta.json) in `${HOME}/.dlt`.
   4. Pull image from SpecterOps Docker Hub:  `docker pull specterops/openhound:latest`
      or run to pull from docker-compose.yml: `docker compose pull`
   5. Run all collectors:    `docker compose up -d`
      or run a single one:   `docker compose up -d collect-jamf preprocess-jamf convert-jamf`

 Example docker-compose file for running OpenHound with Jamf, GitHub, and Okta collectors.
 Collector output is written to local bind-mount directories under `./output/<collector>/`.

## WARNING: 
 All config and secret files referenced below MUST exist before running
 `docker compose up`. If they are missing, Docker will create them as directories,
 which will cause the collector to fail.