# OpenHound Helm Chart

The example openhound chart deploys a single OpenHound collector as a deployment with a single replica. It is designed
to be deployed multiple times in the same cluster for different collectors, each with its own configuration and secrets.

The chart is intended for scheduled BloodHound Enterprise collection. For ad-hoc collection use the OpenHound CLI or the
docker-compose.yml as part of this repository.

## Prerequisites

- Kubernetes cluster v1.33+
- Helm v4.0+

## Install

Create a `values.yaml` file with your configuration using the included `values.example.yaml` as a reference, then
install the chart using:

```bash
helm install -f values.yml openhound-<name> ./deployments/helm/openhound
```

### Example values

```yaml values.example.yaml
# Example values for the JAMF collector
image:
  repository: docker.io/specterops/openhound
  tag: "0.1.1-enterprise"

# Optional environment variables.
env:
  LOG_CONTAINER: "true"
  RUNTIME__LOG_LEVEL: "INFO"
  EXTRACT__WORKERS: "6"

# The dlt config.toml and secrets.toml stored in a Kubernetes ConfigMap and Secret.
# These should be pre-created and contain the necessary configuration for the collector and destination.
config:
  existingConfigMap: openhound-jamf-config
  existingSecret: openhound-jamf-secrets

# Which collector to run and any additional secret-backed files to mount for that collector.
# eg. a private key PEM for a GitHub app or a JSON credential file for Okta.
collector:
  name: jamf
  extraSecretMounts: { }

# The BHE destination config. This is typically stored inside the same secrets.toml referenced by config.existingSecret, but non-sensitive values can be provided here for convenience.
destination:
  bloodhoundEnterprise:
    url: https://test.bloodhoundenterprise.io
    interval: "300"

```

### Existing ConfigMap / Secret

The chart requires a Kubernetes `ConfigMap` and `Secret` to be pre-created and referenced in `config.existingConfigMap`
and `config.existingSecret`:

```yaml
config:
  existingConfigMap: openhound-jamf-config
  existingSecret: openhound-jamf-secrets
```

The content of the configMap and Secret is mounted as `/app/.dlt/config.toml` and `/app/.dlt/secrets.toml`.

Create the necessary `config.toml` and `secrets.toml` files locally and deploy these resources using `kubectl`:

```bash
# Using jamf as an example collector, but the same pattern applies for any collector
kubectl create configmap openhound-jamf-config \
  --from-file=config.toml=./config.toml

kubectl create secret generic openhound-jamf-secrets \
  --from-file=secrets.toml=./secrets.toml
```

For a list of required and optional configuration settings for each collector refer to the OpenHound documentation
on https://bloodhound.specterops.io/openhound/overview.

### Additional secret files

Collectors may require extra secret files, such as a GitHub app PEM or an Okta JSON credential file.
Use `collector.extraSecretMounts` to mount files from pre-existing Kubernetes `Secret` objects:

```yaml
collector:
  extraSecretMounts:
    github-pem:
      secretName: github-app-credentials
      secretKey: github.pem
      mountPath: /app/.dlt/github.pem
    okta-json:
      secretName: okta-client-secret
      secretKey: okta.json
      mountPath: /app/.dlt/okta.json
```

Each item references an existing `Secret`, the key containing the data/file and the target mount path.

Similarly to the previously created secrets.toml you can deploy any additional secrets using `kubectl`:

```bash
kubectl create secret generic github-app-credentials \
  --from-file=github.pem=./github.pem

kubectl create secret generic okta-client-secret \
  --from-file=okta.json=./okta.json
```
