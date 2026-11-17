"""BidBox connector platform: any institution system connects by manifest, not by code changes."""

from bidbox.connectors.registry import Registry, load_registry
from bidbox.connectors.base import Connector, PlannedConnector, HealthResult
from bidbox.connectors.errors import (
    ApprovalRequiredError,
    ConnectorError,
    PlannedConnectorError,
    ERROR_KINDS,
    map_exception,
)
from bidbox.connectors.redaction import redact, redact_text, mask_contact

__all__ = [
    "Registry", "load_registry", "Connector", "PlannedConnector", "HealthResult",
    "ApprovalRequiredError", "ConnectorError", "PlannedConnectorError", "ERROR_KINDS",
    "map_exception", "redact", "redact_text", "mask_contact",
]
