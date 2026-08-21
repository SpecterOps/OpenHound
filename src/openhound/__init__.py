from importlib.metadata import PackageNotFoundError, version

from openhound.core.dlt_jsonl_batching import ensure_dlt_jsonl_batching

ensure_dlt_jsonl_batching()

try:
    __version__ = version("openhound")
except PackageNotFoundError:
    __version__ = "unknown"
