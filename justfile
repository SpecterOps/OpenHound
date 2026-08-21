set dotenv-load := true

collect +args:
    @echo "Collecting data"
    uv run openhound collect {{args}}

preprocess +args:
    @echo "Collecting data"
    uv run openhound preprocess {{args}}

convert +args:
    @echo "Converting data"
    uv run openhound convert {{args}}

lock:
    @echo "Locking dependencies"
    uv lock

sync:
    @echo "Syncing dependencies"
    uv sync --group dev --extra all

lint:
    @echo "Checking code style"
    ruff check .

typecheck:
    @echo "Running type checks"
    uv run mypy src/openhound

dashboard:
    @echo "Starting marimo openhound dashboard"
    marimo edit notebooks/explore.py --watch

# Docker commands for the Enterprise example configuration.
# Examples: `just oh`, `just oh up github`, `just oh down github`, `just oh down`
oh action='up' collector='':
    #!/usr/bin/env bash
    set -euo pipefail

    compose=(
        docker compose
        -f ./example-configurations/bloodhound-enterprise/docker-compose.yml
    )

    case "{{action}}" in
        up)
            if [[ -n "{{collector}}" ]]; then
                "${compose[@]}" up -d --build --force-recreate "scheduler-{{collector}}"
            else
                "${compose[@]}" up -d --build --force-recreate
            fi
            ;;
        down)
            if [[ -n "{{collector}}" ]]; then
                "${compose[@]}" down "scheduler-{{collector}}"
            else
                "${compose[@]}" down
            fi
            ;;
        *)
            >&2 echo "Usage: just oh [up|down] [github|jamf|okta]"
            exit 1
            ;;
    esac

# Follow the most recent logs for one collector, e.g. `just oh-logs github`.
oh-logs collector:
    docker compose \
        -f ./example-configurations/bloodhound-enterprise/docker-compose.yml \
        logs --follow --tail=100 "scheduler-{{collector}}"
