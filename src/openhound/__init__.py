from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openhound")
except PackageNotFoundError:
    __version__ = "unknown"
