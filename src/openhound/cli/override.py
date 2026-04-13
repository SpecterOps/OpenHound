# Import exceptions to only print the error when using the CLI
# from openhound.core.exceptions import ParseException, ConfigException
from pathlib import Path

import typer

from openhound.core import context
from openhound.core.manager import CollectorManager

# import sys


class TyperOverride(typer.Typer):
    """Overrride exception behaviour when the typer CLI is used. Only print the error and exit with status code 1

    Args:
        typer (typer.Typer): The original typer instance.
    """

    def __init__(self, sources_path: Path, *args, **kwargs):
        self.base_manager = CollectorManager.from_path(sources_path)
        self.addon_manager = CollectorManager.from_entrypoint(group="openhound.sources")
        context.addon_manager = self.addon_manager
        context.base_manager = self.base_manager
        super().__init__(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        """Override typer's call with custom exception"""
        # try:
        super(TyperOverride, self).__call__(*args, **kwargs)

        # except ParseException as err:
        #     sys.stderr.write(str(err))
        #     sys.exit(1)

        # except ConfigException as err:
        #     sys.stderr.write(str(err))
        #     sys.exit(1)
