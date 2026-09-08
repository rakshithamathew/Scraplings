"""Gmail transport using the project's existing Composio authentication configuration."""
from __future__ import annotations

from importlib.metadata import version as package_version
from pathlib import Path
import re

from job_automation.integrations.composio import (
    ComposioConfig, ComposioConfigurationError, ComposioConnection, _items, _toolkit_slug, _value,
)

GMAIL_TOOLKIT_VERSION = '20260828_00'


class DeliveryUncertain(RuntimeError):
    """Provider acceptance cannot be determined; never retry automatically."""


class ProviderRejected(RuntimeError):
    """Provider returned a failure; retain the attempt rather than replaying it."""


class ComposioGmailTransport:
    def __init__(self, *, config=None, client_factory=None, upload_dir: Path):
        self.config = config or ComposioConfig.from_env()
        self.upload_dir = Path(upload_dir).resolve()
        self._client_factory = client_factory
        self.client = None
        self.account_id = None

    def verify(self):
        if not self.config.enabled:
            raise ComposioConfigurationError('Local Composio integration is disabled')
        self.config.require_valid()
        if not self.config.gmail_connected_account_id:
            raise ComposioConfigurationError('COMPOSIO_GMAIL_CONNECTED_ACCOUNT_ID is required for email automation')

        def factory(api_key):
            if self._client_factory:
                return self._client_factory(api_key)
            try:
                installed = package_version('composio')
            except Exception:
                raise ComposioConfigurationError('Install job_automation/integrations/requirements.txt') from None
            match = re.match(r'(\d+)\.(\d+)', installed)
            if not match or tuple(map(int, match.groups())) < (0, 21):
                raise ComposioConfigurationError('Composio >= 0.21 is required for non-retrying execution and scoped file uploads')
            from composio import Composio
            return Composio(api_key=api_key, toolkit_versions={'gmail': GMAIL_TOOLKIT_VERSION},
                dangerously_allow_auto_upload_download_files=True,
                file_upload_dirs=[str(self.upload_dir)])

        connection = ComposioConnection(self.config, client_factory=factory)
        # Check precisely this user's selected Gmail account, not an arbitrary active toolkit.
        self.client = connection._get_client()
        accounts = _items(self.client.connected_accounts.list(user_ids=[self.config.user_id]))
        if not any(_value(account, 'id') == self.config.gmail_connected_account_id
                   and _value(account, 'status').upper() == 'ACTIVE'
                   and _toolkit_slug(account) == 'GMAIL' for account in accounts):
            raise ComposioConfigurationError('The selected Gmail account is not active for this Composio user')
        self.account_id = self.config.gmail_connected_account_id
        return self.account_id

    def send(self, *, recipient, subject, body, attachment_path):
        if not self.client or not self.account_id:
            raise ComposioConfigurationError('Verify the Gmail account before sending')
        path = Path(attachment_path).resolve()
        if not path.is_relative_to(self.upload_dir) or not path.is_file():
            raise ValueError('Resume attachment must be a staged file in the configured upload directory')
        try:
            response = self.client.tools.execute(
                slug='GMAIL_SEND_EMAIL', user_id=self.config.user_id,
                connected_account_id=self.account_id, version=GMAIL_TOOLKIT_VERSION,
                arguments={'user_id': 'me', 'recipient_email': recipient, 'subject': subject,
                    'body': body, 'is_html': False, 'attachment': str(path)},
            )
        except Exception:
            # Do not expose provider errors that could contain CV content or credentials.
            raise DeliveryUncertain('Gmail send result is unknown; check Gmail Sent before any manual recovery') from None
        if not isinstance(response, dict):
            raise DeliveryUncertain('Gmail returned an unrecognized send response')
        if response.get('successful') is False or response.get('error'):
            raise ProviderRejected('Composio reported a send failure; no automatic retry')
        data = response.get('data') or {}
        envelope = data.get('response_data', data) if isinstance(data, dict) else {}
        message_id = envelope.get('id') or envelope.get('message_id') if isinstance(envelope, dict) else None
        if response.get('successful') is not True or not isinstance(message_id, str) or not message_id:
            raise DeliveryUncertain('No Gmail message ID confirmed delivery; check Gmail Sent')
        return message_id
