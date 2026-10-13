import errno
import socket

import pytest


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for every test."""


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
