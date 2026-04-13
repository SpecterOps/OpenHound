from types import SimpleNamespace

from openhound.cli import create


class DummyDocs:
    calls = []

    def __init__(
        self,
        name,
        base_docs_dir,
        assets,
        sources=None,
        resources=None,
        transformers=None,
    ):
        self.name = name
        self.base_docs_dir = base_docs_dir
        self.assets = assets
        self.sources = sources
        self.resources = resources
        self.transformers = transformers

    def to_markdown(self, output_path):
        self.__class__.calls.append((self.name, self.base_docs_dir, output_path))


def test_generate_docs_uses_extension_subdirectory_for_single_extension(
    tmp_path, monkeypatch
):
    class DummyGraphResourceDecorator:
        def __init__(self):
            self.collectors = {
                "faker": SimpleNamespace(
                    assets=[],
                    sources=[],
                    resources=[],
                    transformers=[],
                )
            }

    dummy_ext = SimpleNamespace(
        name="faker",
        module="openhound_faker.main",
        dist=SimpleNamespace(name="openhound-faker"),
    )

    monkeypatch.setattr(create, "entry_points", lambda group: [dummy_ext])

    import griffe
    import openhound.docs.pipeline as pipeline_module

    monkeypatch.setattr(griffe, "load_extensions", lambda ext: ext)
    monkeypatch.setattr(griffe, "load", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pipeline_module,
        "GraphResourceDecorator",
        DummyGraphResourceDecorator,
    )
    monkeypatch.setattr(pipeline_module, "CustomCollectorDocs", DummyDocs)
    DummyDocs.calls.clear()

    create.generate_docs(tmp_path)

    assert DummyDocs.calls == [
        ("faker", tmp_path, tmp_path / "sources" / "faker")
    ]
