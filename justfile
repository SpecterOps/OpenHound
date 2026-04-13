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
