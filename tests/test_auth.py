"""Integration tests for GoveeAuthClient login flow.

Tests cover:
- Successful login → GoveeIotCredentials
- HTTP 401 → GoveeAuthError
- HTTP 454 (non-standard) → GoveeLoginRejectedError
- JSON body status codes (200 outer, non-200 inner)
- aiohttp.ClientError → GoveeApiError
- Missing token → GoveeApiError
- Request URL, headers, and payload shape
- get_iot_key and fetch_device_topics header contracts
- _build_govee_headers static method
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.govee.api.auth import (
    GOVEE_APP_VERSION,
    GOVEE_CLIENT_TYPE,
    GOVEE_DEVICE_LIST_URL,
    GOVEE_IOT_KEY_URL,
    GOVEE_IOT_VERSION,
    GOVEE_LOGIN_URL,
    GOVEE_USER_AGENT,
    GoveeAuthClient,
    GoveeIotCredentials,
    _derive_client_id,
)
from custom_components.govee.api.exceptions import (
    Govee2FACodeInvalidError,
    Govee2FARequiredError,
    GoveeApiError,
    GoveeAuthError,
    GoveeLoginRejectedError,
)

# ==============================================================================
# Test data factories
# ==============================================================================

_CERT_PEM = "-----BEGIN CERTIFICATE-----\nMIItest\n-----END CERTIFICATE-----\n"
_KEY_PEM = "-----BEGIN PRIVATE KEY-----\nMIItest\n-----END PRIVATE KEY-----\n"


def create_login_response(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Factory for a successful login API response (HTTP 200, status 200)."""
    response: dict[str, Any] = {
        "status": 200,
        "message": "success",
        "client": {
            "token": "test-token-abc123",
            "refreshToken": "test-refresh-xyz789",
            "topic": "GA/test-account-id",
            "accountId": 99001,
        },
    }
    if overrides:
        response.update(overrides)
    return response


def create_iot_key_response(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Factory for a successful IoT key API response (PEM format, no P12 parsing needed)."""
    data: dict[str, Any] = {
        "certificatePem": _CERT_PEM,
        "privateKey": _KEY_PEM,
        "endpoint": "a1b2c3d4e5f6g7.iot.us-east-1.amazonaws.com",
    }
    if overrides:
        data.update(overrides)
    return {"data": data}


def create_device_list_response(
    devices: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Factory for a device list API response."""
    if devices is None:
        devices = [
            {
                "device": "AA:BB:CC:DD:EE:FF:00:11",
                "deviceExt": {
                    "deviceSettings": {
                        "topic": "GA/device/AA:BB:CC:DD:EE:FF:00:11",
                    }
                },
            }
        ]
    return {"devices": devices}


# ==============================================================================
# HTTP mock helpers
# ==============================================================================


def make_mock_response(
    status: int,
    json_data: Any,
    *,
    content_type: str = "application/json",
) -> MagicMock:
    """Create a mock aiohttp response that works as an async context manager."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_data)
    response.content_type = content_type
    return response


@asynccontextmanager
async def _async_cm(value: Any):
    """Minimal async context manager that yields a single value."""
    yield value


def make_session_post(responses: list[MagicMock]) -> MagicMock:
    """Return a session mock whose .post() yields responses in order.

    Each call to session.post(...) returns the next response in the list.
    The session itself is also used as an async context manager (__aenter__
    returns the response), matching ``async with session.post(...) as resp:``.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    session.close = AsyncMock()

    call_count = {"n": 0}

    def _post(*_args: Any, **_kwargs: Any):
        idx = call_count["n"]
        call_count["n"] += 1
        resp = responses[idx]
        return _async_cm(resp)

    session.post = _post
    return session


def make_session_get(response: MagicMock) -> MagicMock:
    """Return a session mock whose .get() yields the given response."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.close = AsyncMock()

    def _get(*_args: Any, **_kwargs: Any):
        return _async_cm(response)

    session.get = _get
    return session


def make_session_post_get(
    post_response: MagicMock,
    get_response: MagicMock,
) -> MagicMock:
    """Return a session with one POST response (login) and one GET response (IoT key)."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.close = AsyncMock()
    session.post = lambda *a, **kw: _async_cm(post_response)
    session.get = lambda *a, **kw: _async_cm(get_response)
    return session


# ==============================================================================
# Tests: GoveeAuthClient._build_govee_headers
# ==============================================================================


class TestBuildGoveeHeaders:
    """Test the shared Govee app header builder."""

    def test_build_govee_headers_includes_all_required(self):
        """should include all headers required by the Govee app API."""
        # Arrange + Act
        headers = GoveeAuthClient._build_govee_headers(client_id="fixed-client-id")

        # Assert
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"
        assert headers["appVersion"] == GOVEE_APP_VERSION
        assert headers["clientType"] == GOVEE_CLIENT_TYPE
        assert headers["iotVersion"] == GOVEE_IOT_VERSION
        assert headers["clientId"] == "fixed-client-id"
        assert "timestamp" in headers
        assert headers["User-Agent"] == GOVEE_USER_AGENT

    def test_build_govee_headers_generates_client_id_when_none(self):
        """should generate a non-empty clientId when none is supplied."""
        # Arrange + Act
        headers = GoveeAuthClient._build_govee_headers()

        # Assert
        assert headers["clientId"]
        assert len(headers["clientId"]) > 0

    def test_build_govee_headers_app_version_value(self):
        """should embed the documented app version string."""
        # Arrange + Act
        headers = GoveeAuthClient._build_govee_headers()

        # Assert — pin the exact version so tests catch an accidental bump
        assert headers["appVersion"] == "7.4.10"

    def test_build_govee_headers_user_agent_contains_app_version(self):
        """should embed GoveeHome/<version> in the User-Agent."""
        # Arrange + Act
        headers = GoveeAuthClient._build_govee_headers()

        # Assert
        assert f"GoveeHome/{GOVEE_APP_VERSION}" in headers["User-Agent"]

    def test_build_govee_headers_timestamp_is_numeric_string(self):
        """should produce a numeric millisecond timestamp string."""
        # Arrange + Act
        headers = GoveeAuthClient._build_govee_headers()

        # Assert
        assert headers["timestamp"].isdigit()
        # Milliseconds since epoch: should be > 1_000_000_000_000 (year 2001+)
        assert int(headers["timestamp"]) > 1_000_000_000_000


# ==============================================================================
# Tests: GoveeAuthClient.login — success
# ==============================================================================


class TestLoginSuccess:
    """Test successful login flow returning valid GoveeIotCredentials."""

    async def test_login_success(self):
        """should return valid GoveeIotCredentials when login and IoT key both succeed."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert — token and refresh token are set
        assert credentials.token == "test-token-abc123"
        assert credentials.refresh_token == "test-refresh-xyz789"

    async def test_login_success_sets_account_topic(self):
        """should populate account_topic from client.topic in response."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert
        assert credentials.account_topic == "GA/test-account-id"

    async def test_login_success_sets_iot_cert_and_key(self):
        """should populate iot_cert and iot_key from PEM-format IoT key response."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert
        assert credentials.iot_cert == _CERT_PEM
        assert credentials.iot_key == _KEY_PEM

    async def test_login_success_sets_endpoint(self):
        """should populate endpoint from IoT key response."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert
        assert credentials.endpoint == "a1b2c3d4e5f6g7.iot.us-east-1.amazonaws.com"

    async def test_login_success_builds_mqtt_client_id(self):
        """should build MQTT client_id as AP/<accountId>/<client_id>."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login(
            "user@example.com", "s3cr3t", client_id="myclientid"
        )

        # Assert — accountId 99001 from factory
        assert credentials.client_id == "AP/99001/myclientid"

    async def test_login_success_credentials_are_valid(self):
        """should produce credentials whose is_valid property returns True."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert
        assert credentials.is_valid is True

    async def test_login_uses_default_iot_endpoint_when_missing(self):
        """should fall back to the hardcoded AWS IoT endpoint when response omits it."""
        # Arrange
        iot_data_no_endpoint = create_iot_key_response()
        del iot_data_no_endpoint["data"]["endpoint"]
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, iot_data_no_endpoint)
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login("user@example.com", "s3cr3t")

        # Assert — default endpoint from auth.py source
        assert "iot.us-east-1.amazonaws.com" in credentials.endpoint


# ==============================================================================
# Tests: GoveeAuthClient.login — HTTP error codes
# ==============================================================================


class TestLoginHttpErrors:
    """Test login raising the correct exception for non-200 HTTP status codes."""

    async def test_login_http_401_raises_auth_error(self):
        """should raise GoveeAuthError when login returns HTTP 401."""
        # Arrange
        login_resp = make_mock_response(401, {"message": "Unauthorized"})
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeAuthError) as exc_info:
            await client.login("user@example.com", "wrong-password")

        assert exc_info.value.code == 401

    async def test_login_http_454_raises_login_rejected_error(self):
        """should raise GoveeLoginRejectedError when login returns HTTP 454."""
        # Arrange
        login_resp = make_mock_response(454, {"message": "Client type not supported"})
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeLoginRejectedError):
            await client.login("user@example.com", "s3cr3t")

    async def test_login_http_500_raises_login_rejected_error(self):
        """should raise GoveeLoginRejectedError when login returns unexpected HTTP 5xx."""
        # Arrange
        login_resp = make_mock_response(500, {"message": "Internal Server Error"})
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeLoginRejectedError):
            await client.login("user@example.com", "s3cr3t")

    async def test_login_http_454_message_contains_status(self):
        """should include the HTTP status in the GoveeLoginRejectedError message."""
        # Arrange
        login_resp = make_mock_response(454, {"message": "Rejected"})
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeLoginRejectedError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert "454" in str(exc_info.value)


# ==============================================================================
# Tests: GoveeAuthClient.login — JSON body status codes (HTTP 200, inner status)
# ==============================================================================


class TestLoginJsonStatusCodes:
    """Test login raising the correct exception based on JSON-body status field."""

    async def test_login_json_status_401_raises_auth_error(self):
        """should raise GoveeAuthError when HTTP 200 response carries JSON status 401."""
        # Arrange
        body = create_login_response({"status": 401, "message": "Invalid password"})
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeAuthError):
            await client.login("user@example.com", "wrong-password")

    async def test_login_json_status_401_error_has_401_code(self):
        """should set code=401 on GoveeAuthError from JSON body status 401."""
        # Arrange
        body = create_login_response({"status": 401, "message": "Invalid password"})
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeAuthError) as exc_info:
            await client.login("user@example.com", "wrong-password")

        assert exc_info.value.code == 401

    async def test_login_json_status_454_without_code_raises_2fa_required(self):
        """should raise Govee2FARequiredError when status 454 and no code provided."""
        # Arrange
        body = create_login_response({"status": 454, "message": ""})
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(Govee2FARequiredError):
            await client.login("user@example.com", "s3cr3t")

    async def test_login_json_status_454_with_code_raises_2fa_code_invalid(self):
        """should raise Govee2FACodeInvalidError when status 454 and code was provided."""
        # Arrange
        body = create_login_response({"status": 454, "message": ""})
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(Govee2FACodeInvalidError):
            await client.login("user@example.com", "s3cr3t", code="1234")

    async def test_login_json_password_in_message_raises_auth_error(self):
        """should raise GoveeAuthError when message contains 'password' regardless of status code."""
        # Arrange — some Govee responses return a non-401 status but include 'password'
        body = create_login_response(
            {"status": 400, "message": "Wrong password provided"}
        )
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeAuthError):
            await client.login("user@example.com", "wrong-password")


# ==============================================================================
# Tests: GoveeAuthClient.login — connection errors
# ==============================================================================


class TestLoginConnectionErrors:
    """Test login raising GoveeApiError on network-level failures."""

    async def test_login_connection_error_raises_api_error(self):
        """should raise GoveeApiError when aiohttp raises ClientError during login."""
        # Arrange
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post_raises(*_args: Any, **_kwargs: Any):
            raise aiohttp.ClientConnectionError("Connection refused")

        session.post = _post_raises
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert "Connection error" in str(exc_info.value)

    async def test_login_connection_error_wraps_original(self):
        """should chain the original ClientError as __cause__."""
        # Arrange
        original_error = aiohttp.ClientConnectionError("Network unreachable")
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post_raises(*_args: Any, **_kwargs: Any):
            raise original_error

        session.post = _post_raises
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert exc_info.value.__cause__ is original_error

    async def test_login_client_response_error_raises_api_error(self):
        """should raise GoveeApiError when aiohttp raises ClientResponseError."""
        # Arrange
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post_raises(*_args: Any, **_kwargs: Any):
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=503,
                message="Service unavailable",
            )

        session.post = _post_raises
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError):
            await client.login("user@example.com", "s3cr3t")


# ==============================================================================
# Tests: GoveeAuthClient.login — missing token / certificate data
# ==============================================================================


class TestLoginMissingData:
    """Test login raising GoveeApiError when required fields are absent."""

    async def test_login_missing_token_raises_api_error(self):
        """should raise GoveeApiError when login response contains no token."""
        # Arrange
        body = create_login_response()
        body["client"]["token"] = ""  # empty token
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert "token" in str(exc_info.value).lower()

    async def test_login_missing_token_key_raises_api_error(self):
        """should raise GoveeApiError when client dict has no token key at all."""
        # Arrange
        body = create_login_response()
        del body["client"]["token"]
        login_resp = make_mock_response(200, body)
        session = make_session_post_get(login_resp, MagicMock())
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError):
            await client.login("user@example.com", "s3cr3t")

    async def test_login_no_cert_data_in_iot_response_raises_api_error(self):
        """should raise GoveeApiError when IoT key response has no cert or P12 data."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        # IoT response with no cert fields at all
        iot_resp = make_mock_response(200, {"data": {"endpoint": "some.endpoint"}})
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert "certificate" in str(exc_info.value).lower() or "cert" in str(
            exc_info.value
        ).lower()

    async def test_login_invalid_credentials_result_raises_api_error(self):
        """should raise GoveeApiError when assembled credentials fail is_valid check."""
        # Arrange — token present but iot_cert will be empty → is_valid is False
        login_resp = make_mock_response(200, create_login_response())
        # Supply empty cert/key so is_valid returns False
        iot_resp = make_mock_response(
            200,
            {"data": {"certificatePem": "", "privateKey": "", "endpoint": "ep"}},
        )
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.login("user@example.com", "s3cr3t")

        assert "IoT" in str(exc_info.value) or "credentials" in str(exc_info.value).lower()


# ==============================================================================
# Tests: GoveeAuthClient.login — request URL and headers
# ==============================================================================


class TestLoginRequestShape:
    """Test that login sends the correct URL, headers, and payload."""

    async def test_login_sends_v2_url(self):
        """should POST to the v2/login endpoint URL."""
        # Arrange
        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(url: str, *_args: Any, **_kwargs: Any):
            captured["url"] = url
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t")

        # Assert
        assert captured["url"] == GOVEE_LOGIN_URL
        assert "v2/login" in captured["url"]

    async def test_login_sends_correct_headers(self):
        """should include appVersion, User-Agent, clientId, clientType, and iotVersion."""
        # Arrange
        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, headers: dict[str, str] | None = None, **_kwargs: Any):
            captured["headers"] = headers
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t", client_id="test-client-id")

        # Assert — every required header is present with the right value
        headers = captured["headers"]
        assert headers is not None
        assert headers["appVersion"] == GOVEE_APP_VERSION
        assert headers["User-Agent"] == GOVEE_USER_AGENT
        assert headers["clientId"] == "test-client-id"
        assert headers["clientType"] == GOVEE_CLIENT_TYPE
        assert headers["iotVersion"] == GOVEE_IOT_VERSION
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"

    async def test_login_payload_format(self):
        """should POST a JSON body with email, password, client, and clientType."""
        # Arrange
        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured["body"] = json
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("alice@example.com", "hunter2", client_id="cid-999")

        # Assert
        body = captured["body"]
        assert body is not None
        assert body["email"] == "alice@example.com"
        assert body["password"] == "hunter2"
        assert body["client"] == "cid-999"
        assert body["clientType"] == GOVEE_CLIENT_TYPE


# ==============================================================================
# Tests: GoveeAuthClient.get_iot_key — headers
# ==============================================================================


class TestGetIotKeyHeaders:
    """Test that get_iot_key sends Govee app headers with Bearer token."""

    async def test_get_iot_key_sends_app_headers(self):
        """should send standard Govee app headers plus Bearer authorization."""
        # Arrange
        captured: dict[str, Any] = {}
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _get(_url: str, *, headers: dict[str, str] | None = None, **_kw: Any):
            captured["url"] = _url
            captured["headers"] = headers
            return _async_cm(iot_resp)

        session.get = _get
        client = GoveeAuthClient(session=session)

        # Act
        await client.get_iot_key(token="my-bearer-token")

        # Assert
        headers = captured["headers"]
        assert headers is not None
        assert headers["Authorization"] == "Bearer my-bearer-token"
        assert headers["appVersion"] == GOVEE_APP_VERSION
        assert headers["User-Agent"] == GOVEE_USER_AGENT
        assert captured["url"] == GOVEE_IOT_KEY_URL

    async def test_get_iot_key_returns_data_dict(self):
        """should unwrap and return the 'data' field from the IoT key response."""
        # Arrange
        iot_resp = make_mock_response(
            200,
            {
                "data": {
                    "certificatePem": _CERT_PEM,
                    "endpoint": "aws.endpoint",
                }
            },
        )
        session = make_session_get(iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        result = await client.get_iot_key(token="tok")

        # Assert — returns the inner 'data' dict, not the whole response
        assert result["certificatePem"] == _CERT_PEM
        assert result["endpoint"] == "aws.endpoint"

    async def test_get_iot_key_non_200_raises_api_error(self):
        """should raise GoveeApiError when IoT key endpoint returns non-200."""
        # Arrange
        iot_resp = make_mock_response(403, {"message": "Forbidden"})
        session = make_session_get(iot_resp)
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.get_iot_key(token="tok")

        assert exc_info.value.code == 403

    async def test_get_iot_key_connection_error_raises_api_error(self):
        """should raise GoveeApiError when aiohttp raises ClientError during IoT key fetch."""
        # Arrange
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _get_raises(*_args: Any, **_kw: Any):
            raise aiohttp.ClientConnectionError("Timed out")

        session.get = _get_raises
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.get_iot_key(token="tok")

        assert "Connection error" in str(exc_info.value)


# ==============================================================================
# Tests: GoveeAuthClient.fetch_device_topics — headers and parsing
# ==============================================================================


class TestFetchDeviceTopicsHeaders:
    """Test that fetch_device_topics sends correct headers and parses topics."""

    async def test_fetch_device_topics_sends_app_headers(self):
        """should send Govee app headers with Bearer token when fetching device topics."""
        # Arrange
        captured: dict[str, Any] = {}
        device_resp = make_mock_response(200, create_device_list_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post(_url: str, *, headers: dict[str, str] | None = None, **_kw: Any):
            captured["url"] = _url
            captured["headers"] = headers
            return _async_cm(device_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.fetch_device_topics(token="bearer-token-xyz")

        # Assert
        headers = captured["headers"]
        assert headers is not None
        assert headers["Authorization"] == "Bearer bearer-token-xyz"
        assert headers["appVersion"] == GOVEE_APP_VERSION
        assert headers["User-Agent"] == GOVEE_USER_AGENT
        assert captured["url"] == GOVEE_DEVICE_LIST_URL

    async def test_fetch_device_topics_returns_device_id_to_topic_map(self):
        """should return a dict mapping device_id strings to MQTT topic strings."""
        # Arrange
        device_resp = make_mock_response(200, create_device_list_response())
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(device_resp)
        client = GoveeAuthClient(session=session)

        # Act
        topics = await client.fetch_device_topics(token="tok")

        # Assert
        assert "AA:BB:CC:DD:EE:FF:00:11" in topics
        assert topics["AA:BB:CC:DD:EE:FF:00:11"] == "GA/device/AA:BB:CC:DD:EE:FF:00:11"

    async def test_fetch_device_topics_parses_json_string_device_ext(self):
        """should handle deviceExt as a JSON string (not a pre-parsed dict)."""
        # Arrange
        device_ext_str = json.dumps(
            {"deviceSettings": {"topic": "GA/device/CC:DD:EE:FF:00:11"}}
        )
        devices = [
            {"device": "CC:DD:EE:FF:00:11", "deviceExt": device_ext_str}
        ]
        device_resp = make_mock_response(200, create_device_list_response(devices))
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(device_resp)
        client = GoveeAuthClient(session=session)

        # Act
        topics = await client.fetch_device_topics(token="tok")

        # Assert
        assert topics["CC:DD:EE:FF:00:11"] == "GA/device/CC:DD:EE:FF:00:11"

    async def test_fetch_device_topics_skips_devices_without_topic(self):
        """should omit devices that have no topic in their device settings."""
        # Arrange
        devices = [
            {"device": "AA:11:22:33:44:55:66:77", "deviceExt": {}},  # no topic
            {
                "device": "BB:11:22:33:44:55:66:77",
                "deviceExt": {"deviceSettings": {"topic": "GA/device/BB"}},
            },
        ]
        device_resp = make_mock_response(200, create_device_list_response(devices))
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(device_resp)
        client = GoveeAuthClient(session=session)

        # Act
        topics = await client.fetch_device_topics(token="tok")

        # Assert
        assert "AA:11:22:33:44:55:66:77" not in topics
        assert topics["BB:11:22:33:44:55:66:77"] == "GA/device/BB"

    async def test_fetch_device_topics_non_200_raises_api_error(self):
        """should raise GoveeApiError when device list endpoint returns non-200."""
        # Arrange
        device_resp = make_mock_response(401, {"message": "Unauthorized"})
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(device_resp)
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError):
            await client.fetch_device_topics(token="tok")

    async def test_fetch_device_topics_connection_error_raises_api_error(self):
        """should raise GoveeApiError when aiohttp raises ClientError during topic fetch."""
        # Arrange
        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post_raises(*_args: Any, **_kw: Any):
            raise aiohttp.ClientConnectionError("DNS failure")

        session.post = _post_raises
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError):
            await client.fetch_device_topics(token="tok")


# ==============================================================================
# Tests: GoveeIotCredentials.is_valid
# ==============================================================================


class TestGoveeIotCredentialsIsValid:
    """Test the is_valid property on the credentials dataclass."""

    def test_is_valid_returns_true_when_all_fields_present(self):
        """should return True when token, iot_cert, iot_key, and account_topic are set."""
        # Arrange
        creds = GoveeIotCredentials(
            token="tok",
            refresh_token="refresh",
            account_topic="GA/topic",
            iot_cert=_CERT_PEM,
            iot_key=_KEY_PEM,
            iot_ca=None,
            client_id="AP/123/abc",
            endpoint="aws.endpoint",
        )
        # Assert
        assert creds.is_valid is True

    def test_is_valid_returns_false_when_token_empty(self):
        """should return False when token is an empty string."""
        # Arrange
        creds = GoveeIotCredentials(
            token="",
            refresh_token="refresh",
            account_topic="GA/topic",
            iot_cert=_CERT_PEM,
            iot_key=_KEY_PEM,
            iot_ca=None,
            client_id="AP/123/abc",
            endpoint="aws.endpoint",
        )
        # Assert
        assert creds.is_valid is False

    def test_is_valid_returns_false_when_iot_cert_empty(self):
        """should return False when iot_cert is an empty string."""
        # Arrange
        creds = GoveeIotCredentials(
            token="tok",
            refresh_token="refresh",
            account_topic="GA/topic",
            iot_cert="",
            iot_key=_KEY_PEM,
            iot_ca=None,
            client_id="AP/123/abc",
            endpoint="aws.endpoint",
        )
        # Assert
        assert creds.is_valid is False

    def test_is_valid_returns_false_when_account_topic_empty(self):
        """should return False when account_topic is an empty string."""
        # Arrange
        creds = GoveeIotCredentials(
            token="tok",
            refresh_token="refresh",
            account_topic="",
            iot_cert=_CERT_PEM,
            iot_key=_KEY_PEM,
            iot_ca=None,
            client_id="AP/123/abc",
            endpoint="aws.endpoint",
        )
        # Assert
        assert creds.is_valid is False


# ==============================================================================
# Tests: validate_govee_credentials convenience function
# ==============================================================================


class TestValidateGoveeCredentials:
    """Test the module-level convenience wrapper."""

    async def test_validate_govee_credentials_returns_credentials_on_success(self):
        """should return GoveeIotCredentials when login succeeds via the convenience wrapper."""
        # Arrange
        from custom_components.govee.api.auth import validate_govee_credentials

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)

        # Act
        credentials = await validate_govee_credentials(
            "user@example.com", "s3cr3t", session=session
        )

        # Assert
        assert isinstance(credentials, GoveeIotCredentials)
        assert credentials.is_valid is True

    async def test_validate_govee_credentials_propagates_auth_error(self):
        """should propagate GoveeAuthError raised during login."""
        # Arrange
        from custom_components.govee.api.auth import validate_govee_credentials

        login_resp = make_mock_response(401, {"message": "Bad credentials"})
        session = make_session_post_get(login_resp, MagicMock())

        # Assert
        with pytest.raises(GoveeAuthError):
            await validate_govee_credentials("u@e.com", "bad", session=session)


# ==============================================================================
# Tests: 2FA authentication flow
# ==============================================================================


class Test2FAFlow:
    """Test two-factor authentication flow: verification code request, login with code."""

    async def test_login_payload_includes_code_when_provided(self):
        """should include 'code' field in POST payload when code is provided."""
        # Arrange
        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured["body"] = json
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t", client_id="cid", code="1234")

        # Assert
        body = captured["body"]
        assert body is not None
        assert body["code"] == "1234"

    async def test_login_payload_excludes_code_when_none(self):
        """should NOT include 'code' field in POST payload when code is None."""
        # Arrange
        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured["body"] = json
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t", client_id="cid", code=None)

        # Assert
        body = captured["body"]
        assert body is not None
        assert "code" not in body

    async def test_request_verification_code_sends_correct_payload(self):
        """should POST to GOVEE_VERIFICATION_URL with type=8 and email."""
        # Arrange
        from custom_components.govee.api.auth import GOVEE_VERIFICATION_URL

        captured: dict[str, Any] = {}
        verify_resp = make_mock_response(200, {"status": 200})

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured["url"] = _url
            captured["body"] = json
            return _async_cm(verify_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.request_verification_code("alice@example.com", client_id="cid-abc")

        # Assert
        assert captured["url"] == GOVEE_VERIFICATION_URL
        body = captured["body"]
        assert body is not None
        assert body["type"] == 8
        assert body["email"] == "alice@example.com"

    async def test_request_verification_code_uses_client_id_in_headers(self):
        """should pass client_id through to headers as clientId."""
        # Arrange
        captured: dict[str, Any] = {}
        verify_resp = make_mock_response(200, {"status": 200})

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post(_url: str, *_args: Any, headers: dict[str, str] | None = None, **_kwargs: Any):
            captured["headers"] = headers
            return _async_cm(verify_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.request_verification_code("user@example.com", client_id="my-client-id-42")

        # Assert
        headers = captured["headers"]
        assert headers is not None
        assert headers["clientId"] == "my-client-id-42"

    async def test_request_verification_code_raises_on_non_200(self):
        """should raise GoveeApiError when verification endpoint returns non-200."""
        # Arrange
        verify_resp = make_mock_response(500, {"message": "Internal Server Error"})

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(verify_resp)
        client = GoveeAuthClient(session=session)

        # Assert
        with pytest.raises(GoveeApiError) as exc_info:
            await client.request_verification_code("user@example.com", client_id="cid")

        assert "verification code" in str(exc_info.value).lower()

    async def test_validate_govee_credentials_passes_code_and_client_id(self):
        """should forward code and client_id to login() via the convenience wrapper."""
        # Arrange
        from custom_components.govee.api.auth import validate_govee_credentials

        captured: dict[str, Any] = {}

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured["body"] = json
            return _async_cm(login_resp)

        session.post = _post

        # Act
        await validate_govee_credentials(
            "user@example.com",
            "s3cr3t",
            code="5678",
            client_id="my-cid",
            session=session,
        )

        # Assert — code and client fields are in the login POST body
        body = captured["body"]
        assert body is not None
        assert body["code"] == "5678"
        assert body["client"] == "my-cid"

    async def test_login_with_valid_code_returns_credentials(self):
        """should return valid GoveeIotCredentials when login succeeds with a 2FA code."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        credentials = await client.login(
            "user@example.com", "s3cr3t", client_id="cid-2fa", code="9999"
        )

        # Assert
        assert isinstance(credentials, GoveeIotCredentials)
        assert credentials.is_valid is True
        assert credentials.token == "test-token-abc123"
        assert credentials.client_id == "AP/99001/cid-2fa"


# ==============================================================================
# Deterministic client_id tests
# ==============================================================================


class TestDeterministicClientId:
    """Test stable client_id derivation and propagation across calls.

    Govee's account API caches (email, client_id) pairs and rejects
    inconsistent client_ids within a session or across restarts.
    These tests verify we match the reference implementations'
    deterministic-per-email pattern.
    """

    def test_derive_client_id_is_deterministic(self):
        """Same email should always produce the same client_id."""
        cid_1 = _derive_client_id("user@example.com")
        cid_2 = _derive_client_id("user@example.com")
        assert cid_1 == cid_2

    def test_derive_client_id_is_case_insensitive(self):
        """Email case should not affect client_id — Govee normalizes emails."""
        cid_lower = _derive_client_id("user@example.com")
        cid_upper = _derive_client_id("USER@EXAMPLE.COM")
        cid_mixed = _derive_client_id("User@Example.Com")
        assert cid_lower == cid_upper == cid_mixed

    def test_derive_client_id_strips_whitespace(self):
        """Leading/trailing whitespace should not affect client_id."""
        cid_clean = _derive_client_id("user@example.com")
        cid_spaces = _derive_client_id("  user@example.com  ")
        assert cid_clean == cid_spaces

    def test_derive_client_id_different_for_different_emails(self):
        """Different emails should produce different client_ids."""
        cid_a = _derive_client_id("alice@example.com")
        cid_b = _derive_client_id("bob@example.com")
        assert cid_a != cid_b

    def test_derive_client_id_is_32_char_hex(self):
        """client_id should be a 32-character hex string (uuid hex format)."""
        cid = _derive_client_id("user@example.com")
        assert len(cid) == 32
        assert all(c in "0123456789abcdef" for c in cid)

    def test_derive_client_id_empty_email(self):
        """Empty email should produce a stable (even if not useful) client_id."""
        cid_a = _derive_client_id("")
        cid_b = _derive_client_id("")
        assert cid_a == cid_b
        assert len(cid_a) == 32

    async def test_login_uses_deterministic_client_id_when_none_provided(self):
        """login() without explicit client_id should derive from email."""
        # Arrange
        captured_bodies: list[dict[str, Any]] = []
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            captured_bodies.append(json)
            return _async_cm(login_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t")

        # Assert: client field in payload matches _derive_client_id(email)
        assert len(captured_bodies) == 1
        assert captured_bodies[0]["client"] == _derive_client_id("user@example.com")

    async def test_login_stores_client_id_on_instance(self):
        """After login(), self._client_id should be set to the login's client_id."""
        # Arrange
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())
        session = make_session_post_get(login_resp, iot_resp)
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("alice@example.com", "pw", client_id="explicit-cid")

        # Assert
        assert client._client_id == "explicit-cid"

    async def test_get_iot_key_uses_stored_client_id(self):
        """get_iot_key() after login() should reuse the login's client_id."""
        # Arrange
        captured_headers: list[dict[str, str]] = []

        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.post = lambda *a, **kw: _async_cm(login_resp)

        def _get(_url: str, *_args: Any, headers: dict[str, str] | None = None, **_kwargs: Any):
            captured_headers.append(headers or {})
            return _async_cm(iot_resp)

        session.get = _get
        client = GoveeAuthClient(session=session)

        # Act
        await client.login("user@example.com", "s3cr3t", client_id="stable-id-123")

        # Assert: the IoT key GET headers have the SAME clientId as login
        assert len(captured_headers) == 1
        assert captured_headers[0]["clientId"] == "stable-id-123"

    async def test_get_iot_key_explicit_client_id_overrides_stored(self):
        """Explicit client_id on get_iot_key() should win over stored."""
        # Arrange
        captured_headers: list[dict[str, str]] = []
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _get(_url: str, *_args: Any, headers: dict[str, str] | None = None, **_kwargs: Any):
            captured_headers.append(headers or {})
            return _async_cm(iot_resp)

        session.get = _get
        client = GoveeAuthClient(session=session)
        client._client_id = "stored-id"

        # Act
        await client.get_iot_key("token123", client_id="override-id")

        # Assert
        assert captured_headers[0]["clientId"] == "override-id"

    async def test_fetch_device_topics_uses_stored_client_id(self):
        """fetch_device_topics() should reuse the login's client_id."""
        # Arrange
        captured_headers: list[dict[str, str]] = []
        topics_resp = make_mock_response(
            200, {"devices": []}
        )

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()

        def _post(_url: str, *_args: Any, headers: dict[str, str] | None = None, **_kwargs: Any):
            captured_headers.append(headers or {})
            return _async_cm(topics_resp)

        session.post = _post
        client = GoveeAuthClient(session=session)
        client._client_id = "login-cid-xyz"

        # Act
        await client.fetch_device_topics("token123")

        # Assert
        assert captured_headers[0]["clientId"] == "login-cid-xyz"

    async def test_two_logins_same_email_produce_same_client_id(self):
        """Two separate login() calls for the same email should use the same client_id."""
        # Arrange
        captured_client_ids: list[str] = []
        login_resp = make_mock_response(200, create_login_response())
        iot_resp = make_mock_response(200, create_iot_key_response())

        session = MagicMock(spec=aiohttp.ClientSession)
        session.close = AsyncMock()
        session.get = lambda *a, **kw: _async_cm(iot_resp)

        def _post(_url: str, *_args: Any, json: dict[str, Any] | None = None, **_kwargs: Any):
            if json and "client" in json:
                captured_client_ids.append(json["client"])
            return _async_cm(login_resp)

        session.post = _post

        # Act: two separate login calls
        client_1 = GoveeAuthClient(session=session)
        await client_1.login("user@example.com", "pw")

        client_2 = GoveeAuthClient(session=session)
        await client_2.login("user@example.com", "pw")

        # Assert: both logins used the same client_id
        assert len(captured_client_ids) == 2
        assert captured_client_ids[0] == captured_client_ids[1]

    def test_derive_client_id_is_namespaced(self):
        """Our derivation should be namespaced so it doesn't collide with other clients.

        Other Govee integrations (homebridge-govee, govee2mqtt, TheOneOgre)
        use their own prefixes. We use 'hacs-govee:' to avoid collisions.
        """
        import uuid as _uuid
        expected = _uuid.uuid5(_uuid.NAMESPACE_DNS, "hacs-govee:user@example.com").hex
        assert _derive_client_id("user@example.com") == expected

        # Verify we do NOT produce the same as a naked uuid5 (no namespace)
        naked = _uuid.uuid5(_uuid.NAMESPACE_DNS, "user@example.com").hex
        assert _derive_client_id("user@example.com") != naked
