"""Sample API Client."""
from wdnas_client import client
from wdnas_client.exceptions import InvalidLoginError, RequestFailedError
from __future__ import annotations

import socket
from typing import Any

import aiohttp
import async_timeout


class IntegrationBlueprintApiClientError(Exception):
    """Exception to indicate a general API error."""


class IntegrationBlueprintApiClientCommunicationError(
    IntegrationBlueprintApiClientError,
):
    """Exception to indicate a communication error."""


class IntegrationBlueprintApiClientAuthenticationError(
    IntegrationBlueprintApiClientError,
):
    """Exception to indicate an authentication error."""


class IntegrationBlueprintApiClient:
    """API client for your service, using your PyPI module."""

    def __init__(self, hass, username, password, host):
        self.hass = hass
        self.username = username
        self.password = password
        self.host = host
        self._client = None  # Initialize the client later, after successful login

    async def async_initialize_client(self):
        """Initializes the client and logs in."""
        if self._client is None:
            # Create a new client instance and login
            self._client = client(self.username, self.password, self.host)
            await self.hass.async_add_executor_job(self._client.login)

    async def async_get_session_token(self) -> str | None:
        """Attempts to get a session token, adapting to your module's structure."""
        await self.async_initialize_client()  # Ensure client is initialized
        try:
            # Check if PHPSSESID is available and return it as a form of session token
            session_token = self._client.session.cookies.get("PHPSESSID")
            if session_token:
                return session_token
            else:
                return None  # No session token found
        except (InvalidLoginError, RequestFailedError) as e:
            # Handle exceptions raised by your module
            if isinstance(e, InvalidLoginError):
                raise IntegrationBlueprintApiClientAuthenticationError(
                    "Authentication failed"
                ) from e
            else:
                raise IntegrationBlueprintApiClientCommunicationError(
                    f"Error communicating with API: {e}"
                ) from e

    async def async_get_data(self):
        """Example of making an authenticated API request."""
        await self.async_initialize_client()  # Ensure client is initialized
        try:
            # Adapt this to how you fetch data with your module's client
            data = await self.hass.async_add_executor_job(
                self._client.get_some_data
            )
            return data
        except (InvalidLoginError, RequestFailedError) as e:
            # Handle exceptions from your module
            if isinstance(e, InvalidLoginError):
                raise IntegrationBlueprintApiClientAuthenticationError(
                    "Authentication failed"
                ) from e
            else:
                raise IntegrationBlueprintApiClientCommunicationError(
                    f"Error communicating with API: {e}"
                ) from e