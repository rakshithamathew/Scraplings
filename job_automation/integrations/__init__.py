"""Optional third-party integrations for the job-automation application."""

from .composio import (
    ComposioConfig,
    ComposioConnection,
    ConnectionReport,
    ConnectionStatus,
    ToolDiscoveryReport,
)

__all__ = [
    "ComposioConfig",
    "ComposioConnection",
    "ConnectionReport",
    "ConnectionStatus",
    "ToolDiscoveryReport",
]
