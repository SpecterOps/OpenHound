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
  tag: "latest-enterprise"

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

# The BHE destination url is non-sensitive config; set it here, via environment variable, or in config.toml.
# Sensitive credentials (token_id and token_key) belong in secrets.toml referenced by config.existingSecret.
destination:
  bloodhoundEnterprise:
    url: https://test.bloodhoundenterprise.io

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

### Persistent saved runs

Kubernetes supports persistent storage independently of a pod's lifetime. Without
a mounted persistent volume, saved runs in the container's writable filesystem
can be lost when Kubernetes replaces the container or pod, including during a
Helm upgrade. Persistence is disabled by default in this chart.

To retain saved runs, create a PersistentVolumeClaim (PVC) in the same namespace
as the Helm release, then reference it in your values. For example, with a default
StorageClass available in the cluster:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: openhound-jamf-runs
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 20Gi
```

Choose storage capacity and a StorageClass appropriate to your cluster. Use a
separate claim for each collector release.

```yaml
persistence:
  enabled: true
  existingClaim: openhound-jamf-runs
  mountPath: /var/lib/openhound/runs
```

The chart mounts the claim read-write and sets `OPENHOUND_DEFAULT_RUNS_DIRECTORY`
to `mountPath`, unless you supplied that fallback in `env` or `collector.env`.
Use an image with saved-run support. Explicit TOML `openhound.runs_directory` and
`OPENHOUND__RUNS_DIRECTORY` settings retain their existing precedence; ensure
they point inside the mounted directory. Only files written under the mount
persist. This includes the complete run workspace and any upload packages you
choose to write there.

With persistence enabled, upgrades use the `Recreate` deployment strategy: the
old pod stops before its replacement starts, avoiding overlapping scheduler pods
and concurrent volume attachment during rollout. This causes collector downtime;
schedule upgrades between active jobs when possible.

The default pod user/group and `fsGroup` are `65532`. Ensure the storage driver
supports these permissions and that existing data is writable by that user.
Kubernetes volumes do not copy the image directory's contents into the volume.
Enabling persistence hides any files previously at the mount path and does not
migrate runs from an old pod; export needed idle workspaces before upgrading.

The chart references the existing PVC without creating or deleting it. Reusing
the same claim preserves its data across upgrades and Helm uninstall/reinstall.
Deleting the claim or backing volume can destroy that data according to the
cluster's storage policy. Persistent storage does not replace backups or run
retention planning.

To validate chart changes locally, with Helm installed:

```sh
helm lint deployments/helm/openhound -f deployments/helm/values.example.yaml --strict
.venv/bin/pytest tests/test_helm_persistence.py -v
```

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
