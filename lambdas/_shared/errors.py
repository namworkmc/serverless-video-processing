"""Domain errors and HTTP error mapping (NFR-3).

Mapping contract:
    transition conflict (ConditionalCheckFailedException) -> 409
    unknown videoId                                       -> 404
    malformed input                                       -> 400
    anything else                                         -> 500
Every client-facing body is {"error": "<message>"}.
"""


class SharedLayerError(Exception):
    """Base class for shared-layer domain errors."""

    http_status = 500

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class MalformedInputError(SharedLayerError):
    http_status = 400


class NotFoundError(SharedLayerError):
    http_status = 404


class ConflictError(SharedLayerError):
    http_status = 409


class InternalError(SharedLayerError):
    http_status = 500


def is_conditional_check_failed(exc):
    """True for boto3's ConditionalCheckFailedException (duck-typed so the
    layer stays testable without boto3 imported)."""
    return type(exc).__name__ == "ConditionalCheckFailedException"


def map_error(exc):
    """Map an exception to (http_status, {"error": message})."""
    if isinstance(exc, SharedLayerError):
        return exc.http_status, {"error": exc.message}
    if is_conditional_check_failed(exc):
        return 409, {"error": str(exc) or "conditional write conflict"}
    return 500, {"error": f"internal error: {type(exc).__name__}"}
