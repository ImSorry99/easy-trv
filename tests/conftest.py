import errno
import socket
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for every test."""


@pytest.fixture(autouse=True)
def _no_card_serving(hass):
    """Leave the frontend out; test_frontend covers the card.

    The frontend package (``hass_frontend``) is not installed for the tests, so
    the dependency is taken as set up and the card is not registered.
    """
    hass.config.components.add("frontend")
    with patch("custom_components.easy_trv.async_register_card", AsyncMock()):
        yield


@pytest.fixture
def local_binding() -> None:
    """Skip the test where no socket can be bound on the loopback interface.

    Tests that start a real HTTP server (``hass_client``) need this; sandboxes
    that forbid binding make the bind fail with ``EPERM``.
    """
    message = "local port binding not permitted in this environment"
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
    except PermissionError:
        pytest.skip(message)
    except OSError as err:
        if err.errno != errno.EPERM:
            raise
        pytest.skip(message)
