"""Shared access layer — the single enforcement point for the platform.

Every Lambda imports this package; nothing outside it knows the legal
transition table, the event envelope shape, or the error mapping.

Modules:
    status   -- status state machine over DynamoDB conditional writes
    events   -- deterministic event envelopes (UUID5 eventId)
    errors   -- domain errors + HTTP error mapping
    clients  -- boto3 client factories (env-driven, config-not-code)
"""

from shared import clients, errors, events, status  # noqa: F401
