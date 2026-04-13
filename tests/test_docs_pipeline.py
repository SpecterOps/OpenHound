from pathlib import Path

import pytest

from openhound.docs.pipeline import CustomCollectorDocs


def test_render_class_table_supports_inherited_fields_and_templates_are_static():
    asset = {
        "class": "FakeComputer",
        "path": "openhound_faker.models.computer.FakeComputer",
        "node": {
            "kind": "RAND_Computer",
            "icon": "computer",
            "properties": "openhound_faker.models.computer.ComputerProperties",
        },
        "edges": [],
    }
    docs = CustomCollectorDocs("faker", base_docs_dir=Path.cwd(), assets=[asset])

    node_table = docs.render_class_table(
        "openhound_faker.models.computer.ComputerProperties",
        include_inherited=True,
    )
    assert "| `name` | `str` | - |" in node_table
    assert "| `tenant` | `str` | - |" in node_table
    assert "| `hostname` | `str` | - |" in node_table

    resource_table = docs.render_class_table(
        "openhound_faker.models.computer.FakeComputer"
    )
    assert "| `hostname` | `str` | - |" in resource_table
    assert "| `tenant` | `str` | - |" not in resource_table

    asset_render = docs.asset_templates[0]["render"]
    node_render = docs.node_templates[0]["render"]

    assert "::: openhound_faker.models.computer.ComputerProperties" not in asset_render
    assert "::: openhound_faker.models.computer.FakeComputer" not in asset_render
    assert "::: openhound_faker.models.computer.ComputerProperties" not in node_render
    assert "| `hostname` | `str` | - |" in asset_render
    assert "| `name` | `str` | - |" in node_render


def test_render_class_table_uses_docstring_attribute_descriptions(
    tmp_path, monkeypatch
):
    package_dir = tmp_path / "sample_docs_pkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "models.py").write_text(
        """
from dataclasses import dataclass


@dataclass
class BaseProperties:
    \"\"\"Base properties.

    Attributes:
        base_field: Base field description.
    \"\"\"

    base_field: str


@dataclass
class ChildProperties(BaseProperties):
    \"\"\"Child properties.

    Attributes:
        child_field: Child field description.
    \"\"\"

    child_field: int
""".strip()
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    docs = CustomCollectorDocs("sample", base_docs_dir=tmp_path, assets=[])

    table = docs.render_class_table(
        "sample_docs_pkg.models.ChildProperties",
        include_inherited=True,
    )

    assert "| `base_field` | `str` | Base field description. |" in table
    assert "| `child_field` | `int` | Child field description. |" in table


def test_generated_node_and_edge_pages_inline_custom_descriptions(tmp_path):
    descriptions_dir = tmp_path / "descriptions"
    (descriptions_dir / "faker" / "nodes").mkdir(parents=True)
    (descriptions_dir / "faker" / "edges").mkdir(parents=True)
    (descriptions_dir / "faker" / "nodes" / "RAND_Computer.md").write_text(
        "Node-specific handwritten content."
    )
    (descriptions_dir / "faker" / "edges" / "RAND_ExampleReference.md").write_text(
        "Edge-specific handwritten content."
    )

    asset = {
        "class": "FakeComputer",
        "path": "openhound_faker.models.computer.FakeComputer",
        "node": {
            "kind": "RAND_Computer",
            "icon": "computer",
            "properties": "openhound_faker.models.computer.ComputerProperties",
        },
        "edges": [
            {
                "start": "RAND_Computer",
                "end": "RAND_Computer",
                "kind": "RAND_ExampleReference",
                "description": "Binding DummyComputer to another DummyComputer",
                "traversable": False,
            }
        ],
    }
    docs = CustomCollectorDocs(
        "faker",
        base_docs_dir=tmp_path,
        assets=[asset],
        descriptions_dir=descriptions_dir,
    )

    node_render = docs.node_templates[0]["render"]
    edge_render = docs.edge_templates[0]["render"]

    assert "Node-specific handwritten content." in node_render
    assert "Edge-specific handwritten content." in edge_render
    assert '--8<-- "RAND_Computer.md"' not in node_render
    assert '--8<-- "RAND_ExampleReference.md"' not in edge_render


def test_to_markdown_rejects_writes_outside_cli_output_dir(tmp_path):
    docs = CustomCollectorDocs("faker", base_docs_dir=tmp_path, assets=[])

    with pytest.raises(ValueError, match="path traversal attempt"):
        docs.to_markdown(tmp_path.parent / "outside-docs")
