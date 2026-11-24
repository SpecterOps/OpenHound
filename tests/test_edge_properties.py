import pytest

from openhound.core.models.entries_dataclass import EdgeProperties


@pytest.mark.parametrize("traversable", [True, False])
def test_edge_properties_accept_and_discard_legacy_traversable(traversable):
    properties = EdgeProperties(traversable=traversable)

    assert properties.traversable is None
