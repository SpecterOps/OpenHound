from pathlib import Path
from types import SimpleNamespace

import pytest

from openhound.core.convert import Method
from openhound.core.progress import Progress
from openhound.scheduler import dataflow


@pytest.mark.parametrize("output_path", [None, Path("job-output")])
def test_conversion_stages_files_only_when_output_path_is_provided(
    monkeypatch, output_path
):
    calls = []
    extension = SimpleNamespace(
        name="example", converter=lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(dataflow, "extracted_resources", lambda info: ["nodes"])

    assert dataflow.convert(extension, output_path) == ["nodes"]

    expected = {
        "input_path": Path("/tmp/openhound/example"),
        "method": Method.ingest if output_path is None else Method.write,
        "progress": Progress.log,
    }
    if output_path is not None:
        expected["output_path"] = output_path
    assert calls == [expected]


def test_pipeline_finishes_collection_and_preprocessing_before_conversion(monkeypatch):
    calls = []
    extension = SimpleNamespace(preprocessor=object())
    output_path = Path("job-output")

    def collect(extension):
        calls.append("collect")
        return ["source"]

    def preprocess(extension):
        calls.append("preprocess")
        return ["lookup"]

    def convert(extension, destination):
        calls.append(("convert", destination))
        return ["nodes"]

    monkeypatch.setattr(dataflow, "collect", collect)
    monkeypatch.setattr(dataflow, "preprocess", preprocess)
    monkeypatch.setattr(dataflow, "convert", convert)

    assert dataflow.pipeline(extension, output_path) == {
        "collect": ["source"],
        "preprocess": ["lookup"],
        "convert": ["nodes"],
    }
    assert calls == ["collect", "preprocess", ("convert", output_path)]
