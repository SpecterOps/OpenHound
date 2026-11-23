"""Job-scoped configuration using the same DLT resolver as local TOML files."""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from dlt.common.configuration.container import Container
from dlt.common.configuration.exceptions import ConfigurationException
from dlt.common.configuration.providers.doc import CustomLoaderDocProvider
from dlt.common.configuration.specs.pluggable_run_context import PluggableRunContext
from dlt.common.configuration.utils import TraceLogContext
from dlt.common.utils import update_dict_nested


class ManagedConfigurationError(ValueError):
    """A managed configuration failure safe to report without its values."""


def _validate_document(document: dict[str, Any], *, allow_empty: bool = False) -> None:
    # Check only the document boundary. DLT and the collector own setting names,
    # types, and values; custom TOML sections do not require framework changes.
    if not isinstance(document, dict) or (not document and not allow_empty):
        raise ManagedConfigurationError(
            "Managed job configuration must be a non-empty nested JSON object"
        )
    if any(not isinstance(value, dict) for value in document.values()):
        # Never include user-supplied keys or values: either can contain secrets.
        raise ManagedConfigurationError(
            "Managed job configuration must use nested TOML sections "
            "(for example sources.source.aws); flat configuration is not supported"
        )


@contextmanager
def managed_job_configuration(
    params: dict[str, Any], secrets: dict[str, Any]
) -> Iterator[None]:
    """Resolve full-hierarchy JSON for one worker job, then restore its providers.

    Retrieved secrets win per setting. Mappings merge recursively; lists are
    individual settings and are replaced in full. Copy both documents so DLT or
    collector mutations cannot affect the inputs retained by the scheduler.
    """
    _validate_document(params)
    _validate_document(secrets, allow_empty=True)
    document = update_dict_nested(deepcopy(params), deepcopy(secrets))
    provider = CustomLoaderDocProvider(
        "Managed job configuration", lambda: document, supports_secrets=True
    )
    container = Container()
    providers = container[PluggableRunContext].providers
    previous = providers.providers
    # Managed workers run one job at a time. Leave environment/TOML providers as
    # fallback for deployment defaults, with this job's document taking priority.
    providers.providers = [provider, *previous]
    # Credential model fields can be plain strings, not DLT secret hints. Do not
    # retain their values in DLT's configuration traces (including pipeline traces).
    traces = TraceLogContext()
    traces.logging_enabled = False
    try:
        with container.injectable_context(traces):
            yield
    except ConfigurationException:
        # DLT coercion exceptions can include the value, even for credential fields.
        raise ManagedConfigurationError(
            "Managed job configuration could not be resolved; "
            "check setting types and required fields"
        ) from None
    finally:
        providers.providers = previous
