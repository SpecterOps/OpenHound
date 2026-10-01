"""Compatibility launcher for the Docker Enterprise image."""

from openhound.scheduler.startup import main


def start():
    return main()


if __name__ == "__main__":
    raise SystemExit(start())
