from openhound.core.asset import ASSET_REGISTRY


def test_implemented_methods(subtests):
    for resource in ASSET_REGISTRY.keys():
        with subtests.test(msg="BaseAssetMethods", resource=resource.__name__):
            abstract = getattr(resource, "__abstractmethods__", set())
            assert not abstract, (
                f"{resource.__name__} has unimplemented abstract methods: {', '.join(list(abstract))}"
            )
