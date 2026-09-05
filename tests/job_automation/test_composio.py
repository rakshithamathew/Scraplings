from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_automation.integrations.composio import (
    ComposioConfig,
    ComposioConfigurationError,
    ComposioConnection,
    ConnectionStatus,
)


class FakeConnectedAccounts:
    def __init__(self, accounts: list[object] | None = None, error: Exception | None = None) -> None:
        self.accounts = accounts or []
        self.error = error
        self.user_ids: list[str] | None = None

    def list(self, *, user_ids: list[str]) -> object:
        self.user_ids = user_ids
        if self.error:
            raise self.error
        return SimpleNamespace(items=self.accounts)


class FakeTools:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    def get(self, **arguments: object) -> list[dict[str, str]]:
        self.arguments = arguments
        return [{"slug": "GMAIL_GET_PROFILE"}, {"slug": "GMAIL_SEARCH_EMAILS"}]


class FakeClient:
    def __init__(self, accounts: list[object] | None = None, error: Exception | None = None) -> None:
        self.connected_accounts = FakeConnectedAccounts(accounts, error)
        self.tools = FakeTools()


def _config(**changes: object) -> ComposioConfig:
    values: dict[str, object] = {
        "enabled": True,
        "api_key": "secret-test-key",
        "user_id": "local-user",
        "toolkits": ("GMAIL",),
    }
    values.update(changes)
    return ComposioConfig(**values)


def test_configuration_loads_from_explicit_environment_without_exposing_secret() -> None:
    config = ComposioConfig.from_env(
        {
            "COMPOSIO_ENABLED": "true",
            "COMPOSIO_API_KEY": "private-key",
            "COMPOSIO_USER_ID": "user-1",
            "COMPOSIO_TOOLKITS": "gmail, browser, gmail",
        }
    )

    assert config.enabled is True
    assert config.toolkits == ("GMAIL", "BROWSER")
    assert "private-key" not in repr(config)


def test_invalid_enabled_value_is_rejected() -> None:
    with pytest.raises(ComposioConfigurationError, match="COMPOSIO_ENABLED"):
        ComposioConfig.from_env({"COMPOSIO_ENABLED": "sometimes"})


def test_disabled_and_missing_configuration_are_reported_safely() -> None:
    disabled = ComposioConnection(ComposioConfig(enabled=False, user_id=None)).verify_connection()
    missing = ComposioConnection(ComposioConfig(enabled=True, user_id=None)).verify_connection()

    assert disabled.status is ConnectionStatus.DISABLED
    assert missing.status is ConnectionStatus.MISCONFIGURED
    assert "API_KEY" in missing.message


def test_connection_verifies_active_auth_state() -> None:
    account = SimpleNamespace(
        id="ca_gmail",
        status="ACTIVE",
        toolkit=SimpleNamespace(slug="gmail"),
    )
    client = FakeClient([account])
    connection = ComposioConnection(
        _config(gmail_connected_account_id="ca_gmail"),
        client_factory=lambda _: client,
    )

    report = connection.verify_connection()

    assert report.ok is True
    assert report.status is ConnectionStatus.VERIFIED
    assert report.active_accounts == 1
    assert report.active_toolkits == ("GMAIL",)
    assert client.connected_accounts.user_ids == ["local-user"]


def test_missing_configured_account_requires_auth_review() -> None:
    client = FakeClient([])
    report = ComposioConnection(
        _config(gmail_connected_account_id="ca_missing"),
        client_factory=lambda _: client,
    ).verify_connection()

    assert report.ok is False
    assert report.status is ConnectionStatus.NO_ACTIVE_ACCOUNTS


def test_safe_error_redacts_api_key() -> None:
    client = FakeClient(error=RuntimeError("request rejected for secret-test-key"))
    report = ComposioConnection(_config(), client_factory=lambda _: client).verify_connection()

    assert report.status is ConnectionStatus.CONNECTION_FAILED
    assert "secret-test-key" not in report.message
    assert "[REDACTED]" in report.message


def test_tool_discovery_is_read_only_and_scoped() -> None:
    account = SimpleNamespace(id="ca_gmail", status="ACTIVE", toolkit={"slug": "gmail"})
    client = FakeClient([account])
    connection = ComposioConnection(_config(), client_factory=lambda _: client)

    report = connection.discover_tools(limit=10)

    assert report.ok is True
    assert report.tools == ("GMAIL_GET_PROFILE", "GMAIL_SEARCH_EMAILS")
    assert client.tools.arguments == {
        "user_id": "local-user",
        "toolkits": ["GMAIL"],
        "limit": 10,
    }
