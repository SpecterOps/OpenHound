# Logging

OpenHound provides several logging configurations based on the deployment method and enables automatic file rotation.

## Logging modes
Based on the detected runtime OpenHound will automatically apply custom logging handlers and formats. The following modes are currently available.

### Container
The container mode is used when running OpenHound in a container or Kubernetes environment. This is detected by the LOG_CONTAINER  (manually set) environment variable and/or KUBERNETES_SERVICE_HOST environment variable. All logs will be formatted in JSON and send to stdout for easy parsing by container orchestrators/log shippers with container support. Logs will not be written to disk.

### CLI
The CLI mode is used when running OpenHound in a terminal, ie. when TTY detected. OpenHound will automatically output errors to stderr using Rich-formatted logs with colors and enhanced tracebacks for better readability. Additionally, a JSON-formatted log will be written to `openhound.log`. Both handlers will be used when running in CLI mode.

### Service
TODO: The service mode is used when running OpenHound as a service using the `openhound start service` command. A JSON-formatted log will be written to `openhound.log`.

## Log rotation
OpenHound implements both **time-based** and **size-based** log rotation. When a log is rotated, the date/time will be appended to the file name, ex. `openhound.log.2026-02-19_00` or `openhound.log.2026-02-19`. When rotation occurs based on the file size and happens within the same period/interval, the minutes and seconds are also added for uniqueness ex. `openhound.log.2026-02-19_00-15-23`.


## Configuration
Set logging parameters in `.dlt/config.toml`:

```toml
[runtime]
log_level = "INFO"  # Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# The time based rotation settings
log_rotate_when = "midnight"  # S for seconds, H for hours, D for days and 'midnight' for rotating at midnight
log_interval = 1              # Rotate every X unit of seconds, hours, days etc. Ignored when rotate_when is 'midnight'

# The size based rotation settings
log_max_bytes = 10485760  # ex 10_485_760 for 10MB, 0 means rotate by time only
log_backup_count = 14       # the amount of files to keep before deleting the oldest
```

Or set the configuration via environment variables:

```bash
export RUNTIME__LOG_LEVEL="INFO"
export RUNTIME__LOG_MAX_BYTES="10485760"
export RUNTIME__LOG_BACKUP_COUNT="14"
export RUNTIME__LOG_ROTATE_WHEN="midnight"
export RUNTIME__LOG_INTERVAL="1"
```
