"""Guard compatibility patches when the independently pinned extension changes."""

import runpy
from pathlib import Path

import pytest

PREPARATION = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/prepare_aws_bundle.py")
)


@pytest.fixture
def aws_source(tmp_path):
    (tmp_path / "src/openhound_aws").mkdir(parents=True)
    (tmp_path / "extension.yaml").write_text("name: aws\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["openhound==0.2.13", "boto3==1.43.68"]\n'
        '[dependency-groups]\ndev = ["openhound==0.2.13"]\n'
    )
    return tmp_path


def test_packaging_preserves_selected_openhound_and_embeds_manifest(aws_source):
    PREPARATION["patch_packaging"](aws_source, "0.4.1dev9346005")
    text = (aws_source / "pyproject.toml").read_text()
    assert text.count('"openhound==0.4.1.dev9346005"') == 2
    assert '"boto3>=1.43.90"' in text
    assert (
        aws_source / "src/openhound_aws/extension.yaml"
    ).read_text() == "name: aws\n"


def test_changed_upstream_metadata_is_rejected_without_partial_rewrite(aws_source):
    metadata = aws_source / "pyproject.toml"
    changed = metadata.read_text().replace("boto3==1.43.68", "boto3==2.0.0")
    metadata.write_text(changed)
    with pytest.raises(ValueError, match="Unexpected AWS packaging metadata"):
        PREPARATION["patch_packaging"](aws_source, "0.4.1.dev9346005")
    assert metadata.read_text() == changed


@pytest.mark.parametrize("commit", ["main", "abc123", "a" * 39, "g" * 40])
def test_unpinned_source_is_rejected_before_credentials_are_read(commit):
    with pytest.raises(ValueError, match="full, pinned commit SHA"):
        PREPARATION["main"](commit)
