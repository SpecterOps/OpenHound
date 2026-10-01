"""Foreground BloodHound Enterprise scheduler entry point."""

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

from openhound.scheduler.instance import configure_instance, resolve_instance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the OpenHound BHE scheduler in the foreground"
    )
    parser.add_argument(
        "--instance", default="default", help="Windows instance name (default: default)"
    )
    parser.add_argument(
        "--instance-dir", type=Path, help="Writable instance directory override"
    )
    args = parser.parse_args(argv)
    try:
        paths = resolve_instance(args.instance, args.instance_dir)
        if paths:
            configure_instance(paths)

        import dlt

        from openhound.scheduler.service import Service

        required = {
            "destination.bloodhoundenterprise.url": dlt.config,
            "destination.bloodhoundenterprise.collector_name": dlt.config,
            "destination.bloodhoundenterprise.token_id": dlt.secrets,
            "destination.bloodhoundenterprise.token_key": dlt.secrets,
        }
        values = {}
        for key, provider in required.items():
            try:
                values[key] = provider[key]
            except (KeyError, ValueError, TypeError) as exc:
                location = paths.config if paths else "your DLT configuration directory"
                raise ValueError(
                    f"Missing or invalid {key}; set it in {location}/config.toml or secrets.toml, or via environment variables."
                ) from exc
            if not isinstance(values[key], str) or not values[key].strip():
                raise ValueError(f"Missing or invalid {key}; provide a nonempty value.")

        bhe_url = values["destination.bloodhoundenterprise.url"]
        try:
            parsed_url = urlsplit(bhe_url)
            _ = parsed_url.port
        except ValueError as exc:
            raise ValueError(
                "Invalid destination.bloodhoundenterprise.url; use a full http:// or https:// URL."
            ) from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            raise ValueError(
                "Invalid destination.bloodhoundenterprise.url; use a full http:// or https:// URL without credentials."
            )

        from openhound.core.manager import CollectorManager

        collector_name = values["destination.bloodhoundenterprise.collector_name"]
        available = {
            collector.name
            for collector in CollectorManager.from_entrypoint().collectors
        }
        if collector_name not in available:
            raise ValueError(
                f"Collector '{collector_name}' is not installed. Available collectors: {', '.join(sorted(available)) or '(none)'}."
            )
        logging.getLogger(__name__).info(
            "Initializing service for collector '%s'.", collector_name
        )
        Service(
            bhe_uri=values["destination.bloodhoundenterprise.url"],
            token_id=values["destination.bloodhoundenterprise.token_id"],
            token_key=values["destination.bloodhoundenterprise.token_key"],
            collector_name=collector_name,
            log_base_path=paths.logs if paths else None,
            instance_dir=paths.root if paths else None,
        ).start()
        return 0
    except (OSError, ValueError) as exc:
        print(f"OpenHound scheduler: {exc}", file=sys.stderr)
        return 2
