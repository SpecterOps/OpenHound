class IgnoreResponseException(Exception):
    """Raise from a response hook to stop pagination without yielding this page."""
