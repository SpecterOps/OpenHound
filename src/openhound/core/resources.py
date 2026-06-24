import functools
import inspect
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def safe_defer_wrapper(func: Callable) -> Callable:
    """Wrap a DLT defer to catch and log exceptions without stopping the entire pipeline.

    Args:
        func: The defer function

    Returns:
        Wrapped function that catches exceptions and continues (if possible of course)
    """

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"Error executing DLT defer: {e}",
                extra={"phase": "defer_execution"},
            )
            return []

    return sync_wrapper


def safe_resource_wrapper(func: Callable, resource_name: str) -> Callable:
    """Wrap a DLT resource to catch and log exceptions without stopping the entire pipeline. Can either be sync or async generator function.

    Args:
        func: The resource function (can be sync or async generator)
        resource_name: Name of the resource for logging

    Returns:
        Wrapped function that catches exceptions and continues (if possible of course)
    """

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        """A wrapper which yields results from the resource function but catches any unhandled exceptions

        Yields:
            any: Yields items from the original resource function but continues even if exceptions are raised
        """
        try:
            gen = func(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"Error initializing resource '{resource_name}': {e}",
                extra={"resource": resource_name, "phase": "resource_initialization"},
            )
            return

        if inspect.isgenerator(gen):
            while True:
                try:
                    item = next(gen)
                    yield item
                except StopIteration:
                    break
                except Exception as e:
                    logger.error(
                        f"Error in resource '{resource_name}' during iteration: {e}",
                        extra={
                            "resource": resource_name,
                            "phase": "resource_iteration",
                        },
                    )
                    continue
        else:
            yield gen

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        """A wrapper which yields results from the async resource function but catches any unhandled exceptions

        Yields:
            any: Yields items from the original resource function but continues even if exceptions are raised
        """
        try:
            gen = func(*args, **kwargs)
        except Exception as e:
            logger.error(
                f"Error initializing async resource '{resource_name}': {e}",
                extra={"resource": resource_name, "phase": "resource_initialization"},
            )
            return

        if inspect.isasyncgen(gen):
            while True:
                try:
                    item = await gen.__anext__()
                    yield item
                except StopAsyncIteration:
                    break
                except Exception as e:
                    logger.error(
                        f"Error in async resource '{resource_name}' during iteration: {e}",
                        extra={
                            "resource": resource_name,
                            "phase": "resource_iteration",
                        },
                    )
                    continue

        else:
            try:
                result = await gen
                yield result
            except Exception as e:
                logger.error(
                    f"Error awaiting async resource '{resource_name}': {e}",
                    extra={"resource": resource_name, "phase": "resource_awaiting"},
                )

    if inspect.isasyncgenfunction(func):
        return async_wrapper

    else:
        return sync_wrapper
