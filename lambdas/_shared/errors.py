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


def require_field(event, name):
    """Return a non-empty string field or raise MalformedInputError.

    Returns the STRIPPED value so whitespace-padded fields cannot leak
    into S3 keys, event details, or ASL inputs. Shared by every handler
    that validates a domain payload (transcode, event-publisher, ...).
    """
    value = event.get(name) if isinstance(event, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise MalformedInputError(f"missing or empty required field: {name}")
    return value.strip()


def is_client_error_code(exc, code):
    """True when boto3 raised the ClientError subclass named after `code`.

    boto3 generates ClientError subclasses dynamically named after the
    error code, so the class name is the stable signal. Duck-typed so the
    layer stays testable without boto3 imported. Single home for the
    pattern (was duplicated: ConditionalCheckFailedException here,
    ExecutionAlreadyExists in the shim).
    """
    return type(exc).__name__ == code


def is_conditional_check_failed(exc):
    """True for boto3's ConditionalCheckFailedException."""
    return is_client_error_code(exc, "ConditionalCheckFailedException")


def map_error(exc):
    """Map an exception to (http_status, {"error": message})."""
    if isinstance(exc, SharedLayerError):
        return exc.http_status, {"error": exc.message}
    if is_conditional_check_failed(exc):
        return 409, {"error": str(exc) or "conditional write conflict"}
    return 500, {"error": f"internal error: {type(exc).__name__}"}
