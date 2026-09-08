"""Optional, read-only Composio connection and discovery boundary.

This module never executes tools. The Gmail sending transport reuses this
configuration and account boundary in integrations.gmail_sender.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import importlib
import logging
import os
import re
from typing import Any


LOGGER = logging.getLogger(__name__)
_WHITESPACE = re.compile(r"\s+")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


class ComposioConfigurationError(ValueError):
    """Raised when enabled Composio configuration is incomplete or invalid."""


class ConnectionStatus(str, Enum):
    DISABLED = "DISABLED"
    MISCONFIGURED = "MISCONFIGURED"
    SDK_UNAVAILABLE = "SDK_UNAVAILABLE"
    VERIFIED = "VERIFIED"
    NO_ACTIVE_ACCOUNTS = "NO_ACTIVE_ACCOUNTS"
    CONNECTION_FAILED = "CONNECTION_FAILED"


@dataclass(frozen=True, slots=True)
class ComposioConfig:
    """Environment-backed configuration with secret-safe representation."""

    enabled: bool
    user_id: str | None
    api_key: str | None = field(default=None, repr=False)
    gmail_connected_account_id: str | None = None
    browser_connected_account_id: str | None = None
    toolkits: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ComposioConfig":
        values = os.environ if environ is None else environ
        api_key = _optional(values.get("COMPOSIO_API_KEY"))
        enabled_value = values.get("COMPOSIO_ENABLED")
        enabled = bool(api_key) if enabled_value is None else _parse_boolean(enabled_value)
        toolkits = tuple(
            dict.fromkeys(
                item.strip().upper()
                for item in values.get("COMPOSIO_TOOLKITS", "").split(",")
                if item.strip()
            )
        )
        return cls(
            enabled=enabled,
            api_key=api_key,
            user_id=_optional(values.get("COMPOSIO_USER_ID")),
            gmail_connected_account_id=_optional(values.get("COMPOSIO_GMAIL_CONNECTED_ACCOUNT_ID")),
            browser_connected_account_id=_optional(values.get("COMPOSIO_BROWSER_CONNECTED_ACCOUNT_ID")),
            toolkits=toolkits,
        )

    def validation_errors(self) -> tuple[str, ...]:
        if not self.enabled:
            return ()
        errors: list[str] = []
        if not self.api_key:
            errors.append("COMPOSIO_API_KEY is required when Composio is enabled")
        if not self.user_id:
            errors.append("COMPOSIO_USER_ID is required when Composio is enabled")
        return tuple(errors)

    def require_valid(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ComposioConfigurationError("; ".join(errors))


@dataclass(frozen=True, slots=True)
class ConnectionReport:
    ok: bool
    status: ConnectionStatus
    message: str
    active_accounts: int = 0
    total_accounts: int = 0
    active_toolkits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolDiscoveryReport:
    ok: bool
    message: str
    tools: tuple[str, ...] = ()


ClientFactory = Callable[[str], Any]


class ComposioConnection:
    """Read-only facade for configuration, auth checks, and tool discovery."""

    def __init__(
        self,
        config: ComposioConfig | None = None,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config or ComposioConfig.from_env()
        self._client_factory = client_factory
        self._client: Any | None = None

    def verify_connection(self) -> ConnectionReport:
        """Verify API access and summarize auth state without exposing credentials."""
        if not self.config.enabled:
            return ConnectionReport(False, ConnectionStatus.DISABLED, "Composio integration is disabled")
        errors = self.config.validation_errors()
        if errors:
            return ConnectionReport(False, ConnectionStatus.MISCONFIGURED, "; ".join(errors))
        try:
            client = self._get_client()
            response = client.connected_accounts.list(user_ids=[self.config.user_id])
            accounts = _items(response)
            active = [account for account in accounts if _value(account, "status").upper() == "ACTIVE"]
            toolkits = tuple(
                sorted(
                    {
                        toolkit
                        for account in active
                        if (toolkit := _toolkit_slug(account))
                    }
                )
            )
            missing_ids = self._missing_configured_accounts(active)
            if missing_ids:
                return ConnectionReport(
                    False,
                    ConnectionStatus.NO_ACTIVE_ACCOUNTS,
                    "Configured connected account is not active or was not returned for this user",
                    len(active),
                    len(accounts),
                    toolkits,
                )
            if not active:
                return ConnectionReport(
                    True,
                    ConnectionStatus.NO_ACTIVE_ACCOUNTS,
                    "Composio API connection verified; no active connected accounts were found",
                    0,
                    len(accounts),
                )
            return ConnectionReport(
                True,
                ConnectionStatus.VERIFIED,
                "Composio API connection and active account state verified",
                len(active),
                len(accounts),
                toolkits,
            )
        except ModuleNotFoundError:
            return ConnectionReport(
                False,
                ConnectionStatus.SDK_UNAVAILABLE,
                "The optional 'composio' package is not installed",
            )
        except Exception as error:  # SDK/network exceptions vary by installed version.
            LOGGER.warning("Composio connection verification failed (%s)", type(error).__name__)
            return ConnectionReport(
                False,
                ConnectionStatus.CONNECTION_FAILED,
                _safe_error("Unable to verify the Composio connection", error, self.config.api_key),
            )

    def discover_tools(
        self,
        *,
        toolkits: Sequence[str] | None = None,
        limit: int = 20,
    ) -> ToolDiscoveryReport:
        """Read tool definitions only. No Composio tool is executed."""
        if limit < 1 or limit > 100:
            return ToolDiscoveryReport(False, "Tool discovery limit must be between 1 and 100")
        connection = self.verify_connection()
        if not connection.ok:
            return ToolDiscoveryReport(False, connection.message)
        selected = tuple(item.upper() for item in (toolkits or self.config.toolkits) if item)
        try:
            response = self._get_client().tools.get(
                user_id=self.config.user_id,
                toolkits=list(selected) or None,
                limit=limit,
            )
            names = tuple(
                name
                for tool in _items(response)
                if (name := _tool_name(tool))
            )
            return ToolDiscoveryReport(True, f"Discovered {len(names)} Composio tools", names)
        except Exception as error:  # SDK/network exceptions vary by installed version.
            LOGGER.warning("Composio tool discovery failed (%s)", type(error).__name__)
            return ToolDiscoveryReport(
                False,
                _safe_error("Unable to discover Composio tools", error, self.config.api_key),
            )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.config.api_key:
            raise ComposioConfigurationError("COMPOSIO_API_KEY is required")
        if self._client_factory is not None:
            self._client = self._client_factory(self.config.api_key)
        else:
            module = importlib.import_module("composio")
            self._client = module.Composio(api_key=self.config.api_key)
        return self._client

    def _missing_configured_accounts(self, active_accounts: Sequence[Any]) -> set[str]:
        required = {
            value
            for value in (
                self.config.gmail_connected_account_id,
                self.config.browser_connected_account_id,
            )
            if value
        }
        active_ids = {_value(account, "id") for account in active_accounts}
        return required - active_ids


def _optional(value: str | None) -> str | None:
    cleaned = value.strip() if value else ""
    return cleaned or None


def _parse_boolean(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ComposioConfigurationError(
        "COMPOSIO_ENABLED must be one of: true, false, 1, 0, yes, no, on, off"
    )


def _items(response: Any) -> list[Any]:
    if response is None:
        return []
    items = response.get("items") if isinstance(response, Mapping) else getattr(response, "items", response)
    if callable(items):
        return []
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        return list(items)
    try:
        return list(items)
    except TypeError:
        return []


def _value(item: Any, key: str) -> str:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, "")
    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _toolkit_slug(account: Any) -> str:
    toolkit = account.get("toolkit") if isinstance(account, Mapping) else getattr(account, "toolkit", None)
    return _value(toolkit, "slug").upper() if toolkit else ""


def _tool_name(tool: Any) -> str:
    for key in ("slug", "name"):
        value = _value(tool, key)
        if value:
            return value
    if isinstance(tool, Mapping) and isinstance(tool.get("function"), Mapping):
        return str(tool["function"].get("name") or "")
    return ""


def _safe_error(prefix: str, error: Exception, api_key: str | None) -> str:
    message = _WHITESPACE.sub(" ", str(error)).strip()
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    if not message:
        return prefix
    return f"{prefix}: {message[:240]}"
