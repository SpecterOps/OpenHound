from pathlib import Path

import openhound.core.logging  # noqa: F401
from openhound.cli.collect import collect
from openhound.cli.convert import convert
from openhound.cli.create import create_app
from openhound.cli.override import TyperOverride
from openhound.cli.preproc import preprocess
from openhound.cli.privilege_zone import privilege_zone
from openhound.cli.saved_search import saved_searches

BASE_SOUCE_PATH = Path(__file__).parent / "sources"

app = TyperOverride(sources_path=BASE_SOUCE_PATH, pretty_exceptions_enable=True)

app.add_typer(collect, name="collect")
app.add_typer(convert, name="convert")
app.add_typer(preprocess, name="preprocess")
app.add_typer(create_app, name="create")
app.add_typer(saved_searches, name="searches")
app.add_typer(privilege_zone, name="rules")

if __name__ == "__main__":
    app()
